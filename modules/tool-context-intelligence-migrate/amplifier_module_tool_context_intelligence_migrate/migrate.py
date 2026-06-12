"""Migration orchestrator — report → dry-run → confirm → pipeline → gated delete."""

from __future__ import annotations

import sys
import tarfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classify import DEFAULT_SAFETY_WINDOW_HOURS, scan_projects
from .ledger import already_complete, append_entry, read_ledger
from .transform import SchemaVersionError, is_content_superset, transform_session
from .verify import CypherClient, VerifyResult, preflight, verify_session

# ---------------------------------------------------------------------------
# Upload imports (delegated; mockable in unit tests)
# ---------------------------------------------------------------------------

from amplifier_module_tool_context_intelligence_upload.progress import (
    ProgressTracker,
    progress_file_path,
)
from amplifier_module_tool_context_intelligence_upload.session_graph import discover_and_sort
from amplifier_module_tool_context_intelligence_upload.uploader import run_upload

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PROJECTS_ROOT = "~/.amplifier/projects"
DEFAULT_ARCHIVE_DIR = "~/.amplifier/migrate-archive"
DEFAULT_LEDGER_PATH = "~/.amplifier/migrate-ledger.jsonl"


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------


@dataclass
class MigrationReport:
    counts: dict[str, int] = field(
        default_factory=lambda: {"pre_ci": 0, "double": 0, "ci_only": 0, "live": 0}
    )
    processed: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Archive helper
# ---------------------------------------------------------------------------


