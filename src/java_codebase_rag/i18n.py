"""Process-level localization for the ``jrag`` CLI surfaces (en | ru).

Key-catalog runtime (spec D1): stable SCREAMING_SNAKE keys resolve through
flat ``MESSAGES`` dicts in :mod:`i18n_messages_en` / :mod:`i18n_messages_ru`
(runtime strings) and the ``i18n_messages_help_*`` twins (argparse help).
``tr`` formats ``{placeholder}`` templates; ``ntr`` additionally dispatches
the CLDR plural form (en: one/other; ru: one/few/many). Missing RU keys
fall back to the EN catalog entry, so an incomplete translation degrades
to English rather than crashing.

Locale invariant (spec D4): the locale is process state, set only by the
CLI entrypoints (``cli.main`` / ``jrag.main`` after parsing, plus the
dispatch-level argv pre-scan so ``--help`` renders localized). Nothing
under ``java_codebase_rag.mcp`` ever calls :func:`set_locale`, so MCP
responses stay byte-identical English regardless of exported env. The
locale is never read lazily from the environment inside ``tr``/``ntr``.

Argv helpers (spec D3): :func:`scan_lang` reads the ``--lang`` value from
a raw argv (used by ``main()`` before ``build_parser`` so help strings are
localized); :func:`strip_lang_before_verb` removes before-verb lang pairs
so the sub-CLI argparse never sees them (used by ``cli_dispatch``); the
override stash carries the stripped value into the sub-CLI's config
resolution.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

__all__ = [
    "VALID_LANGS",
    "cli_lang_override",
    "get_locale",
    "init_help_locale",
    "ntr",
    "plural_form",
    "reset_locale",
    "scan_lang",
    "set_cli_lang_override",
    "set_locale",
    "strip_lang_before_verb",
    "tr",
]

#: The closed set of interface languages. ``en`` is the built-in default.
VALID_LANGS: frozenset[str] = frozenset({"en", "ru"})

_locale: str = "en"
_cli_lang_override: str | None = None

# Catalog modules are imported lazily (and memoized) so that importing
# :mod:`i18n` — e.g. transitively from a parser builder — stays cheap and
# never pulls backend modules.
_catalog_cache: dict[str, dict[str, Any]] = {}


def _catalog(name: str) -> dict[str, Any]:
    mod = _catalog_cache.get(name)
    if mod is None:
        import importlib

        mod = importlib.import_module(f"java_codebase_rag.{name}").MESSAGES
        _catalog_cache[name] = mod
    return mod


def _catalogs_for(locale: str) -> tuple[dict[str, Any], ...]:
    """Lookup order: active locale runtime/help, then EN runtime/help."""
    if locale == "en":
        return (_catalog("i18n_messages_en"), _catalog("i18n_messages_help_en"))
    return (
        _catalog(f"i18n_messages_{locale}"),
        _catalog(f"i18n_messages_help_{locale}"),
        _catalog("i18n_messages_en"),
        _catalog("i18n_messages_help_en"),
    )


def set_locale(locale: str) -> None:
    """Set the process locale. ``ValueError`` for anything but en/ru."""
    if locale not in VALID_LANGS:
        raise ValueError(f"unsupported locale: {locale!r}")
    global _locale
    _locale = locale


def get_locale() -> str:
    return _locale


def reset_locale() -> None:
    """Back to the built-in default (test-isolation helper)."""
    global _locale
    _locale = "en"


def tr(key: str, **kwargs: Any) -> str:
    """Translate ``key`` in the active locale and format placeholders.

    Lookup falls back to EN for missing active-locale keys; a key missing
    everywhere raises ``KeyError``. A placeholder requested by the caller
    but absent from the template propagates ``KeyError`` from ``format`` —
    that is a programming error, not a runtime condition.
    """
    for catalog in _catalogs_for(_locale):
        if key in catalog:
            template = catalog[key]
            if isinstance(template, dict):
                raise KeyError(
                    f"i18n: plural key {key!r} must go through ntr(), not tr()"
                )
            return template.format(**kwargs)
    raise KeyError(key)


def ntr(key: str, n: int, **kwargs: Any) -> str:
    """Translate plural ``key`` with the CLDR form for ``n``.

    Catalog values for plural keys are dicts of forms (en: one/other;
    ru: one/few/many). ``n`` is injected into the template's placeholders
    automatically. When the winning catalog is an EN fallback (the active
    locale lacks the key), the locale-specific form (few/many) maps to
    EN's ``other`` — the same degrade-to-English promise ``tr`` makes.
    """
    catalogs = _catalogs_for(_locale)
    for idx, catalog in enumerate(catalogs):
        if key in catalog:
            value = catalog[key]
            if not isinstance(value, dict):
                raise KeyError(f"i18n: key {key!r} is not a plural entry")
            form = plural_form(_locale, n)
            is_en_fallback = _locale != "en" and idx >= 2
            if form not in value:
                if is_en_fallback and "other" in value:
                    form = "other"
                else:
                    raise KeyError(
                        f"i18n: plural key {key!r} lacks form {form!r}"
                    )
            return value[form].format(n=n, **kwargs)
    raise KeyError(key)


def plural_form(locale: str, n: int) -> str:
    """CLDR plural category for ``n`` in the supported locales."""
    if locale == "ru":
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            return "few"
        return "many"
    return "one" if n == 1 else "other"


def _valid(value: str) -> bool:
    return value in VALID_LANGS


def scan_lang(argv: list[str]) -> str | None:
    """Value of the first ``--lang V`` / ``--lang=V`` / ``-L V`` in ``argv``.

    Non-destructive. Returns ``None`` when no flag is present, the value is
    missing (flag is the last token), or the value is not en/ru — the first
    recognized flag decides the outcome.
    """
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--lang" or token == "-L":
            if i + 1 < len(argv) and _valid(argv[i + 1]):
                return argv[i + 1]
            return None
        if token.startswith("--lang="):
            value = token.split("=", 1)[1]
            return value if _valid(value) else None
        i += 1
    return None


def strip_lang_before_verb(
    argv: list[str], verbs: frozenset[str]
) -> tuple[str | None, list[str]]:
    """Strip ``--lang``/``-L`` pairs that appear BEFORE the first verb token.

    Tokens at or after the first verb are passed through untouched so the
    subparser's own registered ``--lang`` (validated by argparse choices)
    still sees them. Only valid ``en``/``ru`` pairs are stripped; an invalid
    value is left in place for argparse to reject. Returns
    ``(first_valid_value_or_None, stripped_argv)``. Non-destructive: a new
    list is returned, the input is not mutated.
    """
    out: list[str] = []
    value: str | None = None
    seen_verb = False
    i = 0
    while i < len(argv):
        token = argv[i]
        if not seen_verb and token in verbs:
            seen_verb = True
            out.append(token)
            i += 1
            continue
        if not seen_verb and (token == "--lang" or token == "-L"):
            if i + 1 < len(argv) and _valid(argv[i + 1]):
                if value is None:
                    value = argv[i + 1]
                i += 2
                continue
            # Missing/invalid value: leave the tokens for argparse to reject.
            out.append(token)
            i += 1
            continue
        if not seen_verb and token.startswith("--lang="):
            inner = token.split("=", 1)[1]
            if _valid(inner):
                if value is None:
                    value = inner
                i += 1
                continue
            out.append(token)
            i += 1
            continue
        out.append(token)
        i += 1
    return value, out


def set_cli_lang_override(value: str | None) -> None:
    """Stash the dispatch pre-scan's ``--lang`` value for the sub-CLI main."""
    global _cli_lang_override
    _cli_lang_override = value


