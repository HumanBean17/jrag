"""PR-D2 brownfield client/producer composition (B2b caller-side mirror)."""
from __future__ import annotations

import io
import shutil
from contextlib import redirect_stderr
from pathlib import Path

import ladybug
import pytest

from java_codebase_rag.graph.graph_enrich import _load_brownfield_overrides, collect_annotation_meta_chain

STUB_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "brownfield_client_stubs"


@pytest.fixture(autouse=True)
def _clear_client_brownfield_caches() -> object:
    yield
    _load_brownfield_overrides.cache_clear()
    collect_annotation_meta_chain.cache_clear()


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
    from _builders import build_ladybug_imperative_into

    db_path = tmp / "g.lbug"
    build_ladybug_imperative_into(tmp, db_path)
    return db_path


def _http_calls(db_path: Path) -> list[dict]:
    db = ladybug.Database(str(db_path), read_only=True)
    conn = ladybug.Connection(db)
    r = conn.execute(
        "MATCH (c:Client)-[h:HTTP_CALLS]->(rt:Route) "
        "RETURN c.member_fqn AS fqn, h.strategy AS strategy, h.method_call AS method_call, "
        "rt.path_template AS path_template, rt.feign_name AS feign_name ORDER BY fqn, path_template",
    )
    out: list[dict] = []
    while r.has_next():
        row = r.get_next()
        out.append(
            {
                "fqn": str(row[0] or ""),
                "strategy": str(row[1] or ""),
                "method_call": str(row[2] or ""),
                "path_template": str(row[3] or ""),
                "feign_name": str(row[4] or ""),
            },
        )
    return out


def _async_calls(db_path: Path) -> list[dict]:
    db = ladybug.Database(str(db_path), read_only=True)
    conn = ladybug.Connection(db)
    r = conn.execute(
        "MATCH (pr:Producer)-[c:ASYNC_CALLS]->(rt:Route) "
        "RETURN pr.member_fqn AS fqn, c.strategy AS strategy, rt.topic AS topic ORDER BY fqn, topic",
    )
    out: list[dict] = []
    while r.has_next():
        row = r.get_next()
        out.append({"fqn": str(row[0] or ""), "strategy": str(row[1] or ""), "topic": str(row[2] or "")})
    return out


def _meta(db_path: Path) -> dict:
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph

    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    return LadybugGraph.get(str(db_path)).meta()


def test_20_layer_b_annotation_http_client(tmp_path: Path) -> None:
    yml = """
http_client_overrides:
  annotations:
    ann.LegacyHttpClient:
      client_kind: rest_template
      target_service: chat-core
      path: /legacy/http
      method: POST
"""
    java = {
        "ann/LegacyHttpClient.java": "package ann; import java.lang.annotation.*; @Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD) public @interface LegacyHttpClient {}",
        "p/X.java": "package p; import ann.LegacyHttpClient; class X { @LegacyHttpClient void m() {} }",
    }
    db = _build(tmp_path, yml, java)
    calls = _http_calls(db)
    assert any(c["path_template"] == "/legacy/http" and c["strategy"] == "layer_b_ann" for c in calls)


def test_21_layer_b_fqn_http_client(tmp_path: Path) -> None:
    yml = """
http_client_overrides:
  fqn:
    p.X:
      client_kind: rest_template
      path: /from-fqn
      method: GET
"""
    java = {
        "p/X.java": (
            "package p; import org.springframework.web.client.RestTemplate; "
            "class X { RestTemplate restTemplate; void m(){ restTemplate.postForEntity(\"/builtin\", null, String.class); } }"
        ),
    }
    db = _build(tmp_path, yml, java)
    calls = _http_calls(db)
    assert any(c["path_template"] == "/from-fqn" and c["strategy"] == "layer_b_fqn" for c in calls)


def test_22_layer_a_meta_annotation_chain(tmp_path: Path) -> None:
    yml = """
http_client_overrides:
  annotations:
    CodebaseHttpClient:
      client_kind: rest_template
      path: /meta-client
      method: GET
"""
    java = {
        "ann/LegacyHttpClient.java": "package ann; import java.lang.annotation.*; import com.example.rag.*; @CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/x\", method=CodebaseHttpMethod.GET) @Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD) public @interface LegacyHttpClient {}",
        "p/X.java": "package p; import ann.LegacyHttpClient; class X { @LegacyHttpClient void m() {} }",
    }
    db = _build(tmp_path, yml, java)
    calls = _http_calls(db)
    assert any(c["path_template"] == "/meta-client" and c["strategy"] == "layer_a_meta" for c in calls)


