from __future__ import annotations

import shutil
from pathlib import Path

import ladybug

from ast_java import ONTOLOGY_VERSION
from graph_enrich import _load_brownfield_overrides, collect_annotation_meta_chain

STUB_ROOT = Path(__file__).resolve().parent / "fixtures" / "brownfield_client_stubs"


def _copy_stubs(dest: Path) -> None:
    shutil.copytree(STUB_ROOT, dest, dirs_exist_ok=True)


def _build(tmp: Path, yml: str | None, extra_files: dict[str, str]) -> Path:
    if yml is not None:
        (tmp / ".java-codebase-rag.yml").write_text(yml, encoding="utf-8")
    _copy_stubs(tmp)
    for rel, body in extra_files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    from _builders import build_ladybug_full_into

    db_path = tmp / "g.lbug"
    build_ladybug_full_into(tmp, db_path)
    return db_path


def _rows(db_path: Path, query: str) -> list[tuple]:
    db = ladybug.Database(str(db_path), read_only=True)
    conn = ladybug.Connection(db)
    r = conn.execute(query)
    out: list[tuple] = []
    while r.has_next():
        out.append(tuple(r.get_next()))
    return out


def test_client_rows_emitted_for_codebase_client_on_interface_abstract_method(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        None,
        {
            "p/Api.java": (
                "package p; import com.example.rag.*; "
                "public interface Api { "
                "@CodebaseHttpClient(clientKind=CodebaseClientKind.feign_method, targetService=\"user-svc\", "
                "path=\"/users/{id}\", method=CodebaseHttpMethod.GET) "
                "Object getUser(String id); }"
            ),
        },
    )
    rows = _rows(
        db,
        "MATCH (c:Client) RETURN c.client_kind, c.target_service, c.path, c.method, c.source_layer",
    )
    assert any(
        row[0] == "feign_method"
        and row[1] == "user-svc"
        and row[2] == "/users/{id}"
        and row[3] == "GET"
        and row[4] == "layer_c_source"
        for row in rows
    )


def test_client_rows_emitted_for_codebase_client_on_abstract_class_method(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        None,
        {
            "p/Base.java": (
                "package p; import com.example.rag.*; "
                "public abstract class Base { "
                "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/abs\", method=CodebaseHttpMethod.POST) "
                "abstract void pull(); }"
            ),
        },
    )
    rows = _rows(
        db,
        "MATCH (c:Client) RETURN c.client_kind, c.path, c.method, c.source_layer",
    )
    assert any(
        row[0] == "rest_template" and row[1] == "/abs" and row[2] == "POST" and row[3] == "layer_c_source"
        for row in rows
    )


def test_client_rows_emitted_for_codebase_client_annotations(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        None,
        {
            "p/X.java": (
                "package p; import com.example.rag.*; class X { "
                "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, targetService=\"chat-core\", path=\"/x\", method=CodebaseHttpMethod.POST) "
                "void m() {} }"
            ),
        },
    )
    rows = _rows(
        db,
        "MATCH (c:Client) RETURN c.client_kind, c.target_service, c.path, c.method, c.source_layer",
    )
    assert any(
        row[0] == "rest_template"
        and row[1] == "chat-core"
        and row[2] == "/x"
        and row[3] == "POST"
        and row[4] == "layer_c_source"
        for row in rows
    )


def test_client_rows_synthesized_for_feign_methods(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        None,
        {
            "p/Api.java": (
                "package p; "
                "import org.springframework.cloud.openfeign.FeignClient; "
                "import org.springframework.web.bind.annotation.GetMapping; "
                "@FeignClient(name=\"user-svc\", path=\"/users\") interface Api { "
                "@GetMapping(\"/{id}\") Object get(String id); }"
            ),
        },
    )
    rows = _rows(
        db,
        "MATCH (c:Client) WHERE c.client_kind = 'feign_method' "
        "RETURN c.target_service, c.path, c.method",
    )
    assert any(row[0] == "user-svc" and row[1] == "/users/{id}" and row[2] == "GET" for row in rows)


