"""CLI entry point for context-intelligence-upload.

Runs SYNCHRONOUSLY when invoked by a human.  Uses argparse with two custom
help actions:

  -h        Compact help (usage line + flag list) → stdout, exit 0
  --help    Detailed help (full documentation)    → stdout, exit 0
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import NamedTuple

from amplifier_module_hook_context_intelligence.config_resolver import Destination

from .destinations import DestinationSelectionError, read_destinations, select_destination
from .formats import FORMATS
from .keys_env import load_keys_env_into_environ
from .logging_hook_format import discover_legacy
from .progress import ProgressTracker, progress_file_path
from .reconciliation import reconciliation_summary
from .session_filter import default_scan_root, filter_sessions  # noqa: F401
from .session_graph import ScopeError, resolve_upload_sessions
from .uploader import _count_lines, run_upload

# ---------------------------------------------------------------------------
# Compact help text
# ---------------------------------------------------------------------------

_COMPACT_HELP = """\
usage: context-intelligence-upload --path PATH --server-url URL --api-key KEY
	                                    [--job-id ID] [--progress FILE]
	                                    [--event-delay-ms MS]
	                                    [--no-replay]

Replay context-intelligence session data to a server.

flags:
  -h                 Show this compact help and exit
  --help             Show detailed documentation and exit
  --path             File or folder to replay (required)
  --server-url       Target server base URL (required)
  --api-key          Bearer token for authorization (required)
  --job-id           Job identifier (auto-generated UUID4 if omitted)
  --progress         Progress file path
                       default: /tmp/context-intelligence-upload-{job_id}.json
  --event-delay-ms   Milliseconds to sleep between events (default: 0)
                       Use 50-200 to reduce Neo4j write pressure on the server
  --format           context-intelligence (default) | logging-hook
                       logging-hook = discover+transform legacy hooks-logging in memory
  --no-replay        Disable replay=true; re-enable server 7-day idempotency cache
                       default: off (every event is replayed unconditionally)
"""

# ---------------------------------------------------------------------------
# Detailed help text
# ---------------------------------------------------------------------------

_DETAILED_HELP = """\
context-intelligence-upload
===========================

WHAT IT DOES
------------
Discovers Amplifier context-intelligence session metadata under PATH, sorts
sessions in BFS topological order (parents before children), then replays
every events.jsonl file to the server's /events endpoint.

Progress is tracked atomically in a JSON file on disk so the upload can be
monitored externally.  The final result is written as JSON to stdout.

