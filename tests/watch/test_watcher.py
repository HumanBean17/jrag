"""Tests for ``SourceWatcher`` — debounced per-type reindex dispatcher.

These are FAST LOGIC TESTS: ``pipeline.run_cocoindex_update`` /
``pipeline.run_incremental_graph`` are monkeypatched to fakes and the
``WarmResources`` snapshot methods are spied, so NO real cocoindex/graph build
runs. They pin:

  * classification of an event path into reindex kinds (``java`` / ``sql`` /
    ``yaml``) honoring the cocoindex target-set union + ``LayeredIgnore``;
  * the ``reindex`` call sequence: vectors-then-graph, with
    ``begin_graph_snapshot`` BEFORE the graph subprocess and
    ``commit_graph_snapshot`` ALWAYS (success AND failure) — never a dangling
    snapshot reader (design §4.7 COW lifecycle);
  * sql/yaml changes go vectors-only (the graph does not index SQL/YAML);
  * debounce coalesces a burst of saves into ONE reindex.

The debounce/burst case drives ``_schedule`` directly (the same path the watchdog
handler calls) with a short window, so it is deterministic and does not depend on
real filesystem-event delivery.
"""
from __future__ import annotations

import time
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from watchdog.events import FileCreatedEvent

from java_codebase_rag.watch.watcher import (
    INDEXED_SUFFIXES,
    SourceWatcher,
)


# -- fakes ----------------------------------------------------------------------


class _FakeCfg:
    """Minimal stand-in for ``ResolvedOperatorConfig``: only the 4 attrs watcher uses."""

    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root
        self.index_dir = source_root / "idx"
        self.ladybug_path = source_root / "code_graph.lbug"

    def subprocess_env(self, base=None) -> dict[str, str]:
        return {
            "JAVA_CODEBASE_RAG_INDEX_DIR": str(self.index_dir),
            "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(self.source_root),
        }


class _FakeWarm:
    """Records COW snapshot lifecycle calls into the shared call log."""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def begin_graph_snapshot(self) -> None:
        self._calls.append("begin_graph_snapshot")

    def commit_graph_snapshot(self) -> None:
        self._calls.append("commit_graph_snapshot")


def _make_vec_fake(calls: list[str], rc: int = 0):
    def fake(env, *, full_reprocess, quiet, verbose=True, **_):
        calls.append("run_cocoindex_update")
        return CompletedProcess(args=[], returncode=rc, stdout="", stderr="")

    return fake


def _make_graph_fake(calls: list[str], rc: int = 0):
    def fake(*, source_root, ladybug_path, verbose, quiet=False, env=None, **_):
        calls.append("run_incremental_graph")
        return CompletedProcess(args=[], returncode=rc, stdout="", stderr="")

    return fake


def _scaffold_source(tmp_path: Path) -> Path:
    """Create a minimal Maven-shaped source tree under ``tmp_path``."""
    (tmp_path / "src" / "main" / "java" / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main" / "resources" / "db" / "migration").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_watcher(
    tmp_path: Path,
    calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    vec_rc: int = 0,
    graph_rc: int = 0,
    debounce_ms: int = 60,
) -> SourceWatcher:
    """Build a ``SourceWatcher`` whose pipeline calls + warm snapshot methods record
    into ``calls`` and which is NOT started (use for direct ``reindex`` tests).

    ``monkeypatch`` is threaded in (rather than a bare ``pytest.MonkeyPatch()``)
    so the pipeline patches are auto-undone at test teardown -- a bare
    ``MonkeyPatch()`` never registers a finalizer, leaving the module-level names
    patched for the rest of the session."""
    root = _scaffold_source(tmp_path)
    cfg = _FakeCfg(root)
    warm = _FakeWarm(calls)
    on_event = lambda kind, detail: calls.append(f"on_event:{kind}")  # noqa: E731
    watcher = SourceWatcher(
        cfg,
        warm,
        debounce_ms=debounce_ms,
        backend="polling",
        poll_interval_ms=40,
        on_event=on_event,
    )
    # Patch the module-level names the watcher imported; record into `calls`.
    monkeypatch.setattr(
        "java_codebase_rag.watch.watcher.run_cocoindex_update", _make_vec_fake(calls, rc=vec_rc)
    )
    monkeypatch.setattr(
        "java_codebase_rag.watch.watcher.run_incremental_graph",
        _make_graph_fake(calls, rc=graph_rc),
    )
    return watcher


