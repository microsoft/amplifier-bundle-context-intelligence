#!/usr/bin/env bash
# dest_assert.sh -- parameterized, TWO-SERVER assertion helpers for the
# upload-destinations e2e profile.
#
# Deliberately does NOT source ../upload-format/verify.sh: that harness is
# single-server (one CI_BASE + one CI_API_KEY, asserted at source time) and
# this matrix must assert against server A and server B in the same script.
# Same discipline is preserved here: container guard, numeric-sanity gate,
# poll-until-stable, fail loud with a distinct exit code per failure class.
#
# Every function takes <base> <token> explicitly -- there is no ambient
# "current server", so a scenario can never assert against the wrong one by
# forgetting to re-export a variable.
set -euo pipefail

_fatal() { local code="$1"; shift; echo "FATAL: $*" >&2; exit "$code"; }

guard_in_container() {
    if ! grep -q 'container=lxc' /proc/1/environ 2>/dev/null; then
        _fatal 3 "guard_in_container: not inside an Incus/LXC container -- refusing to touch any server"
    fi
}

_int() {
    local value; value="$(cat)"
    [[ "$value" =~ ^[0-9]+$ ]] || _fatal 4 "_int: expected a non-negative integer, got: '${value}'"
    echo "$value"
}

cy() {
    local base="$1" token="$2" query="$3" body
    body="$(jq -n --arg q "$query" '{query: $q}')"
    curl -sf -X POST "${base}/cypher" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        -d "$body"
}

count_all_at() {
    cy "$1" "$2" "MATCH (n) RETURN count(n) AS c" | jq -r '.results[0].c' | _int
}

count_ws_at() {
    cy "$1" "$2" "MATCH (n {workspace: '${3}'}) RETURN count(n) AS c" | jq -r '.results[0].c' | _int
}

workspaces_at() {
    cy "$1" "$2" "MATCH (n) WHERE n.workspace IS NOT NULL RETURN DISTINCT n.workspace AS w" \
        | jq -r '.results[].w' | sort
}

poll_stable_at() {
    local base="$1" token="$2" ws="$3" prev="" curr="" i
    for ((i = 1; i <= 30; i++)); do
        curr="$(count_ws_at "$base" "$token" "$ws")"
        if [[ "$curr" == "$prev" ]]; then echo "$curr"; return 0; fi
        prev="$curr"; sleep 2
    done
    _fatal 7 "poll_stable_at: workspace='${ws}' on ${base} did not stabilize in 60s; last=${curr}"
}

assert_fresh_at() {
    guard_in_container
    local total; total="$(count_all_at "$1" "$2")"
    [[ "$total" -eq 0 ]] || _fatal 5 "assert_fresh_at: expected count_all==0 on ${1}, got ${total} -- not a fresh backend"
    echo "isolation OK: ${1}"
}

assert_ws_present() {
    local c; c="$(count_ws_at "$1" "$2" "$3")"
    [[ "$c" -gt 0 ]] || _fatal 10 "assert_ws_present[$4]: workspace='${3}' has 0 nodes on ${1} (expected > 0)"
    echo "OK[$4]: workspace='${3}' present on ${1} (${c} nodes)"
}

assert_ws_absent() {
    local c; c="$(count_ws_at "$1" "$2" "$3")"
    [[ "$c" -eq 0 ]] || _fatal 11 "assert_ws_absent[$4]: workspace='${3}' has ${c} nodes on ${1} (expected 0)"
    echo "OK[$4]: workspace='${3}' absent from ${1}"
}

assert_no_growth() {
    [[ "$1" -eq "$2" ]] || _fatal 21 "assert_no_growth[$3]: count changed ${1} -> ${2} (expected no growth)"
    echo "OK[$3]: no growth (${1})"
}

assert_exit_code() {
    local expected="$1" ctx="$2"; shift 2
    local rc=0
    "$@" >/tmp/aec.out 2>/tmp/aec.err || rc=$?
    if [[ "$rc" -ne "$expected" ]]; then
        echo "--- stdout ---"; cat /tmp/aec.out; echo "--- stderr ---"; cat /tmp/aec.err
        _fatal 12 "assert_exit_code[$ctx]: expected ${expected}, got ${rc}"
    fi
    echo "OK[$ctx]: exit ${rc}"
}

assert_stderr_contains() {
    grep -qF -- "$1" /tmp/aec.err /tmp/aec.out \
        || { echo "--- stderr ---"; cat /tmp/aec.err; _fatal 13 "assert_stderr_contains[$2]: '${1}' not found"; }
    echo "OK[$2]: message contains '${1}'"
}
