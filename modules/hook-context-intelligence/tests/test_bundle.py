"""Validation tests for the context-intelligence Amplifier bundle structure."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
MODULE_ROOT = Path(__file__).parent.parent


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

    def _load_behavior(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_behavior_yaml_exists(self):
        assert (REPO_ROOT / "behaviors" / "context-intelligence.yaml").is_file()

    def test_behavior_has_hooks_section(self):
        data = self._load_behavior()
        assert "hooks" in data, "Behavior YAML must have a hooks: section"

    def test_behavior_hook_module_name(self):
        data = self._load_behavior()
        hook_specs = data.get("hooks", [])
        assert len(hook_specs) >= 1
        assert hook_specs[0]["module"] == "hook-context-intelligence"

    def test_behavior_hook_has_source(self):
        data = self._load_behavior()
        hook_spec = data["hooks"][0]
        assert "source" in hook_spec, "Hook spec must have a source field"

    def test_behavior_hook_has_config(self):
        data = self._load_behavior()
        hook_spec = data["hooks"][0]
        assert "config" in hook_spec, "Hook spec must have a config field"
        config = hook_spec["config"]
        assert "exclude_events" in config

    def test_behavior_hook_is_in_hooks_section_not_tools(self):
        data = self._load_behavior()
        hook_modules = [h["module"] for h in data.get("hooks", [])]
        assert "hook-context-intelligence" in hook_modules
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "hook-context-intelligence" not in tool_modules


class TestSkillRegistration:
    """Validate tool-skills module registration in the behavior YAML."""

    def _load_behavior(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_behavior_has_tools_section(self):
        data = self._load_behavior()
        assert "tools" in data, "Behavior YAML must have a tools: section"

    def test_tools_section_has_tool_skills_module(self):
        data = self._load_behavior()
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "tool-skills" in tool_modules, "tools section must include tool-skills module"

    def test_tool_skills_config_has_skills_list(self):
        data = self._load_behavior()
        tool_spec = next(t for t in data["tools"] if t["module"] == "tool-skills")
        skills = tool_spec.get("config", {}).get("skills", [])
        assert len(skills) >= 2, "tool-skills config.skills must have at least 2 entries"

    def test_skills_list_includes_curated_skills(self):
        data = self._load_behavior()
        tool_spec = next(t for t in data["tools"] if t["module"] == "tool-skills")
        skills = tool_spec["config"]["skills"]
        assert any("amplifier-bundle-skills" in s for s in skills), (
            "skills list must include curated skills from amplifier-bundle-skills"
        )

    def test_skills_list_includes_bundle_skills(self):
        data = self._load_behavior()
        tool_spec = next(t for t in data["tools"] if t["module"] == "tool-skills")
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