def test_23_layer_c_source_codebase_client(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p; import com.example.rag.*; class X { "
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/stub23\", method=CodebaseHttpMethod.PUT) void m() {} }"
        ),
    }
    db = _build(tmp_path, None, java)
    calls = _http_calls(db)
    assert any(c["path_template"] == "/stub23" and c["strategy"] == "layer_c_source" for c in calls)


def test_24_layer_c_source_codebase_producer(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p; import com.example.rag.*; class X { "
            "@CodebaseProducer(topic=\"stub24\") void m() {} }"
        ),
    }
    db = _build(tmp_path, None, java)
    calls = _async_calls(db)
    assert any(c["topic"] == "stub24" and c["strategy"] == "layer_c_source" for c in calls)


def test_25_repeatable_codebase_clients(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p; import com.example.rag.*; class X { "
            "@CodebaseHttpClients({"
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/r1\", method=CodebaseHttpMethod.GET),"
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/r2\", method=CodebaseHttpMethod.POST)"
            "}) void m() {} }"
        ),
    }
    db = _build(tmp_path, None, java)
    paths = {c["path_template"] for c in _http_calls(db)}
    assert "/r1" in paths and "/r2" in paths


def test_26_last_writer_wins_fqn_over_source(tmp_path: Path) -> None:
    yml = """
http_client_overrides:
  fqn:
    p.X:
      client_kind: rest_template
      path: /from-yaml
      method: GET
"""
    java = {
        "p/X.java": (
            "package p; import com.example.rag.*; class X { "
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/from-source\", method=CodebaseHttpMethod.POST) void m() {} }"
        ),
    }
    db = _build(tmp_path, yml, java)
    calls = _http_calls(db)
    assert any(c["path_template"] == "/from-yaml" and c["strategy"] == "layer_b_fqn" for c in calls)


def test_27_method_level_brownfield_replaces_builtin_http(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p; import org.springframework.web.client.RestTemplate; import com.example.rag.*; "
            "class X { RestTemplate restTemplate; "
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/override\", method=CodebaseHttpMethod.POST) "
            "void m(){ restTemplate.exchange(\"/builtin\", org.springframework.http.HttpMethod.GET, null, String.class); } }"
        ),
    }
    db = _build(tmp_path, None, java)
    calls = _http_calls(db)
    assert len(calls) == 1
    assert calls[0]["path_template"] == "/override"
    assert calls[0]["strategy"] == "layer_c_source"


def test_28_target_service_forced(tmp_path: Path) -> None:
    yml = """
http_client_overrides:
  fqn:
    p.X:
      client_kind: feign_method
      target_service: user-svc
      path: /fqn
      method: GET
"""
    java = {
        "p/X.java": "package p; class X { void m() {} }",
    }
    db = _build(tmp_path, yml, java)
    calls = _http_calls(db)
    assert any(c["feign_name"] == "user-svc" for c in calls)


def test_29_unknown_client_kind_warns_and_skips(tmp_path: Path) -> None:
    yml = """
http_client_overrides:
  annotations:
    ann.LegacyHttpClient:
      client_kind: nope
      path: /bad
"""
    java = {
        "ann/LegacyHttpClient.java": "package ann; import java.lang.annotation.*; @Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD) public @interface LegacyHttpClient {}",
        "p/X.java": "package p; import ann.LegacyHttpClient; class X { @LegacyHttpClient void m() {} }",
    }
    buf = io.StringIO()
    with redirect_stderr(buf):
        db = _build(tmp_path, yml, java)
    assert "unknown client_kind" in buf.getvalue().lower()
    assert _http_calls(db) == []


def _client_kinds(db_path: Path) -> list[str]:
    db = ladybug.Database(str(db_path), read_only=True)
    conn = ladybug.Connection(db)
    r = conn.execute("MATCH (c:Client) RETURN c.client_kind AS client_kind")
    out: list[str] = []
    while r.has_next():
        out.append(str(r.get_next()[0] or ""))
    return out


def _producer_kinds(db_path: Path) -> list[str]:
    db = ladybug.Database(str(db_path), read_only=True)
    conn = ladybug.Connection(db)
    r = conn.execute("MATCH (p:Producer) RETURN p.producer_kind AS producer_kind")
    out: list[str] = []
    while r.has_next():
        out.append(str(r.get_next()[0] or ""))
    return out


