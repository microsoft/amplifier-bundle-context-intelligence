#!/usr/bin/env bash
# S4 -- MULTI-DESTINATION, NON-INTERACTIVE `--destination NAME`.
# Same two-destination config as S3 (`alpha` -> server A, `beta` -> server B,
# both include ["**"]), but scripted: no TTY, no prompt -- an explicit
# destination name plus -y selects exactly one destination up front.
#
# PASS CRITERIA (server-side):
#   * the named destination's server (A) grows
#   * the other, unnamed destination's server (B) does not grow
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings dual             # alpha -> server A, beta -> server B, both include ["**"]

A_BEFORE="$(count_all_at "$SRV_A_URL" "$SRV_A_TOKEN")"
B_BEFORE="$(count_all_at "$SRV_B_URL" "$SRV_B_TOKEN")"

context-intelligence-upload --destination alpha -y

poll_stable_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" >/dev/null
A_AFTER="$(count_all_at "$SRV_A_URL" "$SRV_A_TOKEN")"
B_AFTER="$(count_all_at "$SRV_B_URL" "$SRV_B_TOKEN")"

[[ "$A_AFTER" -gt "$A_BEFORE" ]] \
    || _fatal 19 "s4: --destination alpha (server A) received nothing (${A_BEFORE} -> ${A_AFTER})"
assert_no_growth "$B_BEFORE" "$B_AFTER" "s4-unnamed-server-B-untouched"

echo "PASS S4: --destination selected exactly one destination, non-interactively"
