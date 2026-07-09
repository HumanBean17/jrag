"""PR-3: vectors-phase + optimize-phase index progress.

Two tests are HEAVY-gated (``JAVA_CODEBASE_RAG_RUN_HEAVY=1``) because they run
a real ``cocoindex update`` against the bank-chat-system corpus (embedding model
cached). The remaining tests are LIGHT: they exercise the renderer against a
synthetic event stream or patch the pipeline helpers so no cocoindex/torch loads.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

from java_codebase_rag.progress import IndexProgressRenderer, ProgressEvent

_PREFIX = "JCIRAG_PROGRESS"

HEAVY = os.environ.get("JAVA_CODEBASE_RAG_RUN_HEAVY", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Light: renderer clamp-on-completion + indeterminate (tests 2, 3, 4)
# ---------------------------------------------------------------------------


def _renderer(*, terminal: bool = True) -> IndexProgressRenderer:
    """Build a renderer over a buffer; force_terminal=True exercises the Live path."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=terminal, width=120, color_system=None)
    return IndexProgressRenderer(["vectors", "optimize", "graph"], console=console)


def test_vectors_progress_clamps_on_completion() -> None:
    """total=100, done=80, then parent status=done -> completed clamps to 100."""
    r = _renderer()
    r.start()
    try:
        r.apply(ProgressEvent(kind="vectors", phase=None, pass_=None, done=80, total=100, status="running", elapsed_s=None))
        assert r._progress.tasks[r._task_ids["vectors"]].completed == 80
        # Parent emits the terminal event (the flow cannot — no "all files done"
        # hook). done=None here; the clamp must still snap completed to total.
        r.apply(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=100, status="done", elapsed_s=1.0))
        task = r._progress.tasks[r._task_ids["vectors"]]
        assert task.completed == 100
        assert task.finished
    finally:
        r.stop()


def test_vectors_progress_approximate_total_overstates_then_clamps() -> None:
    """Approximate pre-walk overstates total (100) but done only reaches 95; the
    parent's status=done still clamps to 100 (no 95% stall)."""
    r = _renderer()
    r.start()
    try:
        r.apply(ProgressEvent(kind="vectors", phase=None, pass_=None, done=None, total=100, status="running", elapsed_s=None))
        r.apply(ProgressEvent(kind="vectors", phase=None, pass_=None, done=95, total=100, status="running", elapsed_s=None))
        assert r._progress.tasks[r._task_ids["vectors"]].completed == 95
        r.apply(ProgressEvent(kind="vectors", phase=None, pass_=None, done=95, total=100, status="done", elapsed_s=42.1))
        assert r._progress.tasks[r._task_ids["vectors"]].completed == 100
        assert r._progress.tasks[r._task_ids["vectors"]].finished
    finally:
        r.stop()


def test_vectors_incremental_renders_indeterminate() -> None:
    """No total event (incremental catch-up) -> task stays indeterminate (total None)."""
    r = _renderer()
    r.start()
    try:
        # Only a done tick, no total — mirrors incremental catch-up where the
        # memo cache skips unchanged files and no total is knowable up front.
        r.apply(ProgressEvent(kind="vectors", phase=None, pass_=None, done=3, total=None, status="running", elapsed_s=None))
        task = r._progress.tasks[r._task_ids["vectors"]]
        assert task.total is None
    finally:
        r.stop()


# ---------------------------------------------------------------------------
# Light: CLI-level vectors/optimize progress (tests 6, 7)
# ---------------------------------------------------------------------------


def _make_stub_completed(*, returncode: int = 0, stderr: str = "") -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(args=["stub"], returncode=returncode, stdout="", stderr=stderr)


