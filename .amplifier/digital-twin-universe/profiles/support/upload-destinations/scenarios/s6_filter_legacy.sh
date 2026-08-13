#!/usr/bin/env bash
# S6 -- FILTER PARITY, LEGACY /<id> LAYOUT.
# Same destination filters, but --format logging-hook, whose sessions live
# straight in the /<id> folder. Legacy discovery now surfaces the real
# working_dir into metadata, so the SAME discriminator applies; the unslug
# approximation is only a deep fallback and must not be needed here.
#
# PASS CRITERIA (server-side, on server A):
#   * KEEP workspace present (slug-identical to the native twin -- no fork)
#   * SKIP workspace ABSENT
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings single

context-intelligence-upload --format logging-hook -y

poll_stable_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" >/dev/null
assert_ws_present "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" "s6-legacy-included"
assert_ws_absent  "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_SKIP" "s6-legacy-excluded"

ACTUAL_WS="$(workspaces_at "$SRV_A_URL" "$SRV_A_TOKEN")"
if [[ "$ACTUAL_WS" != "$WS_KEEP" ]]; then
    echo "--- workspaces on server A ---"; echo "$ACTUAL_WS"
    _fatal 23 "s6: legacy import forked or leaked a workspace beyond '${WS_KEEP}'"
fi

echo "PASS S6: legacy /<id> layout filtered identically, no workspace fork"
