# Plan: call-graph layer (static intra-JVM `CALLS` + `DECLARES`)

Status: **completed** — shipped (static intra-JVM `CALLS` + `DECLARES` on `master`). Self-contained: an agent picking this up
should be able to land it without re-deriving the design. Pairs with
`propose/completed/CALL-GRAPH-PROPOSE.md` (scope, rationale, schema).

## Goal

Add method-level `CALLS` edges between `Symbol` nodes (methods /
constructors) in the Kuzu graph, plus a thin `DECLARES` edge from types
to their own methods. Each `CALLS` edge carries a `confidence` +
`strategy` + `source` triple so downstream tools can filter.

Scope is **static, intra-JVM** only. No HTTP/async cross-service edges,
no AOP proxy resolution, no runtime-trace ingestion (explicit non-goals).
See `propose/completed/CALL-GRAPH-PROPOSE.md` §8.

## Principles (do not relitigate in review)

- **Mostly additive, breaking defaults allowed.** No table dropped, no
  tool signature removed. New params are optional; some defaults change
  (e.g., `trace_flow.follow_calls=True`). See `propose/completed/CALL-GRAPH-PROPOSE.md`
  §3.
- **Confidence-scored edges.** 4-strategy cascade from CMM
  (`tmp/what-to-borrow-from-cmm.md` B1). Implementation details in
  `propose/completed/CALL-GRAPH-PROPOSE.md` §4.2.
- **LanceDB untouched.** No reindex; only the Kuzu graph rebuilds.
- **Ontology bump 3 → 4.** Full graph rebuild required; guarded by
  `KuzuGraph.get()` so stale readers fail loudly.

## File-by-file changes

### 1. `ast_java.py` — call-site extraction

Additions (~150 lines, no removals):

1. New dataclass `CallSite` with fields per `propose/completed/CALL-GRAPH-PROPOSE.md` §4.1
   (`caller_fqn`, `receiver_expr`, `callee_simple`, `arg_count`,
   `is_static_call`, `is_constructor`, `in_lambda`, `line`, `byte`).
   Exported in `__all__`.
2. New dataclass `FileImports` to track static imports:
   ```python
   @dataclass
   class FileImports:
       explicit: dict[str, str]        # SimpleType -> FQN (existing logic, refactored)
       static_methods: dict[str, str]  # simple_name -> FQN
       static_wildcards: list[str]     # type FQNs for wildcard static imports
   ```
   Populated during file-level import parsing. Used by `_collect_call_sites`
   to resolve bare calls that are actually static imports.
3. New `MethodDecl.call_sites: list[CallSite] = field(default_factory=list)`.
   Populated by the existing `_parse_method` after the rest of the method
   is built, so method body visitors can see the full `MethodDecl` shape.
4. New internal helper `_collect_call_sites(method_body: Node, src: bytes,
   *, enclosing_fqn: str, enclosing_sig: str, file_imports: FileImports,
   in_lambda: bool=False) -> list[CallSite]`.
   Visits these tree-sitter node types:
   - `method_invocation` → decompose child fields `object` / `name` /
     `arguments`; `object` absent → bare call (check `file_imports.static_methods`
     first, then fall back to `this`); set `is_static_call=True`
     when `object` is a type-identifier-only expression.
   - `object_creation_expression` → `callee_simple='<init>'`,
     `is_constructor=True`, receiver-expr = the type expression's raw text.
   - `method_reference` → `callee_simple=<name>`; `arg_count=-1`. If
     qualifier is an expression (not a simple identifier or type), mark
     for `chained_receiver` strategy in resolution phase.
   - `explicit_constructor_invocation` → `this` / `super` constructor call;
     `callee_simple='<init>'`.
   - `lambda_expression` / `anonymous_class_body` → recurse with
     `in_lambda=True`. Both lambda bodies and anonymous inner class bodies
     are walked so the calls inside attribute to the enclosing named method.
5. **Implicit super detection**: After collecting explicit call sites in a
   constructor, check if any is an `explicit_constructor_invocation`. If
   not, synthesize a `CallSite` for the implicit `super()`:
   ```python
   if is_constructor and not any(cs.callee_simple == '<init>' and cs.receiver_expr in ('this', 'super') for cs in sites):
       sites.append(CallSite(
           caller_fqn=enclosing_fqn, receiver_expr='super',
           callee_simple='<init>', arg_count=0,
           is_static_call=False, is_constructor=True,
           in_lambda=False, line=method_start_line, byte=method_start_byte
       ))
   ```