PARAMETERS
----------
  --path PATH
      File or folder to replay.  If PATH is a file named metadata.json only
      that single session is processed.  Otherwise the tool recurses into
      PATH searching for metadata.json files.

  --server-url URL
      Base URL of the Context Intelligence ingestion server
      (e.g. https://context-intelligence.example.com).  The endpoint /events is appended
      automatically.

  --api-key KEY
      Bearer token sent in the Authorization header for every request.

  --job-id ID            (optional)
      Stable identifier for this upload job.  Useful for correlating progress
      files and log output across retries.  A random UUID4 is generated and
      printed to stderr when this flag is omitted.

  --progress FILE        (optional)
      Where to write the progress JSON file.
      Default: /tmp/context-intelligence-upload-{job_id}.json

  --event-delay-ms MS   (optional, default: 0)
      Milliseconds to sleep between each successful event POST.  Use this to
      throttle the upload rate and reduce write pressure on the Neo4j backend.
      A value of 0 (the default) means no delay — events are sent as fast as
      the server can accept them.
      Recommended range: 50–200 ms when uploading large session trees and the
      server reports Neo4j thread starvation warnings.

  --format FORMAT       (optional, default: context-intelligence)
      Input format to discover and ingest.

      context-intelligence (default)
          Today's behavior: discover native Context Intelligence session
          trees and replay them.

      logging-hook
          Discover legacy hooks-logging sessions (schema {amplifier.log,
          version 1.x}) and transform them in memory before POSTing to the
          SAME /events path. Non-destructive: no files are written or
          deleted on disk during discovery or transformation.

          The legacy import always uses server dedup (replay=False,
          idempotent — an aborted upload can be safely rerun). ``--no-replay``
          does NOT apply to the logging-hook path and cannot disable dedup
          for it; passing both flags together fails fast with exit code 2
          before any discovery or upload happens.

          Malformed lines, lines with an unknown schema major version, or
          lines missing required fields are skipped with a warning.
          Event names that cannot be mapped to a Context Intelligence event
          type are counted separately (not treated as parse errors).
          Live/in-progress sessions are skipped in their entirety. All of
          these counts are reported in the reconciliation summary printed
          to stderr at the end of the run.

          If zero legacy sessions are discovered under --path, a warning is
          printed to stderr before the run continues. Any live/in-progress
          sessions that were skipped are also listed by session id (as a
          stderr note) so they can be found and re-run later once complete.

          Exit codes (logging-hook): 0 = clean (no skipped/unmapped/
          live-skipped sessions), 3 = completed WITH issues (one or more
          events were skipped or unmapped, or one or more sessions were
          live-skipped -- see the reconciliation summary for counts),
          2 = usage error (see EXIT CODES below). This is additive to the
          default exit codes; the context-intelligence path never returns 3.

METADATA VALIDATION
-------------------
Each metadata.json is validated before upload:

  • format must equal "context-intelligence"  — other formats are skipped
    silently.
  • session_id must be present — files missing it emit a warning to stderr
    and are skipped.
  • parent_id is optional.  An empty string or absent field means the session
    is a root session.  A non-empty parent_id that cannot be resolved in the
    discovered set causes the session to be promoted to root with a warning.

TOPOLOGICAL ORDERING
--------------------
Sessions are sorted using a BFS traversal that guarantees parents are uploaded
before their children:

  1. Collect all valid sessions.
  2. Build a lookup table session_id → (path, metadata).
  3. Classify: root (no parent_id or empty string), child (known parent), or
     orphan (unknown parent → promoted to root with warning).
  4. Sort root sessions alphabetically; sort each node's children
     alphabetically before enqueuing them.
  5. Emit sessions in BFS order.

IDEMPOTENCY
-----------
The upload CLI bypasses the server-side event deduplication cache by default.
Every event in every session is forwarded to the server unconditionally, and the
server processes it on every run.  Neo4j idempotency is guaranteed by
``MERGE + SET n += row.props`` semantics: re-uploading the same session data
produces the same graph state.

Use ``--no-replay`` to re-enable the server's 7-day in-memory deduplication
cache.  With ``--no-replay``, events whose ``idempotency_key`` was seen within
the last 7 days are silently skipped.  Only use this flag when running the
upload tool against a live session in progress where duplicate suppression is
intentional.

WORKSPACE BEHAVIOUR
-------------------
Each event line in events.jsonl carries an optional "workspace" field.  The
tool passes this value to build_payload() unchanged.  No workspace filtering
or transformation is applied by the upload tool itself.

PROGRESS FILE
-------------
The progress file is a JSON object with the following schema:

  {
    "job_id":                       "<string>",
    "status":                       "running" | "completed" | "failed",
    "started_at":                   "<ISO 8601 timestamp>",
    "sessions_total":               <int>,
    "sessions_completed":           <int>,
    "current_session_id":           "<string | null>",
    "current_session_events_total": <int>,
    "current_session_events_sent":  <int>,
    "failed_at":                    null | {
        "session_id":  "<string>",
        "event_index": <int>,
        "http_status": <int>,
        "error":       "<string>"
    }
  }

The file is updated atomically (write to .tmp suffix then rename) after every
event so that external readers always see a consistent snapshot.

EXIT CODES
----------
  0   Success — all sessions uploaded.
  1   Failure — at least one HTTP error occurred during upload.
  2   Invalid invocation — missing required argument, PATH does not exist, or
      no context-intelligence sessions could be found/resolved under PATH.

FINDING SERVER_URL AND API_KEY
------------------------------
These values come from your context-intelligence bundle configuration.

Check environment variables first:

  $ echo $AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL
  $ echo $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY

If not set as environment variables, find them in your Amplifier bundle
configuration YAML (typically ~/.amplifier/settings.yaml) under the
hook-context-intelligence module config section:

  hooks:
    - module: hook-context-intelligence
      config:
        context_intelligence_server_url: "https://your-server.example.com"
        context_intelligence_api_key:    "your-api-key"

When invoking from an Amplifier session via the bash tool, use shell
variable substitution to pass the values directly:

  context-intelligence-upload \\
    --path ~/.amplifier/projects/my-project \\
    --server-url "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL}" \\
    --api-key    "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY}"

Or read them from the YAML config file and pass explicitly:

  context-intelligence-upload \\
    --path ~/.amplifier/projects/my-project \\
    --server-url https://your-server.example.com \\
    --api-key    your-api-key

EXAMPLES
--------
Replay a single session directory:

  context-intelligence-upload \\
      --path ~/.amplifier/projects/my-project/sessions/abc123/context-intelligence \\
      --server-url https://context-intelligence.example.com \\
      --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY

Replay an entire project tree:

  context-intelligence-upload \\
      --path ~/.amplifier/projects/my-project \\
      --server-url https://context-intelligence.example.com \\
      --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY

Target a recovery server with a custom job ID:

  context-intelligence-upload \\
      --path /data/sessions \\
      --server-url https://recovery.example.com \\
      --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY \\
      --job-id my-retry-job-001
"""


# ---------------------------------------------------------------------------
# Custom help actions
# ---------------------------------------------------------------------------


def _make_help_action(text: str) -> type[argparse.Action]:
    """Return a custom argparse.Action that writes *text* to stdout and exits 0."""

    class _Action(argparse.Action):
        def __init__(
            self,
            option_strings: list[str],
            dest: str = argparse.SUPPRESS,
            default: str = argparse.SUPPRESS,
            help: str | None = None,  # noqa: A002
        ) -> None:
            super().__init__(
                option_strings=option_strings,
                dest=dest,
                default=default,
                nargs=0,
                help=help,
            )

        def __call__(
            self,
            parser: argparse.ArgumentParser,
            namespace: argparse.Namespace,
            values: object,
            option_string: str | None = None,
        ) -> None:
            sys.stdout.write(text)
            parser.exit(0)

    return _Action


_CompactHelpAction = _make_help_action(_COMPACT_HELP)
_DetailedHelpAction = _make_help_action(_DETAILED_HELP)


# ---------------------------------------------------------------------------
# Parser factory
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Return the argument parser.  Built with ``add_help=False``."""
    parser = argparse.ArgumentParser(
        prog="context-intelligence-upload",
        add_help=False,
    )

    # Custom help flags
    parser.add_argument(
        "-h",
        action=_CompactHelpAction,
        help="Show compact help and exit",
    )
    parser.add_argument(
        "--help",
        action=_DetailedHelpAction,
        help="Show detailed documentation and exit",
    )

    # Required flags
    parser.add_argument(
        "--path",
        required=False,
        default=None,
        metavar="PATH",
        help="File or folder to replay (default: ~/.amplifier/projects)",
    )
    parser.add_argument(
        "--server-url",
        required=False,
        default=None,
        metavar="URL",
        help="Target server base URL",
    )
    parser.add_argument(
        "--api-key",
        required=False,
        default=None,
        metavar="KEY",
        help="Bearer token for authorization",
    )

    # Optional flags
    parser.add_argument(
        "--job-id",
        default=None,
        metavar="ID",
        help="Job identifier (auto-generated UUID4 if omitted)",
    )
    parser.add_argument(
        "--progress",
        default=None,
        metavar="FILE",
        help="Progress file path (default: /tmp/context-intelligence-upload-{job_id}.json)",
    )
    parser.add_argument(
        "--event-delay-ms",
        type=int,
        default=0,
        metavar="MS",
        dest="event_delay_ms",
        help="Milliseconds to sleep between events (default: 0; use 50-200 to reduce Neo4j pressure)",
    )

    parser.add_argument(
        "--format",
        choices=["context-intelligence", "logging-hook"],
        default="context-intelligence",
        dest="format",
        help=(
            "Input format to discover and ingest. 'context-intelligence' (default) "
            "is today's behavior. 'logging-hook' discovers and transforms legacy "
            "hooks-logging events in memory (non-destructive) and ingests them "
            "through the same /events path."
        ),
    )

    parser.add_argument(
        "--no-replay",
        action="store_true",
        default=False,
        dest="no_replay",
        help=(
            "Disable the default replay=true query parameter on POST /events; "
            "re-enables the server's 7-day idempotency cache"
        ),
    )

    parser.add_argument(
        "--destination",
        default=None,
        metavar="NAME",
        dest="destination",
        help=(
            "Name of the destination to upload to, from the 'destinations' map in "
            "~/.amplifier/settings.yaml. Omit to auto-select when exactly one is "
            "configured, or to be prompted when several are."
        ),
    )

    parser.add_argument(
        "-y",
        "--auto-approve",
        action="store_true",
        default=False,
        dest="auto_approve",
        help=(
            "Skip the 'Proceed? [y/N]' confirmation shown before a destination-mode "
            "upload. Required for non-interactive/CI use."
        ),
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        metavar="N",
        dest="max_retries",
        help=(
            "Number of ADDITIONAL retries after the first attempt for transient "
            "failures (connection errors, timeouts, HTTP 5xx, HTTP 429) using "
            "exponential backoff (default: 5, i.e. up to 6 attempts per event). "
            "Permanent failures (4xx other than 429, and DNS/TLS errors) fail "
            "immediately. Set 0 to disable retries."
        ),
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        dest="timeout_s",
        help=(
            "Read/write HTTP timeout in seconds (default: 30). Increase for a slow "
            "or variable link with large event payloads; the connect timeout is "
            "unaffected."
        ),
    )

    # Auth flags
    parser.add_argument(
        "--auth-mode",
        choices=["static", "entra"],
        default="static",
        dest="auth_mode",
        help=(
            "Authentication mode: 'static' (default) uses --api-key; "
            "'entra' acquires an Entra token via DefaultAzureCredential (managed "
            "identity / workload identity / service principal / 'az login')."
        ),
    )
    parser.add_argument(
        "--auth-resource",
        default=None,
        metavar="RESOURCE",
        dest="auth_resource",
        help=(
            "Entra resource URI, e.g. 'api://<client_id>'. "
            "Required when --auth-mode entra. "
            "Also read from AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_RESOURCE."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Connection resolution
# ---------------------------------------------------------------------------


class _Connection(NamedTuple):
    """The resolved upload connection.

    ``destination`` is None when the connection came from explicit flags, env
    vars, or legacy settings.yaml scalars.  A None destination means NO
    include/exclude filtering and NO confirmation gate (design: "both present
    as before -> original behavior, byte-for-byte").
    """

    server_url: str
    api_key: str
    auth_mode: str
    auth_resource: str
    destination: Destination | None


def _resolve_connection(args: argparse.Namespace) -> _Connection:
    """Resolve the upload connection.

    Precedence:
      1. explicit CLI flags (--server-url/--api-key/--auth-mode/--auth-resource)
      2. AMPLIFIER_CONTEXT_INTELLIGENCE_* env vars
      3. the 'destinations' map in ~/.amplifier/settings.yaml, with ${VAR}
         resolved from ~/.amplifier/keys.env

    ``--destination NAME`` forces tier 3 regardless of env vars.
    """
    import os

    from context_intelligence.config import _expand_env_placeholders, resolve_config

    auth_mode: str = args.auth_mode or os.environ.get(
        "AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_MODE", "static"
    )
    auth_resource: str = _expand_env_placeholders(
        args.auth_resource
        or os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_RESOURCE", "")
        or ""
    )

    explicit = bool(args.server_url or args.api_key)
    env_configured = bool(
        os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL")
        or os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY")
    )

    # Tiers 1 + 2 -- today's behavior, unchanged, no destination object.
    if not args.destination and (explicit or env_configured):
        server_url, api_key = resolve_config(
            server_url=args.server_url,
            api_key=args.api_key,
            auth_mode=auth_mode,
        )
        return _Connection(
            _expand_env_placeholders(server_url),
            _expand_env_placeholders(api_key),
            auth_mode,
            auth_resource,
            None,
        )

    # Tier 3 -- the destinations map (keys.env expands ${VAR} for it).
    load_keys_env_into_environ()
    destinations = read_destinations()

    if not destinations:
        # D2: no destinations map, but a legacy flat settings.yaml config may
        # still exist. resolve_config raises SystemExit when nothing at all is
        # configured -- that is the design's "0 destinations + no args" error.
        try:
            server_url, api_key = resolve_config(server_url=None, api_key=None, auth_mode=auth_mode)
        except SystemExit:
            print(
                "error: no context-intelligence destination configured. Configure a "
                "destination under overrides.hook-context-intelligence.config.destinations "
                "in ~/.amplifier/settings.yaml, or pass --server-url/--api-key.",
                file=sys.stderr,
            )
            sys.exit(2)
        return _Connection(
            _expand_env_placeholders(server_url),
            _expand_env_placeholders(api_key),
            auth_mode,
            auth_resource,
            None,
        )

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    destination = select_destination(destinations, args.destination, interactive)
    return _Connection(
        destination.url,
        destination.api_key,
        destination.auth_mode or auth_mode,
        destination.auth_resource or auth_resource,
        destination,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point — synchronous, exits with an appropriate code."""
    parser = _build_parser()
    args = parser.parse_args()

    # 0. Resolve the connection: explicit flags > env vars > destinations map.
    try:
        conn = _resolve_connection(args)
    except DestinationSelectionError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    server_url = conn.server_url
    api_key = conn.api_key

    from context_intelligence.auth import build_auth_strategy

    auth_strategy = build_auth_strategy(
        auth_mode=conn.auth_mode,
        api_key=conn.api_key,
        auth_resource=conn.auth_resource,
    )

    # 1. Auto-generate job_id if not provided
    job_id: str = args.job_id
    if job_id is None:
        job_id = str(uuid.uuid4())
        prog_default = f"/tmp/context-intelligence-upload-{job_id}.json"
        print(f"job_id: {job_id}  progress={prog_default}", file=sys.stderr)

    # 2. Resolve the scan root: --path if given, else ~/.amplifier/projects.
    target_path = Path(args.path) if args.path else default_scan_root()
    if not target_path.exists():
        print(
            f"error: path does not exist: {target_path}",
            file=sys.stderr,
        )
        sys.exit(2)

    # 2b. DECISION C1 (loud reject): --no-replay is not valid with --format
    #     logging-hook -- the legacy import always dedups, with no override.
    #     Fail fast, before discovery or any upload.
    if args.format == "logging-hook" and args.no_replay:
        print(
            "error: --no-replay is not valid with --format logging-hook "
            "(legacy import always dedups)",
            file=sys.stderr,
        )
        sys.exit(2)

    # 3. Select the discover/parse pair for --format, then run discovery.
    parse_fn = FORMATS[args.format][1]

    # live_sessions_skipped is only ever non-zero on the logging-hook branch
    # below (discovery.live_skipped) -- the context-intelligence branch has
    # no such concept, so it stays 0.
    live_sessions_skipped = 0

    if args.format == "context-intelligence":
        # Resolve upload scope: descendants-only closure of sub-sessions.
        # Fails loud (exit 2) if nothing is discovered or the selected session
        # (single-session mode) cannot be identified.
        try:
            scope = resolve_upload_sessions(target_path)
        except ScopeError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)

        sessions = scope.sessions

        print(
            f"scope: mode={scope.mode} root(s)={','.join(scope.selected_root_ids)} "
            f"uploading {scope.selected_count} of {scope.total_discovered} discovered session(s)",
            file=sys.stderr,
        )
        if scope.dangling_parent_ids:
            print(
                f"note: {len(scope.dangling_parent_ids)} parent session(s) not included "
                f"will appear as placeholders until uploaded: {','.join(scope.dangling_parent_ids)}",
                file=sys.stderr,
            )
    else:
        # logging-hook: discover legacy hooks-logging sessions directly
        # (discover_legacy is used here for its stats; legacy_discover in
        # FORMATS is the thin sessions-only adapter used by run_upload's
        # own default-parse_fn fallback, not by main()).
        discovery = discover_legacy(target_path)
        sessions = discovery.sessions
        live_sessions_skipped = discovery.live_skipped

        if not sessions:
            print(
                f"warning: no legacy (hooks-logging) sessions found under {target_path} "
                "\u2014 check --path and --format",
                file=sys.stderr,
            )

        print(
            f"scope: format={args.format} discovered {len(sessions)} legacy session(s); "
            f"live-skipped={discovery.live_skipped}, "
            f"unresolved-workspace={discovery.unresolved_workspace}, "
            f"unclassified={discovery.unclassified}",
            file=sys.stderr,
        )

        if discovery.live_skipped:
            print(
                f"note: {discovery.live_skipped} live/in-progress session(s) skipped: "
                f"{','.join(discovery.live_skipped_ids)}",
                file=sys.stderr,
            )

        if discovery.unclassified:
            print(
                f"note: {discovery.unclassified} session(s) skipped as unclassified "
                f"(schema sniff inconclusive, not silently dropped): "
                f"{','.join(discovery.unclassified_ids)}",
                file=sys.stderr,
            )

    # 4. Handle no sessions found (fallback guard; resolve_upload_sessions
    #    already raises ScopeError on empty discovery, but keep this as a
    #    defensive no-op path in case that ever changes).
    if not sessions:
        print(
            "No sessions found under the given path — nothing to upload.",
            file=sys.stderr,
        )
        result = {
            "status": "completed",
            "sessions_uploaded": 0,
            "events_uploaded": 0,
        }
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        sys.exit(0)

    # 5. Create progress tracker
    prog_path = progress_file_path(job_id, override=args.progress)
    tracker = ProgressTracker(
        job_id=job_id,
        file_path=prog_path,
        sessions_total=len(sessions),
    )

    # 6. Run upload
    # DECISION D2 (hard-lock): the logging-hook (legacy) path always dedups --
    # replay=False unconditionally, with no --no-replay override (enforced
    # above at step 2b). The context-intelligence path keeps today's behavior.
    replay = False if args.format == "logging-hook" else not args.no_replay
    upload_result = run_upload(
        sessions=sessions,
        server_url=server_url,
        api_key=api_key,
        tracker=tracker,
        event_delay_s=args.event_delay_ms / 1000.0,
        auth_strategy=auth_strategy,
        replay=replay,
        max_retries=args.max_retries,
        timeout_s=args.timeout_s,
        parse_fn=parse_fn,
    )

    # 6b. Print the operator reconciliation summary -- independently-measured
    # counts only (read is a fresh non-blank-line count from the events.jsonl
    # files on disk, NOT a rederivation of ingested + skipped).
    read_total = 0
    for session_dir, _session_metadata in sessions:
        events_file = session_dir / "events.jsonl"
        if events_file.exists():
            read_total += _count_lines(events_file)

    print(
        "reconciliation: "
        + reconciliation_summary(
            read=read_total,
            ingested=upload_result.events_uploaded,
            skipped=upload_result.events_skipped,
            unmapped=upload_result.events_unmapped,
            live_sessions_skipped=live_sessions_skipped,
        ),
        file=sys.stderr,
    )

    # 7. Write result JSON to stdout
    sys.stdout.write(json.dumps(upload_result.to_dict(), indent=2) + "\n")

    # 8. Exit code. Default (context-intelligence) path: 0 on success, 1 on
    #    failure -- byte-unchanged (GATE 2). logging-hook path additionally
    #    distinguishes "completed WITH issues" (skipped/unmapped/live-skipped
    #    sessions) from a fully clean run via exit code 3 (C4).
    if not upload_result.success:
        sys.exit(1)
    if args.format == "logging-hook":
        issues = (
            upload_result.events_skipped + upload_result.events_unmapped + live_sessions_skipped
        )
        sys.exit(3 if issues else 0)
    sys.exit(0)
