"""Test that tool-context-intelligence-query pyproject.toml declares the bundle dependency
in a form that installs standalone (outside the monorepo).

The tools import from the `context_intelligence` package shipped by the parent bundle.
For the module to install standalone under the Amplifier agent's `uv pip install
--no-sources` policy, the bundle MUST be referenced as a PEP 508 direct git reference
inside [project.dependencies] (which survives --no-sources), NOT via a
[tool.uv.sources] `path = "../.."` entry (which --no-sources strips).

Merged from tool-graph-query and tool-blob-read versions (both were identical except
for module name) — now a single guard for the merged module.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

MODULE_ROOT = Path(__file__).parent.parent
PYPROJECT = MODULE_ROOT / "pyproject.toml"

BUNDLE = "amplifier-bundle-context-intelligence"


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def _dep_name(dep: str) -> str:
    """Extract the bare package name from a requirement string.

    Handles version specifiers (>=, ==) and PEP 508 direct references
    (`name @ git+https://...`).
    """
    return dep.split("@")[0].split(">=")[0].split("==")[0].strip()


class TestToolDependencies:
    """Verify the module declares the bundle as a standalone-installable dependency."""

    def test_bundle_declared_as_direct_git_reference(self) -> None:
        """The bundle must be a PEP 508 direct git reference in [project.dependencies].

        A direct `name @ git+https://...` reference survives `--no-sources`,
        unlike a bare name (only resolvable from PyPI) or a [tool.uv.sources] entry.
        """
        data = _load_pyproject()
        deps: list[str] = data["project"]["dependencies"]
        bundle_deps = [d for d in deps if _dep_name(d) == BUNDLE]
        assert bundle_deps, f"Expected '{BUNDLE}' in dependencies, got: {deps}"
        assert "git+https://" in bundle_deps[0], (
            f"Bundle dependency must be a direct git+https reference so it survives "
            f"`uv pip install --no-sources`, got: {bundle_deps[0]!r}"
        )

    def test_bundle_is_not_a_uv_path_source(self) -> None:
        """The bundle must NOT be a [tool.uv.sources] path entry.

        The `path = '../..'` assumption is exactly what breaks standalone install:
        --no-sources strips [tool.uv.sources], leaving an unresolvable reference.
        """
        data = _load_pyproject()
        sources: dict = data.get("tool", {}).get("uv", {}).get("sources", {})
        assert BUNDLE not in sources, (
            f"'{BUNDLE}' must not be a [tool.uv.sources] entry (breaks standalone "
            f"install under --no-sources); declare it as a direct git reference in "
            f"[project.dependencies] instead. Got sources: {sources}"
        )

    def test_dependencies_list_has_httpx_and_bundle(self) -> None:
        """Production deps must include httpx and the bundle.

        amplifier-core is NOT a production dep — it is runtime-provided by the
        Amplifier CLI.
        """
        data = _load_pyproject()
        deps: list[str] = data["project"]["dependencies"]
        assert any("httpx" in d for d in deps), f"httpx not found in {deps}"
        assert any(_dep_name(d) == BUNDLE for d in deps), f"{BUNDLE} not found in {deps}"
        assert not any(_dep_name(d) == "amplifier-core" for d in deps), (
            f"amplifier-core must not be a production dep (runtime-provided): {deps}"
        )

    def test_allow_direct_references_enabled(self) -> None:
        """Building a wheel that carries a direct reference requires this hatch flag."""
        data = _load_pyproject()
        allow = (
            data.get("tool", {}).get("hatch", {}).get("metadata", {}).get("allow-direct-references")
        )
        assert allow is True, (
            "tool.hatch.metadata.allow-direct-references must be true to build a wheel "
            f"carrying the direct git reference, got: {allow!r}"
        )

    def test_entry_point_module_id_is_correct(self) -> None:
        """Entry point key must match the module ID used in detective.md."""
        data = _load_pyproject()
        eps = data.get("project", {}).get("entry-points", {}).get("amplifier.modules", {})
        assert "tool-context-intelligence-query" in eps, (
            f"Entry point 'tool-context-intelligence-query' not found in "
            f"[project.entry-points.'amplifier.modules']: {eps}"
        )
        assert (
            "amplifier_module_tool_context_intelligence_query"
            in eps["tool-context-intelligence-query"]
        )

    def test_asyncio_mode_is_auto(self) -> None:
        """pytest-asyncio must be in auto mode for the test suite to run correctly."""
        data = _load_pyproject()
        asyncio_mode = (
            data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("asyncio_mode")
        )
        assert asyncio_mode == "auto", f"asyncio_mode must be 'auto', got: {asyncio_mode!r}"
