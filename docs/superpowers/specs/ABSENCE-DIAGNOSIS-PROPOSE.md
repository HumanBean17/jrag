<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Proposal: Absence Diagnosis — disambiguating empty exploration results

Status: **active (design)**. Grounded against current source (2026-07-08); every
backend symbol/line range cited is verified against `master` (`168e31a`). This is
a **design spec** — it describes WHAT to build and WHY, with references to
classes, methods, fields, configs, DTOs, and contracts. It contains no
implementation logic; method bodies and algorithms belong to the implementing
plan (`plans/active/PLAN-ABSENCE-DIAGNOSIS.md`, produced next).

Implements the design agreed in the brainstorming session; see "Decisions log".

---

## TLDR

Today an empty result from `search` / `find` / `neighbors` is a bare
`results=[]` with **no metadata** (`mcp_v2.py:944`, `:1036`, `:1639`). That
emptiness is ambiguous: it can mean *"your query is bad — refine"* **or** *"this
symbol genuinely is not part of this project — stop."* Agents routinely lock onto
the first interpretation when the second is true, and loop.

This proposal adds an **absence-diagnosis engine**. Every exploration tool's
empty result carries:

- a **verdict** — `refine_query` | `not_in_project` | `external_dependency` |
  `correct_empty`;
- a **cause** — `identifier_miss` | `nl_miss` | `filter_miss` | `external` |
  `meaningful_empty`; and
- **semantically-appropriate help** for that cause — did-you-mean candidates,
  project vocabulary context, filter relaxation, or an external identity.

It is backed by a **precomputed, versioned vocabulary index** derived from the
graph's `Symbol` nodes, so "does this name exist / what's closest / is it
external" is a bounded-time lookup, not a per-empty scan.

---

## Problem

An agent exploring a codebase via the MCP server or `jrag` CLI often searches for
a class or method that **does not exist in this project** — it was assumed,
misremembered, hallucinated, or carried over from another repo. The empty result
it gets back is indistinguishable from "bad query," so the agent keeps refining
instead of stopping. The two readings are conflated because the empty result
carries no distinguishing signal.

### What exists today (the gap)

- `search` / `find` / `neighbors` return `success=True, results=[]` with **no
  per-empty metadata** — no `total_hits`, no suggestions, no "is this in the
  project" field. Only `advisories` + `hints_structured`, and the relevant
  existing hints all assume the symbol *might* exist and say "keep looking"
  (`mcp_hints.py:541-565`, `:593-605`, `:294-366`).
- Only `resolve` has a first-class "nothing matched" status (`status="none"`,
  `resolve_service.py:570`); only `describe` reports absence as a failure
  (`success=False`, `mcp_v2.py:1096`).
- **No fuzzy / did-you-mean / edit-distance logic exists anywhere** in the
  codebase. Only prefix/suffix/CONTAINS ladders in `resolve_service.py:170-244`,
  and the word "fuzzy" is overloaded to mean graph-edge-resolution-strategy
  (`FUZZY_STRATEGY_SET`, `java_ontology.py:106`) or "use `search`".
- The agent docs frame empties as "keep looking" (`docs/AGENT-GUIDE.md` recovery
  playbook, `:263-268`) or "maybe not indexed" (proof-of-absence caveat, `:29`) —
  nothing says "stop, this genuinely is not here."

### What already exists (the building blocks)

- The graph **is** the project's symbol vocabulary: `Symbol` nodes
  (`_SYM_COLS`, `ladybug_queries.py:296-300`) with `name`, `fqn`, `kind`,
  `module`, `microservice`, `role`, `resolved`.
- **Project-vs-external is already tracked**, just not exposed to the agent:
  - `Symbol.resolved: bool` — `false` for **phantoms** (types referenced but
    never parsed from source: JDK/Spring/library; `build_ast_graph.py:1189`,
    `graph_enrich.py:1689`).
  - `_EXTERNAL_PREFIXES` — hardcoded allowlist `java.`, `javax.`, `jakarta.`,
    `org.springframework.`, `lombok.` (`ladybug_queries.py:240`), consulted only
    internally for traversal (`:1226`, `:1353`, `:1528`) via `_is_external_fqn`
    (`:267`).
  - CALLS-edge `attrs.resolved` / HTTP-ASYNC `attrs.match`
    (`mcp_v2.py:1157-1159`).
