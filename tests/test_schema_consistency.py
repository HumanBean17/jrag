"""DDL ↔ EDGE_SCHEMA endpoint consistency (SCHEMA-V2 PR-A)."""
from __future__ import annotations

import re
from pathlib import Path

from java_ontology import EDGE_SCHEMA

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD_AST_GRAPH = _REPO_ROOT / "build_ast_graph.py"

_REL_DDL_RE = re.compile(
    r'CREATE REL TABLE (\w+)\(FROM (\w+) TO (\w+)',
)


def _ddl_endpoints() -> dict[str, tuple[str, str]]:
    text = _BUILD_AST_GRAPH.read_text(encoding="utf-8")
    out: dict[str, tuple[str, str]] = {}
    for match in _REL_DDL_RE.finditer(text):
        name, src, dst = match.group(1), match.group(2), match.group(3)
        out[name] = (src, dst)
    return out


def test_schema_consistency_all_ddl_endpoints_match_edge_schema() -> None:
    ddl = _ddl_endpoints()
    schema_names = set(EDGE_SCHEMA)
    ddl_names = set(ddl)
    assert schema_names == ddl_names, (
        f"EDGE_SCHEMA keys {sorted(schema_names)} != DDL edges {sorted(ddl_names)}"
    )
    for name, spec in EDGE_SCHEMA.items():
        src, dst = ddl[name]
        assert spec.src == src, f"{name}: schema src {spec.src!r} != DDL {src!r}"
        assert spec.dst == dst, f"{name}: schema dst {spec.dst!r} != DDL {dst!r}"


def test_schema_consistency_http_calls_pre_flip_symbol_to_route() -> None:
    spec = EDGE_SCHEMA["HTTP_CALLS"]
    assert spec.src == "Symbol"
    assert spec.dst == "Route"


def test_schema_consistency_async_calls_pre_flip_symbol_to_route() -> None:
    spec = EDGE_SCHEMA["ASYNC_CALLS"]
    assert spec.src == "Symbol"
    assert spec.dst == "Route"


def test_edge_schema_member_only_flags_on_method_level_edges() -> None:
    assert EDGE_SCHEMA["DECLARES_CLIENT"].member_only is True
    assert EDGE_SCHEMA["EXPOSES"].member_only is True
    assert EDGE_SCHEMA["OVERRIDES"].member_only is True
    assert EDGE_SCHEMA["CALLS"].member_only is True
    assert "DECLARES_PRODUCER" not in EDGE_SCHEMA
    assert EDGE_SCHEMA["HTTP_CALLS"].member_only is False
    assert EDGE_SCHEMA["ASYNC_CALLS"].member_only is False
