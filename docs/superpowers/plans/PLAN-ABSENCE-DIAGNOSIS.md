<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Absence Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan PR-by-PR.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every exploration-tool empty result an unambiguous verdict
(absent / external / refine / correct-empty) plus cause-specific help, so agents
stop looping on searches for symbols that aren't in the project.

**Architecture:** A 3-layer engine — (1) a precomputed, `ontology_version`-stamped
vocabulary index derived from `Symbol` nodes; (2) a stateless `diagnose(...)`
module that classifies an empty result and emits per-cause help; (3) tool
integration that attaches an `AbsenceDiagnosis` to all five MCP outputs and the
CLI `Envelope`.

**Tech Stack:** Python 3.11, pydantic v2, Kùzu (`LadybugGraph`), stdlib `json`,
pytest. No new third-party dependencies.

## Global Constraints

(From the spec `propose/ABSENCE-DIAGNOSIS-PROPOSE.md`, verbatim values.)
- Python `>=3.11`; pydantic `>=2.0,<3`; no new dependencies (msgpack is **not**
  added — see Resolved decisions).
- Editable install only — `.venv/bin/python`; `tests/conftest.py` enforces it.
- New top-level modules must be added to `[tool.setuptools] py-modules` in
  `pyproject.toml` so they ship in the built wheel.
- Every empty result stays `success=True` except `describe` (keeps
  `success=False`); the diagnosis layer never turns an empty into an error.
- The `absence` field is additive/optional on all output models — MCP JSON stays
  backward-compatible.
- Tests follow `AGENTS.md`: erase stale `tests/*/.java-codebase-rag` first; run
  the relevant subset during dev, the full suite once at task end.

---

Status: **active (planning)**. Intended home when approved:
`plans/active/PLAN-ABSENCE-DIAGNOSIS.md`. Implements the approved spec
`propose/ABSENCE-DIAGNOSIS-PROPOSE.md` (commit `7a2a56c`).

> **Grounded against current source (2026-07-08) by direct read of `mcp_v2.py`,
> `resolve_service.py`, `graph_types.py`, `mcp_hints.py`, `ladybug_queries.py`,
> `build_ast_graph.py`, `jrag.py`, `jrag_envelope.py`, `jrag_render.py`,
> `config.py`, `pyproject.toml`, `tests/conftest.py`, `tests/test_mcp_v2.py`,
> `tests/test_resolve_service.py`, `tests/test_mcp_hints.py`,
> `plans/active/PLAN-SEARCH.md`.** Every edit site below is cited `file:line`.

Depends on: nothing external. PR-ABS-0 is the foundation; 1 and 2 build on 0; 3
and 4 build on 2 (and can land in parallel after 2); 5 is docs and lands last.

## Why (context)

Empty results from `search`/`find`/`neighbors` are bare `results=[]` with no
metadata (`mcp_v2.py:944`, `:1036`, `:1639`), so an agent can't tell "refine my
query" from "this symbol genuinely isn't in this project." It loops. The graph
already knows the answer (it holds every `Symbol`, and `Symbol.resolved=false`
marks phantoms/external types). This plan surfaces that knowledge as a
first-class `AbsenceDiagnosis` on every empty result.

## Principles (do not relitigate in review)

1. **Conservative absence.** False-absent (telling an agent to abandon a real
   target) is catastrophic; false-refine is one cheap extra query. Default to
   `refine_query` in the middle band; commit to `not_in_project` only when the
   nearest symbol is far AND the query is identifier-shaped.
2. **Always return candidates + proof.** Regardless of verdict, return the
   nearest N symbols + distances — the one-shot pivot that kills the loop, and
   the auditable proof behind a hard `not_in_project`.
3. **Per-cause help, not one-size.** String did-you-mean only for
   identifier-shaped queries (meaningful only there); NL empties get vocabulary
   context; filter empties get relaxation; external targets get an identity.
4. **External wins.** If the target is external/phantom, say so first.
5. **Diagnosis never fails the tool.** Best-effort enrichment; degrade to
   `refine_query`/`None` on any internal error.

## Architecture (where the code lives)

- **New module `absence_types.py`** — the `AbsenceDiagnosis` DTO + sub-DTOs +
  `AbsenceVerdict`/`AbsenceCause` literals. Imported by `mcp_v2.py`,
  `resolve_service.py`, `absence_diagnosis.py`, `jrag_envelope.py`.
- **New module `absence_vocab.py`** — `VocabularyIndex`: build from graph, load,
  save, lookup, external check. Built at end of graph build in
  `build_ast_graph.write_ladybug` (`:4168`); persisted as a sidecar JSON under
  `JAVA_CODEBASE_RAG_INDEX_DIR`; lazily rebuilt if missing/stale.
- **New module `absence_diagnosis.py`** — `diagnose(...)`: the stateless
  classifier + per-cause help assembler. Reuses `_find_has_identifier_shaped_filter`
  (`mcp_hints.py:280`), `_is_external_fqn` (`ladybug_queries.py:267`),
  `module_counts`/`microservice_counts` (`ladybug_queries.py:987`).
- **Modified `mcp_v2.py`** — add `absence` field to `SearchOutput`/`FindOutput`/
  `DescribeOutput`/`NeighborsOutput` (`:509/:525/:546/:554`); call `diagnose(...)`
  in the 4 empty paths (`:953`, `:1052`, `:1096`, `:1638`).
- **Modified `resolve_service.py`** — add `absence` to `ResolveOutput` (`:121`,
  `extra="forbid"` — declared field is fine); call `diagnose(...)` in the
  `status="none"` branch (`:570`).
- **Modified `build_ast_graph.py`** — build + persist the vocab index at the end
  of `write_ladybug` (after `_write_meta`, `:4208`).
- **Modified `config.py`** — 5 absence knobs (env + YAML) on
  `ResolvedOperatorConfig` (`:353`); add a `_pick_float` helper.
- **Modified `java_codebase_rag/jrag_envelope.py` + `jrag_render.py`** — add
  `absence` to `Envelope` (`:86`) + serialization; render verdicts to text;
  generalize `is_external_entrypoint` under `correct_empty`.
- **Modified `pyproject.toml`** — add the 3 new modules to `py-modules`.
- **Docs** — `docs/AGENT-GUIDE.md`, `docs/CONFIGURATION.md`.

## PR breakdown — overview

