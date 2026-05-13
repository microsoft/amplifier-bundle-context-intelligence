"""Tests for the context_intelligence package skeleton (task-2).

Verifies:
- All 4 required files exist
- The context_intelligence package imports successfully
- The context_intelligence.reconstruct subpackage imports successfully
- The context_intelligence.upload subpackage imports successfully
- The py.typed PEP 561 marker is present (empty file)
- Module docstrings are meaningful and present
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CI_ROOT = REPO_ROOT / "context_intelligence"


class TestFilesExist:
    """All 4 required skeleton files must exist."""

    def test_context_intelligence_init_exists(self):
        """context_intelligence/__init__.py must exist."""
        assert (CI_ROOT / "__init__.py").exists(), "context_intelligence/__init__.py not found"

    def test_py_typed_exists(self):
        """context_intelligence/py.typed must exist (PEP 561 marker)."""
        assert (CI_ROOT / "py.typed").exists(), "context_intelligence/py.typed not found"

    def test_reconstruct_init_exists(self):
        """context_intelligence/reconstruct/__init__.py must exist."""
        assert (CI_ROOT / "reconstruct" / "__init__.py").exists(), (
            "context_intelligence/reconstruct/__init__.py not found"
        )

    def test_upload_init_exists(self):
        """context_intelligence/upload/__init__.py must exist."""
        assert (CI_ROOT / "upload" / "__init__.py").exists(), (
            "context_intelligence/upload/__init__.py not found"
        )


class TestPyTypedIsEmpty:
    """py.typed is a PEP 561 marker that must be empty."""

    def test_py_typed_is_empty(self):
        """context_intelligence/py.typed must be an empty file."""
        py_typed = CI_ROOT / "py.typed"
        assert py_typed.exists(), "py.typed not found"
        assert py_typed.stat().st_size == 0, "py.typed must be empty (PEP 561 marker)"


class TestImports:
    """All packages must be importable without errors."""

    def test_context_intelligence_imports(self):
        """context_intelligence must be importable."""
        import context_intelligence  # noqa: F401

    def test_reconstruct_imports(self):
        """context_intelligence.reconstruct must be importable."""
        import context_intelligence.reconstruct  # noqa: F401

    def test_upload_imports(self):
        """context_intelligence.upload must be importable."""
        import context_intelligence.upload  # noqa: F401

    def test_all_three_in_one_import(self):
        """The acceptance criteria command must succeed: all 3 packages importable."""
        import context_intelligence
        import context_intelligence.reconstruct
        import context_intelligence.upload

        # All imports succeeded if we get here
        assert context_intelligence is not None
        assert context_intelligence.reconstruct is not None
        assert context_intelligence.upload is not None


class TestDocstrings:
    """Package docstrings must be meaningful."""

    def test_context_intelligence_has_docstring(self):
        """context_intelligence must have a module docstring."""
        import context_intelligence

        assert context_intelligence.__doc__ is not None, (
            "context_intelligence must have a docstring"
        )
        assert len(context_intelligence.__doc__.strip()) > 0, "docstring must not be empty"

    def test_context_intelligence_docstring_mentions_levels(self):
        """context_intelligence docstring must describe the 3 architectural levels."""
        import context_intelligence

        doc = context_intelligence.__doc__ or ""
        # The spec requires the docstring to describe 3 levels:
        # Pure Transforms, Network I/O, Filesystem+Orchestration
        assert "Pure Transforms" in doc or "pure transforms" in doc.lower(), (
            "Docstring must mention 'Pure Transforms' level"
        )
        assert "Network I/O" in doc or "network i/o" in doc.lower(), (
            "Docstring must mention 'Network I/O' level"
        )
        assert "Filesystem" in doc or "filesystem" in doc.lower(), (
            "Docstring must mention 'Filesystem' level"
        )

    def test_reconstruct_has_docstring(self):
        """context_intelligence.reconstruct must have a module docstring."""
        import context_intelligence.reconstruct

        assert context_intelligence.reconstruct.__doc__ is not None, (
            "context_intelligence.reconstruct must have a docstring"
        )
        assert len(context_intelligence.reconstruct.__doc__.strip()) > 0

    def test_upload_has_docstring(self):
        """context_intelligence.upload must have a module docstring."""
        import context_intelligence.upload

        assert context_intelligence.upload.__doc__ is not None, (
            "context_intelligence.upload must have a docstring"
        )
        assert len(context_intelligence.upload.__doc__.strip()) > 0

    def test_upload_docstring_mentions_module_location(self):
        """context_intelligence.upload docstring must mention modules/tool-context-intelligence-upload/."""
        import context_intelligence.upload

        doc = context_intelligence.upload.__doc__ or ""
        assert "tool-context-intelligence-upload" in doc, (
            "upload docstring must note upload code location in "
            "modules/tool-context-intelligence-upload/"
        )
