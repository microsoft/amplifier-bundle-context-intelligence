"""
Meta-tests verifying the structure of the DTU bootstrap harness.

These tests are structural/static checks — they verify that the conftest module
defines the correct interface without actually calling amplifier-tester.

The dtu_bootstrap autouse fixture is overridden here so these structural checks
run even when amplifier-tester is not installed.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Override autouse DTU bootstrap for structural-only checks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def dtu_bootstrap():  # type: ignore[override]
    """No-op override: structural tests don't need a live DTU."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONFTEST_PATH = Path(__file__).resolve().parent / "conftest.py"


def import_conftest():
    """Import the bundle_usage conftest as a module (avoids pytest magic)."""
    spec = importlib.util.spec_from_file_location("_bundle_usage_conftest", CONFTEST_PATH)
    assert spec is not None, f"Could not create module spec from {CONFTEST_PATH}"
    assert spec.loader is not None, "Module spec has no loader"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Tests — conftest module structure
# ---------------------------------------------------------------------------


class TestConftest:
    def test_conftest_file_exists(self):
        assert CONFTEST_PATH.exists(), f"conftest.py not found at {CONFTEST_PATH}"

    def test_known_session_id_constant(self):
        mod = import_conftest()
        assert hasattr(mod, "KNOWN_SESSION_ID"), "conftest must export KNOWN_SESSION_ID"
        assert mod.KNOWN_SESSION_ID == "cb56b81d-9cf4-4eb9-9cb0-ed261f63dfc5"

    def test_here_constant(self):
        mod = import_conftest()
        assert hasattr(mod, "HERE"), "conftest must export HERE"
        assert isinstance(mod.HERE, Path)
        assert mod.HERE == CONFTEST_PATH.parent

    def test_dtu_session_class_exists(self):
        mod = import_conftest()
        assert hasattr(mod, "DTUSession"), "conftest must define DTUSession class"

    def test_dtu_session_has_spawn_classmethod(self):
        mod = import_conftest()
        cls = mod.DTUSession
        assert hasattr(cls, "spawn"), "DTUSession must have spawn classmethod"
        assert isinstance(inspect.getattr_static(cls, "spawn"), classmethod)

    def test_dtu_session_has_list_tools(self):
        mod = import_conftest()
        assert hasattr(mod.DTUSession, "list_tools"), "DTUSession must have list_tools method"

    def test_dtu_session_has_activate_mode(self):
        mod = import_conftest()
        assert hasattr(mod.DTUSession, "activate_mode"), "DTUSession must have activate_mode method"

    def test_dtu_session_has_call_tool(self):
        mod = import_conftest()
        assert hasattr(mod.DTUSession, "call_tool"), "DTUSession must have call_tool method"

    def test_dtu_session_has_delegate(self):
        mod = import_conftest()
        assert hasattr(mod.DTUSession, "delegate"), "DTUSession must have delegate method"

    def test_dtu_session_has_close(self):
        mod = import_conftest()
        assert hasattr(mod.DTUSession, "close"), "DTUSession must have close method"

    def test_dtu_bootstrap_fixture_exists(self):
        """dtu_bootstrap must be a session-scoped autouse fixture in conftest."""
        import ast

        source = CONFTEST_PATH.read_text()
        tree = ast.parse(source)

        # Find the dtu_bootstrap function def that has @pytest.fixture decorator
        # with scope="session" and autouse=True
        found_fixture = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "dtu_bootstrap":
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call):
                        # Check decorator arguments
                        kwargs = {kw.arg: kw for kw in deco.keywords}
                        scope_ok = False
                        autouse_ok = False
                        if "scope" in kwargs:
                            v = kwargs["scope"].value
                            if isinstance(v, ast.Constant) and v.value == "session":
                                scope_ok = True
                        if "autouse" in kwargs:
                            v = kwargs["autouse"].value
                            if isinstance(v, ast.Constant) and v.value is True:
                                autouse_ok = True
                        if scope_ok and autouse_ok:
                            found_fixture = True
        assert found_fixture, (
            "conftest.py must define dtu_bootstrap with "
            "@pytest.fixture(scope='session', autouse=True)"
        )

    def test_dtu_session_fixture_exists(self):
        """dtu_session must be a per-test fixture defined in conftest."""
        import ast

        source = CONFTEST_PATH.read_text()
        tree = ast.parse(source)

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "dtu_session":
                for deco in node.decorator_list:
                    # Could be bare @pytest.fixture or @pytest.fixture()
                    is_fixture = (isinstance(deco, ast.Attribute) and deco.attr == "fixture") or (
                        isinstance(deco, ast.Call)
                        and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr == "fixture"
                    )
                    if is_fixture:
                        found = True
        assert found, "conftest.py must define dtu_session with @pytest.fixture"


# ---------------------------------------------------------------------------
# Tests — dtu_setup.sh content
# ---------------------------------------------------------------------------


class TestDtuSetupSh:
    SCRIPT = CONFTEST_PATH.parent / "dtu_setup.sh"

    def test_script_exists(self):
        assert self.SCRIPT.exists(), "dtu_setup.sh must exist"

    def test_script_is_executable(self):
        assert self.SCRIPT.exists(), "dtu_setup.sh must exist"
        assert os.access(str(self.SCRIPT), os.X_OK), "dtu_setup.sh must be executable"

    def test_script_has_set_euo_pipefail(self):
        content = self.SCRIPT.read_text()
        assert "set -euo pipefail" in content

    def test_script_references_bundle_dir(self):
        content = self.SCRIPT.read_text()
        assert "BUNDLE_DIR" in content

    def test_script_references_known_session_id(self):
        content = self.SCRIPT.read_text()
        assert "KNOWN_SESSION_ID" in content
        assert "cb56b81d-9cf4-4eb9-9cb0-ed261f63dfc5" in content

    def test_script_echoes_dtu_ready(self):
        content = self.SCRIPT.read_text()
        assert "==> DTU ready" in content

    def test_script_uses_amplifier_tester_setup_digital_twin(self):
        content = self.SCRIPT.read_text()
        assert "amplifier-tester setup-digital-twin" in content

    def test_script_uses_amplifier_tester_ci_health(self):
        content = self.SCRIPT.read_text()
        assert "amplifier-tester ci-health" in content

    def test_script_uses_amplifier_tester_ci_cypher(self):
        content = self.SCRIPT.read_text()
        assert "amplifier-tester ci-cypher" in content

    def test_script_exits_2_on_ci_health_failure(self):
        content = self.SCRIPT.read_text()
        assert "exit 2" in content

    def test_script_exits_3_on_session_not_found(self):
        content = self.SCRIPT.read_text()
        assert "exit 3" in content
