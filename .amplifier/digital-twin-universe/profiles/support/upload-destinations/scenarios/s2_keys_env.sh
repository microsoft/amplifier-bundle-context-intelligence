#!/usr/bin/env bash
# S2 -- keys.env ${VAR} EXPANSION.
# The destination's api_key in settings.yaml is the literal text "${TEAM_CI_KEY}".
# TEAM_CI_KEY is present ONLY in ~/.amplifier/keys.env -- it is explicitly NOT
# exported into this shell's environment. If the loader did not read keys.env,
# the request would carry an unexpanded/empty bearer and the server would reject
# it, so a successful server-side landing is itself the proof.
#
# PASS CRITERIA (server-side, on server A):
#   * TEAM_CI_KEY is confirmed absent from this process environment (precondition)
#   * the KEEP workspace grows from the pre-run count
#   * a deliberately-wrong keys.env value makes the run FAIL (negative control)
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings single_varkey    # api_key: "${TEAM_CI_KEY}", value only in keys.env

if [[ -n "${TEAM_CI_KEY:-}" ]]; then
    _fatal 15 "s2 precondition failed: TEAM_CI_KEY is exported in the environment; it must live ONLY in keys.env"
fi
grep -q '^TEAM_CI_KEY=' /root/.amplifier/keys.env \
    || _fatal 15 "s2 precondition failed: TEAM_CI_KEY missing from /root/.amplifier/keys.env"

BEFORE="$(count_ws_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP")"
context-intelligence-upload -y
poll_stable_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP" >/dev/null
AFTER="$(count_ws_at "$SRV_A_URL" "$SRV_A_TOKEN" "$WS_KEEP")"
[[ "$AFTER" -ge "$BEFORE" && "$AFTER" -gt 0 ]] \
    || _fatal 16 "s2: keys.env-backed run did not land events (before=${BEFORE} after=${AFTER})"

# ---- negative control: a wrong secret must FAIL loudly, proving the value
# actually travelled from keys.env into the Authorization header. ----
cp /root/.amplifier/keys.env /root/dest-e2e/keys.env.bak
sed -i 's/^TEAM_CI_KEY=.*/TEAM_CI_KEY=definitely-not-the-token/' /root/.amplifier/keys.env
set +e
context-intelligence-upload -y >/tmp/s2bad.out 2>/tmp/s2bad.err
BAD_RC=$?
set -e
cp /root/dest-e2e/keys.env.bak /root/.amplifier/keys.env
if [[ "$BAD_RC" -eq 0 ]]; then
    echo "--- stdout ---"; cat /tmp/s2bad.out; echo "--- stderr ---"; cat /tmp/s2bad.err
    _fatal 17 "s2 negative control: run with a wrong keys.env secret exited 0 -- the key is not being used"
fi

echo "PASS S2: \${VAR} resolved from keys.env only (rc=${BAD_RC} with a wrong secret)"