def archive_originals(
    session_dir: Path,
    archive_dir: Path,
    *,
    project_slug: str,
    session_id: str,
) -> Path:
    """Tar ONLY the legacy ``events.jsonl`` into *archive_dir*.

    **Never archives** ``transcript.jsonl``, ``metadata.json``, or ``config.md``
    — those files are NEVER deleted.

    Returns the tar path: ``archive_dir/{project_slug}__{session_id}.tar``.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)
    tar_path = archive_dir / f"{project_slug}__{session_id}.tar"
    legacy_events = session_dir / "events.jsonl"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(legacy_events, arcname="events.jsonl")
    return tar_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_jsonl_lines(path: Path) -> int:
    """Count non-empty lines in a JSONL file."""
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _make_entry(
    session_id: str,
    project_slug: str,
    bucket: str,
    phase: str,
    *,
    workspace: str = "",
    jsonl_lines: int | None = None,
    graph_count: int | None = None,
    archive_path: str | None = None,
    error: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a ledger entry dict."""
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "project_slug": project_slug,
        "bucket": bucket,
        "phase": phase,
        "workspace": workspace,
        "jsonl_lines": jsonl_lines,
        "graph_count": graph_count,
        "archive_path": archive_path,
        "error": error,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_migration(
    *,
    projects_root: Path,
    server_url: str,
    api_key: str,
    dry_run: bool = True,
    safety_window_hours: float = DEFAULT_SAFETY_WINDOW_HOURS,
    ledger_path: Path,
    archive_dir: Path,
    assume_yes: bool = False,
    confirm: Callable[..., str] = input,
) -> MigrationReport:
    """Orchestrate the full migration.

    Steps:
    1. PREFLIGHT: verify server + credentials.  Fail → abort.
    2. CLASSIFY: scan projects, bucket sessions.  Print report.
    3. If dry_run: print plan and RETURN (no mutation).
    4. CONFIRM: unless assume_yes, require "yes" interactively.
    5. PER SESSION (skip live; skip ledger-complete):
         a. transform  (pre_ci/double only)
         b. archive_originals  (only if legacy events.jsonl exists)
         c. upload (discover_and_sort + run_upload)
         d. verify (gate A + gate B)
         e. superset check (pre_ci/double only)
         f. delete legacy events.jsonl (gated on d+e passing)
       ci_only: d only (verify), then ledger 'verified'.
    6. Return MigrationReport.
    """
    report = MigrationReport()

    # --- Step 1: PREFLIGHT -----------------------------------------------
    pf = preflight(server_url, api_key)
    if not pf.ok:
        print(f"PREFLIGHT FAILED: {pf.reason}", file=sys.stderr)
        report.details.append({"preflight": "failed", "reason": pf.reason})
        return report

    # --- Step 2: CLASSIFY ------------------------------------------------
    sessions = scan_projects(projects_root, safety_window_hours=safety_window_hours)
    for s in sessions:
        report.counts[s.bucket] = report.counts.get(s.bucket, 0) + 1

    total = sum(report.counts.values())
    print(
        f"\nClassification: {total} sessions total — "
        + ", ".join(f"{k}={v}" for k, v in sorted(report.counts.items())),
        file=sys.stderr,
    )

    # --- Step 3: DRY-RUN RETURN ------------------------------------------
    if dry_run:
        print(
            "DRY-RUN mode (pass --apply to execute); no changes made.",
            file=sys.stderr,
        )
        return report

    # --- Step 4: CONFIRM -------------------------------------------------
    if not assume_yes:
        to_process = report.counts.get("pre_ci", 0) + report.counts.get("double", 0)
        prompt = (
            f"About to migrate {to_process} session(s) "
            f"({report.counts.get('pre_ci', 0)} pre_ci, "
            f"{report.counts.get('double', 0)} double). "
            "Type 'yes' to proceed: "
        )
        answer = confirm(prompt)
        if answer.strip().lower() != "yes":
            print("Aborted (user did not confirm).", file=sys.stderr)
            return report

    # --- Step 5: PER-SESSION PIPELINE ------------------------------------
    ledger_entries = read_ledger(ledger_path)
    cypher_client = CypherClient(server_url, api_key)

    for sess in sessions:
        if sess.bucket == "live":
            report.skipped += 1
            continue

        if already_complete(ledger_entries, sess.session_id):
            report.skipped += 1
            continue

        detail: dict[str, Any] = {
            "session_id": sess.session_id,
            "bucket": sess.bucket,
            "project_slug": sess.project_slug,
        }
        workspace = ""
        archive_path_str: str | None = None

        # --- Step 5a: TRANSFORM (pre_ci / double) ------------------------
        if sess.bucket in ("pre_ci", "double"):
            assert sess.legacy_events is not None
            try:
                ci_events_path, _ = transform_session(
                    sess.legacy_events,
                    sess.ci_dir,
                    session_dir=sess.session_dir,
                )
                # Derive workspace from the written CI file
                import json as _json

                for line in sess.ci_events.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                        workspace = rec.get("workspace", "")
                    except _json.JSONDecodeError:
                        pass
                    if workspace:
                        break

                append_entry(
                    ledger_path,
                    _make_entry(
                        sess.session_id,
                        sess.project_slug,
                        sess.bucket,
                        "transformed",
                        workspace=workspace,
                    ),
                )
                ledger_entries = read_ledger(ledger_path)
            except SchemaVersionError as exc:
                append_entry(
                    ledger_path,
                    _make_entry(
                        sess.session_id,
                        sess.project_slug,
                        sess.bucket,
                        "failed",
                        error=str(exc),
                    ),
                )
                ledger_entries = read_ledger(ledger_path)
                detail["error"] = str(exc)
                report.failed += 1
                report.details.append(detail)
                continue
            except Exception as exc:  # noqa: BLE001
                append_entry(
                    ledger_path,
                    _make_entry(
                        sess.session_id,
                        sess.project_slug,
                        sess.bucket,
                        "failed",
                        error=str(exc),
                    ),
                )
                ledger_entries = read_ledger(ledger_path)
                detail["error"] = str(exc)
                report.failed += 1
                report.details.append(detail)
                continue

            # --- Step 5b: ARCHIVE ----------------------------------------
            if sess.legacy_events is not None and sess.legacy_events.exists():
                try:
                    tar_path = archive_originals(
                        sess.session_dir,
                        archive_dir,
                        project_slug=sess.project_slug,
                        session_id=sess.session_id,
                    )
                    archive_path_str = str(tar_path)
                    append_entry(
                        ledger_path,
                        _make_entry(
                            sess.session_id,
                            sess.project_slug,
                            sess.bucket,
                            "archived",
                            workspace=workspace,
                            archive_path=archive_path_str,
                        ),
                    )
                    ledger_entries = read_ledger(ledger_path)
                except Exception as exc:  # noqa: BLE001
                    append_entry(
                        ledger_path,
                        _make_entry(
                            sess.session_id,
                            sess.project_slug,
                            sess.bucket,
                            "failed",
                            error=f"archive: {exc}",
                        ),
                    )
                    ledger_entries = read_ledger(ledger_path)
                    detail["error"] = f"archive: {exc}"
                    report.failed += 1
                    report.details.append(detail)
                    continue

        # --- Step 5c: UPLOAD (all buckets except live) -------------------
        try:
            job_id = str(uuid.uuid4())
            ci_sessions = discover_and_sort(sess.ci_dir)
            tracker = ProgressTracker(
                job_id,
                progress_file_path(job_id),
                sessions_total=len(ci_sessions),
            )
            upload_result = run_upload(ci_sessions, server_url, api_key, tracker, event_delay_s=0.0)
            if not upload_result.success:
                msg = f"upload failed: {upload_result}"
                append_entry(
                    ledger_path,
                    _make_entry(
                        sess.session_id,
                        sess.project_slug,
                        sess.bucket,
                        "failed",
                        workspace=workspace,
                        error=msg,
                    ),
                )
                ledger_entries = read_ledger(ledger_path)
                detail["error"] = msg
                report.failed += 1
                report.details.append(detail)
                continue

            append_entry(
                ledger_path,
                _make_entry(
                    sess.session_id,
                    sess.project_slug,
                    sess.bucket,
                    "uploaded",
                    workspace=workspace,
                ),
            )
            ledger_entries = read_ledger(ledger_path)
        except Exception as exc:  # noqa: BLE001
            append_entry(
                ledger_path,
                _make_entry(
                    sess.session_id,
                    sess.project_slug,
                    sess.bucket,
                    "failed",
                    workspace=workspace,
                    error=f"upload: {exc}",
                ),
            )
            ledger_entries = read_ledger(ledger_path)
            detail["error"] = f"upload: {exc}"
            report.failed += 1
            report.details.append(detail)
            continue

        # --- Step 5d: VERIFY (all buckets except live) -------------------
        jsonl_lines = _count_jsonl_lines(sess.ci_events)
        try:
            verify_result: VerifyResult = verify_session(
                cypher_client,
                sess.session_id,
                ci_events_path=sess.ci_events,
            )
        except Exception as exc:  # noqa: BLE001
            append_entry(
                ledger_path,
                _make_entry(
                    sess.session_id,
                    sess.project_slug,
                    sess.bucket,
                    "failed",
                    workspace=workspace,
                    jsonl_lines=jsonl_lines,
                    error=f"verify error: {exc}",
                ),
            )
            ledger_entries = read_ledger(ledger_path)
            detail["error"] = f"verify error: {exc}"
            report.failed += 1
            report.details.append(detail)
            continue

        if not verify_result.passed:
            append_entry(
                ledger_path,
                _make_entry(
                    sess.session_id,
                    sess.project_slug,
                    sess.bucket,
                    "failed",
                    workspace=workspace,
                    jsonl_lines=jsonl_lines,
                    graph_count=verify_result.event_count_graph,
                    error=verify_result.message,
                ),
            )
            ledger_entries = read_ledger(ledger_path)
            detail["error"] = verify_result.message
            report.failed += 1
            report.details.append(detail)
            continue

        append_entry(
            ledger_path,
            _make_entry(
                sess.session_id,
                sess.project_slug,
                sess.bucket,
                "verified",
                workspace=workspace,
                jsonl_lines=jsonl_lines,
                graph_count=verify_result.event_count_graph,
            ),
        )
        ledger_entries = read_ledger(ledger_path)

        # ci_only: no more work
        if sess.bucket == "ci_only":
            report.processed += 1
            detail["phase"] = "verified"
            report.details.append(detail)
            continue

        # --- Step 5e: SUPERSET (pre_ci / double only) --------------------
        assert sess.legacy_events is not None
        try:
            is_superset = is_content_superset(sess.legacy_events, sess.ci_events)
        except Exception as exc:  # noqa: BLE001
            is_superset = False
            detail["superset_error"] = str(exc)

        if not is_superset:
            append_entry(
                ledger_path,
                _make_entry(
                    sess.session_id,
                    sess.project_slug,
                    sess.bucket,
                    "failed",
                    workspace=workspace,
                    error="superset check failed: CI events are not a superset of legacy events",
                ),
            )
            ledger_entries = read_ledger(ledger_path)
            detail["error"] = "superset check failed"
            report.failed += 1
            report.details.append(detail)
            continue

        # --- Step 5f: DELETE legacy events.jsonl -------------------------
        # Track whether unlink actually happened — the ledger 'deleted' phase
        # is only recorded when the file was physically removed.  If the file
        # is already gone (e.g. a concurrent run deleted it) we skip the entry
        # so the ledger stays truthful.
        deleted_file = False
        if sess.legacy_events is not None and sess.legacy_events.exists():
            sess.legacy_events.unlink()
            deleted_file = True

        if deleted_file:
            append_entry(
                ledger_path,
                _make_entry(
                    sess.session_id,
                    sess.project_slug,
                    sess.bucket,
                    "deleted",
                    workspace=workspace,
                    archive_path=archive_path_str,
                ),
            )
            ledger_entries = read_ledger(ledger_path)
            report.deleted += 1

        report.processed += 1
        detail["phase"] = "deleted" if deleted_file else "verified"
        report.details.append(detail)

    return report
