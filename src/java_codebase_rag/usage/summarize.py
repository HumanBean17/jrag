"""Pure aggregation over usage event streams.

Everything here is deterministic stdlib over data — the only I/O is the two
``load_*`` readers (tolerant of torn lines). Aggregation functions take event
dicts (schema v1, see :mod:`java_codebase_rag.usage.events`) and return plain
JSON-ready shapes for the ``jrag usage`` rollup. Pure like ``eval/metrics.py``
so every metric is unit-testable without a filesystem.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: Session inactivity cutoff (spec decision 9).
SESSION_CUTOFF_S = 1800.0

#: Same-verb reformulation window for struggle signals.
REFORMULATION_WINDOW_S = 300.0

_MISS_STATUSES = ("not_found", "ambiguous")


def _parse_ts(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _status(ev: dict[str, Any]) -> Any:
    return (ev.get("envelope_facts") or {}).get("status")


def _is_miss(ev: dict[str, Any]) -> bool:
    """A call that returned nothing useful: not_found, ambiguous, or ok-but-empty.

    ``ambiguous`` is deliberately included alongside the plan's
    not_found + empty_ok: an ambiguous answer is as unhelpful as a miss for
    the staleness/absence/struggle metrics this feeds.
    """
    facts = ev.get("envelope_facts") or {}
    if _status(ev) in _MISS_STATUSES:
        return True
    return _status(ev) == "ok" and not facts.get("result_count")


# --- readers -----------------------------------------------------------------


def load_events(files: list[Path], since_days: int) -> tuple[list[dict], int]:
    """Stream day files; skip torn lines (counted), drop events older than window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    out: list[dict] = []
    torn = 0
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                ev = json.loads(line)
            except ValueError:
                torn += 1
                continue
            try:
                if _parse_ts(ev["ts"]) >= cutoff.timestamp():
                    out.append(ev)
            except (KeyError, ValueError, TypeError):
                torn += 1
    out.sort(key=lambda e: e.get("ts", ""))
    return out, torn


def load_labels(file: Path) -> list[dict]:
    """Read feedback.jsonl, tolerating missing file and torn lines."""
    try:
        lines = file.read_text().splitlines()
    except OSError:
        return []
    labels = []
    for line in lines:
        try:
            labels.append(json.loads(line))
        except ValueError:
            continue
    return labels


def event_exists(files: list[Path], event_id: str) -> bool:
    """Stream-search retained day files for one event id (feedback anchor)."""
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if event_id in line:
                try:
                    if json.loads(line).get("event_id") == event_id:
                        return True
                except ValueError:
                    continue
    return False


# --- metrics -----------------------------------------------------------------


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(len(ordered) * pct / 100.0)))
    return float(ordered[rank - 1])