def test_29a_unknown_source_client_kind_warns_and_ignored(tmp_path: Path) -> None:
    """In-source @CodebaseHttpClient(clientKind=<invalid enum>) is validated at parse
    time (source-annotation mirror of the YAML-side test_29): the bad value is ignored
    and a warning is emitted, so client_kind stays a closed set safe to surface as an enum."""
    java = {
        "p/X.java": (
            "package p; import com.example.rag.*; class X { "
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.bogus, path=\"/bad\", method=CodebaseHttpMethod.GET) "
            "void m() {} }"
        ),
    }
    buf = io.StringIO()
    with redirect_stderr(buf):
        db = _build(tmp_path, None, java)
    assert "invalid clientkind" in buf.getvalue().lower()
    assert "bogus" not in _client_kinds(db)


def test_29b_unknown_source_producer_kind_warns_and_falls_back(tmp_path: Path) -> None:
    """In-source @CodebaseProducer(producerKind=<invalid enum>) is validated at parse
    time: the bad value is ignored with a warning and producer_kind falls back to the
    kafka_send default."""
    java = {
        "p/X.java": (
            "package p; import com.example.rag.*; class X { "
            "@CodebaseProducer(topic=\"t\", producerKind=CodebaseProducerKind.bogus) void m() {} }"
        ),
    }
    buf = io.StringIO()
    with redirect_stderr(buf):
        db = _build(tmp_path, None, java)
    assert "invalid producerkind" in buf.getvalue().lower()
    kinds = _producer_kinds(db)
    assert "bogus" not in kinds
    assert "kafka_send" in kinds


def test_30_brownfield_percentage_counter(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p; import org.springframework.web.client.RestTemplate; import com.example.rag.*; "
            "class X { RestTemplate restTemplate; "
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/bf\", method=CodebaseHttpMethod.GET) void a(){ restTemplate.getForObject(\"/builtin\", String.class); } "
            "void b(){ restTemplate.getForObject(\"/builtin-b\", String.class); } }"
        ),
    }
    db = _build(tmp_path, None, java)
    m = _meta(db)
    assert float(m.get("http_clients_from_brownfield_pct") or 0.0) > 0.0


def test_31_layer_b_annotation_async(tmp_path: Path) -> None:
    yml = """
async_producer_overrides:
  annotations:
    ann.LegacyEvent:
      client_kind: kafka_send
      topic: topic-31
"""
    java = {
        "ann/LegacyEvent.java": "package ann; import java.lang.annotation.*; @Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD) public @interface LegacyEvent {}",
        "p/X.java": "package p; import ann.LegacyEvent; class X { @LegacyEvent void m() {} }",
    }
    db = _build(tmp_path, yml, java)
    calls = _async_calls(db)
    assert any(c["topic"] == "topic-31" and c["strategy"] == "layer_b_ann" for c in calls)


def test_31a_per_method_scoping_http_replacement(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p; import org.springframework.web.client.RestTemplate; import com.example.rag.*; "
            "class X { RestTemplate restTemplate; "
            "void a(){ restTemplate.getForObject(\"/a\", String.class); } "
            "@CodebaseHttpClient(clientKind=CodebaseClientKind.rest_template, path=\"/b-override\", method=CodebaseHttpMethod.GET) "
            "void b(){ restTemplate.getForObject(\"/b\", String.class); } }"
        ),
    }
    db = _build(tmp_path, None, java)
    calls = _http_calls(db)
    by_method = {c["fqn"]: c for c in calls}
    assert any("#a()" in k and v["path_template"] == "/a" for k, v in by_method.items())
    assert any("#b()" in k and v["path_template"] == "/b-override" for k, v in by_method.items())


def test_31b_async_replacement_parity(tmp_path: Path) -> None:
    java = {
        "p/X.java": (
            "package p; import org.springframework.kafka.core.KafkaTemplate; import com.example.rag.*; "
            "class X { KafkaTemplate<String,String> kafkaTemplate; "
            "@CodebaseProducer(topic=\"override-31b\", producerKind=CodebaseProducerKind.kafka_send) void m(){ kafkaTemplate.send(\"builtin-31b\", \"x\"); } }"
        ),
    }
    db = _build(tmp_path, None, java)
    calls = _async_calls(db)
    assert len(calls) == 1
    assert calls[0]["topic"] == "override-31b"
    assert calls[0]["strategy"] == "layer_c_source"
