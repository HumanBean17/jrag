"""Tests for `build_ast_graph.py` against the bank-chat-system corpus.

These tests pin *structural* invariants of the build (schema present, every
edge type populated, service inference works for both single-module and
multi-module Maven projects). They intentionally avoid asserting on exact
node / edge counts — those will drift as the fixture grows. See
`tests/README.md` for the anti-overfitting rules.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import ladybug
import pytest

from _builders import build_ladybug_to
from ast_java import ONTOLOGY_VERSION
from graph_enrich import _load_brownfield_overrides, collect_annotation_meta_chain


def _parse_ladybug_json(raw: str | None) -> dict:
    """Parse JSON from LadybugDB which returns unquoted keys like {key: value}."""
    if not raw:
        return {}
    # Convert {key: value} to {"key": value}
    quoted = re.sub(r'(\w+):', r'"\1":', raw)
    try:
        return json.loads(quoted)
    except Exception:
        try:
            # Fallback: try parsing as-is (for standard JSON)
            return json.loads(raw)
        except Exception:
            return {}


def _connect(db_path: Path) -> ladybug.Connection:
    db = ladybug.Database(str(db_path), read_only=True)
    return ladybug.Connection(db)


def _scalar(conn: ladybug.Connection, query: str) -> int:
    r = conn.execute(query)
    if not r.has_next():
        return 0
    return int(r.get_next()[0] or 0)


def _column(conn: ladybug.Connection, query: str, idx: int = 0) -> list:
    r = conn.execute(query)
    out: list = []
    while r.has_next():
        out.append(r.get_next()[idx])
    return out


def test_kuzu_db_directory_exists(ladybug_db_path: Path) -> None:
    assert ladybug_db_path.exists()


def test_schema_has_all_expected_tables(ladybug_db_path: Path) -> None:
    conn = _connect(ladybug_db_path)
    # `CALL show_tables() RETURN *;` returns rows (id, name, type, ...) — name is at index 1.
    tables = set(_column(conn, "CALL show_tables() RETURN *;", idx=1))
    # We only assert the tables we depend on are present. The builder is
    # free to add more (e.g. CALLS later) without breaking this test.
    expected = {
        "Symbol", "UnresolvedCallSite", "Route", "Client", "GraphMeta",
        "EXTENDS", "IMPLEMENTS", "INJECTS", "DECLARES", "OVERRIDES", "CALLS", "UNRESOLVED_AT",
        "EXPOSES", "DECLARES_CLIENT", "DECLARES_PRODUCER",
    }
    missing = expected - tables
    assert not missing, f"missing schema tables: {missing}; saw {tables}"


def test_graph_meta_unresolved_counters_present(ladybug_db_path: Path) -> None:
    conn = _connect(ladybug_db_path)
    r = conn.execute(
        "MATCH (m:GraphMeta) RETURN m.pass3_unresolved_phantom_receiver, "
        "m.pass3_unresolved_chained"
    )
    assert r.has_next(), "expected GraphMeta row"
    row = r.get_next()
    assert row[0] is not None and int(row[0]) >= 0
    assert row[1] is not None and int(row[1]) >= 0


def test_calls_callee_declaring_role_matches_parent_symbol_role_yaml_brownfield(
    tmp_path: Path,
) -> None:
    """YAML role_overrides on declaring type → edge attr matches parent Symbol.role."""
    _load_brownfield_overrides.cache_clear()
    collect_annotation_meta_chain.cache_clear()
    root = tmp_path / "proj"
    java_dir = root / "src/main/java/smoke"
    java_dir.mkdir(parents=True)
    (java_dir / "BrownfieldCallRole.java").write_text(
        """
        package smoke;

        @interface LegacyServiceMarker { }

        @LegacyServiceMarker
        class ConfigOnlyService {
            void handle() { }
        }

        class Caller {
            void run(ConfigOnlyService svc) {
                svc.handle();
            }
        }
        """.strip()
        + "\n",
        encoding="utf-8",
    )
    (root / ".java-codebase-rag.yml").write_text(
        "role_overrides:\n"
        "  annotations:\n"
        "    LegacyServiceMarker: SERVICE\n",
        encoding="utf-8",
    )
    db_path = build_ladybug_to(root, tmp_path / "g.lbug", max_pass=3)
    conn = _connect(db_path)
    mismatches = _scalar(
        conn,
        "MATCH ()-[c:CALLS]->(dst:Symbol) "
        "MATCH (parent:Symbol {id: dst.parent_id}) "
        "WHERE c.callee_declaring_role <> parent.role "
        "RETURN count(*)",
    )
    assert mismatches == 0
    roles = _column(
        conn,
        "MATCH ()-[c:CALLS]->(dst:Symbol) "
        "MATCH (parent:Symbol {id: dst.parent_id}) "
        "WHERE parent.fqn = 'smoke.ConfigOnlyService' "
        "RETURN DISTINCT c.callee_declaring_role",
    )
    assert roles == ["SERVICE"]


def test_pass3_callee_declaring_role_bank_annotated_types(ladybug_db_path: Path) -> None:
    """CALLS to methods on @Service declaring types carry callee_declaring_role=SERVICE."""
    conn = _connect(ladybug_db_path)
    rows = _column(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "MATCH (parent:Symbol {id: dst.parent_id}) "
        "WHERE 'Service' IN parent.annotations AND parent.role = 'SERVICE' "
        "RETURN c.callee_declaring_role LIMIT 20",
    )
    assert rows, "expected CALLS to @Service-declared callees on bank-chat-system"
    assert all(str(r) == "SERVICE" for r in rows), rows
    repo_rows = _column(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "MATCH (parent:Symbol {id: dst.parent_id}) "
        "WHERE 'Repository' IN parent.annotations "
        "RETURN DISTINCT c.callee_declaring_role",
    )
    if repo_rows:
        assert all(str(r) == "REPOSITORY" for r in repo_rows), repo_rows


def test_graph_meta_present_and_versioned(ladybug_db_path: Path) -> None:
    conn = _connect(ladybug_db_path)
    r = conn.execute(
        "MATCH (m:GraphMeta) RETURN m.ontology_version, m.built_at, "
        "m.source_root, m.parse_errors, m.counts_json, "
        "m.routes_total, m.exposes_total, m.routes_by_framework, m.routes_resolved_pct, "
        "m.routes_from_brownfield_pct, m.routes_by_layer"
    )
    rows: list = []
    while r.has_next():
        rows.append(r.get_next())
    assert len(rows) == 1, "expected exactly one GraphMeta row"
    row = rows[0]
    ov = row[0]
    built_at = row[1]
    source_root = row[2]
    parse_errors = row[3]
    counts_json = row[4]
    routes_total = row[5]
    exposes_total = row[6]
    routes_by_framework_raw = row[7]
    routes_resolved_pct = row[8]
    routes_from_brownfield_pct = row[9]
    routes_by_layer_raw = row[10]
    assert int(ov) == ONTOLOGY_VERSION
    assert int(built_at) > 0
    assert source_root  # absolute path string
    # Parse errors should be tolerable on a clean fixture; this catches
    # accidental tree-sitter regressions that break every file at once.
    assert int(parse_errors) <= 0  # bank-chat-system is hand-written, no errors expected
    assert counts_json and counts_json.startswith("{")
    counts = _parse_ladybug_json(counts_json)
    assert counts.get("routes", 0) >= 1
    assert int(routes_total) >= 1
    assert int(exposes_total) >= 1
    assert float(routes_resolved_pct) >= 0.0
    by_fw = _parse_ladybug_json(routes_by_framework_raw)
    assert isinstance(by_fw, dict)
    assert len(by_fw) >= 1
    assert float(routes_from_brownfield_pct) >= 0.0
    by_layer = _parse_ladybug_json(routes_by_layer_raw)
    assert isinstance(by_layer, dict)


def test_each_node_kind_present(ladybug_db_path: Path) -> None:
    """Builder must emit at least one node of every Phase-1 kind we care about.

    Exact counts are a moving target; non-zero is the meaningful invariant.
    """
    conn = _connect(ladybug_db_path)
    kinds = set(_column(conn, "MATCH (s:Symbol) RETURN DISTINCT s.kind"))
    for required in ("package", "file", "class", "interface", "method", "constructor"):
        assert required in kinds, f"missing node kind: {required}; saw {kinds}"


def test_each_edge_type_populated(ladybug_db_path: Path) -> None:
    conn = _connect(ladybug_db_path)
    assert _scalar(conn, "MATCH ()-[e:EXTENDS]->() RETURN count(e)") > 0
    assert _scalar(conn, "MATCH ()-[e:IMPLEMENTS]->() RETURN count(e)") > 0
    assert _scalar(conn, "MATCH ()-[e:INJECTS]->() RETURN count(e)") > 0


def test_calls_and_declares_edges_populated(ladybug_db_path: Path) -> None:
    conn = _connect(ladybug_db_path)
    assert _scalar(conn, "MATCH ()-[e:CALLS]->() RETURN count(e)") > 0
    assert _scalar(conn, "MATCH ()-[e:DECLARES]->() RETURN count(e)") > 0


def test_module_inference_recognises_both_layouts(ladybug_graph) -> None:
    """`module_for_path` must find each Maven module's name.

    The corpus exercises both a single-module project (chat-assign) and a
    multi-module reactor (chat-core/{chat-app,chat-engine,chat-domain,
    chat-contracts}). The MCP must handle both shapes.
    """
    counts = ladybug_graph.module_counts()
    # Defensive: don't pin every module to >0 in case the corpus is
    # trimmed; instead require both styles to be represented.
    assert counts.get("chat-assign", 0) > 0, counts
    multi_module = {"chat-app", "chat-engine", "chat-domain", "chat-contracts"}
    seen = multi_module & set(counts)
    assert len(seen) >= 2, (
        "expected module inference to surface multiple chat-core child "
        f"modules, got: {sorted(set(counts))}"
    )


def test_microservice_inference_groups_multi_module_reactor(ladybug_graph) -> None:
    """Multi-module reactor child modules must collapse to one microservice key.

    `chat-core` is the outermost build-marker ancestor for every
    `chat-core/<module>/...` file; it must surface as the microservice
    name regardless of which inner module the file belongs to.
    `chat-assign` is single-module so its module and microservice names
    coincide.
    """
    counts = ladybug_graph.microservice_counts()
    assert counts.get("chat-assign", 0) > 0, counts
    assert counts.get("chat-core", 0) > 0, counts
    # Inner module names must NOT appear at the microservice level — that
    # was exactly the misclassification the rename was meant to fix.
    inner = {"chat-app", "chat-engine", "chat-domain", "chat-contracts"}
    assert not (inner & set(counts)), counts


def test_phantom_nodes_for_external_types(ladybug_db_path: Path) -> None:
    """Spring Data repositories extend `JpaRepository` (an external type).

    The builder must materialise that as a *phantom* (unresolved) Symbol so
    EXTENDS/IMPLEMENTS edges are never dangling.
    """
    conn = _connect(ladybug_db_path)
    n_phantoms = _scalar(
        conn, "MATCH (s:Symbol) WHERE s.resolved = false RETURN count(s)"
    )
    assert n_phantoms > 0, "no phantom nodes — external type resolution may be silently dropping edges"


def test_injects_edges_have_mechanism(ladybug_db_path: Path) -> None:
    """Every INJECTS edge should record *how* the injection happens.

    The bank-chat-system uses constructor injection throughout
    (`ChatManagementService(...)`, `ChatCoreJoinClient(...)`), so we expect
    to see at least one `constructor` mechanism. We don't assert that *all*
    edges are constructor-injected to leave room for future Lombok / setter
    samples.
    """
    conn = _connect(ladybug_db_path)
    mechanisms = set(_column(conn, "MATCH ()-[e:INJECTS]->() RETURN DISTINCT e.mechanism"))
    assert "constructor" in mechanisms, mechanisms


def test_routes_and_exposes_populated(ladybug_db_path: Path) -> None:
    conn = _connect(ladybug_db_path)
    assert _scalar(conn, "MATCH (r:Route) RETURN count(r)") >= 1
    assert _scalar(conn, "MATCH ()-[e:EXPOSES]->() RETURN count(e)") >= 1


def test_route_id_includes_microservice(ladybug_db_path_route_extraction_smoke: Path) -> None:
    """Same HTTP path in two declared microservices → distinct Route primary keys."""
    db_path = ladybug_db_path_route_extraction_smoke
    conn = _connect(db_path)
    ids = _column(
        conn,
        "MATCH (r:Route) WHERE r.path = '/api/users' AND r.kind = 'http_endpoint' "
        "RETURN r.id",
    )
    assert len(set(ids)) >= 2, ids


def test_exposes_edge_direction(ladybug_db_path_route_extraction_smoke: Path) -> None:
    db_path = ladybug_db_path_route_extraction_smoke
    conn = _connect(db_path)
    fwd = _scalar(conn, "MATCH (s:Symbol)-[:EXPOSES]->(r:Route) RETURN count(*)")
    rev = _scalar(conn, "MATCH (r:Route)-[:EXPOSES]->(s:Symbol) RETURN count(*)")
    assert fwd >= 1
    assert rev == 0


def test_symbol_has_capabilities_column(ladybug_db_path: Path) -> None:
    """Symbol nodes must have a `capabilities` STRING[] column (ontology v4)."""
    conn = _connect(ladybug_db_path)
    # Simply SELECT a capabilities value — if the column doesn't exist Kuzu raises.
    try:
        r = conn.execute(
            "MATCH (s:Symbol) WHERE s.kind = 'class' AND s.resolved "
            "RETURN s.capabilities LIMIT 1"
        )
    except Exception as exc:
        pytest.fail(f"capabilities column missing or unreadable: {exc}")
    # The column should exist; the value may be an empty list for most types.
    assert r is not None


def test_cli_entrypoint_runs(tmp_path: Path, corpus_root: Path) -> None:
    """`build_ast_graph.py --source-root <root>` must succeed end-to-end.

    This is an integration smoke test — it calls the script as a user would
    (via the venv Python) and asserts a non-empty Kuzu DB is written.
    """
    target = tmp_path / "graph.lbug"
    script = Path(__file__).resolve().parent.parent / "build_ast_graph.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-root", str(corpus_root),
            "--ladybug-path", str(target),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert target.exists()
    conn = _connect(target)
    assert _scalar(conn, "MATCH (s:Symbol) RETURN count(s)") > 0


def test_pass3_no_phantom_chained_calls_rows(ladybug_db_path: Path) -> None:
    """HV19 — receiver-failure strategies must not appear on CALLS after PR-3."""
    conn = _connect(ladybug_db_path)
    n = _scalar(
        conn,
        "MATCH ()-[c:CALLS]->() "
        "WHERE c.strategy IN ['phantom','chained_receiver'] RETURN count(c)",
    )
    assert n == 0, f"expected zero phantom/chained_receiver CALLS rows, got {n}"


def test_pass3_unresolved_call_site_emitted(ladybug_db_path: Path) -> None:
    conn = _connect(ladybug_db_path)
    n_ucs = _scalar(conn, "MATCH (u:UnresolvedCallSite) RETURN count(u)")
    n_rel = _scalar(conn, "MATCH ()-[:UNRESOLVED_AT]->() RETURN count(*)")
    assert n_ucs >= 1, "bank fixture should emit UnresolvedCallSite rows"
    assert n_rel == n_ucs
    reasons = {
        r[0]
        for r in conn.execute(
            "MATCH (u:UnresolvedCallSite) RETURN DISTINCT u.reason"
        )
    }
    assert reasons <= {"phantom_unresolved_receiver", "chained_receiver"}
    assert len(reasons) >= 1


def test_pass3_known_external_calls_preserved(ladybug_db_path: Path) -> None:
    """HV37 — JDK/external callee stays on CALLS with resolved=False, not phantom strategy."""
    conn = _connect(ladybug_db_path)
    rows = conn.execute(
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE c.resolved = false AND c.strategy <> 'overload_ambiguous' "
        "RETURN c.strategy AS s LIMIT 20"
    )
    found = [str(r[0]) for r in rows]
    assert found, "bank fixture should have known-external CALLS rows"
    assert all(s not in ("phantom", "chained_receiver") for s in found), found


# ---------------------------------------------------------------------------
# Graph-phase JCIRAG_PROGRESS emission (PR-2)
# ---------------------------------------------------------------------------


def _run_builder_verbose(corpus_root: Path, target_db: Path, *, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run build_ast_graph.py --verbose and return the CompletedProcess."""
    script = Path(__file__).resolve().parent.parent / "build_ast_graph.py"
    cmd = [
        sys.executable,
        str(script),
        "--source-root", str(corpus_root),
        "--ladybug-path", str(target_db),
        "--verbose",
        *(extra_args or []),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _progress_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if "JCIRAG_PROGRESS kind=graph" in ln]


