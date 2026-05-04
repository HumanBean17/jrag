"""PR-A3 brownfield route overrides + @CodebaseRoute composition (B2a)."""
from __future__ import annotations

import io
import shutil
from contextlib import redirect_stderr
from pathlib import Path

import kuzu
import pytest

from graph_enrich import _load_brownfield_overrides, collect_annotation_meta_chain

STUB_ROOT = Path(__file__).resolve().parent / "fixtures" / "brownfield_route_stubs"


@pytest.fixture(autouse=True)
def _clear_route_brownfield_caches() -> object:
    yield
    _load_brownfield_overrides.cache_clear()
    collect_annotation_meta_chain.cache_clear()


def _copy_stubs(dest: Path) -> None:
    shutil.copytree(STUB_ROOT, dest, dirs_exist_ok=True)


def _build(tmp: Path, yml: str | None, extra_files: dict[str, str]) -> Path:
    if yml is not None:
        (tmp / ".lancedb-mcp.yml").write_text(yml, encoding="utf-8")
    _copy_stubs(tmp)
    for rel, body in extra_files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    from build_ast_graph import (
        GraphTables,
        pass1_parse,
        pass2_edges,
        pass3_calls,
        pass4_routes,
        write_kuzu,
    )

    db_path = tmp / "g.kuzu"
    tables = GraphTables()
    asts = pass1_parse(tmp, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    pass4_routes(tables, asts, source_root=tmp, verbose=False)
    write_kuzu(db_path, tables, source_root=tmp, verbose=False)
    return db_path


def _route_paths(db_path: Path) -> list[str]:
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    r = conn.execute("MATCH (rt:Route) RETURN rt.path ORDER BY rt.path")
    out: list[str] = []
    while r.has_next():
        out.append(str(r.get_next()[0] or ""))
    return out


def _route_ids(db_path: Path) -> list[str]:
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    r = conn.execute("MATCH (rt:Route) RETURN rt.id ORDER BY rt.id")
    out: list[str] = []
    while r.has_next():
        out.append(str(r.get_next()[0]))
    return out


def _meta(db_path: Path) -> dict:
    from kuzu_queries import KuzuGraph

    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    return KuzuGraph.get(str(db_path)).meta()


def test_19_layer_b_annotation_route(tmp_path: Path) -> None:
    yml = """
route_overrides:
  annotations:
    ann.AcmeRoute:
      framework: spring_mvc
      kind: http_endpoint
      method: GET
      path: /acme
"""
    java = {
        "ann/AcmeRoute.java": (
            "package ann;\nimport java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME)\n@Target({ElementType.METHOD})\n"
            "public @interface AcmeRoute {}\n"
        ),
        "p/Api.java": (
            "package p;\nimport ann.AcmeRoute;\n"
            "public class Api { @AcmeRoute void m() {} }\n"
        ),
    }
    db = _build(tmp_path, yml, java)
    assert "/acme" in _route_paths(db)


def test_20_layer_b_fqn_seeds_all_methods(tmp_path: Path) -> None:
    yml = """
route_overrides:
  fqn:
    com.legacy.UserApi:
      framework: spring_mvc
      kind: http_endpoint
      path: /legacy/users
      method: GET
"""
    java = {
        "com/legacy/UserApi.java": (
            "package com.legacy;\npublic class UserApi {\n"
            "  void a() {}\n  void b() {}\n}\n"
        ),
    }
    db = _build(tmp_path, yml, java)
    conn = kuzu.Connection(kuzu.Database(str(db), read_only=True))
    r = conn.execute(
        "MATCH (s:Symbol)-[:EXPOSES]->(r:Route) "
        "WHERE r.path = '/legacy/users' RETURN count(*)",
    )
    n = r.get_next()[0]
    assert int(n) >= 2


def test_21_layer_a_meta_rest_controller(tmp_path: Path) -> None:
    java = {
        "ann/AcmeRestController.java": (
            "package ann;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "import java.lang.annotation.*;\n"
            "@RestController @Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)\n"
            "public @interface AcmeRestController {}\n"
        ),
        "p/Ctrl.java": (
            "package p;\nimport ann.AcmeRestController;\n"
            "import org.springframework.web.bind.annotation.GetMapping;\n"
            "@AcmeRestController\npublic class Ctrl {\n"
            "  @GetMapping(\"/meta-x\") String m() { return \"\"; }\n}\n"
        ),
    }
    db = _build(tmp_path, None, java)
    assert "/meta-x" in _route_paths(db)


def test_22_layer_c_codebase_route(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p;\n"
            "import com.example.rag.*;\n"
            "public class X {\n"
            "  @CodebaseRoute(framework = CodebaseRouteFrameworkKind.spring_mvc, "
            "kind = CodebaseRouteKind.http_endpoint, path = \"/stub22\")\n"
            "  void m() {}\n}\n"
        ),
    }
    db = _build(tmp_path, None, java)
    assert "/stub22" in _route_paths(db)


def test_23_layer_c_wins_over_get_mapping(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p;\n"
            "import com.example.rag.*;\n"
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\npublic class X {\n"
            "  @GetMapping(\"/a\")\n"
            "  @CodebaseRoute(framework = CodebaseRouteFrameworkKind.spring_mvc, "
            "kind = CodebaseRouteKind.http_endpoint, path = \"/b\")\n"
            "  String m() { return \"\"; }\n}\n"
        ),
    }
    db = _build(tmp_path, None, java)
    paths = _route_paths(db)
    assert "/b" in paths
    assert "/a" not in paths or paths.count("/b") >= 1


