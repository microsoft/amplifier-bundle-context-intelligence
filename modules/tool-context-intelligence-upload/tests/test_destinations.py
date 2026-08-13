"""Tests for reading fan-out destinations from settings.yaml.

read_destinations deliberately delegates destination construction to the
hook's own HookConfigResolver, so these tests assert on the hook's
Destination type -- proving reuse rather than a lookalike reimplementation.
"""

from __future__ import annotations

import os
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


EXPANSION_SETTINGS = """
overrides:
  hook-context-intelligence:
    config:
      destinations:
        team:
          url: "https://${CI_HOST_FROM_KEYS_ENV}/api"
          api_key: "${CI_TOKEN_FROM_KEYS_ENV}"
          auth_resource: "api://${CI_APP_ID_FROM_KEYS_ENV}"
          include: ["**"]
"""


def test_placeholders_expand_from_values_that_only_keys_env_supplies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_environ: None
) -> None:
    """read_destinations must call the keys.env loader BEFORE expanding ${VAR}.

    The three variables exist nowhere but the (stubbed) keys.env loader, so
    if read_destinations skipped the load step the placeholders would expand
    to "".
    """
    os.environ.pop("CI_HOST_FROM_KEYS_ENV", None)
    os.environ.pop("CI_TOKEN_FROM_KEYS_ENV", None)
    os.environ.pop("CI_APP_ID_FROM_KEYS_ENV", None)

    def fake_loader() -> None:
        os.environ.setdefault("CI_HOST_FROM_KEYS_ENV", "team.example.com")
        os.environ.setdefault("CI_TOKEN_FROM_KEYS_ENV", "sk-from-keys-env")
        os.environ.setdefault("CI_APP_ID_FROM_KEYS_ENV", "abc-123")

    monkeypatch.setattr(destinations_mod, "load_keys_env_into_environ", fake_loader)
    settings_path = write_settings(tmp_path, EXPANSION_SETTINGS)

    result = read_destinations(settings_path)

    team = result["team"]
    assert team.url == "https://team.example.com/api"
    assert team.api_key == "sk-from-keys-env"
    assert team.auth_resource == "api://abc-123"


def test_the_real_keys_env_loader_feeds_placeholder_expansion_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_environ: None
) -> None:
    """Same wiring as above, but through the real loader reading a real keys.env file."""
    from amplifier_module_tool_context_intelligence_upload.keys_env import (
        load_keys_env_into_environ,
    )

    os.environ.pop("CI_HOST_FROM_KEYS_ENV", None)
    os.environ.pop("CI_TOKEN_FROM_KEYS_ENV", None)
    os.environ.pop("CI_APP_ID_FROM_KEYS_ENV", None)

    keys_env = tmp_path / "keys.env"
    keys_env.write_text(
        "# team server\n"
        "CI_HOST_FROM_KEYS_ENV=team.example.com\n"
        'CI_TOKEN_FROM_KEYS_ENV="sk-from-keys-env"\n'
        "CI_APP_ID_FROM_KEYS_ENV=abc-123\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        destinations_mod,
        "load_keys_env_into_environ",
        lambda: load_keys_env_into_environ(keys_env),
    )
    settings_path = write_settings(tmp_path, EXPANSION_SETTINGS)

    result = read_destinations(settings_path)

    team = result["team"]
    assert team.url == "https://team.example.com/api"
    assert team.api_key == "sk-from-keys-env"
    assert team.auth_resource == "api://abc-123"


def test_legacy_scalar_config_synthesizes_a_single_default_destination(
    tmp_path: Path,
) -> None:
    """Back-compat: the pre-fan-out flat keys still yield one usable destination.

    The synthesis is the hook resolver's own (config_resolver.py:629-649) — we
    inherit it by reusing HookConfigResolver rather than re-deriving it here.
    """
    settings_path = write_settings(tmp_path, LEGACY_SCALAR_SETTINGS)

    result = read_destinations(settings_path)

    assert list(result) == ["default"]
    default = result["default"]
    assert isinstance(default, Destination)
    assert default.name == "default"
    assert default.url == "https://legacy.example.com"
    assert default.api_key == "legacy-key"
    assert default.include == ("**",)
    assert default.exclude == ()


def test_legacy_scalars_with_a_url_but_no_api_key_yield_no_destinations(
    tmp_path: Path,
) -> None:
    """Matches hook behavior: url-without-key degrades to local-only, never raises."""
    settings_path = write_settings(
        tmp_path,
        "overrides:\n"
        "  hook-context-intelligence:\n"
        "    config:\n"
        "      context_intelligence_server_url: https://legacy.example.com\n",
    )

    assert read_destinations(settings_path) == {}


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("empty file", ""),
        ("no overrides key", "some_other_key: 1\n"),
        ("no hook override", "overrides:\n  hooks-logging:\n    config:\n      x: 1\n"),
        ("empty config block", "overrides:\n  hook-context-intelligence:\n    config: {}\n"),
        (
            "explicit empty destinations",
            "overrides:\n  hook-context-intelligence:\n    config:\n      destinations: {}\n",
        ),
        ("malformed yaml", "overrides: [:: not yaml at all\n"),
        ("top-level scalar", "just-a-string\n"),
    ],
)
def test_settings_without_usable_destinations_return_an_empty_map(
    tmp_path: Path, label: str, body: str
) -> None:
    settings_path = write_settings(tmp_path, body)

    assert read_destinations(settings_path) == {}, label


def test_a_missing_settings_file_returns_an_empty_map(tmp_path: Path) -> None:
    assert read_destinations(tmp_path / "no-such-settings.yaml") == {}


def make_destination(name: str, url: str) -> Destination:
    """Build a Destination directly — selection tests do not need settings.yaml."""
    return Destination(name=name, url=url, api_key=f"{name}-key", include=("**",))


def explode_on_input(_prompt: str = "") -> str:
    raise AssertionError("select_destination must not prompt in this scenario")


def test_selecting_with_no_configured_destinations_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", explode_on_input)

    with pytest.raises(DestinationSelectionError) as excinfo:
        select_destination({}, None, interactive=True)

    message = str(excinfo.value)
    assert "settings.yaml" in message
    assert "--server-url" in message


@pytest.mark.parametrize("interactive", [True, False])
def test_a_single_destination_is_auto_selected_without_prompting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], interactive: bool
) -> None:
    """One destination means there is nothing to disambiguate — so ask nothing."""
    monkeypatch.setattr("builtins.input", explode_on_input)
    only = {"team": make_destination("team", "https://team.example.com")}

    chosen = select_destination(only, None, interactive=interactive)

    assert chosen.name == "team"
    assert capsys.readouterr().out == ""
