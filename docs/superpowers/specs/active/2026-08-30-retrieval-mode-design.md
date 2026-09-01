# Retrieval mode: user-selectable vectors | bm25

- **Date:** 2026-08-30
- **Status:** implemented

## Motivation

Semantic search requires an embedding model (`all-MiniLM-L6-v2`,
`index_common.py:7-10`) that is auto-downloaded from Hugging Face at first
use — or supplied as a local path. Not every user can reach Hugging Face:
corporate proxies, air-gapped networks, and restricted CI block the download
**on every platform**, not just macOS Intel.

Today the vectors-vs-lexical split is decided by packaging, not by the user:

- The PEP 508 markers in `pyproject.toml:35-46` exclude the vector trio
  (cocoindex[lancedb], lancedb, sentence-transformers) on Intel Mac only;
  there, `search` falls back to the lexical backend (`search_lexical.py`)
  and the installer skips the embedding-model prompt (`installer.py:2141-2149`).
- Everywhere else, vectors are mandatory. When the model download fails:
  - **index time**: the cocoindex child exits non-zero — a failed install
    with no remedy offered (`cli.py:386-396`);
  - **query time**: the raw HF error surfaces in the failure envelope and is
    **retried on every query** — dispatch is import-based, the import
    succeeded, so no fallback exists (`mcp_v2.py:1137-1138`).

The lexical backend, meanwhile, is already a first-class citizen: BM25 runs
as one of three fused lists on the vector path (`search_lancedb.py:780-908`,
`docs/DESIGN.md:22`), and the standalone lexical path (BM25 FTS + heuristic
re-rank over the symbol graph) is complete, offline-capable, and exercised
daily by Intel Mac users.

**Goal:** let any user, on any platform, choose BM25 instead of vectors at
install time — the same choice macOS Intel users get by packaging accident.

## Goal & scope

**Goal.** A `retrieval` mode setting — `vectors` (default) | `bm25` — chosen
in the `jrag install` wizard (or via `--retrieval` / env var), persisted to
`.java-codebase-rag.yml`, honored by indexing, search dispatch, and the watch
daemon. Choosing `bm25` means no model download ever happens; the install
completes fully offline.

**In scope.** Config triple (YAML key, env var, install flag); wizard
question; index-time skip of the vectors phase; search-dispatch precedence;
mode-aware advisories and daemon label; remediation hint on vectors
failures; docs; tests.

**Out of scope.** Any ranking/scoring change; silent auto-fallback to
lexical on download failure (approach B — rejected: silent quality
degradation, ambiguous index state); hybrid weight tuning; renaming internal
`lexical` identifiers; a `pip install`-time selection (PEP 508 markers
cannot be user-toggled); MCP/watch restart-on-switch automation.

## Decisions

- **D1 — Public value `bm25`, internal name `lexical`.** The user-facing
  setting value is `bm25` (matches the wizard vocabulary and the user's
  mental model); all internal machinery keeps its existing names
  (`search_lexical`, `lexical_mode`). No rename.
- **D2 — Config triple, standard precedence.** `retrieval` resolves
  CLI > env > YAML > built-in default (`vectors`) via a `_pick_str` in
  `resolve_operator_config` (`config.py:586-766`), following the
  `embedding.model ↔ SBERT_MODEL ↔ --embedding-model` pattern
  (`config.py:634-646`): new `retrieval` + `retrieval_source` fields on
  `ResolvedOperatorConfig`, republished into `os.environ` via
  `apply_to_os_environ` / `subprocess_env` so the MCP server, cocoindex
  child, and watch daemon see one resolved value. Invalid values: rejected
  at the CLI tier by argparse `choices`; at the env/YAML tiers they degrade
  gracefully — a stderr warning naming the two valid values and a fallback
  to `vectors`/`default` (the `watch.backend` pattern, `config.py:721-727`).
- **D3 — Wizard: one question, before the model question.** New
  `select_retrieval()` modeled on `select_surface` (`installer.py:576-635`):
  `vectors (Recommended)` — "semantic search; requires an embedding model,
  auto-downloaded from Hugging Face or usable from a local path" — versus
  `bm25` — "lexical (keyword) search; no model, no downloads, works
  offline". Prefill from existing YAML on re-run; `--retrieval` flag
  bypasses the prompt; non-interactive default `vectors`. On Intel Mac
  (`vector_stack_installed()` false) the prompt is skipped and `bm25` is
  forced — the packaging gate and the knob agree by construction. Choosing
  `bm25` skips the embedding-model question entirely.
- **D4 — Index skip is decided before spawning.** With mode `bm25`,
  `run_cocoindex_update` is never invoked — on every vectors-phase call site
  (installer `run_init_if_needed`, CLI `init`/`increment`/`reprocess`, MCP
  `reprocess` at `server.py:428`, watcher catch-up at `watcher.py:149`). A
  pre-call decision, distinct from today's post-hoc
  `is_cocoindex_preflight_blocker` detection, which stays as-is for the
  Intel-Mac case. Graph + FTS build always runs.
- **D5 — Dispatch: config first, import probe second.** `search_v2` goes
  lexical when mode is `bm25` **or** the vector stack is unimportable —
  today's memoized import probe remains, unchanged, as the safety net.
  Advisory wording becomes mode-aware: chosen mode → "lexical mode
  (retrieval=bm25)"; stack absent → today's platform wording
  (`mcp_v2.py:977-980`). The daemon state-file `"mode"` label reflects the
  config mode (`daemon.py:106-112`).