# -- module constants ----------------------------------------------------------


def test_indexed_suffixes_union():
    """``INDEXED_SUFFIXES`` exposes the java suffix (the resource globs are matched
    by classification helpers, not by a shared iterator)."""
    assert INDEXED_SUFFIXES == (".java",)


# -- classification ------------------------------------------------------------


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("src/main/java/app/Foo.java", {"java"}),
        ("src/main/resources/db/migration/V1__init.sql", {"sql"}),
        ("src/main/resources/application.yml", {"yaml"}),
        ("src/main/resources/application-prod.yaml", {"yaml"}),
        # non-indexed suffixes
        ("README.md", set()),
        ("src/main/resources/db/migration/V1.txt", set()),
        # sql NOT under db/migration is ignored by cocoindex → not indexed
        ("src/main/resources/V1.sql", set()),
        # yaml not matching application* under src/main/resources
        ("src/main/resources/logback.yml", set()),
        ("src/main/resources/application.yaml.bak", set()),
    ],
)
def test_classify_indexed_paths(tmp_path, monkeypatch, rel, expected):
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    assert w._classify(tmp_path / rel) == expected


@pytest.mark.parametrize(
    "rel",
    [
        # ignored by builtin COMMON_EXCLUDED_PATH_PATTERNS (LayeredIgnore honored)
        "src/test/java/app/FooTest.java",  # **/src/test/java/**
        ".git/HEAD",  # **/.git/**  (also not an indexed suffix)
        "node_modules/pkg/Foo.java",  # **/node_modules/**
    ],
)
def test_classify_ignored_paths_fire_nothing(tmp_path, monkeypatch, rel):
    """An event on an ignored path produces no reindex kind — even a ``.java``
    under ``src/test/java/`` is dropped because ``LayeredIgnore`` wins. classify
    resolves paths without requiring them to exist on disk."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    assert w._classify(tmp_path / rel) == set()


def test_classify_outside_source_root_is_empty(tmp_path, monkeypatch):
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    outside = tmp_path.parent / "elsewhere.java"
    assert w._classify(outside) == set()


# -- reindex: java → vectors THEN graph with COW lifecycle ---------------------


def test_reindex_java_runs_vectors_then_graph_with_cow_ordering(tmp_path, monkeypatch):
    """A java change runs vectors THEN graph; ``begin_graph_snapshot`` precedes the
    graph subprocess and ``commit_graph_snapshot`` follows it (COW lifecycle)."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    w.reindex({"java"})
    assert calls == [
        "on_event:indexing_started",
        "on_event:vectors",
        "run_cocoindex_update",
        "on_event:graph",
        "begin_graph_snapshot",
        "run_incremental_graph",
        "commit_graph_snapshot",
        "on_event:indexing_done",
    ]
    assert w.last_reindex is not None
    assert w.last_reindex["kinds"] == ["java"]


