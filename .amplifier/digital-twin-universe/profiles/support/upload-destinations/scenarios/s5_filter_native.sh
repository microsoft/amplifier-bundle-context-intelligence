#!/usr/bin/env bash
# S5 -- FILTER PARITY, CI-NATIVE LAYOUT.
# The single destination includes /home/amplifier/workspaces/keep-me/ and
# excludes **/skip-me/. Both CI-native fixture sessions are discoverable; only
# the KEEP one may land. The discriminator is each session's own recorded
# metadata["working_dir"].
#
# PASS CRITERIA (server-side, on server A):
#   * KEEP workspace present
#   * SKIP workspace ABSENT (0 nodes) -- the filter decided, not the path
#   * distinct workspace set on the server contains no unexpected member
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings single

context-intelligence-upload --format context-intelligence -y

poll_stable_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" >/dev/null
assert_ws_present "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" "s5-native-included"
assert_ws_absent  "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_SKIP" "s5-native-excluded"

ACTUAL_WS="$(workspaces_at "$SRV_A_URL" "$SRV_A_TOKEN")"
if [[ "$ACTUAL_WS" != "$WS_KEEP" ]]; then
    echo "--- workspaces on server A ---"; echo "$ACTUAL_WS"
    _fatal 22 "s5: server A holds workspaces other than '${WS_KEEP}'"
fi

echo "PASS S5: CI-native filtering matched capture-time include/exclude exactly"