def test_declares_client_edge_targets_client_id(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        None,
        {
            "p/X.java": (
                "package p; import com.example.rag.*; class X { "
                "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/edge\", method=CodebaseHttpMethod.GET) void m() {} }"
            ),
        },
    )
    rows = _rows(
        db,
        "MATCH (s:Symbol)-[e:DECLARES_CLIENT]->(c:Client) "
        "RETURN s.id, c.member_id, c.id, e.strategy",
    )
    assert rows
    assert any(row[0] == row[1] and row[3] == "layer_c_source" for row in rows)


def test_client_id_is_deterministic_across_rebuilds(tmp_path: Path) -> None:
    files = {
        "p/X.java": (
            "package p; import com.example.rag.*; class X { "
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, targetService=\"svc\", path=\"/stable\", method=CodebaseHttpMethod.GET) "
            "void m() {} }"
        ),
    }
    db1 = _build(tmp_path / "a", None, files)
    db2 = _build(tmp_path / "b", None, files)
    ids1 = {row[0] for row in _rows(db1, "MATCH (c:Client) RETURN c.id")}
    ids2 = {row[0] for row in _rows(db2, "MATCH (c:Client) RETURN c.id")}
    assert ids1 == ids2


def test_client_source_layer_reflects_winning_override_layer(tmp_path: Path) -> None:
    yml = """
http_client_overrides:
  fqn:
    p.X:
      client_kind: rest_template
      target_service: svc-yaml
      path: /yaml
      method: PUT
"""
    db = _build(
        tmp_path,
        yml,
        {
            "p/X.java": (
                "package p; import com.example.rag.*; class X { "
                "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, targetService=\"svc-source\", path=\"/source\", method=CodebaseHttpMethod.GET) "
                "void m() {} }"
            ),
        },
    )
    rows = _rows(
        db,
        "MATCH (c:Client) RETURN c.target_service, c.path, c.method, c.source_layer",
    )
    assert any(row[0] == "svc-yaml" and row[1] == "/yaml" and row[2] == "PUT" and row[3] == "layer_b_fqn" for row in rows)


def test_client_schema_persisted_and_queryable(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        None,
        {
            "p/X.java": (
                "package p; import com.example.rag.*; class X { "
                "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/meta\", method=CodebaseHttpMethod.GET) void m() {} }"
            ),
        },
    )
    tables = {row[1] for row in _rows(db, "CALL show_tables() RETURN *")}
    assert "Client" in tables
    assert "DECLARES_CLIENT" in tables
    meta_rows = _rows(
        db,
        "MATCH (m:GraphMeta) RETURN m.ontology_version, m.clients_total, m.declares_client_total, m.clients_by_kind",
    )
    assert meta_rows
    assert int(meta_rows[0][0] or 0) == ONTOLOGY_VERSION
    assert int(meta_rows[0][1] or 0) >= 1
    assert int(meta_rows[0][2] or 0) >= 1


def test_graph_meta_counts_producers_and_declares_producer(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        None,
        {
            "p/X.java": (
                "package p; import com.example.rag.*; class X { "
                "@CodebaseProducer(topic=\"meta-topic\") void m() {} }"
            ),
        },
    )
    tables = {row[1] for row in _rows(db, "CALL show_tables() RETURN *")}
    assert "Producer" in tables
    assert "DECLARES_PRODUCER" in tables
    meta_rows = _rows(
        db,
        "MATCH (m:GraphMeta) RETURN m.producers_total, m.declares_producer_total, m.producers_by_kind",
    )
    assert meta_rows
    assert int(meta_rows[0][0] or 0) >= 1
    assert int(meta_rows[0][1] or 0) >= 1


def teardown_module() -> None:
    _load_brownfield_overrides.cache_clear()
    collect_annotation_meta_chain.cache_clear()