def test_24_codebase_routes_repeatable(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p;\nimport com.example.rag.*;\nimport org.springframework.web.bind.annotation.*;\n"
            "@RestController\npublic class X {\n"
            "  @CodebaseRoute(framework = CodebaseRouteFrameworkKind.spring_mvc, "
            "kind = CodebaseRouteKind.http_endpoint, path = \"/r1\")\n"
            "  void m() {}\n"
            "  @CodebaseRoute(framework = CodebaseRouteFrameworkKind.spring_mvc, "
            "kind = CodebaseRouteKind.http_endpoint, path = \"/r2\")\n"
            "  void n() {}\n}\n"
        ),
    }
    db = _build(tmp_path, None, java)
    paths = set(_route_paths(db))
    assert "/r1" in paths and "/r2" in paths


def test_25_layer_b_fqn_wins_over_codebase_route(tmp_path: Path) -> None:
    yml = """
route_overrides:
  fqn:
    p.X:
      framework: spring_mvc
      kind: http_endpoint
      path: /from-yaml
      method: GET
"""
    java = {
        "p/X.java": (
            "package p;\n"
            "import com.example.rag.*;\n"
            "public class X {\n"
            "  @CodebaseRoute(framework = CodebaseRouteFrameworkKind.spring_mvc, "
            "kind = CodebaseRouteKind.http_endpoint, path = \"/from-code\")\n"
            "  void m() {}\n}\n"
        ),
    }
    db = _build(tmp_path, yml, java)
    assert "/from-yaml" in _route_paths(db)


def test_26_missing_config_no_error(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p;\nimport org.springframework.web.bind.annotation.*;\n"
            "@RestController\npublic class X {\n @GetMapping(\"/ok\") void m() {}\n}\n"
        ),
    }
    db = _build(tmp_path, None, java)
    assert "/ok" in _route_paths(db)


def test_27_unknown_framework_stderr(tmp_path: Path) -> None:
    yml = """
route_overrides:
  annotations:
    ann.Z:
      framework: not_a_real_framework
      kind: http_endpoint
"""
    java = {
        "ann/Z.java": (
            "package ann;\nimport java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD) "
            "public @interface Z {}\n"
        ),
        "p/X.java": "package p;\nimport ann.Z;\npublic class X { @Z void m() {} }\n",
    }
    buf = io.StringIO()
    with redirect_stderr(buf):
        _build(tmp_path, yml, java)
    st = buf.getvalue().lower()
    assert "unknown framework" in st and "not_a_real_framework" in st


def test_28_vanilla_get_mapping_ignores_unrelated_overrides(tmp_path: Path) -> None:
    yml = """
route_overrides:
  annotations:
    unused.Unused:
      framework: spring_mvc
      kind: http_endpoint
      path: /unused-only
"""
    java = {
        "unused/Unused.java": (
            "package unused;\nimport java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD) "
            "public @interface Unused {}\n"
        ),
        "p/X.java": (
            "package p;\nimport org.springframework.web.bind.annotation.*;\n"
            "@RestController\npublic class X {\n @GetMapping(\"/vanilla\") void m() {}\n}\n"
        ),
    }
    ra = tmp_path / "a"
    rb = tmp_path / "b"
    ra.mkdir(parents=True, exist_ok=True)
    rb.mkdir(parents=True, exist_ok=True)
    a = _build(ra, None, {"p/X.java": java["p/X.java"]})
    b = _build(rb, yml, java)
    assert _route_ids(a) == _route_ids(b)


def test_29_determinism_pass4_route_ids(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p;\nimport org.springframework.web.bind.annotation.*;\n"
            "@RestController\npublic class X {\n @GetMapping(\"/d\") void m() {}\n}\n"
        ),
    }
    db1 = _build(tmp_path / "r1", None, java)
    db2 = _build(tmp_path / "r2", None, java)
    assert _route_ids(db1) == _route_ids(db2)


def test_30_graph_meta_routes_from_brownfield_pct(tmp_path: Path) -> None:
    yml = """
route_overrides:
  annotations:
    ann.Mark:
      framework: spring_mvc
      kind: http_endpoint
      method: GET
      path: /bf-only
"""
    java = {
        "ann/Mark.java": (
            "package ann;\nimport java.lang.annotation.*;\n"
            "@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD) "
            "public @interface Mark {}\n"
        ),
        "p/X.java": (
            "package p;\nimport ann.Mark;\nimport org.springframework.web.bind.annotation.*;\n"
            "@RestController\npublic class X {\n"
            "  @Mark void a() {}\n"
            "  @GetMapping(\"/builtin\") void b() {}\n}\n"
        ),
    }
    db = _build(tmp_path, yml, java)
    m = _meta(db)
    pct = float(m.get("routes_from_brownfield_pct") or 0.0)
    assert pct > 0.0
    by_layer = m.get("routes_by_layer") or {}
    assert isinstance(by_layer, dict)
    assert int(by_layer.get("layer_b_ann", 0)) >= 1
