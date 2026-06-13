"""Validation tests for the context-intelligence Amplifier bundle structure."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
MODULE_ROOT = Path(__file__).parent.parent


def _load_behavior() -> dict:
    """Load and parse the FULL umbrella behavior YAML file (composes design + logging)."""
    path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
    return yaml.safe_load(path.read_text())


def _load_logging_behavior() -> dict:
    """Load and parse the LOGGING behavior YAML file (hook-only)."""
    path = REPO_ROOT / "behaviors" / "context-intelligence-logging.yaml"
    return yaml.safe_load(path.read_text())


def _load_named_behavior(name: str) -> dict:
    """Load and parse a behavior YAML file by behavior name (no .yaml suffix)."""
    path = REPO_ROOT / "behaviors" / f"{name}.yaml"
    return yaml.safe_load(path.read_text())


def _bundle_refs(data: dict) -> list[str]:
    """Return the list of `includes[].bundle` reference strings for a behavior."""
    return [i["bundle"] for i in data.get("includes", []) if "bundle" in i]


class TestBundleRoot:
    """Validate bundle.md exists and has correct frontmatter."""

    def test_bundle_md_exists(self):
        assert (REPO_ROOT / "bundle.md").is_file()

    def test_bundle_md_has_frontmatter(self):
        content = (REPO_ROOT / "bundle.md").read_text()
        assert content.startswith("---")
        parts = content.split("---", 2)
        assert len(parts) >= 3, "bundle.md must have YAML frontmatter between --- delimiters"
        fm = yaml.safe_load(parts[1])
        assert fm["bundle"]["name"] == "context-intelligence"
        assert "version" in fm["bundle"]
        assert "description" in fm["bundle"]

    def test_bundle_md_includes_foundation(self):
        content = (REPO_ROOT / "bundle.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        includes = fm.get("includes", [])
        bundle_refs = [i["bundle"] for i in includes if "bundle" in i]
        assert any("amplifier-foundation" in ref for ref in bundle_refs)

    def test_bundle_md_includes_behavior(self):
        content = (REPO_ROOT / "bundle.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        includes = fm.get("includes", [])
        bundle_refs = [i["bundle"] for i in includes if "bundle" in i]
        assert any(
            "context-intelligence:behaviors/context-intelligence" in ref for ref in bundle_refs
        )

    def test_root_pyproject_toml_is_for_library(self):
        """Bundle root pyproject.toml belongs to the context_intelligence library, not a bare bundle."""
        pyproject = REPO_ROOT / "pyproject.toml"
        assert pyproject.exists(), "Root pyproject.toml must exist for context_intelligence library"
        import tomllib

        data = tomllib.loads(pyproject.read_text())
        assert data["project"]["name"] == "amplifier-bundle-context-intelligence"
        packages = (
            data.get("tool", {})
            .get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
            .get("packages", [])
        )
        assert "context_intelligence" in packages


class TestFullBehaviorYaml:
    """Validate the FULL umbrella behavior composes design + logging (no inline hook)."""

    def test_behavior_yaml_exists(self):
        assert (REPO_ROOT / "behaviors" / "context-intelligence.yaml").is_file()

    def test_full_behavior_composes_design_and_logging(self):
        """Full umbrella must include BOTH the design (top analysis layer) and logging behaviors."""
        bundle_refs = _bundle_refs(_load_behavior())
        assert any("context-intelligence-design" in ref for ref in bundle_refs), (
            f"Full behavior must include the design behavior, got: {bundle_refs!r}"
        )
        assert any("context-intelligence-logging" in ref for ref in bundle_refs), (
            f"Full behavior must include logging behavior, got: {bundle_refs!r}"
        )

    def test_full_behavior_has_no_inline_hook(self):
        """The hook now lives in the logging behavior, not inline in the full behavior.

        This keeps the hook registered exactly once across the include graph.
        """
        data = _load_behavior()
        hook_modules = [h["module"] for h in data.get("hooks", [])]
        assert "hook-context-intelligence" not in hook_modules, (
            "Full behavior must NOT inline the hook; it is composed via the logging behavior"
        )


class TestLoggingBehaviorYaml:
    """Validate the LOGGING (hook-only) behavior YAML structure."""

    def test_logging_behavior_exists(self):
        assert (REPO_ROOT / "behaviors" / "context-intelligence-logging.yaml").is_file()

    def test_behavior_has_hooks_section(self):
        data = _load_logging_behavior()
        assert "hooks" in data, "Logging behavior YAML must have a hooks: section"

    def test_logging_behavior_has_no_agents_or_tools(self):
        """Logging behavior is hook-ONLY — no analysis surface."""
        data = _load_logging_behavior()
        assert "agents" not in data, "Logging behavior must not declare agents"
        assert "tools" not in data, "Logging behavior must not declare tools"

    def test_behavior_hook_module_name(self):
        data = _load_logging_behavior()
        hook_specs = data.get("hooks", [])
        assert len(hook_specs) >= 1
        assert hook_specs[0]["module"] == "hook-context-intelligence"

    def test_behavior_hook_has_source(self):
        data = _load_logging_behavior()
        hook_spec = data["hooks"][0]
        assert "source" in hook_spec, "Hook spec must have a source field"

    def test_behavior_hook_has_config(self):
        data = _load_logging_behavior()
        hook_spec = data["hooks"][0]
        assert "config" in hook_spec, "Hook spec must have a config field"
        config = hook_spec["config"]
        # Thin forwarder config keys
        assert "context_intelligence_server_url" in config
        assert "log_level" in config

    def test_behavior_hook_is_in_hooks_section_not_tools(self):
        data = _load_logging_behavior()
        hook_modules = [h["module"] for h in data.get("hooks", [])]
        assert "hook-context-intelligence" in hook_modules
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "hook-context-intelligence" not in tool_modules

    def test_behavior_source_points_to_main(self):
        """Source must point to the main branch (post-merge)."""
        data = _load_logging_behavior()
        source = data["hooks"][0].get("source", "")
        # Source may have a #subdirectory= fragment after @main
        assert "@main" in source, f"Source must reference @main branch after merge, got: {source!r}"

    def test_no_graph_store_in_config(self):
        """Thin forwarder has no graph_store config (moved to server)."""
        data = _load_logging_behavior()
        config = data["hooks"][0].get("config", {})
        assert "graph_store" not in config, "graph_store must be removed from thin-forwarder config"
        assert "enable_graph" not in config, (
            "enable_graph must be removed from thin-forwarder config"
        )
