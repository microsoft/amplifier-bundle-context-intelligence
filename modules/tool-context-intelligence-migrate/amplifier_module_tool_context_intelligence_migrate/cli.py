"""CLI entry point for context-intelligence-migrate.

Runs SYNCHRONOUSLY when invoked by a human.  Uses argparse with two custom
help actions (mirrors the sibling upload tool):

  -h        Compact help (usage line + flag list) → stdout, exit 0
  --help    Detailed help (full documentation)    → stdout, exit 0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .migrate import (
    DEFAULT_ARCHIVE_DIR,
    DEFAULT_LEDGER_PATH,
    DEFAULT_PROJECTS_ROOT,
    run_migration,
)

# ---------------------------------------------------------------------------
# Compact help
# ---------------------------------------------------------------------------

_COMPACT_HELP = """\
usage: context-intelligence-migrate [--projects-root PATH] [--server-url URL]
                                     [--api-key KEY] [--apply] [--yes]
                                     [--safety-window-hours N]
                                     [--ledger PATH] [--archive-dir PATH]

Migrate legacy hooks-logging events.jsonl files to Context Intelligence format.

flags:
  -h                     Show this compact help and exit
  --help                 Show detailed documentation and exit
  --projects-root PATH   Sessions root  (default: ~/.amplifier/projects)
  --server-url URL       CI server base URL  (required unless set in env/config)
  --api-key KEY          Bearer token        (required unless set in env/config)
  --apply                Execute destructive migration; ABSENT ⇒ dry-run (default)
  --yes                  Skip interactive confirmation (requires --apply)
  --safety-window-hours  Sessions modified within this window are skipped  [24.0]
  --ledger PATH          Ledger file  (default: ~/.amplifier/migrate-ledger.jsonl)
  --archive-dir PATH     Archive directory  (default: ~/.amplifier/migrate-archive)
"""

# ---------------------------------------------------------------------------
# Detailed help
# ---------------------------------------------------------------------------

_DETAILED_HELP = """\
context-intelligence-migrate
=============================

WHAT IT DOES
------------
Converts legacy Amplifier hooks-logging events.jsonl files into Context
Intelligence (CI) format, uploads them to the CI server, verifies the upload,
and (gated on verification + superset check) deletes the redundant legacy file.

It is a one-time, RE-RUNNABLE migration CLI.  Dry-run is the DEFAULT — you
must pass --apply to perform any file mutations.

SAFETY RULES
------------
* Never touches a "live" session (recently modified or no terminal event).
* Never deletes transcript.jsonl, metadata.json, or config.md.
* Archives the legacy events.jsonl to a tar before deleting it.
* Deletion is gated on BOTH verify gates AND the superset check passing.
* Fully idempotent: a second run skips sessions already marked complete.

BUCKETS
-------
pre_ci     Legacy events.jsonl only; no CI directory yet.
double     Both legacy events.jsonl AND context-intelligence/events.jsonl.
ci_only    Only context-intelligence/events.jsonl; legacy already gone.
live       Recently modified or no session:end; skipped entirely.

FLAGS
-----
--projects-root PATH
    Root directory containing per-project session directories.
    Default: ~/.amplifier/projects

