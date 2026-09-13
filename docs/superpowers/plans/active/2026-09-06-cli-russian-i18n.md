# CLI Russian Localization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Opt-in Russian (`--lang {en,ru}` / `JAVA_CODEBASE_RAG_LANGUAGE` / YAML `language:`) for the entire `jrag` CLI surface — output, help, errors — with English default and MCP always English.

**Architecture:** A key-based translation runtime (`i18n.py` + EN/RU dict catalogs, `tr`/`ntr` with CLDR Russian plurals) resolves a new `language` config knob through the existing `CLI > env > YAML > default` machinery. Locale is process state set only by the two CLI entrypoints (plus a dispatch-level argv pre-scan so `--lang` works before the verb and seeds `--help` rendering); it is never republished to `os.environ`, so MCP subprocesses stay English. Spec: `docs/superpowers/specs/active/2026-09-06-cli-russian-i18n-design.md`.

**Tech Stack:** Python 3 stdlib only (argparse, no new dependencies). Tests: pytest with `capsys` (in-process `main()` calls) and subprocess runs of the installed `jrag` binary (`tests/package/_run_cli`/`_run_jrag` pattern).

## Global Constraints

- Python: `.venv/bin/python` / `.venv/bin/pip` only, never system python. Editable install; if behavior looks stale while pytest passes, run `.venv/bin/pip install -e ".[dev]"` and say nothing.
- Before test runs: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}` (stale manual indexes hijack discovery). Run the relevant subset during a task; the FULL suite once, in Task 12.
- No new runtime dependencies. `pyproject.toml` is NOT modified by any task.
- Never rename `JAVA_CODEBASE_RAG_*` env vars or on-disk `.java-codebase-rag*` names.
- **English byte-stability:** with no `language` setting, every output byte is identical to today. The golden payloads (`tests/jrag/golden/*.json`) and `tests/package/test_jrag_render.py` must pass unchanged — never edit them to accommodate i18n.
- **MCP always English:** `set_locale` is called only inside `cli.main`/`jrag.main` paths and the dispatch pre-scan. Nothing under `src/java_codebase_rag/mcp/` ever calls `set_locale` or reads `JAVA_CODEBASE_RAG_LANGUAGE`. `language` is excluded from `apply_to_os_environ` / `subprocess_env` (unlike `retrieval`).
- **Values translate, contracts don't:** JSON object keys, envelope field names (`status`, `message`, …), status values (`ok`/`error`/`not_found`), exit codes, `--format`/`--detail` choice values, command/verb names, flag names, YAML keys, setting values (`vectors`/`bm25`) stay English. Command names in prose keep their backticks: `` `jrag init` `` stays literal.
- `--version` output stays English (machine-parsed).
- **Russian style guide:** address the operator as formal lowercase «вы»; imperative mood for next-actions («Запустите …», «Уточните …»); no «пожалуйста». Quotes are «ёлочки». Arabic numerals. Glossary (use consistently): index → индекс, graph → граф, symbol → символ, service → сервис, module → модуль, callers → вызывающие стороны, callees → вызываемые стороны, matches → совпадения, found → найдено, not found → не найдено, warning → предупреждение, error → ошибка, usage error → ошибка использования, internal error → внутренняя ошибка, query → запрос, hint → подсказка, verdict → вердикт, truncated → обрезано, external dependency → внешняя зависимость, index directory → каталог индекса, daemon → демон. Leave untranslated: watch, vectors, bm25, retrieval, envelope (as a format name), FQN, service/role/capability enum values.
- **Catalog key prefixes:** `HELP_*` (argparse help/descriptions/epilogs), `MSG_*` (runtime stdout/stderr messages), `ERR_*` (error messages), `LBL_*` (render labels like `next:`/`Verdict:`), `HINT_*` (envelope `agent_next_actions` strings). Keys are SCREAMING_SNAKE and stable.
- **Import-time trap:** `tr()` evaluates at call time. Any module-level string constant that becomes translated (e.g. `pipeline.py` vectors advisories, `cli.py` `_INCREMENT_WARNING_LINES`, `_REFRESH_DEPRECATION`) must be converted to a function returning the string, or the `tr()` call moved to the print site — never left as a translated module constant.
- Every commit message ends with a trailer line: `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Run all pytest commands from the repo root.

---

### Task 1: i18n runtime module and catalogs

**Files:**
- Create: `src/java_codebase_rag/i18n.py`
- Create: `src/java_codebase_rag/i18n_messages_en.py`, `src/java_codebase_rag/i18n_messages_ru.py`
- Create: `src/java_codebase_rag/i18n_messages_help_en.py`, `src/java_codebase_rag/i18n_messages_help_ru.py`
- Test: `tests/package/test_i18n.py`

**Interfaces:**
- Produces (consumed by every later task):
  - `i18n.VALID_LANGS: frozenset[str]` — `{"en", "ru"}`.
  - `i18n.set_locale(locale: str) -> None` — sets the process locale; raises `ValueError` for a value outside `VALID_LANGS`.
  - `i18n.get_locale() -> str` — current locale; `"en"` before any `set_locale` call.
  - `i18n.reset_locale() -> None` — back to `"en"`; test-isolation helper.
  - `i18n.tr(key: str, **kwargs: object) -> str` — looks up `key` in the active locale's runtime catalog, then the help catalog, then the EN runtime catalog, then the EN help catalog; first hit wins; missing everywhere raises `KeyError`. Applies `str.format(**kwargs)`. A format `KeyError` (placeholder absent from the template) propagates — it is a programming error.
  - `i18n.ntr(key: str, n: int, **kwargs: object) -> str` — same lookup, but catalog values for plural keys are dicts of forms; injects `n=n` into kwargs; selects the form via `plural_form`.
  - `i18n.plural_form(locale: str, n: int) -> str` — `"en"`: `"one"` if `n == 1` else `"other"`. `"ru"`: `"one"` if `n % 10 == 1 and n % 100 != 11`; `"few"` if `n % 10 in (2,3,4) and n % 100 not in (12,13,14)`; else `"many"`.
  - `i18n.scan_lang(argv: list[str]) -> str | None` — non-destructive; returns the value of the first `--lang V`, `--lang=V`, `-L V` token pair anywhere in `argv`, or `None` when absent, value missing (flag is last token), or value not in `VALID_LANGS`.
  - `i18n.strip_lang_before_verb(argv: list[str], verbs: frozenset[str]) -> tuple[str | None, list[str]]` — destructive scan; removes recognized lang token pairs only while no token in `verbs` has been seen; tokens after the first verb are passed through untouched. Returns `(scanned_value_or_None, stripped_argv)`.
  - `i18n.set_cli_lang_override(value: str | None) -> None` / `i18n.cli_lang_override() -> str | None` — process-level stash used to carry the dispatch pre-scan result into the sub-CLI `main()`s.
  - `i18n.init_help_locale(scan_value: str | None) -> str` — sets the locale for help rendering: `scan_value` if valid, else `JAVA_CODEBASE_RAG_LANGUAGE` if valid, else best-effort YAML (`config.discover_project_root(Path.cwd())` → `config.find_yaml_config_file` → `load_yaml_mapping` → top-level `language:` key; any error → skip), else `"en"`. Returns the locale set. Never raises.
  - Catalogs: each module exposes `MESSAGES: dict[str, str | dict[str, str]]`. Runtime catalogs hold `MSG_*`/`ERR_*`/`LBL_*`/`HINT_*`; help catalogs hold `HELP_*`.
- Bootstrap key set (so this task is testable standalone; later tasks add their own keys to BOTH locales): `MSG_TEST_PLURAL` plural key — EN `{"one": "{n} match", "other": "{n} matches"}`, RU `{"one": "{n} совпадение", "few": "{n} совпадения", "many": "{n} совпадений"}`; `MSG_TEST_GREETING` — EN `"index ready"`, RU `"индекс готов"`; `LBL_TEST_PREFIX` — EN `"Verdict: "`, RU `"Вердикт: "`. Delete the three `*_TEST_*` keys at the end of Task 6 when real keys replace them (keep the parity test green).

- [ ] **Step 1: Write the failing tests**

`tests/package/test_i18n.py` (pure unit; no index, no subprocess):
- `test_default_locale_is_english` — `get_locale() == "en"`.
- `test_set_locale_valid_and_invalid` — `set_locale("ru")` then `get_locale() == "ru"`; `set_locale("fr")` raises `ValueError`; `reset_locale()` restores `"en"`.
- `test_tr_english_and_russian` — `tr("MSG_TEST_GREETING")` is `"index ready"`; after `set_locale("ru")` it is `"индекс готов"`. Each test ends with `reset_locale()` (autouse fixture).
- `test_tr_placeholder_formatting` — with a placeholder added to a scratch key via monkeypatching the catalog dict (or use `MSG_TEST_PLURAL` forms): `tr(key, n=3)` substitutes `{n}`.
- `test_tr_missing_key_raises` — `tr("MSG_NOPE")` raises `KeyError`.
- `test_ntr_english_plurals` — `ntr("MSG_TEST_PLURAL", 1)` → `"1 match"`; `n` of 0 and 5 → `"0 matches"`/`"5 matches"`.
- `test_ntr_russian_plurals` — under `set_locale("ru")`: n=1 → `"1 совпадение"`, n=2 → `"2 совпадения"`, n=5 → `"5 совпадений"`, n=11 → `"11 совпадений"`, n=21 → `"21 совпадение"`, n=22 → `"22 совпадения"`, n=101 → `"101 совпадение"`, n=111 → `"111 совпадений"`, n=0 → `"0 совпадений"`.
- `test_plural_form_table` — direct `plural_form("ru", n)` assertions for 1, 2, 5, 11, 12, 14, 21, 22, 25, 100, 101, 111; `plural_form("en", 1)`/`(“en”, 2)`.
- `test_scan_lang_forms` — `scan_lang(["--lang", "ru"])`, `scan_lang(["--lang=ru"])`, `scan_lang(["-L", "ru"])` all `"ru"`; `scan_lang(["find", "x"])` is `None`; `scan_lang(["--lang"])` (missing value) is `None`; `scan_lang(["--lang", "fr"])` is `None`.
- `test_strip_lang_before_verb` — with verbs `frozenset({"find"})`: input `["--lang", "ru", "find", "x"]` → value `"ru"`, stripped `["find", "x"]`; input `["find", "--lang", "ru", "x"]` → value `None`, stripped equals input (after-verb tokens untouched); input `["--lang=ru", "status"]` → value `"ru"`, stripped `["status"]`.
- `test_override_stash` — `cli_lang_override()` is `None`; `set_cli_lang_override("ru")` then `cli_lang_override() == "ru"`; `set_cli_lang_override(None)` clears.
- `test_init_help_locale_precedence` — monkeypatch env: no env, no scan → `"en"`; env `JAVA_CODEBASE_RAG_LANGUAGE=ru`, scan `None` → `"ru"`; env `ru`, scan `"en"` → `"en"` (scan wins); scan `"fr"` + env unset → `"en"`; env set to `fr` → `"en"` (invalid ignored).
- `test_catalog_parity` — runtime: keys of `i18n_messages_en.MESSAGES` == keys of `i18n_messages_ru.MESSAGES` (both directions, explicit set assertions with the diff in the failure message); same for the two help catalogs; no key appears in both a runtime and a help catalog.
- `test_catalog_plural_shape` — for every dict-valued catalog entry: EN has exactly `{"one","other"}`; RU has exactly `{"one","few","many"}`; every form value contains `{n}`.
- `test_tr_call_sites_use_known_keys` — regex-scan `src/java_codebase_rag/**/*.py` for literal calls `tr("…")`/`ntr("…")`; every captured key exists in the EN runtime or help catalog. (Static guard against typos; only literal keys are checked.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_i18n.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'java_codebase_rag.i18n'` (or equivalent import failure).

