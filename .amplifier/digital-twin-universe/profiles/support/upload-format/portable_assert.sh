#!/usr/bin/env bash
# portable_assert.sh — runtime-derived, self-referential assertions for the
# upload-format profile. Sources the proven verify.sh and adds ONLY helpers
# that COMPUTE their expected values at runtime from the SHIPPED fixture.
# NOTHING in here hardcodes a node count, slug, timestamp, or session id.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_HERE}/verify.sh"

: "${TOOL_PY:?FATAL: TOOL_PY must point at the python interpreter of the installed tool (set in ci.env)}"

# runtime_slug <working_dir> — the canonical slug, computed live by the
# tool's OWN function (never re-implemented, never a literal).
runtime_slug() {
    "$TOOL_PY" - "$1" <<'PY'
import sys
from amplifier_module_tool_context_intelligence_upload.legacy_transform import derive_workspace
print(derive_workspace(sys.argv[1]))
PY
}

# count_label <Label> — node count for a single graph label (e.g. Session).
count_label() {
    _cypher "MATCH (n:${1}) RETURN count(n) AS c" | jq -r '.results[0].c' | _int
}

# distinct_workspaces — sorted, newline-separated list of every distinct
# non-null n.workspace present in the graph.
distinct_workspaces() {
    _cypher "MATCH (n) WHERE n.workspace IS NOT NULL RETURN DISTINCT n.workspace AS w" \
        | jq -r '.results[].w' | sort
}

# assert_workspace_set <expected_sorted_newline_list> — FATAL 20 unless the
# graph's distinct workspaces EXACTLY equal the expected set. This is the
# no-fork proof: an escaped-hyphen fork would be an unexpected member.
assert_workspace_set() {
    local expected="$1" actual
    actual="$(distinct_workspaces)"
    if [[ "$actual" != "$expected" ]]; then
        _fatal 20 "assert_workspace_set: graph workspaces do not match expected set
--- expected ---
${expected}
--- actual ---
${actual}"
    fi
}

# assert_no_growth <before> <after> <context> — FATAL 21 unless before==after.
assert_no_growth() {
    if [[ "$1" -ne "$2" ]]; then
        _fatal 21 "assert_no_growth[$3]: count changed ${1} -> ${2} (expected no growth)"
    fi
}
