"""
pytest fixtures for DTU bundle-usage end-to-end scenarios.

Provides:
  - dtu_bootstrap: session-scoped autouse fixture — runs dtu_setup.sh once and
    calls pytest.skip() if the DTU cannot be stood up (e.g. amplifier-tester
    not installed, CI graph unreachable, or ground-truth session absent).
  - dtu_session: per-test fixture — spawns a fresh Amplifier session inside the
    DTU and yields a DTUSession handle; closes the session after the test.

Note: If the local amplifier-tester CLI interface differs from what is described
below, adjust the command lists in DTUSession to use the equivalent subcommands.
The four logical operations (spawn, list-tools, call-tool, delegate) are the
interface that scenario tests depend on.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KNOWN_SESSION_ID: str = os.environ.get(
    "BUNDLE_USAGE_DTU_SESSION_ID",
    "21d92985-34a9-40ed-8636-f77cd61b7ca1",
)

HERE: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    *,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run a subprocess without raising on non-zero exit.

    Parameters
    ----------
    cmd:
        Command and arguments list.
    timeout:
        Maximum seconds to wait (default 120).

    Returns
    -------
    subprocess.CompletedProcess
        Caller is responsible for checking returncode.
    """
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def dtu_bootstrap():
    """Run dtu_setup.sh once per pytest session.

    Skips the entire session (via pytest.skip) if the bootstrap script exits
    non-zero, including the cases where:
      - amplifier-tester is not installed
      - the CI graph is unreachable (exit 2)
      - the ground-truth session is absent (exit 3)
    """
    result = _run(["bash", str(HERE / "dtu_setup.sh")])
    if result.returncode != 0:
        pytest.skip(
            f"DTU bootstrap failed (exit {result.returncode}).\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


@pytest.fixture
def dtu_session():
    """Spawn a fresh Amplifier session inside the DTU.

    Yields a DTUSession handle for the test to use, then closes the session
    after the test completes (pass or fail).
    """
    session = DTUSession.spawn()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# DTUSession
# ---------------------------------------------------------------------------


class DTUSession:
    """Thin Python wrapper over ``amplifier-tester session`` subcommands.

    Methods map 1-to-1 onto the four operations scenario tests rely on:
    spawn, list-tools, call-tool, and delegate.
    """

    def __init__(self, session_dir: str) -> None:
        self.session_dir = session_dir

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def spawn(cls) -> "DTUSession":
        """Spawn a new session with the context-intelligence bundle.

        Returns
        -------
        DTUSession
            Wrapper around the newly-created session directory.
        """
        result = _run(
            [
                "amplifier-tester",
                "session",
                "spawn",
                "--bundle",
                "context-intelligence",
            ]
        )
        result.check_returncode()
        session_dir = result.stdout.strip()
        return cls(session_dir=session_dir)

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    def list_tools(self) -> list[str]:
        """Return the list of tool names available in this session."""
        result = _run(
            [
                "amplifier-tester",
                "session",
                "list-tools",
                "--dir",
                self.session_dir,
            ]
        )
        result.check_returncode()
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def activate_mode(self, name: str) -> None:
        """Activate a mode inside the session.

        Sends ``mode(operation="set", name=name)`` via the session tool runner.
        """
        payload = json.dumps({"operation": "set", "name": name})
        result = _run(
            [
                "amplifier-tester",
                "session",
                "tool",
                "--name",
                "mode",
                "--input",
                payload,
            ]
        )
        result.check_returncode()

    def call_tool(self, name: str, **kwargs) -> dict:
        """Call a named tool inside the session.

        Parameters
        ----------
        name:
            Tool name (e.g. ``"bundle_usage"``).
        **kwargs:
            JSON-serialisable keyword arguments forwarded as tool input.

        Returns
        -------
        dict
            Parsed JSON response, or ``{"_raw": stdout}`` if parsing fails.
        """
        payload = json.dumps(kwargs)
        result = _run(
            [
                "amplifier-tester",
                "session",
                "tool",
                "--dir",
                self.session_dir,
                "--name",
                name,
                "--input",
                payload,
            ],
            timeout=180,
        )
        result.check_returncode()
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"_raw": result.stdout}

    def delegate(self, agent: str, prompt: str) -> str:
        """Delegate to an agent inside the session.

        Parameters
        ----------
        agent:
            Agent identifier (e.g. ``"context-intelligence:graph-analyst"``).
        prompt:
            Natural-language instruction for the agent.

        Returns
        -------
        str
            Raw stdout from the delegate subcommand.
        """
        result = _run(
            [
                "amplifier-tester",
                "session",
                "delegate",
                "--dir",
                self.session_dir,
                "--agent",
                agent,
                "--prompt",
                prompt,
            ],
            timeout=600,
        )
        result.check_returncode()
        return result.stdout

    def close(self) -> None:
        """Close the session (best-effort; errors are silently ignored)."""
        _run(
            [
                "amplifier-tester",
                "session",
                "close",
                "--dir",
                self.session_dir,
            ]
        )
