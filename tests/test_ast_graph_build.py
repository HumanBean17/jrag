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

from _builders import build_ladybug_into, build_ladybug_to
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


def test_pass1_parse_incremental_total_excludes_removed_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Incremental pass-1 total must count only files that will actually be visited.

    On an incremental run, ``scope_files`` includes removed files (they were
    deleted, so they participate in scoped deletion), but they no longer exist
    on disk and are therefore never visited by the parse walk. Counting them in
    ``pass1_total`` makes ``done`` undercount then two-way-clamp on completion.
    The fix: the total is ``len(scope_files - removed_files)``.
    """
    import build_ast_graph

    root = tmp_path / "proj"
    java_dir = root / "src/main/java/smoke"
    java_dir.mkdir(parents=True)
    (java_dir / "Real.java").write_text(
        "package smoke;\nclass Real { void go() { } }\n", encoding="utf-8"
    )
    tables = build_ast_graph.GraphTables()
    # scope includes the real file plus a removed (gone-from-disk) file.
    scope_files = {"src/main/java/smoke/Real.java", "src/main/java/smoke/Gone.java"}
    removed_files = {"src/main/java/smoke/Gone.java"}
    build_ast_graph.pass1_parse(
        root, tables, verbose=True, scope_files=scope_files, removed_files=removed_files
    )
    captured = capsys.readouterr()
    pass1_totals = [
        int(m.group(1))
        for m in re.finditer(r"pass=1/6 done=0 total=(\d+)", captured.err)
    ]
    assert pass1_totals, f"expected a count-first pass-1 total line; stderr:\n{captured.err}"
    # The removed file must NOT be counted: total is 1 (only Real.java), not 2.
    assert pass1_totals[0] == 1, (
        f"incremental pass-1 total must exclude removed files; got {pass1_totals[0]}"
    )


# ---------------------------------------------------------------------------
# PR-P1: Bulk COPY FROM for _write_edges
# ---------------------------------------------------------------------------


def _load_baseline() -> dict:
    """Load the baseline fixture generated from the per-row _write_edges implementation."""
    baseline_path = Path(__file__).resolve().parent / "fixtures" / "graph_baseline_bank_chat.json"
    with open(baseline_path, encoding="utf-8") as f:
        return json.load(f)


def test_bulk_write_edges_match_per_row_baseline(ladybug_db_path: Path) -> None:
    """Bulk COPY FROM produces identical graph to the per-row baseline.

    Asserts node count, per-type edge counts, GraphMeta counters, and sampled edge
    properties match the baseline generated from the last per-row _write_edges build.
    """
    baseline = _load_baseline()
    conn = _connect(ladybug_db_path)

    # Assert node count matches
    node_count = int(conn.execute("MATCH (n:Symbol) RETURN COUNT(n)").get_next()[0])
    assert node_count == baseline["node_count"], f"node count mismatch: {node_count} vs {baseline['node_count']}"

    # Assert edge counts per type match
    for edge_type, expected_count in baseline["edge_counts"].items():
        actual_count = int(conn.execute(f"MATCH ()-[r:{edge_type}]->() RETURN COUNT(r)").get_next()[0])
        assert actual_count == expected_count, f"{edge_type} count mismatch: {actual_count} vs {expected_count}"

    # Assert GraphMeta counters match (only PR-P1 edge types)
    meta_row = conn.execute("MATCH (m:GraphMeta) RETURN m.ontology_version, m.counts_json").get_next()
    assert int(meta_row[0]) == baseline["graph_meta"]["ontology_version"], "ontology_version mismatch"
    # Parse both counts_json as LadybugDB-style unquoted JSON for comparison
    actual_counts = _parse_ladybug_json(meta_row[1])
    expected_counts = _parse_ladybug_json(baseline["graph_meta"]["counts_json"])
    # Filter to only PR-P1 edge types (routes/clients/producers are PR-P2)
    p1_keys = {"packages", "files", "types", "members", "phantoms", "extends", "implements", "injects", "declares", "overrides", "calls"}
    actual_counts_p1 = {k: v for k, v in actual_counts.items() if k in p1_keys}
    expected_counts_p1 = {k: v for k, v in expected_counts.items() if k in p1_keys}
    assert actual_counts_p1 == expected_counts_p1, f"GraphMeta PR-P1 counts mismatch: {actual_counts_p1} vs {expected_counts_p1}"

    # Assert sampled edge properties match (verify CALLS callee_declaring_role is preserved)
    for edge_type, sampled_baseline in baseline["sampled_edges"].items():
        result = conn.execute(f"MATCH (a)-[r:{edge_type}]->(b) RETURN a.id, b.id, r LIMIT 3")
        actual_rows = []
        while result.has_next():
            actual_rows.append(result.get_next())
        assert len(actual_rows) == len(sampled_baseline), f"{edge_type}: sampled row count mismatch"
        # For CALLS, verify callee_declaring_role is preserved (don't compare node IDs as they vary per build)
        if edge_type == "CALLS":
            for actual, expected in zip(actual_rows, sampled_baseline):
                actual_props = actual[2]
                expected_props = expected[2]
                assert actual_props["callee_declaring_role"] == expected_props["callee_declaring_role"], \
                    f"CALLS callee_declaring_role mismatch: {actual_props['callee_declaring_role']} vs {expected_props['callee_declaring_role']}"


def test_bulk_write_is_deterministic_double_build(corpus_root: Path, tmp_path: Path) -> None:
    """Bulk COPY FROM is deterministic: two builds of the same corpus produce identical graphs.

    Models on tests/test_brownfield_routes.py::test_29_determinism_pass4_route_ids and
    tests/test_mcp_v2_compose.py::test_overrides_edge_set_deterministic_double_build.
    """
    db1 = tmp_path / "double1.lbug"
    db2 = tmp_path / "double2.lbug"
    build_ladybug_into(corpus_root, db1)
    build_ladybug_into(corpus_root, db2)

    conn1 = _connect(db1)
    conn2 = _connect(db2)

    # Assert identical node counts
    count1 = int(conn1.execute("MATCH (n:Symbol) RETURN COUNT(n)").get_next()[0])
    count2 = int(conn2.execute("MATCH (n:Symbol) RETURN COUNT(n)").get_next()[0])
    assert count1 == count2, f"node count mismatch: {count1} vs {count2}"

    # Assert identical edge counts per type
    for edge_type in ["EXTENDS", "IMPLEMENTS", "INJECTS", "DECLARES", "OVERRIDES", "CALLS", "UNRESOLVED_AT"]:
        c1 = int(conn1.execute(f"MATCH ()-[r:{edge_type}]->() RETURN COUNT(r)").get_next()[0])
        c2 = int(conn2.execute(f"MATCH ()-[r:{edge_type}]->() RETURN COUNT(r)").get_next()[0])
        assert c1 == c2, f"{edge_type} count mismatch: {c1} vs {c2}"

    # Assert identical GraphMeta counters
    meta1 = conn1.execute("MATCH (m:GraphMeta) RETURN m.counts_json").get_next()[0]
    meta2 = conn2.execute("MATCH (m:GraphMeta) RETURN m.counts_json").get_next()[0]
    assert meta1 == meta2, "GraphMeta counts_json mismatch"

    # Spot-check: assert identical CALLS callee_declaring_role for a known edge
    calls1 = conn1.execute("MATCH (a)-[c:CALLS]->(b) RETURN c.callee_declaring_role LIMIT 1").get_next()[0]
    calls2 = conn2.execute("MATCH (a)-[c:CALLS]->(b) RETURN c.callee_declaring_role LIMIT 1").get_next()[0]
    assert calls1 == calls2, f"CALLS callee_declaring_role mismatch: {calls1} vs {calls2}"


def test_bulk_write_preserves_calls_dedup_and_callee_declaring_role(ladybug_db_path: Path) -> None:
    """Bulk COPY FROM preserves CALLS dedup by (src, dst, argc, line) and callee_declaring_role.

    Reuses the @Service callee assertion against a bulk build to verify the materialization
    at staging time produces the same results as the per-row path.
    """
    conn = _connect(ladybug_db_path)

    # Verify CALLS dedup: count unique (src_id, dst_id, arg_count, call_site_line) tuples
    result = conn.execute(
        "MATCH (a)-[c:CALLS]->(b) "
        "RETURN COUNT(DISTINCT {src: a.id, dst: b.id, argc: c.arg_count, line: c.call_site_line})"
    )
    unique_call_keys = int(result.get_next()[0])

    # Total CALLS count should equal unique keys (dedup applied)
    total_calls = int(conn.execute("MATCH ()-[c:CALLS]->() RETURN COUNT(c)").get_next()[0])
    assert unique_call_keys == total_calls, f"CALLS dedup failed: {unique_call_keys} unique keys vs {total_calls} total edges"

    # Verify callee_declaring_role: @Service methods should have callee_declaring_role = "SERVICE"
    service_calls = conn.execute(
        "MATCH (callee:Symbol)<-[:DECLARES]-(member:Symbol)<-[c:CALLS]-(caller:Symbol) "
        "WHERE callee.role = 'SERVICE' "
        "RETURN DISTINCT c.callee_declaring_role LIMIT 10"
    )
    while service_calls.has_next():
        role = service_calls.get_next()[0]
        assert role == "SERVICE", f"@Service callee has unexpected callee_declaring_role: {role}"


def test_bulk_write_empty_rel_table_is_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Bulk COPY FROM with empty rows list is a no-op; corpus with no EXTENDS edges should not error."""
    import build_ast_graph

    root = tmp_path / "proj"
    java_dir = root / "src/main/java/smoke"
    java_dir.mkdir(parents=True)
    # Create a corpus with no inheritance (no EXTENDS/IMPLEMENTS edges)
    (java_dir / "NoInheritance.java").write_text(
        "package smoke;\nclass NoInheritance { void go() { } }\n", encoding="utf-8"
    )

    db_path = tmp_path / "no_inherits.lbug"
    tables = build_ast_graph.GraphTables()
    asts = build_ast_graph.pass1_parse(root, tables, verbose=False)
    build_ast_graph.pass2_edges(tables, asts, verbose=False)
    build_ast_graph.pass3_calls(tables, asts, verbose=False)
    build_ast_graph.pass4_routes(tables, asts, source_root=root, verbose=False)
    build_ast_graph.pass5_imperative_edges(tables, asts, source_root=root, verbose=False)
    build_ast_graph.pass6_match_edges(tables, verbose=False)

    # Build via bulk write (should not error on empty EXTENDS)
    build_ast_graph.write_ladybug(db_path, tables, source_root=root, verbose=False)

    # Verify EXTENDS table is empty
    conn = _connect(db_path)
    extends_count = int(conn.execute("MATCH ()-[r:EXTENDS]->() RETURN COUNT(r)").get_next()[0])
    assert extends_count == 0, "EXTENDS should be empty for this corpus"
