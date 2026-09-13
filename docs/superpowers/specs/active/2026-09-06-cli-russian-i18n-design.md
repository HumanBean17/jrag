# CLI Russian localization (i18n)

- **Date:** 2026-09-06
- **Status:** in_progress

## Motivation

The `jrag` CLI is English-only: ~180–220 runtime messages plus ~180 argparse
help strings across 44 commands (11 operator verbs in `cli.py` + installer
wizard, 33 agent verbs in `jrag.py` + `jrag_render.py` labels), with zero
Cyrillic anywhere in the repo. Russian-speaking operators get no way to run
the tool in their language. UTF-8 stdio is already forced
(`_stdio.py`, called at `jrag.py:4660`), so Cyrillic output is
encoding-safe today.

Constraints that shape the design:

- **Golden payloads pin English.** `tests/jrag/golden/*.json` and
  `tests/package/test_jrag_render.py` pin exact envelope text; the default
  experience must stay byte-identical.
- **MCP shares string producers with the CLI.** `resolve_service.py` and
  `absence/absence_diagnosis.py` messages flow into both the CLI envelope
  and MCP tool responses (`server.py:14`, `mcp_v2.py:39`); translating
  those producers naively would leak Russian into MCP.
- **No global flag group exists.** Common flags attach per-subcommand via
  `parents=[_common_parser()]` (`jrag.py:423-509`), and argparse help is
  baked at `build_parser()` time — before any config resolution runs.

**Goal:** opt-in Russian for the entire CLI surface — output, help, errors —
selected by flag, env var, or project YAML; English default everywhere,
MCP always English.

## Goal & scope

**In scope.** `language: en | ru` knob (YAML / env / flag triple, standard
precedence); key-based translation runtime (`i18n.py` + EN/RU catalogs);
full coverage of operator verbs, installer wizard, agent verbs (envelope
message/warnings/`agent_next_actions` values, `jrag_render.py` labels),
unified dispatcher help, argparse `help=` text, and jrag-authored error
messages; docs; tests.

**Out of scope.** gettext/`.po` tooling; locales beyond `en`/`ru` (the
mechanism is a dict — a future locale is one more file); translating
`docs/`, `skills/`, `agents/` shipped artifacts; an installer wizard
language prompt (YAML/env/flag only); locale-aware number/date formatting;
translating stdlib argparse boilerplate fragments (`usage:` / `options:`
headings, stdlib-generated error text).

## Decisions

- **D1 — Key-based catalog, not gettext.** New `src/java_codebase_rag/i18n.py`:
  `set_locale`/`get_locale` (process state, default `en`), `tr(key, **kwargs)`
  (template lookup + `{placeholder}` formatting, EN fallback for missing RU
  keys, one-time stderr note under `--verbose`), `ntr(key, n, **kwargs)`
  (CLDR plural: `one`/`few`/`many`, ~10 lines, unit-tested against the
  1/2/5/11/21/22/25/101 table). Catalogs are flat `{key: template}` dicts in
  `i18n_messages_en.py` / `i18n_messages_ru.py`; call sites carry stable
  SCREAMING_SNAKE keys, English lives in the EN catalog, not inline.
  Rationale: no `.mo` build step (dual PyPI publish stays simple), keys
  survive English rewording, translations are maintained in-repo. Split a
  catalog by surface (help / runtime) only if it exceeds ~200 entries.
- **D2 — Config triple, standard precedence, no env republication.**
  `language` resolves CLI > env > YAML > built-in default (`en`) via a
  `_pick_str` in `resolve_operator_config` (`config.py:586-793`), following
  the `retrieval` pattern (`config.py:746-759`): new `language` +
  `language_source` fields on `ResolvedOperatorConfig`; both CLI mains
  thread their `--lang` value in (`cli.py:298-305`, `jrag.py:1340-1371`).
  **Deviation from the `retrieval` pattern:** `language` is excluded from
  `apply_to_os_environ` / `subprocess_env` republication — republished env
  is how MCP and daemon subprocesses learn resolved values, and MCP must
  never see a resolved `ru`. Invalid env/YAML values degrade gracefully
  (stderr warning naming valid values, fallback `en`) — the `watch.backend`
  pattern (`config.py:721-727`); at the CLI tier argparse `choices` rejects.