- [ ] **Step 3: Write the implementation**

`i18n.py` per the Interfaces block. Behaviors to make sure of: the fallback order in `tr` (active-locale runtime → active-locale help → EN runtime → EN help); `ntr` raises `KeyError` when the looked-up value is a plain string or the locale's form name is absent from the form dict; `scan_lang`/`strip_lang_before_verb` recognize exactly `--lang V`, `--lang=V`, `-L V` (short form has no `=` variant); `init_help_locale` wraps ALL YAML discovery in `try/except Exception` (help must never crash on a broken YAML). Module has no imports beyond stdlib + `java_codebase_rag.config` (imported lazily inside `init_help_locale` so `jrag --help` stays fast). The four catalog modules contain only `MESSAGES` dicts with the bootstrap keys above. Write the Russian bootstrap strings exactly as given.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_i18n.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/i18n.py src/java_codebase_rag/i18n_messages_en.py src/java_codebase_rag/i18n_messages_ru.py src/java_codebase_rag/i18n_messages_help_en.py src/java_codebase_rag/i18n_messages_help_ru.py tests/package/test_i18n.py`
Run: `git commit -m "feat(i18n): key-catalog runtime with tr/ntr and CLDR Russian plurals"`

---

### Task 2: `language` config knob

**Files:**
- Modify: `src/java_codebase_rag/config.py` (constant near line 34; dataclass near 393; resolver near 746-759)
- Test: `tests/package/test_config.py`