- **`ontology_version`** is a real version key: an `INT64` on the Kùzu
  `GraphMeta` node (`build_ast_graph.py:2916`, set `:3704`, read `:3811`), with a
  Python `ONTOLOGY_VERSION` constant (`java_index_flow_lancedb.py:464`) and
  graph-side version guards (`ladybug_queries.py:377`). This is the natural
  invalidation key for a derived index.
- All artifacts persist under **`JAVA_CODEBASE_RAG_INDEX_DIR`** (default
  `./.java-codebase-rag`): Lance tables + Kùzu + cocoindex state
  (`java_index_flow_lancedb.py:7`, `:311-317`). A sidecar index has an obvious
  home.
- **The CLI already prototypes the disambiguation pattern** — `Envelope.status`
  incl. `"not_found"` (`jrag_envelope.py:39`, render `jrag_render.py:227`),
  `is_external_entrypoint` ("this zero is correct"; `jrag_envelope.py:116`, set
  `jrag.py:2393`), and `_zero_result_guidance` (relax each filter dimension,
  report where matches live; `jrag.py:4139`). The MCP layer never got this
  treatment; this proposal unifies both under one vocabulary.

---

## Goals

1. Every exploration-tool empty result carries an unambiguous verdict + cause +
   cause-appropriate help.
2. Distinguish **absent** (name nowhere in the project) from **external**
   (referenced-but-undefined) from **refine** (near-match / reformulation
   needed).
3. Did-you-mean that lets an agent **pivot in one shot** (no second guessing
   loop), with a **conservative** absence threshold (false-absent is the
   catastrophic failure mode).
4. A precomputed, versioned vocabulary index so the above is bounded-time and
   scales to enterprise codebases.
5. One absence vocabulary across **both** MCP and CLI, and across **all five**
   exploration tools.

## Non-goals

- Changing `search` ranking or the semantic retrieval path.
- Adding new exploration tools beyond surfacing absence on the existing five.
- Detecting absence for non-Java tables (`sql`, `yaml`) in this revision —
  scoped to the Java symbol vocabulary. (Extensible later.)
- Refusing queries or otherwise coercing agent behavior — the tool **informs**;
  the agent decides.

---

## The absence vocabulary (contract)

Every empty result carries one `AbsenceDiagnosis`. Two classifiers plus a set of
optional help payloads; only the payload matching the cause is populated.

### Verdict — what the agent should do

```
AbsenceVerdict = Literal[
  "refine_query",        # empty, but help is offered — keep looking (with help)
  "not_in_project",      # identifier-shaped, no near-match within threshold — stop
  "external_dependency", # target is referenced-but-undefined (JDK/Spring/library/phantom)
  "correct_empty",       # the zero is meaningful and correct (e.g. external entrypoint)
]
```

### Cause — why; selects the help payload

```
AbsenceCause = Literal[
  "identifier_miss",   # identifier-shaped query, no hit
  "nl_miss",           # free-text / semantic query, no hit
  "filter_miss",       # structured find filter, no hit
  "external",          # target is external / phantom
  "meaningful_empty",  # a traversal root whose zero is correct (generalizes is_external_entrypoint)
]
```

### The DTO (contract only — no logic)

```
AbsenceDiagnosis:
  verdict: AbsenceVerdict
  cause: AbsenceCause
  message: str                                   # one agent-readable line, always present

  # populated only for identifier_miss / not_in_project:
  closest_symbols: list[NodeRef] = []            # reuse NodeRef (graph_types.py:28)
  distances: list[float] = []                    # parallel; 0..1, lower = closer
  proof: AbsenceProof | None = None              # backs a hard not_in_project call

  # populated only for external:
  external_identity: ExternalIdentity | None = None

  # populated only for nl_miss:
  vocabulary_context: VocabularyContext | None = None

  # populated only for filter_miss:
  filter_relaxation: FilterRelaxation | None = None
```