def _patch_pipeline_for_vectors_progress(monkeypatch: pytest.MonkeyPatch, *, emit_vectors: bool) -> None:
    """Patch cocoindex + graph helpers so init/increment run without heavy deps.

    When ``emit_vectors`` is True the patched cocoindex helper invokes the
    caller's ``on_progress`` with a synthetic ``kind=vectors`` event — simulating
    what the real subprocess drain would feed the renderer in default mode.
    """
    from java_codebase_rag import cli as _cli
    from java_codebase_rag import pipeline as _pipeline

    def _fake_cocoindex_update(env, *, full_reprocess, quiet, verbose=True, lance_project_root=None, on_progress=None, on_progress_console=None):
        if emit_vectors and on_progress is not None:
            on_progress(
                ProgressEvent(kind="vectors", phase=None, pass_=None, done=10, total=130, status="running", elapsed_s=None)
            )
        return _make_stub_completed(returncode=0)

    def _fake_run_build_ast_graph(*, source_root, ladybug_path, verbose, quiet=False, env=None, on_progress=None, on_progress_console=None):
        return _make_stub_completed(returncode=0)

    def _fake_run_incremental_graph(*, source_root, ladybug_path, verbose, quiet=False, env=None, on_progress=None, on_progress_console=None):
        return _make_stub_completed(returncode=0)

    monkeypatch.setattr(_cli, "run_cocoindex_update", _fake_cocoindex_update)
    monkeypatch.setattr(_cli, "run_build_ast_graph", _fake_run_build_ast_graph)
    monkeypatch.setattr(_cli, "run_incremental_graph", _fake_run_incremental_graph)
    monkeypatch.setattr(_pipeline, "run_cocoindex_update", _fake_cocoindex_update)
    monkeypatch.setattr(_pipeline, "run_build_ast_graph", _fake_run_build_ast_graph)
    monkeypatch.setattr(_pipeline, "run_incremental_graph", _fake_run_incremental_graph)


