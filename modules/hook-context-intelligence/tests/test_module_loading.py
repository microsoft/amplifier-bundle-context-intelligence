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
        assert any(d.startswith("neo4j") for d in deps), (
            f"Expected neo4j in runtime dependencies, got: {deps}"
        )
        assert not any(d.startswith("duckdb") for d in deps), (
            f"duckdb must not be in runtime dependencies, got: {deps}"
        )

    def test_hatchling_build_backend(self):
        data = self._load_pyproject()
        assert data["build-system"]["build-backend"] == "hatchling.build"

    def test_uv_package_true(self):
        data = self._load_pyproject()
        assert data["tool"]["uv"]["package"] is True


class TestBehaviorYamlConfigShape:
    """Validate the behavior YAML has the expected config shape."""

    def _load_behavior_yaml(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_yaml_parses_correctly(self):
        """YAML must parse without errors via yaml.safe_load."""
        data = self._load_behavior_yaml()
        assert isinstance(data, dict)

    def test_hook_module_name_preserved(self):
        """hooks[0]['module'] must be 'hook-context-intelligence' for backward compat."""
        data = self._load_behavior_yaml()
        assert data["hooks"][0]["module"] == "hook-context-intelligence"

    def test_config_has_required_keys(self):
        """Config must have: exclude_events, log_level, graph_store, enable_graph."""
        data = self._load_behavior_yaml()
        config = data["hooks"][0]["config"]
        expected_keys = {"exclude_events", "log_level", "graph_store", "enable_graph"}
        assert expected_keys == set(config.keys())

    def test_graph_store_is_dict(self):
        """graph_store must be a dict (singular store config)."""
        data = self._load_behavior_yaml()
        config = data["hooks"][0]["config"]
        assert isinstance(config["graph_store"], dict)

    def test_enable_graph_in_behavior_yaml_uses_env_interpolation(self):
        """enable_graph must be present and use env-var interpolation syntax.

        The value '${CI_ENABLE_GRAPH:false}' allows the config loader to
        resolve the key from the environment at startup, defaulting to false.
        config_resolver handles the resulting string value correctly.
        """
        data = self._load_behavior_yaml()
        config = data["hooks"][0]["config"]
        assert "enable_graph" in config
        assert config["enable_graph"] == "${CI_ENABLE_GRAPH:false}"

    def test_graph_store_entry_has_type_and_config(self):
        """graph_store entry must have type and config keys."""
        data = self._load_behavior_yaml()
        store = data["hooks"][0]["config"]["graph_store"]
        assert "type" in store
        assert "config" in store

    def test_old_graph_stores_plural_removed(self):
        """The old 'graph_stores' (plural) key must not be in config."""
        data = self._load_behavior_yaml()
        config = data["hooks"][0]["config"]
        assert "graph_stores" not in config
