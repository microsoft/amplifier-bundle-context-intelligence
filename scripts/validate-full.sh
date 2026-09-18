#!/usr/bin/env bash
#
# validate-full.sh — run `validate-bundle-repo` against THIS bundle in FULL mode.
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
# Use a public Core wheel, not a Rust source build, for this bundle's validation.
#
# USAGE
# -----
#   scripts/validate-full.sh [REPO_PATH]
#       REPO_PATH defaults to this bundle's repo root.
#
# ENV
#   CI_VALIDATE_VENV    optional NEW venv directory; never overwrite an existing one
#   CI_VALIDATE_RECIPE  explicit recipe path if more than one Foundation is cached
#
# Requires: uv and a cached Foundation recipe (or CI_VALIDATE_RECIPE).
# A fresh venv is removed on exit. The recipe's report, including failures and
# known false positives, is returned unchanged; exit 0 alone is not a PASS.
#
set -euo pipefail

REPO_PATH="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
REPO_PATH="$(cd "$REPO_PATH" && pwd)"

# Locate the foundation validate-bundle-repo recipe in the Amplifier cache.
# (The bare `amplifier tool invoke` CLI does not resolve the `foundation:` recipe
#  namespace, so we pass the cached recipe by absolute path.)
RECIPE="${CI_VALIDATE_RECIPE:-}"
if [[ -z "$RECIPE" ]]; then
  shopt -s nullglob
  recipes=("${HOME}/.amplifier/cache/"amplifier-foundation-*/recipes/validate-bundle-repo.yaml)
  if [[ ${#recipes[@]} -ne 1 ]]; then
    echo "!! Expected one cached validation recipe; found ${#recipes[@]}. Set CI_VALIDATE_RECIPE." >&2
    exit 1
  fi
  RECIPE="${recipes[0]}"
fi
if [[ ! -f "$RECIPE" || ! -r "$RECIPE" ]]; then
  echo "!! Validation recipe is not a readable file: $RECIPE" >&2
  exit 1
fi

if [[ -n "${CI_VALIDATE_VENV:-}" ]]; then
  VENV="$CI_VALIDATE_VENV"
  # mkdir refuses existing directories and symlinks before uv can touch them.
  mkdir -- "$VENV"
else
  VENV="$(mktemp -d "${TMPDIR:-/tmp}/ci-validate-venv.XXXXXXXX")"
fi
trap 'rm -rf -- "$VENV"' EXIT
export PYTHONNOUSERSITE=1

echo ">> building isolated validation runtime: $VENV"
uv venv --python 3.11 "$VENV" >/dev/null
uv pip install --python "$VENV/bin/python" --only-binary amplifier-core --quiet \
  pip hatchling pyyaml "amplifier-core==1.6.1" \
  "amplifier-foundation @ git+https://github.com/microsoft/amplifier-foundation@7ad00b359fd5c2ac3ee98436b1b3bccabe6e909d" \
  "amplifier-app-cli @ git+https://github.com/microsoft/amplifier-app-cli@14dc68eba05bf65b8c6dea28c3a2db93daa12d38"
"$VENV/bin/python" -c 'import pip, hatchling, yaml, amplifier_core, amplifier_foundation'
# JSON encoding preserves spaces, quotes, and backslashes in the target path.
CONTEXT="$("$VENV/bin/python" -c 'import json,sys; print(json.dumps({"repo_path": sys.argv[1], "enhance_diagrams": "false"}))' "$REPO_PATH")"

echo ">> recipe: $RECIPE"
echo ">> repo:   $REPO_PATH"
echo ">> running validate-bundle-repo in FULL mode ..."
PATH="$VENV/bin:$PATH" "$VENV/bin/amplifier" tool invoke recipes operation=execute \
  recipe_path="$RECIPE" \
  context="$CONTEXT"
