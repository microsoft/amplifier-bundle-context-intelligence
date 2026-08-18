#!/usr/bin/env bash
# S5 -- FILTER PARITY, CI-NATIVE LAYOUT.
# The single destination includes /home/amplifier/workspaces/keep-me/ and
# excludes **/skip-me/. Both CI-native fixture sessions are discoverable; only
# the KEEP one may land. The discriminator is each session's own recorded
# metadata["working_dir"].
#
# Runs against the DEDICATED clean backend C (settings variant single_c). S5/S6
# make ABSOLUTE assertions -- "SKIP workspace absent, ONLY keep present" -- which
# are unsatisfiable on servers A/B: those receive WS_SKIP via the dual include
# "**" in S3/S4. C is written by S5/S6 alone, so absolute absence is provable
# and the scenario is order-independent (no longer sabotaged by S4).
#
# PASS CRITERIA (server-side, on server C):
#   * KEEP workspace present
#   * SKIP workspace ABSENT (0 nodes) -- the filter decided, not the path
#   * distinct workspace set on the server contains no unexpected member
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings single_c

context-intelligence-upload --format context-intelligence -y

poll_stable_at "$SRV_C_URL" "$SRV_C_TOKEN" "$WS_KEEP" >/dev/null
assert_ws_present "$SRV_C_URL" "$SRV_C_TOKEN" "$WS_KEEP" "s5-native-included"
assert_ws_absent  "$SRV_C_URL" "$SRV_C_TOKEN" "$WS_SKIP" "s5-native-excluded"

ACTUAL_WS="$(workspaces_at "$SRV_C_URL" "$SRV_C_TOKEN")"
if [[ "$ACTUAL_WS" != "$WS_KEEP" ]]; then
    echo "--- workspaces on server C ---"; echo "$ACTUAL_WS"
    _fatal 22 "s5: server C holds workspaces other than '${WS_KEEP}'"
fi

echo "PASS S5: CI-native filtering matched capture-time include/exclude exactly"
