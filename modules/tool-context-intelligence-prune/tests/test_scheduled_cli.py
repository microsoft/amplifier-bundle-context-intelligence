"""Thin wrapper parse/render checks; scheduler behavior has separate live proof."""

import json
from pathlib import Path

from click.testing import CliRunner

from amplifier_module_tool_context_intelligence_prune import scheduled_cli as cli


def test_install_passes_config_and_explicit_enable(monkeypatch, tmp_path):
    calls = []

    def observe(config, **options):
        calls.append((config, options))
        return {"state": "installed"}

    monkeypatch.setattr(cli, "install", observe)
    result = CliRunner().invoke(
        cli.main,
        [
            "install",
            "--root",
            str(tmp_path),
            "--at",
            "01:30",
            "--older-than",
            "14d",
            "--enable",
        ],
    )
    assert result.exit_code == 0, result.output
    config, options = calls[0]
    assert config.at == "01:30"
    assert config.older_than == "14d"
    assert config.min_inactive == "7d"
    assert options["enable"] is True


def test_default_run_override_is_none(monkeypatch):
    calls = []

    def observe(config, **options):
        calls.append(options)
        return {"state": "completed", "exit_code": 0}

    monkeypatch.setattr(cli, "run_scheduled", observe)
    assert CliRunner().invoke(cli.main, ["run"]).exit_code == 0
    assert calls == [{"dry_run": None}]
    assert CliRunner().invoke(cli.main, ["run", "--dry-run"]).exit_code == 0
    assert calls[-1] == {"dry_run": True}


def test_bad_schedule_does_not_call_install(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "install", lambda *args, **kwargs: calls.append(True))
    result = CliRunner().invoke(cli.main, ["install", "--at", "SENTINEL\nExecStart=bad"])
    assert result.exit_code == 2
    assert calls == []
    assert "SENTINEL" not in result.output
    assert json.loads(result.output)["error_code"] == "configuration_refused"


def test_status_paths_are_cli_not_implementation_policy(monkeypatch, tmp_path):
    paths = []

    def observe(path):
        paths.append(path)
        return {"state": "installed"}

    monkeypatch.setattr(cli, "status", observe)
    result = CliRunner().invoke(cli.main, ["status", "--config", str(tmp_path / "config.json")])
    assert result.exit_code == 0
    assert paths == [Path(tmp_path / "config.json")]
