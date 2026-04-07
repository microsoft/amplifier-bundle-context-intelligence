#!/usr/bin/env bash
# test-skill-fetch.sh
# Verifies the complete SkillFetcher lifecycle end-to-end.
#
# Usage (from inside the DTU container):
#   cd /workspace && bash test-skill-fetch.sh

# Source env vars written during provisioning.
# Required when the script is exec'd via amplifier-digital-twin (no login shell).
# shellcheck disable=SC1091
source /etc/profile.d/ci-test.sh 2>/dev/null || true

set -uo pipefail

SKILL_NAME="context-intelligence-graph-query"
SERVER_URL="http://localhost:8000"
PASS=0
FAIL=0
WARN=0

ok()   { echo "  PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL + 1)); }
warn() { echo "  WARN  $1"; WARN=$((WARN + 1)); }
section() { echo ""; echo "── $1"; echo "   $(printf '%.0s─' $(seq 1 60))"; }

# ──────────────────────────────────────────────────────────────────────────────
section "Phase 1: Server skill endpoint (no session needed)"
# ──────────────────────────────────────────────────────────────────────────────

# Server version must be >= 2.0.0 for SkillFetcher to use live content.
VERSION=$(curl -sf "$SERVER_URL/version" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','0.0.0'))" \
  2>/dev/null || echo "0.0.0")
MAJOR=$(echo "$VERSION" | cut -d. -f1)
echo "  Server version: $VERSION"
if [ "$MAJOR" -ge 2 ]; then
  ok "Version >= 2.0.0 — SkillFetcher will fetch live skill content"
else
  fail "Version $VERSION < 2.0.0 — SkillFetcher falls back to bundled placeholder"
fi

# The /skills/* endpoints are auth-exempt by server design.
# This is the same GET the SkillFetcher issues at hook mount time.
SKILL_FROM_SERVER=$(curl -sf "$SERVER_URL/skills/$SKILL_NAME" 2>/dev/null || echo "")
SERVER_LEN=${#SKILL_FROM_SERVER}
echo "  /skills/$SKILL_NAME → $SERVER_LEN bytes"

if [ "$SERVER_LEN" -lt 50 ]; then
  fail "Server returned no/empty skill content"
  echo "       → check: curl -v $SERVER_URL/skills/$SKILL_NAME"
  echo "       → check: tail /var/log/ci-server.log"
else
  ok "Server endpoint returns skill content ($SERVER_LEN bytes)"
  echo "  First line: $(echo "$SKILL_FROM_SERVER" | head -1)"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Phase 2: SkillFetcher populates the bundle cache"
# ──────────────────────────────────────────────────────────────────────────────
# The SkillFetcher runs at hook MOUNT time — before the first LLM call.
# Starting any Amplifier session is enough to trigger it.

echo "  Starting Amplifier session to trigger hook mount + SkillFetcher..."
echo "  (session output follows, then verification resumes)"
echo ""
timeout 90 amplifier run "Reply with only the word: SKILL_TEST_OK" 2>&1 || true
echo ""
echo "  Session complete (exit code ignored — hook runs before LLM response)."

SKILL_FILE=$(find ~/.amplifier/cache -name "SKILL.md" \
  -path "*/$SKILL_NAME/*" 2>/dev/null | head -1)

if [ -z "$SKILL_FILE" ]; then
  fail "Skill file not found in bundle cache after session"
  echo "       Expected: ~/.amplifier/cache/.../skills/$SKILL_NAME/SKILL.md"
  echo "       → verify bundle is active: amplifier bundle current"
else
  echo "  Skill file: $SKILL_FILE"
  CACHED_CONTENT=$(cat "$SKILL_FILE")
  CACHED_LEN=${#CACHED_CONTENT}
  echo "  Cached content: $CACHED_LEN bytes"

  if echo "$CACHED_CONTENT" | grep -qi "unavailable\|not available\|placeholder"; then
    fail "Skill file still contains placeholder — SkillFetcher did not fetch from server"
    echo "  First 5 lines of cached file:"
    echo "$CACHED_CONTENT" | head -5 | sed 's/^/    /'
    echo "       → confirm env vars are set: echo \$AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL"
    echo "       → confirm API key is set:   echo \$AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY"
  else
    ok "Cached skill file populated (no placeholder text)"
  fi
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Phase 3: Cached content matches server response"
# ──────────────────────────────────────────────────────────────────────────────

if [ -n "${SKILL_FILE:-}" ] && [ "$SERVER_LEN" -ge 50 ]; then
  CACHED_CONTENT=$(cat "$SKILL_FILE")

  # ETag sidecar — written by SkillFetcher after a successful fetch.
  SKILL_DIR=$(dirname "$SKILL_FILE")
  ETAG_FILE=$(ls "$SKILL_DIR"/*.etag "$SKILL_DIR"/.etag 2>/dev/null | head -1 || echo "")
  if [ -n "$ETAG_FILE" ] && [ -f "$ETAG_FILE" ]; then
    ok "ETag sidecar present — SkillFetcher ran: $(cat "$ETAG_FILE")"
  else
    warn "ETag sidecar not found in $SKILL_DIR — may use different naming"
  fi

  # Content spot-check: first line of server response should appear in cache.
  FIRST_SERVER_LINE=$(echo "$SKILL_FROM_SERVER" | head -1)
  if echo "$CACHED_CONTENT" | grep -qF "$FIRST_SERVER_LINE" 2>/dev/null; then
    ok "Cached content matches server response (first line verified)"
  else
    warn "First line mismatch — cache may be from a previous server version"
    echo "       Server : $(echo "$SKILL_FROM_SERVER" | head -1)"
    echo "       Cache  : $(echo "$CACHED_CONTENT"    | head -1)"
  fi
else
  warn "Skipping content comparison (missing skill file or empty server response)"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Phase 4: skills:discovered event in session events.jsonl"
# ──────────────────────────────────────────────────────────────────────────────

LATEST_EVENTS=$(find /root/.amplifier/projects -name "events.jsonl" \
  -path "*/context-intelligence/*" 2>/dev/null \
  | sort | tail -1)

if [ -z "$LATEST_EVENTS" ]; then
  warn "No events.jsonl found — hook may not have captured events"
elif grep -q '"skills:discovered"' "$LATEST_EVENTS" 2>/dev/null || \
     grep -q '"skill_count"' "$LATEST_EVENTS" 2>/dev/null; then
  ok "skills:discovered event captured in events.jsonl"
  echo "  File: $LATEST_EVENTS"
  # Extract skill_count from the event for extra detail
  SKILL_COUNT=$(grep '"skill_count"' "$LATEST_EVENTS" | head -1 | \
    python3 -c "import sys,json; d=json.loads(sys.stdin.read().strip()); print(d.get('data',{}).get('skill_count','?'))" 2>/dev/null || echo "?")
  echo "  Skill count in event: $SKILL_COUNT"
else
  fail "skills:discovered event NOT found in events.jsonl — event was not triggered or not captured"
  echo "  File checked: $LATEST_EVENTS"
  echo "  → Is tool-skills configured in the bundle?"
  echo "  → Did the session use the context-intelligence bundle?"
fi

if [ -n "${SKILL_FILE:-}" ]; then
  SKILL_DIR=$(dirname "$SKILL_FILE")
  ETAG_FILE="$SKILL_DIR/.etag"
  HASH_FILE="$SKILL_DIR/.content_hash"
  if [ -f "$ETAG_FILE" ] && [ -f "$HASH_FILE" ]; then
    ok "ETag + content_hash sidecars written (fetcher.fetch() executed)"
    echo "  ETag:  $(cat $ETAG_FILE)"
    echo "  Hash:  $(head -c 16 $HASH_FILE)..."
  elif [ -f "$ETAG_FILE" ]; then
    warn "ETag present but no .content_hash sidecar"
  else
    fail "No ETag sidecar — fetcher.fetch() did not complete successfully"
  fi
fi

# ──────────────────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo " Results: $PASS passed  $FAIL failed  $WARN warnings"
echo "══════════════════════════════════════════════════════"
[ "$FAIL" -eq 0 ]
