"""Tests for the language-dispatch seam (`ast.language`).

Task 1 establishes the registry with Java only; Kotlin is added in later
tasks. These tests pin the contract that downstream language dispatch relies on.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr

import pytest

from java_codebase_rag.ast.ast_java import JavaFileAst
from java_codebase_rag.ast.language import (
    FileAst,
    JavaBackend,
    KNOWN_LANGUAGE_IDS,
    LANG_BACKENDS,
    backend_for,
)


def test_backend_for_java_file_returns_java_backend() -> None:
    """(a) `.java` suffix resolves to the registered Java backend."""
    backend = backend_for("src/Foo.java")
    assert backend is not None
    assert backend.language_id == "java"


def test_backend_for_kotlin_file_conditional_on_grammar() -> None:
    """(b) `.kt` resolves to KotlinBackend iff the grammar wheel imports.

    Task 5 registers KotlinBackend behind a ``try: import tree_sitter_kotlin``
    guard: present when the grammar installs, absent (so ``backend_for``
    returns ``None``) on minimal/graph-only installs.
    """
    import importlib.util

    if importlib.util.find_spec("tree_sitter_kotlin") is None:
        assert backend_for("Foo.kt") is None
        return
    backend = backend_for("Foo.kt")
    assert backend is not None
    assert backend.language_id == "kotlin"


def test_backend_for_unrelated_suffix_returns_none() -> None:
    """(c) Non-source files never match a language backend."""
    assert backend_for("README.md") is None


def test_java_backend_parse_sets_language() -> None:
    """(d) `JavaBackend.parse` delegates to `parse_java` and tags language."""
    ast = JavaBackend().parse(b"package x; class F {}", filename="F.java")
    assert isinstance(ast, JavaFileAst)
    assert ast.language == "java"
    # Delegated parser actually parsed the unit, not just an empty stub.
    assert any(t.name == "F" for t in ast.all_types)


def test_javafileast_rejects_unknown_language() -> None:
    """(e) `language` must be one of the registered language ids."""
    with pytest.raises(ValueError):
        JavaFileAst(
            package="x",
            imports=[],
            wildcard_imports=[],
            explicit_imports={},
            top_level_types=[],
            all_types=[],
            language="nope",
        )


def test_javafileast_language_is_required() -> None:
    """(f) `language` has no default — omitting it is a TypeError."""
    with pytest.raises(TypeError):
        JavaFileAst(
            package="x",
            imports=[],
            wildcard_imports=[],
            explicit_imports={},
            top_level_types=[],
            all_types=[],
        )  # type: ignore[call-arg]


def test_fileast_alias_is_javafileast() -> None:
    """(g) `FileAst` is a direct alias for `JavaFileAst` (single-language era)."""
    assert FileAst is JavaFileAst


def test_registry_and_known_ids() -> None:
    """Registry invariants: Java always registered; Kotlin when grammar imports; ids derived from keys."""
    import importlib.util

    assert "java" in LANG_BACKENDS
    assert "java" in KNOWN_LANGUAGE_IDS
    assert LANG_BACKENDS["java"].language_id == "java"
    assert ".java" in LANG_BACKENDS["java"].suffixes
    # Kotlin is conditionally registered (Task 5): present iff the grammar imports.
    if importlib.util.find_spec("tree_sitter_kotlin") is not None:
        assert "kotlin" in LANG_BACKENDS
        assert "kotlin" in KNOWN_LANGUAGE_IDS
        assert LANG_BACKENDS["kotlin"].language_id == "kotlin"
        assert ".kt" in LANG_BACKENDS["kotlin"].suffixes
    else:
        assert "kotlin" not in LANG_BACKENDS
        assert "kotlin" not in KNOWN_LANGUAGE_IDS
    # KNOWN_LANGUAGE_IDS never drifts from the registry keys.
    assert KNOWN_LANGUAGE_IDS == frozenset(LANG_BACKENDS.keys())


def test_parse_sites_dispatch_through_backend_for(monkeypatch, tmp_path) -> None:
    """Pin the dispatch contract the four parse sites rely on (Task 3).

    Each site must call ``backend_for(path).parse(content, filename=path)``
    so a newly-registered backend (Kotlin in later tasks) is routed to
    automatically. We register a recording stub for ``.java`` and confirm:
      (a) the inline dispatch selects the stub — the exact pattern the sites use;
      (b) a real parse site (``graph_enrich._collect_annotation_decl_index``)
          routes through ``backend_for(...).parse(...)`` rather than calling
          ``parse_java`` directly. Before Task 3 the real site called
          ``parse_java`` directly and the stub was never recorded.
    """
    class _RecordingBackend:
        language_id = "java"
        suffixes = (".java",)

        def __init__(self) -> None:
            self.recorded: list[str] = []

        def parse(self, source, *, filename: str = "", verbose: bool = False) -> JavaFileAst:
            self.recorded.append(filename)
            return JavaFileAst(
                package="",
                imports=[],
                wildcard_imports=[],
                explicit_imports={},
                top_level_types=[],
                all_types=[],
                language="java",
            )

    stub = _RecordingBackend()
    monkeypatch.setattr(
        "java_codebase_rag.ast.language.LANG_BACKENDS", {"java": stub}
    )

    # (a) Inline contract — mirrors the dispatch at each of the four sites.
    assert backend_for("src/Foo.java") is stub
    ast = backend_for("src/Foo.java").parse(b"", filename="src/Foo.java")
    assert "src/Foo.java" in stub.recorded
    assert isinstance(ast, JavaFileAst)

    # (b) Real-site pin — graph_enrich must dispatch through the registry.
    from java_codebase_rag.graph.graph_enrich import (
        _collect_annotation_decl_index,
    )

    (tmp_path / "Foo.java").write_bytes(b"@interface Foo {}")
    stub.recorded.clear()
    _collect_annotation_decl_index(str(tmp_path))
    assert any(fn.endswith("Foo.java") for fn in stub.recorded), (
        "_collect_annotation_decl_index did not route through "
        "backend_for(...).parse(); it may still call parse_java directly."
    )


def test_java_backend_parse_forwards_verbose_to_parse_java(monkeypatch) -> None:
    """`JavaBackend.parse(..., verbose=True)` forwards `verbose=True` to `parse_java`.

    Task-3 fix: ``pass1_parse`` dispatches through ``backend.parse(..., verbose=verbose)``.
    ``parse_java``'s ``verbose`` gates ``_maybe_emit_brownfield_exclusivity_shadowing``
    (an INFO stderr event) via the ``_ParseCtx``, so forwarding must be preserved
    end-to-end or the brownfield-exclusivity-shadowing diagnostic is silently lost
    on default graph builds (``pipeline.py`` passes ``--verbose`` in DEFAULT mode).
    """
    captured: dict[str, object] = {}

    def _fake_parse_java(source, *, filename: str = "", verbose: bool = False) -> JavaFileAst:
        captured["verbose"] = verbose
        captured["filename"] = filename
        return JavaFileAst(
            package="",
            imports=[],
            wildcard_imports=[],
            explicit_imports={},
            top_level_types=[],
            all_types=[],
            language="java",
        )

    monkeypatch.setattr("java_codebase_rag.ast.language.parse_java", _fake_parse_java)

    # verbose=True must reach parse_java.
    JavaBackend().parse(b"package x; class F {}", filename="F.java", verbose=True)
    assert captured.get("verbose") is True

    # Default is False — the other three parse sites (jrag, graph_enrich, index
    # flow) rely on this so they never emit the diagnostic unexpectedly.
    captured.clear()
    JavaBackend().parse(b"package x; class F {}", filename="F.java")
    assert captured.get("verbose") is False


def test_backend_parse_verbose_restores_brownfield_shadowing_event() -> None:
    """End-to-end: the brownfield-exclusivity-shadowing INFO event fires again
    when parsing real co-present source through ``JavaBackend().parse(..., verbose=True)``
    — the exact dispatch path ``pass1_parse`` uses after the Task-3 fix.

    Before the fix, ``pass1_parse`` dropped ``verbose`` when it switched to
    ``backend.parse(content, filename=rel)``, so this INFO event was silently lost
    on default graph builds (``pipeline.py`` passes ``--verbose`` in DEFAULT mode).
    """
    src = """
