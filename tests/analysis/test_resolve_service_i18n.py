"""i18n for the shared resolve/absence producers (spec D7).

These modules serve BOTH the CLI (localized when the CLI entrypoint set the
locale) and the MCP server (never sets a locale → English regardless of
exported env). The tests here pin the CLI side; the MCP-side invariant lives
in ``tests/package/test_i18n_cli.py::test_mcp_process_stays_english_with_env_set``.
"""
from __future__ import annotations

from java_codebase_rag import i18n
from java_codebase_rag.analysis.resolve_service import resolve_v2


def test_resolve_message_russian():
    i18n.set_locale("ru")
    try:
        out = resolve_v2("   ")
    finally:
        i18n.reset_locale()
    assert out.message is not None
    assert "Недопустимый идентификатор" in out.message
    assert "Invalid identifier" not in out.message

    i18n.set_locale("ru")
    try:
        out2 = resolve_v2("Foo*")
    finally:
        i18n.reset_locale()
    assert out2.message is not None
    assert "одстановочные" in out2.message  # matches both cases of the RU word


def test_resolve_message_english_default():
    out = resolve_v2("   ")
    assert out.message == "Invalid identifier: whitespace only"

    out2 = resolve_v2("Foo*")
    assert out2.message == (
        "Wildcards (* and ?) are not supported in resolve; "
        "use search(query=...) for ranked text search."
    )