def test_reindex_graph_failure_still_commits_and_does_not_crash(tmp_path, monkeypatch):
    """A nonzero graph returncode still calls ``commit_graph_snapshot`` (no dangling
    snapshot reader — the crash marker drives the next full rebuild), emits
    ``error`` (not ``indexing_done``), and leaves the watcher usable."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch, graph_rc=1)
    w.reindex({"java"})
    assert "begin_graph_snapshot" in calls
    assert calls.index("begin_graph_snapshot") < calls.index("run_incremental_graph")
    assert "commit_graph_snapshot" in calls
    assert calls.index("run_incremental_graph") < calls.index("commit_graph_snapshot")
    assert "on_event:error" in calls
    assert "on_event:indexing_done" not in calls

    # watcher survived: a subsequent successful reindex completes normally
    calls.clear()
    monkeypatch.setattr(
        "java_codebase_rag.watch.watcher.run_incremental_graph", _make_graph_fake(calls, rc=0)
    )
    w.reindex({"java"})
    assert "on_event:indexing_done" in calls
    assert calls.count("commit_graph_snapshot") == 1


# -- reindex: sql / yaml → vectors only ---------------------------------------


def test_reindex_sql_is_vectors_only_no_snapshot(tmp_path, monkeypatch):
    """A sql change runs vectors only; the graph (and its COW snapshot) is untouched
    because the graph does not index SQL."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    w.reindex({"sql"})
    assert calls == [
        "on_event:indexing_started",
        "on_event:vectors",
        "run_cocoindex_update",
        "on_event:indexing_done",
    ]
    assert "run_incremental_graph" not in calls
    assert "begin_graph_snapshot" not in calls
    assert "commit_graph_snapshot" not in calls


def test_reindex_yaml_is_vectors_only_no_snapshot(tmp_path, monkeypatch):
    """A yaml change runs vectors only — same rationale as sql."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    w.reindex({"yaml"})
    assert "run_cocoindex_update" in calls
    assert "run_incremental_graph" not in calls
    assert "begin_graph_snapshot" not in calls
    assert "on_event:indexing_done" in calls


def test_reindex_java_and_sql_runs_graph_once(tmp_path, monkeypatch):
    """A mixed burst (java+sql) runs vectors once and graph once (java present)."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    w.reindex({"java", "sql"})
    assert calls.count("run_cocoindex_update") == 1
    assert calls.count("run_incremental_graph") == 1
    assert "begin_graph_snapshot" in calls
    assert "commit_graph_snapshot" in calls


# -- debounce coalescing -------------------------------------------------------


def test_burst_of_saves_coalesces_to_one_reindex(tmp_path, monkeypatch):
    """Five rapid schedules produce exactly ONE reindex (one vectors call)."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch, debounce_ms=50)
    w.start()
    try:
        for _ in range(5):
            w._schedule({"java"})
        # Wait for the single debounced reindex to fire.
        deadline = time.time() + 3.0
        while time.time() < deadline and calls.count("run_cocoindex_update") < 1:
            time.sleep(0.01)
        # Hold past one debounce window to prove no second fire happens.
        time.sleep(0.15)
        assert calls.count("run_cocoindex_update") == 1
        assert calls.count("run_incremental_graph") == 1
    finally:
        w.stop()


def test_handler_classifies_and_schedules_java(tmp_path, monkeypatch):
    """The watchdog handler bridges to classify→schedule: a created .java event
    enqueues the ``java`` kind into the debounce collector."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch)
    path = tmp_path / "src" / "main" / "java" / "app" / "Foo.java"
    w._handler.on_any_event(FileCreatedEvent(str(path)))
    with w._lock:
        assert "java" in w._pending


def test_real_file_write_through_polling_observer_fires_reindex(tmp_path, monkeypatch):
    """End-to-end: a REAL .java write is picked up by the polling observer and
    flows through classify→debounce→reindex. Proves the observer thread is
    healthy (the float-timeout contract) and the whole pipeline is wired."""
    calls: list[str] = []
    w = _make_watcher(tmp_path, calls, monkeypatch, debounce_ms=60)
    w.start()
    try:
        (tmp_path / "src" / "main" / "java" / "app" / "Greeting.java").write_text(
            "package app;\npublic class Greeting {}\n"
        )
        deadline = time.time() + 4.0
        while time.time() < deadline and calls.count("run_cocoindex_update") < 1:
            time.sleep(0.02)
        assert calls.count("run_cocoindex_update") == 1
        assert "commit_graph_snapshot" in calls
    finally:
        w.stop()
