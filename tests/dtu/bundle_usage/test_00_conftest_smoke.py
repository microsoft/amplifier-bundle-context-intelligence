"""
Smoke tests for conftest.py mount constants and JSONL-primary _BUNDLE_ANALYSIS_SCRIPT.

These are structural/static checks — they run without a live DTU.
Mount requirements verified here:
  ~/.amplifier/projects/ -> /mnt/amplifier-projects (read-only)
  ~/.amplifier/cache/    -> /mnt/amplifier-cache   (read-only)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Override autouse DTU bootstrap for smoke checks (no live DTU needed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def dtu_bootstrap():  # type: ignore[override]
    """No-op override: smoke tests don't need a live DTU."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONFTEST_PATH = Path(__file__).resolve().parent / "conftest.py"


def import_conftest():
    """Import the bundle_usage conftest as a module (avoids pytest magic)."""
    spec = importlib.util.spec_from_file_location("_bundle_usage_conftest_smoke", CONFTEST_PATH)
    assert spec is not None, f"Could not create module spec from {CONFTEST_PATH}"
    assert spec.loader is not None, "Module spec has no loader"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


def test_conftest_exposes_mount_constants():
    """conftest must export HOST_PROJECTS_PATH, HOST_CACHE_PATH, DTU_PROJECTS_MOUNT,
    and DTU_CACHE_MOUNT as Path instances."""
    mod = import_conftest()
    expected = (
        "HOST_PROJECTS_PATH",
        "HOST_CACHE_PATH",
        "DTU_PROJECTS_MOUNT",
        "DTU_CACHE_MOUNT",
    )
    for name in expected:
        assert hasattr(mod, name), f"conftest must export {name}"
        value = getattr(mod, name)
        assert isinstance(value, Path), (
            f"{name} must be a Path instance, got {type(value).__name__}"
        )


def test_bundle_analysis_script_uses_env_vars():
    """_BUNDLE_ANALYSIS_SCRIPT must reference BUNDLE_ANALYSIS_BASE_PATH and
    BUNDLE_ANALYSIS_CACHE_ROOT, and must contain no hard-coded ~/.amplifier path."""
    mod = import_conftest()
    script: str = mod._BUNDLE_ANALYSIS_SCRIPT
    assert "BUNDLE_ANALYSIS_BASE_PATH" in script, (
        "_BUNDLE_ANALYSIS_SCRIPT must reference BUNDLE_ANALYSIS_BASE_PATH env var"
    )
    assert "BUNDLE_ANALYSIS_CACHE_ROOT" in script, (
        "_BUNDLE_ANALYSIS_SCRIPT must reference BUNDLE_ANALYSIS_CACHE_ROOT env var"
    )
    assert "~/.amplifier" not in script, (
        "_BUNDLE_ANALYSIS_SCRIPT must not hard-code a ~/.amplifier path"
    )
    assert "/root/.amplifier" not in script, (
        "_BUNDLE_ANALYSIS_SCRIPT must not hard-code a /root/.amplifier path"
    )
