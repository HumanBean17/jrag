"""PR-G1: `cross_service_resolution` in `.java-codebase-rag.yml` (auto vs brownfield_only)."""
from __future__ import annotations

import io
import shutil
from contextlib import redirect_stderr
from pathlib import Path

import kuzu
import pytest

from _builders import build_graph_tables_to, build_kuzu_to
from build_ast_graph import GraphTables
from graph_enrich import _load_config_cross_service_resolution

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cross_service_smoke"


@pytest.fixture(autouse=True)
def _clear_resolution_config_cache() -> object:
    yield
    _load_config_cross_service_resolution.cache_clear()


def _copy_fixture(dest: Path) -> None:
    shutil.copytree(_FIXTURE, dest, dirs_exist_ok=True)


def _http_row_for_method(tables: GraphTables, method_name: str, *, parent_fqn: str | None = None):
    mid = None
    for m in tables.members:
        if m.decl.name != method_name:
            continue
        if parent_fqn is not None and m.parent_fqn != parent_fqn:
            continue
        mid = m.node_id
        break
    assert mid is not None
    client_ids = {e.client_id for e in tables.declares_client_rows if e.symbol_id == mid}
    for r in tables.http_call_rows:
        if r.client_id in client_ids:
            return r
    return None


def _build_tables(project_root: Path) -> GraphTables:
    """Full pipeline on a **mutable** tree (copy under tmp_path); not the session fixture."""
    return build_graph_tables_to(project_root, max_pass=6)


def _build_db(project_root: Path, db_path: Path) -> None:
    build_kuzu_to(project_root, db_path, max_pass=6)


def test_cross_service_resolution_auto_default(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    tables = _build_tables(root)
    assert tables.cross_service_resolution == "auto"
    assert sum(1 for r in tables.http_call_rows if r.match == "cross_service") >= 1
    assert sum(1 for r in tables.async_call_rows if r.match == "cross_service") >= 1


def test_brownfield_only_suppresses_auto_cross_service(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "cross_service_resolution: brownfield_only\n",
        encoding="utf-8",
    )
    tables = _build_tables(root)
    assert tables.cross_service_resolution == "brownfield_only"
    assert sum(1 for r in tables.http_call_rows if r.match == "cross_service") == 0
    assert sum(1 for r in tables.async_call_rows if r.match == "cross_service") == 0


def test_brownfield_only_keeps_annotated_cross_service(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "cross_service_resolution: brownfield_only\n",
        encoding="utf-8",
    )
    ctrl = root / "svc-b/src/main/java/smoke/b/ControllersB.java"
    body = ctrl.read_text(encoding="utf-8")
    body = body.replace(
        "import org.springframework.web.bind.annotation.PostMapping;\n",
        "import org.springframework.web.bind.annotation.PostMapping;\n"
        "import com.example.rag.CodebaseHttpRoute;\n",
    )
    body = body.replace(
        "    @PostMapping(\"/chat/joinOperator\")\n    public String joinOperator()",
        "    @CodebaseHttpRoute(path = \"/chat/joinOperator\", method = com.example.rag.CodebaseHttpMethod.POST)\n"
        "    @PostMapping(\"/chat/joinOperator\")\n    public String joinOperator()",
    )
    ctrl.write_text(body, encoding="utf-8")

    client = root / "svc-a/src/main/java/smoke/a/ClientA.java"
    cbody = client.read_text(encoding="utf-8")
    cbody = cbody.replace(
        "    public void callCrossService() {\n"
        "        restTemplate.postForEntity(\"/chat/joinOperator\", null, String.class);",
        "    @com.example.rag.CodebaseHttpClient(clientKind = com.example.rag.CodebaseClientKind.rest_template, "
        "targetService = \"svc-b\", path = \"/chat/joinOperator\", method = com.example.rag.CodebaseHttpMethod.POST)\n"
        "    public void callCrossService() {\n"
        "        restTemplate.postForEntity(\"/chat/joinOperator\", null, String.class);",
    )
    client.write_text(cbody, encoding="utf-8")

    tables = _build_tables(root)
    assert sum(1 for r in tables.http_call_rows if r.match == "cross_service") == 1


def test_brownfield_only_suppresses_feign_auto_cross_service(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "cross_service_resolution: brownfield_only\n",
        encoding="utf-8",
    )
    tables = _build_tables(root)
    row = _http_row_for_method(tables, "joinOperator", parent_fqn="smoke.a.BFeignClient")
    assert row is not None
    assert row.match == "unresolved"


def test_meta_reports_cross_service_resolution(tmp_path: Path) -> None:
    from kuzu_queries import KuzuGraph

    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "cross_service_resolution: brownfield_only\n",
        encoding="utf-8",
    )
    db = tmp_path / "g.kuzu"
    _build_db(root, db)
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    assert KuzuGraph(str(db)).meta()["cross_service_resolution"] == "brownfield_only"

    root2 = tmp_path / "proj2"
    _copy_fixture(root2)
    db2 = tmp_path / "g2.kuzu"
    _build_db(root2, db2)
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    assert KuzuGraph(str(db2)).meta()["cross_service_resolution"] == "auto"


