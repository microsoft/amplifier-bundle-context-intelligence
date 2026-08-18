#!/usr/bin/env bash
# S3 -- MULTI-DESTINATION, INTERACTIVE PICK (scripted stdin + a real PTY).
# Two destinations are configured: `alpha` (server A) and `beta` (server B),
# both include ["**"]. The selection prompt and the Proceed? prompt only appear
# on a TTY, so this runs under `script` to get one, and feeds "2\ny\n":
#   "2" -> pick the SECOND listed destination
#   "y" -> confirm the preview
#
# PASS CRITERIA (server-side):
#   * the workspace lands on the server belonging to the destination that was
#     PICKED, and the count on the OTHER server does not grow.
# The picked name is read back out of the captured transcript, so this asserts
# against what the tool actually offered rather than an assumed ordering.
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings dual             # alpha -> server A, beta -> server B, both include ["**"]

A_BEFORE="$(count_all_at "$SRV_A_URL" "$SRV_A_TOKEN")"
B_BEFORE="$(count_all_at "$SRV_B_URL" "$SRV_B_TOKEN")"

printf '2\ny\n' | script -qec "context-intelligence-upload" /dev/null > /tmp/s3.out 2>&1 || {
    cat /tmp/s3.out; _fatal 18 "s3: interactive run failed"
}
echo "--- transcript ---"; cat /tmp/s3.out

grep -qE '(^|[^a-z])2[).:] *beta' /tmp/s3.out \
    || _fatal 18 "s3: entry 2 in the destination prompt was not 'beta' -- transcript above"

poll_stable_at "$SRV_B_URL" "$SRV_B_TOKEN" "$WS_KEEP" >/dev/null
B_AFTER="$(count_all_at "$SRV_B_URL" "$SRV_B_TOKEN")"
A_AFTER="$(count_all_at "$SRV_A_URL" "$SRV_A_TOKEN")"

[[ "$B_AFTER" -gt "$B_BEFORE" ]] \
    || _fatal 18 "s3: picked destination 'beta' (server B) received nothing (${B_BEFORE} -> ${B_AFTER})"
assert_no_growth "$A_BEFORE" "$A_AFTER" "s3-unpicked-server-A-untouched"

echo "PASS S3: interactive pick routed to the chosen destination only"