def cli_lang_override() -> str | None:
    return _cli_lang_override


def init_help_locale(scan_value: str | None) -> str:
    """Set the locale for help rendering; returns the locale set. Never raises.

    Order (spec D3): argv scan value > ``JAVA_CODEBASE_RAG_LANGUAGE`` >
    best-effort project YAML ``language:`` > en. The YAML tier walks up from
    cwd exactly like the full resolver's discovery, but any failure (broken
    YAML, unreadable path, …) silently degrades — ``--help`` must never
    crash on config problems.
    """
    global _locale
    candidate = scan_value if scan_value in VALID_LANGS else None
    if candidate is None:
        from java_codebase_rag.config import ENV_LANGUAGE

        env_raw = os.environ.get(ENV_LANGUAGE, "").strip()
        if env_raw in VALID_LANGS:
            candidate = env_raw
    if candidate is None:
        try:
            from java_codebase_rag import config

            root = config.discover_project_root(Path.cwd())
            if root is not None:
                mapping = config.load_yaml_mapping(root)
                value = mapping.get("language")
                if isinstance(value, str) and value.strip() in VALID_LANGS:
                    candidate = value.strip()
        except Exception:  # noqa: BLE001 - help must never crash on config
            pass
    _locale = candidate if candidate is not None else "en"
    return _locale