```
AbsenceProof:
  nearest_distance: float
  symbol_count_scanned: int
  thresholds_applied: { close: float, absent_floor: float }
  query_shape: "identifier"          # why did-you-mean ran

ExternalIdentity:
  fqn: str
  reason: Literal["prefix", "phantom", "unresolved-call"]
  source: str | None                 # e.g. "org.springframework" / phantom node id

VocabularyContext:
  top_modules: list[(module, count)]
  top_microservices: list[(microservice, count)]
  roles_present: list[(role, count)]
  frequent_name_tokens: list[str]    # to help reformulate in the codebase's own words

FilterRelaxation:
  per_dimension: list[{
    dimension: str,                  # e.g. "role", "module", "microservice"
    constrained_value: str | None,
    matches_under_relaxation: int,
    suggested_value: str | None
  }]
```

`proof` is what makes a conservative `not_in_project` verdict **auditable and
trustworthy** rather than a bare assertion — the thing that lets an agent commit
to "stop" instead of second-guessing and looping.

---

## Architecture — three layers

```
┌─ Layer 3: TOOL INTEGRATION ──────────────────────────────────────┐
│  MCP: search · find · neighbors · describe · resolve             │
│  CLI: Envelope (generalizes status / is_external_entrypoint)     │
│        │  on empty path: absence = diagnose(...)                 │
└────────┼─────────────────────────────────────────────────────────┘
         ▼
┌─ Layer 2: ABSENCE DIAGNOSIS module (new, stateless) ─────────────┐
│  diagnose(tool, query|filter, root_node, scope, index, graph)    │
│    -> AbsenceDiagnosis                                           │
│  classifies CAUSE -> emits semantically-correct help per cause   │
└────────┼─────────────────────────────────────────────────────────┘
         ▼  reads
┌─ Layer 1: VOCABULARY INDEX asset (precomputed, persisted) ───────┐
│  derived from Symbol nodes: names + fqns + resolved flag +       │
│  external classification. Versioned by ontology_version.         │
│  Built at end of graph build; lazily rebuilt if missing/stale.   │
└──────────────────────────────────────────────────────────────────┘
```

The graph's `Symbol` nodes **are** the vocabulary. Layer 1 is a derived,
search-optimized projection — not new source-of-truth data.

---

## Layer 1 — Vocabulary index asset

### What it stores (two parts)

**Part A — Symbol manifest.** One record per `Symbol` node (types *and*
members; `kind` is carried so candidates render correctly via `NodeRef`):

```
SymbolRecord:
  node_id, fqn, simple_name, normalized_name,
  kind, module, microservice, role, resolved
```

`normalized_name` = lowercased, generics/signature stripped — the key did-you-mean
matches against. Feeds candidate construction and `vocabulary_context`
aggregation (extends `module_counts`/`microservice_counts`,
`ladybug_queries.py:987`).

**Part B — Name-lookup structure (n-gram inverted index).** Maps each q-gram
(q=3) of every `normalized_name` to the record ids containing it. A query name's
grams union to a small candidate set, re-ranked by similarity. Gives
**bounded-time, typo-tolerant** lookup (catches mid-word typos like
`PaymnetClient`, which prefix-only matching misses) without scanning the whole
vocabulary.

**External detection needs no separate structure:** a phantom *is* a `Symbol`
node with `resolved=false`, so it is already in the manifest. "Is X external?" =
manifest lookup by name, then check `resolved` + `_is_external_fqn`
(`ladybug_queries.py:267`). O(1)-ish.

### Format & storage

Sidecar file in `JAVA_CODEBASE_RAG_INDEX_DIR`, serialized **msgpack** with a
header:

```
header: { magic, format_version, ontology_version, built_at, symbol_count }
body:   SymbolRecord[]  +  dict[gram, [record_idx...]]
```

Why sidecar msgpack over a Kùzu table or Lance table: fuzzy n-gram lookup is not
a native Kùzu/Lance query shape — it is loaded into an in-memory structure
regardless, so a decoupled binary loads fastest and avoids entangling graph/vector
internals. (SQLite is the inspectable alternative; msgpack is the default for
load speed.)

### Build timing

At the **end of the graph build**, immediately after `GraphMeta` is written
(`build_ast_graph.py:3704`). At that point all `Symbol` nodes exist; the step
enumerates them and emits the manifest + n-gram index. Pure derivation — no
re-parsing. Also exposed as a **standalone CLI subcommand** so the index can be
(re)built without a full reprocess (for backfill / repair).

### Versioning

