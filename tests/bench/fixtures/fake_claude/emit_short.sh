#!/usr/bin/env bash
# Fake claude binary for the no-cap path of run_cell.
#
# Emits exactly one stream-json `assistant` event containing a single tool_use
# of name `Read`, followed by a `result` event with num_turns=1,
# terminal_reason="completed", and token usage. Exits 0.
#
# Records its received argv ("$@") to the path in $JRAG_ARGV_SIDECAR when set,
# so tests can assert the spawned argv (e.g. absent --mcp-config for condition A).
# Records its process environment to $JRAG_ENV_SIDECAR when set, so tests can
# assert the spawned env (e.g. PATH/JRAG index vars for the CLI shim).
set -euo pipefail

if [[ -n "${JRAG_ARGV_SIDECAR:-}" ]]; then
    printf '%s\n' "$@" >"$JRAG_ARGV_SIDECAR"
fi

if [[ -n "${JRAG_ENV_SIDECAR:-}" ]]; then
    env >"$JRAG_ENV_SIDECAR"
fi

cat <<'EOF'
{"type":"assistant","message":{"id":"msg_1","type":"message","role":"assistant","content":[{"type":"tool_use","id":"call_1","name":"Read","input":{"file_path":"/tmp/test.txt"}}]}}
{"type":"result","num_turns":1,"stop_reason":"end_turn","terminal_reason":"completed","is_error":false,"usage":{"input_tokens":100,"output_tokens":5}}
EOF

exit 0
