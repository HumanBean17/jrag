"""Unit tests: Java AST extract + Kuzu build + RRF (no real repos required)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from java_ast_graph.extract import extract_file
from java_ast_graph.graph_retriever import (
    collect_graph_seeds,
    expand_interface_consumers,
    expand_neighbors_bidirectional,
    find_types_in_file_by_rel_path,
    type_kind_file_by_fqns,
)
from java_ast_graph.hybrid_rrf import fuse_vector_and_graph
from java_ast_graph.kuzu_io import open_connection
from tree_sitter import Language, Parser
import tree_sitter_java as tj


FIXTURE = Path(__file__).parent / "fixtures" / "ast_sample" / "Sample.java"


class TestJavaAstGraph(unittest.TestCase):
    def test_tree_sitter_parse(self) -> None:
        src = FIXTURE.read_bytes()
        p = Parser(Language(tj.language()))
        t = p.parse(src)
        self.assertFalse(t.root_node.has_error)

    def test_extract_sample(self) -> None:
        root = FIXTURE.parent
        f = extract_file(FIXTURE, "sample", root)
        self.assertIsNone(f.error)
        self.assertEqual(f.package, "com.example.app")
        fqns = {t.fqn for t in f.types}
        self.assertIn("com.example.app.Sample", fqns)
        self.assertIn("com.example.app.Sample.Inner", fqns)
        self.assertIn("com.example.app.IFace", fqns)

    def test_hybrid_rrf(self) -> None:
        v = [{"filename": "a/A.java", "text": "x"}]
        g = [
            {
                "fqn": "a.B",
                "file_key": "k::B.java",
                "text": "y",
                "filename": "B.java",
            }
        ]
        m = fuse_vector_and_graph(v, g, k=60)
        self.assertEqual(len(m), 2)
        for r in m:
            self.assertIn("_rrf_score", r)
            self.assertNotIn("_rrf_id", r)

    def test_hybrid_rrf_merges_same_file_vector_and_graph(self) -> None:
        v = [{"filename": "a/A.java", "text": "vector chunk", "_kind": "java"}]
        g = [
            {
                "fqn": "com.example.A",
                "file_key": "m::a/A.java",
                "filename": "a/A.java",
                "text": "graph body",
                "edge": "extends",
            }
        ]
        m = fuse_vector_and_graph(v, g, k=60)
        self.assertEqual(len(m), 1)
        row = m[0]
        self.assertEqual(row["text"], "vector chunk")
        self.assertIn("vector", row["_sources"])
        self.assertIn("graph", row["_sources"])
        self.assertEqual(row.get("fqn"), "com.example.A")
        self.assertNotIn("_rrf_id", row)

    def test_hybrid_rrf_merges_two_graph_rows_same_file(self) -> None:
        g = [
            {
                "fqn": "p.Outer",
                "file_key": "k::a/A.java",
                "filename": "a/A.java",
                "text": "t1",
                "edge": "e1",
            },
            {
                "fqn": "p.Inner",
                "file_key": "k::a/A.java",
                "filename": "a/A.java",
                "text": "t2",
                "edge": "e2",
            },
        ]
        m = fuse_vector_and_graph([], g, k=60)
        self.assertEqual(len(m), 1)
        self.assertIn("graph_fqns", m[0])
        self.assertEqual(set(m[0]["graph_fqns"]), {"p.Outer", "p.Inner"})

    def test_build_kuzu_from_fixture(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "kg"
        from java_ast_graph import build as bmod

        r = bmod.run_build(
            db_path=tmp,
            roots=[("sample", FIXTURE.parent)],
            quiet=True,
        )
        self.assertEqual(r, 0)
        _db, conn = open_connection(tmp)
        r2 = conn.execute("MATCH (t:Type) RETURN count(t)")
        n = r2.get_all()[0][0]
        self.assertGreaterEqual(n, 3)
        r3 = conn.execute(
            "MATCH (c:Type)-[:T_IMPLEMENTS]->(i:Type {fqn: $f}) RETURN c.fqn",
            {"f": "com.example.app.IFace"},
        )
        self.assertTrue(r3.get_all())
        by_file = find_types_in_file_by_rel_path(conn, "Sample.java", limit=20)
        self.assertIn("com.example.app.Sample", by_file)
        conn.close()

    def test_interface_consumer_expansion(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "kg"
        from java_ast_graph import build as bmod

        r = bmod.run_build(
            db_path=tmp,
            roots=[("sample", FIXTURE.parent)],
            quiet=True,
        )
        self.assertEqual(r, 0)
        _db, conn = open_connection(tmp)
        iface = "com.example.app.IFace"
        met = type_kind_file_by_fqns(conn, [iface])
        self.assertEqual(met[iface][0], "interface")
        rows = expand_interface_consumers(conn, [iface], limit=20)
        fqns = {str(r["fqn"]) for r in rows}
        self.assertIn("com.example.app.Sample", fqns)
        impl = [r for r in rows if r.get("edge") == "iface_impl"]
        self.assertTrue(impl)
        conn.close()

    def test_collect_graph_seeds_from_chunk_text(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "kg"
        from java_ast_graph import build as bmod

        r = bmod.run_build(
            db_path=tmp,
            roots=[("sample", FIXTURE.parent)],
            quiet=True,
        )
        self.assertEqual(r, 0)
        _db, conn = open_connection(tmp)
        vrows = [
            {
                "filename": "not_in_index/FakeChunk.java",
                "text": "Only IFace in chunk body, not in query zzz.",
            }
        ]
        q = "zzz unrelated"
        s = collect_graph_seeds(
            q,
            vrows,
            conn,
            include_chunk_seeds=True,
        )
        self.assertIn("com.example.app.IFace", s)
        s2 = collect_graph_seeds(
            q,
            vrows,
            conn,
            include_chunk_seeds=False,
        )
        self.assertNotIn("com.example.app.IFace", s2)
        conn.close()

    def test_expand_neighbors_tiered_reaches_2hop(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "kg"
        from java_ast_graph import build as bmod

        r = bmod.run_build(
            db_path=tmp,
            roots=[("sample", FIXTURE.parent)],
            quiet=True,
        )
        self.assertEqual(r, 0)
        _db, conn = open_connection(tmp)
        # From Dep alone, 2-hop bidirectional can reach more than 1-hop.
        r1 = expand_neighbors_bidirectional(
            conn, ["com.example.app.Dep"], depth=1, limit=200
        )
        r2 = expand_neighbors_bidirectional(
            conn, ["com.example.app.Dep"], depth=2, limit=200
        )
        f1 = {str(x["fqn"]) for x in r1}
        f2 = {str(x["fqn"]) for x in r2}
        self.assertTrue(f2.issuperset(f1))
        self.assertGreaterEqual(len(f2), len(f1))
        conn.close()


if __name__ == "__main__":
    unittest.main()
