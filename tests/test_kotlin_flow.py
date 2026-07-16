"""Task 11: Kotlin is wired into the cocoindex LanceDB flow.

Builds a tiny mixed-language module (one ``.java`` ``@Service`` + one ``.kt``
``@RestController`` that injects it), runs the real cocoindex flow against a
fresh temp index, and asserts:

(a) the LanceDB chunk table holds chunks from BOTH files,
(b) the Kotlin controller chunk carries ``language == "kotlin"``,
(c) ``_approximate_vectors_total`` counts ``.java`` + ``.kt`` (not just ``.java``).

Cross-language edge assertions are Task 15 — here we only prove both languages
index into the same Lance table.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

lancedb = pytest.importorskip("lancedb")
pytest.importorskip("tree_sitter_kotlin")


def _require_cocoindex_runtime_deps() -> None:
    """cocoindex loads java_index_flow_lancedb.py with the same Python as the CLI."""
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            "Test needs project deps in the current env (e.g. ``pip install -r requirements*``"
            f" in the venv you use to run pytest): {exc}"
        )


def _cocoindex_flow_specifier(bundle_dir: Path, index_cwd: Path) -> str:
    import os as _os

    flow_file = (bundle_dir / "java_index_flow_lancedb.py").resolve()
    if not flow_file.is_file():
        raise FileNotFoundError(f"missing index flow: {flow_file}")
    start = index_cwd.resolve()
    relp = _os.path.relpath(str(flow_file), start=str(start))
    relp = Path(relp).as_posix()
    return f"{relp}:JavaCodeIndexLance"


_JAVA_SERVICE = """\
package com.example;

import org.springframework.stereotype.Service;

@Service
public class GreetingService {
    public String greet(String name) {
        return "Hello " + name;
    }
}
"""

_KOTLIN_CONTROLLER = """\
package com.example

import org.springframework.web.bind.annotation.RestController
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestParam

@RestController
class GreetingController(val service: GreetingService) {

    @GetMapping("/greet")
    fun greet(@RequestParam name: String): String {
        return service.greet(name)
    }
}
"""


def _write_mixed_module(root: Path) -> tuple[Path, Path]:
    """Write one .java @Service + one .kt @RestController under ``root``."""
    java_file = root / "GreetingService.java"
    kt_file = root / "GreetingController.kt"
    java_file.write_text(_JAVA_SERVICE, encoding="utf-8")
    kt_file.write_text(_KOTLIN_CONTROLLER, encoding="utf-8")
    return java_file, kt_file


def _run_cocoindex(corpus: Path, index_dir: Path, bundle_dir: Path) -> None:
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(
            f"cocoindex CLI not found next to the pytest interpreter ({cocoindex_bin})"
        )
    app_spec = _cocoindex_flow_specifier(
        bundle_dir / "src" / "java_codebase_rag" / "index", corpus
    )
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
    }
    proc = subprocess.run(
        [
            str(cocoindex_bin),
            "update",
            app_spec,
            "--full-reprocess",
            "-f",
        ],
        cwd=str(corpus),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"cocoindex failed (rc={proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


@pytest.fixture()
def mixed_index(tmp_path: Path) -> Path:
    """Fresh temp corpus + fresh Lance index over one .java + one .kt file."""
    _require_cocoindex_runtime_deps()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_mixed_module(corpus)
    index_dir = tmp_path / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    bundle_dir = Path(__file__).resolve().parent.parent
    _run_cocoindex(corpus, index_dir, bundle_dir)
    return index_dir


def test_kotlin_and_java_chunks_both_indexed(mixed_index: Path) -> None:
    """(a) The Lance table holds chunks from BOTH the .java and the .kt file."""
    db = lancedb.connect(str(mixed_index))
    table = db.open_table("javacodeindex_java_code")
    arrow = table.to_arrow()
    filenames = {Path(f).as_posix() for f in arrow.column("filename").to_pylist()}
    assert any(f.endswith("GreetingService.java") for f in filenames), filenames
    assert any(f.endswith("GreetingController.kt") for f in filenames), filenames


def test_kotlin_chunk_tagged_kotlin_language(mixed_index: Path) -> None:
    """(b) The Kotlin controller chunk carries ``language == 'kotlin'``."""
    db = lancedb.connect(str(mixed_index))
    table = db.open_table("javacodeindex_java_code")
    arrow = table.search().where(
        "filename LIKE '%GreetingController.kt'"
    ).to_arrow()
    assert arrow.num_rows > 0, "no chunks found for GreetingController.kt"
    languages = {row.as_py() for row in arrow.column("language")}
    assert "kotlin" in languages, languages


def test_approximate_vectors_total_counts_java_and_kotlin(tmp_path: Path) -> None:
    """(c) ``_approximate_vectors_total`` returns the .java + .kt count (2 here)."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_mixed_module(corpus)
    # Import the flow module (cocoindex is a dep) and exercise the pure-Python
    # pre-walk counter directly — no subprocess needed for this unit-level check.
    from java_codebase_rag.index.java_index_flow_lancedb import (
        _approximate_vectors_total,
    )

    assert _approximate_vectors_total(corpus) == 2