| PR | Scope | Ontology bump | Key files | Independent of |
|---|---|---|---|---|
| PR-ABS-0 | Shared types + `absence` field on 5 models + config knobs | none | `absence_types.py` (new), `mcp_v2.py`, `resolve_service.py`, `config.py`, `pyproject.toml` | — (foundation) |
| PR-ABS-1 | Vocabulary index asset (build/load/save/lookup/backfill) | none | `absence_vocab.py` (new), `build_ast_graph.py`, `jrag.py` (subcmd) | PR-ABS-0 |
| PR-ABS-2 | `diagnose(...)` module — classifier + per-cause help | none | `absence_diagnosis.py` (new) | PR-ABS-0, PR-ABS-1 |
| PR-ABS-3 | MCP tool integration (5 empty paths) | none | `mcp_v2.py`, `resolve_service.py` | PR-ABS-2 |
| PR-ABS-4 | CLI envelope alignment + renderer | none | `jrag_envelope.py`, `jrag_render.py` | PR-ABS-2 |
| PR-ABS-5 | Docs (AGENT-GUIDE recovery playbook + CONFIGURATION knobs) | none | `docs/AGENT-GUIDE.md`, `docs/CONFIGURATION.md` | PR-ABS-3, PR-ABS-4 |

Landing order: 0 → 1 → 2 → (3 ‖ 4) → 5.

## Resolved design decisions

| Topic | Decision |
|---|---|
| Serialization format | **Sidecar JSON (stdlib), not msgpack.** Grounding: msgpack is not a dependency and the codebase uses `json` throughout. `absence_vocab` owns load/save so the format is swappable to msgpack later if a perf test demands it. *(Spec said msgpack; corrected after grounding.)* |
| Index storage | Sidecar file `vocab_index.json` under `JAVA_CODEBASE_RAG_INDEX_DIR`, header-stamped with `ontology_version`. Not a GraphMeta property (the n-gram index can be multi-MB for large repos; GraphMeta is read wholesale on every `meta()` call). |
| Did-you-mean metric | A normalized string similarity ∈ [0,1] (Jaro-Winkler family); exact variant chosen in PR-ABS-2 and pinned by the false-absent guard test. `distance = 1 − similarity`. |
| Threshold defaults | `close=0.85`, `absent_floor=0.40`, `candidate_count=5`, `ngram_q=3` (illustrative defaults in spec; finalized/validated by PR-ABS-2 tests). Config-tunable. |
| `resolve` phantoms | `resolve` does not filter `s.resolved` (`resolve_service.py:170-244`); this plan makes phantom matches explicit via `external_identity` rather than adding a filter. |
| Hints coexistence | `absence` and `hints_structured`/`advisories` compose. The subsumed `mcp_hints` branches are left in place for now (no removal in this plan) to avoid a regression surface; a follow-up can retire them. |
| `correct_empty` | Added (4th verdict), generalizing `is_external_entrypoint`. |

---

# PR-ABS-0 — Shared types, `absence` field on 5 models, config knobs

**Goal:** Land the contracts every later PR consumes — the `AbsenceDiagnosis` DTO,
the optional `absence` field on all five MCP output models, and the config
knobs — with no behavior change yet (`absence` stays `None` everywhere).

**Key facts (verified):**
- Output models are independent classes (no shared base); envelope fields
  repeated per model: `mcp_v2.py:509` (`SearchOutput`), `:525` (`FindOutput`),
  `:546` (`DescribeOutput`), `:554` (`NeighborsOutput`),
  `resolve_service.py:121` (`ResolveOutput`).
- `ResolveOutput`/`ResolveCandidate` set `model_config = ConfigDict(extra="forbid")`
  (`resolve_service.py:114`, `:122`) — a *declared* `absence` field is fine.
- Config precedence CLI > env > YAML > default; env prefix
  `JAVA_CODEBASE_RAG_`; pickers `_pick_str`/`_pick_bool` exist, no `_pick_float`
  (`config.py:396`, `:439`); `ResolvedOperatorConfig` is a frozen dataclass
  (`config.py:353`); end-to-end model = `hints_enabled`
  (`config.py:562` → field `:361` → consumer `server.py:854`).
- Existing top-level py-modules are listed in `pyproject.toml` `[tool.setuptools]
  py-modules` (`:70-89`).

## File-by-file changes

### 0.1 `absence_types.py` (new)

Pydantic v2 models. Field names and types are the contract later PRs import.

```
AbsenceVerdict = Literal["refine_query","not_in_project","external_dependency","correct_empty"]
AbsenceCause   = Literal["identifier_miss","nl_miss","filter_miss","external","meaningful_empty"]
ExternalReason = Literal["prefix","phantom","unresolved-call"]

AbsenceProof(BaseModel):          # backs a hard not_in_project
  nearest_distance: float
  symbol_count_scanned: int
  thresholds_applied: dict[str,float]   # {"close":..,"absent_floor":..}
  query_shape: Literal["identifier"]

ExternalIdentity(BaseModel):
  fqn: str
  reason: ExternalReason
  source: str | None = None

VocabularyContext(BaseModel):
  top_modules: list[tuple[str,int]]
  top_microservices: list[tuple[str,int]]
  roles_present: list[tuple[str,int]]
  frequent_name_tokens: list[str]

FilterRelaxationDim(BaseModel):
  dimension: str
  constrained_value: str | None
  matches_under_relaxation: int
  suggested_value: str | None

FilterRelaxation(BaseModel):
  per_dimension: list[FilterRelaxationDim]

AbsenceDiagnosis(BaseModel):
  verdict: AbsenceVerdict
  cause: AbsenceCause
  message: str
  closest_symbols: list[NodeRef] = []            # reuse graph_types.NodeRef
  distances: list[float] = []
  proof: AbsenceProof | None = None
  external_identity: ExternalIdentity | None = None
  vocabulary_context: VocabularyContext | None = None
  filter_relaxation: FilterRelaxation | None = None
```

Import `NodeRef` from `graph_types` (`graph_types.py:28`). Use
`Field(default_factory=list)` for the two list fields (match the repo's pattern
at `mcp_v2.py:511`).

### 0.2 `mcp_v2.py` (modified)

