"""``jrag`` auto ``--service`` scope (cwd-derived) — parity with the MCP ``ScopeManager``.

When the index is built at the SYSTEM level (e.g. ``tests/bank-chat-system``
covering ``chat-core`` + ``chat-assign``) but the agent's cwd is inside ONE
microservice, the CLI defaults ``--service`` to that microservice so the other
service's results don't leak in (mirrors ``server.py`` ``ScopeManager`` wired
into ``find``/``search``/``neighbors``).

These tests build on the shared session ``ladybug_db_path`` (both services'
symbols) and drive ``main([...])`` with ``monkeypatch.chdir`` to simulate the
agent's working directory. ``find``/``topics``/``impact`` are graph-only (no
Lance); ``search`` mocks ``search_v2`` to inspect the NodeFilter.
"""
from __future__ import annotations

import json
from pathlib import Path


def _run(
    monkeypatch,
    capsys,
    corpus_root: Path,
    ladybug_db_path: Path,
    argv: list[str],
    *,
    cwd: Path,
) -> tuple[int, dict]:
    """Set env (source root + index dir), chdir, run jrag.main, return (rc, json)."""
    from java_codebase_rag.jrag import main

    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(ladybug_db_path.parent))
    monkeypatch.chdir(cwd)
    rc = main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    return rc, payload


def _microservices(payload: dict) -> list[str]:
    return [str(n.get("microservice") or "") for n in payload.get("nodes", {}).values()]


def _has_auto_scope_notice(payload: dict) -> bool:
    return any("auto-scope" in w for w in payload.get("warnings", []))


# ---------------------------------------------------------------------------
# 1. find (query mode) is scoped to the cwd service
# ---------------------------------------------------------------------------


