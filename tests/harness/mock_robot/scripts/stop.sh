#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
#
# Stop phase for the mock_robot wiring fixture.
#
# The Driver shutdown handler already stops the sensor thread and drops the body.
# This exists to reap a provider that outlived its Driver — e.g. `rbnx boot` died
# on an error path before CMD_SHUTDOWN landed.
#
# Reentrant: running it twice, or with nothing running, is a no-op that exits 0.
set -euo pipefail

PATTERN="mock_robot.main"

pids="$(pgrep -f -- "$PATTERN" 2>/dev/null || true)"
if [[ -z "$pids" ]]; then
    echo "[mock_robot/stop] no provider process found — nothing to do"
    exit 0
fi

echo "[mock_robot/stop] SIGTERM to: $pids"
# shellcheck disable=SC2086
kill -TERM $pids 2>/dev/null || true

# Match the 2s budget the deactivate handler uses to join its sensor thread.
for _ in 1 2 3 4; do
    sleep 0.5
    pgrep -f -- "$PATTERN" >/dev/null 2>&1 || {
        echo "[mock_robot/stop] provider exited cleanly"
        exit 0
    }
done

survivors="$(pgrep -f -- "$PATTERN" 2>/dev/null || true)"
if [[ -n "$survivors" ]]; then
    echo "[mock_robot/stop] still alive after 2s; SIGKILL to: $survivors"
    # shellcheck disable=SC2086
    kill -KILL $survivors 2>/dev/null || true
fi
exit 0