6. Counting rules for `arg_count`:
   - For `argument_list`: number of top-level named children.
   - Spread arguments count literally (one per written arg). Do not try
     to unroll varargs at this layer.
7. Bump `ONTOLOGY_VERSION` from `3` to `4`. Document in docstring.

**Exports** in `__all__`: `"CallSite"`, `"FileImports"`.

**Do not** import anything new; everything needed is already in the file.

### 2. `graph_enrich.py` — no functional change

Call-sites are AST-only; no enrichment or override is applied to them.
Leaving this file untouched simplifies review. If `CallSite` needs to be
re-exported from here later (for chunk enrichment purposes), that is a
follow-up.

### 3. `build_ast_graph.py` — third resolution pass + new writers

The current two-pass shape (`pass1_parse`, `pass2_edges`) stays. Append:

#### 3.1 Indexes for pass 3

- Build a **`methods_by_type`** index at the start of `pass3_calls`
  (`parent_fqn` → method / constructor members).
- Receiver-type fallback **`unique_type_name` (0.75)** uses the existing
  **`by_simple_name`** type registry from pass 1 (exactly one type with that
  `decl.name`). Do **not** disambiguate receivers with a per-method simple-name
  map — a globally unique *method* name is not evidence about an unresolved
  receiver identifier.

No change to the existing `tables.types` / `by_simple_name` / `by_package`
indexes beyond what pass 1 already builds.

#### 3.2 New dataclasses (add in `build_ast_graph.py`)

```python
@dataclass
class CallsRow(EdgeRow):
    call_site_line: int = 0
    call_site_byte: int = 0
    arg_count: int = 0
    confidence: float = 0.0
    strategy: str = "phantom"
    source: str = "static"

@dataclass
class DeclaresRow:
    src_id: str  # type Symbol id
    dst_id: str  # method/constructor Symbol id
```

Add `tables.calls_rows: list[CallsRow]`, `tables.declares_rows: list[DeclaresRow]`
to `GraphTables`.

#### 3.3 New `pass3_calls` function

Invoked from `main` after `pass2_edges`. Walks every `MemberEntry`,
resolves each `CallSite` with the algorithm in
`propose/completed/CALL-GRAPH-PROPOSE.md` §4.3, and appends `CallsRow` entries.

**Error handling**: Wrap per-method and per-file processing in try/except:
```python
def pass3_calls(tables: GraphTables, asts: list[JavaFileAst], *, verbose: bool):
    stats = CallResolutionStats()
    for file_ast in asts:
        try:
            _process_file_calls(file_ast, tables, stats)
        except Exception as e:
            log.error(f"Call extraction failed for {file_ast.path}: {e}")
            # Continue with next file

def _process_file_calls(file_ast, tables, stats):
    for method in file_ast.methods:
        try:
            _resolve_method_calls(method, file_ast, tables, stats)
        except Exception as e:
            log.warning(f"Failed to extract calls from {method.fqn}: {e}")
            # Continue with next method
```

**Diagnostic counter** (logged at end of pass3):
```python
@dataclass
class CallResolutionStats:
    total: int = 0
    by_strategy: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    phantom_chained: int = 0
    phantom_other: int = 0

# At end of pass3_calls:
log.info(f"Call resolution: {stats.total} sites, "
         f"{stats.phantom_chained} chained phantoms "
         f"({100*stats.phantom_chained/max(1,stats.total):.1f}%), "
         f"strategies: {dict(stats.by_strategy)}")
```

Resolver helpers (new, all within `build_ast_graph.py`):

1. `_scope_table(method: MemberEntry, tables: GraphTables) -> dict[str, str]` —
   returns variable-name → receiver-type FQN, populated from:
   - Enclosing type fields (walk inherited fields from resolved supertypes
     via existing EXTENDS/IMPLEMENTS data in `tables`).
   - Method parameters.
   - Local `variable_declarator` inside the method body (tree-sitter field
     `type` + `declarator`). A cheap single-pass scan of the body is
     sufficient; no data-flow required.
