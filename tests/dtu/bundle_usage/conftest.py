"""
pytest fixtures for DTU bundle-usage end-to-end scenarios.

Uses amplifier-digital-twin exec against ci-bundle-validate DTU.
CI server (host:8000) is reached via host gateway 10.160.61.1.

Mount requirements (read-only bind mounts into the DTU):
  ~/.amplifier/projects/ -> /mnt/amplifier-projects
  ~/.amplifier/cache/    -> /mnt/amplifier-cache

Note: CI graph server is OPTIONAL — bundle_usage is JSONL-primary as of
the 2026-05-21 redesign.  Tests that require only JSONL data will run
without a reachable CI graph server; tests that exercise graph-backed
paths will skip gracefully when the server is absent.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

KNOWN_SESSION_ID: str = os.environ.get(
    "BUNDLE_USAGE_DTU_SESSION_ID",
    "cb56b81d-9cf4-4eb9-9cb0-ed261f63dfc5",
)
DTU_ENV_ID: str = os.environ.get("BUNDLE_USAGE_DTU_ENV_ID", "ci-bundle-validate")
CI_SERVER_URL: str = "http://10.160.61.1:8000"
CI_API_KEY: str = "kuD8xSnjKC4QTa-4kuqlw9uLA1EonsFntMvVmU8DAjo"
HERE: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Host-side paths and DTU mount points
# ---------------------------------------------------------------------------
HOST_PROJECTS_PATH: Path = Path.home() / ".amplifier" / "projects"
HOST_CACHE_PATH: Path = Path.home() / ".amplifier" / "cache"
DTU_PROJECTS_MOUNT: Path = Path("/mnt/amplifier-projects")
DTU_CACHE_MOUNT: Path = Path("/mnt/amplifier-cache")

# Workspace name for the known session (env-var override for CI portability).
# The default uses the Amplifier path-encoded workspace directory name.
WORKSPACE: str = os.environ.get(
    "BUNDLE_USAGE_DTU_WORKSPACE", "-home-dicolomb-bundle-use-inspectors"
)

# ---------------------------------------------------------------------------
# Python script executed directly inside the DTU for bundle_usage calls.
# Avoids the LLM route entirely — no timeouts, no parsing uncertainty.
# __SESSION_ID__ is replaced at call time via str.replace().
#
# Reads mount paths from env vars injected by _dtu_exec:
#   BUNDLE_ANALYSIS_BASE_PATH  — DTU mount for ~/.amplifier/projects
#   BUNDLE_ANALYSIS_CACHE_ROOT — DTU mount for ~/.amplifier/cache
# CI graph server is NOT required; workspace is discovered from JSONL paths.
# ---------------------------------------------------------------------------
_BUNDLE_ANALYSIS_SCRIPT = """\
import asyncio, dataclasses, json, os, sys
from pathlib import Path

base_path = Path(os.environ.get('BUNDLE_ANALYSIS_BASE_PATH', str(Path.home() / '.amplifier' / 'projects')))
cache_root = Path(os.environ.get('BUNDLE_ANALYSIS_CACHE_ROOT', str(Path.home() / '.amplifier' / 'cache')))

# Ensure context_intelligence is importable.  Prefer the skills-cache location
# (where Amplifier installs the bundle and includes bundle_analysis) over the
# raw bundle-cache directories (which ship only legacy package layout).
import importlib.util as _ilu
if _ilu.find_spec('context_intelligence') is None:
    # Try skills sub-directory first (where Amplifier installs bundles with bundle_analysis),
    # then fall back to the mounted cache dir (which may contain an older package layout).
    for _search_base in [
        Path.home() / '.amplifier' / 'cache' / 'skills',
        cache_root / 'skills',
        cache_root,
    ]:
        for _ci_dir in sorted(_search_base.glob('amplifier-bundle-context-intelligence-*'), reverse=True):
            sys.path.insert(0, str(_ci_dir))
            if _ilu.find_spec('context_intelligence') is not None:
                break
        if _ilu.find_spec('context_intelligence') is not None:
            break

from context_intelligence.bundle_analysis import run_bundle_analysis

session_id = '__SESSION_ID__' or None

# Discover workspace from JSONL directory structure (JSONL-primary, no CI server needed).
# When BUNDLE_ANALYSIS_WORKSPACE is set (injected by the test harness), use it directly.
workspace = os.environ.get('BUNDLE_ANALYSIS_WORKSPACE', '')
if not workspace:
    if session_id:
        for _ws_dir in sorted(base_path.iterdir()):
            if _ws_dir.is_dir() and (_ws_dir / 'sessions' / session_id).exists():
                workspace = _ws_dir.name
                break
    else:
        # No session_id and no workspace hint: use the workspace with the most sessions.
        _best_ws, _best_count = '', 0
        for _ws_dir in base_path.iterdir():
            if _ws_dir.is_dir():
                _sessions_dir = _ws_dir / 'sessions'
                _count = len(list(_sessions_dir.iterdir())) if _sessions_dir.exists() else 0
                if _count > _best_count:
                    _best_ws, _best_count = _ws_dir.name, _count
        workspace = _best_ws