The file header stamps the `ontology_version` it was built against. On load,
compare to the graph's live `GraphMeta.ontology_version`
(`build_ast_graph.py:3811`). **Mismatch ⇒ stale ⇒ discard and rebuild.** Reuses
the existing version-guard pattern (`ladybug_queries.py:377`). Reprocess refreshes
it automatically (same flow that writes `GraphMeta`).

### Loading & lazy backfill (graceful degradation)

- **MCP server:** load into a singleton at startup, held alongside the
  `LadybugGraph` instance.
- **CLI:** load per invocation through the same loader.
- **Missing or stale sidecar** (e.g. an index built before this feature ships):
  the diagnosis layer **lazily rebuilds in-process** from the graph (enumerate
  `Symbol` nodes via the existing `MATCH (s:Symbol)` path /
  `find_by_name_or_fqn`, `ladybug_queries.py:997`) on first use, caches in
  memory, and writes the sidecar for next time. **Old indexes work immediately**;
  the first empty pays a one-time build, every load after is a file read. No
  forced reprocess.

### Scalability

50k-symbol enterprise repo: manifest ~10 MB, n-gram index ~500k entries, msgpack
file ~5–15 MB, loads in well under a second. Per-empty fuzzy lookup ≈ a few
hundred candidate comparisons → low-single-digit ms.

---

## Layer 2 — Absence diagnosis module

A stateless module, one entry point.

### Interface (contract)

```
diagnose(
    tool: Literal["search","find","neighbors","describe","resolve"],
    query: str | None,            # search / resolve  (NL or identifier)
    filt: NodeFilter | None,      # find  (structured filter)
    root_node: NodeRef | None,    # neighbors / describe  (resolved subject)
    scope: { microservice, module },
    vocab: VocabularyIndex,       # Layer 1
    graph: LadybugGraph,          # for filter-relaxation probes
) -> AbsenceDiagnosis
```

### Cause classification — decision procedure

**Precedence: `external_dependency` wins.** If the target is external, that is
the most decisive signal — emit it first and stop.

1. **`root_node` present** (neighbors empty — the subject *exists*, it has an
   id) → never "symbol absent." Sub-cases distinguished by the existing
   neighbors-empty hint logic (`mcp_hints.py:294-366`):
   - Subject is external (`resolved=false` or external FQN) → cause `external`.
   - Zero reflects the subject's genuine nature — a leaf with no callees, or an
     external HTTP entrypoint with no in-repo callers → verdict `correct_empty`,
     cause `meaningful_empty` (generalizes `is_external_entrypoint`,
     `jrag_envelope.py:116`).
   - Requested edge type/direction is inapplicable to the subject's kind →
     verdict `refine_query`, help = the existing neighbors edge-type hints.
2. **`filt` present** (find empty):
   - Identifier-shaped filter (`_find_has_identifier_shaped_filter`,
     `mcp_hints.py:280`) with a near-match that is *filtered out* by other
     dimensions → cause `filter_miss`.
   - Identifier-shaped with **no** near-match → cause `identifier_miss`.
   - Broad / non-identifier filter (e.g. `role=REPOSITORY`) → cause `filter_miss`.
   - *(Distinguishing the two identifier cases: run did-you-mean on the
     identifier value; a close hit excluded by another dimension ⇒ filter_miss
     — relaxation reveals it; nothing close ⇒ identifier_miss.)*
3. **`query` present** (search / resolve / `describe` not-found):
   - `describe` not-found by `fqn` is an `identifier_miss` (did-you-mean on the
     fqn); not-found by `node_id` yields a minimal `refine_query` with no
     did-you-mean (an unknown id, not a misspelled name).
   - Classify query shape via the identifier-shape heuristic (CamelCase token /
     dotted FQN / `Cls#method`, no stopwords) vs free-text NL.
     - Identifier-shaped → cause `identifier_miss` (did-you-mean).
     - NL → cause `nl_miss` (vocabulary context — string did-you-mean is
       meaningless for NL).

### Did-you-mean ranking + the conservative threshold policy

Candidate generation (Layer 1 n-gram lookup) → rank by normalized string
similarity (metric family: Jaro-Winkler / normalized edit-distance, ∈ [0,1];
exact metric finalized in the plan) → keep top **N** (default 5).
`distance = 1 − similarity`, parallel to `closest_symbols`.

**Two-band threshold, config-tunable** (see Configuration):