2. `_resolve_receiver(expr: str, *, call: CallSite, method: MemberEntry,
   ast: JavaFileAst, tables: GraphTables) -> tuple[str | None, str, float]` —
   returns `(type_fqn_or_None, strategy, confidence)`. Resolution cascade:
   - If bare call and `expr` in `ast.file_imports.static_methods` →
     `(fqn, 'static_import', 0.95)`.
   - If bare call, check `ast.file_imports.static_wildcards` for matching
     static method → `(fqn, 'static_import_wildcard', 0.85)`.
   - If `expr` is `this` / `super` / empty → `('this_super', 0.95)`.
   - If `expr` is simple identifier in scope table → `(fqn, 'import_map', 0.95)`.
   - If `expr` is chained (`a.b()`) → `(None, 'chained_receiver', 0.0)`.
   - Else try `same_module` → 0.90, `unique_type_name` → 0.75, `suffix` → 0.55.
3. `_lookup_method(type_fqn: str, callee_simple: str, arg_count: int, *,
   tables: GraphTables) -> list[MemberEntry]` — searches that type's
   methods (and walks resolved supertypes via existing `_resolve_simple`
   on EXTENDS/IMPLEMENTS names), returning all candidates matching by
   `(name, arg_count)`; falls back to name-only when no arity match.
4. `_emit_call(call: CallSite, receiver_fqn: str | None, candidates:
   list[MemberEntry], tables: GraphTables, stats: CallResolutionStats, *,
   strategy: str, confidence: float) -> None`:
   - If `candidates` empty → phantom method node (via `_phantom_method`) +
     `CallsRow` with `resolved=False`, `confidence=0.0`, `strategy='phantom'`.
   - If exactly one candidate → one `CallsRow` with the given confidence /
     strategy.
   - If >1 candidate → one `CallsRow` per candidate,
     `strategy='overload_ambiguous'`, same base confidence.
   - Update `stats` counters.

Strategy / confidence pairing:
- Static import (explicit) → 0.95, `strategy='static_import'`.
- Static import (wildcard) → 0.85, `strategy='static_import_wildcard'`.
- Receiver resolved via `import_map` or file-scoped sibling → 0.95.
- Via same-package / wildcard import → 0.90.
- `this` / `super` / bare call on enclosing type → 0.95, `strategy='this_super'`.
- Implicit super constructor → 0.90, `strategy='implicit_super'`.
- Globally unique **type** simple name for a static qualifier / unresolved
  identifier → 0.75, `strategy='unique_type_name'`.
- Suffix match (single FQN ends with receiver simple) → 0.55.
- Chained receiver (`a.b().c()`) or expression method reference (`getX()::bar`)
  → 0.0 phantom, `strategy='chained_receiver'`.

#### 3.4 Phantom method helper

```python
def _phantom_method(tables, *, receiver_fqn: str | None, receiver_expr: str,
                    callee: str, arg_count: int) -> str:
    """Create or reuse a phantom method node.
    
    FQN format:
    - Receiver resolved: "{receiver_fqn}#{callee}({arg_count})"
    - Receiver unresolved: "?{receiver_expr_short}#{callee}({arg_count})"
    
    The '?' prefix makes phantoms visually distinct and grep-able.
    """
    if receiver_fqn:
        fqn = f"{receiver_fqn}#{callee}({arg_count})"
    else:
        expr_short = (receiver_expr[:50] if receiver_expr else "?")
        fqn = f"?{expr_short}#{callee}({arg_count})"
    
    pid = phantom_id(fqn)
    if pid not in tables.phantoms:
        tables.phantoms[pid] = {
            "id": pid, "kind": "method", "name": callee, "fqn": fqn,
            "package": "", "module": "", "microservice": "",
            "filename": "", "start_line": 0, "end_line": 0,
            "start_byte": 0, "end_byte": 0,
            "modifiers": [], "annotations": [], "capabilities": [],
            "role": "OTHER", "signature": f"{callee}({arg_count})",
            "parent_id": "", "resolved": False,
        }
    return pid
```

#### 3.5 Schema & writers

Append to `build_ast_graph.py`:

```python
_SCHEMA_DECLARES = (
    "CREATE REL TABLE DECLARES(FROM Symbol TO Symbol)"
)
_SCHEMA_CALLS = (
    "CREATE REL TABLE CALLS(FROM Symbol TO Symbol, "
    "call_site_line INT64, call_site_byte INT64, arg_count INT64, "
    "confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN)"
)
```

Update `_drop_all` to drop `CALLS`, `DECLARES` before `Symbol`.
Update `_create_schema` to create them after the existing `INJECTS` table.

