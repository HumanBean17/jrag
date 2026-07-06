"""Tests for the serialized Lance optimize helper (``java_codebase_rag.lance_optimize``).

These tests fake the lancedb async connection/table so the retry logic is
exercised without a real LanceDB on disk. They assert invariants (retry on
commit-conflict, no retry on other errors, missing tables skipped) rather than
overspecifying the lancedb API surface — see ``tests/README.md``.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _conflict_error(version: int = 4424) -> RuntimeError:
    return RuntimeError(
        "lance error: Retryable commit conflict for version "
        f"{version}: This Rewrite transaction was preempted by concurrent "
        "transaction Delete at version 4424. Please retry."
    )


class _FakeTable:
    """Fake async table whose ``optimize`` follows a scripted call sequence."""

    def __init__(self, name: str, outcomes: list[BaseException | None]) -> None:
        self.name = name
        self._outcomes = list(outcomes)
        self.optimize_calls = 0
        # PR-SEARCH-3: optimize_lance_tables also builds the FTS index via
        # ``await table.create_index("text", config=FTS(), replace=True)``.
        # Track those calls so a test can prove the FTS path actually runs
        # (instead of being silently swallowed by the broad except).
        self.create_index_calls: list[dict] = []

    async def optimize(self, *args, **kwargs):  # noqa: ANN002, ANN003 — fake
        self.optimize_calls += 1
        if not self._outcomes:
            return None
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return None

    async def create_index(self, *args, **kwargs):  # noqa: ANN002, ANN003 — fake
        self.create_index_calls.append({"args": args, "kwargs": kwargs})
        return None


class _FakeListResponse:
    def __init__(self, names: set[str]) -> None:
        self.tables = list(names)


class _FakeConnection:
    """Fake async connection: ``list_tables``/``table_names`` + ``open_table`` + sync ``close``."""

    def __init__(self, *, table_names: set[str], tables: dict[str, _FakeTable]) -> None:
        self._names = table_names
        self._tables = tables
        self.closed = False

    async def list_tables(self):  # noqa: ANN201 — fake
        return _FakeListResponse(self._names)

    async def open_table(self, name: str) -> _FakeTable:
        return self._tables[name]

    def close(self) -> None:
        self.closed = True


class _FakeLanceDB:
    """Module stand-in for ``lancedb`` exposing ``connect_async``."""

    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def connect_async(self, uri: str):  # noqa: ANN201 — fake
        return self._connection


def _install_fake_lancedb(monkeypatch, connection: _FakeConnection) -> _FakeLanceDB:
    fake_module = _FakeLanceDB(connection)
    monkeypatch.setitem(sys.modules, "lancedb", fake_module)
    return fake_module


async def test_optimize_retries_commit_conflict_then_succeeds(monkeypatch, tmp_path) -> None:
    """A Retryable commit conflict is retried until ``optimize`` succeeds."""
    from java_codebase_rag import lance_optimize

    table = _FakeTable(
        lance_optimize.LANCE_TABLE_NAMES[0],
        [_conflict_error(), _conflict_error(), None],  # 2 conflicts, then ok
    )
    conn = _FakeConnection(
        table_names={lance_optimize.LANCE_TABLE_NAMES[0]},
        tables={lance_optimize.LANCE_TABLE_NAMES[0]: table},
    )
    _install_fake_lancedb(monkeypatch, conn)

    results = await lance_optimize.optimize_lance_tables(tmp_path, quiet=True)
    assert results[lance_optimize.LANCE_TABLE_NAMES[0]] == "ok"
    assert table.optimize_calls == 3  # 2 retries + 1 success


async def test_optimize_builds_fts_index_after_success(monkeypatch, tmp_path) -> None:
    """A successful optimize builds the FTS index (PR-SEARCH-3).

    Guards against a silent regression: ``optimize_lance_tables`` builds FTS via
    ``await table.create_index("text", config=FTS(), replace=True)`` inside a
    broad ``except`` that swallows failures. Without this test the FTS call is
    never exercised (the ``_FakeTable`` previously had no ``create_index`` and
    ``lancedb.index`` was unmocked), so a future lancedb upgrade that moved
    ``FTS`` or changed the signature would break index-time FTS while every
    optimize test stayed green.
    """
    import types

    from java_codebase_rag import lance_optimize

    name = lance_optimize.LANCE_TABLE_NAMES[0]
    table = _FakeTable(name, [None])  # optimize succeeds on first try
    conn = _FakeConnection(table_names={name}, tables={name: table})
    _install_fake_lancedb(monkeypatch, conn)
    # Make ``from lancedb.index import FTS`` resolve against a stand-in module.
    index_mod = types.ModuleType("lancedb.index")

    class FTS:  # stands in for lancedb.index.FTS config object
        pass

    index_mod.FTS = FTS
    monkeypatch.setitem(sys.modules, "lancedb.index", index_mod)

    results = await lance_optimize.optimize_lance_tables(tmp_path, quiet=True)

    assert results[name] == "ok"
    assert len(table.create_index_calls) == 1, (
        f"expected FTS create_index once after optimize, got {table.create_index_calls}"
    )
    call = table.create_index_calls[0]
    assert call["args"] and call["args"][0] == "text", (
        f"FTS index must target the 'text' column, got args={call['args']}"
    )
    assert call["kwargs"].get("replace") is True, (
        f"FTS index must be built with replace=True, got kwargs={call['kwargs']}"
    )
    assert conn.closed is True


async def test_optimize_does_not_retry_non_conflict_error(monkeypatch, tmp_path) -> None:
    """A non-conflict exception is re-raised (captured per-table), never retried."""
    from java_codebase_rag import lance_optimize

    boom = ValueError("totally unrelated disk error")
    table = _FakeTable(lance_optimize.LANCE_TABLE_NAMES[0], [boom])
    conn = _FakeConnection(
        table_names={lance_optimize.LANCE_TABLE_NAMES[0]},
        tables={lance_optimize.LANCE_TABLE_NAMES[0]: table},
    )
    _install_fake_lancedb(monkeypatch, conn)

    results = await lance_optimize.optimize_lance_tables(tmp_path, quiet=True)
    # The error is captured in the result (not re-raised out of the helper) so
    # the caller can report it; but it must not have been retried.
    assert results[lance_optimize.LANCE_TABLE_NAMES[0]].startswith("error:")
    assert "totally unrelated disk error" in results[lance_optimize.LANCE_TABLE_NAMES[0]]
    assert table.optimize_calls == 1


async def test_optimize_reports_missing_table_as_skipped(monkeypatch, tmp_path) -> None:
    """A table name absent from the DB is reported skipped, with no exception."""
    from java_codebase_rag import lance_optimize

    # DB contains only the java table; sql + yaml are absent (e.g. a repo with
    # no SQL/YAML) and must come back as skipped.
    java_name = lance_optimize.LANCE_TABLE_NAMES[0]
    java_table = _FakeTable(java_name, [None])
    conn = _FakeConnection(table_names={java_name}, tables={java_name: java_table})
    _install_fake_lancedb(monkeypatch, conn)

    results = await lance_optimize.optimize_lance_tables(tmp_path, quiet=True)
    assert results[java_name] == "ok"
    for missing in lance_optimize.LANCE_TABLE_NAMES[1:]:
        assert results[missing] == "skipped"


async def test_optimize_closes_connection_even_on_open_failure(monkeypatch, tmp_path) -> None:
    """``db.close()`` runs in finally even if a table fails to open."""
    from java_codebase_rag import lance_optimize

    name = lance_optimize.LANCE_TABLE_NAMES[0]

    class _ConnOpenFails(_FakeConnection):
        async def open_table(self, name: str) -> _FakeTable:
            raise OSError("cannot open")

    conn = _ConnOpenFails(table_names={name}, tables={})
    _install_fake_lancedb(monkeypatch, conn)

    results = await lance_optimize.optimize_lance_tables(tmp_path, quiet=True)
    assert results[name].startswith("error:")
    assert conn.closed is True


def test_lance_table_names_constant_matches_search_lancedb_tables() -> None:
    """The single source of truth agrees with the search-side TABLES mapping."""
    from java_codebase_rag.lance_optimize import LANCE_TABLE_NAMES

    # Imported lazily to avoid pulling sentence-transformers at collection time.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from search_lancedb import TABLES
    finally:
        sys.path.pop(0)
    assert set(LANCE_TABLE_NAMES) == set(TABLES.values())
    assert len(LANCE_TABLE_NAMES) == 3
