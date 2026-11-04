"""Command-line entry point for durable native capture recovery."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from context_intelligence.recovery_retirement import retire_job

from .recovery import RecoveryRunner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-intelligence-recover",
        description="Recover completed native Context Intelligence captures to configured destinations.",
    )
    parser.add_argument(
        "--path", action="append", type=Path, default=[], help="Additional scan root."
    )
    parser.add_argument("--state-dir", type=Path, help="Owner-only checkpoint directory.")
    parser.add_argument("--job-id", help="Resume this durable recovery job.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once", action="store_true", help="Run once (default) and report unfinished work."
    )
    mode.add_argument(
        "--watch", action="store_true", help="Retry unfinished work, honoring Retry-After."
    )
    mode.add_argument(
        "--retire-job",
        action="store_true",
        help="Permanently abandon this existing job; stop all old launchers first.",
    )
    parser.add_argument("--reason", help="Local explanation for explicit job retirement.")
    parser.add_argument(
        "--dry-run",
        "--plan",
        action="store_true",
        help="Inventory and route without network calls.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.retire_job:
        if not args.state_dir or not args.job_id or not args.reason or args.path or args.dry_run:
            parser.error("--retire-job requires --state-dir, --job-id and --reason only")
        if Path(args.job_id).name != args.job_id or args.job_id in (".", ".."):
            parser.error("--job-id must be one filename component")
        receipt = retire_job(args.state_dir / f"{args.job_id}.v2.sqlite3", reason=args.reason)
        print(json.dumps(receipt, sort_keys=True))
        return
    if args.reason:
        parser.error("--reason requires --retire-job")
    job_id = args.job_id or "default"
    state_dir = args.state_dir or (
        Path.home() / ".local" / "state" / "context-intelligence" / "recovery" / job_id
    )
    runner = RecoveryRunner(state_dir=state_dir, job_id=job_id)
    try:
        next_refresh_at = 0.0
        while True:
            now = time.monotonic()
            refresh = now >= next_refresh_at
            summary = runner.run(args.path, dry_run=args.dry_run, refresh=refresh)
            print(json.dumps(summary.to_dict(), sort_keys=True))
            if not args.watch or not summary.pending or args.dry_run:
                raise SystemExit(1 if summary.pending else 0)
            if refresh:
                next_refresh_at = time.monotonic() + 300.0
            if summary.delivered:
                continue
            # Retry deferrals are respected. When no source record advanced,
            # pause only until the next due retry or mandatory plan refresh.
            until_refresh = max(0.0, next_refresh_at - time.monotonic())
            delay = summary.retry_after_s if summary.retry_after_s is not None else until_refresh
            time.sleep(max(0.1, min(until_refresh, delay)))
    finally:
        runner.close()


if __name__ == "__main__":
    main()