result = asyncio.run(run_bundle_analysis(
    base_path=base_path,
    cache_root=cache_root,
    workspace=workspace,
    session_id=session_id,
))
output = {
    'scope': dataclasses.asdict(result['scope']),
    'signals': result['signals'],
    'inventory': result['inventory'],
    'gap': result['gap'],
}
print(json.dumps(output, default=list))
"""


def _run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)


def _dtu_exec(cmd: str, *, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command inside the DTU, injecting CI server and mount path env vars."""
    wrapped = (
        f"export AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL='{CI_SERVER_URL}' && "
        f"export AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY='{CI_API_KEY}' && "
        f"export BUNDLE_ANALYSIS_BASE_PATH='{DTU_PROJECTS_MOUNT}' && "
        f"export BUNDLE_ANALYSIS_CACHE_ROOT='{DTU_CACHE_MOUNT}' && "
        f"export BUNDLE_ANALYSIS_WORKSPACE='{WORKSPACE}' && "
        f"{cmd}"
    )
    return _run(
        ["amplifier-digital-twin", "exec", DTU_ENV_ID, "--", "bash", "-lc", wrapped],
        timeout=timeout,
    )


def _dtu_run(prompt: str, *, timeout: int = 450) -> str:
    """Run a single-turn amplifier session inside DTU with CI server configured.

    The inner ``amplifier run`` timeout is set to 400 s to give the analyst
    enough time to call ``run_bundle_analysis`` via Python without hitting the
    shell timeout when exploring the codebase.
    """
    escaped = prompt.replace('"', '\\"')
    result = _dtu_exec(
        f'timeout 400 amplifier run --mode single "{escaped}" 2>/dev/null',
        timeout=timeout,
    )
    return result.stdout


@pytest.fixture(scope="session", autouse=True)
def dtu_bootstrap():
    """Check DTU is running, CI server availability (optional), and JSONL preflight."""
    # Check DTU alive
    result = _dtu_exec("amplifier version 2>&1 | head -1", timeout=20)
    if result.returncode != 0:
        pytest.skip(f"DTU '{DTU_ENV_ID}' not available: {result.stderr}")

    # Check CI server reachable from DTU (optional — JSONL-primary redesign).
    # A missing CI server is NOT a skip condition: JSONL-primary tests (test_02,
    # test_09) must run even when the graph server is down.  Only tests that
    # explicitly require graph-backed paths should skip on their own when they
    # detect the server absent.
    ci_check = _dtu_exec(
        f"curl -s --max-time 5 -H 'Authorization: Bearer {CI_API_KEY}' "
        f"'{CI_SERVER_URL}/cypher' -H 'Content-Type: application/json' "
        '-d \'{"query": "MATCH (s:Session) RETURN count(s) as n LIMIT 1"}\' 2>&1',
        timeout=20,
    )
    if "results" not in ci_check.stdout:
        # Log as a warning; do NOT skip — JSONL-only tests proceed without CI server.
        print(
            f"\n[dtu_bootstrap] WARNING: CI server not reachable from DTU "
            f"({CI_SERVER_URL}). Graph-backed assertions will be skipped per-test. "
            f"JSONL-primary tests continue normally."
        )

    # JSONL preflight: verify the expected events.jsonl is accessible via the mount.
    # Use uppercase tokens (EXISTS/MISSING) so they cannot appear in the command
    # string itself, then parse the JSON envelope from _dtu_exec before checking.
    # TODO(infrastructure): ci-bundle-validate DTU profile does not mount
    # ~/.amplifier/projects/ → /mnt/amplifier-projects or
    # ~/.amplifier/cache/   → /mnt/amplifier-cache.
    # Fix: update the DTU profile to declare both bind mounts.
    expected_jsonl = (
        DTU_PROJECTS_MOUNT
        / WORKSPACE
        / "sessions"
        / KNOWN_SESSION_ID
        / "context-intelligence"
        / "events.jsonl"
    )
    jsonl_check = _dtu_exec(
        f"[ -f '{expected_jsonl}' ] && echo EXISTS || echo MISSING",
        timeout=20,
    )
    # Parse the JSON envelope that amplifier-digital-twin exec wraps around output.
    try:
        _outer = json.loads(jsonl_check.stdout)
        _inner = _outer.get("stdout", "") or ""
    except Exception:
        _inner = jsonl_check.stdout or ""
    if "MISSING" in _inner or "EXISTS" not in _inner:
        pytest.skip(
            f"JSONL not found at {expected_jsonl} inside DTU. "
            f"Mount requirements: "
            f"~/.amplifier/projects/ -> /mnt/amplifier-projects (read-only), "
            f"~/.amplifier/cache/ -> /mnt/amplifier-cache (read-only). "
            f"Launch the DTU with these bind mounts to run bundle_usage tests."
        )


