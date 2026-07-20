<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Plan: brownfield role / capability overrides

Status: **completed** — shipped (`BrownfieldOverrides` role/capability layers on `master`). Self-contained: an agent picking
this up should be able to land it without re-deriving the design.

## Goal

Make role and capability inference robust on real-world Java codebases
where the annotation/suffix detectors in `ast_java.py` fall short:

- Wrapper stereotypes (e.g. `@AcmeService` meta-annotated with `@Service`,
  `@CompanyKafkaTopic` meta-annotated with `@KafkaListener`).
- Custom DI frameworks (Guice, Dagger, hand-wired Spring `@Bean` factories).
- Legacy code with no marker annotation but a clear behavioural role.
- Non-Spring stacks (plain JAX-RS, Jakarta EE, Micronaut) mixed in with
  Spring code.
- Vendored / frozen modules where source cannot be modified.

The escape hatch is delivered in three **layers**, each more invasive
than the last. They are complementary; the same merge point in
`graph_enrich.py` consumes all three.

| Layer | Mechanism | User effort | Phase |
|---|---|---|---|
| **B** | Workspace YAML config maps annotations / FQNs / wrappers to roles & capabilities | none on source — config-only | **Phase 1, MVP** |
| **A** | Indexer walks meta-annotations transitively (`@AcmeService` → `@Service` → SERVICE) | none — automatic | **Phase 2** |
| **C** | Explicit `@CodebaseRole("SERVICE")` / `@CodebaseCapability("MESSAGE_LISTENER")` annotations, matched by simple name without a jar dependency | one-line declaration + per-class annotation | **Phase 3, polish** |

Implementation order is deliberate: B unblocks the most users with the
least code; A reduces the manual mapping burden after B exists; C is the
last-resort hatch for cases where neither B nor A applies.

This plan covers all three phases in one document so the agent
implementing it has the complete picture; each phase is independently
shippable.

## Pre-conditions

- `PLAN-CAPABILITIES-MODEL.md` is merged. The `capabilities` field exists
  on `TypeDecl`, `Symbol` (Kuzu), and `SymbolHit`. Without it, layers B
  and C cannot express capability overrides cleanly.

### Pre-flight fix — close `JavaLanceChunk` write-path gap

Surfaced during this plan's implementation pass: the capabilities-model
PR added `"capabilities"` to `JAVA_ENRICHED_COLUMNS` (read side) and to
`ChunkEnrichment` (intermediate), **but did not propagate it through
`JavaLanceChunk` and `process_java_file` (write side)**. As a result,
the `capabilities` column is silently absent from the LanceDB Java
table, and the read-side schema-presence guard
`[c for c in JAVA_ENRICHED_COLUMNS if c in enriched_cols]` quietly drops
the request. Every `codebase_search` capability filter and every
`CodeChunkHit.capabilities` field is therefore a no-op until this is
closed.

Brownfield work depends on this fix: a user-overridden capability that
flows to Kuzu but not to LanceDB makes `codebase_search` blind to the
override while `list_by_capability` (Kuzu) sees it correctly. Users
will report it as a brownfield bug; it isn't.

**Required edits before Phase 1's resolver work begins.** Attribute
clearly in the commit as a capabilities-plan gap:

1. **`java_index_flow_lancedb.py`** — add `capabilities: list[str]` to
   `JavaLanceChunk` next to `role`. Match the list-column declaration
   pattern used by `annotations_on_type` / `symbols`.

2. **`process_java_file`** — propagate from `ChunkEnrichment`:
   ```python
   row=JavaLanceChunk(
       ...
       role=enrich.role,
       capabilities=list(enrich.capabilities),
       ...
   )
   ```

3. **`graph_enrich.enrich_chunk`** — the no-enclosing-type fallback
   branch currently omits `capabilities`; add `capabilities=[]` for
   symmetry with the populated branch.

4. **No `ONTOLOGY_VERSION` bump.** The Kuzu graph schema is unchanged.
   LanceDB tables built before this fix lack the column; the
   read-side guard handles that gracefully (filter ignored,
   `CodeChunkHit.capabilities` empty for stale rows). Users get the
   column populated on the next `refresh_code_index`.