Add one field to each of the four output classes, after `hints_structured`:
`absence: AbsenceDiagnosis | None = None` (`SearchOutput` `:522`,
`FindOutput` `:543`, `DescribeOutput` `:551`, `NeighborsOutput` `:569`). Import
`AbsenceDiagnosis` from `absence_types` near `mcp_v2.py:34`.

### 0.3 `resolve_service.py` (modified)

Add `absence: AbsenceDiagnosis | None = None` to `ResolveOutput`
(`resolve_service.py:131`, after `hints_structured`). Import `AbsenceDiagnosis`
near `resolve_service.py:13`. (Declared field — compatible with `extra="forbid"`.)

### 0.4 `config.py` (modified)

- Add a `_pick_float(env_key, yaml_dict, yaml_path, default) -> tuple[float,
  SettingSource]` helper mirroring `_pick_bool` (`config.py:439`): precedence
  CLI → env (parse via `float(...)`, fall back to default on `ValueError`) → YAML
  path-walk → default.
- Add 5 fields to `ResolvedOperatorConfig` (`config.py:353`) + matching
  `*_source: SettingSource`: `absence_close_threshold: float = 0.85`,
  `absence_absent_floor: float = 0.40`, `absence_candidate_count: int = 5`,
  `absence_ngram_q: int = 3`, `absence_diag_enabled: bool = True`. (Int knobs
  reuse a `_pick_int` added the same way, or `_pick_str`+`int(...)`; pick one and
  be consistent.)
- Read them in `resolve_operator_config` (`config.py:499`) with env names
  `JAVA_CODEBASE_RAG_ABSENCE_CLOSE_THRESHOLD`,
  `_ABSENCE_ABSENT_FLOOR`, `_ABSENCE_CANDIDATE_COUNT`, `_ABSENCE_NGRAM_Q`,
  `_ABSENCE_DIAG_ENABLED`; YAML paths under a new `absence:` section
  (`("absence","close_threshold")`, etc.).

### 0.5 `pyproject.toml` (modified)

Add `absence_types`, `absence_vocab`, `absence_diagnosis` to
`[tool.setuptools] py-modules` (`pyproject.toml:70-89`) so they ship in the
wheel. (Only `absence_types` exists after this PR; list all three now to avoid a
later packaging edit.)

## Tests for PR-ABS-0

`tests/test_absence_types.py` (new):
- `AbsenceDiagnosis` constructs with only `verdict`/`cause`/`message`; the four
  optional payloads default to `None`/`[]`.
- `AbsenceProof`, `ExternalIdentity`, `VocabularyContext`, `FilterRelaxation`
  round-trip via `.model_dump()` → re-parse (pydantic equality).
- Each of the 5 MCP output models accepts `absence=None` (default) and an
  `AbsenceDiagnosis` instance; `.model_dump()["absence"]` reflects it.
- `ResolveOutput.model_dump()` with an `absence` set does not raise under
  `extra="forbid"`.

`tests/test_config.py` (extend, or new if absent):
- `resolve_operator_config(...)` returns the 5 absence fields with defaults when
  no env/YAML is set.
- Setting `JAVA_CODEBASE_RAG_ABSENCE_CLOSE_THRESHOLD=0.9` (monkeypatched env)
  yields `absence_close_threshold == 0.9`; a non-numeric value falls back to the
  default (0.85) without raising.

## Definition of done (PR-ABS-0)
- [ ] `absence_types.py` exists with the DTOs above; `pyproject.toml` lists the 3
  new modules.
- [ ] All 5 output models carry an optional `absence` field; existing tests still
  pass (field is additive).
- [ ] Config knobs readable with defaults; `_pick_float` added.
- [ ] `pytest tests/test_absence_types.py tests/test_config.py -q` green.
- [ ] PR title: `feat(absence): shared diagnosis types, absence field, config knobs`.

## Implementation steps

| # | Step | File(s) | Done when |
|---|---|---|---|
| 1 | Write failing model/contract tests | `tests/test_absence_types.py` | `import absence_types` fails (module missing) |
| 2 | Create `absence_types.py` with the DTOs; add to `pyproject.toml` py-modules | `absence_types.py`, `pyproject.toml` | model tests pass |
| 3 | Write failing config-knob tests | `tests/test_config.py` | tests fail (knobs absent) |
| 4 | Add `_pick_float`/`_pick_int`, 5 fields, env+YAML reads | `config.py` | config tests pass |
| 5 | Add `absence` field + import to the 5 output models | `mcp_v2.py`, `resolve_service.py` | `tests/test_mcp_v2.py` + `tests/test_resolve_service.py` still green (additive) |
| 6 | Run targeted suite; commit | — | green; commit |

---

# PR-ABS-1 — Vocabulary index asset (Layer 1)

**Goal:** A `VocabularyIndex` that can be built from a `LadybugGraph`, persisted
as a versioned sidecar JSON, loaded, and queried for did-you-mean candidates and
external membership. Wired into the graph build so reprocess produces it; a CLI
subcommand rebuilds it standalone.

**Key facts (verified):**
- `LadybugGraph.get(db_path=None)` resolves path via `resolve_ladybug_path`
  (`ladybug_queries.py:370`, `:97`); `_rows(cypher, params)` runs a query
  (`:401`).
- Symbol enumeration query shape (from `find_v2`, `mcp_v2.py:999`):
  `MATCH (s:Symbol) ... RETURN s.id, s.fqn, s.name, s.kind, s.module,
  s.microservice, s.role, s.resolved`. `_SYM_COLS` lists all Symbol columns
  (`ladybug_queries.py:296`).
- `_is_external_fqn(fqn)` + `_EXTERNAL_PREFIXES` (`ladybug_queries.py:267/240`).
- Graph build: `write_ladybug(...)` (`build_ast_graph.py:4168`) calls
  `_write_meta(conn, tables, source_root)` (`:4208`) last, then closes the db.
  The build runs via `run_build_ast_graph` subprocess (`pipeline.py:359`),
  invoked by `cli.py:_cmd_reprocess` (`:516`).
- Index dir: `JAVA_CODEBASE_RAG_INDEX_DIR` (default `./.java-codebase-rag`);
  graph db filename `code_graph.lbug`.
- `pyproject.toml` has no `msgpack`; stdlib `json` is the convention.

## File-by-file changes

### 1.1 `absence_vocab.py` (new)

A `VocabularyIndex` class (no pydantic; plain class + module functions for
load/save to keep the hot path light). Contracts:

```
@dataclass SymbolRecord:
  node_id: str; fqn: str; simple_name: str; normalized_name: str
  kind: str; module: str|None; microservice: str|None; role: str|None
  resolved: bool

class VocabularyIndex:
  def __init__(self, records: list[SymbolRecord], ngram_index: dict[str,list[int]], q: int): ...
  @classmethod
  def build(cls, graph: LadybugGraph, *, q: int) -> "VocabularyIndex": ...
  def save(self, path: Path, *, ontology_version: int) -> None: ...
  @classmethod
  def load(cls, path: Path) -> "VocabularyIndex": ...   # raises VocabIndexStale if header ontology_version != graph's
  def lookup(self, name: str, *, limit: int) -> list[tuple[SymbolRecord,float]]: ...  # ranked (record, similarity)
  def is_external(self, name: str) -> tuple[bool, str|None]: ...   # (is_ext, reason) via prefix/phantom
  @property
  def symbol_count(self) -> int: ...
```

Behavior (no code in the plan — described):
- `build`: enumerate all `Symbol` nodes via one `MATCH (s:Symbol) RETURN ...`
  (columns above); build `SymbolRecord` list (`normalized_name` = lowercased
  `simple_name` with generics/`#method`/signature stripped); build the q-gram
  inverted index `dict[gram -> [record_idx]]` from each `normalized_name`'s q-grams.
- `save`: write JSON `{"format_version":1,"ontology_version":<ov>,
  "built_at":<int>,"symbol_count":<n>,"q":<q>,"records":[...],"ngrams":{...}}`
  to `path`.
- `load`: parse JSON; if `ontology_version` != expected, raise a
  `VocabIndexStale` exception (caller triggers rebuild).
- `lookup`: take the query name's q-grams → union candidate record indexes →
  compute similarity (delegate the metric to `absence_diagnosis`; here return the
  candidate records unranked or with a placeholder — **see Interfaces**) → return
  top-`limit` `(record, similarity)`. To avoid a circular import, `lookup`
  returns candidate records; **ranking by similarity is done in
  `absence_diagnosis`** (PR-ABS-2). Document this split.
- `is_external`: match `name` against records; if a record exists with
  `resolved=False` → `(True, "phantom")`; else if `_is_external_fqn(fqn)` on a
  constructed/known fqn → `(True, "prefix")`; else `(False, None)`.

Sidecar path constant: `VOCAB_INDEX_FILENAME = "vocab_index.json"` (lives next to
`code_graph.lbug` under the index dir).

### 1.2 `build_ast_graph.py` (modified)

In `write_ladybug` (`build_ast_graph.py:4168`), after the `_write_meta(...)`
call (`:4208`) and before `conn.close()`, build and persist the vocab index:
instantiate `VocabularyIndex.build(graph, q=cfg.ngram_q)` (the build uses a fresh
read-only `LadybugGraph` over the just-written db, or reuses the in-memory
`tables` — pick the `LadybugGraph` path for simplicity) and `.save(sidecar_path,
ontology_version=ONTOLOGY_VERSION)`. Wrap in try/except: a build failure must not
fail the graph build (log + continue; the diagnosis layer will lazy-rebuild).

### 1.3 `jrag.py` (modified) — standalone rebuild subcommand

Add a `jrag vocab-index` subcommand (next to existing admin-style commands; the
CLI dispatch is in `java_codebase_rag/jrag.py` around `:466-1102`). Behavior:
resolve the index dir + graph via `resolve_operator_config`, call
`VocabularyIndex.build(...).save(...)`, print `symbol_count` + path. (For
backfill/repair without a full reprocess.)

### 1.4 Lazy-load helper (in `absence_vocab.py`)

`get_vocabulary_index(graph, cfg) -> VocabularyIndex`: module-level cached
singleton (keyed by graph db path). Tries `VocabularyIndex.load(sidecar)`; on
`VocabIndexStale`/`FileNotFoundError`/exception, calls `build(...)` from the
graph, `.save(...)` (best-effort), caches, returns. This is the single entry
point the diagnosis layer (PR-ABS-2) and tools (PR-ABS-3) use.

## Interfaces

- **Consumes (from PR-ABS-0):** `cfg.absence_ngram_q`; `LadybugGraph` API
  (`get`, `_rows`, `_is_external_fqn`); `JAVA_CODEBASE_RAG_INDEX_DIR`.
- **Produces (for PR-ABS-2/3):**
  - `VocabularyIndex.lookup(name, limit) -> list[SymbolRecord]` (candidates; ranking in PR-ABS-2).
  - `VocabularyIndex.is_external(name) -> tuple[bool, str|None]`.
  - `VocabularyIndex.symbol_count -> int`.
  - `get_vocabulary_index(graph, cfg) -> VocabularyIndex` (cached, lazy-backfill).
  - `SymbolRecord` dataclass fields (above).
  - Sidecar schema (JSON shape above) — the persistence contract.

## Tests for PR-ABS-1

`tests/test_absence_vocab.py` (new), using the `ladybug_graph`/`ladybug_db_path`
session fixtures (`tests/conftest.py`):
- `VocabularyIndex.build(graph, q=3)` returns an index whose `symbol_count` ≥ the
  graph's class-symbol count (assert ≥1 for `tests/bank-chat-system`).
- `save` then `load` round-trips: `symbol_count` equal; a known symbol's
  `simple_name` present in records.
- `load` on a sidecar whose header `ontology_version` differs from the graph's
  raises `VocabIndexStale`.
- `lookup("ChatService", limit=5)` (a real name in the corpus) returns that
  record among candidates; `lookup("ChatService")` for a typoed
  `"ChatServic"` still returns `ChatService` among candidates (n-gram recall).
- `is_external` on an external-prefix fqn (e.g. `"java.util.List"`) → `(True,
  "prefix")`; on a phantom present in the corpus → `(True, "phantom")`; on a
  real project symbol → `(False, None)`.
- `get_vocabulary_index`: first call with no sidecar builds + saves; second call
  loads from file (assert build runs once — monkeypatch/spy on `build`).
- Build-failure resilience: monkeypatch `build` to raise → `write_ladybug` still
  succeeds (graph written; sidecar absent).

## Definition of done (PR-ABS-1)
- [ ] `absence_vocab.py` with `VocabularyIndex` + `get_vocabulary_index`;
  `pyproject.toml` lists it (done in PR-ABS-0).
