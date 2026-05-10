# Call Graph Layer — Code Review

**Repository:** [HumanBean17/java-codebase-rag](https://github.com/HumanBean17/java-codebase-rag)
**Commits reviewed:**
- `b3a15d8` — *call graph layer propose*
- `fb5473f` — *call graph layer implementation*

**Reference docs:**
- [`propose/completed/CALL-GRAPH-PROPOSE.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/propose/completed/CALL-GRAPH-PROPOSE.md)
- [`plans/completed/PLAN-CALL-GRAPH.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/plans/completed/PLAN-CALL-GRAPH.md)

**Test status:** all 24 new call-graph tests pass locally
(`tests/test_ast_java_calls.py`, `tests/test_call_graph_smoke_roundtrip.py`,
`tests/test_call_graph_receiver_resolution.py`).

---

## Overall verdict

**Strong, faithfully-scoped implementation.** The proposal is realised as
written, the receiver-type resolver is well-structured, the schema and edge
metadata match the design (confidence + strategy + source), and the test
coverage targets concrete proposal section numbers. Scope discipline is
visible — no creep into HTTP / async / AOP / traces.

There are **three correctness bugs** that should land as a quick follow-up
before Phase 3 is closed, plus a handful of design issues worth pushing back
on. All three bugs share one root cause: **resolution strategy and
confidence are silently downgraded at edge-emit time when the receiver was
already resolved successfully.**

---

## What's done well

- **Confidence + strategy tagging is faithful to the design.** Every edge
  carries (`confidence`, `strategy`, `source='static'`) — clean migration
  path for trace ingestion later.
- **Multigraph dedup at write time** (`(src_id, dst_id, arg_count, line)`)
  is correctly shaped: prevents accidental duplication while preserving
  overload-ambiguous fan-out at distinct call sites.
- **Receiver-type resolver** is clear and matches the proposal: scope table
  built once per method, supertype-bounded lookup, explicit
  `chained_receiver` phantom path, deterministic phantom IDs.
- **Receiver-disambiguation discipline.** `_unique_type_simple_resolve`
  deliberately uses the *type* registry (not a per-method simple-name
  index). The dedicated test
  `test_receiver_disambiguation_uses_type_index_not_method_unique` is
  exactly the right kind of negative test — this is the precise trap
  CMM-style cascades fall into and the implementation avoids it.
- **`_method_ids_for_call_graph_needle`** elegantly accepts type FQN,
  method FQN, or simple method name; fan-out through `DECLARES` from a
  type needle is the right move and matches §6.1.
- **`exclude_external` is filter-on-result, not filter-on-store.** Phantoms
  stay in the graph (so impact analysis can see JDK-adjacent signals), but
  query consumers get clean lists by default. Matches risk #2 mitigation
  in the proposal.
- **Tests target proposal section numbers.** 24 tests, all passing,
  including a Kuzu round-trip on a real fixture project. The shadowing
  test (`test_local_shadows_field_same_name_resolves_receiver`) is the
  kind of edge case that bites in real codebases.
- **Diagnostics are baked in** — `pass3_calls` prints the chained-phantom
  percentage as the proposal mandates.

---

## Bugs (must fix)

### B1. Constructor calls always become phantoms when the class has no explicit constructor

**Severity: high — most common Java call site is broken.**

`new Svc()` in `ScopeReceivers.byLocal()` resolves the receiver type to
`smoke.Svc` correctly. But `Svc` has no explicit constructor in source, so
`_parse_method` is never invoked for an `<init>`, and no constructor
`MemberEntry` is created. `_lookup_method_candidates(type='smoke.Svc',
callee='<init>', argc=0)` finds nothing → fallthrough to phantom at
`confidence=0.0`.

Confirmed empirically against the smoke fixture:

```
['smoke.ScopeReceivers#byLocal()',           'smoke.Svc#<init>(0)', 'phantom', False, 0.0]
['smoke.ScopeReceivers#shadowLocalOverField()', 'smoke.Svc#<init>(0)', 'phantom', False, 0.0]
```

In a real Spring codebase, **every** `new MyDto()`, `new HashMap<>()`,
`new ArrayList<>()` on a project type without a hand-written constructor
lands as a phantom.

**Fix.** When parsing a `TypeDecl` and discovering no constructor
declaration, synthesize a default
`MethodDecl(name="<init>", signature="<init>()", is_constructor=True, ...)`
with `start_line` / `start_byte` from the type declaration and
`parameters=[]`. Make sure it gets a `MemberEntry`.

Two corollary checks:

- `_emit_call_edge` for `new Svc()` should then resolve to the synthesized
  member with `strategy='constructor'` (not `phantom`), `confidence`
  inherited from the receiver-resolution tier.
- Confirm existing `INJECTS` / `DECLARES` accounting doesn't double-count
  the synthesized node.

**Suggested test** — add to `tests/test_call_graph_smoke_roundtrip.py`
(`test_implicit_default_ctor_is_resolved`):

```java
public class HasNoCtor {}
public class Caller { void m() { new HasNoCtor(); } }
```

Assert: `(Caller#m)-[CALLS {strategy:'constructor', resolved:true}]->(HasNoCtor#<init>())`.

---

### B2. Implicit `super()` for a class that doesn't extend anything is mis-tagged as `phantom`

**Severity: medium — diagnostic regression, not a wrong answer.**

`WildUtils` has an explicit `private WildUtils() {}` constructor with no
`super(...)` body, so the AST extractor synthesizes the implicit-super
call site. `_first_supertype_fqn` returns `None` (no `EXTENDS` row →
there is no `Object` node in the index), so `_resolve_receiver_type`
returns `(None, "phantom", 0.0)`. Result:

```
['smoke.WildUtils#WildUtils()', '?super#<init>(0)', 'phantom', False, 0.0]
```

The proposal §4.2 promises strategy `implicit_super (0.90)` for this case.
Right now the agent cannot distinguish "implicit super to `Object`" from
"I have no idea what this call resolved to" — real signal loss.

**Fix.** In `_resolve_receiver_type`, when `expr == 'super'` and
`_first_supertype_fqn(...) is None`, return
`("java.lang.Object", "implicit_super", 0.90)`. In `_emit_call_edge`,
allow phantom callee (no member resolved on `Object`) but **preserve
`strategy='implicit_super'` and `confidence=0.90`** instead of overriding
to `phantom` / `0.0`. This is the same fix-shape as B3 below.

---

### B3. Resolution strategy and confidence are silently overridden to `phantom` / `0.0` when the callee can't be located on a resolved external receiver

**Severity: high — collapses static-import precision when callees are JDK / Spring.**

In `_resolve_and_emit_call`:

```python
if not candidates:
    pid = _phantom_method_id(...)
    _emit_call_edge(..., confidence=0.0, strategy="phantom", resolved=False)
    return
```

This branch fires whenever the receiver type *did* resolve (e.g.
`java.util.Objects` via `static_import`, confidence 0.95) but the callee
method isn't on a type we indexed. The static-import smoke test confirms it:

```
requireNonNull edges: 1
  phantom 0.0 False java.util.Objects#requireNonNull(1)
```

The README and the MCP instructions both tell agents to use
`min_confidence=0.9` to filter noise. Under that filter, **every JDK
static-import call disappears from the graph**, even though the resolver
*knew* the call's target type with 0.95 confidence.

**Fix.** Decouple the *receiver-resolution strategy/confidence* from the
*callee-found* boolean. When `candidates` is empty:

- Keep the phantom callee (creating it on the resolved receiver type —
  already done).
- Keep `resolved=False` on the edge (the *callee node* is a phantom).
- **Preserve the receiver-resolution `strat` and `conf`** unless they're
  `'chained_receiver'`. Specifically: `strategy` stays `'static_import'` /
  `'static_import_wildcard'` / `'import_map'` / `'same_module'` etc.;
  `confidence` stays the receiver-tier value.

The only case where `confidence=0.0, strategy='phantom'` is honest is when
the receiver itself was unresolvable. Distinguishing those two failure
modes is the whole point of the cascade.

Optional: add a small property `callee_found BOOLEAN` on the edge so a
query like *"high-confidence edges with phantom callees"* (= calls into
well-known external libraries) becomes one Cypher predicate.

**Suggested tests:**

- `test_static_import_to_jdk_keeps_high_confidence` — `requireNonNull`
  edge has `confidence>=0.95` and `strategy='static_import'`, with
  `resolved=False` on the edge.
- `test_min_confidence_filter_keeps_high_confidence_static_import_callers`
  — `find_callers('java.util.Objects#requireNonNull(1)', min_confidence=0.9)`
  returns the in-repo caller.

---

## Design issues (push back on the proposal here, not just the implementation)

### D1. Phantom-ID `arg_count` semantics are inconsistent across method-references and regular calls

`_phantom_method_id` builds the FQN as `{receiver}#{callee}({arg_count})`.
For method references the `arg_count` is `-1`. So the same external method
can exist as both `Foo#bar(2)` and `Foo#bar(-1)` phantom nodes — distinct
nodes for the same logical target. The dedup key
`(src_id, dst_id, arg_count, line)` then keeps both edges, doubling the
graph for code that mixes calls and method references on the same target.

**Recommendation.** Either normalize phantom IDs without `arg_count` for
method references (`?{recv}#{callee}(?)`) or drop `arg_count` from the
dedup key and use `(src_id, dst_id, line, byte)` (line+byte already pin a
unique call site).

---

### D2. Method-reference precision is leaving free wins on the table

Method references that *are* unambiguous on name (single method, no
overloads) currently still emit with `arg_count=-1`. Cheap precision win,
no extra resolver complexity: when the receiver type is known and exactly
one method with `name == callee_simple` exists on the receiver type, pick
that single-arity match and emit a fully-resolved edge with the receiver's
real arity instead of `-1`.

---

### D3. Anonymous-inner-class call attribution does the proposal-correct thing, but the design is questionable

Right now `pingFromAnon()` (called from inside
`new Runnable() { run() { pingFromAnon(); } }`) is attributed to
**`NestedCalls#m()`**, the enclosing named method, with
`strategy='this_super'`. That matches §4.1's wording.

But: the anonymous `Runnable` *does* get parsed as a nested type in
`_parse_type` (kind `class`). It produces a `MemberEntry` for its
`run()` method. So the graph has two contradictory facts: the call edge
goes from `NestedCalls#m`, and the structural fact "there exists a
`run()` method here" lives on a separate, disconnected anonymous type
node.

**Recommendation.** Re-attribute calls inside an anonymous-class body to
the anonymous-class member. The named-enclosing fallback is only needed
for **lambdas** (which don't synthesize a member) and static / instance
initializers. For anonymous classes, the call-site naturally belongs to
the anonymous member. This makes
`find_callers('OperatorAssignedProcessor.onOperatorAssigned')` find the
anonymous handler that actually contains the call, instead of the outer
service method.

---

### D4. `expand_methods` discards confidence on the way out

The output is `list[str]` of type FQNs. There's no way for the search-side
fusion in `_graph_expand_merge` to weight a CALLS-derived hit lower than
a structural one. The proposal §6.2 says "merged via existing RRF, no new
caller-visible parameters" — so RRF treats every reach equally regardless
of whether it came from a 0.95 import-map edge or a 0.55 suffix edge.

**Recommendation (small).** Have `expand_methods` return
`list[tuple[str, float]]` (type FQN + max confidence on the discovery
path), and let `_graph_expand_merge` pass that as the RRF rank weight.
Internal-only signature change; no MCP surface change.

---

### D5. `trace_flow`'s default change quietly rebudgets stage capacity across two qualitatively different edge sources

`follow_calls=True` is the new default. Existing agent prompts that
expected type-only stages now get extra entries with
`via.edge_type='CALLS'`. That's good — agents can infer it. But the
per-stage cap (`stage_limit`) now budgets across both edge classes, so a
high-fan-out service can starve INJECTS results in favor of CALLS results.

**Recommendation.** Either:

1. Keep separate budgets (`stage_limit_structural`, `stage_limit_calls`,
   default to `stage_limit` each), or
2. Order ingestion to prefer INJECTS / EXTENDS / IMPLEMENTS first, then
   top up with CALLS until `stage_limit`. The current code already runs
   the structural query first — just keep the CALLS top-up bounded by
   `stage_limit - len(stage_results)` instead of a separate
   `stage_limit * 4` LIMIT.

---

### D6. `_resolve_this_super_field_chain` lacks fixture coverage

The resolver line
`chain = _resolve_this_super_field_chain(expr, member=member, ast=ast, tables=tables)`
is a real bonus over what CMM does — if it walks
`this.fieldA.fieldB.fieldC.method()` correctly. Add a smoke fixture that
exercises it; none of the existing files do.

---

## Smaller nits

- **N1 — Per-call rebuild of `_scope_table`.** `_resolve_and_emit_call`
  calls `_scope_table(member, ast, tables)` on every call site.
  Field / parameter scope is identical for every call inside a single
  method body — locals only grow as you step through the body. Build it
  once per `member` in `_resolve_method_calls` and pass it in. On a
  5-microservice corpus this is the kind of constant-factor that doubles
  `pass3_calls` runtime.
- **N2 — `_lookup_method_candidates`'s `name_only` fallback rule is good,
  but the strategy logic in `_resolve_and_emit_call` is intricate.**
  The branch
  `elif name_only_fb and len(candidates) == 1: edge_strat = strat` is
  correct but easy to misread — the inline comment is good; consider
  promoting it to a docstring section.
- **N3 — `is_static_call` heuristic.** `_infer_static_method_invocation`
  returns `True` when the receiver starts with an uppercase identifier.
  For `var Foo = supplier.get();` followed by `Foo.bar()` this
  misclassifies. Rare in practice, but worth a TODO; conservative fix is
  to consult the scope table (if `Foo` is in scope as a variable, it's
  not a static call).
- **N4 — Ontology guard.** `ONTOLOGY_VERSION` 3 → 4 is set, but confirm
  `KuzuGraph.get` actually raises on `GraphMeta.ontology_version`
  mismatch at read time so a stale graph fails loudly (proposal §5.3).
- **N5 — `pass3_calls` diagnostics.** The log line reports
  chained-phantom % only. Add the `phantom_other` ratio (the bigger one
  in real codebases) so you can spot B1 / B3 regressions in the log
  immediately.
- **N6 — Method reference inside lambda.** `visit` sets
  `lam=lam or chained` for method references with a chained qualifier.
  That conflates "I'm in a lambda" with "this method ref is itself
  chained." `chained` should propagate as a separate flag, not as
  `in_lambda`.
- **N7 — Empty `expr` and `is_static_call=False` branch.** The condition
  `expr in ("", "this") or (not expr and call.is_static_call is False
  and not call.receiver_expr)` is redundant: if `expr == ""` the second
  clause is also true. Simplify to `expr in ("", "this")`.

---

## Suggested fix order

1. **B1, B2, B3 as one PR** titled
   *"call graph: faithful confidence preservation across the resolver→writer boundary"*
   — the three bugs share one architectural fix (don't downgrade
   strategy / confidence at edge-emit time when the receiver was
   resolved). Add the suggested tests in the same PR.
2. **D5 as a separate PR** — `trace_flow` budget split with a regression
   test that seeds a service whose CALLS fan-out exceeds the structural
   one.
3. **D3 (anon-class re-attribution), D4 (`expand_methods` confidence),
   N1 (scope-table caching) as a small follow-up** before opening the
   next phase.

---

## Closing note

This is solid Phase-3 work. Land the three bug fixes and the codebase is
in an excellent spot to start on the next phase — either cross-service
`HTTP_CALLS` (B6 / B7 in
[`what-to-borrow-from-cmm.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/tmp/what-to-borrow-from-cmm.md))
or runtime-trace ingestion (B3 from the same doc). Both will lean on the
resolver and confidence machinery just built; the bug fixes above make
that lean trustworthy.
