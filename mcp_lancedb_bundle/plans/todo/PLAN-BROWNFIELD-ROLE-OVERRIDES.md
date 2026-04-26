# Plan: brownfield role / capability overrides

Status: **agreed, ready to implement**. Self-contained: an agent picking
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
) -> tuple[str, list[str]]:
    """Compose AST inference with brownfield overrides.

    Precedence (highest first):
      1. FQN override from config (Layer B)            -- explicit per-class
      2. `@CodebaseRole` / `@CodebaseCapability`       -- Layer C
      3. Annotation-name mapping from config (Layer B) -- wrapper stereotypes
      4. Meta-annotation walk (Layer A)                -- automatic
      5. Built-in detectors                            -- ast_java.py defaults
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

```python
def resolve_role_and_capabilities(
    type_decl: TypeDecl,
    *,
    overrides: BrownfieldOverrides,
) -> tuple[str, list[str]]:
    # Start from AST inference.
    role = infer_role_for_type(type_decl)
    caps = set(infer_capabilities_for_type(type_decl))

    # Layer B — annotation mapping (wrapper stereotypes).
    type_ann_names = [a.name for a in type_decl.annotations]
    for ann in type_ann_names:
        mapped_role = overrides.annotation_to_role.get(ann)
        if mapped_role and role == "OTHER":
            role = mapped_role
        for c in overrides.annotation_to_capabilities.get(ann, ()):
            caps.add(c)
    # Method-level annotations also drive capabilities (mirror ast_java logic).
    for m in type_decl.methods:
        for ann in m.annotations:
            for c in overrides.annotation_to_capabilities.get(ann.name, ()):
                caps.add(c)

    # Layer B — FQN override (highest precedence, last to apply).
    if type_decl.fqn in overrides.fqn_role:
        role = overrides.fqn_role[type_decl.fqn]
    for c in overrides.fqn_capabilities.get(type_decl.fqn, ()):
        caps.add(c)

    return role, sorted(caps)
```

Note the `role == "OTHER"` guard on annotation mapping: a class that the
AST already classifies as e.g. `SERVICE` is not silently re-classified
by a wrapper-annotation entry. FQN-keyed overrides bypass the guard
because they are explicit per-class.

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

### Implementation

Two-pass index in `build_ast_graph.py`:

**Pass A1 — collect annotation declarations.**
While walking the AST, every `TypeDecl` whose `kind == "annotation"` is
recorded with the simple names of *its own* annotations:

```python
@dataclass
class AnnotationDecl:
    fqn: str
    simple: str
    meta_annotations: tuple[str, ...]   # simple names

annotation_decls: dict[str, AnnotationDecl] = {}   # keyed by simple name
```

When two distinct annotation types share a simple name (rare but
possible across packages), keep the first encountered and warn — the
project is unlikely to use both, and full FQN matching for annotations
on usage sites is unreliable because the AST doesn't always carry the
import resolution.

**Pass A2 — transitive closure.**
Compute, per annotation simple name, the set of *built-in* annotation
simple names reachable through the meta-annotation graph (cycle-safe
BFS, depth limit 4 to bound pathological cases):

```python
def _resolve_meta_chain(simple: str, decls, builtins, seen=None) -> set[str]:
    if seen is None:
        seen = set()
    if simple in seen or len(seen) > 4:
        return set()
    seen.add(simple)
    if simple in builtins:
        return {simple}
    decl = decls.get(simple)
    if decl is None:
        return set()
    out: set[str] = set()
    for parent in decl.meta_annotations:
        out |= _resolve_meta_chain(parent, decls, builtins, seen)
    return out
```

`builtins` is the union of `ROLE_ANNOTATIONS` keys and
`_METHOD_ANN_TO_CAPABILITY` / `_TYPE_ANN_TO_CAPABILITY` keys (imported
from `ast_java.py`).

**Resolver integration.**
Layer A inserts itself between Layer B's annotation-mapping step and the
built-in detectors. Pseudocode:

```python
# In resolve_role_and_capabilities, when the AST role is OTHER:
if role == "OTHER":
    for ann in type_ann_names:
        for builtin in meta_chain(ann):
            mapped = ROLE_ANNOTATIONS.get(builtin)
            if mapped:
                role = mapped
                break
        if role != "OTHER":
            break
```

Same expansion for capabilities (method-level annotations on the type's
methods are walked through `meta_chain` and contribute capabilities).

The `meta_chain` map is built once per project and passed into the
resolver alongside `BrownfieldOverrides`.

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

## Precedence summary (final)

When `resolve_role_and_capabilities` finishes, the following resolution
order has applied (highest priority listed first):

1. **`overrides.fqn`** (Layer B, per-FQN explicit) — wins absolutely.
2. **`@CodebaseRole` / `@CodebaseCapability`** (Layer C, per-class) — wins
   over wrappers and built-ins.
3. **`overrides.annotations`** (Layer B, wrapper-stereotype mapping) — wins
   over Layer A and built-ins, but only when AST role is `OTHER` for the
   role half (capability half is purely additive).
4. **Meta-annotation walk** (Layer A, automatic) — same `OTHER`-only guard
   for the role half; additive for capabilities.
5. **Built-in detectors** in `ast_java.py` — baseline.

Capabilities are **always additive across layers** (a set union). Only
the role field is mutually exclusive, hence the precedence ordering for
roles.

---

## Documentation

- `README.md` — new section "Brownfield overrides" walking through Layer
  B (config), with a complete example block. Mention Layer C as the last
  resort, with the four interface declarations to copy-paste.
- `CODEBASE_REQUIREMENTS.md` — expand the role-inference section to note
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
