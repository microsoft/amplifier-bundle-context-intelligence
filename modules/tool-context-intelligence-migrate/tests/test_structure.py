"""Tests T54–T56: module structure, __init__ exports, pyproject.toml checks."""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# T54: init_exports
# ---------------------------------------------------------------------------


def test_init_exports() -> None:
    """T54: __init__.py exports at least SchemaVersionError, transform_session, scan_projects."""
    import amplifier_module_tool_context_intelligence_migrate as m

    assert hasattr(m, "SchemaVersionError"), "__init__ must export SchemaVersionError"
    assert hasattr(m, "transform_session"), "__init__ must export transform_session"
    assert hasattr(m, "scan_projects"), "__init__ must export scan_projects"


# ---------------------------------------------------------------------------
# T55: main_module_runnable
# ---------------------------------------------------------------------------


def test_main_module_runnable() -> None:
    """T55: __main__.py exists and imports main from cli."""
    # Verify the module can be found (not actually run it to avoid sys.exit)
    import amplifier_module_tool_context_intelligence_migrate.__main__ as main_mod  # noqa: F401


# ---------------------------------------------------------------------------
# T56: pyproject_cli_entry_point
# ---------------------------------------------------------------------------


def test_pyproject_cli_entry_point() -> None:
    """T56: context-intelligence-migrate entry point is declared in pyproject.toml."""
    # Find the pyproject.toml relative to the module's installed path
    pkg_root = Path(__file__).parent.parent
    pyproject = pkg_root / "pyproject.toml"

    if not pyproject.exists():
        # May not be present in all test environments; skip gracefully
        import pytest

        pytest.skip("pyproject.toml not found at expected path")

    content = pyproject.read_text(encoding="utf-8")
    assert "context-intelligence-migrate" in content, (
        "pyproject.toml must declare 'context-intelligence-migrate' entry point"
    )
