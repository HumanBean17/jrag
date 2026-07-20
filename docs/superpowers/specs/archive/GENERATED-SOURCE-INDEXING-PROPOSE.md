> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Generated-Source Indexing: Classify, Tag, Filter — Don't Penalize

## Status

**Active** — in design (2026-07-08). Make generated sources (OpenAPI,
jsonschema2pojo, protobuf, MapStruct, wsimport, QueryDSL, JOOQ, Immutables,
AutoValue, …) a first-class, **tagged** dimension: fully retrievable (closes
the agent dead-end when resolving a generated type), **filterable on demand**,
with **no ranking penalty by default**. Generated code competes on its existing
role merits; the agent gets the metadata and a filter to decide.

A companion implementation plan will live at
`plans/active/PLAN-GENERATED-SOURCE-INDEXING.md`.

## Problem Statement

The indexer has no concept of "generated sources." File discovery is a
filesystem walk + `*.java` filter + gitignore-style rules
(`path_filtering.py:437-477`); `pom.xml` / `build.gradle` are marker files
only, never parsed for source directories or processor output. So whether
generated code is indexed depends entirely on where it sits on disk:

- **Build-output-generated** (`target/generated-sources/`, `build/generated/`)
  is dropped incidentally — pruned as build output
  (`path_filtering.py:51-89`), not because the tool recognizes generation.
- **Committed generated `.java`** (vendored OpenAPI clients, jsonschema2pojo
  models, delombok output, a `generated/` package under `src/main/java/`)
  **is indexed with no classification** — indistinguishable from hand-written
  code.

The committed case is the real gap, with two failure modes:

1. **Agent dead-end (retrieval).** During exploration an agent sees a generated
   type referenced (an OpenAPI `OrderResponse`, a jsonschema2pojo model) and
   tries to resolve its source. Today it either finds nothing (code under a
   pruned build dir) or finds an unclassified file it cannot tell apart from
   hand-written code. There is no signal that this is generated.

2. **Unclassified noise.** Generated code participates in the graph and search
   exactly like hand-written code, with no metadata to filter or reason about.

### What the existing ranking already handles

The role-aware ranking model already mitigates the *ranking-dilution* concern
for the common case. `search_lancedb.py:193` defines `_ROLE_SCORE_WEIGHTS`
("Positive values favour actionable roles"); the hybrid score (line 211) is
`raw_rrf * import_factor + role_weight + symbol_bonus + …`, and `_role_weight`
(line 323) returns `0.0` for any role not in the map. Non-actionable roles —
DTOs, mappers, value objects — already get zero boost and rank below
services/controllers. **Most generated code (DTOs, mappers) is therefore
already down-ranked by its role.** A separate `generated` ranking penalty
would double-count the same signal.

This is why the design below adds **no ranking penalty and no graph fan-out
exclusion** for generated sources: the role model covers ranking, and the rest
is YAGNI (see Non-goals).

## Proposed Solution

Four pieces: **detect, tag, surface, filter.** Default behavior is
**equal treatment** — generated sources rank and traverse exactly like
hand-written code; only the tag and an opt-in filter change anything.

### 1. Detection — content-based, file-level, shared predicate

A single classification predicate, called by **both** the vector flow and the
graph build, decides per file whether it is generated and which generator
family produced it. One function, no duplication.

- **Signals:** `@Generated` annotations
  (`javax.annotation.processing.Generated`, `jakarta.annotation.processing.Generated`,
  and generator-specific equivalents) **and** built-in header-comment patterns
  for the common generators. `generated_by` is inferred from the annotation
  value / header when identifiable; otherwise `generated=true, generated_by=null`.
- **File-level:** a generated file tags all its chunks and all its types.
  (Intra-file / type-level precision is a non-goal.)
- **Content-based, not path-based:** deliberately avoids path heuristics
  (`generated/`, `generated-sources/`) so real packages named `generated` are
  never misclassified — consistent with why `target` / `build` / `out` are
  pruned *conditionally* (`path_filtering.py:30-37`).

**Target generator families (v1):** openapi, jsonschema2pojo, protobuf,
mapstruct, wsimport, querydsl, jooq, immutables, autovalue. *Exact marker
strings per family are verified at implementation time, not asserted here.*

