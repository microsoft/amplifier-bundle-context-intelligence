"""Scheduler C/D/E regressions using owned files and a fake manager boundary.

The manager is not live-systemd acceptance. Policy, CLI, publication and locking
are real; only subprocess manager/runtime probes are replaced.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest
from click.testing import CliRunner

from context_intelligence import config as global_config
from context_intelligence.prune import schedule
from context_intelligence.prune.coordination import receipt_path
from context_intelligence.prune.scheduled import (
    ScheduleConfig,
    ScheduleError,
    admission,
    atomic_json,
    read_json,
    run_scheduled,
)
from test_prune_inventory import session
from test_prune_sourcecap import ledger

REAL_RUN = subprocess.run
REPO = Path(__file__).resolve().parents[1]


class Manager:
    """Only accept the scheduler's bounded external command vocabulary."""

    def __init__(self):
        self.enabled = False
        self.active = False
        self.calls: list[list[str]] = []
        self.observation: tuple[int, str] | None = None

    def run(self, argv, **kwargs):
        argv = list(map(str, argv))
        self.calls.append(argv)
        if argv[0] == "/usr/bin/loginctl":
            return subprocess.CompletedProcess(argv, 0, "no", "")
        if argv[0] != "/usr/bin/systemctl":
            assert argv == [
                sys.executable,
                "-m",
                "amplifier_module_tool_context_intelligence_prune.scheduled_cli",
                "--help",
            ]
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        assert argv[1] == "--user"
        args = argv[2:]
        rc, text = 0, ""
        if args == ["show", "--property=Version"]:
            text = "Version=255"
        elif args[0] == "is-active":
            rc, text = 3, "inactive"
        elif args[0] == "show" and args[1].endswith(".timer"):
            if args[-1] == "--property=UnitFileState,ActiveState" and self.observation:
                rc, text = self.observation
            else:
                text = (
                    f"UnitFileState={'enabled' if self.enabled else 'disabled'}\n"
                    f"ActiveState={'active' if self.active else 'inactive'}"
                )
        elif args[0] == "show" and args[1].endswith(".service"):
            text = "Result=success\nExecMainStatus=0\nActiveState=inactive"
        elif args[0] in ("enable", "disable"):
            self.enabled = args[0] == "enable"
            if "--now" in args:
                self.active = self.enabled
        elif args[0] in ("start", "stop"):
            self.active = args[0] == "start"
        else:
            assert args == ["daemon-reload"]
        return subprocess.CompletedProcess(argv, rc, text, "")


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "modules/tool-context-intelligence-prune"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setattr(global_config, "SETTINGS_PATH", tmp_path / ".amplifier/settings.yaml")
    standard = tmp_path / ".local/state/context-intelligence/recovery"
    monkeypatch.setattr(global_config, "DEFAULT_RECOVERY_STATE_ROOT", standard)
    # C imports this existing constant; also isolate its local binding once added.
    monkeypatch.setattr(schedule, "DEFAULT_RECOVERY_STATE_ROOT", standard, raising=False)
    root = tmp_path / "projects"
    root.mkdir()
    config = ScheduleConfig(root=root, state_root=tmp_path / "state")
    path = tmp_path / "prune/config.json"
    manager = Manager()
    monkeypatch.setattr(schedule.subprocess, "run", manager.run)
    return config, path, manager


def invoke_install(config, path, *extra):
    scheduled_cli = import_module("amplifier_module_tool_context_intelligence_prune.scheduled_cli")
    module_path = scheduled_cli.__file__
    assert module_path is not None
    assert Path(module_path).resolve() == (
        REPO
        / "modules/tool-context-intelligence-prune"
        / "amplifier_module_tool_context_intelligence_prune/scheduled_cli.py"
    )
    return CliRunner().invoke(
        scheduled_cli.main,
        [
            "install",
            "--config",
            str(path),
            "--root",
            str(config.root),
            "--state-root",
            str(config.state_root),
            *extra,
        ],
    )


def snapshot(path):
    return {
        item: (item.read_bytes(), item.stat().st_ino, item.stat().st_mtime_ns)
        for item in path.rglob("*")
        if item.is_file()
    }


