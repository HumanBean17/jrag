"""Serialized post-flow LanceDB optimize with commit-conflict retry.

cocoindex 1.0.7 schedules ``table.optimize()`` (a LanceDB **Rewrite**/compaction
transaction) as a *background* ``asyncio`` task that races concurrent
``table.delete()`` (**Delete**) transactions emitted by later mutation batches.
LanceDB does not allow a Rewrite to commit concurrently with a Delete
(upstream lancedb#1504 — "We do not support concurrent deletes right now"),
which surfaces as a flood of::

    RuntimeError: lance error: Retryable commit conflict for version N: \
This Rewrite transaction was preempted by concurrent transaction Delete ...

To eliminate the race, the flow (``java_index_flow_lancedb.py``) disables the
in-flight background optimize entirely by raising
``num_transactions_before_optimize`` to a value that is effectively never
reached. This module then performs a *single*, serialized optimize after the
flow returns (exit 0 → no concurrent writers), retrying the rare residual
commit conflict that two internal compaction passes can still produce.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Single source of truth for the three Lance table names created by the flow.
# Keep in sync with ``search_lancedb.TABLES`` (the values there mirror these).
LANCE_TABLE_NAMES: tuple[str, ...] = (
    "javacodeindex_java_code",
    "sqlschemaindex_sql_schema",
    "yamlconfigindex_yaml_config",
)

# Commit conflicts are transient; a handful of exponential-backoff retries is
# enough because, post-flow, there are no concurrent writers — only successive
# optimize/compaction passes within this single serialized call can still
# transiently preempt one another.
_MAX_ATTEMPTS = 6
_BASE_BACKOFF_S = 0.1

# Substrings identifying the retryable Lance commit-conflict error. LanceDB
# wraps the underlying lance error text into the raised ``RuntimeError`` str,
# so a substring match is the robust detector (no dedicated exception type).
_RETRYABLE_MARKERS = (
    "Retryable commit conflict",
    "preempted by concurrent transaction",
)


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc)
    return any(marker in text for marker in _RETRYABLE_MARKERS)


async def _list_table_names(db: object) -> set[str]:
    """Existing table names across LanceDB API variants (``list_tables`` ≥ ``table_names``)."""
    if hasattr(db, "list_tables"):
        response = await db.list_tables()
        return set(getattr(response, "tables", response))
    return set(await db.table_names())


async def optimize_lance_tables(index_dir: Path, *, quiet: bool = False) -> dict[str, str]:
    """Optimize all known Lance tables under *index_dir*, serially, with retry.

    Runs ``table.optimize()`` for each name in :data:`LANCE_TABLE_NAMES` that
    exists in the DB. Retryable commit conflicts are retried with exponential
    backoff; any other exception (or an exhausted retry budget) is captured
    per-table in the returned dict and logged to **stderr** — never stdout,
    since this is callable from stdio-MCP / JSON-stdout contexts.

    Args:
        index_dir: directory holding the Lance tables (the flow's LanceDB URI).
        quiet: when True, suppress the per-table success/skip info lines on
            stderr (errors are always logged).

    Returns:
        Mapping of table name → status. Values are ``"ok"``, ``"skipped"``
        (table absent — e.g. a repo with no SQL/YAML), or ``"error: <text>"``.
    """
    # Lazy import: the flow imports this module for LANCE_TABLE_NAMES and must
    # not pay the lancedb import cost at flow-definition time.
    import lancedb

    results: dict[str, str] = {}
    db = await lancedb.connect_async(str(index_dir))
    try:
        try:
            existing = await _list_table_names(db)
        except Exception as exc:
            print(
                f"java-codebase-rag: optimize: failed to list tables in "
                f"{index_dir}: {exc}",
                file=sys.stderr,
            )
            return {name: f"error: list failed: {exc}" for name in LANCE_TABLE_NAMES}

        for name in LANCE_TABLE_NAMES:
            if name not in existing:
                results[name] = "skipped"
                if not quiet:
                    print(
                        f"java-codebase-rag: optimize: {name} absent, skipped",
                        file=sys.stderr,
                    )
                continue
            try:
                table = await db.open_table(name)
            except Exception as exc:
                results[name] = f"error: open failed: {exc}"
                print(
                    f"java-codebase-rag: optimize: {name} open failed: {exc}",
                    file=sys.stderr,
                )
                continue

            last_exc: BaseException | None = None
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    await table.optimize()
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if _is_retryable(exc) and attempt < _MAX_ATTEMPTS - 1:
                        await asyncio.sleep(_BASE_BACKOFF_S * (2**attempt))
                        continue
                    # Non-retryable, or retries exhausted: stop the loop and
                    # surface below — do not swallow silently.
                    break

            if last_exc is None:
                results[name] = "ok"
                if not quiet:
                    print(
                        f"java-codebase-rag: optimize: {name} ok",
                        file=sys.stderr,
                    )
            else:
                results[name] = f"error: {last_exc}"
                print(
                    f"java-codebase-rag: optimize: {name} failed: {last_exc}",
                    file=sys.stderr,
                )
    finally:
        # ``AsyncConnection.close`` is a *sync* method in lancedb 0.30.x.
        db.close()
    return results
