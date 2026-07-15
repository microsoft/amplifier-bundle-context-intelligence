"""Dependency-direction guard for the upload tool (Phase 1 hygiene item I).

The upload tool is the SURVIVOR; amplifier-ci-migrate is the DOOMED tool. The
dependency arrow must only ever point doomed -> survivor: migrate may depend
on (import from) the upload tool, but the upload tool must NEVER depend on
amplifier-ci-migrate.

The Phase 3 Task 11 T1 differential test imports migrate as a test-only
oracle for comparison purposes -- it is relocated into MIGRATE/tests/
specifically so that the upload tool's package and its declared dependencies
stay free of any reference to migrate. This test guards that invariant by
asserting the upload tool's pyproject.toml never mentions "migrate".
"""

from __future__ import annotations

from pathlib import Path


def test_upload_pyproject_has_no_migrate_reference() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "migrate" not in pyproject.lower(), (
        "upload tool must not reference amplifier-ci-migrate — the dependency "
        "arrow points doomed->survivor, never the reverse"
    )
