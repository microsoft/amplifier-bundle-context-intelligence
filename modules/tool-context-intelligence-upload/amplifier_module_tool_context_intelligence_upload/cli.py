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

from .progress import ProgressTracker, progress_file_path
from .session_graph import discover_and_sort
from .uploader import run_upload

# ---------------------------------------------------------------------------
# Compact help text
# ---------------------------------------------------------------------------

_COMPACT_HELP = """\
usage: context-intelligence-upload --path PATH --server-url URL --api-key KEY
	                                    [--job-id ID] [--progress FILE]
	                                    [--event-delay-ms MS]

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

IDEMPOTENCY GUARANTEE
---------------------
The tool has NO built-in deduplication -- re-running will re-upload all sessions.
Idempotency is provided by the server using the ``idempotency_key`` field in every
POST payload.  This key is a SHA-256 hash of the canonical event JSON, so the server
can safely skip already-ingested events by treating ``idempotency_key`` as a natural
key.  This means it is safe to re-upload the same PATH multiple times.

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
  0   Success — all sessions uploaded, or no sessions found.
  1   Failure — at least one HTTP error occurred during upload.
  2   Invalid invocation — missing required argument or PATH does not exist.

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
        required=True,
        metavar="PATH",
        help="File or folder to replay",
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

    # Auth flags
    parser.add_argument(
        "--auth-mode",
        choices=["static", "entra"],
        default="static",
        dest="auth_mode",
        help=(
            "Authentication mode: 'static' (default) uses --api-key; "
            "'entra' acquires a delegated token via 'az login' (AzureCliCredential)."
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
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point — synchronous, exits with an appropriate code."""
    import os

    parser = _build_parser()
    args = parser.parse_args()

    # 0a. Resolve auth mode / resource — CLI flags > env vars
    auth_mode: str = args.auth_mode or os.environ.get(
        "AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_MODE", "static"
    )
    auth_resource: str = (
        args.auth_resource
        or os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_RESOURCE", "")
        or ""
    )

    # 0b. Resolve server config — CLI flags > env vars > settings.yaml
    from context_intelligence.config import resolve_config

    server_url, api_key = resolve_config(
        server_url=args.server_url,
        api_key=args.api_key,
        auth_mode=auth_mode,
    )

    # 0c. Build auth strategy — fail loud on misconfiguration
    from context_intelligence.auth import build_auth_strategy

    auth_strategy = build_auth_strategy(
        auth_mode=auth_mode,
        api_key=api_key,
        auth_resource=auth_resource,
    )

    # 1. Auto-generate job_id if not provided
    job_id: str = args.job_id
    if job_id is None:
        job_id = str(uuid.uuid4())
        prog_default = f"/tmp/context-intelligence-upload-{job_id}.json"
        print(f"job_id: {job_id}  progress={prog_default}", file=sys.stderr)

    # 2. Validate path exists
    target_path = Path(args.path)
    if not target_path.exists():
        print(
            f"error: path does not exist: {target_path}",
            file=sys.stderr,
        )
        sys.exit(2)

    # 3. Discover and sort sessions
    sessions = discover_and_sort(target_path)

    # 4. Handle no sessions found
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
    upload_result = run_upload(
        sessions=sessions,
        server_url=server_url,
        api_key=api_key,
        tracker=tracker,
        event_delay_s=args.event_delay_ms / 1000.0,
        auth_strategy=auth_strategy,
    )

    # 7. Write result JSON to stdout
    sys.stdout.write(json.dumps(upload_result.to_dict(), indent=2) + "\n")

    # 8. Exit 0 on success, 1 on failure
    sys.exit(0 if upload_result.success else 1)
