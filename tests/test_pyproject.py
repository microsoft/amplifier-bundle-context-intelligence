"""Tests validating the root pyproject.toml for amplifier-bundle-context-intelligence.

Verifies that pyproject.toml:
- Exists at the bundle root
- Has the correct package name and version
- Specifies the correct wheel packages (context_intelligence)
- Has the required build system (hatchling)
- Has the correct dev dependencies
- Has pyright and ruff configurations
"""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def test_pyproject_exists():
    """pyproject.toml must exist at repo root."""
    assert PYPROJECT_PATH.exists(), f"pyproject.toml not found at {PYPROJECT_PATH}"


def test_package_name():
    """Package name must be amplifier-bundle-context-intelligence."""
    d = _load_pyproject()
    assert d["project"]["name"] == "amplifier-bundle-context-intelligence"


def test_version():
    """Version must be 0.1.0."""
    d = _load_pyproject()
    assert d["project"]["version"] == "0.1.0"


def test_requires_python():
    """requires-python must be >=3.11."""
    d = _load_pyproject()
    assert d["project"]["requires-python"] == ">=3.11"


def test_no_runtime_dependencies():
    """There must be no runtime dependencies."""
    d = _load_pyproject()
    deps = d["project"].get("dependencies", [])
    assert deps == [], f"Expected no runtime dependencies, got: {deps}"


def test_build_system_hatchling():
    """Build system backend must be hatchling.build."""
    d = _load_pyproject()
    assert d["build-system"]["build-backend"] == "hatchling.build"
    assert "hatchling" in d["build-system"]["requires"]


def test_wheel_packages():
    """Wheel packages must include context_intelligence."""
    d = _load_pyproject()
    packages = d["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["context_intelligence"], f"Got: {packages}"


def test_tool_uv_package():
    """tool.uv.package must be true."""
    d = _load_pyproject()
    assert d["tool"]["uv"]["package"] is True


def test_hatch_metadata_allow_direct_references():
    """tool.hatch.metadata.allow-direct-references must be true."""
    d = _load_pyproject()
    assert d["tool"]["hatch"]["metadata"]["allow-direct-references"] is True


def test_dev_dependencies():
    """Dev dependency-groups must include pytest, pyright, ruff."""
    d = _load_pyproject()
    dev_deps = d["dependency-groups"]["dev"]
    dep_names = [dep.split(">=")[0].split("==")[0].strip() for dep in dev_deps]
    assert "pytest" in dep_names
    assert "pyright" in dep_names
    assert "ruff" in dep_names


def test_pyright_config():
    """Pyright must target python 3.11, basic type checking, include correct paths."""
    d = _load_pyproject()
    pyright = d["tool"]["pyright"]
    assert pyright["pythonVersion"] == "3.11"
    assert pyright["typeCheckingMode"] == "basic"
    assert "context_intelligence" in pyright["include"]
    assert "tests" in pyright["include"]
    assert "." in pyright["extraPaths"]


def test_ruff_config():
    """Ruff must target py311 with line-length 100."""
    d = _load_pyproject()
    ruff = d["tool"]["ruff"]
    assert ruff["target-version"] == "py311"
    assert ruff["line-length"] == 100