- [ ] `write_ladybug` writes `vocab_index.json`; failure doesn't break the build.
- [ ] `jrag vocab-index` rebuilds standalone.
- [ ] `pytest tests/test_absence_vocab.py -q` green.
- [ ] PR title: `feat(absence): precomputed vocabulary index asset + build hook`.

## Implementation steps

| # | Step | File(s) | Done when |
|---|---|---|---|
| 1 | Write failing vocab tests (build/round-trip/stale/lookup/external) | `tests/test_absence_vocab.py` | import fails |
| 2 | Implement `SymbolRecord`, `VocabularyIndex.build/save/load/lookup/is_external` | `absence_vocab.py` | round-trip + stale + lookup tests pass |
| 3 | Implement `get_vocabulary_index` lazy/backfill | `absence_vocab.py` | get/build-once test passes |
| 4 | Hook build into `write_ladybug` (try/except, no-fail) | `build_ast_graph.py` | reprocess produces sidecar; failure-isolated test passes |
| 5 | Add `jrag vocab-index` subcommand | `jrag.py` | manual: `jrag vocab-index` prints count + path |
| 6 | Run targeted suite; commit | — | green; commit |

---

# PR-ABS-2 — `diagnose(...)` module (Layer 2)

**Goal:** The stateless classifier + per-cause help assembler. Pure function of
its inputs (incl. the `VocabularyIndex`); the single place absence logic lives.

**Key facts (verified):**
- `_find_has_identifier_shaped_filter(kind, flt)` (`mcp_hints.py:280`) — identifier
  filter keys per kind: symbol→`fqn_contains`, route→`path_contains`,
  client→`target_service`/`target_path_contains` (`mcp_hints.py:152`).
- `module_counts()`/`microservice_counts()` (`ladybug_queries.py:987`) →
  `{name: resolved-type count}` (resolved types only).
- `NodeRef` fields (`graph_types.py:28`): id/kind/fqn/name/symbol_kind/
  microservice/module/role.
- `_zero_result_guidance(args, graph) -> str|None` (`jrag.py:4139`) — single-dim
  filter relaxation; emits a human string. Port its *logic* (probe unfiltered,
  tally the dim, top-3 alternatives, suggest most-common) into the structured
  `FilterRelaxation` payload, parameterized on `(filter_dims, graph)` instead of
  `argparse.Namespace`.

## File-by-file changes

### 2.1 `absence_diagnosis.py` (new)

```
def diagnose(
    *,
    tool: Literal["search","find","neighbors","describe","resolve"],
    query: str | None,
    filt: dict | None,                 # find's model_dump'd filter
    filter_kind: str | None,           # find's kind, for identifier-shape test
    root_node: NodeRef | None,         # neighbors/describe subject
    scope: dict[str,str],              # {"microservice":..,"module":..}
    vocab: VocabularyIndex,
    graph: LadybugGraph,
    cfg,                               # ResolvedOperatorConfig (thresholds)
) -> AbsenceDiagnosis | None: ...     # None on master-toggle off / unrecoverable error
```

Behavior (decision procedure — design, not code):
- If `not cfg.absence_diag_enabled`: return `None`.
- **External-wins:** if `query`/`filt`/`root_node` yields an external target
  (`vocab.is_external(...)` or `_is_external_fqn` on `root_node.fqn` or a phantom
  match), return `verdict="external_dependency"`, `cause="external"`,
  `external_identity={fqn, reason, source}`, `message` stating "referenced, not
  defined in this project."
- **root_node present (neighbors):** if `root_node` external → `external` (above,
  already handled). Else if the zero is meaningful (leaf / external entrypoint —
  reuse the conditions behind `is_external_entrypoint`, `jrag_envelope.py:116` /
  `jrag.py:2393`) → `correct_empty`/`meaningful_empty`. Else (inapplicable edge
  type/direction) → `refine_query` with a message pointing at
  `describe.edge_summary`.
