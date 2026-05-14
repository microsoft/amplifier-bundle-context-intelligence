#!/usr/bin/env bash
# Creates fake CI-format session directories from fixture JSONL files
# so the recipe's disk enumeration can find them.
#
# Usage: bash tests/dtu/setup_fixtures.sh [target_dir]
# Default target_dir: ~/.amplifier/projects
#
# Directory structure created:
#   {target_dir}/{WORKSPACE}/{session-id}/context-intelligence/events.jsonl
#   {target_dir}/{WORKSPACE}/{session-id}/metadata.json
set -euo pipefail

FIXTURE_DIR="$(cd "$(dirname "$0")/../fixtures" && pwd)"
TARGET_DIR="${1:-$HOME/.amplifier/projects}"
WORKSPACE="ci-test-workspace"

echo "Setting up CI test sessions in $TARGET_DIR/$WORKSPACE/"

# Associative array: session-id → fixture filename
declare -A SESSIONS=(
  ["clean-session-001"]="clean_session.jsonl"
  ["s1-session-001"]="s1_session.jsonl"
  ["s9a-session-001"]="s9a_session.jsonl"
  ["cancel-session-001"]="cancel_session.jsonl"
  ["s4a-session-001"]="s4a_session.jsonl"
  ["s4b-session-001"]="s4b_session.jsonl"
  ["s4c-session-001"]="s4c_session.jsonl"
  ["s4d-session-001"]="s4d_session.jsonl"
  ["s8-session-001"]="s8_session.jsonl"
  ["s9b-session-001"]="s9b_session.jsonl"
)

for session_id in "${!SESSIONS[@]}"; do
  fixture_file="${SESSIONS[$session_id]}"
  session_dir="$TARGET_DIR/$WORKSPACE/$session_id/context-intelligence"
  mkdir -p "$session_dir"

  # Copy fixture as events.jsonl
  cp "$FIXTURE_DIR/$fixture_file" "$session_dir/events.jsonl"

  # Write minimal metadata.json next to (not inside) context-intelligence/
  cat > "$TARGET_DIR/$WORKSPACE/$session_id/metadata.json" << METAEOF
{
  "session_id": "$session_id",
  "workspace": "$WORKSPACE",
  "status": "completed",
  "is_root": true,
  "started_at": "2026-05-01T10:00:00.000Z",
  "last_event_at": "2026-05-01T10:30:00.000Z"
}
METAEOF

  echo "  Created: $session_id  (from $fixture_file)"
done

# s5_stale fixture — has its own metadata.json (status=running, used by score_s5)
S5_SESSION="s5-stale-session-001"
S5_DIR="$TARGET_DIR/$WORKSPACE/$S5_SESSION/context-intelligence"
mkdir -p "$S5_DIR"
cp "$FIXTURE_DIR/metadata/s5_stale/events.jsonl" "$S5_DIR/events.jsonl"
cp "$FIXTURE_DIR/metadata/s5_stale/metadata.json" "$TARGET_DIR/$WORKSPACE/$S5_SESSION/metadata.json"
echo "  Created: $S5_SESSION  (from metadata/s5_stale/)"

echo ""
echo "Done. Created $(( ${#SESSIONS[@]} + 1 )) sessions in $TARGET_DIR/$WORKSPACE/"
echo "Verify with: find $TARGET_DIR/$WORKSPACE -name events.jsonl | wc -l"