- **D3 — Flag `--lang {en,ru}` (`-L`), accepted before and after the verb.**
  `cli_dispatch._console_script_main` gains an argv pre-scan that recognizes
  `--lang ru`, `--lang=ru`, `-L ru` anywhere in argv, strips the pair, and
  records the selection before routing to the sub-CLI. This simultaneously
  (a) makes `jrag --lang ru find …` work despite verb-based dispatch, and
  (b) seeds the help-time locale: `--help` renders before full config
  resolution, so `build_parser()` consults the pre-scan result.
  Help-time order: pre-scan > env > best-effort YAML discovery > `en`.
  The flag is also registered on `_common_parser`, `_core_parser`
  (`jrag.py:432-541`), and the operator `build_parser` (`cli.py:993`) so the
  after-verb form parses and appears in help. Known edge: with a
  YAML-only `language: ru` whose YAML is not discoverable at help time,
  `--help` renders English while runtime messages render Russian —
  documented, accepted. After parsing, the authoritative resolver sets the
  final locale; flag and pre-scan agree by construction.
- **D4 — Locale is CLI-entry process state.** `set_locale` is called only
  by `cli.main` and `jrag.main` after parsing. `tr()` never reads the
  environment lazily. The MCP server (`jrag-mcp`) never calls `set_locale`,
  so MCP responses stay English even with `JAVA_CODEBASE_RAG_LANGUAGE=ru`
  exported in the server's shell.
- **D5 — Subprocess boundaries made explicit.** The watch daemon is
  operator-facing: the CLI spawn site passes the resolved language to the
  daemon child env explicitly (one added key, not config-wide
  republication). MCP spawn sites (`server.py` operator-CLI children)
  scrub `JAVA_CODEBASE_RAG_LANGUAGE` from the child environment so a
  user-exported value cannot localize MCP-triggered progress output.
- **D6 — Coverage boundary: values translate, contracts don't.** JSON
  object keys, envelope field names, `status:` values, exit codes, and
  state-file machine keys stay English; only human-readable values
  (messages, labels, help prose, warnings) localize. jrag-authored error
  wrappers translate (`jrag: error:` → `jrag: ошибка:`, operator
  `jrag: {exc}` wrapper, error-envelope messages). Stdlib argparse
  boilerplate fragments remain English (out of scope, D-list above).
- **D7 — Shared producers adopt `tr()` with default-English.**
  `resolve_service.py`, `absence/absence_diagnosis.py`, and `pipeline.py`
  constants swap inline English for `tr()` keyed strings. In the MCP
  process no locale is ever set (D4), so output is byte-identical to
  today; goldens and MCP tests are unaffected by construction. The
  existing golden suite doubles as the drift guard.

## Configuration contract

| Tier | Name | Values | Default |
| --- | --- | --- | --- |
| YAML | `.java-codebase-rag.yml` → `language:` | `en` \| `ru` | absent = `en` |
| Env | `JAVA_CODEBASE_RAG_LANGUAGE` | same | — |
| CLI | `--lang {en,ru}` / `-L` (before or after verb) | same | `en` |

The YAML key is hand-edited; the installer wizard and
`generate_yaml_config` never write it. `language_source: SettingSource`
reports the winning tier (`config.py:17`).

## Documentation

- `docs/CONFIGURATION.md` — `language` YAML key, env var, precedence, the
  MCP-always-English note, and the YAML-only help edge case.
- `docs/JRAG-CLI.md` — `--lang` usage examples (before/after verb, env,
  YAML), Russian-workflow exit-code parity note.
- `docs/ARCHITECTURE.md` — `i18n.py` in the module map; the locale
  invariant (CLI-entry-only `set_locale`, no env republication) in the
  write/read path notes.
- `docs/DESIGN.md` — one paragraph: opt-in localization, values-not-keys
  boundary, English-first fallback.

## Compatibility

- Default (`en`) output is byte-identical to today: goldens, render tests,
  JSON keys, exit codes untouched.
- MCP server responses are byte-identical regardless of exported env
  (D4/D5).