def _count_filtered_java_files(corpus_root: Path) -> int:
    from path_filtering import LayeredIgnore, iter_java_source_files

    return sum(1 for _ in iter_java_source_files(corpus_root.resolve(), ignore=LayeredIgnore(corpus_root.resolve())))


def test_build_ast_graph_pass1_emits_per_file_progress(corpus_root: Path, tmp_path: Path) -> None:
    """Pass 1 is count-first: a `total=` line precedes the first `done=` tick; ticks advance."""
    target = tmp_path / "p1.lbug"
    proc = _run_builder_verbose(corpus_root, target)
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    lines = _progress_lines(proc.stderr)
    # Pass-1 lines carry pass=1/6 with a total and per-file done ticks.
    pass1 = [ln for ln in lines if "pass=1/6" in ln]
    assert pass1, f"expected pass 1 progress lines, got: {lines!r}"
    # The first pass-1 line with a total must precede the first line with a done tick.
    totals = [ln for ln in pass1 if "total=" in ln]
    dones = [ln for ln in pass1 if "done=" in ln]
    assert totals, f"pass 1 must emit a count-first total; lines: {pass1!r}"
    assert dones, f"pass 1 must emit per-file done ticks; lines: {pass1!r}"
    first_total_idx = lines.index(totals[0])
    first_done_idx = lines.index(dones[0])
    assert first_total_idx <= first_done_idx, "total must precede the first done tick"
    # Done ticks advance (monotonic non-decreasing values).
    done_vals = [int(re.search(r"done=(\d+)", ln).group(1)) for ln in dones if re.search(r"done=(\d+)", ln)]
    assert done_vals == sorted(done_vals), f"done ticks must be monotonic: {done_vals}"