--server-url URL
    Base URL of the CI server (e.g. http://localhost:7474).
    Resolved via: CLI flag > AMPLIFIER_CI_SERVER_URL env var > settings.yaml.

--api-key KEY
    Bearer token for the CI server API.
    Resolved via: CLI flag > AMPLIFIER_CI_API_KEY env var > settings.yaml.

--apply
    Actually perform the migration.  Without this flag the tool runs in
    dry-run mode: it classifies sessions and prints the plan, but makes
    zero changes on disk.

--yes
    Skip the interactive "Type yes to proceed" confirmation.  Requires --apply.
    Useful for scripted / automated runs.

--safety-window-hours N
    Sessions whose files were modified within the last N hours are classified
    as "live" and skipped.  Default: 24.0.

--ledger PATH
    Path to the append-only JSONL ledger.  Records every phase transition for
    every session.  Used to resume interrupted runs idempotently.
    Default: ~/.amplifier/migrate-ledger.jsonl

--archive-dir PATH
    Directory where pre-deletion tars are stored.
    Default: ~/.amplifier/migrate-archive

EXIT CODES
----------
0   Migration completed; no failures.
1   Migration completed with at least one session failure.
2   Bad invocation or preflight failure (server unreachable / bad credentials).
"""

# ---------------------------------------------------------------------------
# Custom help actions (mirrors the sibling upload tool)
# ---------------------------------------------------------------------------


class _CompactHelpAction(argparse.Action):
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

    def __call__(self, parser: argparse.ArgumentParser, *_args: object, **_kwargs: object) -> None:  # type: ignore[override]
        sys.stdout.write(_COMPACT_HELP)
        parser.exit(0)


class _DetailedHelpAction(argparse.Action):
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

    def __call__(self, parser: argparse.ArgumentParser, *_args: object, **_kwargs: object) -> None:  # type: ignore[override]
        sys.stdout.write(_DETAILED_HELP)
        parser.exit(0)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-intelligence-migrate",
        add_help=False,
    )

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
    parser.add_argument(
        "--projects-root",
        default=None,
        dest="projects_root",
        metavar="PATH",
        help=f"Sessions root directory (default: {DEFAULT_PROJECTS_ROOT})",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        dest="server_url",
        metavar="URL",
        help="CI server base URL",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        dest="api_key",
        metavar="KEY",
        help="Bearer token for the CI server",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Perform destructive migration (default: dry-run)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip interactive confirmation (requires --apply)",
    )
    parser.add_argument(
        "--safety-window-hours",
        type=float,
        default=24.0,
        dest="safety_window_hours",
        metavar="N",
        help="Skip sessions modified within the last N hours (default: 24.0)",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        dest="ledger",
        metavar="PATH",
        help=f"Ledger file path (default: {DEFAULT_LEDGER_PATH})",
    )
    parser.add_argument(
        "--archive-dir",
        default=None,
        dest="archive_dir",
        metavar="PATH",
        help=f"Archive directory (default: {DEFAULT_ARCHIVE_DIR})",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point — synchronous, exits with an appropriate code."""
    parser = _build_parser()
    args = parser.parse_args()

    # 0. Resolve server config — CLI flags > env vars > settings.yaml
    from context_intelligence.config import resolve_config

    try:
        server_url, api_key = resolve_config(
            server_url=args.server_url,
            api_key=args.api_key,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not resolve server config: {exc}", file=sys.stderr)
        sys.exit(2)

    if not server_url:
        print(
            "error: --server-url is required (or set AMPLIFIER_CI_SERVER_URL / settings.yaml)",
            file=sys.stderr,
        )
        sys.exit(2)

    if not api_key:
        print(
            "error: --api-key is required (or set AMPLIFIER_CI_API_KEY / settings.yaml)",
            file=sys.stderr,
        )
        sys.exit(2)

    # 1. Resolve paths
    projects_root = Path(
        args.projects_root if args.projects_root is not None else DEFAULT_PROJECTS_ROOT
    ).expanduser()

    ledger_path = Path(args.ledger if args.ledger is not None else DEFAULT_LEDGER_PATH).expanduser()

    archive_dir = Path(
        args.archive_dir if args.archive_dir is not None else DEFAULT_ARCHIVE_DIR
    ).expanduser()

    dry_run = not args.apply

    # 2. Run migration
    from .verify import preflight

    # Quick preflight check: if dry-run we still check credentials before scanning
    pf = preflight(server_url, api_key)
    if not pf.ok:
        print(f"error: preflight failed: {pf.reason}", file=sys.stderr)
        sys.exit(2)

    report = run_migration(
        projects_root=projects_root,
        server_url=server_url,
        api_key=api_key,
        dry_run=dry_run,
        safety_window_hours=args.safety_window_hours,
        ledger_path=ledger_path,
        archive_dir=archive_dir,
        assume_yes=args.yes,
    )

    # 3. Write report JSON to stdout
    report_dict = {
        "counts": report.counts,
        "processed": report.processed,
        "deleted": report.deleted,
        "skipped": report.skipped,
        "failed": report.failed,
        "details": report.details,
    }
    sys.stdout.write(json.dumps(report_dict, indent=2) + "\n")

    # 4. Exit 0 if no failures, else 1
    sys.exit(0 if report.failed == 0 else 1)
