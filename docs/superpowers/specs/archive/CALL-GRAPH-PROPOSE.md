> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Call-graph layer — completed proposal

Status: **completed** — shipped (static intra-JVM `CALLS` + `DECLARES`; plan: [`plans/completed/PLAN-CALL-GRAPH.md`](../../plans/completed/PLAN-CALL-GRAPH.md)). Pairs with
[`plans/completed/PLAN-CALL-GRAPH.md`](../../plans/completed/PLAN-CALL-GRAPH.md) for the
step-by-step implementation.

This proposal realises **point 4 of `docs/PRODUCT-VISION.md`** ("Adding a Call
Graph Layer") with a deliberately narrow scope: **static, intra-JVM
method-to-method edges**. Cross-service HTTP/async, AOP-proxy resolution,
and runtime-trace ingestion are explicit non-goals of this phase.

---

## 1. Why now

The structural graph (`EXTENDS` / `IMPLEMENTS` / `INJECTS`) captures the
*wiring* of the system. It cannot answer behavioural questions whose
signal lives in **method bodies**:

- *"What happens when a client message is enqueued?"* —
  `ChatManagementService.enqueue` → `DistributionChunkService.pickEligibleOperator`
  → `OperatorAssignedProcessor.onOperatorAssigned` is a call chain whose
  intermediate classes do not co-reference each other's names in chunk
  text. No amount of lexical / symbol / role reweighting recovers it.
- *"Where is `SessionStore.close()` invoked from?"* — a pure callers query
  that requires inbound `CALLS` closure on a specific method.
- *"What's the full execution path for `/checkout`?"* — within a single
  service, this is `controller.method → service.method → repo.method`; a
  type-level INJECTS walk approximates it but loses methods and produces
  false positives through unused dependencies.

`trace_flow` today skirts this by stage-ordering **types** by role. That
is heuristic and noisy; method-to-method edges carry the real structural
fact.

---

## 2. Design decisions

The choices below drive the rest of the document. Each one is load-bearing
for answer quality or operational safety, so they are called out before
the schema and algorithms that depend on them.

| Decision | Rationale |
|---|---|
| **Confidence-scored edges, not binary `resolved`.** Every `CALLS` edge carries `confidence DOUBLE` (0.55–1.0) and `strategy STRING` (`import_map` / `same_module` / `unique_type_name` / `suffix`, plus static-import / overload / phantom markers in §4.2). | Lets downstream tools filter `confidence >= 0.8` and show *why* an edge exists. Pattern lifted from `tmp/what-to-borrow-from-cmm.md` §B1 (CMM `pass_calls.c`). |
| **`source STRING` property on every edge, frozen to `'static'` this phase.** | Adding trace-derived (`'trace'`) or DI-proxy (`'di_proxy'`) edges later becomes a data-only change, no schema migration. |
| **Receiver-type resolution is an explicit algorithm, not "best effort".** Scope-resolved variable table → type FQN → method lookup up the inheritance chain. | Written out in §4.3. The dominant Spring-style pattern (field-injected services calling interface methods) must resolve with high precision; ambiguous cases become phantoms rather than wrong edges. |
| **Every call-site kind has an explicit rule.** `this.m()` / bare `m()` / `super.m()` / `ClassName.staticM()` / `new Foo(...)` / `Foo::bar` / `this(...)` / `super(...)`. | Prevents silent misses. Constructor calls emit `CALLS` to an `<init>` node so "who instantiates X?" is answerable. |
| **Lambdas vs anonymous classes.** Calls inside a **lambda** body attribute to the enclosing named method (no synthetic callable symbol exists at index time). Calls inside an **anonymous class** `new T() { ... }` attribute to that class’s own methods (synthetic nested `TypeDecl` + `MethodDecl` entries, stable FQN per site — see §4.1). `Foo::bar` resolves to the method target when the receiver type is known, otherwise phantom. | Aligns structural symbols with `CALLS` edges so `find_callers` reaches the handler method that contains the call; lambdas keep the pragmatic enclosing-method rule. |
| **Overload policy: exact `arg_count` first; if ambiguous, emit one edge per match** with `strategy='overload_ambiguous'`. | Multigraph is fine — BFS dedupes by FQN. The strategy tag preserves diagnosability. |
| **Phantom nodes for JDK / Spring / external callees are kept, not dropped at index time.** Dropping them loses JDK-adjacent impact signals entirely. | Default-on `exclude_external=True` filter at query time (FQN prefix match on `java.` / `javax.` / `jakarta.` / `org.springframework.` / `lombok.`) keeps results clean when the caller doesn't want them. |
| **`DECLARES` thin edge from each type to its own methods / constructors.** Cheap (one per method), written at index time. | Enables single-Cypher hops *type → methods → callees → parent-types*. Without it, every such traversal becomes a two-query join through the `parent_id` scalar. |
| **`impact_analysis` stays type-level.** If a method-level variant is needed later, add `impact_analysis_methods(fqn_or_signature)` as a new tool. | Mixing method and type kinds in one reverse-closure traversal is noisy and hard to reason about. Keep tool intent narrow; agents compose them. |
| **Ontology bump `ONTOLOGY_VERSION` 3 → 4.** Graph-only rebuild required; LanceDB untouched. | Guarded by `KuzuGraph.get()`, so stale readers fail loudly instead of silently serving wrong data. |
| **Scope is static intra-JVM only.** HTTP / async / AOP / traces are explicit non-goals. | Each deserves its own proposal (see §8). Rolling them into this phase bloats the PR and conflates orthogonal design axes. |

---

## 3. Principle: additive evolution

Same posture as `plans/completed/PLAN-CAPABILITIES-MODEL.md` (the most
recent major addition to the graph layer). Nothing existing is removed
or restructured. Breaking changes to defaults are acceptable this phase
(e.g., `trace_flow` gains `follow_calls=True` as the new default).

### What stays exactly as-is

- LanceDB tables, `JavaLanceChunk` schema, CocoIndex flow. **No re-index
  required**.
- Kuzu DB file, existing node table `Symbol`, existing edges
  `EXTENDS` / `IMPLEMENTS` / `INJECTS`.
- `build_ast_graph.py`'s two-pass structure (collect → resolve). A new
  third pass is appended.
- All existing MCP tool signatures (`codebase_search`, `trace_flow`,
  `find_injectors`, `list_by_role`, …). New parameters are optional and
  default to today's behaviour.
- Ranking heuristics in `search_lancedb.py` (`_role_weight`,
  `_symbol_bonus`, `_attach_neighbor_context`). Call-graph does **not**
  introduce new ranking weights in this phase — findability first,
  ranking tuning later.

### What gets added on top

1. **AST**: per-method call-site collection in `ast_java.py`.
2. **Graph builder**: a third resolution pass in `build_ast_graph.py` +
   a new `DECLARES` rel + a new `CALLS` rel with `confidence` / `strategy`
   / `source`.
3. **Kuzu queries**: `find_callers` / `find_callees` / `expand_methods`;
   `CALLS` wired into `graph_neighbors` whitelist; `trace_flow` gains an
   optional `follow_calls` pass.
4. **Search**: `_graph_expand_merge` in `search_lancedb.py` learns about
   `DECLARES` + `CALLS` (method-aware fusion of vector seeds and
   graph-reachable chunks).
5. **MCP server**: `find_callers`, `find_callees` tools; `trace_flow`
   exposes `follow_calls: bool`; `graph_meta` includes new counts.
6. **Ontology bump**: `ONTOLOGY_VERSION` 3 → 4.

---

## 4. Design: call-site extraction & resolution

### 4.1 Extraction (Tree-sitter, third visitor pass)

Over each `method_declaration` / `constructor_declaration` body (the AST
body as returned by tree-sitter-java), collect every call-site node. Relevant
tree-sitter node types:

- `method_invocation` — `foo()`, `this.foo()`, `receiver.foo()`, `Class.foo()`.
- `object_creation_expression` — `new Foo(...)`.
- `method_reference` — `Foo::bar`, `this::bar`, `expr::bar`.
- `explicit_constructor_invocation` — `this(...)`, `super(...)`.

For each call-site, capture a raw, language-model-friendly record:

```python
@dataclass
class CallSite:
    caller_fqn: str          # enclosing method/constructor FQN + signature
    receiver_expr: str       # raw source text of the receiver, "" for bare calls
    callee_simple: str       # "foo" in "bar.foo(args)"; "<init>" for constructors
    arg_count: int           # fixed-arity; varargs count literal args at site
    is_static_call: bool     # ClassName.foo() — receiver is a type, not a value
    is_constructor: bool     # new Foo(...), this(...), super(...)
    in_lambda: bool          # true only when the site is nested under a `lambda_expression` body
    line: int
    byte: int
    chained_method_reference: bool  # true for `expr::name` where `expr` is a method_invocation (chained qualifier)
```

**Lambda bodies**: Walked during collection on the enclosing named method;
`in_lambda=True` marks sites nested under a `lambda_expression` (receiver/`this`
semantics follow the lambda’s captured scope). **Do not** conflate this with
expression-qualified method references (`getX()::trim`): those set
`chained_method_reference=True` and keep `in_lambda=False` unless the reference
text itself sits inside a lambda body.

**Anonymous inner classes** (`new SuperType() { ... }` with `class_body`):
Tree-sitter exposes the body as `class_body` under `object_creation_expression`,
not as a normal nested `class_declaration`. The indexer **materialises** a
synthetic nested `TypeDecl` per anonymous creation site (deterministic id,
e.g. `Outer.<anon>:byte` or `Outer$N` keyed by `start_byte` within the file),
parses its methods/ctors/fields with the same rules as named nested types, and
collects call sites on **those** `MethodDecl` rows (`caller_fqn` =
`synthetic.Fqn#methodSig`). The outer method’s `_collect_call_sites` walk
**does not** recurse into anonymous `class_body` for call-site emission (avoids
duplicate/contradictory edges vs the synthetic type). Constructor `new T() { }`
still records the `new` / `<init>` site on the enclosing method as today.

Rationale (D3): attributing anonymous-class calls to the outer method produced
`CALLS` from `Outer#m` while the graph had no method node for the code that
actually runs inside `run()` — poor `find_callers` behaviour for listener-style
handlers. Lambdas keep outer-method attribution because there is no method
symbol for the lambda body unless we add a future synthetic “lambda$” model.

**Implicit super constructor**: If a constructor body has no explicit
`this(...)` or `super(...)` invocation, Java inserts an implicit `super()`.
The extractor synthesizes a `CallSite` for this case:
`callee_simple='<init>'`, `arg_count=0`, `receiver_expr='super'`,
`is_constructor=True`. This ensures "who instantiates X via inheritance?"
queries see the full chain.

**Static imports**: The file's import list is parsed to track:
- Explicit static imports: `import static com.example.Utils.helper` →
  `static_methods["helper"] = "com.example.Utils.helper"`.
- Wildcard static imports: `import static com.example.Utils.*` →
  `static_wildcards.append("com.example.Utils")`.

A bare call `helper()` is checked against `static_methods` before falling
back to `this.helper()`. This prevents misattributing statically-imported
calls to the enclosing type.

### 4.2 Resolution cascade (confidence-scored)

Adapted from CMM (`pass_calls.c`, `extract_calls.c`). Each strategy emits a
confidence and a strategy label on the edge.

**Primary resolution strategies** (receiver-type resolution):

1. **`static_import` (0.95)** — bare call matches an explicit static import.
   The FQN is known directly from the import statement.
2. **`static_import_wildcard` (0.85)** — bare call matches a method in a
   wildcard-static-imported type. Lower confidence because we must search
   the target type's static methods.
3. **`import_map` (0.95)** — receiver type resolves directly via the
   current file's `explicit_imports`, or the receiver is `this` / `super`
   / a same-file sibling.
4. **`same_module` (0.90)** — receiver type resolves via same-package
   lookup (`<current.package>.<ReceiverType>`) or wildcard-import
   completion.
5. **`unique_type_name` (0.75)** — static qualifier or other unresolved simple
   identifier matched **exactly one** indexed type by simple name (`decl.name`,
   via the same type registry as import / same-package resolution). The builder
   does **not** use a per-method simple-name index for this step (a globally
   unique *method* name is not evidence about receiver type).
6. **`suffix` (0.55)** — last-resort: choose the single type whose FQN
   ends with the receiver's simple name. Skip when >1 candidate.

**Special-case strategy markers** (used alongside or instead of the above):

- **`this_super` (0.95)** — explicit `this.m()`, `super.m()`, or bare call
  resolved to enclosing type.
- **`implicit_super` (0.90)** — synthesized edge for implicit `super()`
  constructor call.
- **`constructor` (inherits receiver confidence)** — `new Foo(...)` or
  explicit `this(...)`/`super(...)`.
- **`method_reference` (inherits receiver confidence)** — `Foo::bar` or
  `var::bar` where receiver is resolvable; when callee lookup yields a unique
  match, the CALLS row stores that method’s arity (see §4.3).
- **`overload_ambiguous` (inherits receiver confidence)** — multiple
  method candidates match by name but not by arity; one edge per candidate.
- **`chained_receiver` (0.0)** — receiver is a chained expression
  (`a.b().c()`) or a method reference with expression qualifier
  (`getX()::bar`); emits phantom.
- **`phantom` (0.0)** — callee not found in resolved receiver type or
  any supertype.

**Phantom FQN format**:

| Case | Format | Example |
|------|--------|---------|
| Receiver resolved, method not found | `{receiver_fqn}#{callee}(?)` | `com.example.Svc#unknown(?)` |
| Receiver unresolved (chained/unknown) | `?{receiver_expr_short}#{callee}({arg_count})` or `…(?)` if `arg_count < 0` | `?a.b()#doThing(1)`, `?s#length(?)` |

The `?` prefix makes phantoms visually distinct and grep-able. The receiver
expression is truncated to 50 characters. Phantom nodes are reused
(deterministic id via `phantom_id(fqn)`) — same pattern as existing phantom
type handling.

When the receiver type FQN is known but the callee is not indexed, the phantom
**node** identity is arity-agnostic (`(?)`) so method references (extractor
`arg_count = -1` at the site) and ordinary invocations with a concrete arity share one
`Symbol` per `(receiver_fqn, callee)` (D1). The **`CALLS` edge** carries the
call-site arity when only the extractor knows it; unresolved method references and
phantom callees keep `-1` on the edge where arity is unknown. When a method reference
resolves to indexed callees, each emitted edge stores that candidate’s parameter
count on `CALLS.arg_count` (unique match: one edge with `strategy='method_reference'`;
overload-ambiguous: one edge per candidate with `strategy='overload_ambiguous'`) so
needles and `min_confidence` filters stay precise (D2).

`find_callers` / `find_callees` accept needles with a numeric arity suffix
(e.g. `java.util.Objects#requireNonNull(1)`); if no symbol matches exactly, the
query layer retries with that suffix replaced by `(?)` so it resolves to the
same phantom node.

### 4.3 Receiver-type resolution

Core algorithm, per enclosing method:

1. **Build a scope table** on entry to the method body:
   - Enclosing type + all accessible fields (own + inherited from resolved
     supertypes).
   - Method parameters.
   - Local variable declarations encountered so far (tree-sitter yields
     `local_variable_declaration` nodes; capture the declared type's simple
     name).
2. **For each `method_invocation`**:
   - **Bare call** (`foo(args)`): receiver = `this` → enclosing type.
   - **`this.foo(args)`**: same as bare.
   - **`super.foo(args)`**: receiver = first resolved supertype of
     enclosing.
   - **`ReceiverExpr.foo(args)`**:
     - If `ReceiverExpr` is a single identifier: look up in scope table
       → local var → param → field → (finally) as a type name for
       `StaticCall` detection.
     - If it's a chained expression (`a.b().c()`) we *do not* attempt
       return-type inference this phase — phantom with `confidence=0.0`
       and `strategy='chained_receiver'`.
     - If `ReceiverExpr` is a **`this` / `super` field chain** (only `.`
       segments, no `(` / `)` — e.g. `this.fieldA.fieldB.method()` or
       `super.fieldA.fieldB.method()`): walk successive `TypeDecl` fields from
       the enclosing type (or first resolved supertype for `super`) to the
       final field’s declared type, then callee lookup on that type. Same
       `import_map` tier as a single-field receiver when the walk succeeds.
   - **`new Foo(args)`**: receiver type = resolved `Foo`; callee = `<init>`;
     `arg_count` must match (if a constructor with that arity exists).
   - **`Foo::bar`** method reference: the extractor sets `CallSite.arg_count=-1`
     (indeterminate at parse time). Receiver type = resolved `Foo` (same rules as
     invocations). After callee lookup on the type + supertype walk: if exactly one
     method named `bar` is found, emit one edge with `strategy='method_reference'`,
     `resolved=true`, and `CALLS.arg_count` equal to that method’s parameter count;
     if several overloads match by name only, emit one `overload_ambiguous` edge per
     candidate, each with that candidate’s arity on `CALLS.arg_count`; if none match,
     phantom callee as usual, with `CALLS.arg_count=-1`. Confidence follows receiver
     resolution in all resolved cases.
   - **`expr::bar`** method reference with expression qualifier (e.g.,
     `getX()::bar`): treat as chained receiver — emit phantom with
     `strategy='chained_receiver'`, `confidence=0.0`.
3. **Callee lookup**: once a receiver type FQN is known, search its own
   methods by `(name, arg_count)`; if no arity match, fall back to
   `name` (`strategy='overload_ambiguous'`); if not found locally, walk
   `EXTENDS`/`IMPLEMENTS` closure up (bounded to phantom or resolved
   supertypes the graph already knows). Emit one edge per match; if zero
   matches, emit a phantom callee under the known receiver type FQN using the
   arity-agnostic phantom format above (site arity remains on the `CALLS` row).

This is intentionally conservative: it recovers the dominant Spring-style
pattern (field-injected services calling interface methods) with high
precision and punts ambiguous cases to phantoms rather than guessing.

### 4.4 Ambiguity and multi-edge policy

- **Overloads with distinct arity**: pick the single match.
- **Overloads with same arity (different param types)**: emit one edge
  per candidate, `strategy='overload_ambiguous'`, each at the resolved
  confidence tier. BFS de-duplicates by FQN, so downstream tools aren't
  double-counting.
- **Polymorphic dispatch** (`ServiceInterface.foo()` at the call site,
  where one or more classes implement the interface): emit an edge to
  the **interface method** (high-confidence); separately, the existing
  `IMPLEMENTS` edges let the agent fan out to implementors when needed.
  Do **not** pre-expand to all implementors — that would explode the
  graph and duplicate work the agent can do on demand.

---

## 5. Schema changes

### 5.1 New edge tables

```sql
-- Cheap, exactly one per method/constructor node. Enables efficient
-- type -> method -> callee -> type hops in a single Cypher query.
CREATE REL TABLE DECLARES (
    FROM Symbol TO Symbol
);

-- The call graph. Multigraph: overload-ambiguous sites emit one edge
-- per candidate. A dedup on (caller_id, callee_id, arg_count, line)
-- is applied at write time.
CREATE REL TABLE CALLS (
    FROM Symbol TO Symbol,
    call_site_line  INT64,
    call_site_byte  INT64,
    arg_count       INT64,
    confidence      DOUBLE,    -- 0.0 .. 1.0; 0.0 = phantom
    strategy        STRING,    -- 'import_map' | 'same_module' | 'unique_type_name'
                               -- | 'suffix' | 'static_import' | 'static_import_wildcard'
                               -- | 'method_reference'
                               -- | 'overload_ambiguous' | 'chained_receiver'
                               -- | 'this_super' | 'constructor' | 'phantom'
    source          STRING,    -- 'static' | 'trace' | 'di_proxy' — 'static' this phase
    resolved        BOOLEAN    -- false when the callee is a phantom
);
```

### 5.2 `Symbol` node — no column changes in this phase

The existing `resolved` field already marks phantoms. No need to add
`external` as a dedicated column yet; `resolved=false` + FQN-prefix
heuristics (`java.`, `javax.`, `jakarta.`, `org.springframework.`) cover
the filter cases we'll expose.

### 5.3 Ontology version bump

`ONTOLOGY_VERSION` **3 → 4** in `ast_java.py`.

`GraphMeta.ontology_version` is consulted on read (`KuzuGraph.get`). Graphs
built at v3 are refused with a clear error pointing at
`refresh_code_index` or `build_ast_graph.py`. LanceDB is **not** re-indexed —
this is a graph-only migration.

---

## 6. Tool surface

### 6.1 New MCP tools

| Tool | What it does |
|---|---|
| `find_callers(fqn_or_signature, depth=1, limit=100, min_confidence=0.0, exclude_external=true)` | Inbound `CALLS` closure. Accepts a type FQN (fan-out through `DECLARES`), a method simple name (+ optional `arg_count`), or a full `type#sig`. For phantom callees with arity-agnostic FQNs (`…#name(?)`), a needle ending in `(N)` with digits-only `N` resolves to the same symbol after an exact-match miss (§4.2). |
| `find_callees(fqn_or_signature, depth=1, limit=100, min_confidence=0.0, exclude_external=true)` | Outbound `CALLS` closure. Same input shapes and needle fallback as `find_callers`. |

Both return `SymbolDto` for each hit **plus** per-edge metadata:
`confidence`, `strategy`, `call_site_line`, `call_site_byte`, `arg_count`, `resolved`.

### 6.2 Extended tools

| Tool | Change |
|---|---|
| `trace_flow` | New optional `follow_calls: bool = True`. When set, each stage's BFS adds `CALLS` alongside `INJECTS | EXTENDS | IMPLEMENTS` on method nodes reachable from the stage's types (via `DECLARES`). Defaults to `True` — breaking change from previous behaviour, but the call-graph-aware trace is the intended primary mode. `stage_limit` is shared with structural-first priority: per hop, `INJECTS` / `EXTENDS` / `IMPLEMENTS` results fill the budget first, and the `CALLS` branch only tops up the remaining slots (no separate `stage_limit_calls` knob). |
| `graph_neighbors` | `CALLS` added to the edge-type whitelist. Depth / direction / limit unchanged. |
| `impact_analysis` | **Unchanged** — stays type-level. A separate `impact_analysis_methods` may ship later if needed. |
| `graph_meta` | `counts` gains `calls` and `declares`. |
| `codebase_search` (via `graph_expand=true`) | `_graph_expand_merge` adds a method-aware path: type seed → `DECLARES` → `CALLS` → `DECLARES(reverse)` → type. Internal helper `expand_methods` returns `(type_fqn, path_confidence)` (max over paths of the min `CALLS.confidence` along each path; structural `expand_fqns` neighbours use weight `1.0`); graph-expanded chunk rows scale their RRF contribution by that weight. Merged via existing RRF. No new caller-visible parameters. |

### 6.3 Agent instructions (`_INSTRUCTIONS`)

Update the MCP server's instruction block to mention `find_callers` /
`find_callees` and the `follow_calls` switch. Keep the existing tool-per-
capability framing so the agent picks the right tool per sub-question
(same principle as `tmp/what-to-borrow-from-cmm.md` §*Skip: `get_architecture`
mega-tool*).

---

## 7. Testing strategy

### 7.1 Unit — pure AST extraction

New `tests/test_ast_java_calls.py`:

1. Bare call → receiver=this, enclosing-type resolution.
2. `this.m(a,b)` → same as #1 with `arg_count=2`.
3. `super.m()` → receiver is first resolved supertype.
4. `svc.m()` where `svc` is a field of type `UserService` → receiver FQN
   = `UserService`, strategy `import_map`.
5. `svc.m()` where `svc` is a ctor param → same result.
6. `svc.m()` where `svc` is a local variable → same result.
7. `Utils.helper()` (static) → receiver type = `Utils`, `is_static=True`.
8. `new Foo(x)` → callee `<init>`, `arg_count=1`, `is_constructor=True`.
9. `Foo::bar` method reference → `CallSite.arg_count=-1`; graph edge
   `strategy='method_reference'` and, when callee lookup finds a unique match,
   `CALLS.arg_count` equals that method’s arity (see §4.3).
10. Chained `a.b().c()` → first call resolved, second emits phantom with
    `strategy='chained_receiver'`.
11. Lambda body call attributed to enclosing method (not the lambda).
12. Overload with distinct arities → exact edge picked.
13. Overload same arity, same name → two edges with
    `strategy='overload_ambiguous'`.
14. Static import `import static com.example.Utils.helper; ... helper();`
    → resolves to `Utils.helper`, `strategy='static_import'`.
15. Wildcard static import `import static com.example.Utils.*; ... helper();`
    → resolves to `Utils.helper`, `strategy='static_import_wildcard'`.
16. Anonymous inner class: calls inside the class body are collected on the
    synthetic nested type’s methods (`caller_fqn` = that member), not on the
    enclosing named method; the outer method still has the `new` / `<init>`
    call site for the anonymous instance.
17. Constructor with no explicit `this()`/`super()` → synthesized edge to
    supertype `<init>(0)` with `strategy='implicit_super'`.
18. Method reference with expression qualifier `getX()::bar` → phantom with
    `strategy='chained_receiver'`.

### 7.2 Integration — Kuzu round-trip (bank-chat-system corpus)

Extend `tests/test_kuzu_queries.py`:

1. `find_callers('ChatManagementService.enqueue')` contains the REST
   controller methods that call it.
2. `find_callees('ChatManagementService.enqueue')` contains
   `DistributionChunkService.pickEligibleOperator` (or equivalent).
3. `find_callers('AssignChatRepository')` (type-form) fans out through
   `DECLARES` and returns the services that call any method of the
   repository, not only those that inject it.
4. `min_confidence=0.9` filter drops `suffix`/`unique_type_name` edges.
5. `exclude_external=True` skips phantom JDK/Spring callees.
6. `trace_flow(query=..., follow_calls=True)` reaches
   `OperatorAssignedProcessor.onOperatorAssigned` from
   `ChatManagementService.enqueue` in ≤3 hops; compare to the baseline
   `follow_calls=False` result to confirm the new path is additive.
7. `graph_meta().counts` includes `calls > 0` and `declares > 0`.
8. `GraphMeta.ontology_version == 4` after rebuild.

### 7.3 Regression

All existing tests (`tests/test_*.py`) must pass (some may need updates
for new defaults). Specifically:

- `test_each_edge_type_populated` — still asserts EXTENDS/IMPLEMENTS/INJECTS
  populated; add a parallel assertion for CALLS/DECLARES in a new test.
- `test_find_injectors_*` — untouched.
- `test_trace_flow_from_controller_seed` — **update assertion** to expect
  call-graph-expanded results (new default `follow_calls=True`), OR
  explicitly pass `follow_calls=False` to preserve legacy assertion.

### 7.4 Micro-fixture for resolution corner cases

New `tests/fixtures/call_graph_smoke/` (small Maven project) covering:

- all resolution strategies (import_map, same_module, unique_type_name, suffix),
- static imports (explicit and wildcard),
- a phantom (JDK call),
- an overload-ambiguous case,
- a `super`-call hierarchy,
- a constructor-invocation chain (explicit and implicit super),
- a method reference (type qualifier and expression qualifier),
- an anonymous inner class with internal calls,
- a lambda with internal calls,
- **`this` / `super` field-chain receivers** (`this.root.mid.inner.target()`,
  `super.root.mid.inner.target()`) exercising `_resolve_this_super_field_chain`.

Used by `test_ast_java_calls.py` (parse-only) and a lightweight Kuzu
round-trip test.

---

## 8. Non-goals (explicit, deferred to future phases)

- **Cross-service `HTTP_CALLS`** (Feign, `RestTemplate`, `WebClient`):
  needs the `Route` node + cross-repo matching pass
  (`tmp/what-to-borrow-from-cmm.md` §B2/B6). Separate proposal.
- **`ASYNC_CALLS`** (Kafka, Rabbit, JMS, Spring messaging): same layer
  as HTTP — shares the `Route` node model.
- **AOP proxy resolution.** Spring `@Async`, `@Transactional`, `@Retryable`
  go through CGLIB / JDK proxies at runtime; static call graphs cannot
  see the indirection. Requires either bytecode analysis or trace
  ingestion. Not this phase.
- **Runtime trace ingestion** (`ingest_traces` from CMM). Huge quality
  lever, but orthogonal: the schema already carries `source` so that
  landing traces later is a data-only change.
- **Polymorphic dispatch expansion.** The static graph connects the call
  site to the declared receiver type; agents expand to implementors via
  existing `IMPLEMENTS` edges. A future `follow_polymorphic=True` could
  bake this in, but the default precision win is better kept opt-in.
- **Data-flow analysis** (which field a call mutates, taint tracking,
  null propagation). Needs a proper semantic pass, not tree-sitter.
- **Incremental Kuzu updates.** Graph is still a full rebuild this
  phase, consistent with Phase 1.
- **Louvain community detection / dead-code detection**
  (`tmp/what-to-borrow-from-cmm.md` §B7/B8). Unlocked by this phase but
  deferred — separate proposals / plans.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Graph size blow-up (CALLS ≈ O(N_methods × avg_calls/method)). | Edge-only payload (fixed columns, no STRING blobs beyond short labels). Kuzu handles multi-million-edge graphs trivially on this codebase size. Per-query caps via `depth`/`limit` stay authoritative. |
| Phantom explosion from JDK calls (`String`, `List`, streams). | `resolved=false` + FQN-prefix exclusion (`java.` / `javax.` / `jakarta.` / `org.springframework.` / `lombok.`) at **query time** keeps the stored graph honest but lets agents filter. Don't drop phantoms at index time — losing them means losing JDK-adjacent impact signals entirely. |
| Receiver-type resolution imprecision on chained calls / generics. | Explicit `strategy='chained_receiver'`, `confidence=0.0`. Agents can filter via `min_confidence`. |
| Overload-ambiguous edges inflate BFS fan-out. | BFS dedupes by destination FQN; `overload_ambiguous` edges collapse naturally. |
| Method-reference & lambda bodies miss some calls. | Tree-sitter walks both; method references resolve to receiver-type + simple name. Good enough for Phase 1 — not perfect. |
| Rebuild cost on large codebases. | Expected 15–30 % over the current graph build (receiver-type table is already in memory from pass 2). No incremental update support this phase — consistent with current behaviour. |
| Ontology bump breaks existing graphs. | `GraphMeta.ontology_version` guard in `KuzuGraph.get` raises a clear error; `refresh_code_index` (with `LANCEDB_MCP_ALLOW_REFRESH=1`) rebuilds graph only (Lance unchanged). |
| Tree-sitter parse failure on malformed method body. | Per-method `try/except` with warning log; the method is skipped, rest of file proceeds. File-level failures log error and continue to next file. No partial graph state — `_drop_all` at start ensures clean slate on re-run. |
| High chained-receiver phantom rate in builder-heavy codebases. | Diagnostic counter logs phantom percentage after `pass3_calls`. If >30%, consider follow-up heuristics (e.g., builder `return this` detection). |

---

## 10. Effort estimate

- Implementation: **2 focused working days**
  (1 day: AST extractor + resolver; 0.5 day: Kuzu schema + writer + queries;
  0.5 day: server surface + search-side expansion).
- Validation on `bank-chat-system` + micro-fixture: **1 day**
  (unit + integration + regression run; manual trace_flow spot-checks).
- Documentation update (`README.md`, `docs/CODEBASE_REQUIREMENTS.md`, MCP
  instructions): **2 hours**.

Total: **3–4 working days** including tests and docs.

---

## 11. Acceptance criteria (summary)

1. `build_ast_graph.py` produces a Kuzu DB with populated `CALLS` and
   `DECLARES` tables at `ONTOLOGY_VERSION=4`.
2. `find_callers` / `find_callees` return non-empty, correct answers on
   the `bank-chat-system` fixture for at least three hand-checked cases.
3. All pre-existing tests pass unchanged (capability / brownfield / kuzu /
   MCP / lance-e2e tests).
4. `trace_flow()` (default `follow_calls=True`) reconstructs the
   `enqueue → pick → assign` chain on the fixture; `follow_calls=False`
   can be passed explicitly to get the legacy type-only behaviour.
5. `codebase_search(graph_expand=True)` response rows can be dominated
   by method-body chunks reachable only through `CALLS` — a query that
   used to miss the target chunk hits it.
6. No LanceDB reindex required.