**Extensible config** — mirror the existing `role_overrides` config-loading
mechanism (`graph_enrich.py:396-414` reads a section from
`.java-codebase-rag.yml`). Add a `generated_detection` section:

```yaml
generated_detection:
  header_patterns:        # extra patterns matched against the file header
    - "// MyInternalCodeGen .*"
  annotation_patterns:    # extra @-annotation simple names treated as generated
    - "MyGeneratedMarker"
  force_fqns:             # explicit per-FQN override (mark generated)
    - "com.example.vendored.Model"
  exclude_fqns: []        # explicit per-FQN override (mark NOT generated)
```

### 2. Data model — tag both indexes

`generated` is **orthogonal to `role`**: a generated type can be a DTO, a
mapper, or a service. It is *not* folded into the role system.

- **LanceDB chunks** — add to the Java chunk schema (the `JavaLanceChunk` /
  `JAVA_ENRICHED_COLUMNS` path, same shape as the `capabilities` column
  addition):
  - `generated: bool`
  - `generated_by: string | null`
- **Graph nodes** — add `generated` and `generated_by` properties on type
  nodes, set during AST graph build (`build_ast_graph.py`).

### 3. Retrieval & filtering behavior — equal treatment by default

- **Default:** generated sources rank and traverse exactly like hand-written
  code.
- **Surfaced:** every MCP search/edge result row carries `generated` and
  `generated_by`, so the agent can reason about / filter client-side.
- **Server-side filter params** (mirror `role` / `exclude_roles`,
  `search_lancedb.py:82-83, 115-117`):
  - `exclude_generated: bool` (default `false`) → `WHERE generated = false`
  - `generated_only: bool` (default `false`) → `WHERE generated = true`
    (optional, symmetric)
- Exposed on the search tool **and** edge queries for symmetry. Server-side
  filtering preserves result budget (a top-20 request returns 20 non-generated
  rows, not 20-then-discard).

### 4. Indexing & migration

Adding the columns/properties is a schema change. Existing indexes require a
**reprocess** (the reprocess path already exists) to populate `generated` /
`generated_by`. Old chunks default to `generated=false, generated_by=null`
until reprocessed — acceptable because detection is purely additive. This
reprocess requirement is documented in the operator CLI docs
(`docs/JAVA-CODEBASE-RAG-CLI.md`).

## Non-goals (deferred until a measured problem appears)

- **Ranking down-weight** for generated sources (the role model already covers
  the common DTO/mapper case).
- **Graph fan-out exclusion** for generated nodes (role ranking is search-side;
  no evidence it is needed for traversal; generated DTOs are shallow leaves).
- **Type-level (intra-file) detection precision.**
- **`generated_by`-based filtering** (the data is stored; a
  `generated_by="openapi"` filter is a trivial later addition).
- **A behavioral opt-in/out flag** that changes default visibility.
- **Generated-aware diagnostics** (e.g. a `diagnose-generated` report).

## Open questions

1. **Exact detection markers** per generator family — verified at
   implementation (annotation FQNs + header patterns). May reveal families
   that emit only a header comment and no `@Generated` (some jsonschema2pojo
   / older protobuf configs).
2. **`generated_by` normalization** — store the raw generator string, map to a
   family best-effort, default to `null`. Settled at implementation.

## References

- `path_filtering.py:437-477` (file walker), `:38-47`
  (`COMMON_EXCLUDED_PATH_PATTERNS`), `:51-89` (conditional build-dir prune).
- `search_lancedb.py:193` (`_ROLE_SCORE_WEIGHTS`), `:211` (hybrid score),
  `:323` (`_role_weight`), `:82-83, 115-117` (`role` / `exclude_roles` filter
  pattern to mirror).
- `java_index_flow_lancedb.py` (CocoIndex flow, Java chunk schema — where the
  `generated` / `generated_by` columns are added).
- `build_ast_graph.py` (AST graph build — where type-node `generated`
  properties are set).
- `graph_enrich.py:396-414` (`role_overrides` config-loading pattern to mirror
  for `generated_detection`).
- `docs/CODEBASE_REQUIREMENTS.md:62-66, 444-466` (existing operator guidance
  telling users to ignore generated code manually — this proposal supersedes
  that workaround).
