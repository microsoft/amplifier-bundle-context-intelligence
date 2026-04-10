#!/usr/bin/env python3
"""Unified CLI for context-intelligence operations.

Subcommands:
    reconstruct  -- Reconstruct local session files from the graph server
    upload       -- Replay session events to the server (delegates to existing module)
    status       -- Check server health and session statistics
    query        -- Run ad-hoc Cypher queries against the graph

All subcommands support:
    --server-url     CI server URL (env: AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL)
    --api-key        CI server API key (env: AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY)

Human-readable messages go to stderr; structured data goes to stdout.

Exit codes:
    0 -- success
    1 -- error
    2 -- invalid arguments
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path manipulation to ensure context_intelligence is importable
# when running the script directly (not installed as a package).
# ---------------------------------------------------------------------------

_here = Path(__file__).resolve().parent
_root = _here.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from context_intelligence import CIClient, resolve_config  # noqa: E402, F401


# ---------------------------------------------------------------------------
# File writing helpers
# ---------------------------------------------------------------------------


def write_json(path: Path, data: dict) -> None:
    """Write a dict as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")


def write_jsonl(path: Path, records: list[dict]) -> int:
    """Write records as JSONL. Returns number of lines written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
    return len(records)


# ---------------------------------------------------------------------------
# Shared CLI arguments
# ---------------------------------------------------------------------------


def _add_server_args(parser: argparse.ArgumentParser) -> None:
    """Add --server-url and --api-key to a subparser."""
    parser.add_argument(
        "--server-url",
        default=None,
        help="CI server URL (env: AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="CI server API key (env: AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY)",
    )


# ---------------------------------------------------------------------------
# Subcommand implementations are in the functions below.
# Each is registered with argparse via set_defaults(func=...).
# Placeholder — subcommand functions are added in Tasks 11-14.
# ---------------------------------------------------------------------------


def cmd_reconstruct(args: argparse.Namespace) -> int:
    """Reconstruct local session files from the graph server. (Task 11)"""
    raise NotImplementedError("cmd_reconstruct is implemented in Task 11")


def cmd_upload(args: argparse.Namespace) -> int:
    """Replay session events to the server. (Task 12)"""
    raise NotImplementedError("cmd_upload is implemented in Task 12")


def cmd_status(args: argparse.Namespace) -> int:
    """Check server health and session statistics. (Task 13)"""
    raise NotImplementedError("cmd_status is implemented in Task 13")


def cmd_query(args: argparse.Namespace) -> int:
    """Run ad-hoc Cypher queries against the graph. (Task 14)"""
    raise NotImplementedError("cmd_query is implemented in Task 14")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns exit code: 0 = success, 1 = error, 2 = invalid args.
    """
    parser = argparse.ArgumentParser(
        prog="context-intelligence",
        description="Unified CLI for context-intelligence operations.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")
    subparsers.required = True

    # -- reconstruct --
    recon_p = subparsers.add_parser(
        "reconstruct",
        help="Reconstruct local session files from the graph server.",
    )
    recon_p.add_argument(
        "--project-dir",
        default=os.getcwd(),
        help="Amplifier project directory (default: cwd)",
    )
    recon_p.add_argument("--events-only", action="store_true", help="Only reconstruct events.jsonl")
    recon_p.add_argument(
        "--transcript-only", action="store_true", help="Only reconstruct transcript.jsonl"
    )
    recon_p.add_argument(
        "--metadata-only", action="store_true", help="Only reconstruct metadata.json"
    )
    recon_p.add_argument("--force", action="store_true", help="Overwrite existing files")
    recon_p.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without writing files"
    )
    recon_p.add_argument(
        "--resolve-blobs",
        action="store_true",
        default=False,
        help="Resolve $blob_ref URIs and inline full content in events.jsonl",
    )
    recon_p.add_argument(
        "--session", default=None, help="Reconstruct a specific session ID (or prefix) only"
    )
    recon_p.add_argument("-v", "--verbose", action="store_true", help="Enable detailed logging")
    _add_server_args(recon_p)
    recon_p.set_defaults(func=cmd_reconstruct)

    # -- upload --
    upload_p = subparsers.add_parser(
        "upload",
        help="Replay session events to the server.",
    )
    upload_p.add_argument("--path", required=True, metavar="PATH", help="File or folder to replay")
    upload_p.add_argument("--job-id", default=None, metavar="ID", help="Job identifier")
    upload_p.add_argument("--progress", default=None, metavar="FILE", help="Progress file path")
    upload_p.add_argument(
        "--event-delay-ms",
        type=int,
        default=0,
        metavar="MS",
        dest="event_delay_ms",
        help="Milliseconds to sleep between events (default: 0)",
    )
    _add_server_args(upload_p)
    upload_p.set_defaults(func=cmd_upload)

    # -- status --
    status_p = subparsers.add_parser(
        "status",
        help="Check server health and session statistics.",
    )
    status_p.add_argument("--workspace", default=None, help="Filter by workspace slug")
    _add_server_args(status_p)
    status_p.set_defaults(func=cmd_status)

    # -- query --
    query_p = subparsers.add_parser(
        "query",
        help="Run ad-hoc Cypher queries against the graph.",
    )
    query_p.add_argument("cypher", help="Cypher query string")
    query_p.add_argument("--workspace", default="*", help="Workspace scope (default: *)")
    _add_server_args(query_p)
    query_p.set_defaults(func=cmd_query)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