- **query present (search/resolve/describe-by-fqn):** classify identifier-shape
  (CamelCase token / dotted FQN / `Cls#method`, no spaces/stopwords — extend the
  heuristic from `_find_has_identifier_shaped_filter`'s spirit). For
  `describe`-by-`node_id` (not fqn) → `refine_query`, no did-you-mean.
  - Identifier-shaped → run did-you-mean (below).
  - NL → `nl_miss`: assemble `vocabulary_context` from `module_counts`/
    `microservice_counts` + role tally + frequent name tokens; `verdict="refine_query"`.
- **filt present (find):** if identifier-shaped (`_find_has_identifier_shaped_filter`)
  → run did-you-mean on the identifier value; if a close hit exists it's
  `filter_miss` with `filter_relaxation` showing where it lives, else
  `identifier_miss`. If broad/non-identifier → `filter_miss` with `filter_relaxation`.

**Did-you-mean (identifier case):**
- `candidates = vocab.lookup(identifier, limit=cfg.absence_candidate_count)` →
  rank by normalized string similarity ∈ [0,1] (Jaro-Winkler family; exact variant
  pinned by tests) → top N `(SymbolRecord, similarity)`.
- Map to `closest_symbols: list[NodeRef]` (build `NodeRef` from each record) and
  parallel `distances = [1-sim for ...]`.
- Verdict: best similarity ≥ `cfg.absence_close_threshold` → `refine_query`;
  best < `cfg.absence_absent_floor` AND identifier-shaped → `not_in_project` with
  `proof={nearest_distance, symbol_count_scanned=vocab.symbol_count,
  thresholds_applied, query_shape="identifier"}`; middle band → `refine_query`.
- Always populate `closest_symbols`/`distances` (even for `not_in_project` — the
  proof + nearest names).

**Filter relaxation (filter_miss):** port `_zero_result_guidance`'s logic into
`FilterRelaxation.per_dimension`: for each constrained filter dimension present
(role/module/microservice/fqn_contains/...), probe `find_v2`/`search_v2` with
that dimension relaxed, tally where matches live, set `matches_under_relaxation`
+ `suggested_value` (most-common bucket). Return the structured payload.

**Robustness:** the whole function is wrapped so any exception → log + return a
minimal `refine_query` `AbsenceDiagnosis` (or `None` if even that can't be built).

## Interfaces

- **Consumes:** `AbsenceDiagnosis` + sub-DTOs (PR-ABS-0); `VocabularyIndex` +
  `SymbolRecord` + `get_vocabulary_index` (PR-ABS-1); `NodeRef` (`graph_types`);
  `_find_has_identifier_shaped_filter` (`mcp_hints.py:280`); `_is_external_fqn`
  (`ladybug_queries.py:267`); `module_counts`/`microservice_counts`
  (`ladybug_queries.py:987`); config thresholds (PR-ABS-0).
- **Produces (for PR-ABS-3/4):** `diagnose(...) -> AbsenceDiagnosis | None` with
  the exact signature above. This is the single call site each tool makes.

## Tests for PR-ABS-2

`tests/test_absence_diagnosis.py` (new). Mirror `tests/test_mcp_hints.py`'s
pure-generator style — call `diagnose(...)` with a real `ladybug_graph` fixture +
its `VocabularyIndex` (synthetic where possible, graph-backed where needed):

Unit matrix (cause × verdict), each naming scenario + expected result:
- identifier, known typo (e.g. `"ChatServic"` vs corpus `ChatService`) →
  `refine_query`, `closest_symbols` non-empty, `ChatService` present, best
  distance small.
- identifier, nothing close (e.g. `"zzzNoSuchClass123"`) → `not_in_project`,
  `proof` populated, `closest_symbols` still returned (nearest-by-name), best
  distance ≥ `absent_floor`.
- middle-band name (a plausible-but-absent identifier) → `refine_query` (NOT
  `not_in_project`) — **the false-absent guard.**
- **False-absent guard (explicit):** a symbol that *exists* under an unusual name
  must never yield `not_in_project` when queried exactly.
- NL query (`"how does chat routing work"`) → `nl_miss`, `vocabulary_context`
  populated with the corpus's top modules/services, NO `closest_symbols`.
- find, identifier-shaped filter excluded by another dim → `filter_miss`,
  `filter_relaxation.per_dimension` non-empty.
- find, broad filter (e.g. `role=REPOSITORY` absent) → `filter_miss`.
- external target (`"java.util.List"`) → `external_dependency`, `external_identity.reason=="prefix"`.
- phantom target present in corpus → `reason=="phantom"`.
- neighbors of a leaf/entrypoint → `correct_empty`.
- neighbors, wrong edge type → `refine_query`.
- master toggle off (`cfg.absence_diag_enabled=False`) → returns `None`.
- exception path (monkeypatch `vocab.lookup` to raise) → returns a `refine_query`
  (no exception escapes).

## Definition of done (PR-ABS-2)
- [ ] `absence_diagnosis.py` with `diagnose(...)`; full unit matrix green.
- [ ] Conservative threshold + false-absent guard validated.
- [ ] `pytest tests/test_absence_diagnosis.py -q` green.
- [ ] PR title: `feat(absence): diagnosis module — classifier + per-cause help`.

## Implementation steps

| # | Step | File(s) | Done when |
|---|---|---|---|
| 1 | Write failing unit-matrix tests | `tests/test_absence_diagnosis.py` | import fails |
| 2 | Implement identifier-shape classifier + did-you-mean + thresholds | `absence_diagnosis.py` | identifier tests + false-absent guard pass |
| 3 | Implement external-wins + neighbors branches | `absence_diagnosis.py` | external/neighbors tests pass |
| 4 | Implement nl_miss (vocabulary_context) + filter_miss (port relaxation) | `absence_diagnosis.py` | nl/filter tests pass |
| 5 | Add master-toggle + exception guard | `absence_diagnosis.py` | toggle + exception tests pass |
| 6 | Run targeted suite; commit | — | green; commit |

---

# PR-ABS-3 — MCP tool integration (Layer 3, MCP)

**Goal:** Wire `diagnose(...)` into the five empty paths and attach the result to
each output's `absence` field. No change to non-empty results.

**Key facts (verified):**
- `search` empty flows through the success return at `mcp_v2.py:953-967`
  (`hits==[]`); the `LadybugGraph` handle is the `graph` param (`:830`) — no local
  `g`; in scope: `query`, `table`, `nf`, `limit`, `offset`.
- `find` empty at `mcp_v2.py:1052-1061` (`refs==[]`); local `g = graph or
  LadybugGraph.get()` (`:980`); in scope: `kind`, `nf`, `filter_dump`
  (`nf.model_dump(exclude_none=True)`, `:1037`).
- `describe` two not-found returns: fqn `mcp_v2.py:1096`, node_id `:1108`;
  local `g` (`:1079`); in scope: `fqn`, `id`, `has_fqn`, `has_id`.
- `neighbors` empty at `mcp_v2.py:1638-1646` (`sliced==[]`); local `g` (`:1388`);
  in scope: `first_origin`, `origin_kind`, `subject_record`, `requested_edge_types`.
- `resolve` none at `resolve_service.py:570-578`; in scope: `trimmed`, `hint_kind`;
  outer `resolve_v2` (`:612`) has `microservice`, `module`, `graph`.

## File-by-file changes

### 3.1 `mcp_v2.py` (modified) — search/find/describe/neighbors

For each empty path, when the result list is empty (`not hits` / `not refs` /
`not sliced`) or at the not-found returns, build `AbsenceDiagnosis` via
`absence_diagnosis.diagnose(...)` and pass `absence=diag` into the output
constructor. Inputs per tool:
- **search:** `tool="search"`, `query=query`, `filt=None`, `root_node=None`,
  `scope={}`, `vocab=get_vocabulary_index(graph, cfg)`, `graph=graph or
  LadybugGraph.get()`, `cfg`. (Search must resolve a graph handle since it has no
  local `g`.)
- **find:** `tool="find"`, `query=None`, `filt=filter_dump`, `filter_kind=kind`,
  `root_node=None`, `graph=g`, ...
- **describe:** fqn path → `tool="describe"`, `query=fqn`; node_id path →
  `tool="describe"`, `query=None` (→ minimal `refine_query`).
- **neighbors:** `tool="neighbors"`, `root_node=<NodeRef from first_origin +
  subject_record>` (build a `NodeRef` from the in-scope origin data), `query=None`.

`cfg` must be reachable in `mcp_v2` — thread it from `server.create_mcp_server`
(where `set_hints_enabled(cfg.hints_enabled)` is already called, `server.py:854`)
into the tool functions, or read via a module-level setter mirroring
`set_hints_enabled`. Pick the setter pattern (least churn): add
`set_absence_config(cfg)` in `mcp_v2`, called from `server.py:854`.

Keep the existing `_hints_or_skip`/`hints_structured` calls untouched (compose).

### 3.2 `resolve_service.py` (modified) — resolve none

In `_resolve_finalize_success` (`resolve_service.py:565`), the `not matches`
branch (`:570`): before/after building the `ResolveOutput`, call `diagnose(...)`
with `tool="resolve"`, `query=trimmed`, `hint_kind` mapped to `filter_kind` where
relevant, `graph` from the outer `resolve_v2` (`:612`), and attach via the
existing `model_copy(update=...)` at `:603-607` (add `"absence": diag`).

## Interfaces

- **Consumes:** `diagnose(...)` (PR-ABS-2); `get_vocabulary_index` (PR-ABS-1);
  `cfg` thresholds (PR-ABS-0); the `absence` field (PR-ABS-0).
- **Produces:** MCP outputs whose `absence` field is populated on empty paths.

## Tests for PR-ABS-3

`tests/test_absence_mcp_integration.py` (new), graph-backed via `ladybug_graph`
(direct tool calls, mirroring `tests/test_mcp_v2.py`):
- `search_v2("zzzNoSuchClass123", graph=...)` → `out.absence.verdict ==
  "not_in_project"`, `out.absence.proof` populated.
- `search_v2("ChatServic", graph=...)` (typo) → `out.absence.verdict ==
  "refine_query"`, `ChatService` in `out.absence.closest_symbols`.
- `search_v2("java.util.List", ...)` → `out.absence.verdict ==
  "external_dependency"`.
- `find_v2(kind="symbol", filter={"fqn_contains":"zzzNoMatch"}, graph=...)` →
  `out.absence` populated (`identifier_miss` or `filter_miss`).
- `describe_v2(fqn="com.no.such.Type", graph=...)` → `out.success is False` AND
  `out.absence.verdict == "not_in_project"` (did-you-mean attached to the failure).
- `neighbors_v2(<leaf id>, ["CALLS"], graph=...)` → `out.absence.vercord ==
  "correct_empty"`.
- Non-empty results: `search_v2("ChatService", ...)` with hits →
  `out.absence is None`.
- `resolve_v2("zzzNoSuch", graph=...)` → `out.status=="none"` AND
  `out.absence.verdict == "not_in_project"`.

## Definition of done (PR-ABS-3)
- [ ] All 5 MCP empty paths attach `absence`; non-empty results unaffected.
- [ ] `tests/test_mcp_v2.py` + `tests/test_resolve_service.py` still green.
- [ ] `pytest tests/test_absence_mcp_integration.py -q` green.
- [ ] PR title: `feat(absence): wire diagnosis into the 5 MCP empty paths`.

## Implementation steps

| # | Step | File(s) | Done when |
|---|---|---|---|
| 1 | Write failing integration tests (5 tools) | `tests/test_absence_mcp_integration.py` | tests fail (absence stays None) |
| 2 | Add `set_absence_config` + thread cfg from `server.py:854` | `mcp_v2.py`, `server.py` | cfg reachable |
| 3 | Wire search + find empty paths | `mcp_v2.py` | search/find integration tests pass |
| 4 | Wire describe (both returns) + neighbors | `mcp_v2.py` | describe/neighbors tests pass |
| 5 | Wire resolve none | `resolve_service.py` | resolve test passes |
| 6 | Run targeted suite; commit | — | green; commit |

---

# PR-ABS-4 — CLI envelope alignment + renderer (Layer 3, CLI)

**Goal:** The `jrag` CLI speaks the same absence vocabulary — `Envelope.absence`
serialized, resolve-none carries it, and the renderer maps verdicts to text
(generalizing `is_external_entrypoint` under `correct_empty`).

**Key facts (verified):**
- `Envelope` is a `@dataclass` (`jrag_envelope.py:86-120`); fields incl. `status`,
  `is_external_entrypoint`, `message`; `to_dict()` (`:122`) omits empty
  optionals, emits `is_external_entrypoint` only when truthy (`:152`); `to_json`/
  `_to_idfree_dict` (`:156`).
- `EnvelopeStatus = Literal["ok","ambiguous","not_found","error"]` (`:39`).
- resolve-none → `not_found` at `jrag_envelope.py:606-613` (returns
  `Envelope(status="not_found", message=...)`).
- Renderer: `_render_not_found` (`jrag_render.py:227`); traversal empty block
  with `is_external_entrypoint` text (`:407-426`); listing empty (`:650-653`).

## File-by-file changes

### 4.1 `java_codebase_rag/jrag_envelope.py` (modified)
- Add `absence: AbsenceDiagnosis | None = None` to `Envelope` (`:120`).
- In `to_dict()` (`:122`) and `_to_idfree_dict`/`to_json`: emit `absence` (as
  `.model_dump()` when present, omit when `None`), matching the
  `is_external_entrypoint` truthy-omit pattern.
- resolve-none conversion (`:606-613`): pull `out.absence` from the `ResolveOutput`
  onto the returned `Envelope(...)`.

### 4.2 `java_codebase_rag/jrag_render.py` (modified)
- Extend `_render_not_found` (`:227`): when `envelope.absence` is present, append
  the verdict + `message` (+ a compact "did-you-mean: a, b, c" line when
  `closest_symbols` is non-empty).
- Traversal empty block (`:407-426`): the `is_external_entrypoint` branch now also
  covers `absence.verdict == "correct_empty"` (same "external entrypoint — no
  in-repo callers" text, or a `correct_empty` message). Map other verdicts to a
  short line (`not_in_project`/`external_dependency`/`refine_query`) above the
  existing zero-line.
- Listing empty (`:650-653`): when `absence` present, render the verdict line
  before/instead of the bare `0 <noun>`.

## Interfaces

- **Consumes:** `AbsenceDiagnosis` (PR-ABS-0); the MCP outputs' `absence` (PR-ABS-3).
- **Produces:** CLI text + JSON reflecting absence verdicts.

## Tests for PR-ABS-4

`tests/test_jrag_envelope_absence.py` (new):
- `Envelope(status="not_found", absence=<diag>).to_dict()` includes `absence`
  with the verdict; `Envelope(status="ok")` omits it.
- `_render_not_found` for a `not_in_project` envelope prints the verdict + a
  did-you-mean line when `closest_symbols` non-empty.
- `is_external_entrypoint=True` and `absence.verdict=="correct_empty"` render the
  same "external entrypoint" text (no regression).
- resolve `status="none"` (with `out.absence`) → envelope carries `absence`,
  rendered as not-found + verdict.

## Definition of done (PR-ABS-4)
- [ ] `Envelope.absence` serialized; renderer maps all 4 verdicts.
- [ ] Existing CLI render tests still green; `is_external_entrypoint` unchanged.
- [ ] `pytest tests/test_jrag_envelope_absence.py -q` green.
- [ ] PR title: `feat(absence): CLI envelope + renderer alignment`.

## Implementation steps

| # | Step | File(s) | Done when |
|---|---|---|---|
| 1 | Write failing envelope/render tests | `tests/test_jrag_envelope_absence.py` | tests fail |
| 2 | Add `absence` field + serialization | `jrag_envelope.py` | to_dict test passes |
| 3 | Thread resolve-none `out.absence` into Envelope | `jrag_envelope.py:606` | resolve-none test passes |
| 4 | Extend renderer (not-found, traversal, listing) | `jrag_render.py` | render tests pass |
| 5 | Run targeted suite; commit | — | green; commit |

---

# PR-ABS-5 — Docs

**Goal:** Operators and agents know the verdicts and how to read `absence`; the
new knobs are documented.

## File-by-file changes

### 5.1 `docs/AGENT-GUIDE.md`
- Replace/augment the "Empty `search`/`neighbors`" rows in the recovery playbook
  (`:263-268`) with the verdict vocabulary: read `absence.verdict` first
  (`not_in_project` → stop; `external_dependency` → it's a dep; `refine_query` →
  use `closest_symbols`/`vocabulary_context`/`filter_relaxation`; `correct_empty`
  → the zero is right).
- Update the proof-of-absence caveat (`:29`) to point at `absence.proof` as the
  auditable signal.

### 5.2 `docs/CONFIGURATION.md`
- Document the 5 `absence_*` env vars + the `absence:` YAML section, with
  defaults and the "raise `absence_absent_floor` to be more conservative" guidance.

## Definition of done (PR-ABS-5)
- [ ] AGENT-GUIDE recovery playbook + proof-of-absence updated.
- [ ] CONFIGURATION documents the 5 knobs.
- [ ] PR title: `docs(absence): verdict vocabulary + config knobs`.

## Implementation steps

| # | Step | File(s) | Done when |
|---|---|---|---|
| 1 | Update AGENT-GUIDE recovery playbook + proof-of-absence | `docs/AGENT-GUIDE.md` | verdicts documented |
| 2 | Document 5 config knobs | `docs/CONFIGURATION.md` | knobs + defaults present |
| 3 | Commit | — | commit |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Sidecar JSON grows large on enterprise repos; slow load | Med | `VocabularyIndex` owns load/save (swap to msgpack later); perf-guard test in PR-ABS-1; lazy build is cached per process |
| 2 | `diagnose(...)` adds latency to every empty | Med | Empty results are the minority path; index lookup is bounded; master toggle `absence_diag_enabled` + exception guard |
| 3 | `resolve` `extra="forbid"` rejects undeclared fields | Low | `absence` is a declared field (PR-ABS-0); covered by a model-dump test |
| 4 | `search` has no local graph handle | Low | Resolve `graph or LadybugGraph.get()` in the empty path (PR-ABS-3) |
| 5 | False-absent on unusual naming | High | Conservative two-band threshold + explicit false-absent guard test (PR-ABS-2); defaults tunable via config |
| 6 | Build hook breaks reprocess | High | try/except around the vocab build in `write_ladybug`; failure-isolated test (PR-ABS-1) |
| 7 | Subsumed `mcp_hints` branches drift from `absence` | Low | Left in place this plan (compose); follow-up retires them |

# Out of scope

- Non-Java tables (`sql`, `yaml`) absence diagnosis.
- Removing/retiring the subsumed `mcp_hints` branches.
- Switching the index format to msgpack or adding compression.
- A new MCP tool (membership is surfaced on existing tools + `resolve`).

# Whole-plan done definition

1. All 5 MCP outputs + CLI `Envelope` carry a populated `absence` on empty paths;
   non-empty results unaffected.
2. `absence_diag_enabled=False` restores pre-feature behavior (all `absence=None`).
3. The false-absent guard test passes (no real symbol is ever declared absent).
4. `vocab_index.json` is produced by reprocess and by `jrag vocab-index`; missing
   or stale sidecar is lazily rebuilt without error.
5. Docs updated; full test suite green (run once at task end per `AGENTS.md`).

# Verification (end-to-end, on `tests/bank-chat-system`)

```bash
.venv/bin/pip install -e .
rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}
# Rebuild the graph + vocab index for the fixture, then exercise the verdicts:
JAVA_CODEBASE_RAG_INDEX_DIR=tests/bank-chat-system/.java-codebase-rag .venv/bin/jrag status
.venv/bin/jrag search zzzNoSuchClass123        # expect: not_in_project + proof
.venv/bin/jrag search ChatServic               # expect: refine_query + did-you-mean -> ChatService
.venv/bin/jrag search "java.util.List"         # expect: external_dependency
.venv/bin/jrag find --kind symbol --fqn-contains zzzNoMatch   # expect: absence populated
# Toggle off -> pre-feature behavior:
JAVA_CODEBASE_RAG_ABSENCE_DIAG_ENABLED=false .venv/bin/jrag search zzzNoSuchClass123  # absence absent
# Full suite at the end:
.venv/bin/python -m pytest -q
```

# Tracking

- PR-ABS-0 — _pending_
- PR-ABS-1 — _pending_
- PR-ABS-2 — _pending_
- PR-ABS-3 — _pending_
- PR-ABS-4 — _pending_
- PR-ABS-5 — _pending_

# Notes

- Serialization deviation from the spec (msgpack → stdlib JSON) is documented in
  Resolved decisions; the format is encapsulated behind `absence_vocab`'s
  load/save so it is swappable.
- The `diagnose(...)` ranking metric is left to a Jaro-Winkler-family variant
  pinned by the PR-ABS-2 tests (the spec defers the exact metric to the plan;
  this plan defers the exact variant to the tests, which is the contract that
  matters).
