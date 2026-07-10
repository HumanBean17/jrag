"""Unit tests for iterative `_build_meta_chain` and `collect_annotation_meta_chain`."""
from __future__ import annotations

from java_codebase_rag.graph.graph_enrich import (
    AnnotationDecl,
    _build_meta_chain,
    _meta_builtins,
    collect_annotation_meta_chain,
)


def test_iterative_build_cycle_ab_terminates() -> None:
    a = "A"
    b = "B"
    decls = {
        a: AnnotationDecl("p.A", a, (b,)),
        b: AnnotationDecl("p.B", b, (a,)),
    }
    bset = _meta_builtins()
    m = _build_meta_chain(decls, bset, max_depth=4)
    assert m[a] == frozenset() and m[b] == frozenset()


def test_iterative_build_six_hop_line_no_service_for_w1() -> None:
    """W1->…->W6->@Service: 4 closure rounds are not enough to reach Service from W1."""
    decls: dict[str, AnnotationDecl] = {}
    for i in range(1, 6):
        nxt = f"W{i + 1}"
        decls[f"W{i}"] = AnnotationDecl(
            f"m.W{i}", f"W{i}", (nxt,),
        )
    decls["W6"] = AnnotationDecl("m.W6", "W6", ("Service",))
    bset = _meta_builtins()
    m = _build_meta_chain(decls, bset, max_depth=4)
    assert "Service" not in m.get("W1", ())


def test_collect_annotation_meta_chain_cache_independent_roots(
    tmp_path, tmp_path_factory,
) -> None:
    """Two project roots in one process get separate cached chains (Fix 1 acceptance)."""
    r1 = tmp_path
    r2 = tmp_path_factory.mktemp("p2")
    a1 = r1 / "A.java"
    a1.write_text("package a; @interface A {}\n", encoding="utf-8")
    a2 = r2 / "A.java"
    a2.write_text("package a; @interface A {}\n", encoding="utf-8")

    collect_annotation_meta_chain.cache_clear()
    c1 = collect_annotation_meta_chain(str(r1.resolve()))
    c2 = collect_annotation_meta_chain(str(r2.resolve()))
    assert c1 is not c2
    # Second call is cached (same id)
    assert collect_annotation_meta_chain(str(r1.resolve())) is c1


def test_collect_annotation_meta_chain_deterministic(
    tmp_path: object,
) -> None:
    """Same tree twice → identical meta map (Fix 6)."""
    a = tmp_path / "B.java"  # out of order path names on purpose
    a.write_text("package x; @interface B {}\n", encoding="utf-8")
    b = tmp_path / "A.java"
    b.write_text("package x; @interface A {}\n", encoding="utf-8")

    collect_annotation_meta_chain.cache_clear()
    r = str(tmp_path.resolve())
    m1 = collect_annotation_meta_chain(r)
    m2 = collect_annotation_meta_chain(r)
    assert m1 == m2
    # Force recompute: clear and rebuild
    collect_annotation_meta_chain.cache_clear()
    m3 = collect_annotation_meta_chain(r)
    assert m1 == m3
