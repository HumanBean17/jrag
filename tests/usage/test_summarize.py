"""usage.summarize — pure aggregation over event streams (Task 8)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from java_codebase_rag.usage import summarize


def _ts(seconds_ago: float) -> str:
    now = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _cmd(verb: str, *, ago: float = 60, status: str = "ok", count: int = 3,
         dur: float = 10.0, q: str | None = "Foo", ppid: int = 1,
         age: float | None = 100.0, served: str = "cold", eid: str | None = None,
         rc: int = 0, **extra) -> dict:
    ev = {
        "v": 1, "ts": _ts(ago), "surface": "cli", "event": "command",
        "project_key": "k", "pid": 100, "verb": verb, "query": q,
        "flags": {}, "duration_ms": dur, "rc": rc,
        "envelope_facts": {
            "status": status, "result_count": count, "truncated": False,
            "candidates_count": 0, "absence_verdict": None,
            "absence_cause": None, "warnings_count": 0,
        },
        "index_age_s": age, "served_by": served, "ppid": ppid, "cwd": "/p",
    }
    if eid:
        ev["event_id"] = eid
    ev.update(extra)
    return ev


def test_load_events_skips_torn(tmp_path: Path) -> None:
    f = tmp_path / "events-2026-09-13.jsonl"
    f.write_text(
        json.dumps(_cmd("find")) + "\nnot-json\n" + json.dumps(_cmd("search")) + "\n"
    )
    events, torn = summarize.load_events([f], since_days=7)
    assert torn == 1
    assert [e["verb"] for e in events] == ["find", "search"]


def test_sessions_gap_split_and_pid_guard() -> None:
    a1 = _cmd("search", ago=4000, ppid=1)
    a2 = _cmd("search", ago=3900, ppid=1)   # same session as a1 (100s gap)
    a3 = _cmd("search", ago=100, ppid=1)    # >1800s from a2 -> new session
    b1 = _cmd("find", ago=90, ppid=2)       # different ppid -> own session
    result = summarize.sessions([a3, a2, a1, b1])  # unsorted input
    assert len(result) == 3
    assert [e["verb"] for e in result[0]] == ["search", "search"]
    assert [e["verb"] for e in result[1]] == ["search"]
    assert [e["verb"] for e in result[2]] == ["find"]


def test_per_verb_metrics() -> None:
    events = [
        _cmd("search", status="ok", count=5, dur=100, served="cold"),
        _cmd("search", status="ok", count=0, dur=900, served="daemon"),
        _cmd("search", status="not_found", count=0, dur=300),
        _cmd("search", status="error", count=0, dur=5000, rc=2),
        _cmd("find", status="ok", count=1, dur=50),
    ]
    rows = {r["verb"]: r for r in summarize.per_verb(events)}
    s = rows["search"]
    assert s["calls"] == 4
    assert s["ok"] == 2 and s["not_found"] == 1 and s["error"] == 1
    assert s["empty_ok"] == 1  # ok with result_count == 0
    assert s["p50_ms"] == 300.0
    assert s["p95_ms"] == 5000.0
    assert s["median_result_count"] == 0.0  # median of [0, 0, 5] -> sorted [0,0,5]
    assert rows["find"]["calls"] == 1
    # latency split by served_by: cold sample is [100, 300, 5000]
    assert s["p50_ms_cold"] == 300.0 and s["p50_ms_daemon"] == 900.0


def test_staleness_bins() -> None:
    events = [
        _cmd("search", ago=60, age=500, status="ok"),            # <1h fresh, hit
        _cmd("search", ago=50, age=7_200, status="not_found"),   # 1-6h, miss
        _cmd("search", ago=40, age=100_000, status="ok", count=0),  # >24h, empty-ok miss
        _cmd("search", ago=30, age=None),                        # unknown
    ]
    bins = {b["bucket"]: b for b in summarize.staleness_bins(events)}
    assert (bins["<1h"]["calls"], bins["<1h"]["miss_count"]) == (1, 0)
    assert (bins["1-6h"]["calls"], bins["1-6h"]["miss_count"]) == (1, 1)
    assert (bins[">24h"]["calls"], bins[">24h"]["miss_count"]) == (1, 1)
    assert (bins["unknown"]["calls"], bins["unknown"]["miss_count"]) == (1, 0)


def test_struggle_signals() -> None:
    session = [
        _cmd("search", ago=600, q="OrderServce", status="not_found", ppid=7),
        _cmd("search", ago=500, q="OrderService", status="ok", ppid=7),  # reformulation
        _cmd("search", ago=400, q="Repo", status="ok", ppid=7),
        _cmd("search", ago=300, q="Repo", status="ok", ppid=7),          # repeat
        _cmd("find", ago=200, q="Nothing", status="not_found", ppid=7),  # terminal miss -> abandoned
    ]
    result = summarize.struggle(summarize.sessions(session))
    assert result["reformulation_chains"] == 1
    assert result["abandoned"] == 1
    assert any(r["query"] == "Repo" and r["count"] == 2 for r in result["top_repeats"])


def test_absence_top_and_feedback_join() -> None:
    miss1 = _cmd("search", q="KafkaProducer", status="not_found", eid="e1")
    miss1["envelope_facts"]["absence_verdict"] = "absent"
    miss1["envelope_facts"]["absence_cause"] = "not_in_project"
    miss2 = _cmd("search", q="OrderReposistory", status="not_found")
    miss2["envelope_facts"]["absence_verdict"] = "close_miss"
    miss2["envelope_facts"]["absence_cause"] = "typo"
    top = summarize.absence_top([miss1, miss1, miss2], limit=5)
    by_term = {t["term"]: t for t in top}
    assert by_term["kafkaproducer"]["count"] == 2
    assert by_term["kafkaproducer"]["causes"]["not_in_project"] == 2

    labels = [
        {"ts": _ts(10), "event_id": "e1", "rating": "bad", "note": ""},
        {"ts": _ts(5), "event_id": "gone", "rating": "good", "note": ""},
    ]
    joined = summarize.feedback_join([miss1, miss2], labels)
    assert joined["labels_total"] == 2 and joined["bad"] == 1 and joined["good"] == 1
    assert joined["orphan_ids"] == ["gone"]


def test_watch_health() -> None:
    events = [
        {"v": 1, "ts": _ts(3000), "surface": "watch", "event": "reindex",
         "kind": "indexing_done", "detail": {"phases": ["vectors", "graph"]},
         "project_key": "k", "pid": 1},
        {"v": 1, "ts": _ts(2000), "surface": "watch", "event": "reindex",
         "kind": "indexing_done", "detail": {}, "project_key": "k", "pid": 1},
        {"v": 1, "ts": _ts(1000), "surface": "watch", "event": "reindex",
         "kind": "error", "detail": {"phase": "vectors", "returncode": 1,
                                     "stderr_tail": "boom"},
         "project_key": "k", "pid": 1},
    ]
    state = {"consecutive_errors": 1, "queries_served": 5,
             "last_vectors_ok_at": 2000.0, "last_graph_ok_at": None}
    h = summarize.watch_health(events, state)
    assert h["reindex_success_pct"] == pytest.approx(2 / 3)
    assert h["consecutive_errors"] == 1
    assert h["queries_served"] == 5
    assert len(h["recent_failures"]) == 1
    assert h["recent_failures"][0]["stderr_tail"] == "boom"


def test_event_exists(tmp_path: Path) -> None:
    f = tmp_path / "events-2026-09-13.jsonl"
    f.write_text(json.dumps(_cmd("find", eid="deadbeef01")) + "\ntorn\n")
    assert summarize.event_exists([f], "deadbeef01") is True
    assert summarize.event_exists([f], "missing99") is False


def test_storage_status(tmp_path: Path) -> None:
    a = tmp_path / "events-2026-09-01.jsonl"
    b = tmp_path / "events-2026-09-13.jsonl"
    a.write_text("{}\n")
    b.write_text("{}\n{}\n")
    d = tmp_path / "events-2026-09-13.drops"
    d.write_text("3\n")
    s = summarize.storage_status([a, b], [d])
    assert s["files"] == 2 and s["total_bytes"] > 0
    assert s["oldest_day"] == "2026-09-01"
    assert s["drops_total"] == 3
