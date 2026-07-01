"""Docs-verification tests for the auto-recovery dispatch documentation.

Verifies that:
- The README config table documents the dispatch_read_timeout knob and its env var.
- The README auto-recovery section covers the full architecture/flow vocabulary.
- Both .dot diagram files name the configurable read-timeout knob.
- Both .dot diagram files parse cleanly via `dot -Tcanon`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Bundle root: tests/ -> hook-context-intelligence/ -> modules/ -> amplifier-bundle-context-intelligence/
_BUNDLE_ROOT = Path(__file__).resolve().parents[3]

_README = _BUNDLE_ROOT / "README.md"

_DOT_FILES = [
    _BUNDLE_ROOT / "docs" / "dispatch-circuit-breaker.dot",
    _BUNDLE_ROOT / "docs" / "dispatch-auto-recovery-lifecycle.dot",
]


def _readme_text() -> str:
    return _README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# README config-reference row
# ---------------------------------------------------------------------------


def test_readme_documents_read_timeout_row() -> None:
    """The README config table must include a row for dispatch_read_timeout.

    The row must name both the config key and the env-var placeholder so that
    users can discover how to tune the HTTP read timeout.
    """
    text = _readme_text()
    assert "dispatch_read_timeout" in text, (
        "README is missing the 'dispatch_read_timeout' config key"
    )
    assert "AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_READ_TIMEOUT" in text, (
        "README is missing the env-var 'AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_READ_TIMEOUT'"
    )


# ---------------------------------------------------------------------------
# README auto-recovery flow section vocabulary
# ---------------------------------------------------------------------------


def test_readme_has_auto_recovery_flow_section() -> None:
    """The README must cover the full auto-recovery architecture vocabulary.

    Checks that the narrative (any case) contains:
    - 'auto-recovery'      — section heading / concept name
    - 'retry-in-place'     — in-place retry terminology
    - 'full-jitter'        — backoff algorithm name
    - 'degraded'           — state name
    - 'overflow'           — queue-full scenario
    - 'drain'              — shutdown drain concept
    """
    lower = _readme_text().lower()
    required_tokens = [
        "auto-recovery",
        "retry-in-place",
        "full-jitter",
        "degraded",
        "overflow",
        "drain",
    ]
    missing = [tok for tok in required_tokens if tok not in lower]
    assert not missing, (
        f"README auto-recovery section is missing vocabulary tokens: {missing}"
    )


# ---------------------------------------------------------------------------
# .dot diagram files — read_timeout knob
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dot_path", _DOT_FILES, ids=[p.name for p in _DOT_FILES])
def test_dot_mentions_read_timeout_knob(dot_path: Path) -> None:
    """Each .dot diagram must name the configurable read-timeout knob.

    Acceptable spellings: 'read_timeout' or 'read timeout' (case-insensitive).
    """
    text = dot_path.read_text(encoding="utf-8").lower()
    assert ("read_timeout" in text) or ("read timeout" in text), (
        f"{dot_path.name} does not mention the read-timeout knob "
        f"('read_timeout' or 'read timeout' not found)"
    )


# ---------------------------------------------------------------------------
# .dot diagram files — graphviz parse check
# ---------------------------------------------------------------------------


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
        f"`dot -Tcanon {dot_path.name}` failed (exit {result.returncode}):\n"
        f"stderr: {result.stderr}"
    )
