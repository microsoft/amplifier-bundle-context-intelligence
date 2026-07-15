#!/usr/bin/env bash
# dtu/verify.sh
# Promoted verbatim from the workspace dtu/verify.sh harness -- this copy is the
# single source of truth for the upload-format profile's assertions.
#
# Isolation + verification harness for Context Intelligence DTU testing.
#
# MUST be sourced INSIDE the DTU container, NEVER on the host. Every function
# that touches CI_BASE (localhost:8000 inside the container) is guarded, either
# directly or transitively, by guard_in_container so this script can never be
# accidentally pointed at a host production graph.
#
# This file exists so Tasks 6 and 8 do not each hand-roll their own divergent
# exec+curl+cypher incantations -- one committed source of truth for:
#   - numeric-sanity gating       (_int)
#   - fresh-backend isolation     (assert_fresh_backend)
#   - exact-count assertions      (assert_count)
#   - poll-until-stable counting  (poll_stable_ws)
#   - label-presence assertion    (assert_labels)
#
# Usage (inside the container):
#   source dtu/verify.sh
#   assert_fresh_backend
#   ... run the upload under test ...
#   poll_stable_ws my-workspace
#   assert_count my-workspace 42
#   assert_labels
#
# Required env:
#   CI_API_KEY   Bearer token for the /cypher and /status endpoints.
#                No default -- the script FATALs immediately if unset.
# Optional env:
#   CI_BASE      Base URL of the context-intelligence server.
#                Default: http://localhost:8000
#
# count_all / status_processed are the isolation discriminator this harness is
# built around: a fresh DTU backend is empty (count_all==0, events_processed==0);
# a host production graph is not. assert_fresh_backend proves that discriminator
# before anything else in this file is allowed to run.

set -euo pipefail

: "${CI_API_KEY:?FATAL: CI_API_KEY is required and must be set}"
CI_BASE="${CI_BASE:-http://localhost:8000}"

# ---------------------------------------------------------------------------
# _fatal <exit_code> <message...>
#
# Print message to stderr and exit with the given code. Internal helper --
# every FATAL exit code documented in this file's function comments funnels
# through here.
# ---------------------------------------------------------------------------
_fatal() {
    local code="$1"
    shift
    echo "FATAL: $*" >&2
    exit "$code"
}

# ---------------------------------------------------------------------------
# guard_in_container (A2)
#
# Refuses to let any function touch CI_BASE unless this process is actually
# running inside an Incus/LXC container. Detected via the container=lxc
# marker Incus/LXD writes into PID 1's environment. FATAL exit 3 otherwise --
# this is the guard that prevents this harness from ever being run against a
# host machine by accident.
# ---------------------------------------------------------------------------
guard_in_container() {
    if ! grep -q 'container=lxc' /proc/1/environ 2>/dev/null; then
        _fatal 3 "guard_in_container: not running inside an Incus/LXC container -- refusing to touch CI_BASE=${CI_BASE}"
    fi
}

# ---------------------------------------------------------------------------
# _cypher <query>
#
# POST a single Cypher query to /cypher with Bearer auth. The query string is
# jq-encoded into the JSON request body so it is safely escaped regardless of
# quoting/special characters. Prints the raw JSON response body.
# ---------------------------------------------------------------------------
_cypher() {
    local query="$1"
    local body
    body="$(jq -n --arg q "$query" '{query: $q}')"
    curl -sf -X POST "${CI_BASE}/cypher" \
        -H "Authorization: Bearer ${CI_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$body"
}

# ---------------------------------------------------------------------------
# _int (B2 -- numeric-sanity gate)
#
# Reads a single value from stdin and asserts it matches ^[0-9]+$. Echoes the
# validated integer on success; FATAL exit 4 otherwise. Every count read in
# this file is piped through _int so a malformed/non-numeric response (e.g. an
# error payload, null, or empty string) fails loud instead of silently
# comparing false-equal or false-unequal downstream.
# ---------------------------------------------------------------------------
_int() {
    local value
    value="$(cat)"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        _fatal 4 "_int: expected a non-negative integer, got: '${value}'"
    fi
    echo "$value"
}

# ---------------------------------------------------------------------------
# count_ws <workspace>
#
# Node count scoped to a single workspace.
# ---------------------------------------------------------------------------
count_ws() {
    local workspace="$1"
    _cypher "MATCH (n {workspace: '${workspace}'}) RETURN count(n) AS c" \
        | jq -r '.results[0].c' \
        | _int
}

# ---------------------------------------------------------------------------
# count_all
#
# Total node count across the entire backend graph (no workspace filter).
# ---------------------------------------------------------------------------
count_all() {
    _cypher "MATCH (n) RETURN count(n) AS c" \
        | jq -r '.results[0].c' \
        | _int
}