def test_cli_init_vectors_phase_progress_on_stderr(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In default mode a vectors-phase progress event is parsed and rendered to
    stderr; the raw ``JCIRAG_PROGRESS`` line is NOT echoed verbatim."""
    from java_codebase_rag import cli as cli_mod

    idx = tmp_path / "idx_vectors_prog"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    _patch_pipeline_for_vectors_progress(monkeypatch, emit_vectors=True)
    buf = io.StringIO()
    import contextlib

    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["init", "--source-root", str(corpus_root), "--index-dir", str(idx)]
        )
    assert rc == 0
    err = buf.getvalue()
    # The raw structured line is consumed by the parser, never raw-relayed.
    assert "JCIRAG_PROGRESS kind=vectors" not in err
    # But vectors-phase progress IS rendered (non-TTY concise fallback prints a
    # "vectors ..." line). The synthetic event had done=10, total=130.
    assert "vectors" in err.lower()


def test_cli_reprocess_optimize_phase_progress(
    corpus_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The optimize phase renders when optimize_lance_tables emits kind=optimize.

    Patches server.run_refresh_pipeline to drive the renderer's on_progress with
    a synthetic optimize event (mirrors the in-process emission). Asserts the
    phase renders and the raw line is not echoed."""
    import contextlib

    from java_codebase_rag import cli as cli_mod
    from java_codebase_rag.mcp import server

    idx = tmp_path / "idx_optimize_prog"
    idx.mkdir()
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    async def _fake_refresh(*, quiet=False, verbose=True, on_progress=None, on_progress_console=None):
        # Emit a synthetic optimize event (mirrors lance_optimize's in-process
        # emission) so the renderer's optimize task is exercised.
        if on_progress is not None:
            on_progress(ProgressEvent(kind="optimize", phase=None, pass_=None, done=None, total=None, status="running", elapsed_s=None))
            on_progress(ProgressEvent(kind="optimize", phase=None, pass_=None, done=None, total=None, status="done", elapsed_s=1.2))
        from java_codebase_rag.mcp.server import RefreshIndexOutput

        return RefreshIndexOutput(success=True, message=None, phases_run=["vectors", "graph"])

    monkeypatch.setattr(server, "run_refresh_pipeline", _fake_refresh)

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cli_mod.main(
            ["reprocess", "--source-root", str(corpus_root), "--index-dir", str(idx)]
        )
    assert rc == 0
    err = buf.getvalue()
    # The raw structured line is consumed by the parser, never raw-relayed.
    assert "JCIRAG_PROGRESS kind=optimize" not in err
    # The optimize phase rendered (non-TTY concise fallback prints an
    # "optimize done ..." line for the terminal event).
    assert "optimize" in err.lower()


# ---------------------------------------------------------------------------
# Light: retirement guard (test 8)
# ---------------------------------------------------------------------------


def test_spinner_removed_and_emit_vectors_helpers_removed() -> None:
    """Spinner and emit_vectors_start/_finish are no longer importable; no
    remaining references anywhere in the production tree."""
    from java_codebase_rag import cli_format, cli_progress

    # The retired symbols must not be importable.
    assert not hasattr(cli_format, "Spinner"), "Spinner should have been removed from cli_format"
    assert not hasattr(cli_progress, "emit_vectors_start"), "emit_vectors_start should have been removed"
    assert not hasattr(cli_progress, "emit_vectors_finish"), "emit_vectors_finish should have been removed"
    # And no remaining references anywhere in the production tree.
    repo_root = Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    for py in (repo_root / "src" / "java_codebase_rag").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # Word-boundary match for the retired Spinner class (not rich's SpinnerColumn).
        if re.search(r"\bSpinner\b", text):
            offenders.append(str(py))
    server_py = repo_root / "src" / "java_codebase_rag" / "mcp" / "server.py"
    if server_py.is_file():
        text = server_py.read_text(encoding="utf-8")
        if re.search(r"\bSpinner\b", text) or "emit_vectors_start" in text or "emit_vectors_finish" in text:
            offenders.append(str(server_py))
    assert not offenders, f"retired symbols still referenced in: {offenders}"


# ---------------------------------------------------------------------------
# Heavy: real cocoindex flow emission + pre-walk divergence (tests 1, 5)
# ---------------------------------------------------------------------------


def _cocoindex_bin() -> Path:
    return Path(sys.executable).parent / "cocoindex"


def _require_cocoindex_runtime_deps() -> None:
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(f"heavy e2e needs project deps: {exc}")


pytestmark_heavy = pytest.mark.skipif(
    not HEAVY,
    reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the cocoindex vectors-flow test",
)


def _run_cocoindex_update(corpus_root: Path, index_dir: Path) -> subprocess.CompletedProcess:
    """Run a real ``cocoindex update --full-reprocess`` and return the result."""
    _require_cocoindex_runtime_deps()
    cocoindex_bin = _cocoindex_bin()
    if not cocoindex_bin.is_file():
        pytest.skip(f"cocoindex not installed in venv: {cocoindex_bin}")
    bundle_dir = Path(__file__).resolve().parent.parent
    flow = (bundle_dir / "src" / "java_codebase_rag" / "index" / "java_index_flow_lancedb.py").resolve()
    start = Path(corpus_root).resolve()
    relp = os.path.relpath(str(flow), start=str(start))
    relp = Path(relp).as_posix()
    app_spec = f"{relp}:JavaCodeIndexLance"
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(Path(corpus_root).resolve()),
    }
    return subprocess.run(
        [str(cocoindex_bin), "update", app_spec, "--full-reprocess", "-f"],
        cwd=str(corpus_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )


@pytestmark_heavy
def test_flow_emits_vectors_progress_per_file(corpus_root: Path, tmp_path: Path) -> None:
    """A real ``cocoindex update`` emits ``JCIRAG_PROGRESS kind=vectors`` lines
    in captured stderr: the one-shot approximate ``total=`` line from app_main
    plus per-file ``done=`` ticks (the gating spike, promoted to a regression test)."""
    index_dir = tmp_path / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    proc = _run_cocoindex_update(corpus_root, index_dir)
    assert proc.returncode == 0, f"cocoindex failed: stdout={proc.stdout}\nstderr={proc.stderr}"
    lines = [ln for ln in proc.stderr.splitlines() if "JCIRAG_PROGRESS kind=vectors" in ln]
    assert lines, f"expected vectors progress lines, got stderr:\n{proc.stderr}"
    # The one-shot approximate total line from app_main.
    totals = [ln for ln in lines if "total=" in ln and "done=" not in ln]
    assert totals, f"expected a one-shot total line; lines: {lines!r}"
    # Per-file done ticks (throttled every ~25 files).
    ticks = [ln for ln in lines if "done=" in ln]
    assert ticks, f"expected per-file done ticks; lines: {lines!r}"
    # Done ticks are monotonic non-decreasing.
    done_vals = [int(m.group(1)) for ln in ticks if (m := re.search(r"done=(\d+)", ln))]
    assert done_vals, f"could not parse done values from: {ticks!r}"
    assert done_vals == sorted(done_vals), f"done ticks must be monotonic: {done_vals}"


@pytestmark_heavy
def test_pre_walk_total_divergence_bounded(corpus_root: Path, tmp_path: Path) -> None:
    """On the fixture, the approximate pre-walk total exactly equals the number of
    files actually processed (the spike measured gap == 0).

    The TRUE processed count is read from the LanceDB tables (distinct
    ``filename`` values across the three tables) rather than the throttled
    ``done=k`` tick stream: ticks fire only every 25th file, so ``max(done)``
    lands on a multiple of 25 and would mask the real divergence. The accepted
    over-count on larger trees is the ignored / empty / undecodable file count
    (the renderer clamps to total on completion regardless)."""
    index_dir = tmp_path / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    proc = _run_cocoindex_update(corpus_root, index_dir)
    assert proc.returncode == 0, f"cocoindex failed: stdout={proc.stdout}\nstderr={proc.stderr}"
    lines = [ln for ln in proc.stderr.splitlines() if "JCIRAG_PROGRESS kind=vectors" in ln]
    # The one-shot approximate total.
    total_lines = [ln for ln in lines if "total=" in ln and "done=" not in ln]
    assert total_lines, f"expected a total line; lines: {lines!r}"
    total_match = re.search(r"total=(\d+)", total_lines[0])
    assert total_match, f"could not parse total from: {total_lines[0]!r}"
    pre_walk_total = int(total_match.group(1))
    # The tick stream is throttled (every 25th file), so it cannot yield the
    # true processed count. Read the ground truth from the LanceDB tables:
    # distinct filename values across the three tables the flow populated.
    import lancedb

    db = lancedb.connect(str(index_dir.resolve()))
    actual_done = 0
    for tname in ("javacodeindex_java_code", "sqlschemaindex_sql_schema", "yamlconfigindex_yaml_config"):
        try:
            tbl = db.open_table(tname)
        except Exception as exc:  # pragma: no cover - table missing only on a broken flow
            raise AssertionError(f"Lance table {tname} missing after flow: {exc}") from exc
        rows = tbl.search().select(["filename"]).limit(1_000_000).to_list()
        actual_done += len({r["filename"] for r in rows if r.get("filename") is not None})
    # On this fixture the pre-walk matches exactly (gap == 0): all counted
    # files are non-empty / decodable / not-ignored, and the YAML predicate
    # was fixed to include application*.yml. The accepted over-count on larger
    # trees is the ignored / empty file count (the renderer clamps regardless).
    gap = pre_walk_total - actual_done
    assert gap >= 0, (
        f"pre-walk total {pre_walk_total} < actual done {actual_done} (under-count: "
        f"a matcher predicate is dropping files the flow still processes)"
    )
    assert gap == 0, (
        f"pre-walk over-count on fixture: pre_walk_total={pre_walk_total} "
        f"actual_done={actual_done} gap={gap} (expected 0; the over-count is the "
        f"ignored/empty/undecodable file count — the renderer clamps regardless)"
    )
