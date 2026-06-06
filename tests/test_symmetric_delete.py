"""Tests for symmetric delete helpers (PR-T2).

Each delete helper takes ``(conn, file_path)`` and returns the deleted row
count.  The helpers are additive — nothing calls them yet (that's PR-T3).
"""

from __future__ import annotations

import kuzu
import pytest

from _builders import build_kuzu_to


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path, corpus_root):
    """Per-test fresh writeable Kuzu DB from bank-chat-system (pass1-5)."""
    db_path = tmp_path / "code_graph.kuzu"
    build_kuzu_to(corpus_root, db_path, max_pass=5)
    db = kuzu.Database(str(db_path))
    return kuzu.Connection(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count(conn, cypher, params=None):
    r = conn.execute(cypher, params or {})
    return int(r.get_next()[0]) if r.has_next() else 0


def _find_file_with(conn, cypher):
    r = conn.execute(cypher)
    assert r.has_next(), f"no matching rows in fixture: {cypher}"
    return str(r.get_next()[0])


# ---------------------------------------------------------------------------
# Per-helper tests
# ---------------------------------------------------------------------------

class TestDeleteSymbolsForFile:
    def test_delete_symbols_for_file(self, conn):
        from build_ast_graph import delete_symbols_for_file

        fp = _find_file_with(
            conn,
            "MATCH (s:Symbol) WHERE s.kind = 'class' "
            "RETURN s.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (s:Symbol) WHERE s.filename = $fp RETURN count(s) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_symbols_for_file(conn, fp)
        assert n > 0

        after = _count(
            conn,
            "MATCH (s:Symbol) WHERE s.filename = $fp RETURN count(s) AS n",
            {"fp": fp},
        )
        assert after == 0

    def test_deletes_declares_edges(self, conn):
        from build_ast_graph import delete_symbols_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:DECLARES]->(b:Symbol) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        delete_symbols_for_file(conn, fp)

        remaining = _count(
            conn,
            "MATCH (a:Symbol)-[e:DECLARES]->(b:Symbol) "
            "WHERE a.filename = $fp OR b.filename = $fp "
            "RETURN count(e) AS n",
            {"fp": fp},
        )
        assert remaining == 0


class TestDeleteExtendsForFile:
    def test_delete_extends_for_file(self, conn):
        from build_ast_graph import delete_extends_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:EXTENDS]->(b:Symbol) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (a:Symbol)-[e:EXTENDS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_extends_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (a:Symbol)-[e:EXTENDS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert after == 0


class TestDeleteImplementsForFile:
    def test_delete_implements_for_file(self, conn):
        from build_ast_graph import delete_implements_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:IMPLEMENTS]->(b:Symbol) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (a:Symbol)-[e:IMPLEMENTS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_implements_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (a:Symbol)-[e:IMPLEMENTS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert after == 0


class TestDeleteInjectsForFile:
    def test_delete_injects_for_file(self, conn):
        from build_ast_graph import delete_injects_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:INJECTS]->(b:Symbol) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (a:Symbol)-[e:INJECTS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_injects_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (a:Symbol)-[e:INJECTS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert after == 0


class TestDeleteCallsForFile:
    def test_delete_calls_for_file(self, conn):
        from build_ast_graph import delete_calls_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (a:Symbol)-[e:CALLS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_calls_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (a:Symbol)-[e:CALLS]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert after == 0

    def test_deletes_unresolved_call_sites(self, conn):
        from build_ast_graph import delete_calls_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        delete_calls_for_file(conn, fp)

        remaining = _count(
            conn,
            "MATCH (a:Symbol)-[e:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert remaining == 0


class TestDeleteRoutesForFile:
    def test_delete_routes_for_file(self, conn):
        from build_ast_graph import delete_routes_for_file

        fp = _find_file_with(
            conn,
            "MATCH (r:Route) RETURN r.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (r:Route) WHERE r.filename = $fp RETURN count(r) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_routes_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (r:Route) WHERE r.filename = $fp RETURN count(r) AS n",
            {"fp": fp},
        )
        assert after == 0

    def test_deletes_exposes_edges(self, conn):
        from build_ast_graph import delete_routes_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:EXPOSES]->(r:Route) "
            "RETURN DISTINCT r.filename AS fn LIMIT 1",
        )
        delete_routes_for_file(conn, fp)

        remaining = _count(
            conn,
            "MATCH (a:Symbol)-[e:EXPOSES]->(r:Route) "
            "WHERE r.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert remaining == 0


class TestDeleteClientsForFile:
    def test_delete_clients_for_file(self, conn):
        from build_ast_graph import delete_clients_for_file

        fp = _find_file_with(
            conn,
            "MATCH (c:Client) RETURN c.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (c:Client) WHERE c.filename = $fp RETURN count(c) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_clients_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (c:Client) WHERE c.filename = $fp RETURN count(c) AS n",
            {"fp": fp},
        )
        assert after == 0

    def test_deletes_declares_client_edges(self, conn):
        from build_ast_graph import delete_clients_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:DECLARES_CLIENT]->(c:Client) "
            "RETURN DISTINCT c.filename AS fn LIMIT 1",
        )
        delete_clients_for_file(conn, fp)

        remaining = _count(
            conn,
            "MATCH (a:Symbol)-[e:DECLARES_CLIENT]->(c:Client) "
            "WHERE c.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert remaining == 0


class TestDeleteProducersForFile:
    def test_delete_producers_for_file(self, conn):
        from build_ast_graph import delete_producers_for_file

        fp = _find_file_with(
            conn,
            "MATCH (p:Producer) RETURN p.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (p:Producer) WHERE p.filename = $fp RETURN count(p) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_producers_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (p:Producer) WHERE p.filename = $fp RETURN count(p) AS n",
            {"fp": fp},
        )
        assert after == 0

    def test_deletes_declares_producer_edges(self, conn):
        from build_ast_graph import delete_producers_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:DECLARES_PRODUCER]->(p:Producer) "
            "RETURN DISTINCT p.filename AS fn LIMIT 1",
        )
        delete_producers_for_file(conn, fp)

        remaining = _count(
            conn,
            "MATCH (a:Symbol)-[e:DECLARES_PRODUCER]->(p:Producer) "
            "WHERE p.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert remaining == 0


class TestDeleteHttpCallsForFile:
    def test_delete_http_calls_for_file(self, conn):
        from build_ast_graph import delete_http_calls_for_file

        fp = _find_file_with(
            conn,
            "MATCH (c:Client)-[:HTTP_CALLS]->(r:Route) "
            "RETURN DISTINCT c.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) "
            "WHERE c.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_http_calls_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) "
            "WHERE c.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert after == 0


class TestDeleteAsyncCallsForFile:
    def test_delete_async_calls_for_file(self, conn):
        from build_ast_graph import delete_async_calls_for_file

        fp = _find_file_with(
            conn,
            "MATCH (p:Producer)-[:ASYNC_CALLS]->(r:Route) "
            "RETURN DISTINCT p.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) "
            "WHERE p.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_async_calls_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) "
            "WHERE p.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert after == 0


class TestDeleteOverridesForFile:
    def test_delete_overrides_for_file(self, conn):
        from build_ast_graph import delete_overrides_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:OVERRIDES]->(b:Symbol) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        before = _count(
            conn,
            "MATCH (a:Symbol)-[e:OVERRIDES]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert before > 0

        n = delete_overrides_for_file(conn, fp)
        assert n == before

        after = _count(
            conn,
            "MATCH (a:Symbol)-[e:OVERRIDES]->(b:Symbol) "
            "WHERE a.filename = $fp RETURN count(e) AS n",
            {"fp": fp},
        )
        assert after == 0


class TestDeleteAllForFile:
    def test_delete_all_for_file(self, conn):
        from build_ast_graph import delete_all_for_file

        fp = _find_file_with(
            conn,
            "MATCH (s:Symbol) WHERE s.kind = 'class' "
            "RETURN s.filename AS fn LIMIT 1",
        )
        result = delete_all_for_file(conn, fp)

        assert isinstance(result, dict)
        assert len(result) > 0
        assert all(isinstance(v, int) for v in result.values())

        # Verify symbols are gone
        after = _count(
            conn,
            "MATCH (s:Symbol) WHERE s.filename = $fp RETURN count(s) AS n",
            {"fp": fp},
        )
        assert after == 0

    def test_calls_each_helper(self, conn):
        """delete_all_for_file should return counts from each sub-helper."""
        from build_ast_graph import delete_all_for_file

        fp = _find_file_with(
            conn,
            "MATCH (s:Symbol) WHERE s.kind = 'class' "
            "RETURN s.filename AS fn LIMIT 1",
        )
        result = delete_all_for_file(conn, fp)

        expected_keys = {
            "symbols", "extends", "implements", "injects",
            "calls", "routes", "clients", "producers",
            "http_calls", "async_calls", "overrides",
        }
        assert expected_keys == set(result.keys())


class TestDeleteEdgeCases:
    def test_delete_idempotent(self, conn):
        from build_ast_graph import delete_extends_for_file

        fp = _find_file_with(
            conn,
            "MATCH (a:Symbol)-[:EXTENDS]->(b:Symbol) "
            "RETURN DISTINCT a.filename AS fn LIMIT 1",
        )
        first = delete_extends_for_file(conn, fp)
        assert first > 0

        second = delete_extends_for_file(conn, fp)
        assert second == 0

    def test_delete_unknown_file_returns_zero(self, conn):
        from build_ast_graph import delete_all_for_file

        bogus = "no/such/File.java"
        result = delete_all_for_file(conn, bogus)

        assert all(v == 0 for v in result.values())