def test_grammar_absent_flow_skips_kotlin_cleanly(tmp_path: Path) -> None:
    """Grammar-absent install must SKIP ``.kt`` files cleanly — no crash.

    ``app_main`` registers the ``.kt`` cocoindex matcher + ``process_kotlin_file``
    drain only when ``_KOTLIN_REGISTERED`` (registry-derived from ``LANG_BACKENDS``).
    On a grammar-absent install ``backend_for('.kt')`` returns ``None``; before the
    gate, a ``.kt`` reaching ``process_kotlin_file`` → ``_parse_and_enrich_java``
    returned ``([], None)`` → ``classify_java_file`` dereferenced ``ast.all_types``
    on ``None`` → ``AttributeError``. This test simulates grammar-absence by
    monkeypatching the registry-derived constants and proves the pre-walk skips
    ``.kt`` (count excludes it, no exception) and the dispatch guard holds.
    """
    import java_codebase_rag.index.java_index_flow_lancedb as flow

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_mixed_module(corpus)

    # Sanity: with the grammar present (the only way this file is collected — it
    # importorskips tree_sitter_kotlin) the gate is ON and both files are counted.
    assert flow._KOTLIN_REGISTERED is True
    assert flow._INDEXED_SOURCE_SUFFIXES == (".java", ".kt")
    assert flow._approximate_vectors_total(corpus) == 2

    # Simulate a grammar-absent install: registry has no ``.kt`` backend, so the
    # derived suffix tuple drops to ``(".java",)`` and the gate turns OFF.
    saved_suffixes = flow._INDEXED_SOURCE_SUFFIXES
    saved_gate = flow._KOTLIN_REGISTERED
    flow._INDEXED_SOURCE_SUFFIXES = (".java",)
    flow._KOTLIN_REGISTERED = False
    try:
        # The pre-walk now counts ONLY the .java file (clean skip of .kt) and
        # raises no exception — mirroring how the gated matcher/drain in app_main
        # never yields .kt to process_kotlin_file.
        assert flow._approximate_vectors_total(corpus) == 1
    finally:
        flow._INDEXED_SOURCE_SUFFIXES = saved_suffixes
        flow._KOTLIN_REGISTERED = saved_gate


def test_grammar_absent_parse_guard_returns_empty(monkeypatch, tmp_path: Path) -> None:
    """Last line of defense: if a ``.kt`` somehow reached ``_parse_and_enrich_java``
    with no registered backend, it returns ``([], None)`` cleanly (no AttributeError)
    rather than letting ``classify_java_file`` deref ``ast.all_types`` on None."""
    import java_codebase_rag.index.java_index_flow_lancedb as flow

    # backend_for('.kt') returns None when the kotlin grammar is absent.
    monkeypatch.setattr(flow, "backend_for", lambda rel: None)
    enrichments, ast = flow._parse_and_enrich_java(
        b"package com.x\n", [], "Foo.kt", tmp_path  # type: ignore[arg-type]
    )
    assert enrichments == []
    assert ast is None  # the caller (process_kotlin_file) would skip enrichment.
