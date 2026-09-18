"""Launcher wiring checks; real recipe validation remains the DTU gate."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate-full.sh"


@pytest.fixture
def launcher(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    recipe = home / ".amplifier/cache/amplifier-foundation-one/recipes/validate-bundle-repo.yaml"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("# test recipe")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "calls.jsonl"
    # Outbound spies exercise the actual shell, argv, and failure propagation.
    # They do not stand in for a successful full validation result.
    uv = tools / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json,os,pathlib,sys\n"
        "with open(os.environ['CALL_LOG'], 'a') as f:\n"
        " f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if sys.argv[1]=='venv':\n"
        " b=pathlib.Path(sys.argv[-1])/'bin'; b.mkdir()\n"
        " (b/'python').write_text('#!/bin/sh\\n"
        'if [ "$2" = "import pip, hatchling, yaml, amplifier_core, amplifier_foundation" ]; '
        f'then exit 0; fi\\nexec {sys.executable} "$@"\\n\')\n'
        " (b/'python').chmod(0o755)\n"
        " (b/'amplifier').write_text('#!/bin/sh\\n"
        f"exec {sys.executable} \"'+os.environ['CLI_SPY']+'\" \"$@\"\\n')\n"
        " (b/'amplifier').chmod(0o755)\n"
        "else:\n"
        " sys.exit(int(os.environ.get('INSTALL_STATUS','0')))\n"
    )
    uv.chmod(0o755)
    spy = tmp_path / "cli_spy.py"
    spy.write_text(
        "import json,os,sys\n"
        "with open(os.environ['CLI_RESULT'],'w') as f:\n"
        " json.dump({'args':sys.argv[1:], 'no_user_site':os.environ.get('PYTHONNOUSERSITE')},f)\n"
        "sys.exit(int(os.environ.get('RECIPE_STATUS','0')))\n"
    )
    ambient = tools / "amplifier"
    ambient.write_text("#!/bin/sh\nexit 98\n")
    ambient.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "PATH": f"{tools}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "CLI_SPY": str(spy),
        "CLI_RESULT": str(tmp_path / "cli-result.json"),
    }
    env.pop("CI_VALIDATE_VENV", None)
    env.pop("CI_VALIDATE_RECIPE", None)
    return tmp_path, recipe, env


def _run(launcher, **overrides):
    tmp_path, _, env = launcher
    repo = tmp_path / 'repo with "quotes"'
    repo.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(SCRIPT), str(repo)],
        env={**env, **overrides},
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("status", ["0", "7"])
def test_runs_venv_cli_and_preserves_status_and_json_paths(launcher, status):
    tmp_path, _, env = launcher
    result = _run(launcher, RECIPE_STATUS=status)
    assert result.returncode == int(status), result.stderr
    calls = [json.loads(line) for line in Path(env["CALL_LOG"]).read_text().splitlines()]
    assert "--only-binary" in calls[1]
    assert "amplifier-core==1.6.1" in calls[1]
    assert not any("amplifier-core@" in arg for arg in calls[1])
    response = json.loads(Path(env["CLI_RESULT"]).read_text())
    context = json.loads(next(arg[8:] for arg in response["args"] if arg.startswith("context=")))
    assert context["repo_path"] == str(tmp_path / 'repo with "quotes"')
    assert response["no_user_site"] == "1"
    assert not Path(calls[0][-1]).exists()


def test_refuses_existing_venv_without_touching_it(launcher):
    tmp_path, _, env = launcher
    existing = tmp_path / "already-exists"
    existing.mkdir()
    marker = existing / "keep"
    marker.write_text("untouched")
    assert _run(launcher, CI_VALIDATE_VENV=str(existing)).returncode != 0
    assert marker.read_text() == "untouched"
    assert not Path(env["CALL_LOG"]).exists()


def test_multiple_recipes_require_explicit_selection(launcher):
    _, recipe, env = launcher
    other = Path(env["HOME"]) / ".amplifier/cache/amplifier-foundation-two/recipes"
    other.mkdir(parents=True)
    (other / recipe.name).write_text("# other")
    assert _run(launcher).returncode != 0
    assert not Path(env["CALL_LOG"]).exists()
    assert _run(launcher, CI_VALIDATE_RECIPE=str(recipe)).returncode == 0


def test_missing_recipe_fails_before_install(launcher):
    _, recipe, env = launcher
    recipe.unlink()
    assert _run(launcher, CI_VALIDATE_RECIPE=str(recipe)).returncode != 0
    assert not Path(env["CALL_LOG"]).exists()


def test_install_failure_cleans_up_and_does_not_invoke_cli(launcher):
    _, _, env = launcher
    assert _run(launcher, INSTALL_STATUS="9").returncode == 9
    calls = [json.loads(line) for line in Path(env["CALL_LOG"]).read_text().splitlines()]
    assert not Path(calls[0][-1]).exists()
    assert not Path(env["CLI_RESULT"]).exists()
