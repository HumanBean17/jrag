"""PR-T3: Incremental rebuild equivalence, closure, and fallback tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import kuzu

from build_ast_graph import (
    ONTOLOGY_VERSION,
    DepsIndex,
    FileDeps,
    GraphTables,
    _read_dependency_index,
    build_ast_graph_incremental,
    expand_to_closure,
    pass1_parse,
    pass2_edges,
    pass3_calls,
    pass4_routes,
    pass5_imperative_edges,
    pass6_match_edges,
    write_kuzu,
)

TESTS_DIR = Path(__file__).resolve().parent
CORPUS = TESTS_DIR / "bank-chat-system"


def _full_rebuild_into(corpus: Path, db_path: Path) -> Path:
    tables = GraphTables()
    asts = pass1_parse(corpus, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    pass4_routes(tables, asts, source_root=corpus, verbose=False)
    pass5_imperative_edges(tables, asts, source_root=corpus, verbose=False)
    pass6_match_edges(tables, verbose=False)
    write_kuzu(db_path, tables, source_root=corpus, verbose=False)
    return db_path


def _dump_node_ids(db_path: Path) -> set[str]:
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    ids: set[str] = set()
    for kind in ("Symbol", "Route", "Client", "Producer", "UnresolvedCallSite"):
        try:
            r = conn.execute(f"MATCH (n:{kind}) RETURN n.id AS id")
            while r.has_next():
                ids.add(r.get_next()[0])
        except Exception:
            pass
    conn.close()
    return ids


def _dump_edge_rows(db_path: Path) -> set[tuple[str, ...]]:
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    labels = [
        "DECLARES", "EXTENDS", "IMPLEMENTS", "INJECTS",
        "CALLS", "OVERRIDES", "EXPOSES",
        "DECLARES_CLIENT", "DECLARES_PRODUCER",
        "HTTP_CALLS", "ASYNC_CALLS",
    ]
    rows: set[tuple[str, ...]] = set()
    for label in labels:
        try:
            result = conn.execute(
                f"MATCH (a)-[e:{label}]->(b) RETURN a.id AS src, b.id AS dst"
            )
        except Exception:
            continue
        while result.has_next():
            row = result.get_next()
            rows.add((row[0], row[1], label))
    conn.close()
    return rows


def _get_graph_meta(conn: kuzu.Connection) -> dict:
    r = conn.execute("MATCH (m:GraphMeta) WHERE m.key = 'graph' RETURN m.*")
    if not r.has_next():
        return {}
    row = r.get_next()
    cols = [d["name"] for d in r.get_column_names()]
    # Kuzu returns column names via get_column_names on result
    return dict(zip(cols, row))


def _get_any_java_file(corpus: Path) -> str:
    """Return a relative path to any .java file in corpus."""
    for p in sorted(corpus.rglob("*.java")):
        try:
            return str(p.resolve().relative_to(corpus.resolve()))
        except ValueError:
            continue
    raise FileNotFoundError(f"No .java files in {corpus}")


def _copy_corpus(src: Path, dst: Path) -> Path:
    """Copy corpus to dst for mutation."""
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


# ---- Equivalence tests ----


class TestIncrementalEquivalence:
    """Incremental rebuild must produce identical graph state to full rebuild."""

    def _assert_equivalence(self, corpus: Path, tmp_path: Path) -> None:
        """Run full rebuild, then incremental with one file 'changed', compare."""
        # Full rebuild baseline
        full_db = tmp_path / "full" / "code_graph.kuzu"
        _full_rebuild_into(corpus, full_db)
        full_nodes = _dump_node_ids(full_db)
        full_edges = _dump_edge_rows(full_db)

        # Incremental: pick one file as "changed"
        changed_file = _get_any_java_file(corpus)
        incr_db = tmp_path / "incr" / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)

        result = build_ast_graph_incremental(
            corpus, incr_db, {changed_file}, verbose=False,
        )
        assert result == "incremental"

        incr_nodes = _dump_node_ids(incr_db)
        incr_edges = _dump_edge_rows(incr_db)
        assert incr_nodes == full_nodes, (
            f"Node sets differ: {len(incr_nodes)} vs {len(full_nodes)}.\n"
            f"Missing: {full_nodes - incr_nodes}\n"
            f"Extra: {incr_nodes - full_nodes}"
        )
        assert incr_edges == full_edges, (
            f"Edge sets differ: {len(incr_edges)} vs {len(full_edges)}.\n"
            f"Missing: {full_edges - incr_edges}\n"
            f"Extra: {incr_edges - full_edges}"
        )

    def test_incremental_matches_full_bank_chat_system(self, tmp_path: Path) -> None:
        self._assert_equivalence(CORPUS, tmp_path / "bank_chat")

    def test_incremental_matches_full_cross_service_smoke(self, tmp_path: Path) -> None:
        self._assert_equivalence(
            TESTS_DIR / "fixtures" / "cross_service_smoke",
            tmp_path / "cross_service",
        )

    def test_incremental_matches_full_call_graph_smoke(self, tmp_path: Path) -> None:
        self._assert_equivalence(
            TESTS_DIR / "fixtures" / "call_graph_smoke",
            tmp_path / "call_graph",
        )

    def test_incremental_matches_full_http_caller_smoke(self, tmp_path: Path) -> None:
        self._assert_equivalence(
            TESTS_DIR / "fixtures" / "http_caller_smoke",
            tmp_path / "http_caller",
        )

    def test_incremental_matches_full_route_extraction_smoke(self, tmp_path: Path) -> None:
        self._assert_equivalence(
            TESTS_DIR / "fixtures" / "route_extraction_smoke",
            tmp_path / "route_extraction",
        )

    def test_incremental_multiple_files_changed(self, tmp_path: Path) -> None:
        # Use cross_service_smoke (smaller corpus) to avoid >50% dirty-set heuristic
        corpus = TESTS_DIR / "fixtures" / "cross_service_smoke"
        full_db = tmp_path / "full" / "code_graph.kuzu"
        _full_rebuild_into(corpus, full_db)

        incr_db = tmp_path / "incr" / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)

        # Pick all files from one service (small enough to stay under 50%)
        java_files = sorted(
            str(p.resolve().relative_to(corpus.resolve()))
            for p in corpus.rglob("*.java")
            if "svc-a" in str(p)
        )
        assert len(java_files) >= 2, f"Expected >= 2 svc-a files, got {java_files}"

        result = build_ast_graph_incremental(
            corpus, incr_db, set(java_files), verbose=False,
        )
        assert result == "incremental"

        assert _dump_node_ids(incr_db) == _dump_node_ids(full_db)
        assert _dump_edge_rows(incr_db) == _dump_edge_rows(full_db)


# ---- Fallback tests ----


class TestIncrementalFallback:
    def test_incremental_fallback_on_missing_deps_json(self, tmp_path: Path) -> None:
        corpus = CORPUS
        incr_db = tmp_path / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)
        # Remove .deps.json
        deps = incr_db.parent / ".deps.json"
        deps.unlink(missing_ok=True)
        result = build_ast_graph_incremental(
            corpus, incr_db, {"some/File.java"}, verbose=False,
        )
        assert result is None

    def test_incremental_fallback_on_stale_ontology(self, tmp_path: Path) -> None:
        corpus = CORPUS
        incr_db = tmp_path / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)
        deps = incr_db.parent / ".deps.json"
        data = json.loads(deps.read_text())
        data["ontology_version"] = 0
        deps.write_text(json.dumps(data))
        result = build_ast_graph_incremental(
            corpus, incr_db, {"some/File.java"}, verbose=False,
        )
        assert result is None

    def test_incremental_fallback_on_large_dirty_set(self, tmp_path: Path) -> None:
        corpus = CORPUS
        incr_db = tmp_path / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)
        # Mark >50% of files dirty
        deps = incr_db.parent / ".deps.json"
        idx = _read_dependency_index(deps)
        assert idx is not None
        all_files = list(idx.files.keys())
        most_files = set(all_files[: int(len(all_files) * 0.6) + 1])
        result = build_ast_graph_incremental(
            corpus, incr_db, most_files, verbose=False,
        )
        assert result is None


# ---- Closure tests ----


class TestClosureExpansion:
    def _make_deps_index(self, files: dict[str, FileDeps]) -> DepsIndex:
        return DepsIndex(version=1, ontology_version=ONTOLOGY_VERSION, files=files)

    def test_closure_includes_inverse_injects(self) -> None:
        idx = self._make_deps_index({
            "a/Foo.java": FileDeps(declares=["com.example.Foo"]),
            "a/Bar.java": FileDeps(injects=["com.example.Foo"]),
        })
        dirty = expand_to_closure({"a/Foo.java"}, idx)
        assert "a/Bar.java" in dirty

    def test_closure_includes_inverse_extends(self) -> None:
        idx = self._make_deps_index({
            "a/Base.java": FileDeps(declares=["com.example.Base"]),
            "a/Child.java": FileDeps(extends=["com.example.Base"]),
        })
        dirty = expand_to_closure({"a/Base.java"}, idx)
        assert "a/Child.java" in dirty

    def test_closure_includes_inverse_calls(self) -> None:
        idx = self._make_deps_index({
            "a/Service.java": FileDeps(declares=["com.example.Service"]),
            "a/Client.java": FileDeps(calls=["com.example.Service#run()"]),
        })
        dirty = expand_to_closure({"a/Service.java"}, idx)
        assert "a/Client.java" in dirty

    def test_closure_includes_inverse_overrides(self) -> None:
        idx = self._make_deps_index({
            "a/Base.java": FileDeps(declares=["com.example.Base"]),
            "a/Impl.java": FileDeps(overrides=["com.example.Base#method()"]),
        })
        dirty = expand_to_closure({"a/Base.java"}, idx)
        assert "a/Impl.java" in dirty

    def test_closure_includes_meta_annotation(self) -> None:
        idx = self._make_deps_index({
            "a/CustomAnno.java": FileDeps(declares=["com.example.CustomAnno"]),
            "a/User.java": FileDeps(uses_anno=["CustomAnno"]),
        })
        dirty = expand_to_closure({"a/CustomAnno.java"}, idx)
        assert "a/User.java" in dirty

    def test_closure_includes_forward_deps(self) -> None:
        idx = self._make_deps_index({
            "a/Foo.java": FileDeps(
                injects=["com.example.Service"],
                declares=["com.example.Foo"],
            ),
            "a/Service.java": FileDeps(declares=["com.example.Service"]),
        })
        dirty = expand_to_closure({"a/Foo.java"}, idx)
        assert "a/Service.java" in dirty

    def test_closure_empty_changed_returns_empty(self) -> None:
        idx = self._make_deps_index({"a/Foo.java": FileDeps()})
        dirty = expand_to_closure(set(), idx)
        assert dirty == set()

    def test_closure_unknown_path_ignored(self) -> None:
        idx = self._make_deps_index({"a/Foo.java": FileDeps()})
        dirty = expand_to_closure({"nonexistent/File.java"}, idx)
        assert dirty == set()


# ---- Incremental deps merge ----


class TestIncrementalDepsMerge:
    def test_incremental_deps_json_merge(self, tmp_path: Path) -> None:
        corpus = CORPUS
        incr_db = tmp_path / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)
        deps = incr_db.parent / ".deps.json"

        idx_before = _read_dependency_index(deps)
        assert idx_before is not None
        unchanged_file = next(iter(idx_before.files))

        changed_file = _get_any_java_file(corpus)
        result = build_ast_graph_incremental(
            corpus, incr_db, {changed_file}, verbose=False,
        )
        assert result == "incremental"

        idx_after = _read_dependency_index(deps)
        assert idx_after is not None
        # Unchanged file entries preserved
        assert unchanged_file in idx_after.files
        # Changed file entries updated
        assert changed_file in idx_after.files


# ---- Pass6 global invariant ----


class TestPass6GlobalInvariant:
    def test_incremental_pass6_global_invariant(self, tmp_path: Path) -> None:
        corpus = TESTS_DIR / "fixtures" / "cross_service_smoke"
        full_db = tmp_path / "full" / "code_graph.kuzu"
        _full_rebuild_into(corpus, full_db)

        incr_db = tmp_path / "incr" / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)

        changed_file = _get_any_java_file(corpus)
        result = build_ast_graph_incremental(
            corpus, incr_db, {changed_file}, verbose=False,
        )
        assert result == "incremental"

        # Compare HTTP_CALLS and ASYNC_CALLS match outcomes
        db_full = kuzu.Database(str(full_db))
        conn_full = kuzu.Connection(db_full)
        db_incr = kuzu.Database(str(incr_db))
        conn_incr = kuzu.Connection(db_incr)

        for label in ("HTTP_CALLS", "ASYNC_CALLS"):
            full_matches: dict[str, str] = {}
            r = conn_full.execute(
                f"MATCH (a)-[e:{label}]->(b) RETURN a.id, e.match"
            )
            while r.has_next():
                row = r.get_next()
                full_matches[row[0]] = row[1]

            incr_matches: dict[str, str] = {}
            r = conn_incr.execute(
                f"MATCH (a)-[e:{label}]->(b) RETURN a.id, e.match"
            )
            while r.has_next():
                row = r.get_next()
                incr_matches[row[0]] = row[1]

            assert full_matches == incr_matches, (
                f"{label} match outcomes differ: "
                f"full={full_matches} vs incr={incr_matches}"
            )

        conn_full.close()
        conn_incr.close()


# ---- Meta global stats ----


class TestIncrementalMetaStats:
    def test_incremental_meta_global_stats(self, tmp_path: Path) -> None:
        corpus = CORPUS
        full_db = tmp_path / "full" / "code_graph.kuzu"
        _full_rebuild_into(corpus, full_db)

        incr_db = tmp_path / "incr" / "code_graph.kuzu"
        _full_rebuild_into(corpus, incr_db)

        changed_file = _get_any_java_file(corpus)
        result = build_ast_graph_incremental(
            corpus, incr_db, {changed_file}, verbose=False,
        )
        assert result == "incremental"

        # Compare key meta fields
        db_full = kuzu.Database(str(full_db))
        conn_full = kuzu.Connection(db_full)
        db_incr = kuzu.Database(str(incr_db))
        conn_incr = kuzu.Connection(db_incr)

        for field in (
            "routes_total", "clients_total", "producers_total",
            "http_calls_total", "async_calls_total",
        ):
            r_full = conn_full.execute(
                f"MATCH (m:GraphMeta) WHERE m.key = 'graph' RETURN m.{field}"
            )
            val_full = r_full.get_next()[0] if r_full.has_next() else None
            r_incr = conn_incr.execute(
                f"MATCH (m:GraphMeta) WHERE m.key = 'graph' RETURN m.{field}"
            )
            val_incr = r_incr.get_next()[0] if r_incr.has_next() else None
            assert val_full == val_incr, f"{field}: full={val_full} vs incr={val_incr}"

        # Check last_rebuild_mode
        r = conn_incr.execute(
            "MATCH (m:GraphMeta) WHERE m.key = 'graph' RETURN m.last_rebuild_mode"
        )
        mode = r.get_next()[0] if r.has_next() else None
        assert mode == "incremental"

        r = conn_full.execute(
            "MATCH (m:GraphMeta) WHERE m.key = 'graph' RETURN m.last_rebuild_mode"
        )
        mode = r.get_next()[0] if r.has_next() else None
        assert mode == "full"

        conn_full.close()
        conn_incr.close()


# ---- CLI flag tests ----


class TestChangedPathsCLI:
    def test_changed_paths_cli_flag_valid(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        corpus = CORPUS
        kuzu_path = tmp_path / "code_graph.kuzu"
        _full_rebuild_into(corpus, kuzu_path)

        changed_file = _get_any_java_file(corpus)
        paths_file = tmp_path / "changed.txt"
        paths_file.write_text(changed_file + "\n")

        result = subprocess.run(
            [
                sys.executable, "build_ast_graph.py",
                "--source-root", str(corpus),
                "--kuzu-path", str(kuzu_path),
                "--changed-paths", str(paths_file),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0

    def test_changed_paths_cli_flag_empty(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        corpus = CORPUS
        kuzu_path = tmp_path / "code_graph.kuzu"
        _full_rebuild_into(corpus, kuzu_path)

        paths_file = tmp_path / "changed.txt"
        paths_file.write_text("")

        result = subprocess.run(
            [
                sys.executable, "build_ast_graph.py",
                "--source-root", str(corpus),
                "--kuzu-path", str(kuzu_path),
                "--changed-paths", str(paths_file),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
