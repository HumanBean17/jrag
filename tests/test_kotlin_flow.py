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
