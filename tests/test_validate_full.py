"""Regression tests for the isolated full-validator wrapper."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate-full.sh"
CLI_REF = "4d168ed822314dced895c8cf7fdbb24233cbe31b"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _create_fake_uv(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" >> "$FAKE_UV_ARGS"

if [[ "$1" == "venv" ]]; then
  venv="${!#}"
  mkdir -p "$venv/bin"
  cat > "$venv/bin/python" <<'PYTHON'
#!/usr/bin/env bash
if [[ "${1:-}" == */bin/amplifier ]]; then
  printf '%s\\n' "$1" > "$FAKE_CLI_PATH"
  shift
  printf 'private-python=%s\\n' "$0" > "$FAKE_CLI_PYTHON"
  printf '%s\\n' "$@" > "$FAKE_CLI_ARGS"
  printf '%s\\n' "$PATH" > "$FAKE_CLI_ENV_PATH"
  printf '%s\\n' "${AMPLIFIER_HOME-__UNSET__}" > "$FAKE_CLI_AMPLIFIER_HOME"
  printf '%s\\n' "$PYTHONNOUSERSITE" > "$FAKE_CLI_PYTHONNOUSERSITE"
  exit "${FAKE_PRIVATE_CLI_EXIT:-0}"
fi
if [[ "$*" == *"import pip, hatchling, amplifier_foundation"* ]]; then
  [[ "${FAKE_FAIL_PRIVATE_IMPORTS:-}" != "1" ]]
  exit
fi
if [[ "$*" == *"json.dumps"* ]]; then
  printf '%s\\n' "$2" > "$FAKE_JSON_CODE"
  printf '%s\\n' "$3" > "$FAKE_JSON_INPUT"
  printf '%s\\n' "$FAKE_JSON_CONTEXT"
  exit
fi
exit 64
PYTHON
  chmod +x "$venv/bin/python"
elif [[ "$1" == "pip" && "${FAKE_OMIT_PRIVATE_CLI:-}" != "1" ]]; then
  python=""
  for ((index = 1; index <= $#; index++)); do
    if [[ "${!index}" == "--python" ]]; then
      next=$((index + 1))
      python="${!next}"
      break
    fi
  done
  venv="$(dirname "$(dirname "$python")")"
  cat > "$venv/bin/amplifier" <<'AMPLIFIER'
#!/usr/bin/env bash
exec "VENV_PYTHON" "$0" "$@"
AMPLIFIER
  sed -i "s|VENV_PYTHON|$venv/bin/python|" "$venv/bin/amplifier"
  chmod +x "$venv/bin/amplifier"
fi
""",
    )


def _create_recipe(home: Path, revision: str = "test") -> Path:
    recipe = home / ".amplifier" / "cache" / f"amplifier-foundation-{revision}" / "recipes"
    recipe.mkdir(parents=True)
    recipe_path = recipe / "validate-bundle-repo.yaml"
    recipe_path.write_text("name: validate-bundle-repo\n")
    return recipe_path


