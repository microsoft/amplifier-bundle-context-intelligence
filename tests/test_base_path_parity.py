# pyright: reportMissingImports=false
# (pytest is a test-only dep; the hook module is resolved at runtime via the
#  sys.path insert below — neither is visible to the static type checker here.)
"""Parity + consistency-check tests for relocation base_path (§D.2 / §C.3).

Two **duplicated-by-design** canonicalizers exist because the fold gate forbids the
hook's ``config_resolver`` from importing the reader package:

  - reader: ``context_intelligence.config.canonicalize_base_path``
  - writer: ``HookConfigResolver.base_path`` (byte-equivalent inline copy)

These tests PIN ``writer ≡ reader`` so the hand-synced copies cannot drift silently,
and PIN the §C.3 divergence condition (``reader_writer_roots_disagree``) that the
hook's ``on_session_ready`` uses to decide whether to warn LOUD. Both were previously
only verified by hand; this freezes them in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The writer-side canonicalizer lives in the hook module package, which is not on
# the default import path for the root test suite. Add it (the package __init__ is
# import-clean — handlers / amplifier_core are imported lazily inside functions).
REPO_ROOT = Path(__file__).parent.parent
HOOK_MODULE_DIR = REPO_ROOT / "modules" / "hook-context-intelligence"
if str(HOOK_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_MODULE_DIR))

from amplifier_module_hook_context_intelligence.config_resolver import (  # noqa: E402
    HookConfigResolver,
)

from context_intelligence.config import (  # noqa: E402
    DEFAULT_BASE_PATH,
    canonicalize_base_path,
    reader_writer_roots_disagree,
)


def _writer_root(value: str) -> Path:
    """Drive the REAL writer property with ``config['base_path'] = value``."""
    return HookConfigResolver(config={"base_path": value}, coordinator=None).base_path


# The input vector that was previously checked by hand — now pinned.
PARITY_INPUTS = [
    "${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:}",  # unexpanded placeholder → default
    "/tmp/relocated",  # absolute → used as-is
    "~/relocated",  # tilde → expanded
    "relative/bad",  # relative → default
    "",  # empty → default
    "   ",  # whitespace → default
]


class TestWriterReaderParity:
    """writer ≡ reader for every input shape (the duplication's safety net)."""

    @pytest.mark.parametrize("value", PARITY_INPUTS)
    def test_writer_equals_reader(self, value: str) -> None:
        writer = _writer_root(value)
        reader = canonicalize_base_path(value)
        assert writer == reader, f"writer/reader drift on {value!r}: {writer} != {reader}"

    @pytest.mark.parametrize("value", PARITY_INPUTS)
    def test_writer_always_absolute(self, value: str) -> None:
        assert _writer_root(value).is_absolute()

    def test_unexpanded_placeholder_is_default_silently(self, caplog) -> None:
        """A literal ``${...}`` (host did not expand) → default, with NO noisy warning."""
        with caplog.at_level("WARNING"):
            root = _writer_root("${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:}")
        assert root == DEFAULT_BASE_PATH
        assert not any("not absolute" in r.getMessage() for r in caplog.records)


class TestConsistencyDivergence:
    """Pins the §C.3 condition consumed by on_session_ready."""

    def test_agree_when_env_matches_writer(self) -> None:
        disagree, reader, writer = reader_writer_roots_disagree(
            "/tmp/relocated", Path("/tmp/relocated")
        )
        assert disagree is False
        assert reader == writer

    def test_agree_when_both_default(self) -> None:
        # env unset (None) and writer at default → consistent.
        disagree, _reader, _writer = reader_writer_roots_disagree(None, DEFAULT_BASE_PATH)
        assert disagree is False

    def test_disagree_when_writer_relocated_but_env_unset(self) -> None:
        # The exact trap fix #1 exists to catch: relocation via config.base_path
        # with the env var unset — env-only readers cannot see it.
        disagree, reader, writer = reader_writer_roots_disagree(None, Path("/data/ci"))
        assert disagree is True
        assert reader == DEFAULT_BASE_PATH
        assert writer == Path("/data/ci")

    def test_disagree_when_env_and_writer_differ(self) -> None:
        disagree, _reader, _writer = reader_writer_roots_disagree("/a/one", Path("/b/two"))
        assert disagree is True
