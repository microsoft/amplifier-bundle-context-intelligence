"""Tests for entry point discovery and pyproject.toml validation.

Validates that the CLI entry point is properly configured and the
pyproject.toml structure matches the spec.
"""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

MODULE_ROOT = Path(__file__).parent.parent


def _load_pyproject() -> dict:
    """Load and return the parsed pyproject.toml as a dict."""
    pyproject_path = MODULE_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        return tomllib.load(f)


class TestEntryPointDiscovery:
    """Test that the amplifier.modules entry point is discoverable."""

    def test_entry_point_exists_in_amplifier_modules_group(self):
        """'tool-context-intelligence-upload' must appear in the amplifier.modules group."""
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        names = [ep.name for ep in eps]
        assert "tool-context-intelligence-upload" in names

    def test_entry_point_value_is_correct(self):
        """Entry point value must be 'amplifier_module_tool_context_intelligence_upload:mount'."""
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        ep = next(ep for ep in eps if ep.name == "tool-context-intelligence-upload")
        assert ep.value == "amplifier_module_tool_context_intelligence_upload:mount"


class TestCliEntryPoint:
    """Test that the CLI console_scripts entry point is properly configured."""

    def test_cli_entry_point_exists_in_console_scripts(self):
        """'context-intelligence-upload' must appear in console_scripts."""
        eps = importlib.metadata.entry_points(group="console_scripts")
        names = [ep.name for ep in eps]
        assert "context-intelligence-upload" in names

    def test_cli_entry_point_value_is_correct(self):
        """CLI entry point value must point to amplifier_module_tool_context_intelligence_upload.cli:main."""
        eps = importlib.metadata.entry_points(group="console_scripts")
        ep = next(ep for ep in eps if ep.name == "context-intelligence-upload")
        assert ep.value == "amplifier_module_tool_context_intelligence_upload.cli:main"


class TestPyprojectStructure:
    """Validate key fields in pyproject.toml against the spec."""

    def test_has_amplifier_modules_entry_points_with_tool(self):
        """[project.entry-points.'amplifier.modules'] must contain 'tool-context-intelligence-upload'."""
        data = _load_pyproject()
        entry_points = data["project"]["entry-points"]["amplifier.modules"]
        assert "tool-context-intelligence-upload" in entry_points

    def test_has_scripts_with_context_intelligence_upload(self):
        """[project.scripts] must contain 'context-intelligence-upload'."""
        data = _load_pyproject()
        scripts = data["project"]["scripts"]
        assert "context-intelligence-upload" in scripts

    def test_build_backend_is_hatchling(self):
        """Build backend must be 'hatchling.build'."""
        data = _load_pyproject()
        assert data["build-system"]["build-backend"] == "hatchling.build"

    def test_tool_uv_package_is_true(self):
        """[tool.uv] package must be True."""
        data = _load_pyproject()
        assert data["tool"]["uv"]["package"] is True

    def test_runtime_dependencies_include_hook_module(self):
        """Runtime dependencies must include 'amplifier-module-hook-context-intelligence'."""
        data = _load_pyproject()
        deps = data["project"]["dependencies"]
        assert any("amplifier-module-hook-context-intelligence" in d for d in deps)

    def test_runtime_dependencies_include_httpx(self):
        """Runtime dependencies must include 'httpx'."""
        data = _load_pyproject()
        deps = data["project"]["dependencies"]
        assert any("httpx" in d for d in deps)
