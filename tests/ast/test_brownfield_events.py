"""Unit tests for structured brownfield stderr events (PR-1)."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr

from java_codebase_rag.ast.brownfield_events import (
    emit_brownfield_exclusivity_shadowing,
    emit_brownfield_method_string_literal,
    emit_structured_brownfield_event,
)


def test_emit_brownfield_events_jsonl_contract() -> None:
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_brownfield_exclusivity_shadowing(
            method_fqn="com.example.Demo#m",
            shadowed_framework_annotations=["GetMapping"],
        )
        emit_brownfield_method_string_literal(
            method_fqn="com.example.Other#n",
            reason="non_enum_method_value",
        )

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2

    info_rec = json.loads(lines[0])
    assert info_rec["event"] == "brownfield-exclusivity-shadowing"
    assert info_rec["severity"] == "INFO"
    assert info_rec["method_fqn"] == "com.example.Demo#m"
    assert info_rec["shadowed_framework_annotations"] == ["GetMapping"]

    warn_rec = json.loads(lines[1])
    assert warn_rec["event"] == "brownfield-method-string-literal"
    assert warn_rec["severity"] == "WARN"
    assert warn_rec["method_fqn"] == "com.example.Other#n"
    assert warn_rec["reason"] == "non_enum_method_value"


def test_emit_structured_brownfield_event_reserved_keys_not_shadowed_by_fields() -> None:
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_structured_brownfield_event(
            "brownfield-exclusivity-shadowing",
            "INFO",
            event="wrong",
            severity="WARN",
            method_fqn="x.Y#z",
        )
    rec = json.loads(buf.getvalue().strip())
    assert rec["event"] == "brownfield-exclusivity-shadowing"
    assert rec["severity"] == "INFO"
    assert rec["method_fqn"] == "x.Y#z"