New Cypher inserts (mirror `_CREATE_EXT`):

```python
_CREATE_DECL = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:DECLARES]->(b)"
)
_CREATE_CALL = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:CALLS {"
    "call_site_line: $line, call_site_byte: $byte, arg_count: $argc, "
    "confidence: $conf, strategy: $strat, source: $src_kind, resolved: $resolved"
    "}]->(b)"
)
```

Extend `_write_edges(conn, tables)` with loops writing `tables.declares_rows`
and `tables.calls_rows`. Populate `declares_rows` from `tables.members`
right before `_write_edges` runs (cheap, no extra parse pass).

**Deduplication** before writing CALLS (handles edge cases where same call
site resolves through multiple code paths):

```python
def _write_edges(conn, tables):
    # ... existing EXTENDS/IMPLEMENTS/INJECTS writes ...
    
    # Write DECLARES (no dedup needed — one per method)
    for row in tables.declares_rows:
        conn.execute(_CREATE_DECL, {"src": row.src_id, "dst": row.dst_id})
    
    # Dedup CALLS by (src_id, dst_id, arg_count, call_site_line)
    seen_calls: set[tuple[str, str, int, int]] = set()
    unique_calls = []
    for row in tables.calls_rows:
        key = (row.src_id, row.dst_id, row.arg_count, row.call_site_line)
        if key not in seen_calls:
            seen_calls.add(key)
            unique_calls.append(row)
    
    for row in unique_calls:
        conn.execute(_CREATE_CALL, {
            "src": row.src_id, "dst": row.dst_id,
            "line": row.call_site_line, "byte": row.call_site_byte,
            "argc": row.arg_count, "conf": row.confidence,
            "strat": row.strategy, "src_kind": row.source,
            "resolved": row.resolved,
        })
```

Extend `_write_meta` counts dict with `"calls"` (count of `unique_calls`)
and `"declares"` (count of `declares_rows`).

**Partial failure safety**: Kuzu writes are not transactional across tables.
If `pass3_calls` or `_write_edges` fails mid-write, run `build_ast_graph.py`
again — the `_drop_all` at start ensures clean slate. No partial-graph
state is left behind.

#### 3.6 CLI

`main()` calls `pass3_calls(tables, asts, verbose=...)` between
`pass2_edges` and `write_kuzu`. No new CLI flag.

### 4. `kuzu_queries.py` — new helpers + wiring

Additions (~80 lines):

1. Extend `_symbol_return_for` — **no change needed**; call rows return a
   Symbol and edge properties are projected separately.
2. New dataclass `CallEdge` parallel to `EdgeHit` with fields
   `confidence: float`, `strategy: str`, `source: str`,
   `call_site_line: int`, `call_site_byte: int`, `arg_count: int`,
   `resolved: bool`, plus `src: SymbolHit` and `dst: SymbolHit`.
3. New helper `find_callers(
     needle: str, *, depth: int=1, limit: int=100,
     min_confidence: float=0.0, exclude_external: bool=True,
     module: str | None=None, microservice: str | None=None,
   ) -> list[CallEdge]`.

   Input `needle` is resolved in this order:
   - Exact `fqn` match on a `Symbol` with `kind IN ('method','constructor','class','interface','enum','record')`.
   - Simple method name → all methods of that name (leaves disambiguation
     to the agent).
   - Type FQN → fan out via `DECLARES`.

   Query sketch (type-fan-out case):
   ```cypher
   MATCH (t:Symbol) WHERE t.fqn = $needle OR t.name = $needle
   MATCH (t)-[:DECLARES]->(m:Symbol)
   MATCH (caller:Symbol)-[c:CALLS*1..$depth]->(m)
   WHERE c.confidence >= $min_conf
     AND (NOT $exclude_external OR caller.resolved)
   RETURN caller, c  LIMIT $limit
   ```

   Method-symbol case is the same minus the `DECLARES` hop.

4. New helper `find_callees(...)` — symmetric on the outbound direction.

5. New helper `expand_methods(type_fqns: list[str], *, depth: int=1,
   min_confidence: float=0.0) -> list[str]` used by
   `search_lancedb._graph_expand_merge` to produce neighbour type FQNs via
   the method graph.

6. Add `"CALLS"` and `"DECLARES"` to the edge-type whitelist used by
   `neighbors()`. `impact_analysis()` stays **unchanged** (type-level,
   per §6.2 of the proposal).