package x;
import com.example.rag.*;
import org.springframework.web.bind.annotation.*;
@RestController
class C {
  @GetMapping("/p")
  @CodebaseHttpRoute(path = "/bf", method = CodebaseHttpMethod.GET)
  String m() { return ""; }
}
"""
    # verbose=True → the INFO event must reach stderr through backend.parse.
    buf = io.StringIO()
    with redirect_stderr(buf):
        JavaBackend().parse(src.encode(), filename="C.java", verbose=True)
    shadow_lines = [
        ln for ln in buf.getvalue().splitlines()
        if "brownfield-exclusivity-shadowing" in ln
    ]
    assert shadow_lines, (
        "brownfield-exclusivity-shadowing INFO event was not emitted via "
        "backend.parse(..., verbose=True); verbose was not forwarded to parse_java"
    )
    rec = json.loads(shadow_lines[0])
    assert rec["event"] == "brownfield-exclusivity-shadowing"
    assert rec["severity"] == "INFO"
    assert "GetMapping" in rec["shadowed_framework_annotations"]

    # verbose=False (default) → the event must stay silent.
    buf2 = io.StringIO()
    with redirect_stderr(buf2):
        JavaBackend().parse(src.encode(), filename="C.java")
    assert not any(
        "brownfield-exclusivity-shadowing" in ln
        for ln in buf2.getvalue().splitlines()
    )
