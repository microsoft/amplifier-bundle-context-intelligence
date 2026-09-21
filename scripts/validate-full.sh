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
# The CLI sets AMPLIFIER_PYTHON for recipe shell steps. PATH alone cannot move
# those steps into another venv: the CLI itself must run from the prepared venv.
# This script invokes that venv's CLI explicitly, so both its fixed shebang and
# the recipe's `python3` resolve to private dependencies. It uses a public Core
# wheel, not a Rust source build, for this bundle's validation.
#
# This is a launch/dependency helper, not a verdict gate. It propagates the
# `amplifier tool invoke` exit status unchanged; a zero process exit is not a
# validation PASS. User/CI must inspect `env_check.validation_mode`,
# `build_check.build_tested`, `build_check.build_success`, and
# `quality_classification.quality_level`, plus `final_report`. Full PASS
# requires full mode, a successful tested build, no ERROR findings, and a report
# consistent with those machine results. The recipe has no structured
# `overall_verdict` field. Diagram checks and deterministic generation remain
# enabled; optional LLM label enhancement is disabled.
#
# USAGE
# -----
#   scripts/validate-full.sh [REPO_PATH]
#       REPO_PATH defaults to this bundle's repo root.
#
# ENV
#   CI_VALIDATE_VENV    optional NEW venv directory; never overwrite an existing one.
#                       The directory is removed on exit.
#   CI_VALIDATE_RECIPE  explicit readable recipe file; otherwise exactly one cached
#                       Foundation validator must exist. Ambiguity fails before
#                       environment creation or dependency installation.
#
# Requires: uv and a cached Foundation recipe (or CI_VALIDATE_RECIPE).
# Keep TMPDIR and CI_VALIDATE_VENV outside the target repository so installed
# dependency skills are not scanned as source.
#
set -euo pipefail

REPO_PATH="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CLI_REF="main"
FOUNDATION_REF="main"

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

# Normalize only after recipe selection, so cache-selection errors remain clear
# even when the target path does not exist.
REPO_PATH="$(cd "$REPO_PATH" && pwd)"

if [[ -n "${CI_VALIDATE_VENV:-}" ]]; then
  VENV="$CI_VALIDATE_VENV"
  # mkdir refuses existing paths and symlinks before uv can touch them.
  if ! mkdir -- "$VENV"; then
    echo "!! CI_VALIDATE_VENV already exists; refusing to modify it: $VENV" >&2
    exit 1
  fi
else
  VENV="$(mktemp -d "${TMPDIR:-/tmp}/ci-validate.XXXXXX")"
fi

# Every directory is newly claimed by this invocation and is removed on exit.
trap 'rm -rf -- "$VENV"' EXIT
export PYTHONNOUSERSITE=1

echo ">> building isolated validation runtime: $VENV"
uv venv --python 3.11 --allow-existing "$VENV" >/dev/null
# Refresh canonical Git main sources and the latest compatible published Core
# wheel for each new validation runtime. The CLI declares Foundation through
# tool.uv.sources; align it via an override rather than a conflicting direct URL.
printf '%s\n' \
  "amplifier-foundation @ git+https://github.com/microsoft/amplifier-foundation@$FOUNDATION_REF" \
  > "$VENV/overrides.txt"
uv pip install --python "$VENV/bin/python" --quiet --upgrade \
  --only-binary amplifier-core \
  --overrides "$VENV/overrides.txt" \
  pip hatchling pyyaml amplifier-core \
  "amplifier-app-cli @ git+https://github.com/microsoft/amplifier-app-cli@$CLI_REF"

if ! "$VENV/bin/python" -c 'import pip, hatchling, yaml, amplifier_core, amplifier_foundation'; then
  echo "!! private validation Python is missing required imports" >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/amplifier" ]]; then
  echo "!! private validation venv did not install an executable amplifier CLI" >&2
  exit 1
fi

# Emit only package versions and resolved Git revisions, never credentials or
# environment values. The temporary runtime is removed; retain this output with
# the validation results. Core uses the published wheel channel, not Git main.
"$VENV/bin/python" -c 'import importlib.metadata as metadata,json
packages={}
for name in ("amplifier-core", "amplifier-app-cli", "amplifier-foundation"):
    dist=metadata.distribution(name)
    direct=json.loads(dist.read_text("direct_url.json") or "{}")
    packages[name]={"version":dist.version,"vcs":direct.get("vcs_info")}
print("CI_VALIDATE_RUNTIME="+json.dumps({"core_channel":"latest-published-wheel","packages":packages},sort_keys=True))'

# JSON encoding preserves spaces, quotes, and backslashes in the target path.
CONTEXT="$("$VENV/bin/python" -c 'import json,sys; print(json.dumps({"repo_path": sys.argv[1], "enhance_diagrams": "false"}))' "$REPO_PATH")"

echo ">> recipe: $RECIPE"
echo ">> repo:   $REPO_PATH"
echo ">> launching validate-bundle-repo with full-mode-capable private dependencies ..."
echo ">> require full mode, a successful tested build, no ERROR findings, and a consistent final report; exit 0 is not PASS"
PATH="$VENV/bin:$PATH" "$VENV/bin/amplifier" tool invoke recipes operation=execute \
  recipe_path="$RECIPE" \
  context="$CONTEXT"
