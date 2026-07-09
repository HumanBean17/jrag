"""Tests for ``java_codebase_rag.pipeline`` subprocess helpers.

Focus: ``run_cocoindex_update`` drops the Lance target tables before a *full
reprocess* so the update takes the fast INSERT path. The in-place alternative
(cocoindex's bulk-update ``merge_insert``) emits ~one deletion-vector + version
commit per matched row — O(rows) of tiny file IO that hangs for many minutes on
large repos. Drop+recreate is identical output for a full rebuild.
"""
from __future__ import annotations

import subprocess

from java_codebase_rag import pipeline


def _ok() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _stub_impl(monkeypatch, seen: dict) -> None:
    """Replace the impl + post-optimize with no-op stubs (no cocoindex/lancedb)."""

    def fake_impl(env, **kwargs):
        seen["update"] = seen.get("update", 0) + 1
        seen["full_reprocess"] = kwargs.get("full_reprocess")
        return _ok()

    monkeypatch.setattr(pipeline, "_run_cocoindex_update_impl", fake_impl)
    monkeypatch.setattr(pipeline, "_maybe_run_serialized_optimize", lambda *a, **k: None)


def test_full_reprocess_drops_tables_first(monkeypatch) -> None:
    """full_reprocess=True drops exactly once before the update (INSERT path)."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    drops: list[dict] = []

    def fake_drop(env, *, quiet):
        drops.append(env)
        return _ok()

    monkeypatch.setattr(pipeline, "run_cocoindex_drop", fake_drop)

    pipeline.run_cocoindex_update({"X": "1"}, full_reprocess=True, quiet=True)

    assert len(drops) == 1, "full_reprocess must drop exactly once before update"
    assert drops[0] == {"X": "1"}, "drop must receive the same env as the update"
    assert seen["update"] == 1


def test_increment_does_not_drop(monkeypatch) -> None:
    """full_reprocess=False (increment) must NOT drop — it would lose the table."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    drops: list[dict] = []

    def fake_drop(env, *, quiet):
        drops.append(env)
        return _ok()

    monkeypatch.setattr(pipeline, "run_cocoindex_drop", fake_drop)

    pipeline.run_cocoindex_update({}, full_reprocess=False, quiet=True)

    assert drops == [], "increment must not drop the tables"
    assert seen["update"] == 1


def test_drop_failure_falls_back_to_inplace(monkeypatch, capsys) -> None:
    """A non-preflight drop failure does not abort — the update still runs in-place."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    monkeypatch.setattr(
        pipeline,
        "run_cocoindex_drop",
        lambda env, *, quiet: subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="boom"
        ),
    )

    pipeline.run_cocoindex_update({}, full_reprocess=True, quiet=True)

    assert seen["update"] == 1, "update must still run after a non-fatal drop failure"
    assert "drop-before-reprocess failed" in capsys.readouterr().err


def test_drop_preflight_blocker_is_silent(monkeypatch, capsys) -> None:
    """A preflight drop stub (cocoindex not installed, e.g. graph-only) is not noisy."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    monkeypatch.setattr(
        pipeline,
        "run_cocoindex_drop",
        lambda env, *, quiet: subprocess.CompletedProcess(
            args=["cocoindex"], returncode=127, stdout="", stderr="not found"
        ),
    )

    pipeline.run_cocoindex_update({}, full_reprocess=True, quiet=True)

    assert seen["update"] == 1
    # 127 preflight is expected on graph-only installs and must NOT warn.
    assert "drop-before-reprocess failed" not in capsys.readouterr().err
