#!/usr/bin/env bash
#
# validate-full.sh — launch `validate-bundle-repo` with full-mode-capable private dependencies.
#
# WHY THIS EXISTS
# --------------
# The validator runs its Python checks through a bash `python3` heredoc. In a
# default Amplifier environment that `python3` is a minimal interpreter with no
# `pip` and no `amplifier_foundation` / `hatchling`, so the recipe self-downgrades
# to `validation_mode: structural_only` — it SKIPS the two checks that matter most
# for a behaviour split: BundleRegistry resolution of the layered includes, and the
# package build check.
#
# This script builds a private, throwaway uv venv with the validator's dependencies
# and the Amplifier CLI. It invokes that venv's CLI explicitly (rather than the host
# CLI), so both the CLI's fixed shebang and the recipe's `python3` resolve to the
# interpreter that can `import pip`, `amplifier_foundation`, and `hatchling`.
#
# This is a launch/dependency helper, not a verdict gate. It propagates the
# `amplifier tool invoke` exit status unchanged; a zero process exit is not a
# validation PASS. User/CI must inspect the recipe's published structured
# `env_check.validation_mode`, `quality_classification.quality_level`, and
# `build_check` fields. Full PASS requires full mode, a successful tested build,
# no ERROR findings, and a final report consistent with those machine results.
# Use Foundation validator v3.16.1+ for corrected mode/path detection. Result parsing
# and any remaining recipe/full-gate behavior are outside this interpreter repair.
# Diagram checks and generation remain enabled; optional LLM label enhancement
# is disabled so repeated validation does not rewrite labels nondeterministically.
#
# (This is the uv-based equivalent of the recipe's own documented
#  `uvx --with hatchling --with amplifier-foundation amplifier tool invoke ...`
#  one-liner; the venv form is used because the recipe shells out to `python3`,
#  so the deps must live on the PATH `python3`, not just in a uvx tool env.)
#
# USAGE
# -----
#   scripts/validate-full.sh [REPO_PATH]
#       REPO_PATH defaults to this bundle's repo root.
#
# ENV
#   CI_VALIDATE_VENV   set a new venv location. The path must not already exist.
#                      By default, a unique throwaway venv is created beneath
#                      TMPDIR (or /tmp) and removed on exit. Keep it outside the
#                      target repo so dependency skills are not scanned as source.
#   CI_VALIDATE_RECIPE explicit readable recipe file; otherwise exactly one
#                      cached Foundation validator must exist. Ambiguity fails
#                      before environment creation or dependency installation.
#
# Requires: uv and network access to install the pinned private CLI. When
# CI_VALIDATE_RECIPE is unset, exactly one Foundation validator must be present
# in ~/.amplifier/cache.
#
set -euo pipefail

REPO_PATH="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CLI_REF="4d168ed822314dced895c8cf7fdbb24233cbe31b"

# Select before installing: never guess between cached recipe revisions.
# The caller may choose a file explicitly without changing settings or caches.
if [[ -n "${CI_VALIDATE_RECIPE:-}" ]]; then
  RECIPE="$CI_VALIDATE_RECIPE"
else
  shopt -s nullglob
  recipes=("${HOME}/.amplifier/cache/"amplifier-foundation-*/recipes/validate-bundle-repo.yaml)
  shopt -u nullglob
  if [[ ${#recipes[@]} -eq 0 ]]; then
    echo "!! no cached Foundation validator; set CI_VALIDATE_RECIPE to its recipe file" >&2
    exit 1
  fi
  if [[ ${#recipes[@]} -ne 1 ]]; then
    echo "!! multiple cached Foundation validators; select one with CI_VALIDATE_RECIPE" >&2
    printf '   %s\n' "${recipes[@]}" >&2
    exit 1
  fi
  RECIPE="${recipes[0]}"
fi
if [[ ! -f "$RECIPE" || ! -r "$RECIPE" ]]; then
  echo "!! validation recipe is not a readable file: $RECIPE" >&2
  exit 1
fi

if [[ -n "${CI_VALIDATE_VENV:-}" ]]; then
  VENV="$CI_VALIDATE_VENV"
  if [[ -e "$VENV" || -L "$VENV" ]]; then
    echo "!! CI_VALIDATE_VENV already exists; refusing to modify it: $VENV" >&2
    exit 1
  fi
  VENV_OWNED=false
else
  VENV="$(mktemp -d "${TMPDIR:-/tmp}/ci-validate.XXXXXX")"
  VENV_OWNED=true
fi

if [[ "$VENV_OWNED" == true ]]; then
  trap 'rm -rf "$VENV"' EXIT
fi

echo ">> building deps venv: $VENV"
uv venv --python 3.11 --allow-existing "$VENV" >/dev/null
uv pip install --python "$VENV/bin/python" --quiet \
  pip hatchling pyyaml \
  "amplifier-app-cli @ git+https://github.com/microsoft/amplifier-app-cli@$CLI_REF"

if ! PYTHONNOUSERSITE=1 "$VENV/bin/python" -c 'import pip, hatchling, amplifier_foundation'; then
  echo "!! private validation Python is missing required imports" >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/amplifier" ]]; then
  echo "!! private validation venv did not install an executable amplifier CLI" >&2
  exit 1
fi

echo ">> recipe: $RECIPE"
echo ">> repo:   $REPO_PATH"
echo ">> launching validate-bundle-repo with full-mode-capable private dependencies ..."
echo ">> require full mode, a successful tested build, no ERROR findings, and a consistent final report; exit 0 is not PASS"
CONTEXT="$("$VENV/bin/python" -c 'import json, sys; print(json.dumps({"repo_path": sys.argv[1], "enhance_diagrams": "false"}))' "$REPO_PATH")"
PYTHONNOUSERSITE=1 \
PATH="$VENV/bin:$PATH" "$VENV/bin/amplifier" tool invoke recipes operation=execute \
  recipe_path="$RECIPE" \
  context="$CONTEXT"
