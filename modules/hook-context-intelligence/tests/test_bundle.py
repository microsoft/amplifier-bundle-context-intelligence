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

    def test_no_root_pyproject_toml(self):
        """Bundles are configuration, not Python packages — no root pyproject.toml."""
        assert not (REPO_ROOT / "pyproject.toml").exists()


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
        assert "exclude_events" in config

    def test_behavior_hook_is_in_hooks_section_not_tools(self):
        data = _load_behavior()
        hook_modules = [h["module"] for h in data.get("hooks", [])]
        assert "hook-context-intelligence" in hook_modules
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "hook-context-intelligence" not in tool_modules


class TestSkillRegistration:
    """Validate tool-skills module registration in the behavior YAML."""

    def _find_tool_skills_spec(self) -> dict:
        """Find the tool-skills module spec, or fail with a clear message."""
        data = _load_behavior()
        spec = next((t for t in data.get("tools", []) if t["module"] == "tool-skills"), None)
        assert spec is not None, "tool-skills module not found in tools section"
        return spec

    def test_behavior_has_tools_section(self):
        data = _load_behavior()
        assert "tools" in data, "Behavior YAML must have a tools: section"

    def test_tools_section_has_tool_skills_module(self):
        data = _load_behavior()
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "tool-skills" in tool_modules, "tools section must include tool-skills module"

    def test_tool_skills_config_has_skills_list(self):
        tool_spec = self._find_tool_skills_spec()
        skills = tool_spec.get("config", {}).get("skills", [])
        assert len(skills) >= 2, "tool-skills config.skills must have at least 2 entries"

    def test_skills_list_includes_curated_skills(self):
        tool_spec = self._find_tool_skills_spec()
        skills = tool_spec["config"]["skills"]
        assert any("amplifier-bundle-skills" in s for s in skills), (
            "skills list must include curated skills from amplifier-bundle-skills"
        )

    def test_skills_list_includes_bundle_skills(self):
        tool_spec = self._find_tool_skills_spec()
        skills = tool_spec["config"]["skills"]
        assert any("context-intelligence" in s and "skills" in s for s in skills), (
            "skills list must include bundle skills from context-intelligence"
        )

    def test_skill_directory_exists(self):
        skill_dir = REPO_ROOT / "skills" / "context-intelligence-graph-search"
        assert skill_dir.is_dir(), "Skill directory must exist"

    def test_skill_md_file_exists(self):
        skill_md = REPO_ROOT / "skills" / "context-intelligence-graph-search" / "SKILL.md"
        assert skill_md.is_file(), "SKILL.md must exist in skill directory"

    def test_skill_md_has_valid_frontmatter(self):
        skill_md = REPO_ROOT / "skills" / "context-intelligence-graph-search" / "SKILL.md"
        content = skill_md.read_text()
        assert content.startswith("---"), "SKILL.md must start with frontmatter delimiter"
        parts = content.split("---", 2)
        assert len(parts) >= 3, "SKILL.md must have YAML frontmatter between --- delimiters"
        fm = yaml.safe_load(parts[1])
        assert fm["name"] == "context-intelligence-graph-search"
        assert "description" in fm and len(fm["description"]) > 0
        assert fm.get("license") == "MIT"


class TestBehaviorYamlForestConfig:
    """Validate forest-aware graph_stores config in behavior YAML (task-10)."""

    def test_log_level_is_plain_warning_no_env_var(self):
        """log_level must be plain 'WARNING', not an env-var interpolation string."""
        data = _load_behavior()
        config = data["hooks"][0]["config"]
        log_level = config["log_level"]
        assert log_level == "WARNING", f"Expected plain 'WARNING', got {log_level!r}"
        assert "${" not in str(log_level), "log_level must not contain env var interpolation"

    def test_no_env_var_interpolation_anywhere(self):
        """No env var interpolation patterns anywhere in the YAML."""
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        raw_text = path.read_text()
        assert "${" not in raw_text, "Behavior YAML must not contain env var interpolation (${...})"

    def test_graph_stores_section_exists(self):
        """Hook config must have a graph_stores (plural) list."""
        data = _load_behavior()
        config = data["hooks"][0]["config"]
        assert "graph_stores" in config, "Hook config must have a graph_stores section"
        assert isinstance(config["graph_stores"], list)

    def test_graph_forest_name_at_store_entry_level(self):
        """graph_forest_name must be at each graph_stores[] entry level, NOT inside backend config."""
        data = _load_behavior()
        store_entry = data["hooks"][0]["config"]["graph_stores"][0]
        assert "graph_forest_name" in store_entry, "graph_forest_name must be at store entry level"
        assert store_entry["graph_forest_name"] == "default"

    def test_graph_forest_name_not_inside_backend_config(self):
        """graph_forest_name must NOT be inside graph_stores[].config."""
        data = _load_behavior()
        store_entry = data["hooks"][0]["config"]["graph_stores"][0]
        backend_config = store_entry.get("config", {})
        assert "graph_forest_name" not in backend_config, (
            "graph_forest_name must be at store entry level, not inside config"
        )

    def test_graph_store_root_in_file_backend(self):
        """graph_stores[].config must use graph_store_root, not location."""
        data = _load_behavior()
        store_entry = data["hooks"][0]["config"]["graph_stores"][0]
        backend_config = store_entry.get("config", {})
        assert "graph_store_root" in backend_config, "store config must have graph_store_root"
        assert backend_config["graph_store_root"] == "~/.amplifier/graphs"
        assert "location" not in backend_config, (
            "store config must use graph_store_root, not location"
        )

    def test_graph_store_root_uses_graphs_plural(self):
        """Path must end with 'graphs' (plural), not 'graph'."""
        data = _load_behavior()
        root = data["hooks"][0]["config"]["graph_stores"][0]["config"]["graph_store_root"]
        assert root.endswith("/graphs"), (
            f"graph_store_root must end with '/graphs' (plural), got {root!r}"
        )

    def test_old_graph_store_singular_removed(self):
        """The old graph_store (singular) key must not exist in config."""
        data = _load_behavior()
        config = data["hooks"][0]["config"]
        assert "graph_store" not in config, (
            "Old graph_store (singular) key must be removed; use graph_stores (plural)"
        )