**Interfaces:**
- Consumes: `i18n.VALID_LANGS` (import the frozenset from `i18n`; do not duplicate the literal in config.py).
- Produces (consumed by Tasks 3, 9):
  - `ENV_LANGUAGE = "JAVA_CODEBASE_RAG_LANGUAGE"` module constant in `config.py`.
  - `resolve_operator_config(..., cli_language: str | None = None)` — new keyword-only param, last in the signature.
  - `ResolvedOperatorConfig.language: str = "en"` and `.language_source: SettingSource = "default"` (trailing fields with defaults, mirroring `retrieval`/`retrieval_source` at config.py:393-394).
  - Resolution: `_pick_str(cli_val=cli_language, env_key=ENV_LANGUAGE, yaml_path=("language",), default="en")`. Invalid value (any tier) → stderr line exactly shaped like the `retrieval` fallback at config.py:753-759: `jrag: language={value!r} is not one of en/ru; falling back to 'en'.` then `language, language_source = "en", "default"`.
  - **`apply_to_os_environ` and `subprocess_env` gain NO language key** (spec D2 — the deliberate deviation from the `retrieval` pattern).
  - `subprocess_env(base: dict[str, str] | None = None, *, language: bool = False)` — new keyword-only param; when `True`, adds `JAVA_CODEBASE_RAG_LANGUAGE: self.language` to the returned dict. Default `False` keeps every existing caller byte-identical. (Used only by Task 9's watch call sites.)
  - Side change: `i18n.init_help_locale` (Task 1) read the env var by string literal; switch it to `config.ENV_LANGUAGE` so the name lives in one place.

- [ ] **Step 1: Write the failing tests**

Add to `tests/package/test_config.py` (follow that file's existing fixture style for tmp YAML + env isolation):
- `test_language_default_is_en` — resolve with nothing set: `cfg.language == "en"`, `cfg.language_source == "default"`.
- `test_language_cli_wins` — `cli_language="ru"` + env `ru` unset + YAML `language: en`: `("ru", "cli")`.
- `test_language_env_beats_yaml` — env `JAVA_CODEBASE_RAG_LANGUAGE=ru` + YAML `language: en`: `("ru", "env")`.
- `test_language_yaml` — YAML `language: ru`, no env, no CLI: `("ru", "yaml")`.
- `test_language_invalid_env_degrades` — env `JAVA_CODEBASE_RAG_LANGUAGE=fr`: result `("en", "default")` and stderr (capsys) contains `language='fr' is not one of en/ru`.
- `test_language_invalid_yaml_degrades` — YAML `language: fr`: `("en", "default")` + same stderr shape.
- `test_language_not_republished_to_environ` — resolve with env tier `ru`, call `cfg.apply_to_os_environ()`, assert `os.environ.get("JAVA_CODEBASE_RAG_LANGUAGE")` is unchanged (unset) — distinct from `JAVA_CODEBASE_RAG_RETRIEVAL` which IS set.
- `test_subprocess_env_language_opt_in` — `cfg.subprocess_env()` lacks the key; `cfg.subprocess_env(language=True)["JAVA_CODEBASE_RAG_LANGUAGE"] == cfg.language`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_config.py -v -k language`
Expected: FAIL — `TypeError: resolve_operator_config() got an unexpected keyword argument 'cli_language'` (or AttributeError on `.language`).

- [ ] **Step 3: Write the implementation**

Add the constant, the two dataclass fields, the `cli_language` param, the `_pick_str` call + invalid-value fallback (copy the `retrieval` block's shape verbatim, substituted values), pass both fields into the `ResolvedOperatorConfig(...)` constructor call, and add the `language=` keyword to `subprocess_env` only. No other caller of `resolve_operator_config` changes in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_config.py tests/package/test_config_watch.py tests/package/test_i18n.py -v`
Expected: all PASS (watch config tests prove existing callers unaffected).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/config.py tests/package/test_config.py`
Run: `git commit -m "feat(i18n): language knob resolved cli > env > yaml > default, never republished"`

---

### Task 3: dispatch pre-scan, `--lang` flag, main() wiring, unified help

**Files:**
- Modify: `src/java_codebase_rag/cli_dispatch.py` (`_console_script_main`, `_print_unified_help`)
- Modify: `src/java_codebase_rag/jrag.py` (`_common_parser` ~432, `_core_parser` ~516, `main` ~4575, `_resolve_cfg` ~1340)
- Modify: `src/java_codebase_rag/cli.py` (`build_parser` ~993 via a new flag helper, `main` ~1240, `_resolved_from_ns` ~298)
- Test: `tests/package/test_cli_dispatch.py`, `tests/package/test_i18n_cli.py` (new)

**Interfaces:**
- Consumes: everything from Task 1 (`scan_lang`, `strip_lang_before_verb`, `init_help_locale`, `set_cli_lang_override`, `cli_lang_override`, `set_locale`, `reset_locale`) and Task 2 (`cli_language` param, `cfg.language`).
- Produces (consumed by Tasks 4-10):
  - Flag definition, identical in all three registration sites (`jrag._common_parser`, `jrag._core_parser`, and a new `cli._add_lang_flag(p)` helper applied to every operator subparser in `cli.build_parser`): `--lang` with `-L` alias, `choices=("en", "ru")`, `dest="lang"`, `default=None`, help text `HELP_FLAG_LANG` (EN: "Interface language for output, help, and errors (default: en; also JAVA_CODEBASE_RAG_LANGUAGE or YAML language:)." / RU: «Язык интерфейса: вывод, справка, ошибки (по умолчанию en; также JAVA_CODEBASE_RAG_LANGUAGE или language: в YAML).»). In `jrag.py`, add the same registration to the top-level parser too (so `jrag --lang en --help` parses) — top-level-only tokens are still an error for in-process `main()` before-verb use; that is accepted (real users go through the dispatch strip).
  - `cli_dispatch._console_script_main` flow, in order: `maybe_warn_legacy_alias()` → pre-scan (`value, stripped = strip_lang_before_verb(sys.argv[1:], OPERATOR_VERBS | AGENT_VERBS)`; if `value is not None`: rewrite `sys.argv[1:] = stripped` and `set_cli_lang_override(value)`) → `init_help_locale(value)` runs regardless (so env/YAML drive help too) → unified-help check → `_choose_target()` → forward.
  - `jrag.main(argv)` head, before `build_parser()`: `init_help_locale(scan_lang(raw))` — catches the after-verb form for `--help` in the in-process path. After `parse_args` succeeds: `cli_language = getattr(args, "lang", None) or cli_lang_override()` and after `_resolve_cfg` resolves, `_resolve_cfg` itself calls `set_locale(cfg.language)` as its last statement before returning.
  - `jrag._resolve_cfg`: adds `cli_language=getattr(args, "lang", None) or i18n.cli_lang_override()` to the `resolve_operator_config(...)` call, then `set_locale(cfg.language)`.
  - `cli.main(argv)` head, before `build_parser()` (after the `refresh` rewrite): `init_help_locale(scan_lang(raw))`. `cli._resolved_from_ns`: adds the same `cli_language=` expression, and its caller invokes `set_locale(cfg.language)` right after obtaining cfg — implement as the last statement of `_resolved_from_ns` (matching `_resolve_cfg`).
  - `_print_unified_help`: the section header string `"Operator commands (indexing & maintenance; run `jrag <command> --help` for details):\n"` becomes `tr("MSG_UNIFIED_OPERATOR_HEADER")` (catalog strings: EN keeps today's exact text; RU: «Команды оператора (индексация и обслуживание; подробности — `jrag <command> --help`):\n»).
- Constraint: locale set this early must not change output of any existing default-locale run — `init_help_locale` with nothing configured resolves `"en"` and `tr` returns today's strings.

- [ ] **Step 1: Write the failing tests**

`tests/package/test_cli_dispatch.py` additions (in-process, capsys):
- `test_unified_help_russian_when_locale_ru` — `i18n.set_locale("ru")`, call `cli_dispatch._print_unified_help(io.StringIO())`, assert the captured text contains «Команды оператора» and does not contain "Operator commands"; `reset_locale()` in teardown.
- `test_strip_lang_prefix_integration` — unit-call `strip_lang_before_verb` with the real `OPERATOR_VERBS | AGENT_VERBS` union over `["--lang", "ru", "install", "--quiet"]` → value `"ru"`, stripped `["install", "--quiet"]`.

`tests/package/test_i18n_cli.py` (new; in-process `main()` + capsys, following `test_version_flag.py` style — no index needed):
- `test_jrag_help_russian_after_verb_flag` — `jrag.main(["find", "--lang", "ru", "--help"])` returns 0 and stdout contains «Язык интерфейса» (the localized flag help) — proves build_parser consulted the locale.
- `test_jrag_help_english_by_default` — `jrag.main(["find", "--help"])` stdout contains "Interface language" (EN) and no Cyrillic.
- `test_cli_help_english_by_default` — `cli.main(["init", "--help"])` returns 0, stdout has "Interface language", no Cyrillic.
- `test_cli_help_russian` — `cli.main(["init", "--lang", "ru", "--help"])` stdout contains «Язык интерфейса».
- `test_jrag_invalid_lang_rejected` — `jrag.main(["find", "--lang", "fr", "--help"])` → argparse error path returns 1 (choices rejection reaches the envelope/usage error).
- `test_env_drives_help_locale` — monkeypatch env `JAVA_CODEBASE_RAG_LANGUAGE=ru`: `jrag.main(["find", "--help"])` stdout is Russian (env tier works with no flags at all). Teardown resets locale + env.
- Every test ends with `i18n.reset_locale()` and `i18n.set_cli_lang_override(None)` (autouse fixture).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py tests/package/test_cli_dispatch.py -v`
Expected: new tests FAIL (no `--lang` flag → argparse error/exit, or English-only help); existing dispatch tests still PASS.

- [ ] **Step 3: Write the implementation**

Implement per Interfaces. Notes: register `--lang` on the top-level `jrag` parser AFTER `--version` (order in help output); `cli._add_lang_flag` mirrors `_add_verbosity_flags` (cli.py:320-333) and is applied to every operator subparser where `_add_index_embedding_flags`/`_add_verbosity_flags` are applied today; `dest="lang"` everywhere so `getattr(args, "lang", None)` is uniform (also on `_core_parser` commands). `init_help_locale` result is advisory for rendering; the authoritative re-set happens via `_resolve_cfg`/`_resolved_from_ns`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py tests/package/test_cli_dispatch.py tests/package/test_version_flag.py tests/package/test_jrag_enum_choices.py -v`
Expected: all PASS (the enum/choices suite proves existing parser surfaces are intact).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/cli_dispatch.py src/java_codebase_rag/jrag.py src/java_codebase_rag/cli.py tests/package/test_cli_dispatch.py tests/package/test_i18n_cli.py`
Run: `git commit -m "feat(i18n): --lang flag, dispatch pre-scan, locale wiring in both mains"`

---

### Task 4: agent render layer — `jrag_render.py` + `jrag_envelope.py`

**Files:**
- Modify: `src/java_codebase_rag/jrag_render.py`
- Modify: `src/java_codebase_rag/jrag_envelope.py` (authored hint strings only)
- Test: `tests/package/test_jrag_render.py` (add cases; never modify existing ones)

**Interfaces:**
- Consumes: `i18n.tr`, `i18n.ntr`, `set_locale`/`reset_locale` (Task 1).
- Produces: every fixed English phrasing in the render path becomes a `tr()`/`ntr()` call evaluated at render time. The full inventory to migrate (from the module as it stands):
  - `_ABSENCE_VERDICT_TEXT` values (jrag_render.py:40-45) → four `LBL_ABSENCE_*` keys; the `"Verdict: "` prefix (line 52) → `LBL_VERDICT_PREFIX`.
  - `"next: "` prefix (line 126) → `LBL_NEXT_PREFIX`.
  - Error/not-found prefixes: `"error:"`, `"not found:"`, `"Did you mean:"` (lines ~269-298) → `LBL_ERROR_PREFIX`, `LBL_NOT_FOUND_PREFIX`, `LBL_DID_YOU_MEAN`.
  - Truncation notice `"truncated: more results — …"` (lines ~261-264) → `MSG_TRUNCATED` (keep the `--offset` mention; RU: «обрезано: результатов больше — …»).
  - `"warning:"` prefix (line ~924) → `LBL_WARNING_PREFIX`.
  - Empty-listing `"0 {noun}"` line (lines ~374-376) → shared template key `MSG_ZERO_LISTING` (`"{noun}: 0"` in both locales; the count is always 0, so no plural forms needed); the noun words themselves (matches, routes, clients, producers, topics, jobs, listeners, entities) become `LBL_NOUN_*` keys looked up at render time.
  - `"external entrypoint — no in-repo callers"` (lines ~500/507) → `MSG_EXTERNAL_ENTRYPOINT`.
  - `"inbound:"`/`"outbound:"` (lines ~537/541), `"↑ supertypes:"`/`"↓ subtypes:"` (~592/596), `"stage N:"` template (~576-580) → `LBL_INBOUND`, `LBL_OUTBOUND`, `LBL_SUPERTYPES`, `LBL_SUBTYPES`, `LBL_STAGE` (`"stage {n}:"`).
  - `"Narrow with --kind …"` (~694) → `HINT_NARROW_KIND`.
  - `jrag_envelope.py`: every literal appended to `agent_next_actions` (the `next_actions_hook` hints and command-specific hints — `jrag inspect …` drill-downs, resolve suggestions) → `HINT_*` keys. `jrag <cmd>` command text inside hints stays literal English (command names never translate); only the surrounding sentence translates.
- EN catalog values are the EXACT current strings (byte-for-byte, including em dashes and spacing) — this is what keeps `test_jrag_render.py` and the goldens green.

- [ ] **Step 1: Write the failing tests**

Add to `tests/package/test_jrag_render.py` (same pure-unit style: construct `Envelope`/`AbsenceDiagnosis` objects, call `render`, assert text):
- `test_render_russian_not_found_verdict` — envelope `status="not_found"` with an absence diagnosis verdict `refine_query`, locale `ru`: output contains «Вердикт: уточните запрос» (exact RU label per catalog) and does not contain "Verdict:".
- `test_render_russian_error_prefix` — `status="error"` envelope with a message: RU output line starts with «ошибка:» (the `LBL_ERROR_PREFIX` RU value).
- `test_render_russian_next_lines` — envelope with two `agent_next_actions`: RU output contains «далее: » prefix on both lines.
- `test_render_russian_truncated_notice` — envelope `truncated=True`: RU output contains «обрезано».
- `test_render_russian_zero_listing` — empty listing with noun matches under RU: output is «совпадения: 0» (final exact string per the chosen zero-listing approach; assert the Cyrillic noun and the digit 0).
- `test_render_english_unchanged_after_migration` — re-assert three already-pinned behaviors in NEW tests (do not touch existing ones): `"0 matches"` for an empty matches listing, `"next: "` prefix, `"Verdict: "` + "refine your query" — proving EN byte-stability from the same code paths.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_jrag_render.py -v`
Expected: new RU tests FAIL with English output; existing tests still PASS.

- [ ] **Step 3: Write the implementation**

Migrate the inventory above: add each key to BOTH catalogs (EN exact-current, RU per the style guide), replace literals with `tr()`/`ntr()` calls. `_ABSENCE_VERDICT_TEXT` becomes a function or the lookup moves into `_verdict_line` so evaluation is call-time (import-time trap). No signature changes — `render()`'s contract is untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_jrag_render.py tests/package/test_i18n.py -v`
Expected: all PASS including parity (catalogs grew in both locales).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/jrag_render.py src/java_codebase_rag/jrag_envelope.py src/java_codebase_rag/i18n_messages_en.py src/java_codebase_rag/i18n_messages_ru.py tests/package/test_jrag_render.py`
Run: `git commit -m "feat(i18n): localize agent render labels and envelope hints"`

---

### Task 5: agent-verb runtime strings and error paths — `jrag.py`

**Files:**
- Modify: `src/java_codebase_rag/jrag.py`
- Test: `tests/package/test_i18n_cli.py`

**Interfaces:**
- Consumes: Task 1 runtime, Task 3 wiring, Task 4 render labels.
- Produces: the remaining authored strings in `jrag.py` migrate to `tr()`:
  - The ~26 `Envelope(message=…)` construction sites: `message=` values become `tr("MSG_*")`/`ntr(...)` (missing-index remediation, ambiguous-resolution counts via `ntr`, resolve outcomes, watch/status/vocab-index/prime status lines).
  - `main()` error paths: `"usage error"` fallback (jrag.py:4618), `"internal error: "` prefix (4638), stderr `"jrag: error: "` prefix (4623) → `ERR_*`/`LBL_*` keys (RU stderr prefix: `jrag: ошибка: `).
  - `"\nInterrupted.\n"` in `_console_script_main` (4664) → `MSG_INTERRUPTED` (RU: «\nПрервано.\n»).
  - stderr notes: auto-scope notice (~152), watch status verb output (~1484-1538), vocab-index output (~1405-1426), watch detach lines (1590-1597).
  - `_preparse_render_flags` fallbacks stay as-is (flag values, not prose).
- Counts inside messages use `ntr` plural keys where the English differs by count (`"1 candidate"` / `"N candidates"`, ambiguous lists).

- [ ] **Step 1: Write the failing tests**

Add to `tests/package/test_i18n_cli.py`:
- `test_error_envelope_russian_usage_error` — `jrag.main(["callers"])` (missing required positional) with `set_cli_lang_override("ru")` and monkeypatched env unset: returns 2, stdout error envelope contains the RU usage-error message, stderr line starts `jrag: ошибка:`. Teardown resets.
- `test_error_envelope_russian_internal_error` — monkeypatch a verb handler to raise `RuntimeError("boom")`, run under RU: stdout envelope message starts with the RU «внутренняя ошибка:» prefix, rc 2.
- `test_missing_index_russian_message` — `jrag.main(["status", "--lang", "ru", "--index-dir", <empty tmp path>])` in a cwd with no project: the not-found/error envelope message is Russian (assert a Cyrillic substring of the missing-index remediation) — no index build needed.
- `test_ambiguous_plural_russian` — unit assertion on the migrated count message: `set_locale("ru")` then `ntr("MSG_AMBIGUOUS_CANDIDATES", 3)` yields «3 кандидата» and `ntr(..., 5)` yields «5 кандидатов»; under `"en"`, 1 → "1 candidate" and 5 → "5 candidates" (exact EN per the pre-change source string for the ambiguous-resolution count).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py -v`
Expected: new tests FAIL (English messages).

- [ ] **Step 3: Write the implementation**

Migrate per Interfaces. Add every key to both catalogs. Keep exit codes and envelope field names untouched. The `f"{cmd}: {msg}"` composition (4619-4620) keeps the literal command name prefix — only the argparse-originated message text stays English (stdlib fragments, out of scope).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py tests/package/test_jrag_locate.py tests/package/test_jrag_auto_scope.py tests/package/test_jrag_status.py -v`
Expected: all PASS (the three existing suites prove default-EN behavior survived the migration).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/jrag.py src/java_codebase_rag/i18n_messages_en.py src/java_codebase_rag/i18n_messages_ru.py tests/package/test_i18n_cli.py`
Run: `git commit -m "feat(i18n): localize agent-verb messages and error paths"`

---

### Task 6: operator CLI strings — `cli.py`, `_deprecation.py`, `pipeline.py`

**Files:**
- Modify: `src/java_codebase_rag/cli.py` (constants at 44-69, handler prints, `_emit` internal-error message at 1263, `main` error wrapper at 1254, `"\nInterrupted.\n"` at 1287)
- Modify: `src/java_codebase_rag/_deprecation.py`
- Modify: `src/java_codebase_rag/pipeline.py` (constants at 26-48)
- Test: `tests/package/test_i18n_cli.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces:
  - `cli.py` module constants `_INCREMENT_WARNING_LINES`, `_REFRESH_DEPRECATION` (used at 1243-1245), and the `_REPROCESS_DRIFT_VECTORS_ONLY` line → functions returning `list[str]`/`str` built from `tr()` calls (import-time trap — Global Constraints); update their call sites accordingly.
  - Every handler `print(...)` user-facing line (erase prompts ~789-810, meta/tables headers ~344-350, diagnose-ignore / analyze-pr / unresolved-calls output) → `tr()`/`ntr()`.
  - `main()` paths: `f"jrag: {exc}"` stderr wrapper → `jrag: ` prefix + `ERR_ARG_PREFIX` composition (RU: `jrag: ошибка: …`); `{"success": False, "exit_code": 2, "message": f"internal error: {exc}"}` — JSON keys and `success`/`exit_code` stay English; the message VALUE localizes its «внутренняя ошибка:» prefix only.
  - `_deprecation.py`: `maybe_warn_legacy_alias` warning text + `emit_legacy_env_hints_if_present`/`emit_legacy_yaml_hint_if_needed` strings → `tr()`.
  - `pipeline.py` constants `VECTORS_SKIPPED_GRAPH_ONLY`, `VECTORS_SKIPPED_BM25`, `RETRIEVAL_BM25_HINT` → functions (same trap); their consumers are `cli.py`, `installer.py`, and the reprocess stderr relay — all call-time.
  - Delete the three `MSG_TEST_*`/`LBL_TEST_*` bootstrap keys from all catalogs and from `test_i18n.py`'s direct-reference tests (keep parity/shape/static-scan tests).

- [ ] **Step 1: Write the failing tests**

Add to `tests/package/test_i18n_cli.py`:
- `test_cli_arg_error_russian` — `cli.main(["erase", "--lang", "ru", "--bogus"])` returns 2 and stderr starts `jrag: ошибка:`.
- `test_cli_internal_error_russian` — monkeypatch an operator handler to raise; RU run: stdout JSON parses (json.loads), keys are exactly `success`/`exit_code`/`message` (English keys), message value contains «внутренняя ошибка», rc 2.
- `test_refresh_deprecation_russian` — `cli.main(["refresh", "--lang", "ru"])` under cwd tmp: stderr contains the RU deprecation line (and the command still maps to reprocess behavior — rc from reprocess path).
- `test_erase_prompt_russian` — monkeypatch `builtins.input` to decline; `cli.main(["erase", "--lang", "ru", "--index-dir", <tmp>])` reaches the confirmation prompt: captured stdout prompt text is Russian (Cyrillic substring assert), rc unchanged from the EN path.
- `test_increment_warning_lazy_russian` — direct unit: call the new warning-lines function under `set_locale("ru")`, assert Cyrillic; call under `"en"`, assert the exact current English lines (byte-stable).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py -v`
Expected: new tests FAIL (English output / AttributeError on the not-yet-existing functions is acceptable — prefer writing them test-first against the function names given above).

- [ ] **Step 3: Write the implementation**

Migrate per Interfaces; every key in both catalogs; constants → functions with the same names minus the leading underscore convention where they are consumed cross-module (`pipeline.py` exports become `vectors_skipped_graph_only()` etc. — update `installer.py` import sites minimally NOW if they reference the constants, full installer migration is Task 7).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py tests/package/test_i18n.py tests/package/test_java_codebase_rag_cli.py tests/package/test_cli_quiet_parity.py tests/package/test_cli_progress_stdout_invariant.py tests/package/test_deprecation.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/cli.py src/java_codebase_rag/_deprecation.py src/java_codebase_rag/pipeline.py src/java_codebase_rag/i18n_messages_en.py src/java_codebase_rag/i18n_messages_ru.py tests/package/test_i18n_cli.py tests/package/test_i18n.py`
Run: `git commit -m "feat(i18n): localize operator CLI messages; lazy advisory constants"`

---

### Task 7: installer wizard — `installer.py`

**Files:**
- Modify: `src/java_codebase_rag/installer.py` (~80 wizard prints/prompts)
- Test: `tests/package/test_installer.py`

**Interfaces:**
- Consumes: Tasks 1-3, Task 6's `pipeline.py` function renames.
- Produces: every wizard prompt, progress line, and summary line in `run_install`/`select_*` helpers → `tr()`; the `select_retrieval` option descriptions; the vectors-failure remediation tip (spec sibling of the retrieval spec's D6 hint); `generate_yaml_config` writes YAML keys — YAML KEYS STAY ENGLISH (never translate `retrieval:`, `language:`-style keys). No behavior change: same questions, same defaults, same exit codes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/package/test_installer.py` (reuse its existing stdin-feeding/monkeypatch fixtures):
- `test_wizard_prompt_russian` — feed default answers via monkeypatched `input`; run the wizard surface entry (or `select_retrieval` directly) with `set_locale("ru")`: prompt text contains Cyrillic; returned selection is the default (behavior parity).
- `test_wizard_english_default` — same run without locale: prompts byte-identical to today (assert one pinned English prompt line that exists pre-change — copy it from the current source).
- `test_generate_yaml_keys_english_under_ru` — run `generate_yaml_config` under `set_locale("ru")` with non-default answers: emitted YAML contains only English keys (`retrieval:`, `embedding:`, `microservice_roots:`), and does NOT contain `language:` (the installer never writes it — spec).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_installer.py -v -k russian or -k language`
Expected: RU tests FAIL (English prompts).

- [ ] **Step 3: Write the implementation**

Migrate all wizard strings; keys in both catalogs. Interactive prompts keep their `\n` shapes and `[y/N]`-style affordances untranslated (bracket affordances are input syntax).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_installer.py tests/package/test_installer_surface.py tests/package/test_installer_retrieval.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/installer.py src/java_codebase_rag/i18n_messages_en.py src/java_codebase_rag/i18n_messages_ru.py tests/package/test_installer.py`
Run: `git commit -m "feat(i18n): localize installer wizard; YAML keys stay English"`

---

### Task 8: shared producers — `resolve_service.py`, `absence_diagnosis.py`

**Files:**
- Modify: `src/java_codebase_rag/analysis/resolve_service.py` (messages ~611-613 and siblings)
- Modify: `src/java_codebase_rag/absence/absence_diagnosis.py` (authored help texts at ~146, 192, 247, 285, 326, 409-418, 655)
- Test: `tests/analysis/test_resolve_service_i18n.py` (new; check the existing `tests/analysis/` layout and follow it), `tests/package/test_i18n_cli.py`

**Interfaces:**
- Consumes: Task 1 runtime.
- Produces: authored message strings in both modules → `tr()` evaluated at call time. **These functions are shared with MCP** — the MCP safety property is that no code path under `src/java_codebase_rag/mcp/` ever sets a locale, so the same `tr()` returns English there. Do NOT add locale parameters to these functions; they read process state only.

- [ ] **Step 1: Write the failing tests**

- `tests/analysis/test_resolve_service_i18n.py`: `test_resolve_message_russian` — build the minimal fake-graph/identifier inputs the way existing `tests/analysis/` resolve tests do; call the resolve function under `set_locale("ru")` with no matches: returned message is Russian (Cyrillic assert). `test_resolve_message_english_default` — same call, no locale: message byte-identical to the pre-change string (pin it from current source).
- `tests/package/test_i18n_cli.py`: `test_mcp_process_stays_english_with_env_set` — monkeypatch `os.environ["JAVA_CODEBASE_RAG_LANGUAGE"]="ru"`, import `java_codebase_rag.mcp.server` fresh (or call the absence-diagnosis authoring function directly WITHOUT any set_locale — simulating the MCP process), assert the produced text is English. This encodes the invariant: env alone never localizes; only `set_locale` does.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/analysis/test_resolve_service_i18n.py tests/package/test_i18n_cli.py -v`
Expected: RU test FAILs; the MCP-English test PASSes already (it pins current behavior — keep it as the regression guard).

- [ ] **Step 3: Write the implementation**

Swap literals for `tr()` calls; keys in both catalogs; EN values byte-exact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/analysis/ tests/absence/ tests/mcp/ tests/package/test_i18n_cli.py -v`
Expected: all PASS — the MCP suite passing is the proof that shared producers stayed English on the MCP path.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/analysis/resolve_service.py src/java_codebase_rag/absence/absence_diagnosis.py src/java_codebase_rag/i18n_messages_en.py src/java_codebase_rag/i18n_messages_ru.py tests/analysis/test_resolve_service_i18n.py tests/package/test_i18n_cli.py`
Run: `git commit -m "feat(i18n): shared producers localize via tr(); MCP path stays English"`

---

### Task 9: subprocess boundaries — watch daemon, watcher reprocess, MCP scrub

**Files:**
- Modify: `src/java_codebase_rag/jrag.py` (watch detach Popen ~1564)
- Modify: `src/java_codebase_rag/watch/watcher.py` (subprocess_env call sites 303, 327)
- Modify: `src/java_codebase_rag/mcp/server.py` (`_cocoindex_subprocess_env` 267-277)
- Test: `tests/package/test_i18n_cli.py`, `tests/watch/` (follow existing watch test layout)

**Interfaces:**
- Consumes: Task 2's `subprocess_env(language=True)`, `cfg.language`.
- Produces:
  - Watch detach spawn (`jrag.py` `_spawn_detached`): the `Popen(...)` gains `env=dict(os.environ, JAVA_CODEBASE_RAG_LANGUAGE=cfg.language)` — the daemon child resolves `ru` from its env tier even when the parent used the CLI flag tier (flag values are not inherited any other way).
  - `watcher.py` reprocess child spawns (lines 303, 327): `self.cfg.subprocess_env()` → `self.cfg.subprocess_env(language=True)`.
  - `mcp/server.py` `_cocoindex_subprocess_env`: after `os.environ.copy()`, remove `JAVA_CODEBASE_RAG_LANGUAGE` from the copy (pop with default None). Every MCP child spawn (lines 402, 530, 550, 574) flows through this one function.

- [ ] **Step 1: Write the failing tests**

- `tests/package/test_i18n_cli.py`: `test_mcp_subprocess_env_scrubs_language` — monkeypatch `os.environ["JAVA_CODEBASE_RAG_LANGUAGE"]="ru"`, call `server._cocoindex_subprocess_env(tmp_path)`: returned dict has no `JAVA_CODEBASE_RAG_LANGUAGE` key (and still carries `JAVA_CODEBASE_RAG_SOURCE_ROOT`).
- `tests/watch/` (new test, following the file layout/style of the existing watch tests, e.g. the daemon-state polling test): `test_watcher_passes_language_to_child` — construct the watcher's cfg with `language="ru"` (via a resolved config on a tmp project), capture the env dict the spawn call builds (monkeypatch `subprocess.Popen`/`subprocess.run` to record `env=`), assert `JAVA_CODEBASE_RAG_LANGUAGE == "ru"`.
- `test_detach_spawn_env` — same recording pattern for the detach Popen: env contains the resolved language.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py -v -k "scrub or language_to_child or detach_spawn"` plus the new watch test path
Expected: FAIL (no language key passed / scrub absent).

- [ ] **Step 3: Write the implementation**

The three changes per Interfaces. Nothing else in the spawn call sites changes (stdio, setsid, close_fds untouched).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py tests/watch/ tests/test_config_watch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/jrag.py src/java_codebase_rag/watch/watcher.py src/java_codebase_rag/mcp/server.py tests/`
Run: `git commit -m "feat(i18n): language crosses operator subprocesses, never MCP ones"`

---

### Task 10: help-text migration — the `HELP_*` catalogs

**Files:**
- Modify: `src/java_codebase_rag/jrag.py` (~150 `help=`/`description` strings, the main description block ~392-409)
- Modify: `src/java_codebase_rag/cli.py` (~34 help strings incl. `_add_index_embedding_flags`/`_add_verbosity_flags`/`_add_lang_flag` helps)
- Modify: `src/java_codebase_rag/i18n_messages_help_en.py`, `src/java_codebase_rag/i18n_messages_help_ru.py`
- Test: `tests/package/test_i18n_cli.py`

**Interfaces:**
- Consumes: Task 3 (locale is set before `build_parser()` in every path that renders help).
- Produces: every argparse `help=`, `description=`, `epilog=` string becomes `tr("HELP_*")` evaluated at parser-build time. `metavar`s, `dest`s, `choices` stay literal. The multi-line `jrag` description block becomes a small set of `HELP_DESC_*` keys (one per paragraph) joined at build time — keep the EN concatenation byte-identical (line breaks included) so `jrag --help` EN output is unchanged.
- `--version` help/version string stays literal English.

- [ ] **Step 1: Write the failing tests**

Add to `tests/package/test_i18n_cli.py`:
- `test_help_russian_representative_commands` — `jrag.main([c, "--lang", "ru", "--help"])` for `c` in `("find", "callers", "status")`: rc 0, stdout contains Cyrillic and none of the pinned English help phrases ("Filter by microservice", "Output format", "Index directory override").
- `test_help_english_byte_stable` — snapshot-compare: run `jrag.main(["find", "--help"])` before touching source? Not possible post-hoc — instead assert three pinned EN lines in new tests: "Filter by microservice.", "Output format (default: text).", "Cap on results (default 20)." for `find --help`; and for `cli.main(["init", "--help"])`: "Java repository root (default: cwd)".
- `test_operator_help_russian` — `cli.main(["install", "--lang", "ru", "--help"])`: Cyrillic present, "Java repository root" absent.
- `test_unified_help_russian_via_dispatch_subprocess` — subprocess (`_run_jrag` pattern from `tests/package/test_jrag_listing.py`): `jrag --lang ru --help` → stdout contains «Команды оператора» and the RU description header; exit code 0. Also `jrag --lang=ru status --help` (equals-form through dispatch).
- `test_no_raw_english_help_left` — static grep test: regex over `jrag.py`/`cli.py` for `help="` / `help=('` literals that contain a lowercase English word — asserts every match is a `tr(` call or an empty string (metavar-style). Fail listing offending line numbers.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py -v -k help`
Expected: RU help tests FAIL (English help); the static grep test FAILs listing the not-yet-migrated literals.

- [ ] **Step 3: Write the implementation**

Mechanical migration: one `HELP_*` key per string (shared strings — "Index directory override (default: discovered from cwd)." appears in both parsers and both files — share ONE key), EN values byte-exact, RU values per the style guide. Keep key names derived from the flag (`HELP_FLAG_SERVICE`, `HELP_FLAG_LIMIT`, `HELP_CMD_FIND`, `HELP_DESC_JRAG_INTRO`, …). Do not reflow the EN description block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py tests/package/test_i18n.py tests/package/test_cli_dispatch.py tests/package/test_jrag_enum_choices.py tests/package/test_version_flag.py -v`
Expected: all PASS (parity tests confirm both help catalogs grew identically).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/jrag.py src/java_codebase_rag/cli.py src/java_codebase_rag/i18n_messages_help_en.py src/java_codebase_rag/i18n_messages_help_ru.py tests/package/test_i18n_cli.py`
Run: `git commit -m "feat(i18n): localize argparse help for all 44 commands"`

---

### Task 11: documentation

**Files:**
- Modify: `docs/CONFIGURATION.md`, `docs/JRAG-CLI.md`, `docs/ARCHITECTURE.md`, `docs/DESIGN.md`

**Interfaces:**
- Consumes: shipped behavior of Tasks 1-10.
- Produces (docs are English — the tool's docs are not localized, only the CLI):
  - `CONFIGURATION.md`: new `language` section — YAML `language: en | ru`, `JAVA_CODEBASE_RAG_LANGUAGE`, `--lang`/`-L`; precedence CLI > env > YAML > default `en`; invalid-value degradation; the "MCP is always English" note; the YAML-only help edge case (help renders English when the YAML is not discoverable from cwd); statement that the installer never writes the key.
  - `JRAG-CLI.md`: `--lang` before/after verb examples, env/YAML recipes, note that exit codes / JSON keys / `--format` values are locale-independent.
  - `ARCHITECTURE.md`: `i18n.py` + catalog modules in the module map; the locale invariant (CLI-entry-only `set_locale`, no env republication, MCP scrub) in the subprocess/env notes.
  - `DESIGN.md`: one short paragraph — opt-in localization, values-not-contracts boundary, EN-first fallback.

- [ ] **Step 1: Write the docs**

Per Interfaces; match each file's existing heading style and cross-link style.

- [ ] **Step 2: Verify claims against behavior**

Run: `.venv/bin/python -m pytest tests/package/test_i18n_cli.py -v` and spot-check two documented examples by running the installed binary: `.venv/bin/jrag --lang ru --help | head -5` (Cyrillic) and `JAVA_CODEBASE_RAG_LANGUAGE=ru .venv/bin/jrag find --help | head -5` (Cyrillic). Expected: both Russian.

- [ ] **Step 3: Commit**

Run: `git add docs/CONFIGURATION.md docs/JRAG-CLI.md docs/ARCHITECTURE.md docs/DESIGN.md`
Run: `git commit -m "docs(i18n): language knob, precedence, MCP-always-English invariant"`

---

### Task 12: full-suite verification

**Files:**
- No source changes expected — this task only verifies and fixes regressions found.

- [ ] **Step 1: Clean stale test indexes**

Run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`

- [ ] **Step 2: Run the FULL suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: all PASS, zero failures. The golden payloads (`tests/jrag/golden/`), MCP suite, and every pre-existing CLI test must pass WITHOUT modification — that is the English byte-stability proof.

- [ ] **Step 3: Fix any regression in the task that introduced it**

If a test fails, the regression is in the most recent touched module per the failure; fix at the source (never edit the golden/pinned test).

- [ ] **Step 4: Final catalog audit**

Run: `.venv/bin/python -m pytest tests/package/test_i18n.py -v` once more (parity + static key scan) and `grep -rn "tr(\"" src/java_codebase_rag/ | wc -l` sanity-checks a nonzero call count. Expected: parity green.

- [ ] **Step 5: Commit (if anything changed) and report**

Run: `git status --short` — expected clean (or commit the fix from Step 3 with `fix(i18n): …`).

---

## Self-Review Notes

- Spec coverage: D1 → Task 1; D2 → Task 2 (+ Task 9 scrub/pass-through); D3 → Task 3; D4 → Tasks 3, 8 (MCP-English tests); D5 → Task 9; D6 → Global Constraints + Tasks 4-7 error paths; D7 → Task 8; help coverage → Tasks 3, 10; docs → Task 11; goldens/MCP regression → every task's Step 4 + Task 12.
- Types consistent: `cli_language` (resolver param) vs `args.lang` (flag dest) vs `cfg.language` (resolved field) — used uniformly; `subprocess_env(language=True)` introduced in Task 2, consumed in Task 9 only.
- No implementation code above — signatures, contracts, exact expected strings only.