7. `trace_flow` gains optional `follow_calls: bool = True` (breaking
   change — new default). When true, in the inner stage loop the Cypher
   pattern changes from `[e:INJECTS|EXTENDS|IMPLEMENTS]` to:
   ```cypher
   (root:Symbol)-[:DECLARES]->(m:Symbol)-[c:CALLS]->(m2:Symbol)
     <-[:DECLARES]-(n:Symbol)
   ```
   merged (UNION ALL) with the existing edge pattern. `via` edge label
   becomes `"CALLS"` when this branch produced the hit. Minimum
   confidence applies on the `CALLS` branch only.

Tests for each helper land in `tests/test_kuzu_queries.py` (see §7).

### 5. `search_lancedb.py` — graph expansion augmentation

One localised change in `_graph_expand_merge`:

```python
try:
    # Existing type-to-type expansion (EXTENDS|IMPLEMENTS|INJECTS)
    neighbor_fqns = graph.expand_fqns(seed_fqns, depth=expand_depth)
    # NEW: add types reached via method-graph
    neighbor_fqns = list(dict.fromkeys(
        neighbor_fqns + graph.expand_methods(seed_fqns, depth=expand_depth)
    ))
except Exception:
    return vector_rows
```

Dedupe via `dict.fromkeys` (preserves order, first-wins). RRF fusion is
untouched — the new FQNs simply enter the same LanceDB follow-up
select.

No new caller-visible parameters. No new ranking weights.

### 6. `server.py` — MCP tools

Additions (~80 lines, no removals):

1. New `CallEdgeDto` Pydantic model and `CallEdgeListOutput` wrapper,
   mirroring `InjectionEdgeDto` / `InjectorsOutput`.
2. New `find_callers` tool (async, uses `asyncio.to_thread` same as
   existing tools). Description must name `min_confidence` and
   `exclude_external` and give two examples (method form, type form).
3. New `find_callees` tool — symmetric.
4. `trace_flow` adds `follow_calls: bool = Field(default=True, ...)`.
   Forwards to `KuzuGraph.trace_flow(..., follow_calls=follow_calls)`.
   Breaking change: default is now `True`; callers wanting legacy
   type-only behaviour must pass `follow_calls=False` explicitly.
5. `graph_meta` — no code change; the extra counts propagate through
   `GraphMeta.counts` automatically.
6. Update `_INSTRUCTIONS` to mention `find_callers`, `find_callees`, and
   the `follow_calls` switch. Keep each sentence short; the instruction
   block is the agent's routing guide.

### 7. `README.md` updates

1. In §5 *AST Graph layer* edge-types paragraph, change `Phase 1` to
   `Phase 1 + Phase 3` and add:
   ```
   **Phase 3 (call graph):** `CALLS` (method → method, confidence-scored,
   strategy-tagged), `DECLARES` (type → method). External JDK / Spring
   calls become phantom method nodes with `resolved=false`.
   ```
2. Add a tool row for `find_callers` / `find_callees` in the Tools table.
3. Replace the §6 *Deferred* block's `CALLS` bullet with a
   "now implemented" callout; leave `HTTP_CALLS` / `ASYNC_CALLS` / agentic
   router / incremental Kuzu on the deferred list.
4. Add a `Call graph` subsection explaining:
   - The four strategies and confidence values.
   - How to filter by `min_confidence`.
   - Why phantoms aren't dropped at index time.

### 8. `docs/CODEBASE_REQUIREMENTS.md`

Add a "Call graph" note listing the tree-sitter node types the extractor
depends on:
- Call-site extraction: `method_invocation`, `object_creation_expression`,
  `method_reference`, `explicit_constructor_invocation`
- Context extraction: `lambda_expression`, `anonymous_class_body`,
  `local_variable_declaration`, `formal_parameter`
- Static import parsing: `import_declaration` with `static` modifier

This is the project's single source for "what this MCP assumes about
Java" — keep it in sync.

## Test plan

### 9.1 New test files

1. `tests/test_ast_java_calls.py` — unit tests for the 18 corner cases
   listed in `propose/completed/CALL-GRAPH-PROPOSE.md` §7.1. No tree-sitter fixture
   files needed beyond inline source snippets.