| Best similarity | Verdict | Rationale |
|---|---|---|
| ≥ `close` (≈0.85, illustrative) | `refine_query` | likely typo/misremember — show the near-match |
| `< absent_floor` (≈0.4, illustrative) **and** identifier-shaped | `not_in_project` | confidently absent — stop |
| middle band | `refine_query` | **conservative default** — never commit to absent when uncertain |

**Always** return the nearest N candidates + distances, regardless of verdict —
that is both the one-shot pivot (kills the loop) and the `proof`. This realizes
the locked asymmetry: false-absent is catastrophic, false-refine is one cheap
extra query.

### Per-cause help payloads

| Cause | Payload | Source |
|---|---|---|
| `identifier_miss` | `closest_symbols` + `distances` + `proof` | Layer 1 ranking |
| `nl_miss` | `vocabulary_context` | manifest aggregation (extends `module_counts`/`microservice_counts`, `ladybug_queries.py:987`) |
| `filter_miss` | `filter_relaxation` | ports `_zero_result_guidance` (`jrag.py:4139`) into a structured payload, now MCP-available |
| `external` | `external_identity` | `_is_external_fqn` (`:267`) + `resolved=false` + CALLS-edge `resolved` attr |
| `meaningful_empty` | `message` (+ existing entrypoint context) | generalizes `is_external_entrypoint` (`jrag_envelope.py:116`) |

### Relationship to existing hints

The diagnosis **subsumes** the relevant `mcp_hints.py` branches (resolve-none
`:541`, find-identifier `:593`, neighbors-empty `:294`, search-weak `:575`) —
those become special cases of the five causes. The `hints_structured` /
`StructuredHint` machinery remains the **transport** for "next-action"
suggestions (e.g. "call `resolve(...)`"); `AbsenceDiagnosis` is the **diagnosis**
carried alongside. They compose, not conflict.

---

## Layer 3 — Tool integration

### The shared field (additive, non-breaking)

Every output model gains one optional field:

```
absence: AbsenceDiagnosis | None = None
```

`None` on success-with-results; populated only on the empty / not-found path.
Added to `SearchOutput`, `FindOutput`, `NeighborsOutput`, `DescribeOutput`
(`mcp_v2.py:509`, `:525`, `:546`, `:554`), `ResolveOutput`
(`resolve_service.py:121`), and the CLI `Envelope` (`jrag_envelope.py:86`). MCP
JSON stays backward-compatible (clients ignore unknown optional fields).

### Per-tool wiring (one thin call site each)

| Tool | Empty path | `diagnose(...)` inputs |
|---|---|---|
| `search` | `mcp_v2.py:944` | `tool="search", query=…` |
| `find` | `mcp_v2.py:1036` | `tool="find", filt=…` |
| `neighbors` | `mcp_v2.py:1639` | `tool="neighbors", root_node=…` |
| `describe` | `mcp_v2.py:1096` (today `success=False`) | `tool="describe", query=fqn` → now actionable did-you-mean |
| `resolve` | `resolve_service.py:570` (`status="none"`) | `tool="resolve", query=…` → rich absence replaces the bare "try search" hint |

Each empty branch: detect empty → `absence = diagnose(...)` → attach. The CLI
renderer maps `absence` to human text (extends `_render_not_found`,
`jrag_render.py:227`).

### CLI alignment

The CLI `Envelope` already has `status`, `is_external_entrypoint`, and
`_zero_result_guidance`. The `absence` field generalizes them: `status="not_found"`
maps onto the absence verdicts; `is_external_entrypoint` becomes the
`correct_empty` / `meaningful_empty` case; `_zero_result_guidance` becomes
`filter_relaxation`. The renderer keeps producing equivalent human text so
existing CLI consumers see no regression.

---

## External detection

Driven by three signals (checked in precedence order):

1. **Prefix** — `_EXTERNAL_PREFIXES` (`ladybug_queries.py:240`) → `reason="prefix"`.
2. **Phantom** — `Symbol.resolved=false` node with a matching name →
   `reason="phantom"`.
3. **Unresolved call target** — a CALLS edge with `attrs.resolved=false`
   (`mcp_v2.py:1157`) whose callee matches → `reason="unresolved-call"`.

Note `resolve` today does **not** filter on `s.resolved`
(`_resolve_symbol_candidates`, `resolve_service.py:170-244`), so it can already
return a phantom as a candidate — this proposal makes that explicit and labeled
rather than incidental.

