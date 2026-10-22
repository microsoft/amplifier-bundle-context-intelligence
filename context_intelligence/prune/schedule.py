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
from datetime import datetime
from pathlib import Path
from typing import Any

from .scheduled import (
    JobBusy,
    ScheduleConfig,
    ScheduleError,
    admission,
    atomic_json,
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
    return checked_path(Path.home() / ".config/systemd/user")


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


def _owned(config_path: Path) -> dict[str, Any]:
    value = read_json(_manifest(config_path))
    if (
        value.get("format") != "context-intelligence-local-prune-installation"
        or value.get("version") != 1
    ):
        raise ScheduleError("unknown_installation_manifest")
    name = _name(value.get("name"))
    expected = {f"{name}.service", f"{name}.timer"}
    units = value.get("units")
    if not isinstance(units, dict) or set(units) != expected:
        raise ScheduleError("invalid_installation_units")
    for filename, digest in units.items():
        if _hash(_read_unit(_unit_dir() / filename)) != digest:
            raise ScheduleError("owned_unit_was_modified")
    return value


def install(
    config: ScheduleConfig,
    *,
    config_path: Path | None = None,
    name: str = DEFAULT_NAME,
    python: Path | None = None,
    enable: bool = False,
) -> dict[str, Any]:
    """Install/update only owned units. Never start the service immediately.

    Configuration and execution admission share the same lock. Timer updates are
    disabled before publication. Failures leave it disabled, with prior bytes
    restored when possible, rather than arming a partially installed job.
    """
    config_path = checked_path(config_path or default_config_path())
    name = _name(name)
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
            prior = _owned(config_path)
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
        old_config = read_json(config_path) if prior else None
        old_units = (
            {filename: _read_unit(_unit_dir() / filename) for filename in texts} if prior else {}
        )
        if prior:
            _systemctl("disable", "--now", f"{name}.timer")
        try:
            atomic_json(config_path, config.to_dict())
            for filename, text in texts.items():
                _write_unit(_unit_dir() / filename, text.encode())
            manifest = {
                "format": "context-intelligence-local-prune-installation",
                "version": 1,
                "name": name,
                "python": str(python),
                "config": str(config_path),
                "config_sha256": config.sha256,
                "units": {filename: _hash(text.encode()) for filename, text in texts.items()},
            }
            atomic_json(_manifest(config_path), manifest)
            _systemctl("daemon-reload")
            if enable:
                _systemctl("enable", "--now", f"{name}.timer")
        except (OSError, ScheduleError):
            # No auto-enable on failure. Preserve a truthful incomplete result.
            _systemctl("disable", "--now", f"{name}.timer", allow_failure=True)
            if prior and old_config is not None:
                atomic_json(config_path, old_config)
                for filename, raw in old_units.items():
                    _write_unit(_unit_dir() / filename, raw)
                atomic_json(_manifest(config_path), prior)
                _systemctl("daemon-reload", allow_failure=True)
            raise
    return status(config_path)


def set_enabled(config_path: Path, enabled: bool) -> dict[str, Any]:
    with admission(config_path):
        manifest = _owned(config_path)
        if enabled:
            config = ScheduleConfig.load(config_path)
            if config.sha256 != manifest["config_sha256"]:
                raise ScheduleError("configuration_differs_from_installed_units")
        _systemctl("enable" if enabled else "disable", "--now", manifest["name"] + ".timer")
    return status(config_path)


def uninstall(config_path: Path) -> dict[str, Any]:
    with admission(config_path):
        manifest = _owned(config_path)
        active = _systemctl("is-active", manifest["name"] + ".service", allow_failure=True)
        if active in ("active", "activating", "deactivating", "reloading"):
            raise JobBusy("service_active")
        _systemctl("disable", "--now", manifest["name"] + ".timer")
        for filename in manifest["units"]:
            (_unit_dir() / filename).unlink()
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
    age = None
    if report and report.get("finished_at"):
        try:
            age = max(
                0.0,
                (
                    datetime.now().astimezone() - datetime.fromisoformat(report["finished_at"])
                ).total_seconds(),
            )
        except (ValueError, TypeError):
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
        "last_run_age_seconds": age,
        "last_run_stale": age is None or age > 2 * 86400,
        "notice": "Local time; missed runs are not caught up. Only safely inactive supported roots qualify.",
    }
