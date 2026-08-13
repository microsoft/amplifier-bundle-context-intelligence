#!/usr/bin/env bash
# S9 -- FAILURE MODES. Every one must fail LOUDLY and never hang.
#   (a) 0 destinations configured + no connection args          -> exit 2
#   (b) non-interactive, 2+ destinations, no --destination      -> exit 2, names listed
#   (c) non-interactive, no --auto-approve (preview unconfirmable) -> exit 2
#   (d) --destination naming something unconfigured             -> exit 2, names listed
#   (e) a bad API key                                           -> non-zero, clear message
# Each run is wrapped in `timeout 60` so a regression that BLOCKS on stdin
# fails the scenario instead of hanging the DTU.
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

# ---- (a) no destinations at all ----
use_settings none
assert_exit_code 2 "s9a-no-destinations" timeout 60 context-intelligence-upload -y
assert_stderr_contains "--server-url" "s9a-guidance-mentions-flags"

# ---- (b) ambiguity in a non-interactive shell ----
use_settings dual
assert_exit_code 2 "s9b-non-interactive-ambiguity" timeout 60 context-intelligence-upload -y
assert_stderr_contains "alpha" "s9b-lists-valid-names"
assert_stderr_contains "beta"  "s9b-lists-valid-names"

# ---- (c) non-interactive without --auto-approve ----
use_settings single
assert_exit_code 2 "s9c-non-interactive-without-auto-approve" timeout 60 context-intelligence-upload
assert_stderr_contains "--auto-approve" "s9c-tells-user-the-fix"

# ---- (d) unknown --destination name ----
use_settings dual
assert_exit_code 2 "s9d-unknown-destination" timeout 60 context-intelligence-upload --destination nope -y
assert_stderr_contains "alpha" "s9d-lists-valid-names"

# ---- (e) bad API key: a real request, rejected by a real server ----
use_settings single
set +e
timeout 120 context-intelligence-upload --server-url "$SRV_A_URL" --api-key totally-wrong-key -y \
    >/tmp/s9e.out 2>/tmp/s9e.err
BAD_RC=$?
set -e
echo "--- bad-key stdout ---"; cat /tmp/s9e.out
echo "--- bad-key stderr ---"; cat /tmp/s9e.err
[[ "$BAD_RC" -ne 0 ]] || _fatal 28 "s9e: a bad API key exited 0"
[[ "$BAD_RC" -ne 124 ]] || _fatal 28 "s9e: run TIMED OUT (rc=124) -- it hung instead of failing"
grep -qiE '401|403|unauthor|forbidden|auth' /tmp/s9e.err /tmp/s9e.out \
    || _fatal 28 "s9e: bad-key failure message does not name an auth problem"

echo "PASS S9: all failure modes exit loudly (2/2/2/2 + non-zero bad key), none hang"