2. `tests/fixtures/call_graph_smoke/` — small Maven project (pom.xml +
   a handful of `.java` files) covering:
   - `ImportMapTest.java` — explicit import resolution
   - `SameModuleTest.java` — same-package resolution
   - `StaticImportTest.java` — explicit and wildcard static imports
   - `OverloadTest.java` — overload-ambiguous case
   - `SuperChainTest.java` — `super` call hierarchy + implicit super
   - `ConstructorChainTest.java` — constructor invocation chain
   - `MethodReferenceTest.java` — type qualifier + expression qualifier
   - `AnonymousClassTest.java` — anonymous class with internal calls
   - `LambdaTest.java` — lambda with internal calls
   - `ChainedCallTest.java` — builder/fluent API pattern (phantom case)
   - `JdkCallTest.java` — phantom JDK call (String, List)
   
   Used by both unit and integration tests.

### 9.2 Extensions to existing tests

1. `tests/test_ast_graph_build.py`:
   - New test `test_calls_and_declares_edges_populated`: asserts
     `count(CALLS) > 0` and `count(DECLARES) > 0` on the bank-chat-system
     graph.
   - Update `test_schema_has_all_expected_tables` to add `"CALLS"` and
     `"DECLARES"` to the `expected` set.
   - Update `test_graph_meta_present_and_versioned` to expect
     `ontology_version == 4`.
2. `tests/test_kuzu_queries.py`:
   - `test_find_callers_for_enqueue` — calls into `ChatManagementService.enqueue`
     include the controller method(s).
   - `test_find_callees_walks_service_boundary` — callees of a controller
     method include the injected service's methods.
   - `test_find_callers_type_form_via_declares` — passing a type FQN fans
     out through `DECLARES`.
   - `test_min_confidence_filter_drops_low_tier` — a phantom/suffix edge
     is hidden at `min_confidence=0.9`.
   - `test_exclude_external_drops_jdk` — default-on filter skips
     phantoms under `java.` / `javax.` / `jakarta.` / `org.springframework.`.
   - `test_trace_flow_follow_calls_reaches_callback` — with
     `follow_calls=True` the trace reaches
     `OperatorAssignedProcessor.onOperatorAssigned` (or equivalent) from
     an entrypoint seed.
   - `test_static_import_resolves_correctly` — static import call edges
     have `strategy='static_import'` or `'static_import_wildcard'`.
   - `test_implicit_super_edge_created` — constructor without explicit
     `this()`/`super()` has an edge with `strategy='implicit_super'`.
3. `tests/test_mcp_tools.py`:
   - Smoke tests for `find_callers` / `find_callees` through the MCP
     server wrapper (same pattern as `test_find_injectors_tool`).
   - Confirm `graph_meta` counts include `calls` and `declares`.

### 9.3 Regression

All pre-existing tests must pass (some may need updates for new default):

- Capability / brownfield / Lance e2e tests — call-graph is orthogonal.
- `test_trace_flow_from_controller_seed` — **update assertion** to expect
  call-graph-expanded results (new default `follow_calls=True`), OR
  explicitly pass `follow_calls=False` to preserve legacy assertion.
- `test_find_injectors_*` — untouched; injection edges are independent.

### 9.4 Manual validation

Run `trace_flow` on the bank-chat-system corpus with a behavioural
query ("what happens when a client message is enqueued") with and
without `follow_calls`; compare stage expansions. Document the diff in
the PR description as evidence.

**Diagnostic output verification**: After running `build_ast_graph.py` on
the corpus, verify the logged `CallResolutionStats`:
- Total call sites count is reasonable (expected: 500–2000 for bank-chat-system)
- `phantom_chained` percentage is documented in PR (flag for follow-up if >30%)
- Strategy distribution is logged and reviewed for anomalies

Include the stats output in the PR description.

## Rollout

Single PR. Breaking changes:
- Ontology bump 3 → 4 (graph rebuild required).
- `trace_flow` default behaviour changes (`follow_calls=True`).

1. Merge with `trace_flow.follow_calls` default `True`.
2. User runs `refresh_code_index(confirm=true)` (or `build_ast_graph.py
   --source-root <repo>`) once.
3. New MCP tools are live; call-graph-aware `trace_flow` is the new default.
   Users can pass `follow_calls=False` explicitly for legacy behaviour.

## Implementation step list