def _environment(
    tmp_path: Path,
    *,
    omit_private_cli: bool = False,
    fail_private_imports: bool = False,
    caller_amplifier_home: str | None = None,
    private_cli_exit: int = 0,
) -> dict[str, str]:
    tool_bin = tmp_path / "tool bin"
    host_bin = tmp_path / "host bin"
    home = tmp_path / "home"
    tool_bin.mkdir()
    host_bin.mkdir()
    _create_fake_uv(tool_bin)
    _write_executable(
        host_bin / "amplifier",
        """#!/usr/bin/env bash
touch "$FAKE_HOST_CLI_SENTINEL"
exit 97
""",
    )
    _create_recipe(home)

    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{tool_bin}:{host_bin}:{environment['PATH']}",
            "FAKE_UV_ARGS": str(tmp_path / "uv-args"),
            "FAKE_CLI_PATH": str(tmp_path / "cli-path"),
            "FAKE_CLI_PYTHON": str(tmp_path / "cli-python"),
            "FAKE_CLI_ARGS": str(tmp_path / "cli-args"),
            "FAKE_CLI_ENV_PATH": str(tmp_path / "cli-env-path"),
            "FAKE_CLI_AMPLIFIER_HOME": str(tmp_path / "cli-amplifier-home"),
            "FAKE_CLI_PYTHONNOUSERSITE": str(tmp_path / "cli-pythonnousersite"),
            "FAKE_HOST_CLI_SENTINEL": str(tmp_path / "host-cli-called"),
            "FAKE_JSON_CODE": str(tmp_path / "json-code"),
            "FAKE_JSON_INPUT": str(tmp_path / "json-input"),
        }
    )
    environment.pop("AMPLIFIER_HOME", None)
    environment.pop("CI_VALIDATE_RECIPE", None)
    environment.pop("CI_VALIDATE_VENV", None)
    if caller_amplifier_home is not None:
        environment["AMPLIFIER_HOME"] = caller_amplifier_home
    if omit_private_cli:
        environment["FAKE_OMIT_PRIVATE_CLI"] = "1"
    if fail_private_imports:
        environment["FAKE_FAIL_PRIVATE_IMPORTS"] = "1"
    environment["FAKE_PRIVATE_CLI_EXIT"] = str(private_cli_exit)
    return environment


