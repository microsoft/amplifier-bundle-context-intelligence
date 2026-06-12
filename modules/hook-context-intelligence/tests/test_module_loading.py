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

    def test_on_session_ready_exists_and_is_valid(self):
        """Module must expose on_session_ready as an async function with coordinator param."""
        import inspect
        import amplifier_module_hook_context_intelligence as mod

        fn = getattr(mod, "on_session_ready", None)
        assert fn is not None, "Module must define on_session_ready"
        assert inspect.iscoroutinefunction(fn), "on_session_ready must be async"

        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        required = [
            p
            for p in params
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            )
            and p.default is inspect.Parameter.empty
        ]
        assert len(required) >= 1, (
            "on_session_ready must have at least one required positional param (coordinator)"
        )
        assert params[0].name == "coordinator"


class TestBundleYamlEntryPointConsistency:
    def _load_behavior_yaml(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_behavior_yaml_module_matches_entry_point(self):
        data = self._load_behavior_yaml()
        # Located by module name, not position: the behavior also wires hooks-mode.
        hook_modules = [h["module"] for h in data.get("hooks", [])]
        assert "hook-context-intelligence" in hook_modules

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

    def test_runtime_dependencies_httpx_present(self):
        """httpx must be a runtime dependency (used by LoggingHandler + BlobTool)."""
        data = self._load_pyproject()
        deps = data["project"].get("dependencies", [])
        assert any(d.startswith("httpx") for d in deps), (
            f"Expected httpx in runtime dependencies, got: {deps}"
        )

    def test_runtime_dependencies_no_neo4j(self):
        """neo4j must NOT be a runtime dependency (graph creation is now server-side)."""
        data = self._load_pyproject()
        deps = data["project"].get("dependencies", [])
        assert not any(d.startswith("neo4j") for d in deps), (
            f"neo4j must not be in runtime dependencies (graph is server-side), got: {deps}"
        )

    def test_hatchling_build_backend(self):
        data = self._load_pyproject()
        assert data["build-system"]["build-backend"] == "hatchling.build"

    def test_uv_package_true(self):
        data = self._load_pyproject()
        assert data["tool"]["uv"]["package"] is True


class TestBehaviorYamlConfigShape:
    """Validate the behavior YAML has the expected thin-forwarder config shape."""

    def _load_behavior_yaml(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def _ci_hook(self, data: dict) -> dict:
        """Return the hook-context-intelligence spec, located by module name.

        The behavior may wire multiple hooks (e.g. hooks-mode for mode discovery),
        so this must not assume a fixed position in the hooks list.
        """
        matches = [
            h for h in data.get("hooks", []) if h.get("module") == "hook-context-intelligence"
        ]
        assert matches, "behavior must wire the hook-context-intelligence hook"
        return matches[0]

    def test_yaml_parses_correctly(self):
        """YAML must parse without errors via yaml.safe_load."""
        data = self._load_behavior_yaml()
        assert isinstance(data, dict)

    def test_hook_module_name_preserved(self):
        """behavior must wire 'hook-context-intelligence' (located by module name)."""
        data = self._load_behavior_yaml()
        assert self._ci_hook(data)["module"] == "hook-context-intelligence"

    def test_config_has_thin_forwarder_keys(self):
        """Config must have: context_intelligence_server_url, workspace, log_level."""
        data = self._load_behavior_yaml()
        config = self._ci_hook(data)["config"]
        assert "context_intelligence_server_url" in config
        assert "workspace" in config
        assert "log_level" in config

    def test_no_graph_store_in_config(self):
        """graph_store (singular or plural) must not be in config."""
        data = self._load_behavior_yaml()
        config = self._ci_hook(data)["config"]
        assert "graph_store" not in config
        assert "graph_stores" not in config

    def test_no_enable_graph_in_config(self):
        """enable_graph must not be in config (graph is server-side now)."""
        data = self._load_behavior_yaml()
        config = self._ci_hook(data)["config"]
        assert "enable_graph" not in config
