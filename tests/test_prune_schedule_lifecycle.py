"""Installer fault injection, not a substitute for actual systemd acceptance.

External manager/runtime probes are replaced. Real ownership manifests,
configuration files, unit publication, rollback and locking execute on fixtures.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from context_intelligence import config as global_config
from context_intelligence.prune import schedule
from context_intelligence.prune.scheduled import ScheduleConfig, ScheduleError, read_json


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(global_config, "SETTINGS_PATH", tmp_path / ".amplifier/settings.yaml")
    standard = tmp_path / ".local/state/context-intelligence/recovery"
    monkeypatch.setattr(global_config, "DEFAULT_RECOVERY_STATE_ROOT", standard)
    monkeypatch.setattr(schedule, "DEFAULT_RECOVERY_STATE_ROOT", standard, raising=False)

    # status() probes loginctl directly, outside the _systemctl seam below.
    # Keep every external probe inside this fixture; actual CLI imports are
    # independently exercised in the scheduled subprocess regressions.
    def probe(argv, **kwargs):
        if argv[0] == "/usr/bin/loginctl":
            assert argv[1:] == [
                "show-user",
                str(schedule.os.getuid()),
                "--property=Linger",
                "--value",
            ]
            return subprocess.CompletedProcess(argv, 0, "no", "")
        assert argv == [
            sys.executable,
            "-m",
            "amplifier_module_tool_context_intelligence_prune.scheduled_cli",
            "--help",
        ]
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(schedule.subprocess, "run", probe)
    config = ScheduleConfig(root=tmp_path / "projects", state_root=tmp_path / "state")
    path = tmp_path / "prune/config.json"
    state = {"enabled": False, "active": False, "calls": []}

    def observe(*args, **kwargs):
        state["calls"].append(args)
        if args[:2] == ("show", "--property=Version"):
            return "Version=255"
        if args[0] == "is-active":
            return "inactive"
        if args[0] == "show" and args[1].endswith(".timer"):
            return (
                "UnitFileState="
                + ("enabled" if state["enabled"] else "disabled")
                + "\nActiveState="
                + ("active" if state["active"] else "inactive")
            )
        if args[0] == "show":
            return "Result=success\nExecMainStatus=0\nActiveState=inactive"
        if args[0] == "enable":
            state["enabled"] = True
        if args[0] == "disable":
            state["enabled"] = False
        if args[0] == "start" or (args[0] == "enable" and "--now" in args):
            state["active"] = True
        if args[0] == "stop" or (args[0] == "disable" and "--now" in args):
            state["active"] = False
        return ""

    monkeypatch.setattr(schedule, "_systemctl", observe)
    return config, path, state


def test_first_install_second_write_failure_cleans_only_owned_outputs(installation, monkeypatch):
    config, path, state = installation
    write = schedule._write_unit
    calls = 0

    def failed(filename, raw):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second write failure")
        write(filename, raw)

    monkeypatch.setattr(schedule, "_write_unit", failed)
    with pytest.raises(OSError):
        schedule.install(config, config_path=path, python=Path(sys.executable), enable=True)
    assert not list(schedule._unit_dir().glob("*.service"))
    assert not list(schedule._unit_dir().glob("*.timer"))
    assert not schedule._manifest(path).exists()
    assert not path.exists()
    assert state["enabled"] is False and state["active"] is False
    monkeypatch.setattr(schedule, "_write_unit", write)
    assert schedule.install(config, config_path=path)["state"] == "installed"


def test_update_failure_restores_files_and_enabled_active_timer(installation, monkeypatch):
    config, path, state = installation
    schedule.install(config, config_path=path, enable=True)
    original = path.read_bytes()
    old_manifest = schedule._manifest(path).read_bytes()
    write = schedule._write_unit
    calls = 0

    def failed(filename, raw):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second write failure")
        write(filename, raw)

    monkeypatch.setattr(schedule, "_write_unit", failed)
    with pytest.raises(OSError):
        schedule.install(replace(config, at="01:30"), config_path=path)
    assert state["enabled"] is True and state["active"] is True
    assert path.read_bytes() == original
    assert schedule._manifest(path).read_bytes() == old_manifest
    schedule._owned(path)


def test_reconfigure_omitted_enable_preserves_state(installation):
    config, path, state = installation
    schedule.install(config, config_path=path, enable=True)
    result = schedule.install(replace(config, at="01:30"), config_path=path)
    assert result["timer"]["UnitFileState"] == "enabled"
    assert state["active"] is True


def test_hard_interruption_first_install_is_owned_and_repairable(installation, monkeypatch):
    config, path, state = installation
    write = schedule._write_unit
    calls = 0

    def killed(filename, raw):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        write(filename, raw)

    monkeypatch.setattr(schedule, "_write_unit", killed)
    with pytest.raises(KeyboardInterrupt):
        schedule.install(config, config_path=path, enable=True)
    manifest = schedule._owned(path, incomplete=True)
    assert manifest["state"] == "installing"
    with pytest.raises(ScheduleError):
        schedule._owned(path)
    monkeypatch.setattr(schedule, "_write_unit", write)
    assert schedule.install(config, config_path=path)["state"] == "installed"
    assert state["enabled"] is True and state["active"] is True


def test_partial_uninstall_can_retry_without_accepting_modified_unit(installation, monkeypatch):
    config, path, state = installation
    schedule.install(config, config_path=path, enable=True)
    unlink = Path.unlink
    failures = 0

    def failed(self, *args, **kwargs):
        nonlocal failures
        if self.name == schedule.DEFAULT_NAME + ".timer" and failures == 0:
            failures += 1
            raise OSError("injected unlink failure")
        return unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failed)
    with pytest.raises(OSError):
        schedule.uninstall(path)
    assert read_json(schedule._manifest(path))["state"] == "uninstalling"
    assert schedule.uninstall(path)["state"] == "uninstalled"
    assert not list(schedule._unit_dir().glob("context-intelligence-local-prune.*"))
    assert path.exists() and not schedule._manifest(path).exists()
    assert schedule.install(config, config_path=path)["state"] == "installed"


def test_modified_surviving_unit_is_not_removed(installation):
    config, path, state = installation
    schedule.install(config, config_path=path)
    unit = schedule._unit_dir() / (schedule.DEFAULT_NAME + ".timer")
    unit.write_text("FOREIGN MODIFICATION")
    with pytest.raises(ScheduleError, match="modified"):
        schedule.uninstall(path)
    assert unit.read_text() == "FOREIGN MODIFICATION"


def test_systemctl_timeout_is_sanitized(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE SENTINEL", 30)

    monkeypatch.setattr(schedule.subprocess, "run", timeout)
    with pytest.raises(ScheduleError) as observed:
        schedule._systemctl("daemon-reload")
    assert "PRIVATE" not in str(observed.value)


@pytest.mark.parametrize("boundary", ["after_disable", "after_restore"])
def test_timer_transition_crashes_leave_repairable_desired_state(
    installation, monkeypatch, boundary
):
    config, path, state = installation
    schedule.install(config, config_path=path, enable=True)
    systemctl = schedule._systemctl
    fired = False

    def killed(*args, **kwargs):
        nonlocal fired
        result = systemctl(*args, **kwargs)
        match = (
            (args[0] == "disable" and "--now" in args)
            if boundary == "after_disable"
            else args[0] == "start"
        )
        if match and not fired:
            fired = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(schedule, "_systemctl", killed)
    with pytest.raises(KeyboardInterrupt):
        schedule.install(replace(config, at="01:30"), config_path=path)
    assert read_json(schedule._manifest(path))["state"] == "installing"
    schedule._owned(path, incomplete=True)
    monkeypatch.setattr(schedule, "_systemctl", systemctl)
    assert schedule.install(config, config_path=path)["state"] == "installed"
    assert state["enabled"] is True and state["active"] is True


def test_failed_timer_restore_and_failed_rollback_remain_owned(installation, monkeypatch):
    config, path, state = installation
    schedule.install(config, config_path=path, enable=True)
    systemctl = schedule._systemctl
    write = schedule._write_unit
    restoring = False

    def fail_restore(*args, **kwargs):
        nonlocal restoring
        if args[0] == "start":
            restoring = True
            raise ScheduleError("injected_timer_restore_failure")
        return systemctl(*args, **kwargs)

    def fail_rollback(*args, **kwargs):
        if restoring:
            raise OSError("injected_rollback_write_failure")
        return write(*args, **kwargs)

    monkeypatch.setattr(schedule, "_systemctl", fail_restore)
    monkeypatch.setattr(schedule, "_write_unit", fail_rollback)
    with pytest.raises(ScheduleError, match="rollback_incomplete"):
        schedule.install(replace(config, at="01:30"), config_path=path)
    assert schedule._owned(path, incomplete=True)["state"] == "installing"
    monkeypatch.setattr(schedule, "_systemctl", systemctl)
    monkeypatch.setattr(schedule, "_write_unit", write)
    assert schedule.install(config, config_path=path)["state"] == "installed"
