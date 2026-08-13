"""Tests for reading fan-out destinations from settings.yaml.

read_destinations deliberately delegates destination construction to the
hook's own HookConfigResolver, so these tests assert on the hook's
Destination type -- proving reuse rather than a lookalike reimplementation.
"""

from __future__ import annotations

import os  # noqa: F401 -- kept for parity with future destination-selection tests in this module
from pathlib import Path

import pytest
from amplifier_module_hook_context_intelligence.config_resolver import Destination

from amplifier_module_tool_context_intelligence_upload import destinations as destinations_mod
from amplifier_module_tool_context_intelligence_upload.destinations import (
    DestinationSelectionError,  # noqa: F401 -- exercised by select_destination tests
    read_destinations,
    select_destination,  # noqa: F401 -- exercised by select_destination tests
)

MULTI_DESTINATION_SETTINGS = """
overrides:
  hook-context-intelligence:
    config:
      destinations:
        team:
          url: https://team.example.com
          api_key: team-key
          include: ["repos/**"]
          exclude: ["repos/client-*/**"]
        personal:
          url: https://personal.example.com
          api_key: personal-key
          include: ["**"]
"""

LEGACY_SCALAR_SETTINGS = """
overrides:
  hook-context-intelligence:
    config:
      context_intelligence_server_url: https://legacy.example.com
      context_intelligence_api_key: legacy-key
"""


def write_settings(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _never_read_the_real_keys_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests off the developer's real ~/.amplifier/keys.env.

    Tests that exercise keys.env-backed expansion override this by
    re-patching the same attribute (later monkeypatch wins).
    """
    monkeypatch.setattr(destinations_mod, "load_keys_env_into_environ", lambda: None)


def test_read_destinations_parses_the_multi_destination_map(tmp_path: Path) -> None:
    settings_path = write_settings(tmp_path, MULTI_DESTINATION_SETTINGS)

    result = read_destinations(settings_path)

    assert sorted(result) == ["personal", "team"]
    assert all(isinstance(dest, Destination) for dest in result.values())

    team = result["team"]
    assert team.name == "team"
    assert team.url == "https://team.example.com"
    assert team.api_key == "team-key"
    assert team.include == ("repos/**",)
    assert team.exclude == ("repos/client-*/**",)
    assert team.auth_mode == "static"
    assert team.auth_resource == ""

    personal = result["personal"]
    assert personal.url == "https://personal.example.com"
    assert personal.include == ("**",)
    assert personal.exclude == ()
