"""Test that the hook module pyproject.toml declares the context_intelligence dependency.

TDD: This test is written FIRST and will FAIL until pyproject.toml is updated.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

MODULE_ROOT = Path(__file__).parent.parent
PYPROJECT = MODULE_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


class TestHookDependencies:
    """Verify hook module's pyproject.toml declares amplifier-bundle-context-intelligence."""

    def test_amplifier_bundle_context_intelligence_in_dependencies(self) -> None:
        """amplifier-bundle-context-intelligence must appear in the dependencies list."""
        data = _load_pyproject()
        deps: list[str] = data["project"]["dependencies"]
        dep_names = [d.split(">=")[0].split("==")[0].strip() for d in deps]
        assert "amplifier-bundle-context-intelligence" in dep_names, (
            f"Expected 'amplifier-bundle-context-intelligence' in dependencies, got: {deps}"
        )

    def test_uv_sources_has_path_entry_for_bundle(self) -> None:
        """[tool.uv.sources] must have path = '../..' for amplifier-bundle-context-intelligence."""
        data = _load_pyproject()
        sources: dict = data.get("tool", {}).get("uv", {}).get("sources", {})
        assert "amplifier-bundle-context-intelligence" in sources, (
            f"Expected 'amplifier-bundle-context-intelligence' in [tool.uv.sources], got: {sources}"
        )
        entry = sources["amplifier-bundle-context-intelligence"]
        assert entry.get("path") == "../..", f"Expected path = '../..', got: {entry}"

    def test_dependencies_list_has_httpx_and_bundle(self) -> None:
        """The production dependencies must include httpx and amplifier-bundle-context-intelligence.
        amplifier-core is NOT a production dep — it is runtime-provided by the Amplifier CLI.
        """
        data = _load_pyproject()
        deps: list[str] = data["project"]["dependencies"]
        assert any("httpx" in d for d in deps), f"httpx not found in {deps}"
        assert any("amplifier-bundle-context-intelligence" in d for d in deps), (
            f"amplifier-bundle-context-intelligence not found in {deps}"
        )
        assert not any("amplifier-core" in d for d in deps), (
            f"amplifier-core must not be a production dep (runtime-provided): {deps}"
        )