def test_meta_resolution_null_for_old_graphs(tmp_path: Path) -> None:
    from kuzu_queries import KuzuGraph

    db_path = tmp_path / "legacy.kuzu"
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    conn.execute(
        "CREATE NODE TABLE GraphMeta("
        "key STRING PRIMARY KEY, "
        "ontology_version INT64, built_at INT64, source_root STRING, "
        "counts_json STRING, parse_errors INT64, "
        "routes_total INT64, exposes_total INT64, "
        "routes_by_framework STRING, "
        "routes_resolved_pct DOUBLE, "
        "routes_from_brownfield_pct DOUBLE, "
        "routes_by_layer STRING, "
        "http_calls_total INT64, "
        "async_calls_total INT64, "
        "http_calls_by_strategy STRING, "
        "async_calls_by_strategy STRING, "
        "http_calls_resolved_pct DOUBLE, "
        "async_calls_resolved_pct DOUBLE, "
        "http_clients_from_brownfield_pct DOUBLE, "
        "async_producers_from_brownfield_pct DOUBLE, "
        "http_calls_match_breakdown STRING, "
        "async_calls_match_breakdown STRING, "
        "cross_service_calls_total INT64, "
        "pass3_skipped_cross_service INT64)"
    )
    conn.execute(
        "CREATE (:GraphMeta {key: $k, ontology_version: $ov, built_at: $t, "
        "source_root: $sr, counts_json: $cj, parse_errors: $pe, "
        "routes_total: $rt, exposes_total: $et, "
        "routes_by_framework: $rfw, routes_resolved_pct: $rrp, "
        "routes_from_brownfield_pct: $rfbp, routes_by_layer: $rbl, "
        "http_calls_total: $hct, async_calls_total: $act, "
        "http_calls_by_strategy: $hbs, async_calls_by_strategy: $abs, "
        "http_calls_resolved_pct: $hcrp, async_calls_resolved_pct: $acrp, "
        "http_clients_from_brownfield_pct: $hcbp, "
        "async_producers_from_brownfield_pct: $apbp, "
        "http_calls_match_breakdown: $hmb, async_calls_match_breakdown: $amb, "
        "cross_service_calls_total: $csct, pass3_skipped_cross_service: $p3})",
        {
            "k": "graph",
            "ov": 7,
            "t": 0,
            "sr": "/tmp",
            "cj": "{}",
            "pe": 0,
            "rt": 0,
            "et": 0,
            "rfw": "{}",
            "rrp": 0.0,
            "rfbp": 0.0,
            "rbl": "{}",
            "hct": 0,
            "act": 0,
            "hbs": "{}",
            "abs": "{}",
            "hcrp": 0.0,
            "acrp": 0.0,
            "hcbp": 0.0,
            "apbp": 0.0,
            "hmb": "{}",
            "amb": "{}",
            "csct": 0,
            "p3": 0,
        },
    )
    conn.close()
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None
    assert KuzuGraph(str(db_path)).meta()["cross_service_resolution"] is None


def test_unknown_value_falls_back_to_auto(tmp_path: Path) -> None:
    root = tmp_path / "ymlonly"
    root.mkdir()
    (root / ".java-codebase-rag.yml").write_text(
        "cross_service_resolution: nonsense\n",
        encoding="utf-8",
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        v = _load_config_cross_service_resolution(str(root))
    assert v == "auto"
    assert "unknown value" in buf.getvalue()


def test_brownfield_client_with_auto_route_does_not_match(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "cross_service_resolution: brownfield_only\n",
        encoding="utf-8",
    )
    client = root / "svc-a/src/main/java/smoke/a/ClientA.java"
    cbody = client.read_text(encoding="utf-8")
    cbody = cbody.replace(
        "interface BFeignClient {\n    @PostMapping(\"/chat/joinOperator\")\n    String joinOperator();",
        "interface BFeignClient {\n"
        "    @com.example.rag.CodebaseHttpClient(clientKind = com.example.rag.CodebaseClientKind.feign_method, "
        "targetService = \"svc-b\", path = \"/chat/joinOperator\", method = com.example.rag.CodebaseHttpMethod.POST)\n"
        "    @PostMapping(\"/chat/joinOperator\")\n"
        "    String joinOperator();",
    )
    client.write_text(cbody, encoding="utf-8")

    tables = _build_tables(root)
    feign_row = _http_row_for_method(tables, "joinOperator", parent_fqn="smoke.a.BFeignClient")
    assert feign_row is not None
    assert feign_row.match != "cross_service"
