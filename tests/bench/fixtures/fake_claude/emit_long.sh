#!/usr/bin/env bash
# Fake claude binary for the driver-side turn-cap path of run_cell.
#
# Emits four stream-json `assistant` events (each with a single Read tool_use),
# then a `result` event with num_turns=4. With max_turns=2 the driver SIGTERMs
# on the 3rd assistant event (count=3 exceeds 2) and never reads the result.
# Exits 0 when allowed to run to completion.
#
# Records its received argv ("$@") to the path in $JRAG_ARGV_SIDECAR when set.
set -euo pipefail

if [[ -n "${JRAG_ARGV_SIDECAR:-}" ]]; then
    printf '%s\n' "$@" >"$JRAG_ARGV_SIDECAR"
fi

cat <<'EOF'
{"type":"assistant","message":{"id":"msg_1","type":"message","role":"assistant","content":[{"type":"tool_use","id":"call_1","name":"Read","input":{"file_path":"/tmp/a"}}]}}
{"type":"assistant","message":{"id":"msg_2","type":"message","role":"assistant","content":[{"type":"tool_use","id":"call_2","name":"Read","input":{"file_path":"/tmp/b"}}]}}
{"type":"assistant","message":{"id":"msg_3","type":"message","role":"assistant","content":[{"type":"tool_use","id":"call_3","name":"Read","input":{"file_path":"/tmp/c"}}]}}
{"type":"assistant","message":{"id":"msg_4","type":"message","role":"assistant","content":[{"type":"tool_use","id":"call_4","name":"Read","input":{"file_path":"/tmp/d"}}]}}
{"type":"result","num_turns":4,"stop_reason":"end_turn","terminal_reason":"completed","is_error":false,"usage":{"input_tokens":100,"output_tokens":5}}
EOF

exit 0