---

## Configuration

New tunables exposed via the existing config surface (`docs/CONFIGURATION.md`:
env vars / project YAML):

| Knob | Default (illustrative) | Purpose |
|---|---|---|
| `absence_close_threshold` | 0.85 | similarity ≥ this ⇒ `refine_query` (near-match) |
| `absence_absent_floor` | 0.40 | similarity < this (identifier-shaped) ⇒ `not_in_project` |
| `absence_candidate_count` (N) | 5 | nearest symbols returned |
| `absence_ngram_q` | 3 | n-gram width |
| `absence_diag_enabled` | true | master toggle (degrades to today's behavior if false) |

Exact defaults are finalized in the plan and validated by the did-you-mean quality
tests (incl. the false-absent guard).

---

## Error handling / degradation

**The diagnosis layer must never turn an empty result into an error.** Empties
stay `success=True` (`describe` keeps its current `success=False` semantics, now
with actionable `absence`).

- **Missing / stale vocab index** → lazy rebuild (Layer 1).
- **Rebuild fails / graph unreadable** → degrade to `refine_query` + a `message`
  that the diagnosis index is unavailable; fall back to existing hints; return
  `absence=None` rather than failing.
- **Empty / unindexed project** (no `Symbol` nodes) → `refine_query` with
  message "index appears empty/unindexed — verify the project was indexed"
  (mirrors the `AGENT-GUIDE.md` proof-of-absence caveat, `:29`). Never a false
  `not_in_project`.
- **Diagnosis exception** → catch, log, emit minimal/none. The tool still returns
  its normal empty payload.
- **Rebuild-too-slow guardrail** (huge repo, cold cache) → cap rebuild cost; if
  exceeded, fall back to `refine_query` rather than blocking the query.

---

## Testing

- **`diagnose` unit matrix** — parameterized over (cause × verdict):
  identifier_miss-absent, identifier_miss-refine (close typo), nl_miss,
  filter_miss (relaxation correct), external (prefix / phantom / unresolved-call),
  meaningful_empty. Small fixture graph.
- **Index build / load** — manifest + n-gram contents from a fixture graph;
  msgpack round-trip; version header written/read; stale-version ⇒ rebuild.
- **Did-you-mean quality + false-absent guard** — known typo ⇒ expected closest;
  near-match ⇒ `refine` (not absent); nothing-close ⇒ `not_in_project`; middle
  band ⇒ `refine`; and explicitly: a symbol that *exists* under an unusual name
  must never yield `not_in_project`.
- **Per-tool integration** — each of the five empty paths attaches the right
  `absence`.
- **CLI envelope / JSON** — `absence` serialized; renderer text per verdict;
  `is_external_entrypoint` still renders under `correct_empty`.
- **Backfill** — missing sidecar ⇒ first empty rebuilds + writes file ⇒ second
  load reads file.
- **Regression** — the subsumed `mcp_hints` branches still produce equivalent
  guidance.
- **Performance guard** (optional) — 50k-symbol fixture ⇒ empty-path diagnosis
  under a latency budget; sidecar load < 1s.

All tests follow `AGENTS.md`: `.venv/bin/python`, editable install, erase stale
`tests/*/.java-codebase-rag` indexes, run the full suite once at task end.

---

## Migration / rollout

- **Index acquisition:** lazy backfill means existing indexes gain the feature on
  first empty (one-time in-process build, then persisted). No forced reprocess;
  a reprocess refreshes it.
- **`ontology_version`:** bumping is **not required** for the data model (the
  vocab index is derived, versioned by the *current* `ontology_version`, not a
  new one). The plan confirms whether the sidecar's `format_version` warrants a
  distinct bump.
- **Backward compat:** the `absence` field is optional; MCP clients that ignore
  unknown fields are unaffected. CLI text output is preserved (renderer maps the
  new vocabulary to existing phrasing).
- **Docs:** update `docs/AGENT-GUIDE.md` (the recovery playbook + proof-of-absence
  caveat) to document the verdicts and how to read `absence`; note the new config
  knobs in `docs/CONFIGURATION.md`.

---

## Decisions log

1. **Distinguish absent / external / refine** (not collapse them) — the right
   next action differs for each. *(brainstorming Q1)*
2. **Richness = per-cause help** (not "verdict only" or "always did-you-mean"):
   string did-you-mean is only meaningful for identifier-shaped queries; NL
   empties get vocabulary context; filter empties get relaxation. Correctness
   over cost. *(Q2)*
3. **Conservative `not_in_project` threshold, but always return candidates + proof**
   — false-absent is catastrophic, false-refine is one cheap extra query.
   Candidates kill the loop via pivot, so conservative does not mean weak.
   *(Q3)*
4. **All five tools, both surfaces, one vocabulary** — the most-correct scope; the
   CLI already prototypes it, the MCP layer is the gap. *(Q4)*
5. **Precomputed vocabulary index (approach C)** — only option that is both
   most-correct and most-efficient at scale; fits the existing
   enrichment/`ontology_version` grain.
6. **`correct_empty` verdict added** (generalizes `is_external_entrypoint`) — a
   meaningful zero is distinct from a failure; prevents misreading. *(checkpoint 4)*
7. **External-wins precedence** — if the target is external, say so first.
8. **Sidecar msgpack** for the index (load speed); SQLite noted as the
   inspectable alternative.
9. **`n`-gram (q=3) lookup** for typo-tolerant, bounded-time fuzzy match.

---

## Grounding references (verified against `master` `168e31a`, 2026-07-08)

- **MCP tools & registration:** `server.py:590-842` (`search`, `find`,
  `describe`, `neighbors`, `resolve`).
- **Output models:** `SearchOutput` `mcp_v2.py:509`, `FindOutput` `:525`,
  `DescribeOutput` `:546`, `NeighborsOutput` `:554`, `ResolveOutput`
  `resolve_service.py:121`; envelope fields `success/message/advisories/
  hints_structured`; `StructuredHint` `graph_types.py:39`, `NodeRef` `:28`.
- **Empty paths:** `search` `mcp_v2.py:944`, `find` `:1036`, `describe` `:1096`,
  `neighbors` `:1639`, `resolve` none `resolve_service.py:570`.
- **Resolve machinery:** `_resolve_symbol_candidates` `resolve_service.py:170-244`
  (no `s.resolved` filter), `_resolve_rank_candidates` `:504`.
- **Symbol universe / enumeration:** `_SYM_COLS` `ladybug_queries.py:296`,
  `find_v2` MATCH `(s:Symbol)` `mcp_v2.py:999`, `module_counts`/`microservice_counts`
  `ladybug_queries.py:987`, `find_by_name_or_fqn` `:997`.
- **External / phantom:** `Symbol.resolved` `ladybug_queries.py:127`,
  phantom `build_ast_graph.py:1189` + `graph_enrich.py:1689`,
  `_EXTERNAL_PREFIXES` `ladybug_queries.py:240`, `_is_external_fqn` `:267`
  (used `:1226`, `:1353`, `:1528`), CALLS `attrs.resolved` `mcp_v2.py:1157`,
  HTTP/ASYNC `attrs.match` `:1158`.
- **Versioning & persistence:** `ontology_version` schema `build_ast_graph.py:2916`,
  set `:3704`, read `:3811`, guard `ladybug_queries.py:377`, constant
  `ONTOLOGY_VERSION` `java_index_flow_lancedb.py:464`; `JAVA_CODEBASE_RAG_INDEX_DIR`
  `java_index_flow_lancedb.py:7`, `:311`; enrichment entrypoint
  `enrich_chunk` `java_index_flow_lancedb.py:385`, `:433`.
- **Existing hints (subsumed):** `mcp_hints.py:541` (resolve-none), `:593` /
  `:280` (find identifier-shape), `:294` (neighbors-empty), `:575` (search-weak).
- **CLI prototypes:** `Envelope` `jrag_envelope.py:86`, `EnvelopeStatus` `:39`,
  `is_external_entrypoint` `:116` (set `jrag.py:2393`), `_zero_result_guidance`
  `jrag.py:4139`, `_render_not_found` `jrag_render.py:227`.
- **Docs:** `docs/AGENT-GUIDE.md:29` (proof-of-absence), `:17` (ontology caveat),
  `:263-268` (recovery playbook), `:158` (resolve status table); `docs/CONFIGURATION.md`
  (hints/advisories toggle `:162`).