@pytest.fixture
def dtu_session():
    session = DTUSession()
    yield session
    session.close()


class DTUSession:
    """Amplifier session runner inside the DTU."""

    def __init__(self) -> None:
        self._active_mode: str | None = None

    @classmethod
    def spawn(cls) -> "DTUSession":
        return cls()

    def list_tools(self) -> list[str]:
        result = _dtu_exec("amplifier tool list 2>&1", timeout=30)
        if result.returncode != 0:
            pytest.fail(f"tool list failed: {result.stdout}")
        tools = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("[on]"):
                parts = stripped.split()
                if len(parts) >= 2:
                    tools.append(parts[1])
        return tools

    def activate_mode(self, name: str) -> None:
        self._active_mode = name

    def run_prompt(self, prompt: str, *, timeout: int = 450) -> str:
        """Run a prompt inside the DTU, activating stored mode first."""
        if self._active_mode:
            full_prompt = (
                f"Step 1: Activate the {self._active_mode} mode by calling "
                f"mode(operation=set, name={self._active_mode}). "
                f"If the first call is denied (warn gate), call it again immediately. "
                f"Step 2: {prompt}"
            )
        else:
            full_prompt = prompt
        return _dtu_run(full_prompt, timeout=timeout)

    def call_tool(self, name: str, **kwargs) -> dict:
        """For bundle_usage: run Python directly in DTU, bypassing the LLM.

        The LLM route is unreliable for ``bundle_usage`` because the model
        produces formatted text/tables rather than raw JSON, and the regex
        extraction frequently fails.  The direct path writes a self-contained
        Python script to the DTU via base64 and executes it, then parses the
        last JSON line from stdout.

        For all other tool names the original LLM-delegation route is kept as
        a fallback.
        """
        if name == "bundle_usage":
            return self._call_bundle_usage_direct(**kwargs)
        # LLM route for other tools (kept for completeness / future scenarios)
        args_str = ", ".join(f'{k}="{v}"' for k, v in kwargs.items())
        prompt = (
            f"Delegate to context-intelligence:bundle-usage-analyst with instruction: "
            f"Use Python to call run_bundle_analysis from "
            f"context_intelligence.bundle_analysis directly. "
            f"Read the CI server URL and API key from ~/.amplifier/settings.yaml. "
            f"Call run_bundle_analysis with {args_str or 'no extra arguments'} "
            f"and return the complete JSON result including signals, inventory, "
            f"and gap sections."
        )
        output = self.run_prompt(prompt, timeout=480)
        candidates = re.findall(r"\{[^{}]{20,}\}", output, re.DOTALL)
        for block in reversed(candidates):
            try:
                return json.loads(block)
            except Exception:
                pass
        return {"_raw": output}

    def _call_bundle_usage_direct(self, **kwargs) -> dict:
        """Execute run_bundle_analysis directly in the DTU via Python.  No LLM.

        Encodes ``_BUNDLE_ANALYSIS_SCRIPT`` as base64, pipes it into the DTU
        via ``bash -lc``, and parses the last JSON line from stdout.  Mount
        paths are injected as env vars by ``_dtu_exec`` so the script only
        needs to read ``os.environ``.

        ``amplifier-digital-twin exec`` wraps the command output in an outer
        JSON envelope: ``{"id": ..., "exit_code": N, "stdout": "...", ...}``.
        We unwrap that envelope first, then find the last JSON-looking line in
        the inner stdout (which is the ``print(json.dumps(output))`` line from
        the Python script).
        """
        session_id = kwargs.get("session_id", "")
        py_script = _BUNDLE_ANALYSIS_SCRIPT.replace("__SESSION_ID__", session_id)
        encoded = base64.b64encode(py_script.encode()).decode()
        proc = _dtu_exec(
            f"echo '{encoded}' | base64 -d > /tmp/rba.py && python3 /tmp/rba.py",
            timeout=60,
        )
        # amplifier-digital-twin exec wraps output as a single JSON line:
        #   {"id": "...", "exit_code": 0, "stdout": "<actual output>", "stderr": ""}
        # Extract the inner stdout before searching for our result JSON.
        try:
            outer = json.loads(proc.stdout)
            inner_stdout = outer.get("stdout", "")
        except Exception:
            inner_stdout = proc.stdout

        for line in reversed(inner_stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    pass
        return {"_raw": inner_stdout}

    def delegate(self, agent: str, prompt: str) -> str:
        full_prompt = f"Delegate to {agent} with this instruction: {prompt}"
        return self.run_prompt(full_prompt, timeout=480)

    def close(self) -> None:
        pass  # DTU persists
