"""DDL ↔ EDGE_SCHEMA consistency (SCHEMA-V2 PR-A).

Endpoint (src/dst) parity only in PR-A; ``EDGE_SCHEMA.attrs`` vs DDL column lists
is a follow-up (column parity test or codegen).
"""
from __future__ import annotations

import re
from pathlib import Path

from java_ontology import BROWNFIELD_RESOLVER_STRATEGY_SET, EDGE_SCHEMA

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD_AST_GRAPH = _REPO_ROOT / "build_ast_graph.py"

_REL_DDL_RE = re.compile(
    r'CREATE REL TABLE (\w+)\(FROM (\w+) TO (\w+)',
)
_STRATEGY_LITERAL_RE = re.compile(
    r"""(?:strategy|resolution_strategy|edge_strat)\s*=\s*["']([a-z_]+)["']""",
)
_EMITTER_FILES = (
    "build_ast_graph.py",
    "graph_enrich.py",
    "ast_java.py",
)


def _ddl_endpoints() -> dict[str, tuple[str, str]]:
    text = _BUILD_AST_GRAPH.read_text(encoding="utf-8")
    out: dict[str, tuple[str, str]] = {}
    for match in _REL_DDL_RE.finditer(text):
        name, src, dst = match.group(1), match.group(2), match.group(3)
        out[name] = (src, dst)
    return out


def _strategy_literals_in_emitters() -> set[str]:
    found: set[str] = set()
    for rel in _EMITTER_FILES:
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        found.update(_STRATEGY_LITERAL_RE.findall(text))
    return found


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


def test_schema_consistency_http_calls_post_flip_client_to_route() -> None:
    spec = EDGE_SCHEMA["HTTP_CALLS"]
    assert spec.src == "Client"
    assert spec.dst == "Route"


def test_schema_consistency_async_calls_post_flip_producer_to_route() -> None:
    spec = EDGE_SCHEMA["ASYNC_CALLS"]
    assert spec.src == "Producer"
    assert spec.dst == "Route"


def test_edge_schema_member_only_flags_on_method_level_edges() -> None:
    assert EDGE_SCHEMA["DECLARES_CLIENT"].member_only is True
    assert EDGE_SCHEMA["DECLARES_PRODUCER"].member_only is True
    assert EDGE_SCHEMA["EXPOSES"].member_only is True
    assert EDGE_SCHEMA["OVERRIDES"].member_only is True
    assert EDGE_SCHEMA["CALLS"].member_only is True
    assert EDGE_SCHEMA["HTTP_CALLS"].member_only is False
    assert EDGE_SCHEMA["ASYNC_CALLS"].member_only is False


def test_http_async_typical_traversals_post_flip() -> None:
    http_trav = EDGE_SCHEMA["HTTP_CALLS"].typical_traversals
    assert "member_subject" in http_trav
    assert "DECLARES_CLIENT" in http_trav["member_subject"]
    async_trav = EDGE_SCHEMA["ASYNC_CALLS"].typical_traversals
    assert "member_subject" in async_trav
    assert "DECLARES_PRODUCER" in async_trav["member_subject"]


def test_brownfield_resolver_strategy_literals_emitted_in_builder_subset() -> None:
    literals = _strategy_literals_in_emitters()
    assert literals, "expected strategy literals from emitter modules"
    unknown = literals - BROWNFIELD_RESOLVER_STRATEGY_SET
    assert not unknown, f"strategy literals not in BROWNFIELD_RESOLVER_STRATEGY_SET: {sorted(unknown)}"
