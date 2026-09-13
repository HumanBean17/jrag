"""usage.events — event record contract, v1 schema (Task 2)."""

from __future__ import annotations

import datetime as dt

from java_codebase_rag.usage import events


def _ts_parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_common_core() -> None:
    ev = events.build_command_event(
        verb="search",
        query="OrderService",
        flags={"limit": 20},
        duration_ms=12.5,
        rc=0,
        envelope_facts={
            "status": "ok",
            "result_count": 3,
            "truncated": False,
            "candidates_count": 0,
            "absence_verdict": None,
            "absence_cause": None,
            "warnings_count": 0,
        },
        index_age_s=120.0,
        served_by="cold",
        ppid=111,
        cwd="/tmp/proj",
        project_key="abc123def456",
    )
    assert ev["v"] == 1
    assert ev["surface"] == "cli"
    assert ev["event"] == "command"
    assert ev["project_key"] == "abc123def456"
    assert isinstance(ev["pid"], int)
    _ts_parse(ev["ts"])  # RFC3339-ish, parseable, UTC
    payload = {k: v for k, v in ev.items()
               if k not in {"v", "ts", "surface", "event", "project_key", "pid"}}
    assert payload == {
        "verb": "search",
        "query": "OrderService",
        "flags": {"limit": 20},
        "duration_ms": 12.5,
        "rc": 0,
        "envelope_facts": {
            "status": "ok",
            "result_count": 3,
            "truncated": False,
            "candidates_count": 0,
            "absence_verdict": None,
            "absence_cause": None,
            "warnings_count": 0,
        },
        "index_age_s": 120.0,
        "served_by": "cold",
        "ppid": 111,
        "cwd": "/tmp/proj",
    }


def test_cap_query() -> None:
    assert events.cap_query(None) is None
    assert events.cap_query("a\nb") == "a"
    assert len(events.cap_query("x" * 300)) == 200


def test_event_id_deterministic() -> None:
    a = events.derive_event_id("t", 1, "find", "Foo")
    b = events.derive_event_id("t", 1, "find", "Foo")
    c = events.derive_event_id("t", 1, "find", "Bar")
    assert a == b and len(a) == 10
    assert a != c


def test_reindex_and_daemon_shapes() -> None:
    ri = events.build_reindex_event(
        kind="error",
        detail={"phase": "vectors", "returncode": 1, "stderr_tail": "boom"},
        project_key="k",
    )
    assert ri["surface"] == "watch" and ri["event"] == "reindex"
    assert ri["kind"] == "error"
    assert ri["detail"]["stderr_tail"] == "boom"
    da = events.build_daemon_event("start", {"mode": "vector"}, "k")
    assert da["surface"] == "watch" and da["event"] == "daemon"
    assert da["lifecycle"] == "start" and da["detail"] == {"mode": "vector"}
    assert "pid" in da and "ts" in da


def test_rfc3339_now_format() -> None:
    ts = events.rfc3339_now()
    parsed = _ts_parse(ts)
    assert ts.endswith("Z")
    assert parsed.tzinfo is not None
    # millisecond precision survives the round-trip
    assert parsed.microsecond > 0 or "." in ts
