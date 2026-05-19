"""
pytest fixtures for DTU bundle-usage end-to-end scenarios.

Uses amplifier-digital-twin exec against ci-bundle-validate DTU.
CI server (host:8000) is reached via host gateway 10.160.61.1.
"""

from __future__ import annotations

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
        """Call a bundle-usage tool via the analyst agent.

        Delegates to ``context-intelligence:bundle-usage-analyst`` with an
        explicit instruction to invoke ``run_bundle_analysis`` directly via
        Python (importing from ``context_intelligence.bundle_analysis``).
        This avoids the analyst spending time searching for ``bundle_usage``
        as a direct tool name — it should go straight to Python.
        """
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
        # Try to extract JSON from output — attempt progressive extraction
        # from largest to smallest JSON object
        candidates = re.findall(r"\{[^{}]{20,}\}", output, re.DOTALL)
        for block in reversed(candidates):  # largest last in re.findall order
            try:
                return json.loads(block)
            except Exception:
                pass
        return {"_raw": output}

    def delegate(self, agent: str, prompt: str) -> str:
        full_prompt = f"Delegate to {agent} with this instruction: {prompt}"
        return self.run_prompt(full_prompt, timeout=480)

    def close(self) -> None:
        pass  # DTU persists
