"""Test that the hook module pyproject.toml does NOT depend on its parent bundle.

Design: The hook module should be installable independently of its parent bundle
so it can be used in environments that compose modules individually.

TDD: These tests are written to enforce decoupling from the parent bundle.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

MODULE_ROOT = Path(__file__).parent.parent
PYPROJECT = MODULE_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


class TestHookDependencies:
    """Verify hook module's pyproject.toml does NOT depend on its parent bundle.
    
    Per the design proposal: the hook module should be installable independently
    of its parent bundle so it can be used in environments that compose modules
    individually (e.g. amplifier-agent's --no-sources install policy).
    """

    def test_amplifier_bundle_context_intelligence_not_in_dependencies(self) -> None:
        """amplifier-bundle-context-intelligence must NOT appear in dependencies."""
        data = _load_pyproject()
        deps: list[str] = data["project"]["dependencies"]
        dep_names = [d.split(">=")[0].split("==")[0].strip() for d in deps]
        assert "amplifier-bundle-context-intelligence" not in dep_names, (
            "The hook module must not declare its parent bundle as a runtime "
            "dependency. Doing so makes the module uninstallable in environments "
            "that strip [tool.uv.sources] at install time."
        )

    def test_uv_sources_does_not_reference_bundle(self) -> None:
        """[tool.uv.sources] must NOT have a path entry for the parent bundle."""
        data = _load_pyproject()
        sources: dict = data.get("tool", {}).get("uv", {}).get("sources", {})
        assert "amplifier-bundle-context-intelligence" not in sources, (
            "The hook module must not pin its parent bundle via [tool.uv.sources]. "
            "This entry is invisible under --no-sources install and produces an "
            "unresolvable dependency."
        )

    def test_dependencies_list_has_httpx_only(self) -> None:
        """Production dependencies must be httpx (and nothing else from this project)."""
        data = _load_pyproject()
        deps: list[str] = data["project"]["dependencies"]
        assert any("httpx" in d for d in deps), "httpx is required for HTTP dispatch"
        assert not any("amplifier-bundle-context-intelligence" in d for d in deps), (
            "Parent bundle must not be a runtime dependency of the hook."
        )
        assert not any("amplifier-core" in d for d in deps), (
            f"amplifier-core must not be a production dep (runtime-provided): {deps}"
        )