5. **Tests (tiered, pick-and-choose).** Split the regression guard
   into three independent tests so cheap CI doesn't pay for the heavy
   tier on every PR:

   - **Tier 1 — schema (cheap, runs on every PR).** Pure Python
     introspection of `JavaLanceChunk`: assert `capabilities` is
     declared with the same Arrow list-string type as
     `annotations_on_type` / `symbols`. No filesystem, no LanceDB.
   - **Tier 2 — write-path unit (medium, runs on every PR).** Call
     `process_java_file` directly on an in-memory fixture; assert the
     produced `JavaLanceChunk` row carries
     `capabilities == ["MESSAGE_LISTENER"]` (or whatever the fixture
     sets). No actual LanceDB writes.
   - **Tier 3 — end-to-end (heavy, optional / nightly).** Build a
     fresh LanceDB index over a fixture with `fqn.com.legacy.X.role:
     SERVICE` and `fqn.com.legacy.X.capabilities: [MESSAGE_LISTENER]`,
     query the table directly, assert the row carries the
     capability, then call
     `codebase_search(query="...", capability="MESSAGE_LISTENER")`
     and assert the class appears. This is the only tier that
     exercises the LanceDB list-predicate syntax end-to-end; gate it
     behind a marker so per-PR CI can skip if too slow.

   Tier 2 is the smallest test that proves the write-path is
   wired correctly. Tier 3 is necessary as a one-time validation but
   does not need to run on every commit.

## Principle: pure evolution, additive

Same posture as `PLAN-CAPABILITIES-MODEL.md`. Existing callers see no
behavioural change unless they opt in (config file, meta-annotation,
explicit override).

## Single merge point

All three layers feed into one place: a new function in `graph_enrich.py`
that takes the AST-derived `(role, capabilities)` tuple and returns the
final `(role, capabilities)` after override resolution.

```python
# graph_enrich.py
def resolve_role_and_capabilities(
    type_decl: TypeDecl,
    *,
    overrides: BrownfieldOverrides,
    meta_chain: dict[str, set[str]] | None = None,  # Layer A index, None when Phase 2 not landed
) -> tuple[str, list[str]]:
    """Compose AST inference with brownfield overrides.

    Execution order (REQUIRED, do not reorder). Each step runs against
    the role/cap state produced by the previous step — never against a
    fresh AST baseline. Steps later in the list override earlier ones
    whenever they fire. "Priority" is therefore exactly "position in
    this list" (last to fire = highest priority); a separate priority
    axis is intentionally not maintained.

      1. built-ins                  -- infer_role_for_type / infer_capabilities_for_type
      2. Layer B annotations        -- cfg.annotation_to_role / annotation_to_capabilities
      3. Layer A meta-walk          -- meta_chain (Phase 2; no-op if meta_chain is None)
      4. Layer C in-code annotations -- @CodebaseRole / @CodebaseCapability
      5. Layer B per-FQN            -- cfg.fqn_role / fqn_capabilities

    Role mutation rule. Each guarded step (2 and 3) checks
    `if role == "OTHER"` against the *current* role at the moment it
    runs. Step 2 (user config) runs before step 3 (auto meta-walk), so
    if both target the same class, user config wins: step 2 sets a
    non-OTHER role, step 3's guard fails, step 3 is skipped. Steps 4
    and 5 are unconditional and run last, overriding anything.

    Capability mutation rule. Every layer is unconditionally additive
    (set union). Capabilities never conflict; ordering doesn't matter
    semantically, but the resolver always returns `sorted(caps)` so
    the on-disk representation is deterministic.

    See PLAN-BROWNFIELD-ROLE-OVERRIDES.md §"Resolver execution order"
    for the full table with both role and capability halves shown
    side-by-side.
    """
```

`BrownfieldOverrides` is loaded once per project root, cached, and
passed in by the build pipeline. `ast_java.py` stays I/O-free and
unaware of overrides.

The build pipeline (`build_ast_graph.py`) calls this resolver instead of
calling `infer_role_for_type` / `infer_capabilities_for_type` directly.

---

## Phase 1 — Layer B: external mapping config (MVP)

### Where the config lives

Extend the existing `.lancedb-mcp.yml` / `.lancedb-mcp.yaml` file at
`project_root` (the same file `graph_enrich.load_microservice_overrides`
already reads from). Do **not** introduce a new file.

### Schema

