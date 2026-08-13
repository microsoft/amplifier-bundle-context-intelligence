#!/usr/bin/env bash
# S1 -- ZERO-ARG GESTURE.
# Config: exactly ONE destination (single-dest settings.yaml, activated by the
# profile). No flags except -y (this shell is not a TTY, and the design
# mandates exit 2 for a non-TTY run without --auto-approve -- proven in S9).
#
# PASS CRITERIA (server-side, on server A):
#   * the KEEP workspace exists with > 0 nodes
#   * the SKIP workspace has 0 nodes (the single destination's exclude held)
#   * server B was never touched (0 nodes total)
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings single           # profile-provided helper: installs the 1-dest settings.yaml

context-intelligence-upload -y

poll_stable_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" >/dev/null
assert_ws_present "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" "s1-zero-arg-keep"
assert_ws_absent  "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_SKIP" "s1-zero-arg-skip-excluded"

B_TOTAL="$(count_all_at "$SRV_B_URL" "$SRV_B_TOKEN")"
[[ "$B_TOTAL" -eq 0 ]] || _fatal 14 "s1: server B received ${B_TOTAL} nodes but was never targeted"

echo "PASS S1: zero-arg gesture landed events on server A only, exclude honored"
