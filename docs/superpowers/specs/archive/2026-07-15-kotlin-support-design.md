# Kotlin language support (mixed Java+Kotlin JVM repos)

**Status:** Active — design approved 2026-07-15 after a four-lens review (architect gap-analysis, contract-correctness, skeptic breakage, tree-sitter-kotlin grammar fact-check).
**Tracks:** Approach 1 ("sibling extractor reusing the AST contract") from the Kotlin-support design conversation.
**Related / deferred:** [issue #449](https://github.com/HumanBean17/java-codebase-rag/issues/449) — Approach 2 (shared language-agnostic IR `FileAst` + `LanguageBackend` as a first-class plugin registry). Approach 1 is implemented first; #449 is revisited on its documented triggers.
**Depends on:** `tree-sitter-kotlin>=1.1.0,<2` shipping a wheel for every platform the package already supports (incl. macOS x86_64). See *Error handling* for the gating fallback.

## Summary

Add Kotlin (`.kt`) indexing alongside Java (`.java`) so that a mixed JVM repo — the common Java→Kotlin-migration Spring estate — gets **one merged symbol graph** with **cross-language edges**: a Kotlin class `implements` a Java interface, a Kotlin function `calls` a Java `@Service` method, a Kotlin `@FeignClient` hits a Java route.

The mechanism is a **sibling extractor**, `ast_kotlin.py`, that emits the *existing* `JavaFileAst` / `TypeDecl` / `MethodDecl` / `CallSite` dataclasses behind a thin `LanguageBackend` dispatch shim. Because both languages emit FQN-keyed symbols into one graph, the existing resolver and graph passes run unchanged and produce cross-language edges for free. Spring role/route/client/producer detectors — keyed by annotation *name*, identical in Kotlin — are reused on Kotlin symbols with no per-language duplication.

This is **not** "swap the grammar." Four review lenses established that the extractor must actively speak the existing contract's vocabulary (synthesize JVM accessors, emit Java-literal modifiers, fold Kotlin kinds into the existing five) for the "untouched graph builder" property to hold. Those requirements are contracts in this spec, not implementation detail.

## Background & current state

- The package parses Java via `tree-sitter-java`. `ast/ast_java.py` is the **only** module that touches tree-sitter nodes (parser bootstrap + per-thread `Parser` TLS, `ast_java.py:228-235`).
- `graph/build_ast_graph.py` (~4470 lines) consumes pure-Python dataclasses — `JavaFileAst`, `TypeDecl` (`ast_java.py:370`), `MethodDecl` (`:301`), `CallSite` (`:285`), `FieldDecl` (`:255`), `FileImports` (`:276`), `AnnotationRef` (`:242`). It does **not** inspect tree-sitter node types. This is the seam Approach 1 exploits.
- The resolver is FQN-keyed with lookup order: same-file → explicit import → same package → wildcard import → `java.lang` → phantom (`build_ast_graph.py:1122-1171`; phantom set `_JAVA_LANG_SIMPLE` at `:1189`).
- Cross-file type resolution is gated by `_TYPE_KINDS = ("class","interface","enum","annotation","record")` (`build_ast_graph.py:3280`) at `_load_existing_types` (`:597`) and incremental property-refresh (`:3450`).
- Method-call resolution is **methods-only**: `members`/`methods_by_type` are populated from `decl.methods` only (`build_ast_graph.py:974-981`); `MemberEntry.kind ∈ {"method","constructor"}` (`:209-211`); candidate lookup matches on `m.decl.name == callee_simple` (`:1508`,`:1522`). Fields are never call targets.
- Modifier-driven logic reads `m.decl.modifiers` directly: `"static"` (`:1568`, `:3503` — OVERRIDES gating), `"final"` (`:1314`), `"static" in f.modifiers` (`:1312`).
- `INJECTS` mechanism is location-keyed: constructor injection reads `chosen.parameters[].annotations` (`:1321-1332`), field injection reads `f.annotations` (`:1302-1305`).
- Symbol columns are a fixed DDL: `_NODE_COLUMNS` (`:3273`) generates the LadybugDB schema (`modifiers STRING[]` at `:2935`); `_SET_SYMBOL_BY_ID` (`:3288`) enumerates columns. Adding a column is a schema migration across both write paths.
- Spring detectors are annotation-name keyed and language-agnostic at the annotation level: role tables `ast_java.py:90-107`, capability tables `:114-120`, consumed in `graph_enrich.py:800`,`:813`.
- `.java` is hardcoded in **14** touch-points (see *Components*), all transitively downstream of one suffix gate (`graph/path_filtering.py:472`).
- Kotlin is currently a **documented non-goal**: `docs/CODEBASE_REQUIREMENTS.md` ("Tree-sitter Java only; no Kotlin/Groovy/Scala").
- cocoindex flows fix each `lancedb.TableTarget` at `@coco.fn` decoration time (`process_java_file` `java_index_flow_lancedb.py:404-408`); there is no polymorphic process function. SQL and YAML each have their own matcher + process fn + table.

## Goals

- Index `.kt` alongside `.java` in a mixed Spring-JVM repo into **one** merged LadybugDB graph + LanceDB chunk index.
- Produce cross-language `CALLS`, `IMPLEMENTS`, `EXTENDS`, `OVERRIDES`, `INJECTS` edges (both directions) via the unchanged FQN resolver.
- Reuse Spring role/route/client/producer detection on Kotlin symbols — a Kotlin `@RestController` is a `CONTROLLER`, a Kotlin `@FeignClient` method gets `DECLARES_CLIENT`, and these resolve across to Java routes/services.
- **Zero regression** to the Java path: the graph builder's kind/modifier/annotation consumers stay unedited; correctness is achieved entirely by the extractor speaking the existing contract.
- Graceful degradation when `tree-sitter-kotlin` is absent (Intel-Mac gating, install failure): `.kt` files skipped, Java indexing and querying unaffected.
- Symbol/chunk provenance: every node carries a `language` field so downstream (display, `inspect`, search scoring) can distinguish Kotlin from Java.

## Non-goals (v1)

- **No** resolution of idiomatic Kotlin→Kotlin extension-function calls (`s.ex()`). Java→Kotlin extension calls (`FileKt.ex(s)`) resolve; the Kotlin-to-Kotlin form is a documented limitation and the legitimate trigger for #449.
- **No** rename of the `JavaFileAst` dataclass (an alias `FileAst = JavaFileAst` is added now; the full rename and `LanguageBackend`-as-plugin-registry are #449).
- **No** Kotlin-native framework detectors beyond Spring (Ktor, Exposed, Koin, Coroutines). v1 ships Spring-on-Kotlin; other frameworks slot in later behind the same backend seam.
- **No** generalized multi-language file-classification: `classify_java_file` (`graph_enrich.py:1720`) is reused on Kotlin ASTs unchanged (Java generated-code banners only); Kotlin codegen markers (ksp, Kotlinpoet, Moshi codegen) are a later milestone.
- **No** change to `SearchHit` / `SearchOutput` / `NodeFilter` schemas; the `language` field rides on existing open columns / the symbol row.
- **No** hard-failure paths. Kotlin parse/resolution problems degrade like Java's (per-file skip + log).
- **No** collision *repair* for same-FQN Kotlin+Java declarations; v1 warns (see *Documented limitations*).

## Architecture & data flow

### The `LanguageBackend` seam

A new module `ast/language.py` defines a minimal dispatch contract — deliberately *not* the #449 plugin registry:

```
LanguageBackend (Protocol):
    language_id: str                 # "java" | "kotlin"
    suffixes: tuple[str, ...]        # (".java",) | (".kt",)
    def parse(self, source, *, filename) -> JavaFileAst: ...

LANG_BACKENDS: dict[str, LanguageBackend] = {"java": JavaBackend, "kotlin": KotlinBackend}
def backend_for(path) -> LanguageBackend | None     # dispatch by suffix
```

`JavaBackend.parse` delegates to the existing `parse_java`; `KotlinBackend.parse` delegates to the new `parse_kotlin`. `KotlinBackend` is registered **only if** `tree_sitter_kotlin` imports successfully. Flow-wiring (matcher patterns, the `process_*_file` cocoindex fns, table targets) stays in `index/java_index_flow_lancedb.py:app_main`; the backend owns only the AST contract.

### End-to-end flow for a `.kt` file

```
discover (.kt via widened iter_source_files)
  → backend_for(path) = KotlinBackend
  → KotlinBackend.parse → JavaFileAst(language="kotlin")
  → build_ast_graph pass1 (calls backend.parse, not hardcoded parse_java)
  → merged JavaFileAst stream (Java + Kotlin) → passes 2/3 (resolver + edges)
  → Spring passes over ALL symbols (shared annotation-name tables)
  → persist: symbol/edge/route/client rows (language column) + LanceDB chunks
  → watch dispatches .kt saves to KotlinBackend
  → query surfaces unchanged; inspect shows language provenance
```

### Cross-language resolution (the payoff)

Both backends emit FQN-keyed symbols. Kotlin `package`/`import` populate the existing `FileImports`. The unchanged resolver then resolves a Kotlin `CallSite` to a Java `MethodDecl` (and vice versa) by the same lookup order. Example: Kotlin `FooController.getUser()` calls `userService.findById(42)` where `UserService` is a Java `@Service`; the Kotlin import `import com.foo.UserService` feeds `FileImports`; the resolver looks up `com.foo.UserService.findById`, finds the Java `MethodDecl`, emits a `CALLS` edge Kotlin→Java.

### Grammar contract (tree-sitter-kotlin PyPI 1.1.0)

`ast_kotlin.py` must target the **installed** 1.1.0 grammar, which is a restructured lineage — **not** the `fwcd/tree-sitter-kotlin` grammar. Online `node-types.json` is unreliable; the extractor is pinned to observed 1.1.0 behavior. Required node-type handling (contract, not algorithm):

| Kotlin source | tree-sitter-kotlin 1.1.0 node |
|---|---|
| `class` / `interface` / `enum class` / `annotation class` / `data class` | `class_declaration` (discriminate interface by `interface` keyword child; enum by `modifiers > class_modifier[enum]` + `enum_class_body`; data by `class_modifier[data]`; annotation by `class_modifier[annotation]`) |
| `object Singleton` | `object_declaration` |
| `companion object { … }` | `companion_object` (distinct node; name in optional child `identifier`) |
| `fun` (member or top-level) | `function_declaration` (name in child `identifier` field `name`) |
| `var`/`val` property | `property_declaration` (name in child `variable_declaration > identifier`; body in `getter`/`setter`) |
| `package x.y` | `package_header` (dotted path in child `qualified_identifier`) |
| `import x.y.Z` / `import … as B` / `import x.y.*` | `import` (dotted path in `qualified_identifier`; alias is sibling `identifier`; **no** `import_alias` node) |
| modifiers | single `modifiers` container with typed sub-containers (`function_modifier`, `class_modifier`, `inheritance_modifier`, `member_modifier`, `property_modifier`, `visibility_modifier`, `parameter_modifier`); keyword itself is an **anonymous token** matched by literal text (no `suspend`/`data`/`override` named nodes) |
| annotations | `annotation` (singular), inside `modifiers`; with args via child `constructor_invocation > (user_type, value_arguments)` |
| use-site target (`@field:`/`@get:`/`@set:`/`@param:`) | `annotation > use_site_target > (field|get|set|param, :)`; parameter annotations sit under `parameter_modifiers` |
| `@file:JvmName("X")` / `@file:JvmMultifileClass()` | `file_annotation` (sibling of `package_header`/`import`); name in `constructor_invocation > user_type > identifier`; target is the anonymous `file` token |
| type reference | `user_type` with **flat** `identifier`/`.` children (dotted types are NOT `qualified_identifier` in type position); wrappers `nullable_type`, `type_projection`, `type_arguments` |
| names (all) | `identifier` (there is **no** `simple_identifier` or `type_identifier` in 1.1.0) |
| call site | `call_expression > navigation_expression` (receiver = left spine; callee = last `identifier`) + `value_arguments > value_argument` |
| `typealias` | `type_alias` (a reference, not a Symbol) |

## Components

### New files

| File | Responsibility |
|---|---|
| `ast/language.py` | `LanguageBackend` Protocol, `LANG_BACKENDS` registry, `backend_for(path)`, `FileAst = JavaFileAst` alias. `KotlinBackend` registered conditionally on the `tree_sitter_kotlin` import. |
| `ast/ast_kotlin.py` | `parse_kotlin(source, *, filename) -> JavaFileAst` over `tree_sitter_kotlin.language()`, per-thread `Parser` TLS (mirror `ast_java.py:219-235`). Honors every contract in *Kotlin extractor requirements*. |

### Edited files (the 14 touch-points + contract additions)

| File:line | Change |
|---|---|
| `ast/ast_java.py` (dataclasses) | Add `language: str` (required, no default) to `JavaFileAst`; validate in `__post_init__` against registered ids. Add a `use_site_target: str \| None` field to `AnnotationRef` (`:242`). `parse_java` sets `language="java"`. |
| `graph/path_filtering.py:472` | The suffix gate: `iter_java_source_files` → `iter_source_files` yielding every registered backend's suffixes. **Single chokepoint** that fixes the five `iter_*_source_files` callers below. |
| `graph/build_ast_graph.py:1068` | `pass1_parse` calls `backend_for(path).parse(...)` instead of hardcoded `parse_java`. (Contained: result still satisfies the full `JavaFileAst` contract read at `:1081`/`:1090`/`:1093`/`:1100`.) |
| `graph/build_ast_graph.py:535,1031,1046,4173` | `FileHashTracker` / pass1 count / parse loop / hash-seed iterate `iter_source_files` (fixed transitively by the chokepoint). |
| `graph/graph_enrich.py:390,402` | `_collect_annotation_decl_index` walks `iter_source_files` and parses via `backend_for` (the sixth parse site) so Kotlin `annotation class` declarations join the meta-annotation index (`:410`,`:455`). |
| `index/java_index_flow_lancedb.py:681` | Add a `**/*.kt` matcher (parallel to `**/*.java`/SQL/YAML). |
| `index/java_index_flow_lancedb.py` (new `process_kotlin_file`) | cocoindex fn bound to the existing `JavaLanceChunk` table (fields `primary_type_kind`,`role`,`capabilities` are language-agnostic); drains via `_drain_files_concurrently` (`:726`). |
| `index/java_index_flow_lancedb.py:390` | `_parse_and_enrich_java` → per-suffix dispatch (`backend_for`); `process_java_file` (`:405`) unchanged for Java. |
| `index/java_index_flow_lancedb.py:241` | `_approximate_vectors_total` counts all registered suffixes. |
| `index/java_index_flow_lancedb.py:656` | Annotation meta-chain warm-up happens before the parse loop (preserved ordering). |
| `watch/watcher.py:57` | `INDEXED_SUFFIXES = (".java", ".kt")` (read from the registry). |
| `watch/watcher.py:242` | `_classify` dispatches via `backend_for(path)`. |
| `jrag.py:3648` | `_cmd_imports` (`jrag imports`) parses via `backend_for`. |
| `search/search_lancedb.py:405,434,540,767` and `search/search_scoring.py:245,292` | `_kind == "java"` / `kind == "java"` branches generalize to `language in ("java","kotlin")` (or key off the new field). Additive bonuses only; non-matching rows already skip the bonus. |
| `ast/chunk_heuristics.py:20-41` | Add a Kotlin branch for type/`import` heuristics (Kotlin `import`/`fun`/`object`); key off `language`, not `kind=="java"`. |
| `pyproject.toml` | Add `tree-sitter-kotlin>=1.1.0,<2`; gate with the same PEP 508 Intel-Mac marker as `torch`/`lancedb` if no x86_64 macOS wheel exists. |

## Contract decisions (what `parse_kotlin` must emit)

### Kinds — fold to the existing five; do **not** extend `_TYPE_KINDS`

Extending `_TYPE_KINDS` is a silent-bug trap (any missed `kind ==` branch drops Kotlin types from resolution). All Kotlin kinds fold:

| Kotlin construct | `TypeDecl.kind` | Why |
|---|---|---|
| `class` | `class` | literal |
| `interface` | `interface` | literal; correctly skips injection (`:1277`) and is the Feign-iface gate |
| `enum class` | `enum` | literal; default-ctor synth eligible |
| `annotation class` | `annotation` | **load-bearing** — drives the meta-annotation index (`graph_enrich.py:410`,`:455`) and incremental invalidation (`:746`) |
| `data class` | `record` | semantic analog; auto-promotes to DTO (`ast_java.py:2761`); opts out of default-ctor synth |
| `object` | `class` | singleton analog |
| `companion object` | `class` (nested under enclosing type) | holds static-ish members |
| top-level functions | synthetic `class` facade | JVM truth (Kotlin compiles them to a `*Kt` class) |

### Modifiers — unified `modifiers`, no new column

A parallel `kotlin_modifiers` field is **rejected**: the `"static"`/`"final"` checks (`:1568`,`:3503`,`:1314`,`:1312`) read `modifiers` directly and would silently never fire for Kotlin. `parse_kotlin` emits Java-vocabulary strings into the shared `modifiers` list:

| Kotlin construct | emit into `modifiers` | Load-bearing consumer |
|---|---|---|
| `@JvmStatic` method / companion-object member / top-level fn | `"static"` | OVERRIDES gating (`:3503`); static-call resolution |
| `const val` | `"static"`, `"final"` | field handling (`:1312`,`:1314`) |
| `val` property / `class` / `fun` (default) | `"final"` | Kotlin classes/vals are final by default |
| `open class` / `open fun` | (omit `"final"`) | allows override candidacy |

Kotlin-only modifiers with no Java consumer (`suspend`, `inline`, `operator`, `infix`, `tailrec`, `data`, `sealed`) ride along in `modifiers` harmlessly — no consumer checks for them — preserving fidelity for display.

### Provenance

- `JavaFileAst.language` is **required** (no default), validated in `__post_init__`. Two controlled producers; a forgotten value must not silently classify Kotlin as Java.
- Add `FileAst = JavaFileAst` alias now (the name is cosmetic — it is not a serialized key; search's `_kind=="java"` is an unrelated chunk marker). Full rename → #449.

## Kotlin extractor requirements

These are the load-bearing behaviors `parse_kotlin` must implement for the "untouched graph builder" property to hold. Each was a review finding.

1. **Synthesize JVM property accessors as `MethodDecl`s (B1 — spec-blocker).** Because call resolution is methods-only (`members`/`methods_by_type` exclude fields), mapping a Kotlin property to a single `FieldDecl` makes every Java `foo.getName()`/`setFoo(...)`/`isBar()` call a phantom — the dominant Java→Kotlin interop pattern (data classes, DTOs, `@ConfigurationProperties`). For every non-private property, `parse_kotlin` emits the `FieldDecl` **and** synthesized `MethodDecl`s for the JVM accessors (`getName`/`setName` for `var`; `getName`/`isName` for `Boolean`-typed `val`) so cross-language CALLS resolve.
2. **Honor `@file:JvmName` / `@file:JvmMultifileClass` (B5).** Multiple `.kt` files carrying a shared `@JvmName("X") @file:JvmMultifileClass()` compile to **one** JVM class `pkg.X`. A per-file synthetic facade produces wrong FQNs and silent last-wins collisions (`tables.types[fqn]` overwrites at `:970`). `parse_kotlin` (or a pre-pass over parsed Kotlin ASTs) groups multifile files by shared facade name and merges them into one synthetic `TypeDecl` accumulating members across files. Non-multifile `@file:JvmName("X")` sets the single-file facade name to `X`.
3. **Model annotation use-site targets (B6).** `AnnotationRef.use_site_target` captures `field`/`get`/`set`/`param`. `parse_kotlin` routes each annotation to the slot the JVM target implies so `INJECTS` mechanism (constructor vs field, `:1321-1332` vs `:1302-1305`) and member-annotation detectors are correct. Default target resolution honoring an annotation's own `@Target` meta is out of scope for v1 (documented approximation); explicit `use_site_target` always wins.
4. **Build `FileImports` from the Kotlin grammar (B7).** Kotlin imports parse as `import`/`package_header`/`qualified_identifier`, not Java's `import_declaration`/`scoped_identifier`. `FileImports.explicit` is populated from Kotlin `import` nodes; `static_methods`/`static_wildcards` stay empty (Kotlin has no `import static`). Aliased imports (`import … as B`) record the alias.
5. **Kotlin resolution model (B8).** Default imports (`kotlin.*`, `kotlin.collections.*`, `kotlin.sequences.*`, `kotlin.io.*`, etc.) extend the phantom/known-type fallback beyond `_JAVA_LANG_SIMPLE` for Kotlin files. Free-function calls (`foo()`) resolve against the synthetic facade `<File>Kt.foo` / `@JvmName` facade. The existing `_resolve_simple` (type-only) and `pass3_calls` handle these once the facade `TypeDecl`s are registered.
6. **Flow shape (B9).** A dedicated `process_kotlin_file` cocoindex fn + `**/*.kt` matcher writing the existing `JavaLanceChunk` table — the proven parallel pattern, not a polymorphic dispatch (the table target is fixed at decoration time).
7. **`extends`/`implements` partition (soft contract-breaker).** Kotlin collapses superclass + superinterfaces into one `:` clause; the dataclass demands a split. `parse_kotlin` best-effort partitions using same-compilation-unit declared kinds for known supertypes; **unknown supertypes default to `implements`** (a spurious IMPLEMENTS is less damaging than a false EXTENDS, and unknown interfaces are more common than unknown external classes). Documented approximation.
8. **Constructor delegation & anonymous types.** Kotlin primary/secondary constructor delegation (`: Super(...)`) emits `<init>`/`super()` `CallSite`s so the constructor/implicit-super strategies fire. Object expressions / SAM conversions get synthetic FQNs compatible with the enclosing-type fallback (the existing `<anon:byte>` scheme is Java-specific; Kotlin uses an equivalent synthetic-naming path).
9. **Thread-safety.** `parse_kotlin` uses a per-thread `Parser` (mirror `ast_java.py:219-235`) because the cocoindex flow fans out concurrently (`process_java_file` via `asyncio.to_thread`).

## Documented v1 limitations

- **Extension-function Kotlin→Kotlin calls** (`s.ex()`) do not resolve (phantom). Java→Kotlin (`FileKt.ex(s)`) resolves when the facade is correctly named and the method is emitted `static`. This is the #449 trigger.
- **Same-FQN Kotlin+Java declaration** (illegal at compile, but the indexer sees both files): silent last-wins (`tables.types[fqn]` overwrites); v1 emits a warning. `symbol_id` includes `file_path`, so two Symbol nodes coexist but CALLS edges attach to the last-registered type.
- **Generated-code classification**: `classify_java_file` is reused on Kotlin; Kotlin codegen banners are not detected. Marked for a later milestone.
- **Annotation default-target resolution** without modeling each annotation's `@Target` meta is best-effort; explicit `use_site_target` always wins.

## Error handling

- **`tree-sitter-kotlin` absent at runtime** (Intel-Mac gating / install failure): `KotlinBackend` is unregistered; `.kt` files are skipped with a debug log; Java indexing and querying continue unaffected. Mirrors the existing Intel-Mac lexical-fallback pattern. The package import never fails because the grammar is missing.
- **`pyproject` gating**: if `tree-sitter-kotlin` has no macOS x86_64 wheel, it is gated with the same PEP 508 marker as `torch`/`lancedb` so Intel-Mac `pip install` keeps working (graph-only / lexical fallback, unchanged guarantee).
- **Parse failures** (malformed Kotlin, grammar-rejected syntax): per-file skip + log (filename + line), index continues. One unparseable `.kt` never aborts the run.
- **Grammar API skew**: parser construction is contained in `KotlinBackend`; any `language()` exposure difference across versions fails closed (Kotlin skipped), not registry-wide.
- **Resolution misses** (unindexed Java type, third-party lib): existing phantom path — no new failure mode.
- **Backward compatibility / re-index**: `language` is required on new writes; pre-existing Java-only indexes have no `language` value and are treated as `"java"` on read (the field's absence implies Java). Adding Kotlin to an existing project requires a fresh `init` (erase + rebuild) — the standard operator workflow. Whether the `language` column warrants an ontology-version bump is an implementation decision (today `ast_java.py:ONTOLOGY_VERSION`).

## Testing

- **`ast_kotlin.py` extractor unit tests** — a small Kotlin fixture asserting `parse_kotlin` output for: class/object/interface/enum/data/annotation class; companion object (→ nested `Companion`); top-level functions (→ `<Basename>Kt` facade with correct FQN); properties **plus synthesized accessor `MethodDecl`s**; `suspend`/`inline` modifiers in `modifiers`; Spring annotations with use-site targets; `import`/`import as`/wildcard; `@file:JvmName` overriding the facade name; `@file:JvmMultifileClass` merging two files into one facade.
- **`LanguageBackend` dispatch** — `backend_for(path)` by suffix; unknown suffix → `None`; simulated `tree_sitter_kotlin` import failure → `KotlinBackend` unregistered, `.kt` skipped, registry still imports.
- **Cross-language resolution (core proof)** — a mixed fixture: Kotlin `implements` a Java interface; Kotlin function `calls` a Java `@Service` method; Kotlin `@FeignClient` hits a Java route. Assert the merged graph contains `IMPLEMENTS`/`CALLS`/`HTTP_CALLS` edges crossing Kotlin→Java.
- **Property-accessor CALLS parity** — Java calling `getName()`/`setName()` on a Kotlin data class resolves (guards B1).
- **Spring-detector parity** — Kotlin `@RestController`/`@Service`/`@Repository`/`@FeignClient`/`@KafkaListener` produce the same nodes/edges as Java equivalents; Kotlin `@param:Autowired` yields a constructor `INJECTS` (guards B6).
- **End-to-end index + query** — mixed fixture indexed fresh in a temp dir (erase stale manual indexes first per `CLAUDE.md`); assert `jrag`/MCP return both languages and the cross-language edges.
- **Graceful degradation** — malformed `.kt` skipped, index completes; `KotlinBackend` absent → `.kt` skipped, Java still queryable.
- **Java regression** — existing Java fixtures (e.g. `tests/bank-chat-system/`) index and query identically (guards the "zero regression" goal).
- **Provenance** — symbol rows / `inspect` carry `language: kotlin` vs `language: java` correctly.
- **Baseline:** develop against the relevant subset; full suite once at end.

## Deferred / follow-ups

1. [issue #449] Shared language-agnostic IR (`FileAst`) + `LanguageBackend` as a first-class plugin registry; full rename of `JavaFileAst`. Triggered when a Kotlin construct (e.g. extension-function K→K resolution, coroutine `suspend` call-graph accuracy) proves un-force-fittable, or a third JVM language is added.
2. Kotlin-native framework detectors (Ktor routes, Exposed tables, Koin modules, Coroutines `suspend` call-graph).
3. Kotlin generated-code classification (ksp / Kotlinpoet / Moshi codegen / grpc-kotlin markers) in `classify_java_file`.
4. Annotation default-target resolution modeling each annotation's `@Target` meta.
5. Same-FQN Kotlin+Java collision: warn-and-dedup strategy beyond the v1 warning.
6. Third JVM languages (Scala/Groovy) behind the same `LanguageBackend` seam.

## References

- `ast/ast_java.py` — `JavaFileAst`/`TypeDecl`(`:370`)/`MethodDecl`(`:301`)/`CallSite`(`:285`)/`FieldDecl`(`:255`)/`FileImports`(`:276`)/`AnnotationRef`(`:242`); parser TLS `:219-235`; `_TYPE_KINDS` `:193-199`; role tables `:90-107`; capability tables `:114-120`; `_collect_imports` `:906`, `_import_declaration_is_static` `:2656-2685`; DTO inference `:2761`; `ONTOLOGY_VERSION`.
- `ast/language.py` (new) — `LanguageBackend`, `LANG_BACKENDS`, `backend_for`, `FileAst` alias.
- `ast/ast_kotlin.py` (new) — `parse_kotlin`.
- `graph/build_ast_graph.py` — `pass1_parse`/`parse_java` call `:1068`; `_register_type`/members `:970-981`; `_resolve_simple` `:1122-1171`; `_JAVA_LANG_SIMPLE` `:1189`; `_load_existing_types` `:597`; `_TYPE_KINDS` `:3280`; modifier checks `:1312`,`:1314`,`:1568`,`:3503`; `_lookup_method_candidates`/`collect_on_type` `:1508`,`:1522`; `_emit_injects` `:1277`,`:1302-1305`,`:1321-1332`; `methods_by_type` `:1370-1373`; `MemberEntry.kind` `:209-211`; `_NODE_COLUMNS` `:3273`; `_SCHEMA_NODE`/`modifiers STRING[]` `:2935`; `_SET_SYMBOL_BY_ID` `:3288`; property-refresh `:3450`; `FileHashTracker` `:535`; pass1 count/loop `:1031`,`:1046`; `_init_hash_tracker` `:4173`; anon naming `:1546`; implicit-super `:1933-1941`.
- `graph/path_filtering.py:472` — the suffix-gate chokepoint.
- `graph/graph_enrich.py` — `_collect_annotation_decl_index` `:390`,`:402`; meta-annotation index `:410`,`:455`; role detection `:800`,`:813`; `classify_java_file` `:1720`; `enrich_chunk` `:1877`; invalidation `:746`.
- `index/java_index_flow_lancedb.py` — `_parse_and_enrich_java` `:390`; `process_java_file` `:404-408`,`:405`; `process_sql_file` `:487`; `process_yaml_file` `:536`; matchers `:677-703` (java `:681`); `_approximate_vectors_total` `:241`; `_drain_files_concurrently` `:726`; `app_main` `:608`; warm-up `:656`.
- `watch/watcher.py` — `INDEXED_SUFFIXES` `:57`; `_classify` `:242`.
- `jrag.py:3648` — `_cmd_imports`.
- `search/search_lancedb.py:405,434,540,767`; `search/search_scoring.py:245,292`; `ast/chunk_heuristics.py:20-41`.
- `pyproject.toml` — `tree-sitter-java`/`tree-sitter` deps; Intel-Mac PEP 508 marker pattern.
- `docs/CODEBASE_REQUIREMENTS.md` — current "Java only" requirement (to be amended).
