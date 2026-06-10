"""Validation tests for the tool-context-intelligence-upload pyproject.toml."""

import tomllib
from pathlib import Path

MODULE_ROOT = Path(__file__).parent.parent
PYPROJECT_PATH = MODULE_ROOT / "pyproject.toml"


def _load() -> dict:
    with open(PYPROJECT_PATH, "rb") as f:
        return tomllib.load(f)


class TestProjectSection:
    """Validate [project] section fields match the spec."""

    def test_pyproject_exists(self):
        assert PYPROJECT_PATH.exists(), "pyproject.toml must exist at module root"

    def test_project_name(self):
        data = _load()
        assert data["project"]["name"] == "amplifier-module-tool-context-intelligence-upload"

    def test_project_version(self):
        data = _load()
        assert data["project"]["version"] == "0.1.1"

    def test_requires_python(self):
        data = _load()
        assert data["project"]["requires-python"] == ">=3.11"

    def test_license(self):
        data = _load()
        assert data["project"]["license"] == "MIT"

    def test_dependencies_include_httpx(self):
        data = _load()
        deps = data["project"]["dependencies"]
        assert any("httpx>=0.28.1" in d for d in deps)

    def test_dependencies_include_hook_module(self):
        data = _load()
        deps = data["project"]["dependencies"]
        assert any("amplifier-module-hook-context-intelligence" in d for d in deps)


class TestNoAmplifierEntryPoints:
    """Validate that amplifier.modules entry points have been removed."""

    def test_no_amplifier_modules_entry_point(self):
        """[project.entry-points.'amplifier.modules'] must NOT exist after wrapper removal."""
        data = _load()
        entry_points = data.get("project", {}).get("entry-points", {})
        assert "amplifier.modules" not in entry_points, (
            "amplifier.modules entry-point must be removed — CLI is the single code path"
        )


class TestScripts:
    """Validate scripts section."""

    def test_cli_script(self):
        data = _load()
        scripts = data["project"]["scripts"]
        assert scripts["context-intelligence-upload"] == (
            "amplifier_module_tool_context_intelligence_upload.cli:main"
        )


class TestBuildSystem:
    """Validate build system configuration."""

    def test_build_backend_is_hatchling(self):
        data = _load()
        assert data["build-system"]["build-backend"] == "hatchling.build"

    def test_build_requires_hatchling(self):
        data = _load()
        assert "hatchling" in data["build-system"]["requires"]


class TestUVConfig:
    """Validate [tool.uv] configuration."""

    def test_uv_package_true(self):
        data = _load()
        assert data["tool"]["uv"]["package"] is True

    def test_wheel_packages(self):
        data = _load()
        packages = data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
        assert "amplifier_module_tool_context_intelligence_upload" in packages


class TestDependencyGroups:
    """Validate [dependency-groups] dev dependencies."""

    def test_dev_does_not_include_amplifier_core(self):
        """amplifier-core must NOT be in dev deps after wrapper removal."""
        data = _load()
        dev = data["dependency-groups"]["dev"]
        assert not any("amplifier-core" in d for d in dev), (
            "amplifier-core must be removed from dev deps — no longer needed"
        )

    def test_dev_includes_pytest(self):
        data = _load()
        dev = data["dependency-groups"]["dev"]
        assert any("pytest>=" in d for d in dev)

    def test_dev_includes_pytest_asyncio(self):
        data = _load()
        dev = data["dependency-groups"]["dev"]
        assert any("pytest-asyncio>=" in d for d in dev)

    def test_dev_includes_pyright(self):
        data = _load()
        dev = data["dependency-groups"]["dev"]
        assert any("pyright>=" in d for d in dev)

    def test_dev_includes_ruff(self):
        data = _load()
        dev = data["dependency-groups"]["dev"]
        assert any("ruff>=" in d for d in dev)


class TestUVSources:
    """Validate [tool.uv.sources] configuration."""

    def test_no_amplifier_core_in_uv_sources(self):
        """amplifier-core must NOT be in [tool.uv.sources] after wrapper removal."""
        data = _load()
        sources = data.get("tool", {}).get("uv", {}).get("sources", {})
        assert "amplifier-core" not in sources, (
            "amplifier-core must be removed from uv sources — no longer a dependency"
        )

    def test_hook_module_source_is_editable_local(self):
        data = _load()
        source = data["tool"]["uv"]["sources"]["amplifier-module-hook-context-intelligence"]
        assert source.get("editable") is True
        assert "hook-context-intelligence" in source.get("path", "")


class TestPytestConfig:
    """Validate [tool.pytest.ini_options] configuration."""

    def test_asyncio_mode_auto(self):
        data = _load()
        opts = data["tool"]["pytest"]["ini_options"]
        assert opts["asyncio_mode"] == "auto"

    def test_asyncio_default_fixture_loop_scope(self):
        data = _load()
        opts = data["tool"]["pytest"]["ini_options"]
        assert opts["asyncio_default_fixture_loop_scope"] == "function"


class TestPyrightConfig:
    """Validate [tool.pyright] configuration."""

    def test_python_version(self):
        data = _load()
        assert data["tool"]["pyright"]["pythonVersion"] == "3.11"

    def test_type_checking_mode(self):
        data = _load()
        assert data["tool"]["pyright"]["typeCheckingMode"] == "basic"


class TestRuffConfig:
    """Validate [tool.ruff] configuration."""

    def test_target_version(self):
        data = _load()
        assert data["tool"]["ruff"]["target-version"] == "py311"

    def test_line_length(self):
        data = _load()
        assert data["tool"]["ruff"]["line-length"] == 100
