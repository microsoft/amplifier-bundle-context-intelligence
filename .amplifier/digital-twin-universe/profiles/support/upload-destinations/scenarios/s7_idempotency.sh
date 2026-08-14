#!/usr/bin/env bash
# S7 -- IDEMPOTENCY. Re-running the same upload must skip, not duplicate.
#
# PASS CRITERIA (server-side, on server A):
#   * count_all after the second run EQUALS count_all after the first
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings single

context-intelligence-upload -y
poll_stable_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" >/dev/null
FIRST="$(count_all_at "$SRV_A_URL" "$SRV_A_TOKEN")"
[[ "$FIRST" -gt 0 ]] || _fatal 24 "s7: first run landed nothing; nothing to prove idempotent"

context-intelligence-upload -y
poll_stable_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" >/dev/null
SECOND="$(count_all_at "$SRV_A_URL" "$SRV_A_TOKEN")"

assert_no_growth "$FIRST" "$SECOND" "s7-re-run-idempotency"
echo "PASS S7: re-run skipped events instead of duplicating them (count_all=${SECOND})"