def test_find_query_scoped_to_cwd_service(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """``find Client`` from chat-assign/ must NOT surface the chat-core Client.

    ``Client`` exists only as ``com.bank.chat.domain.Client`` (chat-core), so
    under auto-scope the name search runs with ``microservice="chat-assign"``
    and returns nothing; the chat-core entity no longer leaks in.
    """
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["find", "Client", "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    assert rc == 0, payload
    assert payload["status"] == "ok"
    assert not _microservices(payload), (
        f"expected 0 nodes under chat-assign scope, got: {_microservices(payload)}"
    )
    assert _has_auto_scope_notice(payload), payload


def test_find_query_unscoped_at_corpus_root(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """From the system root, no microservice is detected → no auto-scope.

    The chat-core ``Client`` is visible again and there is no auto-scope notice.
    """
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["find", "Client", "--format", "json"],
        cwd=corpus_root,
    )
    assert rc == 0, payload
    ms = _microservices(payload)
    assert "chat-core" in ms, f"chat-core Client should be visible at system root: {ms}"
    assert not _has_auto_scope_notice(payload), payload


# ---------------------------------------------------------------------------
# 4 & 5. explicit --service wins; --no-auto-scope disables
# ---------------------------------------------------------------------------


def test_explicit_service_overrides_auto_scope(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """``--service chat-core`` from chat-assign/ wins over the cwd-derived scope."""
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["find", "Client", "--service", "chat-core", "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    assert rc == 0, payload
    assert "chat-core" in _microservices(payload), payload
    # Explicit flag → _service_user=True → no auto-scope notice.
    assert not _has_auto_scope_notice(payload), payload


def test_no_auto_scope_flag_disables(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """``--no-auto-scope`` from chat-assign/ shows cross-service results again."""
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["find", "Client", "--no-auto-scope", "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    assert rc == 0, payload
    assert "chat-core" in _microservices(payload), payload
    assert not _has_auto_scope_notice(payload), payload


def test_no_auto_scope_env_disables(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """``JRAG_NO_AUTO_SCOPE=1`` is equivalent to the flag."""
    monkeypatch.setenv("JRAG_NO_AUTO_SCOPE", "1")
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["find", "Client", "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    assert rc == 0, payload
    assert "chat-core" in _microservices(payload), payload


# ---------------------------------------------------------------------------
# 2. search — the auto-scope reaches the NodeFilter pushed into search_v2
# ---------------------------------------------------------------------------


def test_search_filter_carries_auto_scope(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """search builds ``filter.microservice`` from the cwd-derived default."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(ladybug_db_path.parent))
    monkeypatch.chdir(corpus_root / "chat-assign")

    captured: dict = {}

    def mock_search_v2(query, **kwargs):
        # Capture the FIRST (real search) call's filter. The zero-result
        # guidance probe (PR-SEARCH-4) makes a second call with filter=None
        # when an empty result has a filter set, so capturing every call
        # would overwrite this with the probe's None.
        if "filter" not in captured:
            captured["filter"] = kwargs.get("filter")
        return mcp_v2.SearchOutput(
            success=True, results=[], limit=kwargs.get("limit", 5),
            offset=kwargs.get("offset", 0), advisories=[],
        )

    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)
    rc = main(["search", "anything", "--format", "json"])
    capsys.readouterr()  # drain
    assert rc == 0
    nf = captured.get("filter")
    assert nf is not None, "search_v2 was not called"
    assert getattr(nf, "microservice", None) == "chat-assign", (
        f"auto-scope did not reach search NodeFilter: {nf!r}"
    )


# ---------------------------------------------------------------------------
# 6. impact — the post-filter warning is gated on _service_user
# ---------------------------------------------------------------------------


IMPACT_SYMBOL = "com.bank.chat.assign.integration.AuditLogClient"


def test_impact_no_warning_under_auto_scope(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Auto-injected --service still filters impact results but emits NO
    'post-filter on impact' caveat (the user didn't ask for --service)."""
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["impact", IMPACT_SYMBOL, "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    # Resolve may legitimately yield not_found/many on a fixture symbol; the
    # only contract here is the WARNING shape, so accept ok/not_found/error.
    assert rc in (0, 2), payload
    warnings = payload.get("warnings", [])
    assert not any("post-filter on impact" in w for w in warnings), (
        f"auto-scope should not trigger the explicit--service caveat: {warnings}"
    )


def test_impact_warns_under_explicit_service(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Explicit ``--service`` keeps the 'post-filter on impact' caveat."""
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["impact", IMPACT_SYMBOL, "--service", "chat-assign", "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    assert rc in (0, 2), payload
    # The caveat must be present IF the command reached the impact stage
    # (status ok). On resolve failure there's nothing to caveat about.
    if payload.get("status") == "ok":
        assert any("post-filter on impact" in w for w in payload.get("warnings", [])), payload


# ---------------------------------------------------------------------------
# 7. topics — the 6th transparency seam (topics builds its own envelope)
# ---------------------------------------------------------------------------


def test_topics_scoped_and_notices(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """``topics`` from chat-assign/ lists only chat-assign producers and carries
    the auto-scope notice (topics does not go through _render_listing)."""
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["topics", "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    assert rc == 0, payload
    # Every producer row inside every topic group must be chat-assign.
    leaked = []
    for node in payload.get("nodes", {}).values():
        producers = node.get("producers", []) if isinstance(node, dict) else []
        for prod in producers:
            ms = str(prod.get("microservice") or "")
            if ms and ms != "chat-assign":
                leaked.append(ms)
    assert not leaked, f"cross-service producers leaked under chat-assign scope: {leaked}"
    assert _has_auto_scope_notice(payload), payload


# ---------------------------------------------------------------------------
# Bonus: an EXCLUDED command (status) does not get auto-scoped (no notice,
# no spurious "--service not applied" warning from the cwd-derived value).
# ---------------------------------------------------------------------------


def test_excluded_command_not_auto_scoped(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """``status`` is orientation (not opt-in): no auto-scope, no spurious warning."""
    rc, payload = _run(
        monkeypatch, capsys, corpus_root, ladybug_db_path,
        ["status", "--format", "json"],
        cwd=corpus_root / "chat-assign",
    )
    assert rc == 0, payload
    assert not _has_auto_scope_notice(payload), payload
    assert not any("--service is not applied" in w for w in payload.get("warnings", [])), payload
