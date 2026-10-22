"""Thin Click adapter for library-owned local scheduling and execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from context_intelligence.prune.schedule import (
    DEFAULT_NAME,
    install,
    set_enabled,
    status,
    uninstall,
)
from context_intelligence.prune.scheduled import (
    ScheduleConfig,
    ScheduleError,
    default_config_path,
    run_scheduled,
)


def _emit(value: dict[str, Any]) -> None:
    click.echo(json.dumps(value, sort_keys=True))


def _call(function: Any, *args: Any, **kwargs: Any) -> None:
    try:
        _emit(function(*args, **kwargs))
    except (ScheduleError, OSError, ValueError):
        # Do not print exception messages/causes containing configuration values.
        _emit({"state": "refused", "error_code": "schedule_management_refused"})
        raise click.exceptions.Exit(1) from None


@click.group()
def main() -> None:
    """Configure an opt-in Linux user timer. Defaults: 02:00 local, older than 7d.

    Only supported roots idle for min-inactive qualify. Legacy, active and
    recovery-registered sessions remain untouched regardless of payload age.
    """


def _config_option(function: Any) -> Any:
    return click.option(
        "--config",
        type=click.Path(path_type=Path),
        default=default_config_path,
        show_default="user local-prune config",
    )(function)


@main.command("install")
@_config_option
@click.option(
    "--root", type=click.Path(path_type=Path), default=lambda: Path.home() / ".amplifier/projects"
)
@click.option("--state-root", type=click.Path(path_type=Path), default=None)
@click.option("--recovery-root", multiple=True, type=click.Path(path_type=Path))
@click.option("--at", default="02:00", show_default=True, help="Daily HH:MM in machine local time.")
@click.option("--older-than", default="7d", show_default=True)
@click.option(
    "--min-inactive",
    default="7d",
    show_default=True,
    help="Independent inactivity; at least 1d and min-idle.",
)
@click.option("--min-idle", default="1h", show_default=True)
@click.option("--max-runtime", default=3600, type=int, show_default=True)
@click.option(
    "--dry-run/--apply", default=False, help="Store a preview-only job instead of applying."
)
@click.option("--name", default=DEFAULT_NAME, show_default=True)
@click.option(
    "--enable/--disable",
    default=None,
    help="Set timer state; omitted preserves prior state (first install is disabled).",
)
def install_command(
    config: Path,
    root: Path,
    state_root: Path | None,
    recovery_root: tuple[Path, ...],
    at: str,
    older_than: str,
    min_inactive: str,
    min_idle: str,
    max_runtime: int,
    dry_run: bool,
    name: str,
    enable: bool | None,
) -> None:
    """Install or replace owned configuration (unspecified options reset to defaults)."""
    try:
        options: dict[str, Any] = dict(
            root=root.expanduser().absolute(),
            recovery_roots=tuple(p.expanduser().absolute() for p in recovery_root),
            at=at,
            older_than=older_than,
            min_inactive=min_inactive,
            min_idle=min_idle,
            max_runtime=max_runtime,
            dry_run=dry_run,
        )
        if state_root is not None:
            options["state_root"] = state_root.expanduser().absolute()
        parsed = ScheduleConfig(**options)
    except (OSError, ValueError, ScheduleError):
        _emit({"state": "refused", "error_code": "configuration_refused"})
        raise click.exceptions.Exit(2) from None
    _call(install, parsed, config_path=config, name=name, enable=enable)


@main.command("run")
@_config_option
@click.option(
    "--dry-run", is_flag=True, default=None, help="Preview without changing the stored config."
)
def run_command(config: Path, dry_run: bool | None) -> None:
    """Execute one bounded invocation; normally called by the installed service."""
    report = run_scheduled(config, dry_run=dry_run)
    _emit(report)
    raise click.exceptions.Exit(report["exit_code"])


@main.command("status")
@_config_option
def status_command(config: Path) -> None:
    """Show exact config, next timer trigger, OS exit result and last run."""
    _call(status, config)


@main.command("enable")
@_config_option
def enable_command(config: Path) -> None:
    _call(set_enabled, config, True)


@main.command("disable")
@_config_option
def disable_command(config: Path) -> None:
    """Disable future starts; never stop an in-progress pruning transaction."""
    _call(set_enabled, config, False)


@main.command("uninstall")
@_config_option
def uninstall_command(config: Path) -> None:
    """Remove only unchanged owned units; retain config and run evidence."""
    _call(uninstall, config)


if __name__ == "__main__":
    main()