@pytest.mark.parametrize(
    "observation",
    [
        (1, ""),
        (0, ""),
        (0, "UnitFileState=enabled"),
        (0, "ActiveState=active"),
        (0, "UnitFileState=PRIVATE\nActiveState=active"),
        (0, "UnitFileState=enabled\nActiveState=activating"),
        (0, "UnitFileState=enabled\nUnitFileState=disabled\nActiveState=active"),
        (0, "UnitFileState=enabled\nActiveState=active\nActiveState=inactive"),
    ],
)
@pytest.mark.parametrize("explicit", [(), ("--disable",)])
def test_update_unknown_timer_state_has_no_mutation(installation, observation, explicit):
    config, path, manager = installation
    first = invoke_install(config, path, "--enable")
    assert first.exit_code == 0, first.output
    before = snapshot(path.parent.parent)
    manager.calls.clear()
    manager.observation = observation
    result = invoke_install(config, path, "--at", "01:10", *explicit)
    assert manager.enabled and manager.active, result.output
    assert result.exit_code == 1, result.output
    assert json.loads(result.output) == {
        "state": "refused",
        "error_code": "schedule_management_refused",
    }
    assert snapshot(path.parent.parent) == before
    assert all(
        argv[2] in ("show", "is-active")
        for argv in manager.calls
        if argv[0] == "/usr/bin/systemctl"
    )


def test_first_install_disabled_update_preserves_and_uninstall_retains(installation):
    config, path, manager = installation
    assert invoke_install(config, path).exit_code == 0
    assert not manager.enabled and not manager.active
    assert not any(argv[-1] == "--property=UnitFileState,ActiveState" for argv in manager.calls)
    assert schedule.set_enabled(path, True)["state"] == "installed"
    assert invoke_install(config, path, "--at", "01:10").exit_code == 0
    assert manager.enabled and manager.active
    saved = path.read_bytes()
    inode = (path.parent / "admission.lock").stat().st_ino
    assert schedule.uninstall(path)["state"] == "uninstalled"
    assert path.read_bytes() == saved
    assert (path.parent / "admission.lock").stat().st_ino == inode
    assert invoke_install(config, path).exit_code == 0
    assert not manager.enabled and not manager.active


@pytest.mark.parametrize("phase", ["installing", "uninstalling"])
@pytest.mark.parametrize("saved_state", [(True, True), ("unknown", False)])
def test_incomplete_install_uses_known_saved_state_without_query(installation, phase, saved_state):
    config, path, manager = installation
    assert invoke_install(config, path, "--enable").exit_code == 0
    manifest = read_json(schedule._manifest(path))
    manifest.update(state=phase, timer_enabled=saved_state[0], timer_active=saved_state[1])
    atomic_json(schedule._manifest(path), manifest)
    for filename in manifest["units"]:
        (schedule._unit_dir() / filename).unlink()
    manager.observation = (1, "")
    manager.calls.clear()
    before = snapshot(path.parent.parent)
    result = invoke_install(config, path)
    assert not any(argv[-1] == "--property=UnitFileState,ActiveState" for argv in manager.calls)
    if saved_state == (True, True):
        assert result.exit_code == 0, result.output
        assert manager.enabled and manager.active
    else:
        assert result.exit_code == 1, result.output
        assert snapshot(path.parent.parent) == before


@pytest.mark.parametrize(
    "alias",
    [
        "last-run.json",
        "last-success.json",
        "installation.json",
        "admission.lock",
        "nested/../last-run.json",
        "service",
        "timer",
        "unit-parent",
    ],
)
def test_config_alias_refused_before_publication_or_manager(installation, monkeypatch, alias):
    config, path, manager = installation
    if alias in ("service", "timer"):
        suffix = "service" if alias == "service" else "timer"
        path = schedule._unit_dir() / f"{schedule.DEFAULT_NAME}.{suffix}"
    elif alias == "unit-parent":
        path = path.parent / "unit-container"
        monkeypatch.setattr(schedule, "_unit_dir", lambda: path / "systemd")
    else:
        path = path.parent / alias
    # Existing source and sentinel must survive even if validation refuses.
    source = config.root / "events.jsonl"
    source.write_text("SYNTHETIC SOURCE\n", encoding="utf-8")
    sentinel = config.root / "sentinel.json"
    atomic_json(sentinel, {"sentinel": "unchanged"})
    atomic_json(path, config.to_dict())
    before = snapshot(config.root.parent)
    result = invoke_install(config, path)
    assert manager.calls == [], result.output
    assert result.exit_code == 1, result.output
    assert snapshot(config.root.parent) == before