def sessions(events: list[dict]) -> list[list[dict]]:
    """Group command events into sessions by (ppid, cwd) + inactivity cutoff.

    Events from different (ppid, cwd) keys never merge (adjacency rule: only
    consecutive same-key events extend a session — a re-used pid appearing
    after another key's session starts a fresh one).
    """
    ordered = sorted(
        (e for e in events if e.get("event") == "command"),
        key=lambda e: e.get("ts", ""),
    )
    out: list[list[dict]] = []
    current: list[dict] = []
    current_key: tuple | None = None
    last_ts: float | None = None
    for ev in ordered:
        try:
            ts = _parse_ts(ev["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        key = (ev.get("ppid"), ev.get("cwd"))
        if (
            current
            and key == current_key
            and last_ts is not None
            and ts - last_ts <= SESSION_CUTOFF_S
        ):
            current.append(ev)
        else:
            if current:
                out.append(current)
            current = [ev]
            current_key = key
        last_ts = ts
    if current:
        out.append(current)
    return out


def per_verb(events: list[dict]) -> list[dict]:
    """Per-verb usage rows: calls, status mix, emptiness, latency by served_by."""
    by_verb: dict[str, list[dict]] = {}
    for ev in events:
        if ev.get("event") != "command":
            continue
        by_verb.setdefault(ev.get("verb") or "?", []).append(ev)
    rows = []
    for verb, evs in sorted(by_verb.items()):
        counts = Counter(str(_status(e)) for e in evs)
        durs = _durations(evs)
        cold = _durations(evs, served_by="cold")
        hot = _durations(evs, served_by="daemon")
        counts_list = [
            (e.get("envelope_facts") or {}).get("result_count")
            for e in evs
            if isinstance((e.get("envelope_facts") or {}).get("result_count"), (int, float))
        ]
        rows.append({
            "verb": verb,
            "calls": len(evs),
            "ok": counts.get("ok", 0),
            "ambiguous": counts.get("ambiguous", 0),
            "not_found": counts.get("not_found", 0),
            "error": counts.get("error", 0),
            "empty_ok": sum(1 for e in evs if _is_miss(e) and _status(e) == "ok"),
            "median_result_count": (
                statistics.median(counts_list) if counts_list else None
            ),
            "truncated_count": sum(
                1 for e in evs if (e.get("envelope_facts") or {}).get("truncated")
            ),
            "p50_ms": percentile(durs, 50),
            "p95_ms": percentile(durs, 95),
            "p50_ms_cold": percentile(cold, 50),
            "p50_ms_daemon": percentile(hot, 50),
        })
    return rows


def _durations(events: list[dict], served_by: str | None = None) -> list[float]:
    """Present duration_ms values only — absent timing must not drag p50 to 0."""
    out = []
    for ev in events:
        if served_by is not None and ev.get("served_by") != served_by:
            continue
        value = ev.get("duration_ms")
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


_STALENESS_BUCKETS = (("<1h", 3600.0), ("1-6h", 6 * 3600.0),
                      ("6-24h", 24 * 3600.0), (">24h", float("inf")))


def staleness_bins(events: list[dict]) -> list[dict]:
    """Calls and miss counts bucketed by index age at call time."""
    out = [{"bucket": name, "calls": 0, "miss_count": 0}
           for name, _ in _STALENESS_BUCKETS]
    out.append({"bucket": "unknown", "calls": 0, "miss_count": 0})
    index = {b["bucket"]: b for b in out}
    for ev in events:
        if ev.get("event") != "command":
            continue
        age = ev.get("index_age_s")
        if not isinstance(age, (int, float)):
            index["unknown"]["calls"] += 1
            index["unknown"]["miss_count"] += 1 if _is_miss(ev) else 0
            continue
        for name, bound in _STALENESS_BUCKETS:
            if age < bound:
                index[name]["calls"] += 1
                index[name]["miss_count"] += 1 if _is_miss(ev) else 0
                break
    return out


def struggle(session_lists: list[list[dict]]) -> dict:
    """Struggle proxies: repeats, reformulation chains, abandoned sessions."""
    repeat_counter: Counter[tuple[str, str]] = Counter()
    reformulations = 0
    abandoned = 0
    for session in session_lists:
        for ev in session:
            if ev.get("verb") and ev.get("query") is not None:
                repeat_counter[(ev["verb"], ev["query"])] += 1
        for prev, nxt in zip(session, session[1:]):
            try:
                gap = _parse_ts(nxt["ts"]) - _parse_ts(prev["ts"])
            except (KeyError, ValueError, TypeError):
                continue
            if (
                _status(prev) in _MISS_STATUSES
                and prev.get("verb") == nxt.get("verb")
                and prev.get("query") != nxt.get("query")
                and 0 <= gap <= REFORMULATION_WINDOW_S
            ):
                reformulations += 1
        if session and _is_miss(session[-1]):
            abandoned += 1
    top = [{"verb": verb, "query": query, "count": count}
           for (verb, query), count in repeat_counter.items() if count > 1]
    top.sort(key=lambda t: -t["count"])
    return {
        "top_repeats": top[:5],
        "reformulation_chains": reformulations,
        "abandoned": abandoned,
    }


def absence_top(events: list[dict], limit: int = 10) -> list[dict]:
    """Aggregate missed queries by term with absence verdict/cause counts."""
    terms: dict[str, dict] = {}
    for ev in events:
        if ev.get("event") != "command" or not _is_miss(ev):
            continue
        if not ev.get("query"):
            continue
        term = str(ev["query"]).strip().lower()
        entry = terms.setdefault(term, {"term": term, "count": 0,
                                        "verdicts": {}, "causes": {}})
        entry["count"] += 1
        facts = ev.get("envelope_facts") or {}
        if facts.get("absence_verdict"):
            entry["verdicts"][facts["absence_verdict"]] = (
                entry["verdicts"].get(facts["absence_verdict"], 0) + 1
            )
        if facts.get("absence_cause"):
            entry["causes"][facts["absence_cause"]] = (
                entry["causes"].get(facts["absence_cause"], 0) + 1
            )
    return sorted(terms.values(), key=lambda t: -t["count"])[:limit]


def feedback_join(events: list[dict], labels: list[dict]) -> dict:
    """Join owner labels to events; aged-out labels report as orphans."""
    known = {e.get("event_id") for e in events if e.get("event_id")}
    matched = [l for l in labels if l.get("event_id") in known]
    return {
        "labels_total": len(labels),
        "good": sum(1 for l in labels if l.get("rating") == "good"),
        "bad": sum(1 for l in labels if l.get("rating") == "bad"),
        "matched": len(matched),
        "orphan_ids": [l["event_id"] for l in labels
                       if l.get("event_id") is not None
                       and l["event_id"] not in known],
    }


def watch_health(events: list[dict], state: dict | None) -> dict:
    """Reindex success over the window + daemon-state passthrough + failures.

    Durations come from ``indexing_started`` → terminal-event ts deltas (the
    watcher emits no explicit duration field); an unterminated start (hung or
    crashed mid-reindex) contributes no duration sample.
    """
    watch_events = [e for e in events if e.get("event") == "reindex"]
    done = [e for e in watch_events if e.get("kind") == "indexing_done"]
    failures = [e for e in watch_events if e.get("kind") == "error"]
    total = len(done) + len(failures)
    state = state or {}
    recent = [
        {
            "ts": e.get("ts"),
            "phase": (e.get("detail") or {}).get("phase"),
            "returncode": (e.get("detail") or {}).get("returncode"),
            "stderr_tail": (e.get("detail") or {}).get("stderr_tail", ""),
        }
        for e in failures[-3:]
    ]
    durations = _reindex_durations(watch_events)
    return {
        "reindex_success_pct": (len(done) / total) if total else None,
        "reindex_count_window": total,
        "reindex_duration_p50_s": percentile(durations, 50),
        "reindex_duration_max_s": max(durations) if durations else None,
        "consecutive_errors": state.get("consecutive_errors"),
        "last_vectors_ok_at": state.get("last_vectors_ok_at"),
        "last_graph_ok_at": state.get("last_graph_ok_at"),
        "queries_served": state.get("queries_served"),
        "last_error": state.get("last_error"),
        "recent_failures": recent,
    }


def _reindex_durations(watch_events: list[dict]) -> list[float]:
    """Seconds between each ``indexing_started`` and its terminal event."""
    out: list[float] = []
    started_ts: float | None = None
    for ev in watch_events:  # load_events returns ts-sorted input
        kind = ev.get("kind")
        try:
            ts = _parse_ts(ev["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        if kind == "indexing_started":
            started_ts = ts
        elif kind in ("indexing_done", "error") and started_ts is not None:
            out.append(max(0.0, ts - started_ts))
            started_ts = None
    return out


def storage_status(files: list[Path], drops: list[Path]) -> dict:
    """Bytes, file count, oldest retained day, dropped-overflow total."""
    total = 0
    oldest: str | None = None
    for path in files:
        try:
            total += path.stat().st_size
            day = path.stem.removeprefix("events-")
            if oldest is None or day < oldest:
                oldest = day
        except OSError:
            continue
    drops_total = 0
    for path in drops:
        try:
            raw = path.read_text().strip()
            if raw.isdigit():
                drops_total += int(raw)
        except OSError:
            continue
    return {
        "files": len(files),
        "total_bytes": total,
        "oldest_day": oldest,
        "drops_total": drops_total,
    }