- No on-disk index, schema, or state-file format change; no new runtime
  dependencies (stdlib only); `pyproject.toml` gains only new modules.
- Existing project YAMLs (no `language` key) resolve to `en`.

## Tests

- **Unit** (new `tests/package/test_i18n.py`): EN/RU key parity (both
  directions), duplicate-key detection, placeholder-set consistency
  between catalogs, CLDR plural table (1/2/5/11/21/101…), EN-fallback
  behavior.
- **Config** (`tests/package/test_config.py` / `test_config_watch.py`
  pattern): precedence flag > env > YAML > default; `language_source`
  values; invalid env/YAML value → warning + `en`; confirmation that
  `language` is absent from `apply_to_os_environ` output.
- **CLI integration** (in-process `main()` + `capsys`, the
  `test_version_flag.py` pattern): one operator verb and one agent verb
  under `--lang ru` → Cyrillic output, unchanged JSON keys and rc; the
  before-verb placement via the dispatch pre-scan; env and YAML paths.
- **Help**: subprocess test (`_run_jrag` pattern) — `jrag --lang ru --help`
  and `jrag find --lang ru --help` render Russian usage/prose.
- **MCP isolation**: `resolve_v2` / absence output with
  `JAVA_CODEBASE_RAG_LANGUAGE=ru` set → English (no `set_locale` on the
  server path); MCP spawn env scrub verified.
- **Regression**: existing render and golden suites pass unchanged
  (default locale).
- Repo test rules apply: erase stale manual indexes under `tests/`; run
  the relevant subset during development, the full suite once at the end.

## Files touched (design-level)

| File | Change |
| --- | --- |
| `src/java_codebase_rag/i18n.py` | new — locale state, `tr`/`ntr`, plural rule |
| `src/java_codebase_rag/i18n_messages_en.py`, `i18n_messages_ru.py` | new — flat key catalogs |
| `src/java_codebase_rag/config.py` | `language` + `language_source` resolution; excluded from env republication |
| `src/java_codebase_rag/cli_dispatch.py` | `--lang` argv pre-scan; unified help localization |
| `src/java_codebase_rag/cli.py` | `--lang` registration; `set_locale` in `main`; call sites → `tr()` |
| `src/java_codebase_rag/jrag.py` | `--lang` on common/core parsers; `set_locale` in `main`; message sites → `tr()` |
| `src/java_codebase_rag/jrag_render.py` | render labels/prefixes → `tr()` |
| `src/java_codebase_rag/installer.py` | wizard strings → `tr()` |
| `src/java_codebase_rag/pipeline.py`, `analysis/resolve_service.py`, `absence/absence_diagnosis.py` | constants/messages → `tr()` (default-en, D7) |
| `src/java_codebase_rag/watch/` (spawn site), `mcp/server.py` | daemon env pass-through; MCP child-env scrub |
| `docs/CONFIGURATION.md`, `docs/JRAG-CLI.md`, `docs/ARCHITECTURE.md`, `docs/DESIGN.md` | knob + invariant docs |
| `tests/package/test_i18n.py`, `tests/package/test_config.py`, CLI/MCP test files | coverage per above |

## TL;DR

Add opt-in Russian to the whole `jrag` CLI — operator verbs, installer
wizard, agent verbs, render labels, help text, and errors — via one knob:
`--lang {en,ru}` (works before or after the verb through a dispatch
pre-scan), `JAVA_CODEBASE_RAG_LANGUAGE`, or `language:` in the project
YAML; precedence CLI > env > YAML > default `en`, with the usual
`SettingSource` tracking. Translation runs on a hand-rolled key catalog
(`i18n.py` + EN/RU dicts, `tr`/`ntr` with proper Russian plurals) — no
gettext, no build step. Two invariants protect the rest of the system:
JSON/envelope contract keys and exit codes never translate, and locale is
set only inside the two CLI entrypoints — never republished to env — so
MCP responses and golden payloads stay byte-identical English even when
Russian is enabled. Default behavior is unchanged everywhere; shared
producers (`resolve_service`, absence diagnosis) adopt `tr()` with
default-English so one string source serves both worlds.
