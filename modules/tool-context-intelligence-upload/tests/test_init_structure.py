"""Tests verifying __init__.py is a bare package marker.

After the Amplifier-wrapper removal, __init__.py must contain exactly one line:
a module-level docstring. No imports, no classes, no mount(), no __amplifier_module_type__.
"""

from __future__ import annotations

from pathlib import Path

INIT_PY = (
    Path(__file__).parent.parent
    / "amplifier_module_tool_context_intelligence_upload"
    / "__init__.py"
)

EXPECTED_CONTENT = (
    '"""context-intelligence-upload'
    " \u2014 replay context-intelligence session events to a server.\"\"\"\n"
)


class TestInitPyStructure:
    """Verify __init__.py is a bare package marker — exactly 1 line."""

    def test_init_py_has_exactly_one_line(self):
        """__init__.py must contain exactly one non-empty line."""
        lines = INIT_PY.read_text(encoding="utf-8").splitlines()
        non_empty = [ln for ln in lines if ln.strip()]
        assert len(non_empty) == 1, (
            f"Expected 1 non-empty line, found {len(non_empty)}: {non_empty}"
        )

    def test_init_py_content_is_correct_docstring(self):
        """__init__.py must be exactly the specified docstring (plus trailing newline)."""
        content = INIT_PY.read_text(encoding="utf-8")
        assert content == EXPECTED_CONTENT, (
            f"Content mismatch.\nExpected: {EXPECTED_CONTENT!r}\nGot:      {content!r}"
        )

    def test_init_py_has_no_import_statements(self):
        """__init__.py must not contain any import statements."""
        content = INIT_PY.read_text(encoding="utf-8")
        assert "import " not in content, "Found import statement in __init__.py"

    def test_init_py_has_no_mount_function(self):
        """__init__.py must not define mount()."""
        content = INIT_PY.read_text(encoding="utf-8")
        assert "def mount" not in content, "Found mount() definition in __init__.py"

    def test_init_py_has_no_amplifier_module_type(self):
        """__init__.py must not set __amplifier_module_type__."""
        content = INIT_PY.read_text(encoding="utf-8")
        assert "__amplifier_module_type__" not in content, (
            "Found __amplifier_module_type__ in __init__.py"
        )

    def test_module_imports_cleanly(self):
        """The package must be importable without errors."""
        import amplifier_module_tool_context_intelligence_upload  # noqa: F401