- **D6 — Loud failure with an escape hatch, never silent fallback.** When
  the vectors phase fails (index time) or the SBERT model fails to load
  (query time), the existing error output gains one remediation line
  pointing at `jrag install --retrieval bm25` / `JAVA_CODEBASE_RAG_RETRIEVAL=bm25`
  — "indexing and search then work without downloading a model." No failure
  classification, no degradation.
- **D7 — Switching costs, documented not automated.** `vectors → bm25`: no
  reindex (graph + FTS exist on every index). `bm25 → vectors`: one
  `jrag reprocess`. Either direction: restart running MCP servers / watch
  daemon (backend and model are memoized per process).
- **D8 — Disclosed limitation.** In `bm25` mode the `sql`/`yaml` tables are
  not searchable — the lexical path covers Java/Kotlin symbols only
  (`docs/CONFIGURATION.md:472`). Disclosed in the wizard note, docs, and
  advisory.

## Configuration contract

| Tier | Name | Values | Default |
| --- | --- | --- | --- |
| YAML | `.java-codebase-rag.yml` → `retrieval:` | `vectors` \| `bm25` | absent = `vectors` |
| Env | `JAVA_CODEBASE_RAG_RETRIEVAL` | same | — |
| CLI | `jrag install --retrieval {vectors,bm25}` | same | `vectors` |

`generate_yaml_config` (`installer.py:967`) writes `retrieval: bm25` only
when the answer is non-default — mirroring how `embedding.model` /
`microservice_roots` are conditional — and preserves/replaces it on update
re-runs.

## Error handling (vectors chosen, download unreachable)

Today's failure text, plus one remediation line (exact wording plan-level):

> Tip: no network for the embedding model? Switch to keyword search —
> `jrag install --retrieval bm25` (or set `JAVA_CODEBASE_RAG_RETRIEVAL=bm25`).
> Indexing and search then work fully offline.

## Documentation

- `README.md:42` — platform paragraph generalized: lexical on Intel Mac
  **or** user-selected bm25.
- `docs/CONFIGURATION.md` — `JAVA_CODEBASE_RAG_RETRIEVAL` + `retrieval` YAML
  entries; "Graph-only (macOS Intel) lexical ranking" (§460-472)
  retitled/broadened to lexical mode.
- `docs/ARCHITECTURE.md:72,87,89` — dispatch description gains the
  config-first check.
- `docs/DESIGN.md:22` — the lexical-first-class note extends to
  user-selectable primary mode.
- `docs/JRAG-CLI.md` — `--retrieval` flag; switching recipe (reprocess +
  restart).
- Shipped-artifact audit: `grep "Apple Silicon" install_data/ skills/
  agents/` finds no copies of the platform wording today; re-verify when
  docs change.

## Compatibility

- No graph schema, Lance table, or `ONTOLOGY_VERSION` change; **no rebuild
  required** for either direction's first run.
- Existing YAMLs (no `retrieval` key) resolve to `vectors` — behavior
  identical to today on every platform.
- Existing indexes work unchanged in both modes (graph + FTS always
  present).
- Intel Mac behavior is unchanged (knob forced to `bm25` by the packaging
  gate).

## Tests

- **Config** (`tests/test_config_watch.py`): precedence flag > env > YAML >
  default; invalid value rejected; env republication.
- **Installer** (`tests/package/test_installer.py`, following the
  `test_installer_surface.py` pattern): `select_retrieval` (interactive
  default `vectors`, flag, prefill on re-run, Intel-Mac force);
  `generate_yaml_config` writes/omits/preserves the key; `bm25` skips the
  model prompt.
- **Dispatch** (`tests/mcp/test_mcp_v2.py`): `JAVA_CODEBASE_RAG_RETRIEVAL=bm25`
  → lexical path + mode-aware advisory (existing `run_search` monkeypatch
  seam); unset + faked `run_search` → vector path unchanged.
- **Index skip**: mode `bm25` never spawns cocoindex; graph still builds;
  vectors-mode paths unchanged.
- Repo test rules apply: erase stale manual indexes under `tests/`; run the
  relevant subset during development, the full suite once at the end.

## Files touched (design-level)

| File | Change |
| --- | --- |
| `src/java_codebase_rag/config.py` | `retrieval` resolution + fields + env publication |
| `src/java_codebase_rag/installer.py` | `select_retrieval`; `run_install` wiring; `generate_yaml_config` key |
| `src/java_codebase_rag/cli.py` | `--retrieval` flag; vectors-phase skip in init/increment/reprocess |
| `src/java_codebase_rag/mcp/mcp_v2.py` | mode-first dispatch; mode-aware advisory |
| `src/java_codebase_rag/mcp/server.py` | reprocess skip under bm25 |
| `src/java_codebase_rag/watch/daemon.py`, `watcher.py` | mode-aware skip + state-file label |
| `README.md`, `docs/CONFIGURATION.md`, `docs/ARCHITECTURE.md`, `docs/DESIGN.md`, `docs/JRAG-CLI.md` | generalize lexical-mode docs |
| `tests/test_config_watch.py`, `tests/package/`, `tests/mcp/test_mcp_v2.py` | coverage per above |

## TL;DR

Vectors-vs-BM25 is decided by packaging markers today, so users who cannot
reach Hugging Face get hard failures on every platform but Intel Mac. This
change adds one setting — `retrieval: vectors | bm25` — through the repo's
standard YAML/env/flag triple, asked in the `jrag install` wizard (bm25
skips the model question; Intel Mac stays forced), honored at index time
(the embedding phase is simply never spawned) and at search dispatch (config
first, import probe as safety net). Vectors failures gain a switch-to-bm25
remediation hint instead of a raw error. No ranking changes, no schema
changes, no rebuild required; existing installs behave identically.
