"""Opt-in Linux systemd user scheduling for the local retention library.

No system manager changes, daemon installs, automatic catch-up, or ingestion
restarts. A stable per-installation lock excludes configuration changes and runs.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import DEFAULT_RECOVERY_STATE_ROOT, strict_global_recovery_state_root
from .scheduled import (
    JobBusy,
    ScheduleConfig,
    ScheduleError,
    admission,
    atomic_json,
    checked_config_path,
    checked_path,
    default_config_path,
    read_json,
)

DEFAULT_NAME = "context-intelligence-local-prune"


def _systemctl(*args: str, allow_failure: bool = False) -> str:
    if sys.platform != "linux":
        raise ScheduleError("systemd_user_scheduling_requires_linux")
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScheduleError("systemd_user_manager_unavailable") from exc
    if result.returncode and not allow_failure:
        raise ScheduleError("systemd_user_command_failed")
    return result.stdout.strip()


def _name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"context-intelligence-local-prune(?:-[a-z0-9]+)*", value) is None
    ):
        raise ScheduleError("invalid_local_prune_unit_name")
    return value


def _argument(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ScheduleError("control_character_in_unit_argument")
    # systemd specifiers and environment expansion are separate from shell quoting.
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
        + '"'
    )


def unit_text(
    config: ScheduleConfig, config_path: Path, python: Path, name: str = DEFAULT_NAME
) -> dict[str, str]:
    name = _name(name)
    if not python.is_absolute() or not python.is_file():
        raise ScheduleError("installed_absolute_python_required")
    command = " ".join(
        _argument(arg)
        for arg in (
            str(python),
            "-m",
            "amplifier_module_tool_context_intelligence_prune.scheduled_cli",
            "run",
            "--config",
            str(checked_path(config_path)),
        )
    )
    service = (
        "[Unit]\nDescription=Local Context Intelligence raw request retention\n\n"
        "[Service]\nType=oneshot\nRestart=no\nUMask=0077\nNice=10\n"
        # HOME freezes defaults only. Resolved additional recovery authority is
        # saved in the installed policy; current settings must still resolve.
        f"Environment={_argument('HOME=' + str(Path.home())).replace('$$', '$')}\n"
        f"ExecStart={command}\nTimeoutStartSec={config.max_runtime + 120}\nTimeoutStopSec=120\n"
        "StandardOutput=journal\nStandardError=journal\n"
    )
    timer = (
        "[Unit]\nDescription=Nightly local raw request retention\n\n[Timer]\n"
        f"OnCalendar=*-*-* {config.at}:00\nUnit={name}.service\n"
        "Persistent=false\nRandomizedDelaySec=0\nAccuracySec=1s\n\n[Install]\nWantedBy=timers.target\n"
    )
    return {f"{name}.service": service, f"{name}.timer": timer}


def _unit_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        raise ScheduleError("XDG_CONFIG_HOME_requires_absolute_path")
    return checked_path(base / "systemd/user")


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_unit(path: Path) -> bytes:
    path = checked_path(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_size > 32768
    ):
        raise ScheduleError("unsafe_unit_file")
    return path.read_bytes()


def _write_unit(path: Path, raw: bytes) -> None:
    checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".local-prune-unit-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def _manifest(config_path: Path) -> Path:
    return config_path.parent / "installation.json"


def _owned(config_path: Path, *, incomplete: bool = False) -> dict[str, Any]:
    value = read_json(_manifest(config_path))
    if (
        value.get("format") != "context-intelligence-local-prune-installation"
        or value.get("version") != 1
    ):
        raise ScheduleError("unknown_installation_manifest")
    name = _name(value.get("name"))
    if value.get("config") != str(checked_path(config_path)):
        raise ScheduleError("installation_configuration_path_mismatch")
    expected = {f"{name}.service", f"{name}.timer"}
    units = value.get("units")
    if not isinstance(units, dict) or set(units) != expected:
        raise ScheduleError("invalid_installation_units")
    phase = value.get("state", "installed")
    if phase not in ("installed", "installing", "uninstalling"):
        raise ScheduleError("unknown_installation_state")
    pending = phase != "installed"
    if pending and not incomplete:
        raise ScheduleError("installation_incomplete")
    previous = value.get("previous_units", {})
    if not isinstance(previous, dict) or not set(previous) <= expected:
        raise ScheduleError("invalid_prior_unit_manifest")
    for filename, digest in units.items():
        path = _unit_dir() / filename
        if pending and incomplete and not os.path.lexists(path):
            continue
        accepted = (digest, previous.get(filename)) if pending and incomplete else (digest,)
        if _hash(_read_unit(path)) not in accepted:
            raise ScheduleError("owned_unit_was_modified")
    return value


def _timer_state(name: str) -> tuple[bool, bool]:
    text = _systemctl("show", name + ".timer", "--property=UnitFileState,ActiveState")
    state: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in state:
            raise ScheduleError("timer_state_unavailable")
        state[key] = value
    if (
        set(state) != {"UnitFileState", "ActiveState"}
        or state["UnitFileState"] not in ("enabled", "enabled-runtime", "disabled")
        or state["ActiveState"] not in ("active", "inactive", "failed")
    ):
        raise ScheduleError("timer_state_unavailable")
    return state["UnitFileState"] != "disabled", state["ActiveState"] == "active"


def _timer_restore(name: str, enabled: bool, active: bool) -> None:
    _systemctl("enable" if enabled else "disable", name + ".timer")
    _systemctl("start" if active else "stop", name + ".timer")


def install(
    config: ScheduleConfig,
    *,
    config_path: Path | None = None,
    name: str = DEFAULT_NAME,
    python: Path | None = None,
    enable: bool | None = None,
) -> dict[str, Any]:
    """Install/update only owned units. Never start the service immediately.

    Configuration and execution admission share the same lock. Timer updates are
    disabled before publication. Owned incomplete transactions can be repaired
    without accepting modified foreign files. A failed update restores the prior
    timer state only after all prior bytes and the manager reload are restored.
    """
    name = _name(name)
    config_path = checked_config_path(
        config_path or default_config_path(),
        unit_paths=tuple(_unit_dir() / f"{name}.{suffix}" for suffix in ("service", "timer")),
    )
    if enable is not None and type(enable) is not bool:
        raise ScheduleError("enable_requires_boolean_or_none")
    try:
        authority = strict_global_recovery_state_root()
    except ValueError:
        raise ScheduleError("recovery_authority_unavailable") from None
    authority = checked_path(authority).resolve(strict=False)
    standard = checked_path(DEFAULT_RECOVERY_STATE_ROOT).resolve(strict=False)
    roots = tuple(
        dict.fromkeys(checked_path(root).resolve(strict=False) for root in config.recovery_roots)
    )
    if authority != standard and authority not in roots:
        roots += (authority,)
    config = replace(config, recovery_roots=roots)
    python = (python or Path(sys.executable)).absolute()
    texts = unit_text(config, config_path, python, name)
    with admission(config_path):
        _systemctl("show", "--property=Version")  # Unit manager readiness, not global health.
        try:
            runtime = subprocess.run(
                [
                    str(python),
                    "-m",
                    "amplifier_module_tool_context_intelligence_prune.scheduled_cli",
                    "--help",
                ],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScheduleError("installed_runtime_unavailable") from exc
        if runtime.returncode:
            raise ScheduleError("installed_runtime_import_failed")
        prior: dict[str, Any] | None = None
        if _manifest(config_path).exists():
            prior = _owned(config_path, incomplete=True)
            if prior["name"] != name:
                raise ScheduleError("installation_name_change_refused")
        else:
            if os.path.lexists(config_path):
                # Uninstall deliberately retains a valid private configuration.
                # Explicit reinstall may reuse it, but never adopt foreign units.
                ScheduleConfig.load(config_path)
            for filename in texts:
                if os.path.lexists(_unit_dir() / filename):
                    raise ScheduleError("foreign_unit_exists")
        active = _systemctl("is-active", f"{name}.service", allow_failure=True)
        if active in ("active", "activating", "deactivating", "reloading"):
            raise JobBusy("service_active")
        old_config = read_json(config_path) if config_path.exists() else None
        old_units = {
            filename: _read_unit(_unit_dir() / filename)
            for filename in texts
            if prior and os.path.lexists(_unit_dir() / filename)
        }
        if prior is None:
            was_enabled, was_active = False, False
        elif prior.get("state", "installed") != "installed":
            if (
                type(prior.get("timer_enabled")) is not bool
                or type(prior.get("timer_active")) is not bool
            ):
                raise ScheduleError("timer_state_unavailable")
            was_enabled, was_active = prior["timer_enabled"], prior["timer_active"]
        else:
            was_enabled, was_active = _timer_state(name)
        enabled, active_timer = (was_enabled, was_active) if enable is None else (enable, enable)
        manifest = {
            "format": "context-intelligence-local-prune-installation",
            "version": 1,
            "state": "installing",
            "name": name,
            "python": str(python),
            "config": str(config_path),
            "config_sha256": config.sha256,
            "timer_enabled": enabled,
            "timer_active": active_timer,
            "units": {filename: _hash(text.encode()) for filename, text in texts.items()},
            "previous_units": {filename: _hash(raw) for filename, raw in old_units.items()},
        }
        try:
            # Durable ownership intent BEFORE changing timer state or units. A killed
            # process leaves recognized, resumable missing/old/new owned files.
            atomic_json(_manifest(config_path), manifest)
            if prior:
                _systemctl("disable", "--now", f"{name}.timer")
            atomic_json(config_path, config.to_dict())
            for filename, text in texts.items():
                _write_unit(_unit_dir() / filename, text.encode())
            _systemctl("daemon-reload")
            _timer_restore(name, enabled, active_timer)
            manifest["state"] = "installed"
            manifest["previous_units"] = {}
            atomic_json(_manifest(config_path), manifest)
        except (OSError, ScheduleError) as failure:
            try:
                # Retain both generations throughout rollback too. A second
                # failure must not turn our own restored units into foreign files.
                manifest.update(
                    state="installing",
                    timer_enabled=was_enabled,
                    timer_active=was_active,
                    previous_units={filename: _hash(raw) for filename, raw in old_units.items()},
                )
                atomic_json(_manifest(config_path), manifest)
                _systemctl("disable", "--now", f"{name}.timer", allow_failure=True)
                for filename, text in texts.items():
                    path = _unit_dir() / filename
                    if filename in old_units:
                        if os.path.lexists(path) and _hash(_read_unit(path)) not in (
                            _hash(old_units[filename]),
                            _hash(text.encode()),
                        ):
                            raise ScheduleError("rollback_refuses_modified_unit")
                        _write_unit(path, old_units[filename])
                    elif os.path.lexists(path):
                        if _hash(_read_unit(path)) != _hash(text.encode()):
                            raise ScheduleError("rollback_refuses_modified_unit")
                        path.unlink()
                if old_config is not None:
                    atomic_json(config_path, old_config)
                elif config_path.exists():
                    if ScheduleConfig.load(config_path).sha256 != config.sha256:
                        raise ScheduleError("rollback_refuses_modified_configuration")
                    config_path.unlink()
                _systemctl("daemon-reload")
                if prior and prior.get("state", "installed") == "installed":
                    _timer_restore(name, was_enabled, was_active)
                if prior:
                    atomic_json(_manifest(config_path), prior)
                elif _manifest(config_path).exists():
                    _manifest(config_path).unlink()
            except (OSError, ScheduleError) as rollback:
                raise ScheduleError("installation_rollback_incomplete") from rollback
            raise failure
    return status(config_path)


def set_enabled(config_path: Path, enabled: bool) -> dict[str, Any]:
    with admission(config_path):
        manifest = _owned(config_path)
        if enabled:
            config = ScheduleConfig.load(config_path)
            if config.sha256 != manifest["config_sha256"]:
                raise ScheduleError("configuration_differs_from_installed_units")
        _systemctl("enable" if enabled else "disable", "--now", manifest["name"] + ".timer")
        manifest.update(timer_enabled=enabled, timer_active=enabled)
        atomic_json(_manifest(config_path), manifest)
    return status(config_path)


def uninstall(config_path: Path) -> dict[str, Any]:
    with admission(config_path):
        manifest = _owned(config_path, incomplete=True)
        active = _systemctl("is-active", manifest["name"] + ".service", allow_failure=True)
        if active in ("active", "activating", "deactivating", "reloading"):
            raise JobBusy("service_active")
        _systemctl("disable", "--now", manifest["name"] + ".timer")
        manifest["state"] = "uninstalling"
        atomic_json(_manifest(config_path), manifest)
        for filename in manifest["units"]:
            (_unit_dir() / filename).unlink(missing_ok=True)
        _systemctl("daemon-reload")
        _manifest(config_path).unlink()
        # Keep config, last-run evidence and stable lock. No raw data removed here.
    return {"state": "uninstalled", "retained_configuration": str(config_path)}


def status(config_path: Path | None = None) -> dict[str, Any]:
    config_path = checked_path(config_path or default_config_path())
    config = ScheduleConfig.load(config_path)
    manifest = _owned(config_path)
    name = manifest["name"]
    timer = _systemctl(
        "show",
        name + ".timer",
        "--property=ActiveState,UnitFileState,NextElapseUSecRealtime,TimersCalendar",
    )
    service = _systemctl(
        "show", name + ".service", "--property=Result,ExecMainStatus,InvocationID,ActiveState"
    )
    last = config_path.parent / "last-run.json"
    report = read_json(last) if last.exists() else None
    success_path = config_path.parent / "last-success.json"
    success = read_json(success_path) if success_path.exists() else None
    age = None
    if success and success.get("finished_at"):
        try:
            age = max(
                0.0,
                (
                    datetime.now().astimezone() - datetime.fromisoformat(success["finished_at"])
                ).total_seconds(),
            )
        except (ValueError, TypeError):
            pass
    linger = "unknown"
    try:
        probe = subprocess.run(
            ["/usr/bin/loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip() in ("yes", "no"):
            linger = probe.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "state": "installed",
        "config": config.to_dict(),
        "config_sha256": config.sha256,
        "installation_matches_config": manifest["config_sha256"] == config.sha256,
        "python": manifest["python"],
        "name": name,
        "timer": dict(line.split("=", 1) for line in timer.splitlines() if "=" in line),
        "service": dict(line.split("=", 1) for line in service.splitlines() if "=" in line),
        "last_run": report,
        "last_successful_run": success,
        "last_successful_run_age_seconds": age,
        "last_successful_run_stale": age is None or age > 2 * 86400,
        "linger": linger,
        "notice": "Local time; missed runs are not caught up. Only safely inactive supported roots qualify.",
    }