@pytest.mark.parametrize(
    "filename", ["last-run.json", "last-success.json", "installation.json", "admission.lock"]
)
def test_direct_run_and_admission_refuse_fixed_alias_without_overwrite(installation, filename):
    config, path, manager = installation
    path = path.parent / filename
    atomic_json(path, config.to_dict())
    before = snapshot(path.parent)
    with (
        pytest.raises(ScheduleError, match="^managed_configuration_path_overlap$"),
        admission(path),
    ):
        pytest.fail("aliased lock admitted")
    assert run_scheduled(path)["exit_code"] == 1
    assert snapshot(path.parent) == before
    assert manager.calls == []


@pytest.mark.parametrize("filename", ["config.json", 'custom %$" policy.json'])
def test_ordinary_policy_survives_two_real_cli_runs(installation, filename):
    config, path, manager = installation
    scheduled_cli = import_module("amplifier_module_tool_context_intelligence_prune.scheduled_cli")
    path = path.parent / filename
    result = invoke_install(config, path)
    assert result.exit_code == 0, result.output
    before = path.read_bytes()
    for _ in range(2):
        result = CliRunner().invoke(scheduled_cli.main, ["run", "--config", str(path)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["summary"]["payloads"] == 0
        assert path.read_bytes() == before
    assert (path.parent / "last-run.json").exists()
    assert (path.parent / "last-success.json").exists()


def child_run(path, home, authority):
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": os.pathsep.join(
            (str(REPO), str(REPO / "modules/tool-context-intelligence-prune"))
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    if authority is not None:
        env["PR143_TEST_AUTHORITY"] = str(authority)
    provenance = REAL_RUN(
        [
            sys.executable,
            "-B",
            "-c",
            "import context_intelligence.prune.schedule as s;"
            "import amplifier_module_tool_context_intelligence_prune.scheduled_cli as c;"
            "print(s.__file__);print(c.__file__)",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert provenance.returncode == 0, provenance.stderr
    assert [Path(line).resolve() for line in provenance.stdout.splitlines()] == [
        REPO / "context_intelligence/prune/schedule.py",
        REPO
        / "modules/tool-context-intelligence-prune"
        / "amplifier_module_tool_context_intelligence_prune/scheduled_cli.py",
    ]
    result = REAL_RUN(
        [
            sys.executable,
            "-B",
            "-m",
            "amplifier_module_tool_context_intelligence_prune.scheduled_cli",
            "run",
            "--config",
            str(path),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.stderr == ""
    assert "PR143_TEST_AUTHORITY" not in result.stdout
    assert str(home) not in result.stdout
    print(
        json.dumps(
            {
                "variable_present": authority is not None,
                "rc": result.returncode,
                "report": json.loads(result.stdout),
            },
            sort_keys=True,
        )
    )
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("portable_default", [False, True])
def test_installed_authority_is_mandatory_in_headless_cli(
    installation, monkeypatch, portable_default
):
    config, path, manager = installation
    home = config.root.parent
    capture = session(home, "scheduled-source")
    authority_parent = home / "authority"
    authority_parent.mkdir()
    db, conn = ledger(authority_parent, capture.path, "eligible")
    conn.close()
    authority = db.parent
    fallback = home / "fallback"
    fallback.mkdir()
    settings = global_config.SETTINGS_PATH
    settings.parent.mkdir()
    default = str(fallback) if portable_default else ""
    settings.write_text(
        "overrides:\n  hook-context-intelligence:\n    config:\n"
        f"      recovery_state_root: '${{PR143_TEST_AUTHORITY:{default}}}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PR143_TEST_AUTHORITY", str(authority))
    result = invoke_install(config, path)
    assert result.exit_code == 0, result.output
    policy_before = path.read_bytes()
    manifest_before = schedule._manifest(path).read_bytes()
    source_before = capture.path.read_bytes()
    ledger_before = db.read_bytes()
    present_rc, present = child_run(path, home, authority)
    assert present_rc == 0, present
    assert present["reasons"] == {"recovery_registered": 1}
    assert present["summary"]["payloads"] == 0
    assert capture.path.read_bytes() == source_before
    assert not receipt_path(capture.path).exists()
    missing_rc, missing = child_run(path, home, None)
    assert missing["summary"]["changed_files"] == 0, missing
    if portable_default:
        # Current authority resolves to valid empty B; only installed A protects
        # this source. Strict-parser refusal cannot make this regression pass.
        assert missing_rc == 0, missing
        assert missing["reasons"] == {"recovery_registered": 1}, missing
    else:
        assert missing_rc == 1, missing
        assert missing["reasons"] == {"recovery_unavailable": 1}, missing
    assert missing["summary"]["payloads"] == 0
    assert missing["summary"]["changed_files"] == 0
    assert capture.path.read_bytes() == source_before
    assert not receipt_path(capture.path).exists()
    assert db.read_bytes() == ledger_before
    assert path.read_bytes() == policy_before
    assert schedule._manifest(path).read_bytes() == manifest_before
    saved = ScheduleConfig.load(path)
    assert saved.recovery_roots == (authority.resolve(),)
    assert read_json(schedule._manifest(path))["config_sha256"] == saved.sha256


def test_install_canonical_authority_dedup_and_standard_optional(installation, monkeypatch):
    config, path, manager = installation
    # No settings: absent default must not become an explicitly required root.
    assert invoke_install(config, path).exit_code == 0
    assert ScheduleConfig.load(path).recovery_roots == ()
    standard = global_config.DEFAULT_RECOVERY_STATE_ROOT
    settings = global_config.SETTINGS_PATH
    settings.parent.mkdir()
    authority = config.root.parent / "custom-authority"
    settings.write_text(
        "overrides:\n  hook-context-intelligence:\n    config:\n"
        f"      recovery_state_root: '{authority}'\n",
        encoding="utf-8",
    )
    explicit = (standard, authority / ".." / authority.name, authority)
    schedule.install(replace(config, recovery_roots=explicit), config_path=path)
    assert ScheduleConfig.load(path).recovery_roots == (standard.resolve(), authority.resolve())
    assert not authority.exists()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("UnitFileState=enabled-runtime\nActiveState=failed", (True, False)),
        ("UnitFileState=disabled\nActiveState=active", (False, True)),
    ],
)
def test_timer_usable_states_do_not_collapse_enabledness_or_activity(installation, text, expected):
    config, path, manager = installation
    manager.observation = (0, text)
    assert schedule._timer_state(schedule.DEFAULT_NAME) == expected


@pytest.mark.parametrize("fault", ["timeout", "invocation"])
def test_timer_query_exception_preserves_installation(installation, monkeypatch, fault):
    config, path, manager = installation
    assert invoke_install(config, path, "--enable").exit_code == 0
    before = snapshot(path.parent.parent)
    run = manager.run
    manager.calls.clear()

    def failed(argv, **kwargs):
        if argv[-1] == "--property=UnitFileState,ActiveState":
            if fault == "timeout":
                raise subprocess.TimeoutExpired("PRIVATE", 30)
            raise OSError("PRIVATE")
        return run(argv, **kwargs)

    monkeypatch.setattr(schedule.subprocess, "run", failed)
    result = invoke_install(config, path)
    assert result.exit_code == 1, result.output
    assert "PRIVATE" not in result.output
    assert manager.enabled and manager.active
    assert snapshot(path.parent.parent) == before
    assert all(
        argv[2] in ("show", "is-active")
        for argv in manager.calls
        if argv[0] == "/usr/bin/systemctl"
    )


@pytest.mark.parametrize("root_value", ["${PR143_TEST_AUTHORITY:}", "relative", "["])
def test_install_unknown_authority_refuses_before_admission(installation, monkeypatch, root_value):
    config, path, manager = installation
    monkeypatch.delenv("PR143_TEST_AUTHORITY", raising=False)
    settings = global_config.SETTINGS_PATH
    settings.parent.mkdir()
    settings.write_text(
        "overrides:\n  hook-context-intelligence:\n    config:\n"
        f"      recovery_state_root: {root_value}\n",
        encoding="utf-8",
    )
    before = snapshot(config.root.parent)
    with pytest.raises(ScheduleError, match="^recovery_authority_unavailable$"):
        schedule.install(config, config_path=path)
    assert manager.calls == []
    assert not path.parent.exists()
    assert snapshot(config.root.parent) == before
