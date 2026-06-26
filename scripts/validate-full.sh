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
# This script builds a throwaway uv venv that HAS those deps, puts its `bin` first
# on PATH, and runs the recipe — so the recipe's `python3` resolves to an
# interpreter that can `import amplifier_foundation` and `import hatchling`, which
# flips the run to `validation_mode: full`.
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
#   CI_VALIDATE_VENV   override the venv location (default: $TMPDIR/ci-validate-venv)
#
# Requires: uv, and the `amplifier` CLI on PATH, with the amplifier-foundation
# bundle present in ~/.amplifier/cache (it ships the recipe).
#
set -euo pipefail

REPO_PATH="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="${CI_VALIDATE_VENV:-${TMPDIR:-/tmp}/ci-validate-venv}"

echo ">> building deps venv: $VENV"
uv venv --python 3.11 "$VENV" >/dev/null
uv pip install --python "$VENV/bin/python" --quiet \
  hatchling pyyaml \
  "amplifier-core @ git+https://github.com/microsoft/amplifier-core@main" \
  "amplifier-foundation @ git+https://github.com/microsoft/amplifier-foundation@main"

# Locate the foundation validate-bundle-repo recipe in the Amplifier cache.
# (The bare `amplifier tool invoke` CLI does not resolve the `foundation:` recipe
#  namespace, so we pass the cached recipe by absolute path.)
RECIPE="$(ls -1 "${HOME}/.amplifier/cache/"amplifier-foundation-*/recipes/validate-bundle-repo.yaml 2>/dev/null | head -1 || true)"
if [[ -z "$RECIPE" ]]; then
  echo "!! validate-bundle-repo.yaml not found under ~/.amplifier/cache/amplifier-foundation-*/recipes/" >&2
  echo "   Ensure the amplifier-foundation bundle is installed/cached, then retry." >&2
  exit 1
fi

echo ">> recipe: $RECIPE"
echo ">> repo:   $REPO_PATH"
echo ">> running validate-bundle-repo in FULL mode ..."
PATH="$VENV/bin:$PATH" amplifier tool invoke recipes operation=execute \
  recipe_path="$RECIPE" \
  context="{\"repo_path\":\"$REPO_PATH\"}"
