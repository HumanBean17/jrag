from __future__ import annotations

import io
import shutil
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from _builders import build_graph_tables_to, build_ladybug_to
from java_codebase_rag.graph.build_ast_graph import GraphTables
from java_codebase_rag.graph.graph_enrich import _load_brownfield_overrides, collect_annotation_meta_chain
from java_codebase_rag.graph.ladybug_queries import LadybugGraph

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "cross_service_smoke"


@pytest.fixture(autouse=True)
def _clear_caches() -> object:
    yield
    _load_brownfield_overrides.cache_clear()
    collect_annotation_meta_chain.cache_clear()
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None


def _copy_fixture(dest: Path) -> None:
    shutil.copytree(_FIXTURE, dest, dirs_exist_ok=True)


def _build_tables(project_root: Path) -> GraphTables:
    """Full pipeline on a **mutable** tree (copy under tmp_path); not the session fixture."""
    return build_graph_tables_to(project_root, max_pass=6)


def _build_graph(project_root: Path, db_path: Path) -> LadybugGraph:
    build_ladybug_to(project_root, db_path, max_pass=6)
    return LadybugGraph(str(db_path))


def _symbol_by_fqn(symbols, fqn: str):
    for s in symbols:
        if s.fqn == fqn:
            return s
    return None


def test_feign_client_emits_client_role(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    g = _build_graph(root, tmp_path / "g.lbug")
    sym = _symbol_by_fqn(g.list_by_role("CLIENT"), "smoke.a.BFeignClient")
    assert sym is not None
    assert sym.role == "CLIENT"
    assert "HTTP_CLIENT" in sym.capabilities


def test_no_legacy_feign_client_role_in_graph(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    g = _build_graph(root, tmp_path / "g.lbug")
    assert g.list_by_role("FEIGN_CLIENT") == []


def test_resttemplate_class_gets_client_role_from_messaging(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    g = _build_graph(root, tmp_path / "g.lbug")
    sym = _symbol_by_fqn(g.find_by_name_or_fqn("smoke.a.ClientA"), "smoke.a.ClientA")
    assert sym is not None
    # ClientA injects KafkaTemplate → CLIENT role (symmetric with CONTROLLER)
    assert sym.role == "CLIENT"
    assert "MESSAGE_PRODUCER" in sym.capabilities
    assert "HTTP_CLIENT" not in sym.capabilities


def test_brownfield_feign_client_role_dropped(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "role_overrides:\n"
        "  annotations:\n"
        "    FeignClient: FEIGN_CLIENT\n"
        "  fqn:\n"
        "    smoke.a.BFeignClient:\n"
        "      role: FEIGN_CLIENT\n",
        encoding="utf-8",
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        g = _build_graph(root, tmp_path / "g.lbug")
    stderr = buf.getvalue().lower()
    assert "unknown role" in stderr and "feign_client" in stderr
    sym = _symbol_by_fqn(g.find_by_name_or_fqn("smoke.a.BFeignClient"), "smoke.a.BFeignClient")
    assert sym is not None
    assert sym.role == "CLIENT"


def test_brownfield_client_role_accepted(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "role_overrides:\n"
        "  fqn:\n"
        "    smoke.a.ClientA:\n"
        "      role: CLIENT\n",
        encoding="utf-8",
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        g = _build_graph(root, tmp_path / "g.lbug")
    assert "unknown role" not in buf.getvalue().lower()
    sym = _symbol_by_fqn(g.find_by_name_or_fqn("smoke.a.ClientA"), "smoke.a.ClientA")
    assert sym is not None
    assert sym.role == "CLIENT"


def test_brownfield_http_client_capability_accepted(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    (root / ".java-codebase-rag.yml").write_text(
        "role_overrides:\n"
        "  fqn:\n"
        "    smoke.a.ClientA:\n"
        "      capabilities: [HTTP_CLIENT]\n",
        encoding="utf-8",
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        g = _build_graph(root, tmp_path / "g.lbug")
    assert "unknown capability" not in buf.getvalue().lower()
    sym = _symbol_by_fqn(g.find_by_name_or_fqn("smoke.a.ClientA"), "smoke.a.ClientA")
    assert sym is not None
    assert "HTTP_CLIENT" in sym.capabilities


def test_message_producer_capability_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    g = _build_graph(root, tmp_path / "g.lbug")
    sym = _symbol_by_fqn(g.find_by_name_or_fqn("smoke.a.ClientA"), "smoke.a.ClientA")
    assert sym is not None
    assert "MESSAGE_PRODUCER" in sym.capabilities


def test_trace_flow_includes_client_in_stage_2(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _copy_fixture(root)
    g = _build_graph(root, tmp_path / "g.lbug")
    assert "CLIENT" in LadybugGraph._FLOW_STAGES[2]
    stages = g.trace_flow(["smoke.a.BFeignClient"], depth=2, stage_limit=20)
    if len(stages) >= 3:
        assert any(s.symbol.role == "CLIENT" for s in stages[2])


def test_codebase_search_entry_roles_includes_client() -> None:
    assert "CLIENT" in LadybugGraph._ENTRYPOINT_ROLES
    assert "FEIGN_CLIENT" not in LadybugGraph._ENTRYPOINT_ROLES
