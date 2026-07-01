"""Docs-verification tests for the auto-recovery dispatch documentation.

Only structural properties are verified — not wording:
- Both .dot diagram files parse cleanly via `dot -Tcanon`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Bundle root: tests/ -> hook-context-intelligence/ -> modules/ -> amplifier-bundle-context-intelligence/
_BUNDLE_ROOT = Path(__file__).resolve().parents[3]

_DOT_FILES = [
    _BUNDLE_ROOT / "docs" / "dispatch-circuit-breaker.dot",
    _BUNDLE_ROOT / "docs" / "dispatch-auto-recovery-lifecycle.dot",
]


@pytest.mark.parametrize("dot_path", _DOT_FILES, ids=[p.name for p in _DOT_FILES])
def test_dot_parses_with_graphviz(dot_path: Path) -> None:
    """Each .dot file must parse cleanly via `dot -Tcanon` (exit code 0).

    Skipped when the `dot` binary is not installed (not a failure).
    """
    dot_bin = shutil.which("dot")
    if dot_bin is None:
        pytest.skip("graphviz 'dot' binary not found; skipping parse check")

    result = subprocess.run(
        [dot_bin, "-Tcanon", str(dot_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`dot -Tcanon {dot_path.name}` failed (exit {result.returncode}):\nstderr: {result.stderr}"
    )