```yaml
microservice_roots: [...]   # existing key, unchanged

role_overrides:
  # Wrapper-annotation simple names → primary role
  annotations:
    AcmeService: SERVICE
    CompanyController: CONTROLLER
    LegacyDao: REPOSITORY

  # Wrapper-annotation simple names → capabilities (list)
  capabilities:
    CompanyKafkaTopic: [MESSAGE_LISTENER]
    AcmeBatch: [SCHEDULED_TASK]

  # Per-FQN explicit overrides (highest precedence)
  fqn:
    com.legacy.OrderProcessor:
      role: SERVICE
      capabilities: [MESSAGE_LISTENER]
    com.acme.payments.PaymentEventBus:
      capabilities: [MESSAGE_PRODUCER]   # role unspecified → keep AST-inferred
```

All three sub-sections are optional. Unknown role / capability strings
log a warning at load time and are ignored (don't crash the build).

### Loader

In `graph_enrich.py`, mirror the existing `_load_config_microservice_roots`
pattern:

```python
@dataclass(frozen=True)
class BrownfieldOverrides:
    annotation_to_role: dict[str, str]
    annotation_to_capabilities: dict[str, tuple[str, ...]]
    fqn_role: dict[str, str]
    fqn_capabilities: dict[str, tuple[str, ...]]

@lru_cache(maxsize=64)
def _load_brownfield_overrides(project_root_str: str) -> BrownfieldOverrides:
    """Read `role_overrides` from `.lancedb-mcp.yml`. Cached per project_root."""
    ...
```

Same failure mode as the existing config loader: missing file, missing
key, malformed YAML, or missing PyYAML all silently produce an empty
`BrownfieldOverrides`. Config is strictly opt-in.

A known set of valid roles/capabilities is checked against
`ast_java.ROLE_ANNOTATIONS.values()` ∪ a hardcoded capability set;
unknowns are dropped with a `print(..., file=sys.stderr)` warning.

### Resolver behaviour for Layer B

Phase 1 lands two layers of the resolver: built-ins (already there via
`ast_java.py`) and Layer B (annotation map + FQN override). Layers A
and C land in Phases 2 and 3 respectively. The skeleton must already
be ordered correctly so subsequent phases plug in without reshuffling.

```python
def resolve_role_and_capabilities(
    type_decl: TypeDecl,
    *,
    overrides: BrownfieldOverrides,
    meta_chain: dict[str, set[str]] | None = None,  # populated by Phase 2
) -> tuple[str, list[str]]:
    # ----- Step 1: Built-ins (AST baseline). -----
    role = infer_role_for_type(type_decl)
    caps = set(infer_capabilities_for_type(type_decl))
    type_ann_names = [a.name for a in type_decl.annotations]

    # ----- Step 2: Layer B annotation map. -----
    # Guard: only fires when AST baseline yielded OTHER. Protects
    # explicit AST stereotypes (@RestController, @Service) from being
    # silently reclassified by a wrapper-annotation config entry that
    # happens to be present on the same class. Step 2 runs BEFORE
    # Step 3 by design — see §"Resolver execution order".
    if role == "OTHER":
        for ann in type_ann_names:
            mapped = overrides.annotation_to_role.get(ann)
            if mapped:
                role = mapped
                break

    # Capabilities from Layer B annotations are unconditionally additive.
    for ann in type_ann_names:
        for c in overrides.annotation_to_capabilities.get(ann, ()):
            caps.add(c)
    # Method-level annotations contribute capabilities too.
    for m in type_decl.methods:
        for ann in m.annotations:
            for c in overrides.annotation_to_capabilities.get(ann.name, ()):
                caps.add(c)

    # ----- Step 3 (Phase 2): Layer A meta-annotation walk. -----
    # Lands in Phase 2. Guard re-checks the *current* role: if Step 2
    # already set it, A is skipped, which is the contract that
    # "user-written B beats automatic A".
    if meta_chain is not None and role == "OTHER":
        # ... walk type_ann_names through meta_chain into ROLE_ANNOTATIONS ...
        pass
    # Layer A capability expansion is unconditional (additive). Phase 2.

    # ----- Step 4 (Phase 3): Layer C `@CodebaseRole`. -----
    # Phase 3. Unconditional override (no OTHER guard).
    # ... read AnnotationRef.arguments['value'] for "CodebaseRole" /
    #     "CodebaseCapability" / "CodebaseCapabilities" ...

    # ----- Step 5: Layer B FQN override. -----
    # Unconditional. Runs last, so it overrides everything else
    # (highest effective priority — see §"Resolver execution order").
    if type_decl.fqn in overrides.fqn_role:
        role = overrides.fqn_role[type_decl.fqn]
    for c in overrides.fqn_capabilities.get(type_decl.fqn, ()):
        caps.add(c)

    return role, sorted(caps)
```

Two invariants encoded above; preserve them through future refactors:

1. **Role steps are guarded; capability steps are not.** Roles are
   mutually exclusive (one value per type), so each non-unconditional
   role step has an `if role == "OTHER"` guard against the *current*
   role to avoid clobbering work done by an earlier step. Capabilities
   form a multi-set, so every layer contributes via `caps.add(...)`
   without guards.

2. **Execution order is built-ins → B-annotations → A-meta → C → FQN
   (do not reorder).** This is the canonical execution order from
   §"Resolver execution order" of this plan. Each step's OTHER guard
   reads the *current* role, not the AST baseline — so once Layer B
   has set role to a non-OTHER value, Layer A's guard fails and A is
   skipped. That is exactly what makes user-configured B beat
   automatic A when they conflict on the same wrapper annotation.

Steps 4 (Layer C) and 5 (Layer B per-FQN) are unconditional: the user
wrote the marker on *this* class (or named the class explicitly in
config), so they override anything else. They run last by design.

### Wiring

In `build_ast_graph.py`, replace direct calls to
`infer_role_for_type(d)` and (when capabilities land)
`infer_capabilities_for_type(d)` with:

```python
overrides = _load_brownfield_overrides(str(project_root.resolve()))
role, capabilities = resolve_role_and_capabilities(d, overrides=overrides)
```

Also wire the same resolver into the chunk-enrichment path in
`graph_enrich.py` so LanceDB chunks carry the overridden values.

### Phase 1 tests

In `tests/test_brownfield_overrides.py` (new):

1. Empty config → resolver matches stock AST behaviour for a fixture set.
2. `annotations.AcmeService: SERVICE` + class `@AcmeService Foo` → role
   `SERVICE`. Same class without the entry → `OTHER`.
3. `annotations.AcmeService: SERVICE` + class `@AcmeService` *and*
   `@RestController` → role stays `CONTROLLER` (AST already classified;
   guard kicks in).
4. `capabilities.CompanyKafkaTopic: [MESSAGE_LISTENER]` + method
   annotated `@CompanyKafkaTopic` → enclosing type carries
   `MESSAGE_LISTENER` capability.
5. `fqn.com.legacy.X: { role: SERVICE, capabilities: [MESSAGE_LISTENER] }`
   on a class that AST classifies as `OTHER` with no capabilities → both
   override values appear.
6. FQN override for `role` *wins over* a conflicting AST inference (e.g.
   AST says `COMPONENT`, FQN says `SERVICE`).
7. Unknown role string in config → entry dropped, no crash, warning
   logged.
8. Malformed YAML → empty overrides, no crash.
9. **LanceDB write-path round-trip (covers the pre-flight fix).** Build
   a fresh LanceDB index over a fixture with `fqn.com.legacy.X.role:
   SERVICE` and `fqn.com.legacy.X.capabilities: [MESSAGE_LISTENER]`.
   Query the table directly and assert the row carries
   `capabilities = ["MESSAGE_LISTENER"]`. Then call
   `codebase_search(query="...", capability="MESSAGE_LISTENER")` and
   assert the class appears in results. Without the pre-flight fix this
   test fails at the table-query step (column missing).

---

## Phase 2 — Layer A: meta-annotation walking

### Why

`@AcmeService` is itself defined as:

```java
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Service
public @interface AcmeService {}
```

After Phase 1 the user can paper over this with a config entry, but
ideally the indexer notices automatically.

### Single source of truth (REQUIRED — read before implementation)

The `meta_chain` map is consumed by **two independent pipelines**:

- `build_ast_graph.py` (Kuzu writer) — walks the project tree itself
  and writes `Symbol` nodes via `infer_role_for_type` /
  `resolve_role_and_capabilities`.
- `graph_enrich.py::enrich_chunk` (Lance writer) — called by
  CocoIndex's `process_java_file`, which has its own file-iteration
  logic.

If each pipeline computes `meta_chain` independently from its own scan,
they can disagree on which `@interface` declarations exist (different
exclusion patterns, different parse-failure handling, different file
order) — producing inconsistent roles for the same class in Kuzu vs.
LanceDB. That bug is hard to attribute because the symptoms appear
across two different MCP tools.

**Constraint:** `meta_chain` MUST be produced by exactly one builder
function, called `collect_annotation_meta_chain`, living in a shared
helper module (`graph_enrich.py` is the natural home — it's already
imported by both pipelines). Both pipelines call the same function;
neither re-implements the scan.

```python
# graph_enrich.py (or a sibling module)

@lru_cache(maxsize=4)
def collect_annotation_meta_chain(
    project_root_str: str,
    *,
    max_depth: int = 4,
) -> dict[str, frozenset[str]]:
    """Build the meta-annotation reachability map for a project.

    Single source of truth for Layer A inference. Both `build_ast_graph`
    (pass1) and `enrich_chunk` consume the result of this function;
    neither re-implements the scan.

    Determinism guarantees:
      * file iteration is sorted (`sorted(root.rglob("*.java"))`) so
        results don't drift between OSes / filesystems.
      * exclusion rules match `build_ast_graph.py`'s pass1 exactly
        (skip `target/`, `build/`, `out/`, `.git/`, hidden dirs, and
        any path matched by the project's existing skip-rules).
      * on duplicate annotation simple-name across packages, keep the
        first encountered AFTER sorted iteration; emit a stderr
        warning naming both FQNs.
      * parse errors on a file: skip with a stderr warning; do not
        let one bad file silently change the chain.

    Returns a `dict[str, frozenset[str]]` mapping every known
    annotation simple-name to the set of *built-in* simple-names
    (subset of `ROLE_ANNOTATIONS` keys ∪ capability detector keys)
    reachable from it through meta-annotation links. Keys present in
    the dict but absent from `builtins` map to an empty frozenset
    when no chain reaches a built-in.
    """
```

**`@lru_cache(maxsize=4)`** matches the existing
`_load_brownfield_overrides` pattern. The cache is keyed by
`project_root_str` so multiple projects in one process work; size is
small because we only ever index a handful of roots concurrently.

**`max_depth=4`** is the hard cap on the closure (see "Depth and
cycles" below). Tests in this plan assume `max_depth=4`; if the cap
moves, regenerate test fixtures.

### Implementation

Two-pass setup that consumes `collect_annotation_meta_chain`:

**Pass A1 — collect annotation declarations** (inside the canonical
builder, not duplicated per-pipeline). While walking the AST, every
`TypeDecl` whose `kind == "annotation"` is recorded with the simple
names of *its own* annotations:

```python
@dataclass(frozen=True)
class AnnotationDecl:
    fqn: str
    simple: str
    meta_annotations: tuple[str, ...]   # simple names
```

The output of pass A1 is `dict[str, AnnotationDecl]` keyed by simple
name. See "Single source of truth" above for the duplicate-name and
parse-error semantics.

**Pass A2 — transitive closure (iterative fixed-point).**
Compute, per annotation simple name, the set of built-in simple names
reachable through the meta-annotation graph. **Use an iterative
fixed-point closure**, not the recursive depth-tracked walk shown in
earlier drafts of this plan — recursion conflates cycle detection with
depth bounding, and on branching DAGs the cap fires
non-deterministically depending on sibling iteration order.

```python
def _build_meta_chain(
    decls: dict[str, AnnotationDecl],
    builtins: frozenset[str],
    *,
    max_depth: int = 4,
) -> dict[str, frozenset[str]]:
    """Iterative closure over the meta-annotation graph.

    Cycle-safe by construction: each iteration is a monotone update on
    the chain dict, so cycles converge after at most `max_depth`
    rounds without infinite recursion. Depth-bounded uniformly:
    `max_depth` rounds means at most `max_depth` hops from any
    starting simple-name.
    """
    chain: dict[str, set[str]] = {b: {b} for b in builtins}
    for _ in range(max_depth):
        changed = False
        for simple, decl in decls.items():
            reach: set[str] = set()
            for parent in decl.meta_annotations:
                reach |= chain.get(parent, set())
            if reach and not reach.issubset(chain.get(simple, set())):
                chain.setdefault(simple, set()).update(reach)
                changed = True
        if not changed:
            break
    return {k: frozenset(v) for k, v in chain.items()}
```

`builtins` is the union of `ROLE_ANNOTATIONS` keys (in `ast_java.py`)
and `_METHOD_ANN_TO_CAPABILITY` / `_TYPE_ANN_TO_CAPABILITY` keys
(also `ast_java.py`). Import them; do not duplicate.

**Why iterative over recursive:** the metric ("hops from start name")
is the loop counter, not a derived property of accumulated state.
Cycle detection falls out of the monotone-update invariant. The
`changed` early-exit makes typical projects terminate in 1–2 rounds.

### Limitations (callout — must be in user-facing docs)

Two non-obvious constraints that come from the design above:

1. **Duplicate annotation simple-names across packages.** When two
   distinct `@interface` types in different packages share the same
   simple name (e.g. `com.team1.AcmeService` and
   `com.team2.AcmeService`), only the first-encountered declaration
   contributes to `meta_chain`. Detection is by simple name because
   the AST does not always carry import-resolved FQNs at usage
   sites. Monorepos that legitimately have name collisions across
   teams should rename one or use `role_overrides.fqn` (Layer B) on
   the affected classes. The first-seen winner is deterministic
   because the canonical builder iterates files in sorted order.

2. **Incremental indexing can produce stale roles.** CocoIndex
   re-processes only changed files. If the changed file is an
   `@interface` declaration (e.g. user removes a `@Service`
   meta-annotation from `@AcmeService`), every class previously
   annotated with `@AcmeService` would need re-enrichment to update
   its resolved role. Phase 2 does **not** track this dependency
   automatically. Workaround: run a full
   `refresh_code_index(confirm=true)` after editing any annotation
   declaration in the project. Document this in the README's
   capabilities section under a "When to do a full rebuild" note. A
   future enhancement could persist the chain hash and force
   re-enrichment of dependents on change, but it is out of scope
   for this plan.

**Resolver integration.**
Layer A inserts at **step 3** in the resolver (see Phase 1's "Resolver
behaviour for Layer B" section for the full step list). Critical: the
guard `if role == "OTHER"` reads the role state **after** Layer B has
had a chance to apply, not the raw AST baseline. This is what enforces
the priority `B (3) > A (4)`: when both B and A are configured for the
same wrapper annotation, B runs first, sets the role, and A's guard
then fails.

```python
# Step 3 in resolve_role_and_capabilities, AFTER Layer B annotation map:
if meta_chain is not None and role == "OTHER":
    for ann in type_ann_names:
        for builtin in meta_chain.get(ann, ()):
            mapped = ROLE_ANNOTATIONS.get(builtin)
            if mapped:
                role = mapped
                break
        if role != "OTHER":
            break
```

Same expansion for capabilities (method-level annotations on the type's
methods are walked through `meta_chain` and contribute capabilities) —
but **unconditionally additive**, no OTHER guard. Capabilities don't
have priority conflicts.

The `meta_chain` map is built once per project and passed into the
resolver alongside `BrownfieldOverrides`. When `meta_chain is None`
(Phase 1, before Layer A lands), step 3 is a no-op and the resolver
behaves as Phase-1-only.

### Phase 2 tests

1. Fixture: `@interface AcmeService` meta-annotated with `@Service`,
   class `@AcmeService Foo`. Without config: `role == "SERVICE"`.
2. Two-hop chain: `@AcmeOrchestrator` → `@AcmeService` → `@Service`.
   Class annotated with `@AcmeOrchestrator` resolves to `SERVICE`.
3. Cycle: `@A` meta-annotates `@B`, `@B` meta-annotates `@A`. No
   crash, role stays `OTHER`.
4. Depth cap: chain of 6 wrappers. Resolver caps at depth 4, role
   `OTHER`. (Acceptable: pathological case warrants explicit config.)
5. Method-level: `@interface CompanyKafkaTopic` meta-annotated with
   `@KafkaListener`. A class with a method annotated `@CompanyKafkaTopic`
   gets `MESSAGE_LISTENER` capability.
6. Layer B + Layer A interaction: a class with both an FQN override and
   a meta-annotated wrapper — FQN wins.
7. **B-beats-A regression guard (CRITICAL — execution-order invariant).**
   Fixture: `@interface AcmeProcessor` meta-annotated with `@Service`.
   Class `@AcmeProcessor Foo` (no other stereotype, AST → OTHER).
   Config: `role_overrides.annotations: { AcmeProcessor: COMPONENT }`.
   Expected: `role == "COMPONENT"` — B fires first and sets role,
   A's guard then fails and A is skipped. Without this test, a future
   refactor could silently swap B and A's order and brownfield users
   would see their explicit config quietly ignored in favour of the
   automatic meta-walk.

---

## Phase 3 — Layer C: `@CodebaseRole` / `@CodebaseCapability`

### Why

The remaining cases:

- A user wants per-class explicit override but doesn't want to maintain
  an FQN-listed config entry.
- A class genuinely has no annotation chain that reaches a built-in
  stereotype (no Spring at all) and adding a wrapper just for indexing
  is overkill.

### Annotation contract

Two annotations, **detected by simple name only — no jar dependency**.
The user declares them anywhere in their tree:

```java
package com.acme.rag;            // any package the user wants

import java.lang.annotation.*;

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)   // SOURCE is fine — indexer reads .java
public @interface CodebaseRole {
    String value();   // e.g. "SERVICE"
}

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseCapabilities.class)
public @interface CodebaseCapability {
    String value();   // e.g. "MESSAGE_LISTENER"
}

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseCapabilities {
    CodebaseCapability[] value();
}
```

The README documents this contract. We do **not** publish a Maven
artifact; the user copies the four interface declarations into their
codebase. This eliminates dependency-management friction.

### Detection

In `ast_java.py`, the existing `AnnotationRef` carries the **raw source
text** of the annotation, not parsed args. We need argument extraction
for `@CodebaseRole("SERVICE")`. Two options:

1. **Cheap:** add a string parser in `ast_java.py` that, given an
   `AnnotationRef.qualified` source-text, extracts the first string
   literal. Robust for `@CodebaseRole("SERVICE")` and
   `@CodebaseRole(value = "SERVICE")`. Reject anything else.
2. **Clean:** extend `AnnotationRef` with a `arguments: dict[str, str]`
   field populated during AST walk by reading the annotation's
   `arguments` child node. More code, more correct. Recommended.

Recommend option 2 — it's a one-time investment that any future
annotation-with-args feature reuses.

```python
@dataclass
class AnnotationRef:
    name: str
    qualified: str
    arguments: dict[str, str] = field(default_factory=dict)
```

Populated in `_annotation_name`'s sibling code by walking the
annotation's `argument_list` / `element_value_pair` children. Default
key is `"value"` for single-argument form (`@Foo("x")` → `{"value":
"x"}`).

### Resolver behaviour for Layer C

In `resolve_role_and_capabilities`, **after** Layer B annotation mapping
but **before** the FQN override (so FQN config still wins as the
ultimate per-class hatch):

```python
for ann in type_decl.annotations:
    if ann.name == "CodebaseRole":
        v = ann.arguments.get("value")
        if v in _VALID_ROLES:
            role = v
    elif ann.name == "CodebaseCapability":
        v = ann.arguments.get("value")
        if v in _VALID_CAPABILITIES:
            caps.add(v)
    elif ann.name == "CodebaseCapabilities":
        # Repeatable container — args carry an array literal we parse on demand.
        ...
```

Invalid values are silently ignored (warning logged). Valid value sets
come from a single shared module so `ast_java.py`, `graph_enrich.py`,
and `server.py` agree.

### Phase 3 tests

1. `@CodebaseRole("SERVICE")` on an otherwise plain class → role
   `SERVICE`.
2. `@CodebaseRole("CONTROLLER") @Service` on a class → `CONTROLLER`
   (Layer C overrides built-in; only FQN override would win above).
3. `@CodebaseCapability("MESSAGE_LISTENER")` → that capability is added
   on top of AST-derived capabilities.
4. `@CodebaseCapability("MESSAGE_LISTENER") @CodebaseCapability("MESSAGE_PRODUCER")`
   (or the `@CodebaseCapabilities({...})` container form) → both appear.
5. `@CodebaseRole("BOGUS")` → role unchanged, warning logged, no crash.
6. Annotation-arg parsing: `@CodebaseRole(value = "SERVICE")` and
   `@CodebaseRole("SERVICE")` produce the same result.

---

## Resolver execution order (final, single source of truth)

The previous draft of this plan presented two parallel lists — a
"priority" list (highest-first) and an "execution order" list
(reverse) — which forced readers to mentally invert one to verify the
other. Both lists encoded the same information, so collapse them into
one list ordered by execution. Priority is metadata on each step, not
a separate axis to chase.

**Read top-to-bottom = code execution order. Each step runs against
the role/cap state produced by the previous step, never against a
fresh AST baseline.** Steps later in the list override steps earlier
in the list whenever they fire; "priority" is therefore exactly
"position in this list" (last to fire = highest priority).

```
                      ROLE half                                      CAPABILITY half
1. built-ins       infer_role_for_type(td)                         caps = set(td.capabilities)
   (always runs)   from ast_java.py                                from ast_java.py

2. Layer B         if role == "OTHER":                             caps |= cfg.annotation_to_capabilities
   annotations         role = cfg.annotation_to_role[ann]          for any annotation on td
   (cfg-driven)        for any annotation on td                    (additive, no guard)

3. Layer A         if meta_chain and role == "OTHER":              if meta_chain:
   meta-walk           role = built_in_role_via_meta_chain(td)         caps |= built_in_caps_via_meta_chain(td)
   (auto, Phase 2)     # guard re-checks CURRENT role —             (additive)
                       # if step 2 already set it, A is skipped

4. Layer C         if td has @CodebaseRole:                        if td has @CodebaseCapability:
   in-code             role = ann.value                                caps |= ann.values
   annotations         (unconditional override)                        (additive)

5. Layer B         if td.fqn in cfg.fqn_role:                      if td.fqn in cfg.fqn_capabilities:
   per-FQN            role = cfg.fqn_role[td.fqn]                      caps |= cfg.fqn_capabilities[td.fqn]
   (cfg-driven)       (unconditional, beats everything)               (additive)

return (role, sorted(caps))
```

**Reading guide.** "Priority" of a layer = its position in this list,
counted from the bottom. Layer 5 (per-FQN) is the highest-priority
override because it runs last and is unguarded; Layer 1 (built-ins) is
the lowest because anything later can replace it.

**Why B-annotations run before A-meta-walk** (steps 2 and 3): user
intent expressed in config must beat automatic inference. If step 3
ran first, then step 2's `role == "OTHER"` guard would already see a
non-OTHER role from meta-walk and would never fire, silently
demoting explicit user config below auto-inference. The execution
order in this list is the only correct interleaving; do not reorder.

### Invariants (encode as comments + tests)

1. **Roles guarded; capabilities unguarded.** Every role-mutating step
   except C and FQN has an `if role == "OTHER"` guard against the
   *current* role state. Every capability step is unconditionally
   additive (`caps.add(...)`).
2. **B runs before A in execution order.** The B-beats-A semantic
   depends on this. The Phase 2 test #7 is the regression guard.
3. **`meta_chain is None` is a valid Phase-1 state.** Phase 2's
   resolver step is a no-op when `meta_chain is None`, so the
   resolver works correctly with Phase 1 alone.
4. **Capabilities are always sorted and deduplicated on return.** The
   resolver returns `sorted(caps)`. Tests should assert exact equality
   against a sorted list, not just `set` membership, to catch
   accidental ordering instability.

---

## Documentation

- `README.md` — new section "Brownfield overrides" walking through Layer
  B (config), with a complete example block. Mention Layer C as the last
  resort, with the four interface declarations to copy-paste.
- `docs/CODEBASE_REQUIREMENTS.md` — expand the role-inference section to note
  the override layers exist.
- MCP server `instructions` string in `server.py` — one extra sentence
  noting that "role and capability inference can be customised per-project
  via `.lancedb-mcp.yml`".

## Ontology version

No additional bump beyond the `2 → 3` from `PLAN-CAPABILITIES-MODEL.md`.
The override layers don't change graph schema; they only change how the
existing fields are populated.

## Out of scope (do not implement here)

- Override layers for the `microservice` / `module` derivation
  (already handled by `microservice_roots` in the same config file).
- Override layers for ranking weights (`_ROLE_SCORE_WEIGHTS`). Future
  work; would need its own design discussion.
- A REST endpoint or MCP tool to introspect the active overrides at
  runtime. Useful for debugging; deferred — log on load is sufficient
  for now.
- Hot reload of the config file. Build pipeline reads it once per index
  build; users re-run `refresh_code_index` after editing.

## Rollout

Three independent PRs, in order Phase 1 → Phase 2 → Phase 3. Each phase
is functional on its own:

- Phase 1 ships the resolver scaffolding plus the config loader and the
  three sub-sections of override. Closes the brownfield gap for users
  willing to maintain a config.
- Phase 2 adds meta-annotation walking. Eliminates the most common
  reason users would need to write Layer B `annotations:` entries.
- Phase 3 adds `@CodebaseRole` / `@CodebaseCapability`. Optional polish;
  ship only if user demand surfaces.

After each phase: rebuild the graph (`refresh_code_index(confirm=true)`)
to apply the new resolver outputs. No ontology bump per phase — the
schema is stable from `PLAN-CAPABILITIES-MODEL.md` onwards.