def test_build_ast_graph_pass1_total_is_exact_filtered_count(corpus_root: Path, tmp_path: Path) -> None:
    """The count-first pass-1 total equals the exact non-ignored .java file count."""
    target = tmp_path / "p1total.lbug"
    proc = _run_builder_verbose(corpus_root, target)
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    lines = _progress_lines(proc.stderr)
    pass1_total_lines = [
        ln for ln in lines if "pass=1/6" in ln and "total=" in ln and "done=" in ln
    ]
    # Fall back to any pass-1 line carrying a total.
    if not pass1_total_lines:
        pass1_total_lines = [ln for ln in lines if "pass=1/6" in ln and "total=" in ln]
    assert pass1_total_lines, f"no pass-1 total line found; lines: {lines!r}"
    totals = [int(re.search(r"total=(\d+)", ln).group(1)) for ln in pass1_total_lines if re.search(r"total=(\d+)", ln)]
    assert totals, f"could not parse total from: {pass1_total_lines!r}"
    expected = _count_filtered_java_files(corpus_root)
    assert totals[0] == expected, f"pass-1 total {totals[0]} != filtered count {expected}"


def test_build_ast_graph_passes_2_to_6_emit_step_progress(corpus_root: Path, tmp_path: Path) -> None:
    """Each of passes 2–6 emits a `pass=N/6` step line on entry/exit."""
    target = tmp_path / "p2to6.lbug"
    proc = _run_builder_verbose(corpus_root, target)
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    lines = _progress_lines(proc.stderr)
    for n in range(2, 7):
        step_lines = [ln for ln in lines if f"pass={n}/6" in ln]
        assert step_lines, f"pass {n}/6 emitted no progress lines; full: {lines!r}"


def test_build_ast_graph_quiet_emits_no_progress(corpus_root: Path, tmp_path: Path) -> None:
    """Without --verbose the builder emits no JCIRAG_PROGRESS lines."""
    script = Path(__file__).resolve().parent.parent / "build_ast_graph.py"
    target = tmp_path / "quiet.lbug"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-root", str(corpus_root),
            "--ladybug-path", str(target),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert _progress_lines(proc.stderr) == [], "quiet build must not emit JCIRAG_PROGRESS"
