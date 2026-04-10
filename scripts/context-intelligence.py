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
import logging
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path manipulation to ensure context_intelligence is importable
# when running the script directly (not installed as a package).
# ---------------------------------------------------------------------------

_here = Path(__file__).resolve().parent
_root = _here.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from context_intelligence import CIClient  # noqa: E402
from context_intelligence.config import resolve_config  # noqa: E402, F401

import context_intelligence.config as _ci_config  # noqa: E402
import context_intelligence.reconstruct.discover as _ci_discover  # noqa: E402
import context_intelligence.reconstruct.events as _ci_events  # noqa: E402
import context_intelligence.reconstruct.metadata as _ci_metadata  # noqa: E402
import context_intelligence.reconstruct.transcript as _ci_transcript  # noqa: E402

log = logging.getLogger("context_intelligence_cli")


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
    # ── Logging ──────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Resolve configuration ─────────────────────────────────────────────────
    server_url, api_key = _ci_config.resolve_config(
        server_url=args.server_url,
        api_key=args.api_key,
    )
    client = CIClient(server_url, api_key)

    # Determine what to reconstruct.
    # When any --*-only flag is set, only that artifact is produced.
    only_flags = (args.events_only, args.transcript_only, args.metadata_only)
    any_only = any(only_flags)
    do_events = args.events_only if any_only else True
    do_transcript = args.transcript_only if any_only else True
    do_metadata = args.metadata_only if any_only else True

    # ── Derive workspace and session directory ────────────────────────────────
    workspace = _ci_discover.workspace_slug(args.project_dir)
    sessions_dir = _ci_discover.sessions_dir_for_project(args.project_dir)
    log.info("Project dir : %s", os.path.abspath(args.project_dir))
    log.info("Workspace   : %s", workspace)
    log.info("Sessions dir: %s", sessions_dir)
    log.info("Server      : %s", server_url)
    log.info(
        "Reconstruct : %s",
        " + ".join(
            (["events.jsonl"] if do_events else [])
            + (["transcript.jsonl"] if do_transcript else [])
            + (["metadata.json"] if do_metadata else [])
        ),
    )
    if args.resolve_blobs:
        log.info("Blob resolution: ENABLED")

    # ── Discover sessions ─────────────────────────────────────────────────────
    log.info("Discovering sessions for workspace %s ...", workspace)
    sessions, disk_only_ids = _ci_discover.discover_sessions(client, workspace, sessions_dir)

    # Filter to a specific session if requested
    if args.session:
        prefix = args.session
        sessions = [s for s in sessions if (s.get("s.node_id") or "").startswith(prefix)]
        disk_only_ids = [sid for sid in disk_only_ids if sid.startswith(prefix)]

    if not sessions and not disk_only_ids:
        log.warning("No sessions found for workspace %s", workspace)
        return 0

    log.info(
        "Found %d graph session(s), %d disk-only session(s)",
        len(sessions),
        len(disk_only_ids),
    )

    # ── Process each session ──────────────────────────────────────────────────
    total_count = len(sessions) + len(disk_only_ids)
    stats: dict[str, int] = {
        "total": total_count,
        "disk_only": len(disk_only_ids),
        "events_written": 0,
        "events_skipped": 0,
        "transcript_written": 0,
        "transcript_skipped": 0,
        "metadata_written": 0,
        "metadata_skipped": 0,
        "errors": 0,
    }
    start_time = time.time()

    for idx, session in enumerate(sessions, 1):
        session_id = session.get("s.node_id", "")
        status = session.get("s.status", "")
        started = session.get("s.started_at", "")

        log.info(
            "[%d/%d] Session %s (status=%s, started=%s)",
            idx,
            stats["total"],
            session_id[:12],
            status,
            (started or "?")[:19],
        )

        session_dir = sessions_dir / session_id
        events_path = session_dir / "events.jsonl"
        transcript_path = session_dir / "transcript.jsonl"

        # ── events.jsonl ──────────────────────────────────────────────────────
        if do_events:
            if events_path.exists() and not args.force:
                log.info("  events.jsonl exists, skipping (use --force to overwrite)")
                stats["events_skipped"] += 1
            else:
                try:
                    events = _ci_events.extract_events(
                        client,
                        session_id,
                        workspace,
                        resolve_blobs=args.resolve_blobs,
                    )
                    if events:
                        if args.dry_run:
                            log.info(
                                "  [DRY RUN] Would write events.jsonl (%d events)",
                                len(events),
                            )
                        else:
                            n = write_jsonl(events_path, events)
                            log.info("  Wrote events.jsonl (%d events)", n)
                        stats["events_written"] += 1
                    else:
                        log.info("  No events found, skipping events.jsonl")
                        stats["events_skipped"] += 1
                except Exception as exc:
                    log.error("  Failed to extract events: %s", exc, exc_info=args.verbose)
                    stats["errors"] += 1

        # ── transcript.jsonl ──────────────────────────────────────────────────
        if do_transcript:
            if transcript_path.exists() and not args.force:
                log.info("  transcript.jsonl exists, skipping (use --force to overwrite)")
                stats["transcript_skipped"] += 1
            else:
                try:
                    messages = _ci_transcript.extract_transcript(client, session_id, workspace)
                    if messages:
                        if args.dry_run:
                            log.info(
                                "  [DRY RUN] Would write transcript.jsonl (%d messages)",
                                len(messages),
                            )
                        else:
                            n = write_jsonl(transcript_path, messages)
                            log.info("  Wrote transcript.jsonl (%d messages)", n)
                        stats["transcript_written"] += 1
                    else:
                        log.info("  No messages found, skipping transcript.jsonl")
                        stats["transcript_skipped"] += 1
                except Exception as exc:
                    log.error("  Failed to extract transcript: %s", exc, exc_info=args.verbose)
                    stats["errors"] += 1

        # ── metadata.json ─────────────────────────────────────────────────────
        if do_metadata:
            metadata_path = session_dir / "metadata.json"
            if metadata_path.exists() and not args.force:
                log.info("  metadata.json exists, skipping (use --force to overwrite)")
                stats["metadata_skipped"] += 1
            else:
                try:
                    metadata = _ci_metadata.extract_metadata(client, workspace, session_id)
                    if metadata:
                        if args.dry_run:
                            log.info(
                                "  [DRY RUN] Would write metadata.json (%s)",
                                metadata.get("bundle", "no bundle"),
                            )
                        else:
                            write_json(metadata_path, metadata)
                            bundle_info = metadata.get("bundle", "no bundle")
                            log.info("  Wrote metadata.json (bundle=%s)", bundle_info)
                        stats["metadata_written"] += 1
                    else:
                        log.info("  No metadata extracted, skipping metadata.json")
                        stats["metadata_skipped"] += 1
                except Exception as exc:
                    log.error("  Failed to extract metadata: %s", exc, exc_info=args.verbose)
                    stats["errors"] += 1

    # ── Process disk-only sessions ────────────────────────────────────────────
    if disk_only_ids and do_metadata:
        graph_count = len(sessions)
        for didx, disk_sid in enumerate(disk_only_ids, 1):
            overall_idx = graph_count + didx
            session_dir = sessions_dir / disk_sid

            log.info(
                "[%d/%d] Session %s (disk-only)",
                overall_idx,
                stats["total"],
                disk_sid[:12],
            )

            metadata_path = session_dir / "metadata.json"
            if metadata_path.exists() and not args.force:
                log.info("  metadata.json exists, skipping (use --force to overwrite)")
                stats["metadata_skipped"] += 1
            else:
                try:
                    metadata = _ci_metadata.build_disk_only_metadata(disk_sid, session_dir)
                    if args.dry_run:
                        log.info("  [DRY RUN] Would write metadata.json (disk-only)")
                    else:
                        write_json(metadata_path, metadata)
                        log.info("  Wrote metadata.json (disk-only)")
                    stats["metadata_written"] += 1
                except Exception as exc:
                    log.error(
                        "  Failed to build disk-only metadata: %s",
                        exc,
                        exc_info=args.verbose,
                    )
                    stats["errors"] += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    log.info("")
    log.info("═══ Summary ════════════════════════════════════════════════════════")
    log.info("Sessions processed : %d", stats["total"])
    if stats["disk_only"]:
        log.info("  (disk-only       : %d)", stats["disk_only"])
    if do_events:
        log.info(
            "events.jsonl       : %d written, %d skipped",
            stats["events_written"],
            stats["events_skipped"],
        )
    if do_transcript:
        log.info(
            "transcript.jsonl   : %d written, %d skipped",
            stats["transcript_written"],
            stats["transcript_skipped"],
        )
    if do_metadata:
        log.info(
            "metadata.json      : %d written, %d skipped",
            stats["metadata_written"],
            stats["metadata_skipped"],
        )
    if stats["errors"]:
        log.info("Errors             : %d", stats["errors"])
    log.info("Time elapsed       : %.1fs", elapsed)
    if args.dry_run:
        log.info("(DRY RUN \u2014 no files were written)")
    log.info("═" * 63)

    return 1 if stats["errors"] else 0


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
