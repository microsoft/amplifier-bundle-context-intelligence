"""Validation tests for the context-intelligence Amplifier bundle structure."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
MODULE_ROOT = Path(__file__).parent.parent


def _load_behavior() -> dict:
    """Load and parse the behavior YAML file."""
    path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
    return yaml.safe_load(path.read_text())


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


class TestBehaviorYaml:
    """Validate behavior YAML structure."""

    def test_behavior_yaml_exists(self):
        assert (REPO_ROOT / "behaviors" / "context-intelligence.yaml").is_file()

    def test_behavior_has_hooks_section(self):
        data = _load_behavior()
        assert "hooks" in data, "Behavior YAML must have a hooks: section"

    def test_behavior_hook_module_name(self):
        data = _load_behavior()
        hook_specs = data.get("hooks", [])
        assert len(hook_specs) >= 1
        assert hook_specs[0]["module"] == "hook-context-intelligence"

    def test_behavior_hook_has_source(self):
        data = _load_behavior()
        hook_spec = data["hooks"][0]
        assert "source" in hook_spec, "Hook spec must have a source field"

    def test_behavior_hook_has_config(self):
        data = _load_behavior()
        hook_spec = data["hooks"][0]
        assert "config" in hook_spec, "Hook spec must have a config field"
        config = hook_spec["config"]
        # Thin forwarder config keys
        assert "context_intelligence_server" in config
        assert isinstance(config["context_intelligence_server"], dict)
        assert "log_level" in config

    def test_behavior_hook_is_in_hooks_section_not_tools(self):
        data = _load_behavior()
        hook_modules = [h["module"] for h in data.get("hooks", [])]
        assert "hook-context-intelligence" in hook_modules
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "hook-context-intelligence" not in tool_modules

    def test_behavior_source_points_to_main(self):
        """Source must point to the main branch (post-merge)."""
        data = _load_behavior()
        source = data["hooks"][0].get("source", "")
        # Source may have a #subdirectory= fragment after @main
        assert "@main" in source, f"Source must reference @main branch after merge, got: {source!r}"

    def test_no_graph_store_in_config(self):
        """Thin forwarder has no graph_store config (moved to server)."""
        data = _load_behavior()
        config = data["hooks"][0].get("config", {})
        assert "graph_store" not in config, "graph_store must be removed from thin-forwarder config"
        assert "enable_graph" not in config, (
            "enable_graph must be removed from thin-forwarder config"
        )
