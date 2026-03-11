"""TDD test: verify conftest Neo4j constants are env-var-driven.

This test imports conftest as a regular module (via importlib) and checks that
setting NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NEO4J_DATABASE env vars is
respected.  Before the change the constants are hardcoded, so the tests will
fail (RED). After the change they should pass (GREEN).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path


# ---------------------------------------------------------------------------
# Helper to (re)load conftest with a given set of env vars active
# ---------------------------------------------------------------------------

CONFTEST_PATH = Path(__file__).parent / "conftest.py"


def _reload_conftest(env_overrides: dict[str, str]) -> types.ModuleType:
    """Reload conftest module in an isolated env snapshot."""
    # Remove cached conftest modules so importlib loads fresh code
    for key in list(sys.modules):
        if "conftest" in key:
            del sys.modules[key]

    old_env = os.environ.copy()
    try:
        # Clear Neo4j-related vars, then apply overrides
        for k in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
            os.environ.pop(k, None)
        os.environ.update(env_overrides)

        spec = importlib.util.spec_from_file_location("_conftest_under_test", CONFTEST_PATH)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    finally:
        os.environ.clear()
        os.environ.update(old_env)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_neo4j_uri_defaults_when_no_env_var() -> None:
    """NEO4J_URI should default to 'neo4j://localhost:7690' when env var absent."""
    mod = _reload_conftest({})
    assert mod.NEO4J_URI == "neo4j://localhost:7690"  # type: ignore[attr-defined]


def test_neo4j_uri_reads_env_var() -> None:
    """NEO4J_URI should use the NEO4J_URI env var when set."""
    mod = _reload_conftest({"NEO4J_URI": "neo4j://somehost:7687"})
    assert mod.NEO4J_URI == "neo4j://somehost:7687"  # type: ignore[attr-defined]


def test_neo4j_auth_is_none_when_no_credentials() -> None:
    """NEO4J_AUTH should be None when NEO4J_USER/NEO4J_PASSWORD are absent."""
    mod = _reload_conftest({})
    assert mod.NEO4J_AUTH is None  # type: ignore[attr-defined]


def test_neo4j_auth_builds_tuple_from_env_vars() -> None:
    """NEO4J_AUTH should be (user, pass) tuple when both env vars are set."""
    mod = _reload_conftest({"NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "secret"})
    assert mod.NEO4J_AUTH == ("neo4j", "secret")  # type: ignore[attr-defined]


def test_neo4j_database_defaults_when_no_env_var() -> None:
    """NEO4J_DATABASE should default to 'neo4j' when env var absent."""
    mod = _reload_conftest({})
    assert mod.NEO4J_DATABASE == "neo4j"  # type: ignore[attr-defined]


def test_neo4j_database_reads_env_var() -> None:
    """NEO4J_DATABASE should use the NEO4J_DATABASE env var when set."""
    mod = _reload_conftest({"NEO4J_DATABASE": "mydb"})
    assert mod.NEO4J_DATABASE == "mydb"  # type: ignore[attr-defined]
