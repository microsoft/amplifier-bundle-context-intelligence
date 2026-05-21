#!/usr/bin/env bash
# Bootstrap a Digital Twin Universe for the bundle-usage feature.
#
# Side effects:
#   - Installs the current bundle (this checkout) into the DTU's cache so the
#     new bundle_usage tool, bundle-usage agent, and bundle-usage mode are
#     reachable from inside DTU sessions.
#   - Confirms CI graph reachability.
#   - Locates the ground-truth session id used by scenarios 2/3/4/6/7.
#
# Exit codes:
#   0  Success — DTU ready
#   2  CI graph unreachable
#   3  Ground-truth session not found in CI graph
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
KNOWN_SESSION_ID="${BUNDLE_USAGE_DTU_SESSION_ID:-cb56b81d-9cf4-4eb9-9cb0-ed261f63dfc5}"

echo "==> Installing bundle from $BUNDLE_DIR ..."
amplifier-tester setup-digital-twin --bundle-path "$BUNDLE_DIR" --install-mode editable

echo "==> Verifying CI graph reachability ..."
if ! amplifier-tester ci-health; then
    echo "ERROR: CI graph unreachable" >&2
    exit 2
fi

echo "==> Confirming ground-truth session $KNOWN_SESSION_ID ..."
result=$(amplifier-tester ci-cypher "MATCH (s:Session {session_id: '$KNOWN_SESSION_ID'}) RETURN s.session_id LIMIT 1")
if [[ -z "$result" ]]; then
    echo "ERROR: Ground-truth session $KNOWN_SESSION_ID not found in CI graph" >&2
    exit 3
fi

echo "==> DTU ready"
