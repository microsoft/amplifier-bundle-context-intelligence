"""Tests for module loading, entry point resolution, and YAML consistency."""

import importlib.metadata
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
MODULE_ROOT = Path(__file__).parent.parent


class TestEntryPointDiscovery:
    def test_hook_entry_point_exists(self):
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        ep_names = [ep.name for ep in eps]
        assert "hook-context-intelligence" in ep_names

    def test_hook_entry_point_loads_mount_function(self):
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        hook_ep = next(ep for ep in eps if ep.name == "hook-context-intelligence")
        mount_fn = hook_ep.load()
        assert callable(mount_fn)

    def test_hook_entry_point_target_is_correct(self):
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        hook_ep = next(ep for ep in eps if ep.name == "hook-context-intelligence")
        assert hook_ep.value == "amplifier_module_hook_context_intelligence:mount"


class TestModuleTypeClassification:
    def test_module_type_is_hook(self):
        import amplifier_module_hook_context_intelligence

        assert amplifier_module_hook_context_intelligence.__amplifier_module_type__ == "hook"


class TestBundleYamlEntryPointConsistency:
    def _load_behavior_yaml(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_behavior_yaml_module_matches_entry_point(self):
        data = self._load_behavior_yaml()
        hook_specs = data.get("hooks", [])
        assert len(hook_specs) >= 1
        module_name = hook_specs[0]["module"]
        assert module_name == "hook-context-intelligence"

    def test_entry_point_resolution_would_succeed(self):
        module_id = "hook-context-intelligence"
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        mount_fn = None
        for ep in eps:
            if ep.name == module_id:
                mount_fn = ep.load()
                break
        assert mount_fn is not None


class TestPyprojectStructure:
    def _load_pyproject(self) -> dict:
        import tomllib

        path = MODULE_ROOT / "pyproject.toml"
        with open(path, "rb") as f:
            return tomllib.load(f)

    def test_has_amplifier_modules_entry_points(self):
        data = self._load_pyproject()
        eps = data["project"]["entry-points"]["amplifier.modules"]
        assert isinstance(eps, dict)
        assert len(eps) >= 1

    def test_hook_entry_point_format(self):
        data = self._load_pyproject()
        eps = data["project"]["entry-points"]["amplifier.modules"]
        hook_ep = eps["hook-context-intelligence"]
        assert ":" in hook_ep
        module_path, attr = hook_ep.split(":")
        assert attr == "mount"
        assert module_path == "amplifier_module_hook_context_intelligence"

    def test_runtime_dependencies(self):
        data = self._load_pyproject()
        deps = data["project"].get("dependencies", [])
        assert "duckdb>=1.0" in deps, f"Expected duckdb>=1.0 in runtime dependencies, got: {deps}"

    def test_hatchling_build_backend(self):
        data = self._load_pyproject()
        assert data["build-system"]["build-backend"] == "hatchling.build"

    def test_uv_package_true(self):
        data = self._load_pyproject()
        assert data["tool"]["uv"]["package"] is True