# ---------------------------------------------------------------------------
# status_processed
#
# Total events actually written to Neo4j, from GET /status.
#
# NOTE (discovered live against server_version 6.7.0): the server's /status
# response has NO top-level `events_processed` field -- that field only
# exists in the per-session dashboard shape (dashboard.py), not the top-level
# /status payload. The top-level payload instead carries a `metrics` object
# (registry.py) with `accepted_total` / `written_total` / etc. `written_total`
# is the correct analogue of "processed" for the isolation discriminator this
# harness needs: it counts events actually persisted to Neo4j, so a fresh
# backend reads 0 and a populated one reads > 0.
# ---------------------------------------------------------------------------
status_processed() {
    curl -sf "${CI_BASE}/status" -H "Authorization: Bearer ${CI_API_KEY}" \
        | jq -r '.metrics.written_total' \
        | _int
}

# ---------------------------------------------------------------------------
# assert_fresh_backend (A1 -- isolation discriminator)
#
# Proves this is a fresh, empty DTU backend and NOT a host production graph:
#   1. guard_in_container -- must actually be inside the container
#   2. count_all must be exactly 0
#   3. status_processed must be exactly 0
# Prints "isolation OK" on success; FATAL exit 5 on either count mismatch.
# ---------------------------------------------------------------------------
assert_fresh_backend() {
    guard_in_container

    local total
    total="$(count_all)"
    if [[ "$total" -ne 0 ]]; then
        _fatal 5 "assert_fresh_backend: expected count_all==0 (fresh backend), got ${total} -- this looks like a non-empty/production graph"
    fi

    local processed
    processed="$(status_processed)"
    if [[ "$processed" -ne 0 ]]; then
        _fatal 5 "assert_fresh_backend: expected status_processed==0 (fresh backend), got ${processed} -- this looks like a non-empty/production graph"
    fi

    echo "isolation OK"
}

# ---------------------------------------------------------------------------
# assert_count <workspace> <expected>
#
# Exact-count assertion (A1) scoped to a workspace. FATAL exit 6 on mismatch.
# ---------------------------------------------------------------------------
assert_count() {
    local workspace="$1"
    local expected="$2"
    local actual
    actual="$(count_ws "$workspace")"
    if [[ "$actual" -ne "$expected" ]]; then
        _fatal 6 "assert_count: workspace='${workspace}' expected ${expected}, got ${actual}"
    fi
}

# ---------------------------------------------------------------------------
# poll_stable_ws <workspace>
#
# Poll count_ws (B1) up to 30 iterations (60s wall-clock at 2s/iteration)
# until two consecutive reads agree, indicating the async ingestion pipeline
# has caught up and the count has stabilized. Echoes the stable count on
# success; FATAL exit 7 if it never stabilizes within the budget.
# ---------------------------------------------------------------------------
poll_stable_ws() {
    local workspace="$1"
    local prev=""
    local curr=""
    local i

    for ((i = 1; i <= 30; i++)); do
        curr="$(count_ws "$workspace")"
        if [[ "$curr" == "$prev" ]]; then
            echo "$curr"
            return 0
        fi
        prev="$curr"
        sleep 2
    done

    _fatal 7 "poll_stable_ws: workspace='${workspace}' did not stabilize after 30 iterations (60s); last count=${curr}"
}

# ---------------------------------------------------------------------------
# assert_labels
#
# Asserts at least one node with the :Event label exists in the graph. FATAL
# exit 8 otherwise.
# ---------------------------------------------------------------------------
assert_labels() {
    local has_events
    has_events="$(_cypher "MATCH (n:Event) RETURN count(n) AS c" | jq -r '(.results[0].c // 0) > 0')"
    if [[ "$has_events" != "true" ]]; then
        _fatal 8 "assert_labels: no :Event label found in the graph"
    fi
}

# ---------------------------------------------------------------------------
# event_data_field <workspace> <event_name> <field>
#
# Reads the DefaultHandler's full-data-blob property (n.data, a JSON string
# per context_intelligence_server/handlers/data_layer_1/default.py) off the
# first :Event node matching <workspace>/<event_name>, and returns the value
# of <field> within that decoded JSON blob. Used to verify the exact
# data.timestamp VALUE the legacy transform produced (TB-11), not merely
# that a timestamp is present.
# ---------------------------------------------------------------------------
event_data_field() {
    local workspace="$1"
    local event_name="$2"
    local field="$3"
    _cypher "MATCH (n:Event {workspace: '${workspace}'}) WHERE n.event_name = '${event_name}' RETURN n.data AS d LIMIT 1" \
        | jq -r '.results[0].d' \
        | jq -r --arg f "$field" '.[$f] // empty'
}

# ---------------------------------------------------------------------------
# assert_event_field <workspace> <event_name> <field> <expected>
#
# Exact-match assertion (TB-11) on a single field inside an :Event node's
# data blob. FATAL exit 9 on mismatch (including empty/missing).
# ---------------------------------------------------------------------------
assert_event_field() {
    local workspace="$1"
    local event_name="$2"
    local field="$3"
    local expected="$4"
    local actual
    actual="$(event_data_field "$workspace" "$event_name" "$field")"
    if [[ "$actual" != "$expected" ]]; then
        _fatal 9 "assert_event_field: workspace='${workspace}' event='${event_name}' field='${field}' expected '${expected}', got '${actual}'"
    fi
}
