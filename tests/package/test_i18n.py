"""Unit tests for the ``java_codebase_rag.i18n`` key-catalog runtime.

Covers: locale state (default en), ``tr``/``ntr`` lookup + formatting, the
CLDR Russian plural rule (one/few/many), argv lang scanning/stripping, the
CLI override stash, help-time locale precedence, catalog parity and shape,
and a static guard that every literal ``tr("...")``/``ntr("...")`` call site
uses a key that exists in the EN catalogs.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from java_codebase_rag import i18n
from java_codebase_rag import i18n_messages_en
from java_codebase_rag import i18n_messages_help_en
from java_codebase_rag import i18n_messages_help_ru
from java_codebase_rag import i18n_messages_ru

_SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "java_codebase_rag"

_TR_CALL_RE = re.compile(r"\b_?n?tr\(\s*[\"']([A-Z0-9_]+)[\"']")


@pytest.fixture(autouse=True)
def _clean_locale_state():
    """Every test starts and ends in the default state: en, no override."""
    i18n.reset_locale()
    i18n.set_cli_lang_override(None)
    yield
    i18n.reset_locale()
    i18n.set_cli_lang_override(None)


def test_default_locale_is_english():
    assert i18n.get_locale() == "en"


def test_set_locale_valid_and_invalid():
    i18n.set_locale("ru")
    assert i18n.get_locale() == "ru"
    with pytest.raises(ValueError):
        i18n.set_locale("fr")
    i18n.reset_locale()
    assert i18n.get_locale() == "en"


def test_tr_english_and_russian():
    assert i18n.tr("MSG_ERASE_ABORTED") == "Aborted."
    i18n.set_locale("ru")
    assert i18n.tr("MSG_ERASE_ABORTED") == "Отменено."


def test_tr_placeholder_formatting(monkeypatch):
    monkeypatch.setitem(i18n_messages_en.MESSAGES, "MSG_TEST_SCRATCH", "{n} files")
    monkeypatch.setitem(i18n_messages_ru.MESSAGES, "MSG_TEST_SCRATCH", "файлов: {n}")
    assert i18n.tr("MSG_TEST_SCRATCH", n=3) == "3 files"
    i18n.set_locale("ru")
    assert i18n.tr("MSG_TEST_SCRATCH", n=3) == "файлов: 3"


def test_tr_missing_key_raises():
    with pytest.raises(KeyError):
        i18n.tr("MSG_NOPE")


def test_tr_missing_placeholder_propagates(monkeypatch):
    """A template placeholder the caller did not supply is a programming error."""
    monkeypatch.setitem(i18n_messages_en.MESSAGES, "MSG_TEST_SCRATCH", "{n} files")
    with pytest.raises(KeyError):
        i18n.tr("MSG_TEST_SCRATCH")


def test_ntr_english_plurals():
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 1) == "1 candidate"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 0) == "0 candidates"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 5) == "5 candidates"


def test_ntr_russian_plurals():
    i18n.set_locale("ru")
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 1) == "1 кандидат"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 2) == "2 кандидата"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 5) == "5 кандидатов"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 11) == "11 кандидатов"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 21) == "21 кандидат"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 22) == "22 кандидата"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 101) == "101 кандидат"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 111) == "111 кандидатов"
    assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 0) == "0 кандидатов"


def test_plural_form_table():
    ru_expect = {
        1: "one", 2: "few", 5: "many", 11: "many", 12: "many", 14: "many",
        21: "one", 22: "few", 25: "many", 100: "many", 101: "one", 111: "many",
    }
    for n, form in ru_expect.items():
        assert i18n.plural_form("ru", n) == form, f"plural_form('ru', {n})"
    assert i18n.plural_form("en", 1) == "one"
    assert i18n.plural_form("en", 2) == "other"


def test_scan_lang_forms():
    assert i18n.scan_lang(["--lang", "ru"]) == "ru"
    assert i18n.scan_lang(["--lang=ru"]) == "ru"
    assert i18n.scan_lang(["-L", "ru"]) == "ru"
    assert i18n.scan_lang(["find", "x"]) is None
    assert i18n.scan_lang(["--lang"]) is None  # missing value
    assert i18n.scan_lang(["--lang", "fr"]) is None  # invalid value


def test_strip_lang_before_verb():
    verbs = frozenset({"find"})
    value, stripped = i18n.strip_lang_before_verb(["--lang", "ru", "find", "x"], verbs)
    assert value == "ru"
    assert stripped == ["find", "x"]

    value, stripped = i18n.strip_lang_before_verb(["find", "--lang", "ru", "x"], verbs)
    assert value is None
    assert stripped == ["find", "--lang", "ru", "x"]

    value, stripped = i18n.strip_lang_before_verb(["--lang=ru", "status"], verbs)
    assert value == "ru"
    assert stripped == ["status"]


def test_override_stash():
    assert i18n.cli_lang_override() is None
    i18n.set_cli_lang_override("ru")
    assert i18n.cli_lang_override() == "ru"
    i18n.set_cli_lang_override(None)
    assert i18n.cli_lang_override() is None


def test_init_help_locale_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no project YAML above a bare tmp dir
    monkeypatch.delenv("JAVA_CODEBASE_RAG_LANGUAGE", raising=False)
    assert i18n.init_help_locale(None) == "en"

    monkeypatch.setenv("JAVA_CODEBASE_RAG_LANGUAGE", "ru")
    assert i18n.init_help_locale(None) == "ru"
    assert i18n.init_help_locale("en") == "en"  # scan beats env
    monkeypatch.delenv("JAVA_CODEBASE_RAG_LANGUAGE")

    assert i18n.init_help_locale("fr") == "en"  # invalid scan ignored
    monkeypatch.setenv("JAVA_CODEBASE_RAG_LANGUAGE", "fr")
    assert i18n.init_help_locale(None) == "en"  # invalid env ignored
    i18n.reset_locale()


def test_catalog_parity():
    for en, ru, label in (
        (i18n_messages_en.MESSAGES, i18n_messages_ru.MESSAGES, "runtime"),
        (i18n_messages_help_en.MESSAGES, i18n_messages_help_ru.MESSAGES, "help"),
    ):
        missing_in_ru = sorted(set(en) - set(ru))
        missing_in_en = sorted(set(ru) - set(en))
        assert not missing_in_ru, f"{label}: keys missing in RU catalog: {missing_in_ru}"
        assert not missing_in_en, f"{label}: keys missing in EN catalog: {missing_in_en}"


def test_catalog_no_key_in_both_runtime_and_help():
    for en, ru, label in (
        (i18n_messages_en.MESSAGES, i18n_messages_help_en.MESSAGES, "en"),
        (i18n_messages_ru.MESSAGES, i18n_messages_help_ru.MESSAGES, "ru"),
    ):
        overlap = sorted(set(en) & set(ru))
        assert not overlap, f"{label}: keys in both runtime and help catalogs: {overlap}"


def test_catalog_plural_shape():
    for catalog, locale, expected_forms, label in (
        (i18n_messages_en.MESSAGES, "en", {"one", "other"}, "en runtime"),
        (i18n_messages_help_en.MESSAGES, "en", {"one", "other"}, "en help"),
        (i18n_messages_ru.MESSAGES, "ru", {"one", "few", "many"}, "ru runtime"),
        (i18n_messages_help_ru.MESSAGES, "ru", {"one", "few", "many"}, "ru help"),
    ):
        for key, value in catalog.items():
            if not isinstance(value, dict):
                continue
            assert set(value) == expected_forms, (
                f"{label}/{key}: forms {sorted(value)} != {sorted(expected_forms)}"
            )
            for form, template in value.items():
                assert "{n}" in template, f"{label}/{key}/{form}: missing {{n}}"


def test_tr_call_sites_use_known_keys():
    known = set(i18n_messages_en.MESSAGES) | set(i18n_messages_help_en.MESSAGES)
    offenders: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        for match in _TR_CALL_RE.finditer(path.read_text(encoding="utf-8")):
            key = match.group(1)
            if key not in known:
                offenders.append(f"{path.name}: {key}")
    assert not offenders, f"tr/ntr call sites with unknown keys: {offenders}"


# ----- Review follow-up hardening --------------------------------------------


def test_ntr_english_fallback_for_locale_forms(monkeypatch):
    """A plural key missing from the RU catalog degrades to EN — including
    when the locale form (few/many) does not exist in the EN entry."""
    monkeypatch.delitem(i18n_messages_ru.MESSAGES, "MSG_AMBIGUOUS_CANDIDATES")
    monkeypatch.setitem(
        i18n_messages_en.MESSAGES,
        "MSG_TEST_FALLBACK_PLURAL",
        {"one": "{n} item", "other": "{n} items"},
    )
    i18n.set_locale("ru")
    try:
        assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 5) == "5 candidates"
        assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 2) == "2 candidates"
        assert i18n.ntr("MSG_AMBIGUOUS_CANDIDATES", 1) == "1 candidate"
        assert i18n.ntr("MSG_TEST_FALLBACK_PLURAL", 5) == "5 items"
    finally:
        i18n.reset_locale()


def test_ntr_missing_form_in_complete_catalog_raises(monkeypatch):
    """When the ACTIVE locale's own catalog has the key but lacks the form,
    the error stays loud (a real translation gap, not a fallback case)."""
    monkeypatch.setitem(
        i18n_messages_en.MESSAGES,
        "MSG_TEST_BAD_PLURAL",
        {"one": "{n} x"},
    )
    with pytest.raises(KeyError):
        i18n.ntr("MSG_TEST_BAD_PLURAL", 5)


def test_catalog_no_duplicate_keys():
    """A duplicated key in a dict literal silently overrides — catch it via
    AST on the catalog sources (dict literals, not the loaded module)."""
    import ast

    for name in (
        "i18n_messages_en",
        "i18n_messages_ru",
        "i18n_messages_help_en",
        "i18n_messages_help_ru",
    ):
        src = (_SRC_ROOT / f"{name}.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [
                    k.value
                    for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                ]
                dupes = sorted({k for k in keys if keys.count(k) > 1})
                assert not dupes, f"{name}: duplicate keys {dupes}"


def _template_placeholders(value):
    if isinstance(value, dict):
        union: set[str] = set()
        for t in value.values():
            union |= set(re.findall(r"{([a-zA-Z_][a-zA-Z0-9_]*)}", t))
        return union
    return set(re.findall(r"{([a-zA-Z_][a-zA-Z0-9_]*)}", value))


def _unescape_braces(value):
    if isinstance(value, dict):
        return {f: t.replace("{{", "{").replace("}}", "}") for f, t in value.items()}
    return value.replace("{{", "{").replace("}}", "}")


def test_catalog_placeholder_parity():
    """EN and RU templates must carry the same {placeholder} set — a missing
    one silently drops the value from RU output (format never errors)."""
    for label, en, ru in (
        ("runtime", i18n_messages_en.MESSAGES, i18n_messages_ru.MESSAGES),
        ("help", i18n_messages_help_en.MESSAGES, i18n_messages_help_ru.MESSAGES),
    ):
        for key in sorted(set(en) & set(ru)):
            e = _template_placeholders(_unescape_braces(en[key]))
            r = _template_placeholders(_unescape_braces(ru[key]))
            assert e == r, f"{label}/{key}: EN {sorted(e)} != RU {sorted(r)}"


def test_shared_producer_keys_render_russian():
    """Every ABS_*/RS_* entry renders under ru (format placeholders resolve)."""
    for key in sorted(
        k for k in i18n_messages_en.MESSAGES if k.startswith(("ABS_", "RS_"))
    ):
        ph = _template_placeholders(i18n_messages_ru.MESSAGES[key])
        i18n.set_locale("ru")
        try:
            out = i18n.tr(key, **{p: "X" for p in ph})
        finally:
            i18n.reset_locale()
        assert isinstance(out, str) and out, key
