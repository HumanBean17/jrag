#!/usr/bin/env bash
# Fake claude binary for the driver-side wall-clock timeout path of run_cell.
#
# On the main invocation, replaces this shell with `sleep` (exec) so the
# watchdog's proc.terminate() SIGTERMs `sleep` directly — no bash signal-forwarding
# quirk, no orphaned child holding the stdout pipe. The pipe closes, the blocked
# readline returns EOF, and exit_reason is "timeout". With wall_timeout_s << 30
# the cell returns in ~that, not 30s.
#
# _claude_code_version invokes `<bin> --version` with a 10s capture timeout;
# answer it fast so that call doesn't mask the wall-clock assertion.
set -euo pipefail

if [[ "${1:-}" == "--version" ]]; then
    echo "emit-slow-fake 1.0"
    exit 0
fi

if [[ -n "${JRAG_ARGV_SIDECAR:-}" ]]; then
    printf '%s\n' "$@" >"$JRAG_ARGV_SIDECAR"
fi

exec sleep 30