| # | Step | File(s) | Done when |
|---|---|---|---|
| 1 | Add `CallSite`, `FileImports` dataclasses; static import parsing; third visitor pass to `parse_java`. | `ast_java.py` | `MethodDecl.call_sites` populated on fixture file; unit tests pass. |
| 2 | Bump `ONTOLOGY_VERSION` to 4; update docstring. | `ast_java.py` | `ONTOLOGY_VERSION == 4`. |
| 3 | Extend `GraphTables` with `calls_rows` + `declares_rows` + `CallResolutionStats` (and pass-3 `methods_by_type` index). | `build_ast_graph.py` | Pass-1 finishes without regressing existing tests. |
| 4a | Implement `_scope_table` helper. | `build_ast_graph.py` | Unit test: scope table built correctly for fixture method (fields, params, locals). |
| 4b | Implement `_resolve_receiver` with full strategy cascade (static imports, this/super, import_map, same_module, unique_type_name, suffix, chained_receiver). | `build_ast_graph.py` | Unit test: each strategy path exercised. |
| 4c | Implement `_lookup_method` with supertype walk. | `build_ast_graph.py` | Unit test: inherited method found via EXTENDS. |
| 4d | Implement `_emit_call` + `_phantom_method` (with `?` prefix for unresolved receivers). | `build_ast_graph.py` | Unit test: phantom created for unknown callee; stats updated. |
| 4e | Wire helpers into `pass3_calls` loop with error handling and stats logging. | `build_ast_graph.py` | `calls_rows`/`declares_rows` populated; diagnostic stats logged. |
| 5 | Add `DECLARES` + `CALLS` to schema, drop/create, writer loops with dedup, meta counts. | `build_ast_graph.py` | Rebuild succeeds; `graph_meta.counts.calls > 0`. |
| 6 | Wire `pass3_calls` into `main`. | `build_ast_graph.py` | CLI smoke test runs pass1 → pass2 → pass3. |
| 7 | Implement `find_callers`, `find_callees`, `expand_methods`; add `CALLS`/`DECLARES` to `neighbors` whitelist; extend `trace_flow` with `follow_calls`. | `kuzu_queries.py` | Integration tests pass. |
| 8 | Augment `_graph_expand_merge` to also call `expand_methods`. | `search_lancedb.py` | Graph-expand results include method-reachable chunks on the smoke corpus. |
| 9 | Add MCP tools (`find_callers`, `find_callees`), `follow_calls` param on `trace_flow`, update `_INSTRUCTIONS`. | `server.py` | `test_mcp_tools.py` additions pass. |
| 10 | Update tests: new files + extend `test_ast_graph_build.py` / `test_kuzu_queries.py` / `test_mcp_tools.py`. | `tests/` | `pytest` green. |
| 11 | Update `README.md` + `docs/CODEBASE_REQUIREMENTS.md`. | docs | Manual review. |
| 12 | Confirm `propose/completed/CALL-GRAPH-PROPOSE.md` is the only active call-graph proposal (old deferred draft already removed; git history retains it). | `propose/` | Directory listing shows a single call-graph proposal. |

## Out of scope (for this plan, tracked elsewhere)

- Cross-service `HTTP_CALLS` / `ASYNC_CALLS`, `Route` node model
  (`tmp/what-to-borrow-from-cmm.md` §B2, §B6).
- AOP proxy resolution (`@Async`, `@Transactional`, `@Retryable`).
- `ingest_traces` runtime-trace MCP tool (B3).
- Louvain community detection (B7), dead-code detection (B8).
- Incremental Kuzu rebuild on per-file changes.

Each of the above gets its own proposal + plan once this lands and the
core call graph is proven on the `bank-chat-system` corpus.

## Done-definition

1. `build_ast_graph.py --source-root tests/bank-chat-system` produces a
   graph with `ontology_version=4`, non-empty `CALLS` and `DECLARES`.
2. `pytest` green (new + regression).
3. `refresh_code_index(confirm=true)` on a real project rebuilds the
   graph only; LanceDB data folder untouched.
4. A manual `trace_flow(...)` call (default `follow_calls=True`) on the
   corpus returns a chain that includes a method node reached through a
   `CALLS` edge (visible as `via.edge_type == "CALLS"` in at least one
   stage entry).
5. `README.md` documents the new tools and the `Phase 3` edge set.
6. **Performance**: Diagnostic stats logged after `pass3_calls`:
   - Expected scale for `bank-chat-system`: ~500–2000 `CALLS` edges,
     ~200–400 `DECLARES` edges.
   - Rebuild overhead: <30% over current graph build time.
   - Chained-receiver phantom percentage documented in PR description.
