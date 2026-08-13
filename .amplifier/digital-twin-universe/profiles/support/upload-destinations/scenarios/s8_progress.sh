#!/usr/bin/env bash
# S8 -- PROGRESS RENDERING: TTY vs piped.
# Runs the SAME upload twice: once under a real PTY (`script`), once with
# stdout piped to a file. The TTY transcript must carry ANSI control bytes
# (the live redraw) and the two-level session counter; the piped capture must
# carry NO ANSI at all and one plain completion line per session. Both must
# end with the final summary.
set -euo pipefail
source /root/dest-e2e/env.sh
source /root/dest-e2e/dest_assert.sh
guard_in_container

use_settings single

script -qec "context-intelligence-upload -y" /dev/null > /tmp/s8_tty.out 2>&1
context-intelligence-upload -y > /tmp/s8_piped.out 2>&1

echo "--- TTY capture ---";   cat -v /tmp/s8_tty.out   | head -40
echo "--- piped capture ---"; cat -v /tmp/s8_piped.out | head -40

grep -qP '\x1b\[' /tmp/s8_tty.out \
    || _fatal 25 "s8: TTY run produced no ANSI control sequences -- the live renderer did not engage"
grep -qE '\[[0-9]+/[0-9]+\]' /tmp/s8_tty.out \
    || _fatal 25 "s8: TTY run is missing the two-level [n/N] session counter"

grep -qP '\x1b\[' /tmp/s8_piped.out \
    && _fatal 26 "s8: piped run emitted ANSI control sequences -- the TTY-aware fallback did not engage"

for f in /tmp/s8_tty.out /tmp/s8_piped.out; do
    grep -qiE 'session|event' "$f" \
        || _fatal 27 "s8: no final summary found in ${f}"
done

echo "PASS S8: two-level live render on a TTY, plain lines when piped"
