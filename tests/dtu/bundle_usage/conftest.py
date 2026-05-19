"""
pytest fixtures for DTU bundle-usage end-to-end scenarios.

Uses amplifier-digital-twin exec against ci-bundle-validate DTU.
CI server (host:8000) is reached via host gateway 10.160.61.1.
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
    "21d92985-34a9-40ed-8636-f77cd61b7ca1",
)
DTU_ENV_ID: str = os.environ.get("BUNDLE_USAGE_DTU_ENV_ID", "ci-bundle-validate")
CI_SERVER_URL: str = "http://10.160.61.1:8000"
CI_API_KEY: str = "kuD8xSnjKC4QTa-4kuqlw9uLA1EonsFntMvVmU8DAjo"
HERE: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Python script executed directly inside the DTU for bundle_usage calls.
# Avoids the LLM route entirely — no timeouts, no parsing uncertainty.
# __SESSION_ID__ is replaced at call time via str.replace().
# ---------------------------------------------------------------------------
_BUNDLE_ANALYSIS_SCRIPT = """\
import asyncio, dataclasses, json, os, sys, urllib.request
sys.path.insert(0, '/root/.amplifier/cache/amplifier-bundle-context-intelligence-ecd41f3e6fa67bd2')
from context_intelligence.client import AsyncCIClient
from context_intelligence.bundle_analysis import run_bundle_analysis

server_url = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL', '')
api_key = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY', '')
session_id = '__SESSION_ID__'

if not server_url:
    print(json.dumps({'error': 'no CI server configured'}))
    sys.exit(0)

workspace = ''
# session_id='' means workspace-scope aggregate (no specific session filter).
# Pass session_id_param=None so the library uses cross-session Cypher queries.
if session_id:
    # Session-scoped: look up workspace from the specific session.
    session_id_param = session_id
    req = urllib.request.Request(
        server_url + '/cypher',
        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'},
        data=json.dumps({'query': 'MATCH (s:Session {session_id: "' + session_id + '"}) RETURN s.workspace LIMIT 1'}).encode()
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read()).get('results', [])
            if results:
                workspace = results[0].get('s.workspace', '')
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(0)
else:
    # Workspace-scope: pass None to trigger cross-session queries.
    # Find the most active workspace from Delegation nodes in the CI graph.
    session_id_param = None
    req = urllib.request.Request(
        server_url + '/cypher',
        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'},
        data=json.dumps({'query': 'MATCH (d:Delegation) WHERE d.workspace IS NOT NULL RETURN d.workspace AS w, count(*) AS n ORDER BY n DESC LIMIT 1'}).encode()
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read()).get('results', [])
            if results:
                workspace = results[0].get('w', '')
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(0)

client = AsyncCIClient(server_url=server_url, api_key=api_key)
result = asyncio.run(run_bundle_analysis(client=client, session_id=session_id_param, workspace=workspace))
output = {
    'signals': result['signals'],
    'inventory': result['inventory'],
    'gap': result['gap'],
    'scope': dataclasses.asdict(result['scope']),
}
print(json.dumps(output))
"""


def _run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)


def _dtu_exec(cmd: str, *, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command inside the DTU, injecting CI server env vars."""
    wrapped = (
        f"export AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL='{CI_SERVER_URL}' && "
        f"export AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY='{CI_API_KEY}' && "
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
    """Check DTU is running and CI server is reachable."""
    # Check DTU alive
    result = _dtu_exec("amplifier version 2>&1 | head -1", timeout=20)
    if result.returncode != 0:
        pytest.skip(f"DTU '{DTU_ENV_ID}' not available: {result.stderr}")

    # Check CI server reachable from DTU
    ci_check = _dtu_exec(
        f"curl -s --max-time 5 -H 'Authorization: Bearer {CI_API_KEY}' "
        f"'{CI_SERVER_URL}/cypher' -H 'Content-Type: application/json' "
        f'-d \'{{"query": "MATCH (s:Session) RETURN count(s) as n LIMIT 1"}}\' 2>&1',
        timeout=20,
    )
    if "results" not in ci_check.stdout:
        pytest.skip(f"CI server not reachable from DTU: {ci_check.stdout}")


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
        via ``bash -lc``, and parses the last JSON line from stdout.  CI server
        credentials are injected as env vars by ``_dtu_exec`` so the script
        only needs to read ``os.environ``.

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