def test_launches_pinned_private_cli_and_preserves_paths_with_spaces(tmp_path: Path) -> None:
    """The wrapper launches private tools without relocating caller settings identity."""
    caller_amplifier_home = str(tmp_path / "caller amplifier home")
    environment = _environment(tmp_path, caller_amplifier_home=caller_amplifier_home)
    repo_path = tmp_path / 'bundle "quoted" \\ path'
    venv_path = tmp_path / "private venv with spaces"
    expected_context = {"repo_path": str(repo_path), "enhance_diagrams": "false"}
    environment["FAKE_JSON_CONTEXT"] = json.dumps(expected_context)

    result = subprocess.run(
        [str(SCRIPT_PATH), str(repo_path)],
        env={**environment, "CI_VALIDATE_VENV": str(venv_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert Path(environment["FAKE_CLI_PATH"]).read_text().strip() == str(
        venv_path / "bin" / "amplifier"
    )
    assert Path(environment["FAKE_CLI_PYTHON"]).read_text().strip() == (
        f"private-python={venv_path / 'bin' / 'python'}"
    )
    assert not Path(environment["FAKE_HOST_CLI_SENTINEL"]).exists()

    uv_args = Path(environment["FAKE_UV_ARGS"]).read_text().splitlines()
    assert "--allow-existing" in uv_args
    python_targets = [
        uv_args[index + 1] for index, argument in enumerate(uv_args[:-1]) if argument == "--python"
    ]
    assert str(venv_path / "bin" / "python") in python_targets
    assert "pip" in uv_args
    assert "hatchling" in uv_args
    # The CLI supplies Core/Foundation through its own dependency closure.
    # Repeating Foundation as a direct Git requirement conflicts with its
    # tool.uv.sources mapping during a real install.
    assert not any(arg.startswith("amplifier-foundation @") for arg in uv_args)
    assert not any(arg.startswith("amplifier-core @") for arg in uv_args)
    assert (
        f"amplifier-app-cli @ git+https://github.com/microsoft/amplifier-app-cli@{CLI_REF}"
        in uv_args
    )

    cli_args = Path(environment["FAKE_CLI_ARGS"]).read_text().splitlines()
    expected_recipe = (
        Path(environment["HOME"])
        / ".amplifier/cache/amplifier-foundation-test/recipes/validate-bundle-repo.yaml"
    )
    assert f"recipe_path={expected_recipe}" in cli_args
    context = next(argument for argument in cli_args if argument.startswith("context="))
    assert json.loads(context.removeprefix("context=")) == expected_context
    assert Path(environment["FAKE_JSON_INPUT"]).read_text().strip() == str(repo_path)
    # Exercise the wrapper's actual expression too: fake CLI output alone cannot
    # prove that diagram enhancement is disabled or quoted paths survive.
    json_result = subprocess.run(
        [sys.executable, "-c", Path(environment["FAKE_JSON_CODE"]).read_text(), str(repo_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(json_result.stdout) == expected_context
    assert Path(environment["FAKE_CLI_ENV_PATH"]).read_text().splitlines()[0] == (
        f"{venv_path}/bin:{environment['PATH']}"
    )
    assert Path(environment["FAKE_CLI_AMPLIFIER_HOME"]).read_text().strip() == caller_amplifier_home
    assert Path(environment["FAKE_CLI_PYTHONNOUSERSITE"]).read_text().strip() == "1"


def test_propagates_private_cli_failure_status(tmp_path: Path) -> None:
    """The helper reports the invoked CLI status rather than creating a validation verdict."""
    environment = _environment(tmp_path, private_cli_exit=7)
    venv_path = tmp_path / "private venv"
    environment["FAKE_JSON_CONTEXT"] = json.dumps({"repo_path": str(REPO_ROOT)})

    result = subprocess.run(
        [str(SCRIPT_PATH)],
        env={**environment, "CI_VALIDATE_VENV": str(venv_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 7
    assert Path(environment["FAKE_CLI_PATH"]).read_text().strip() == str(
        venv_path / "bin" / "amplifier"
    )


@pytest.mark.parametrize("cli_exit", [0, 7])
def test_default_venv_stays_outside_target_and_is_cleaned(tmp_path: Path, cli_exit: int) -> None:
    """Installed validator skills must never become default validation inputs."""
    environment = _environment(tmp_path, private_cli_exit=cli_exit)
    repo_path = tmp_path / "target repo"
    repo_path.mkdir()
    temp_root = tmp_path / "private temp"
    temp_root.mkdir()
    environment["TMPDIR"] = str(temp_root)
    environment["FAKE_JSON_CONTEXT"] = json.dumps(
        {"repo_path": str(repo_path), "enhance_diagrams": "false"}
    )

    result = subprocess.run(
        [str(SCRIPT_PATH), str(repo_path)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == cli_exit, result.stderr
    venv = Path(environment["FAKE_CLI_PATH"]).read_text().strip()
    venv_path = Path(venv).parent.parent
    assert venv_path.parent == temp_root
    assert venv_path.name.startswith("ci-validate.")
    assert not venv_path.exists()
    assert not (repo_path / ".amplifier").exists()
    assert not Path(environment["FAKE_HOST_CLI_SENTINEL"]).exists()


def test_preserves_an_unset_amplifier_home(tmp_path: Path) -> None:
    """The private tools venv must not fabricate an Amplifier settings home."""
    environment = _environment(tmp_path)
    venv_path = tmp_path / "private venv"
    environment["FAKE_JSON_CONTEXT"] = json.dumps({"repo_path": str(REPO_ROOT)})

    result = subprocess.run(
        [str(SCRIPT_PATH)],
        env={**environment, "CI_VALIDATE_VENV": str(venv_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert Path(environment["FAKE_CLI_AMPLIFIER_HOME"]).read_text().strip() == "__UNSET__"


def test_refuses_an_existing_override_without_clobbering_it(tmp_path: Path) -> None:
    """An explicitly supplied existing venv path is never recreated or altered."""
    environment = _environment(tmp_path)
    existing_venv = tmp_path / "existing venv"
    existing_venv.mkdir()
    user_file = existing_venv / "user-file"
    user_file.write_text("keep me")

    result = subprocess.run(
        [str(SCRIPT_PATH)],
        env={**environment, "CI_VALIDATE_VENV": str(existing_venv)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "already exists; refusing to modify it" in result.stderr
    assert user_file.read_text() == "keep me"
    assert not Path(environment["FAKE_UV_ARGS"]).exists()


def test_fails_on_missing_private_imports_without_running_host_cli(tmp_path: Path) -> None:
    """Missing private dependencies must stop before an accidental host-CLI fallback."""
    environment = _environment(tmp_path, fail_private_imports=True)
    venv_path = tmp_path / "private venv"

    result = subprocess.run(
        [str(SCRIPT_PATH)],
        env={**environment, "CI_VALIDATE_VENV": str(venv_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "private validation Python is missing required imports" in result.stderr
    assert not Path(environment["FAKE_HOST_CLI_SENTINEL"]).exists()


def test_fails_without_private_cli_instead_of_falling_back_to_host(tmp_path: Path) -> None:
    """A failed private CLI install must not run the host `amplifier` executable."""
    environment = _environment(tmp_path, omit_private_cli=True)
    venv_path = tmp_path / "private venv"

    result = subprocess.run(
        [str(SCRIPT_PATH)],
        env={**environment, "CI_VALIDATE_VENV": str(venv_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "did not install an executable amplifier CLI" in result.stderr
    assert not Path(environment["FAKE_HOST_CLI_SENTINEL"]).exists()


@pytest.mark.parametrize("recipe_count", [0, 2])
def test_requires_unambiguous_recipe_before_any_install(tmp_path: Path, recipe_count: int) -> None:
    environment = _environment(tmp_path)
    home = Path(environment["HOME"])
    if recipe_count == 0:
        (
            home / ".amplifier/cache/amplifier-foundation-test/recipes/validate-bundle-repo.yaml"
        ).unlink()
    else:
        _create_recipe(home, "another")
    repo_path = tmp_path / "target repo"

    result = subprocess.run(
        [str(SCRIPT_PATH), str(repo_path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "CI_VALIDATE_RECIPE" in result.stderr
    assert ("no cached" if recipe_count == 0 else "multiple cached") in result.stderr
    assert not repo_path.exists()
    assert not Path(environment["FAKE_UV_ARGS"]).exists()
    assert not Path(environment["FAKE_CLI_PATH"]).exists()
    assert not Path(environment["FAKE_HOST_CLI_SENTINEL"]).exists()


@pytest.mark.parametrize("cached_recipe_count", [0, 2])
def test_explicit_recipe_bypasses_cache_ambiguity(tmp_path: Path, cached_recipe_count: int) -> None:
    environment = _environment(tmp_path)
    home = Path(environment["HOME"])
    if cached_recipe_count == 0:
        (
            home / ".amplifier/cache/amplifier-foundation-test/recipes/validate-bundle-repo.yaml"
        ).unlink()
    else:
        _create_recipe(home, "another")
    recipe = tmp_path / "chosen recipe with spaces.yaml"
    recipe.write_text("name: explicit-validator\n")
    environment["CI_VALIDATE_RECIPE"] = str(recipe)
    environment["CI_VALIDATE_VENV"] = str(tmp_path / "private venv")
    environment["FAKE_JSON_CONTEXT"] = json.dumps({"repo_path": str(REPO_ROOT)})

    result = subprocess.run(
        [str(SCRIPT_PATH)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f">> recipe: {recipe}" in result.stdout
    cli_args = Path(environment["FAKE_CLI_ARGS"]).read_text().splitlines()
    assert f"recipe_path={recipe}" in cli_args
    assert not Path(environment["FAKE_HOST_CLI_SENTINEL"]).exists()


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_invalid_explicit_recipe_never_falls_back(tmp_path: Path, kind: str) -> None:
    environment = _environment(tmp_path)
    recipe = tmp_path / "invalid recipe.yaml"
    if kind == "directory":
        recipe.mkdir()
    environment["CI_VALIDATE_RECIPE"] = str(recipe)
    repo_path = tmp_path / "target repo"

    result = subprocess.run(
        [str(SCRIPT_PATH), str(repo_path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "not a readable file" in result.stderr
    assert not repo_path.exists()
    assert not Path(environment["FAKE_UV_ARGS"]).exists()
    assert not Path(environment["FAKE_CLI_PATH"]).exists()
