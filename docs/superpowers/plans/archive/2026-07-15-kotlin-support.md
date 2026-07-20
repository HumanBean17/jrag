# Kotlin Language Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index `.kt` files alongside `.java` in a mixed Spring JVM repo into one merged symbol graph, with cross-language CALLS/IMPLEMENTS/EXTENDS/OVERRIDES/INJECTS edges and Spring detector reuse on Kotlin symbols.

**Architecture:** A sibling extractor `ast_kotlin.py` emits the existing `JavaFileAst` dataclasses behind a `LanguageBackend` dispatch seam (`ast/language.py`). Both languages feed one FQN-keyed graph, so the unchanged resolver produces cross-language edges. Correctness lives in the extractor speaking the existing contract's vocabulary (synthesize JVM accessors, emit Java-literal modifiers, fold Kotlin kinds into the existing five). The graph builder's Java-path consumers stay unedited; one additive, Kotlin-gated resolution hook (Task 13) handles Kotlin default imports + top-level-function calls.

**Tech Stack:** Python 3.11+, tree-sitter 0.25, tree-sitter-java 0.23, **tree-sitter-kotlin 1.1.0 (new)**, cocoindex/lancedb (vector path), LadybugDB (graph). Tests via `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/active/2026-07-15-kotlin-support-design.md`. **Branch:** `feature/kotlin-support`. **Deferred:** issue #449 (shared IR + plugin registry).

## Global Constraints

Copied verbatim from the spec; every task's requirements include these.

- Use `.venv/bin/python` and `.venv/bin/pip` only — never system python/pip. Editable install; if stale, run `.venv/bin/pip install -e ".[dev]"`.
- Tests erase stale manual indexes first: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`. Tests build a fresh index in a temp dir; never commit one under `tests/`.
- Develop against the relevant test subset; run the full suite once at the end.
- **Zero Java regression:** the graph builder's kind/modifier/annotation consumers (`build_ast_graph.py`, `graph_enrich.py`) are unedited. Correctness is achieved by `parse_kotlin` speaking the existing contract. The single exception is Task 13 (Kotlin resolution), which is additive and Kotlin-gated so Java resolution is byte-identical.
- **No new graph column.** Kotlin modifiers go into the existing shared `modifiers` list (Java vocabulary). Provenance uses the AST-level `language` field, not a new LadybugDB column.
- **Kotlin kinds fold into the existing five** (`class`,`interface`,`enum`,`annotation`,`record`); do NOT extend `_TYPE_KINDS`.
- **Grammar target:** tree-sitter-kotlin PyPI 1.1.0 (restructured lineage, not fwcd). Pin to observed 1.1.0 node behavior; do not trust online `node-types.json`.

## Shared Contracts (data shapes)

Every task is self-contained; these shapes are restated in Produces blocks where used. All live in `src/java_codebase_rag/ast/ast_java.py` unless noted.

`JavaFileAst`: `package: str`, `imports: list[str]` (raw as written), `wildcard_imports: list[str]`, `explicit_imports: dict[str,str]`, `top_level_types: list[TypeDecl]`, `all_types: list[TypeDecl]` (flat, includes nested), `parse_error: bool=False`, `source_bytes: int=0`, `file_imports: FileImports`, `routes_skipped_unresolved: int=0`, **`language: str`** (new, required, no default).

`TypeDecl`: `name: str`, `kind: str`, `fqn: str`, `modifiers: list[str]`, `annotations: list[AnnotationRef]`, `extends: list[str]` (simple names), `implements: list[str]`, `fields: list[FieldDecl]`, `methods: list[MethodDecl]`, `nested: list[TypeDecl]`, `start_byte/end_byte/start_line/end_line: int`, `outer_fqn: str|None`, `capabilities: list[str]`.

`MethodDecl`: `name: str`, `return_type: str` (simple; `""` for constructors), `is_constructor: bool`, `parameters: list[ParamDecl]`, `modifiers: list[str]`, `annotations: list[AnnotationRef]`, `signature: str` (`"name(T1,T2)"`), `start_byte/end_byte/start_line/end_line: int`, `call_sites: list[CallSite]`, `local_vars: list[tuple[str,str]]`, `routes: list[RouteDecl]`, `outgoing_calls: list[OutgoingCallDecl]`.

`FieldDecl`: `name: str`, `type_name: str`, `type_raw: str`, `modifiers: list[str]`, `annotations: list[AnnotationRef]`, `start_byte/end_byte/start_line/end_line: int`.

`ParamDecl`: `name: str`, `type_name: str`, `type_raw: str`, `annotations: list[AnnotationRef]`.

`CallSite`: `caller_fqn: str`, `receiver_expr: str` (`""` for bare calls), `callee_simple: str` (method name or `"<init>"`), `arg_count: int` (`-1` for method refs), `is_static_call: bool`, `is_constructor: bool`, `in_lambda: bool`, `line: int`, `byte: int`, `chained_method_reference: bool=False`.

`FileImports`: `explicit: dict[str,str]` (simple→FQN), `static_methods: dict[str,str]`, `static_wildcards: list[str]`.

`AnnotationRef`: `name: str` (simple last segment), `qualified: str` (raw source text), `arguments: dict[str,str]`, `argument_kinds: dict[str,str]`, `container_capability_values: tuple[str,...]`, `container_capability_kinds: tuple[str,...]`, **`use_site_target: str|None`** (new; one of `"field"|"get"|"set"|"param"` or `None`).

`parse_java(source, *, filename) -> JavaFileAst` (existing; sets `language="java"` after Task 1).

`parse_kotlin(source, *, filename) -> JavaFileAst` (new; sets `language="kotlin"`).

## File Structure

**New files:**
- `src/java_codebase_rag/ast/language.py` — `LanguageBackend` Protocol, `LANG_BACKENDS` registry, `backend_for(path)`, `KNOWN_LANGUAGE_IDS`, `FileAst = JavaFileAst` alias.
- `src/java_codebase_rag/ast/ast_kotlin.py` — `parse_kotlin` + per-thread Kotlin `Parser` TLS.
- `tests/test_language_backend.py` — dispatch + registry tests.
- `tests/test_ast_kotlin.py` — extractor unit tests.
- `tests/fixtures/mixed-jvm/` — small mixed Java+Kotlin fixture (Java service + Kotlin controller/data class).
- `tests/test_kotlin_integration.py` — end-to-end merged-graph + cross-language edge tests.

**Modified files:** `ast/ast_java.py` (field additions), `graph/path_filtering.py`, `graph/build_ast_graph.py`, `graph/graph_enrich.py`, `index/java_index_flow_lancedb.py`, `watch/watcher.py`, `jrag.py`, `search/search_lancedb.py`, `search/search_scoring.py`, `ast/chunk_heuristics.py`, `pyproject.toml`, `docs/CODEBASE_REQUIREMENTS.md`, `README.md`.

---

### Task 1: `LanguageBackend` registry + `language` field + `FileAst` alias (Java only)

**Files:**
- Create: `src/java_codebase_rag/ast/language.py`
- Modify: `src/java_codebase_rag/ast/ast_java.py` (JavaFileAst dataclass ~L390; parse_java)
- Test: `tests/test_language_backend.py`

**Interfaces:**
- Produces:
  - `LanguageBackend` Protocol with `language_id: str`, `suffixes: tuple[str,...]`, `parse(self, source, *, filename: str) -> JavaFileAst`.
  - `JavaBackend` (in `language.py`): `language_id="java"`, `suffixes=(".java",)`, `parse` delegates to `parse_java`.
  - `LANG_BACKENDS: dict[str, LanguageBackend] = {"java": JavaBackend()}`.
  - `backend_for(path: Path | str) -> LanguageBackend | None` — returns the backend whose `suffixes` contain the path's suffix, else `None`.
  - `KNOWN_LANGUAGE_IDS: frozenset[str]` — derived from `LANG_BACKENDS` keys (`{"java"}` in this task).
  - `FileAst = JavaFileAst` (alias re-exported from `language.py`).
  - `JavaFileAst.language: str` — required, no default; `__post_init__` raises `ValueError` if `language not in KNOWN_LANGUAGE_IDS`.
- Consumes: `parse_java` (existing).

- [ ] **Step 1: Write the failing tests**

`tests/test_language_backend.py` verifies: (a) `backend_for("src/Foo.java")` returns the Java backend (`language_id == "java"`); (b) `backend_for("Foo.kt")` returns `None` (Kotlin not registered yet); (c) `backend_for("README.md")` returns `None`; (d) `JavaBackend().parse(b"package x; class F {}", filename="F.java")` returns a `JavaFileAst` with `language == "java"`; (e) constructing `JavaFileAst(package="x", imports=[], wildcard_imports=[], explicit_imports={}, top_level_types=[], all_types=[], language="nope")` raises `ValueError`; (f) omitting `language` raises `TypeError` (required field); (g) `FileAst is JavaFileAst`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_language_backend.py -v`
Expected: FAIL — `language.py` import fails / `language` field absent.

- [ ] **Step 3: Write minimal implementation**

Add `language: str` (no default) to `JavaFileAst`; add `__post_init__` that raises `ValueError` when `language` is not in `KNOWN_LANGUAGE_IDS`. `parse_java` sets `language="java"` on the returned `JavaFileAst`. Create `language.py` with the Protocol, `JavaBackend` delegating to `parse_java`, `LANG_BACKENDS`, `KNOWN_LANGUAGE_IDS` (frozenset of registry keys), `backend_for` (match path suffix against each backend's `suffixes`), and `from .ast_java import JavaFileAst as FileAst` plus a module-level `FileAst = JavaFileAst` alias. Avoid circular import: `language.py` imports `parse_java`/`JavaFileAst` from `ast_java`; `ast_java.__post_init__` imports `KNOWN_LANGUAGE_IDS` lazily inside the method (function-local import) to avoid an import cycle.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_language_backend.py -v`
Expected: PASS. Then run the Java extractor subset to confirm no regression:
Run: `.venv/bin/pytest tests/test_build_ast_graph.py -v 2>/dev/null || .venv/bin/pytest tests/ -k "java or ast or build_ast" -v`
Expected: PASS (Java path unchanged — `parse_java` now just sets `language`).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/ast/language.py src/java_codebase_rag/ast/ast_java.py tests/test_language_backend.py`
Run: `git commit -m "feat(ast): LanguageBackend registry + JavaFileAst.language field (Java only)"`

---

### Task 2: Generalize the file iterator (the chokepoint)

**Files:**
- Modify: `src/java_codebase_rag/graph/path_filtering.py:430-477` (`iter_java_source_files`)
- Modify: callers — `graph/build_ast_graph.py:535,1031,1046,4173`; `graph/graph_enrich.py:390`
- Test: `tests/test_path_filtering.py` (extend or create)

**Interfaces:**
- Produces: `iter_source_files(root, *, ignore=None) -> Iterator[Path]` (rename of `iter_java_source_files`) that yields files whose suffix matches ANY registered backend in `LANG_BACKENDS`. Keep `iter_java_source_files` as a thin alias delegating to `iter_source_files` to avoid churning every import site blindly — but UPDATE the four `build_ast_graph` callers + the one `graph_enrich` caller to call `iter_source_files`. Pruning logic (UNCONDITIONAL_PRUNE_DIRS, `_is_build_output_dir`) unchanged.
- Consumes: `LANG_BACKENDS` / backend `suffixes` from Task 1.

- [ ] **Step 1: Write the failing test**

`tests/test_path_filtering.py` verifies: in a temp tree containing `A.java`, `B.kt`, `C.txt`, `iter_source_files(root)` yields only `A.java` (Kotlin backend not registered yet, so `.kt` is not a known suffix); after the Java path the set of yielded suffixes is exactly `{".java"}`. Also verify a `.java` file inside a pruned build-output dir (alongside a pom.xml) is NOT yielded.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_path_filtering.py -v`
Expected: FAIL — `iter_source_files` not defined.

- [ ] **Step 3: Write minimal implementation**

Add `iter_source_files` derived from the existing `iter_java_source_files` body, replacing the `if not fn.endswith(".java")` gate with: collect the union of all `b.suffixes` across `LANG_BACKENDS.values()`; yield only files whose suffix is in that union. Keep `iter_java_source_files` as an alias. Update the four `build_ast_graph.py` call sites and `graph_enrich.py:390` to call `iter_source_files`. Behavior with only Java registered is identical to before.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_path_filtering.py -v`
Expected: PASS. Run a Java graph smoke test:
Run: `.venv/bin/pytest tests/ -k "build_ast or graph" -v`
Expected: PASS (Java iteration unchanged).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/graph/path_filtering.py src/java_codebase_rag/graph/build_ast_graph.py src/java_codebase_rag/graph/graph_enrich.py tests/test_path_filtering.py`
Run: `git commit -m "refactor(graph): iter_source_files dispatches by registered suffixes"`

---

### Task 3: Wire parse call sites to `backend.parse`

**Files:**
- Modify: `src/java_codebase_rag/graph/build_ast_graph.py:1068` (`pass1_parse`)
- Modify: `src/java_codebase_rag/graph/graph_enrich.py:402` (`_collect_annotation_decl_index`)
- Modify: `src/java_codebase_rag/jrag.py:3648` (`_cmd_imports`)
- Modify: `src/java_codebase_rag/index/java_index_flow_lancedb.py:390` (`_parse_and_enrich_java`)
- Test: extend `tests/test_language_backend.py`

**Interfaces:**
- Produces: each parse site calls `backend_for(path).parse(content, filename=rel)` instead of `parse_java(content, filename=rel)`. With only Java registered this is behavior-preserving.
- Consumes: `backend_for`, `LanguageBackend.parse` (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_language_backend.py`: a test that monkeypatches `LANG_BACKENDS` with a stub backend whose `parse` records the filename it was called with and returns a minimal `JavaFileAst`, then calls a thin helper that mirrors the dispatch (`backend_for(path).parse(b"", filename=str(path))`) and asserts the stub backend was selected for its suffix. (This pins the dispatch contract the four sites will use.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_language_backend.py -v`
Expected: FAIL (stub-dispatch assertion not yet wired through the helper / sites still call `parse_java` directly).

- [ ] **Step 3: Write minimal implementation**

At each of the four sites, replace `parse_java(<content>, filename=<rel>)` with `backend_for(<rel>).parse(<content>, filename=<rel>)`. Guard: if `backend_for(rel)` is `None` (unknown suffix), skip the file (it should not reach here because the iterator already filters, but guard defensively with `continue`/`return`). No other logic changes.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_language_backend.py -v`
Expected: PASS. Java regression:
Run: `.venv/bin/pytest tests/ -k "build_ast or enrich or imports or flow" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/graph/build_ast_graph.py src/java_codebase_rag/graph/graph_enrich.py src/java_codebase_rag/jrag.py src/java_codebase_rag/index/java_index_flow_lancedb.py tests/test_language_backend.py`
Run: `git commit -m "refactor: dispatch parse sites through LanguageBackend.parse"`

---

### Task 4: Add `tree-sitter-kotlin` dependency

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Test: `tests/test_ast_kotlin.py` (smoke import only, this task)

**Interfaces:**
- Produces: `tree-sitter-kotlin>=1.1.0,<2` in `[project.dependencies]`. If pip reports no macOS x86_64 wheel (check `platform_machine`), gate it with the same marker used for `torch`/`lancedb`: `; sys_platform != 'darwin' or platform_machine != 'x86_64'`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

`tests/test_ast_kotlin.py` starts with a smoke test: `import tree_sitter_kotlin` succeeds and `tree_sitter_kotlin.language()` returns a truthy language object. (This test will gate every later extractor task.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: FAIL — `tree_sitter_kotlin` not installed in the editable env yet.

- [ ] **Step 3: Write minimal implementation**

Run `.venv/bin/pip install 'tree-sitter-kotlin>=1.1.0,<2'`. Check wheel coverage: `.venv/bin/pip download --no-deps --dest /tmp/tsk-probe --platform macosx_10_9_x86_64 'tree-sitter-kotlin>=1.1.0,<2' 2>&1` — if it resolves an x86_64 macOS wheel, no marker needed; if it fails, add the Intel-Mac PEP 508 marker. Add the line to `pyproject.toml` `[project.dependencies]` (with marker if required). Reinstall editable: `.venv/bin/pip install -e ".[dev]"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: PASS (import succeeds, `language()` truthy).

- [ ] **Step 5: Commit**

Run: `git add pyproject.toml tests/test_ast_kotlin.py`
Run: `git commit -m "build: add tree-sitter-kotlin>=1.1.0 dependency"`

---

### Task 5: `parse_kotlin` foundation + `KotlinBackend` registration

**Files:**
- Create: `src/java_codebase_rag/ast/ast_kotlin.py`
- Modify: `src/java_codebase_rag/ast/language.py` (register KotlinBackend)
- Test: `tests/test_ast_kotlin.py`

**Interfaces:**
- Produces:
  - `parse_kotlin(source, *, filename: str) -> JavaFileAst` returning a `JavaFileAst` with `language="kotlin"`, populated `package`, `imports`, `wildcard_imports`, `explicit_imports`, `file_imports`, and `top_level_types`/`all_types` (empty lists in this task — declarations come in Task 6). `parse_error=True` on tree-sitter error.
  - A per-thread `Parser` over `tree_sitter_kotlin.language()` (thread-local storage, mirror `ast_java.py:228-235`).
  - `KotlinBackend` in `language.py`: `language_id="kotlin"`, `suffixes=(".kt",)`, `parse` delegates to `parse_kotlin`. Registered in `LANG_BACKENDS` **only if** `import tree_sitter_kotlin` succeeds (try/except around the import at module load; on failure, `KotlinBackend` is absent and `.kt` files are skipped).
  - After registration, `KNOWN_LANGUAGE_IDS` includes `"kotlin"` and `backend_for("F.kt")` returns `KotlinBackend`.
- Consumes: `JavaFileAst`, `FileImports` shapes; `tree_sitter_kotlin` (Task 4).

- [ ] **Step 1: Write the failing tests**

`tests/test_ast_kotlin.py` verifies for input `b"package com.foo\n\nimport com.bar.Baz\nimport com.qux.*\n"`: (a) returned `language == "kotlin"`; (b) `package == "com.foo"`; (c) `imports == ["com.bar.Baz", "com.qux.*"]`; (d) `explicit_imports == {"Baz": "com.bar.Baz"}`; (e) `wildcard_imports == ["com.qux"]`; (f) `file_imports.explicit == {"Baz": "com.bar.Baz"}`, `file_imports.static_methods == {}`, `file_imports.static_wildcards == []`; (g) `top_level_types == []` and `all_types == []`; (h) `parse_error is False`. Also: `backend_for("F.kt").language_id == "kotlin"`; an aliased import `import com.bar.Baz as Q` records `explicit_imports["Q"] == "com.bar.Baz"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: FAIL — `parse_kotlin` not defined / KotlinBackend not registered.

- [ ] **Step 3: Write minimal implementation**

`ast_kotlin.py`: per-thread `Parser` TLS over `tree_sitter_kotlin.language()`. `parse_kotlin(source, *, filename)`: parse the bytes; read `package_header > qualified_identifier` for the package (empty string if absent); walk top-level `import` nodes — for each, read the child `qualified_identifier` text and an optional trailing `identifier` alias; populate `imports` (raw, with `.*` for wildcards), `wildcard_imports` (the package prefix for `.*`), `explicit_imports` (simple-or-alias → FQN), and `file_imports.explicit`. Set `language="kotlin"`. Return `JavaFileAst` with empty `top_level_types`/`all_types`. Set `parse_error` from the tree-sitter error flag. In `language.py`: try `import tree_sitter_kotlin`; on success import `parse_kotlin` and add `KotlinBackend` to `LANG_BACKENDS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py tests/test_language_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/ast/ast_kotlin.py src/java_codebase_rag/ast/language.py tests/test_ast_kotlin.py`
Run: `git commit -m "feat(ast): parse_kotlin foundation (package, imports) + KotlinBackend"`

---

### Task 6: Kotlin type declarations — folded kinds + top-level-fn facade

**Files:**
- Modify: `src/java_codebase_rag/ast/ast_kotlin.py`
- Test: `tests/test_ast_kotlin.py`

**Interfaces:**
- Produces: `parse_kotlin` now populates `top_level_types` and `all_types` with `TypeDecl`s for Kotlin declarations per the kind-fold map. The facade for top-level functions is introduced here as a `TypeDecl` (its members arrive in Task 8). Kind map (contract): `class`→`class`; `interface` (discriminate via `interface` keyword child on a `class_declaration`)→`interface`; `enum class` (discriminate via `modifiers > class_modifier` text `enum` + `enum_class_body`)→`enum`; `annotation class` (`class_modifier[annotation]`)→`annotation`; `data class` (`class_modifier[data]`)→`record`; `object Singleton` (`object_declaration`)→`class`; `companion object` (`companion_object`)→`class`, nested under its enclosing `TypeDecl` with `name="Companion"` (or the declared name). `fqn` = `<package>.<name>` (nested: `<outer_fqn>.<name>`). `outer_fqn=None` for top-level. A file with ≥1 top-level function (handled in Task 8) gets a synthetic facade `TypeDecl` named per `@file:JvmName` or `<Basename>Kt` (Task 9 sets the exact name; here register the facade slot when top-level functions exist).
- Consumes: `TypeDecl` shape; `parse_kotlin` foundation (Task 5).

- [ ] **Step 1: Write the failing tests**

For each fixture assert the produced `TypeDecl.kind`, `name`, `fqn`, `outer_fqn`, and nesting: (a) `class Foo` in `package com.x` → one top-level `TypeDecl{name="Foo", kind="class", fqn="com.x.Foo", outer_fqn=None}`; (b) `interface Bar` → `kind="interface"`; (c) `enum class E { A, B }` → `kind="enum"`; (d) `annotation class Ann` → `kind="annotation"`; (e) `data class D(val i: Int)` → `kind="record"`; (f) `object Single` → `kind="class"`; (g) `class Outer { companion object { } }` → `Outer` has a `nested` entry `TypeDecl{name="Companion", kind="class", outer_fqn="com.x.Outer"}` and `all_types` contains both `Outer` and `Outer.Companion`. Also assert a file with no declarations yields `top_level_types == []`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: FAIL — declarations not yet produced.

- [ ] **Step 3: Write minimal implementation**

In `parse_kotlin`, after imports, walk the `source_file` children for `class_declaration` / `object_declaration` / `companion_object` nodes. For a `class_declaration`, determine the keyword/modifier: if an anonymous `interface` keyword child → `interface`; elif `modifiers > class_modifier` text is `enum` → `enum`; `annotation` → `annotation`; `data` → `record`; else `class`. For `object_declaration` → `class`. Build `TypeDecl` with name from the `identifier` child, `fqn`/`outer_fqn` from package + nesting. Recurse into `class_body`/`enum_class_body` for nested `companion_object`/`class_declaration`, attaching to `nested` and adding to `all_types`. (Members — fields/methods — are Task 8; do not populate yet.) Do not extend `_TYPE_KINDS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/ast/ast_kotlin.py tests/test_ast_kotlin.py`
Run: `git commit -m "feat(ast_kotlin): type declarations with folded kinds + nested companion"`

---

### Task 7: Members — functions, properties (+accessors), modifiers

**Files:**
- Modify: `src/java_codebase_rag/ast/ast_kotlin.py`
- Test: `tests/test_ast_kotlin.py`

**Interfaces:**
- Produces: `parse_kotlin` populates `TypeDecl.methods` (`MethodDecl`) and `TypeDecl.fields` (`FieldDecl`). For each non-private `property_declaration` (`var`/`val`), emit a `FieldDecl` **plus** synthesized JVM-accessor `MethodDecl`s so cross-language CALLS resolve (B1): `var name` → `getName()` (return_type = property type) and `setName(T)` (one `ParamDecl`); `val name` → `getName()` only; a `Boolean`-typed `val`/`var` named `foo` (or `isFoo`) → `isFoo()` instead of `getName()`. Synthesized accessors carry `modifiers` including `"final"` for `val` getters / `var` setters as appropriate, and `"static"` when the property is in a companion object or annotated `@JvmStatic`/`@JvmField`-equivalent (see modifier map). Functions (`function_declaration`) → `MethodDecl` with `is_constructor` true only for `constructor(...)`; primary/secondary constructors populate `parameters` from the constructor header. Modifier vocabulary (B2) emitted into the shared `modifiers` list (Java literals): companion-object member / `@JvmStatic` / top-level function → includes `"static"`; `val` getter & default `class`/`fun` → includes `"final"`; `open` → omits `"final"`. Kotlin-only tokens (`suspend`, `inline`, `operator`, `infix`, `tailrec`, `data`, `sealed`) ride along in `modifiers` unchanged. `signature` format `"name(T1,T2)"` using simple param type names; `return_type` simple name (`""` for constructors, `"void"`-equivalent left as the Kotlin `Unit` simple name is acceptable as `""`).
- Consumes: `TypeDecl`, `MethodDecl`, `FieldDecl`, `ParamDecl`, `AnnotationRef` shapes (Task 6).

- [ ] **Step 1: Write the failing tests**

Fixtures + assertions: (a) `class P(var name: String)` → `fields` has one `FieldDecl{name="name", type_name="String"}`, and `methods` contains synthesized `MethodDecl{name="getName", is_constructor=False}` and `{name="setName", parameters=[ParamDecl{name=non-empty, type_name="String"}]}`; (b) `class P(val b: Boolean)` → methods contains `{name="isB"}` and NOT `getName`; (c) `class P(val i: Int)` → methods contains `{name="getI"}` only (no setter); (d) a private `private val x` → no synthesized accessor (only the `FieldDecl`); (e) `class C { fun go(a: Int): String { } }` → `MethodDecl{name="go", return_type="String", signature="go(Int)", is_constructor=False, parameters=[ParamDecl{name="a", type_name="Int"}]}`; (f) `class C { constructor(a: Int) }` → a `MethodDecl{is_constructor=True}`; (g) `class C { companion object { val CONST = 1 } }` → the `CONST` accessor `MethodDecl` has `"static"` in `modifiers`; (h) `suspend fun s()` → `modifiers` contains `"suspend"` (ride-along) and the method exists.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: FAIL — members not produced.

- [ ] **Step 3: Write minimal implementation**

Within each `TypeDecl`'s body walk `property_declaration` and `function_declaration` children. Property: name from `variable_declaration > identifier`; type from the type annotation (`user_type`/`nullable_type`); emit `FieldDecl`. If the property is not private (`visibility_modifier` ≠ `private`), synthesize accessor `MethodDecl`s per the JVM convention above, deriving `static` from enclosing companion/`@JvmStatic`. Function: name from `identifier` field `name`; `is_constructor` when the keyword is `constructor`; params from `function_value_parameters > function_value_parameter > parameter > (simple_identifier/identifier, user_type)`; return type from the `: type` child (`""` for constructors). Build `modifiers` by walking the `modifiers` container's typed sub-containers, mapping keyword text to the Java vocabulary plus ride-alongs. Compute `signature`/`return_type`. Populate `methods`/`fields` on the enclosing `TypeDecl` (including the facade for top-level functions — Task 9 names it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/ast/ast_kotlin.py tests/test_ast_kotlin.py`
Run: `git commit -m "feat(ast_kotlin): functions, properties + synthesized JVM accessors, modifier vocabulary"`

---

### Task 8: Annotations (with use-site targets) + extends/implements partition

**Files:**
- Modify: `src/java_codebase_rag/ast/ast_kotlin.py`
- Modify: `src/java_codebase_rag/ast/ast_java.py` (`AnnotationRef` ~L242 — add `use_site_target`)
- Test: `tests/test_ast_kotlin.py`

**Interfaces:**
- Produces:
  - `AnnotationRef.use_site_target: str|None` (new field, default `None`) on the shared `AnnotationRef`. Java `parse_java` leaves it `None`.
  - `parse_kotlin` emits `AnnotationRef` for each Kotlin `annotation` node, capturing `name` (simple, from `user_type > identifier` last segment), `qualified` (raw text), `arguments`/`argument_kinds` from `constructor_invocation > value_arguments` (mirror the Java annotation-arg extraction), and `use_site_target` from `annotation > use_site_target` (one of `"field"|"get"|"set"|"param"`, else `None`). Parameter annotations (under `parameter_modifiers`) attach to the `ParamDecl`; property annotations (under `modifiers`) attach to the `FieldDecl` and, where the target implies it, to the corresponding synthesized accessor `MethodDecl`.
  - `extends`/`implements` partition (B7-soft): for a `class_declaration`'s `:` supertype list (`super_specifier` children), each supertype is placed in `extends` if it is a known class (declared in the same compilation unit as `kind in {"class","record","enum"}`) else in `implements`. **Unknown supertypes default to `implements`.** Interfaces have only `implements`. Supertype simple names (generics/nullable stripped). No cross-file resolution in the extractor.
- Consumes: `AnnotationRef`, `TypeDecl`, `MethodDecl`, `ParamDecl`, `FieldDecl` shapes.

- [ ] **Step 1: Write the failing tests**

Assertions: (a) `@RestController class H` → `TypeDecl.annotations` contains `AnnotationRef{name="RestController", use_site_target=None}`; (b) `class C(@param:Autowired val r: Repo)` → the `r` `ParamDecl.annotations` contains `AnnotationRef{name="Autowired", use_site_target="param"}`, and the synthesized `getR` accessor does NOT carry `Autowired`; (c) `@get:Column val n` → the `n` `FieldDecl` has no `Column`, but the synthesized `getN` `MethodDecl.annotations` contains `AnnotationRef{name="Column", use_site_target="get"}`; (d) `class D : Base(c), Iface` where `Base` is declared `class Base` in the same file → `extends == ["Base"]`, `implements == ["Iface"]`; (e) `class D : ExternalBase` (unknown) → `implements == ["ExternalBase"]`, `extends == []`; (f) a Java `AnnotationRef` constructed by `parse_java` has `use_site_target is None`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py tests/test_language_backend.py -v`
Expected: FAIL — `use_site_target` absent / annotations not extracted.

- [ ] **Step 3: Write minimal implementation**

Add `use_site_target: str|None = None` to `AnnotationRef`. In `parse_kotlin`, extract annotations from the `modifiers` container (and `parameter_modifiers` for params): read `annotation > use_site_target` token text for the target; read `constructor_invocation > user_type` for the name/qualified; read `value_arguments` for args (reuse the Java arg-extraction approach by shape). Route each annotation to the correct slot per its target. For the supertype partition: collect the `:` clause entries; for each, strip `<...>`/`?`; classify via the in-CU declared-kind map; unknown → `implements`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py tests/test_language_backend.py -v`
Expected: PASS. Java regression on annotation extraction:
Run: `.venv/bin/pytest tests/ -k "annotation" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/ast/ast_java.py src/java_codebase_rag/ast/ast_kotlin.py tests/test_ast_kotlin.py`
Run: `git commit -m "feat(ast_kotlin): annotations w/ use-site targets + extends/implements partition"`

---

### Task 9: `@file:JvmName` / `@file:JvmMultifileClass` facade naming + merge

**Files:**
- Modify: `src/java_codebase_rag/ast/ast_kotlin.py`
- Test: `tests/test_ast_kotlin.py`

**Interfaces:**
- Produces: the synthetic top-level-function facade `TypeDecl` is named correctly and multifile groups merge (B5). Facade name resolution: if the file has `@file:JvmName("X")` (`file_annotation > constructor_invocation > user_type > identifier == "JvmName"`, name from the string argument), the facade is named `X`; else `<Basename>Kt` where `<Basename>` is the source filename stem with the `.kt` suffix removed, title-cased per Kotlin's rule (the compiler uses the exact filename stem + `Kt`; use the filename stem as given + `Kt`). When two or more files share the same `@file:JvmName("X")` AND each also has `@file:JvmMultifileClass`, all their top-level functions/properties merge into ONE facade `TypeDecl` (FQN `<package>.X`) accumulating members across files. A `parse_kotlin` call parses a single file; therefore expose a module-level merge helper `merge_multifile_facades(asts: list[JavaFileAst]) -> list[JavaFileAst]` that groups facades by `(package, facade_name, is_multifile)` and concatenates the facade TypeDecl's `methods`/`fields` (deduping identical signatures), leaving non-facade types untouched. Non-multifile files keep their own facade.
- Consumes: `parse_kotlin` (Tasks 5–8); `JavaFileAst` shape.

- [ ] **Step 1: Write the failing tests**

Assertions: (a) file `foo.kt` with `@file:JvmName("Custom")` and a top-level `fun go()` → facade `TypeDecl{name="Custom", fqn="<pkg>.Custom"}` whose `methods` contains `go`; facade name is NOT `FooKt`; (b) file `bar.kt` with no file-annotation and a top-level `fun go()` → facade name `BarKt`; (c) two parsed `JavaFileAst`s for `a.kt`/`b.kt`, both `@file:JvmName("X") @file:JvmMultifileClass()`, each with one distinct top-level function → after `merge_multifile_facades([ast_a, ast_b])`, exactly ONE facade `TypeDecl` named `X` whose `methods` contains both functions, and the total `top_level_types` across the result has one `X` (not two); (d) two files with the same `@file:JvmName("X")` but WITHOUT `@JvmMultifileClass` → `merge_multifile_facades` leaves them as two separate facades (no merge) — this is the collision case; it does NOT silently overwrite (the merge helper returns both, and a warning is emitted — see Task 16 for the graph-level warning; here just assert both facades survive in the output list).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: FAIL — facade naming/merge not implemented.

- [ ] **Step 3: Write minimal implementation**

In `parse_kotlin`, read `file_annotation` nodes at the top of `source_file`: detect `JvmName` (capture the string arg) and `JvmMultifileClass`. Name the facade accordingly and mark it (e.g., add a sentinel to `capabilities` such as `"kotlin_facade"` and `"kotlin_multifile"` so downstream — Task 13 resolution and the graph builder — can recognize it without a new field/column). Implement `merge_multifile_facades`: group `JavaFileAst`s by `(package, facade_name)` where the facade is multifile; for each group, concatenate member lists onto one facade `TypeDecl` (dedupe identical `name`+`signature`); return the reshaped `JavaFileAst` list. The index flow (Task 11) calls `merge_multifile_facades` across a module's Kotlin ASTs before graph build.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/ast/ast_kotlin.py tests/test_ast_kotlin.py`
Run: `git commit -m "feat(ast_kotlin): @file:JvmName facade naming + @JvmMultifileClass merge"`

---

### Task 10: Call sites + constructor delegation

**Files:**
- Modify: `src/java_codebase_rag/ast/ast_kotlin.py`
- Test: `tests/test_ast_kotlin.py`

**Interfaces:**
- Produces: `parse_kotlin` populates each `MethodDecl.call_sites` (`CallSite`). For a `call_expression > navigation_expression` (receiver call) → `receiver_expr` = receiver text, `callee_simple` = last `identifier`, `is_static_call` true when the receiver is a type name (capitalized heuristic is NOT acceptable — defer to Task 13 resolution; set `is_static_call` true when the receiver text matches an imported type or same-CU type, else false), `is_constructor` false. For `call_expression` whose callee target is a type name with no receiver (`Foo(...)` — no `new` in Kotlin) → `is_constructor=True`, `callee_simple="<init>"`, `receiver_expr="Foo"`. Constructor delegation `: Super(...)` / `: this(...)` in a constructor header → a `CallSite` with `callee_simple="<init>"` on the relevant super/this type at the constructor's start byte. `arg_count` = number of `value_argument` children (`-1` for `::` method references). `chained_method_reference=True` for `expr::name` where `expr` is a call chain. `caller_fqn` = `<type_fqn>#<signature>`. Bare receiverless calls (`foo()`) → `receiver_expr=""`, `callee_simple="foo"` (Task 13 resolves these against the file facade).
- Consumes: `CallSite` shape; `MethodDecl`; Task 7 signatures.

- [ ] **Step 1: Write the failing tests**

Fixture `class C(val r: Repo) { fun go() { r.find(1); Other(2) } }` → `go`'s `call_sites` contains: (a) a `CallSite{callee_simple="find", receiver_expr="r", arg_count=1, is_constructor=False, is_static_call=False}`; (b) a `CallSite{callee_simple="<init>", receiver_expr="Other", arg_count=1, is_constructor=True}`. Also: a top-level `fun util()` calling bare `helper()` → `call_sites` has `CallSite{callee_simple="helper", receiver_expr="", is_constructor=False}`. Constructor delegation `class D : Base(7)` → the constructor `MethodDecl.call_sites` contains `CallSite{callee_simple="<init>", receiver_expr="Base", is_constructor=True, arg_count=1}`. `repo.findById(1).orElse(null)` → one `CallSite{callee_simple="findById",...}` and one `CallSite{callee_simple="orElse",...}`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: FAIL — call sites not extracted.

- [ ] **Step 3: Write minimal implementation**

Walk each function/constructor body and header for `call_expression` nodes. Split receiver vs callee via `navigation_expression` (left spine = receiver text; last `identifier` = callee). Classify constructor calls (callee is a type name, no navigation receiver). Emit `CallSite`s with correct flags/counts. Set `caller_fqn` from the enclosing method. Leave resolution (static-vs-instance certainty, facade calls) to Task 13; here just capture the raw call shape faithfully.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ast_kotlin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/ast/ast_kotlin.py tests/test_ast_kotlin.py`
Run: `git commit -m "feat(ast_kotlin): call-site extraction + constructor delegation"`

---

### Task 11: cocoindex flow — `process_kotlin_file` + matcher + counts

**Files:**
- Modify: `src/java_codebase_rag/index/java_index_flow_lancedb.py` (matcher ~L681; `_approximate_vectors_total` ~L241; `_parse_and_enrich_java` ~L390; `app_main` ~L608; warm-up ~L656; `_drain_files_concurrently` ~L726)
- Test: `tests/test_kotlin_flow.py`

**Interfaces:**
- Produces:
  - A `**/*.kt` `localfs.walk_dir` matcher in `app_main` (parallel to the `**/*.java` matcher).
  - `process_kotlin_file` — a `@coco.fn` bound to the existing `JavaLanceChunk` `TableTarget` (the chunk schema fields `primary_type_kind`, `role`, `capabilities` are language-agnostic). It calls `backend_for(path).parse` (= `parse_kotlin`), then runs the SAME enrichment (`enrich_chunk`/`classify`) used by `process_java_file`, producing chunks with the `language` field set to `"kotlin"`.
  - `_approximate_vectors_total` counts all registered suffixes (`.java` + `.kt`).
  - `app_main` runs `merge_multifile_facades` over the parsed Kotlin ASTs of a module before graph build (Task 9). Concretely: the flow parses Kotlin files, groups by module, applies `merge_multifile_facades`, then drains. (If cocoindex's dataflow shape makes a cross-file pre-pass awkward, the merge may instead run inside `build_ast_graph`'s pass1 over the parsed ASTs — the implementer picks the cheaper correct location; the contract is that multifile facades are merged before `_register_type`.)
  - Annotation meta-chain warm-up (`:656`) runs before the parse loop and now covers `.kt` via `iter_source_files`.
- Consumes: `parse_kotlin`, `merge_multifile_facades` (Tasks 5–10); `backend_for`; `JavaLanceChunk` table.

- [ ] **Step 1: Write the failing test**

`tests/test_kotlin_flow.py` (temp-dir index, fresh) builds a tiny mixed module (one `.java` `@Service`, one `.kt` `@RestController` that injects it), runs the index flow, and asserts: (a) the LanceDB chunk table contains chunks from BOTH files; (b) the Kotlin controller chunk has `language="kotlin"` (or the equivalent marker the table stores); (c) `_approximate_vectors_total` over the temp root returns the count of `.java` + `.kt` files (not just `.java`). (Cross-language edge assertions are Task 15.)

- [ ] **Step 2: Run test to verify it fail**

Run: `.venv/bin/pytest tests/test_kotlin_flow.py -v`
Expected: FAIL — no `.kt` matcher / `process_kotlin_file`.

- [ ] **Step 3: Write minimal implementation**

Add the `**/*.kt` matcher + `process_kotlin_file` cocoindex fn writing `JavaLanceChunk`. Mirror `process_java_file`'s enrichment but dispatch parsing through `backend_for`. Update `_approximate_vectors_total` to sum counts for `.java` and `.kt`. Ensure warm-up covers `.kt`. Wire `merge_multifile_facades` into the parse path (chosen location per the Produces note).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_kotlin_flow.py -v`
Expected: PASS. Java flow regression:
Run: `.venv/bin/pytest tests/ -k "flow or index" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/index/java_index_flow_lancedb.py tests/test_kotlin_flow.py`
Run: `git commit -m "feat(index): process_kotlin_file + .kt matcher + multifile merge in flow"`

---

### Task 12: Watcher dispatch

**Files:**
- Modify: `src/java_codebase_rag/watch/watcher.py:57` (`INDEXED_SUFFIXES`), `:242` (`_classify`)
- Test: `tests/test_watcher.py` (extend)

**Interfaces:**
- Produces: `INDEXED_SUFFIXES` is derived from the registry (`(".java", ".kt")` when Kotlin is registered; just `(".java",)` when `tree_sitter_kotlin` import failed). `_classify` dispatches a changed file to its backend via `backend_for(path)`; a `.kt` change reprocesses via `KotlinBackend`; an unknown suffix is ignored.
- Consumes: `LANG_BACKENDS`/`backend_for`.

- [ ] **Step 1: Write the failing test**

`tests/test_watcher.py` asserts: `INDEXED_SUFFIXES` contains `.kt` when `tree_sitter_kotlin` is importable (monkeypatch/assume present in the dev env); `_classify(Path("src/Foo.kt"))` routes to the Kotlin backend (returns a classification indicating Kotlin); `_classify(Path("README.md"))` returns None/ignored.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_watcher.py -v`
Expected: FAIL — `.kt` not in `INDEXED_SUFFIXES`.

- [ ] **Step 3: Write minimal implementation**

Compute `INDEXED_SUFFIXES` from the union of registered backend suffixes. In `_classify`, use `backend_for(path)` to pick the reprocessing path; skip unknown suffixes.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_watcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/watch/watcher.py tests/test_watcher.py`
Run: `git commit -m "feat(watch): dispatch .kt changes via LanguageBackend"`

---

### Task 13: Kotlin resolution model (additive, Kotlin-gated)

**Files:**
- Modify: `src/java_codebase_rag/graph/build_ast_graph.py` (`_resolve_simple` ~L1122; `pass3_calls` call-resolution region; phantom set `_JAVA_LANG_SIMPLE` ~L1189)
- Test: `tests/test_kotlin_resolution.py`

**Interfaces:**
- This is the ONE additive graph-builder change. It is gated on the calling file being Kotlin (`language == "kotlin"`) so Java resolution is byte-identical.
- Produces:
  - A Kotlin default-import known-type set (`kotlin.*`, `kotlin.collections.*`, `kotlin.sequences.*`, `kotlin.io.*`, `kotlin.ranges.*`, `kotlin.text.*`, `kotlin.comparisons.*`, `kotlin.jvm.*`, `kotlin.annotation.*`, `kotlin.reflect.*`, `kotlin.math.*`, `kotlin.contracts.*`) consulted by the resolver's phantom fallback for Kotlin files, so e.g. `List`, `Map`, `Sequence`, `Pair`, `Triple` are not phantoms.
  - Receiverless free-function calls (`CallSite.receiver_expr == ""`) from a Kotlin file resolve against that file's synthetic facade `TypeDecl` (recognized via the `"kotlin_facade"` capability from Task 9): the facade's `methods` are candidate targets for bare calls in the same file. The existing `pass3_calls` candidate lookup is extended to include the file's facade members for Kotlin receiverless calls only.
- Consumes: `JavaFileAst.language`; facade `TypeDecl` (`"kotlin_facade"` capability); `_resolve_simple`/`pass3_calls` existing behavior.

- [ ] **Step 1: Write the failing test**

`tests/test_kotlin_resolution.py` (temp-dir merged index): a Kotlin file with `fun helper() {}` and `fun caller() { helper() }` (both top-level, same file) → the merged graph contains a `CALLS` edge from `caller` to `helper` (resolved, not phantom). A second scenario: a Kotlin file referencing `kotlin.collections.List` implicitly (`val xs: List<Int>`) resolves the type `List` to a known (non-phantom) symbol or at least does NOT crash the resolver. A Java regression scenario: the same call shapes in a Java file resolve identically to before (no behavior change) — assert an existing Java CALLS edge count is unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_kotlin_resolution.py -v`
Expected: FAIL — Kotlin facade/default-import resolution not present.

- [ ] **Step 3: Write minimal implementation**

Add the Kotlin default-import set. In `_resolve_simple`, when the owning file's `language == "kotlin"`, additionally treat the default-import simple names as known (non-phantom). In `pass3_calls`, when a `CallSite` has `receiver_expr == ""` and the owning file is Kotlin, add the file's facade `TypeDecl.methods` (matched by `callee_simple` + `arg_count`) to the candidate set. Gate every addition on `language == "kotlin"` so the Java path is untouched. Do not change `_TYPE_KINDS`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_kotlin_resolution.py -v`
Expected: PASS. Java resolution regression:
Run: `.venv/bin/pytest tests/ -k "build_ast or resolve or call" -v`
Expected: PASS (Java CALLS edge counts unchanged).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/graph/build_ast_graph.py tests/test_kotlin_resolution.py`
Run: `git commit -m "feat(graph): Kotlin default imports + facade free-function resolution (Kotlin-gated)"`

---

### Task 14: Search scoring + chunk-heuristic language generalization

**Files:**
- Modify: `src/java_codebase_rag/search/search_lancedb.py:405,434,540,767`; `src/java_codebase_rag/search/search_scoring.py:245,292`; `src/java_codebase_rag/ast/chunk_heuristics.py:20-41`
- Test: `tests/test_search_kotlin.py`

**Interfaces:**
- Produces: the `kind == "java"` / `_kind == "java"` branches in scoring generalize to also include Kotlin (the additive role/bonus weighting now applies to Kotlin symbols whose `language == "kotlin"`). `chunk_heuristics` gains a Kotlin branch keyed off `language` (Kotlin `import`/`fun`/`object`/`class` density) parallel to the Java branch.
- Consumes: the `language` field on chunks/symbols.

- [ ] **Step 1: Write the failing test**

`tests/test_search_kotlin.py` asserts: a Kotlin `@RestController` controller chunk receives the same role weighting as an equivalent Java controller when scored (i.e., the bonus that was Java-only now applies to Kotlin); and `chunk_heuristics` for a Kotlin source string classifies `import` lines correctly (import-density heuristic fires for Kotlin `import` lines, not only Java `import`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_search_kotlin.py -v`
Expected: FAIL — Kotlin excluded from role/bonus weighting.

- [ ] **Step 3: Write minimal implementation**

Change the gating predicates from `kind == "java"` (or `_kind == "java"`) to `kind in ("java", "kotlin")` / `language in ("java", "kotlin")` as appropriate to each site (prefer the `language` field where the row carries it). In `chunk_heuristics`, branch on `language == "kotlin"` for the Kotlin type/import regex (Kotlin `fun`/`object`/`class`/`interface` declaration lines and `import` lines).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_search_kotlin.py -v`
Expected: PASS. Java search regression:
Run: `.venv/bin/pytest tests/ -k "search or scoring" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/search/ src/java_codebase_rag/ast/chunk_heuristics.py tests/test_search_kotlin.py`
Run: `git commit -m "feat(search): generalize java-only branches to kotlin (role weighting + chunk heuristics)"`

---

### Task 15: Cross-language integration tests (the core proof)

**Files:**
- Create: `tests/fixtures/mixed-jvm/` (a Java `@Service` + Kotlin `@RestController` + Kotlin data class)
- Create: `tests/test_kotlin_integration.py`
- Test: this task IS the test.

**Interfaces:**
- Produces: a merged-graph integration test proving cross-language edges and Spring-detector parity.
- Consumes: the full pipeline (Tasks 1–14).

- [ ] **Step 1: Write the failing tests**

`tests/fixtures/mixed-jvm/` contains: `src/main/java/com/foo/UserService.java` (`@Service class UserService { public String getById(Long id){...} }`), `src/main/kotlin/com/foo/UserController.kt` (`@RestController class UserController(val userService: UserService) { @GetMapping fun get(): String = userService.getById(1) }`), and `src/main/kotlin/com/foo/UserDto.kt` (`data class UserDto(val name: String)`). `tests/test_kotlin_integration.py` builds a fresh index in a temp dir and asserts: (a) a `CALLS` edge from Kotlin `UserController.get` to Java `UserService.getById` (resolved); (b) `UserController` is a `CONTROLLER` node (Spring role reused on Kotlin); (c) an `INJECTS` edge into `UserController` with mechanism `constructor` (Kotlin primary-constructor injection via `@param`/default); (d) a Java caller `someJavaCode.getName()` on a Kotlin `UserDto` resolves to the synthesized `getName` accessor (B1 parity); (e) `IMPLEMENTS` if a Kotlin class implements a Java interface in the fixture; (f) querying via `jrag`/MCP returns both languages. (Add the Java caller for (d) as a small `.java` file in the fixture.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_kotlin_integration.py -v`
Expected: FAIL (until the pipeline is complete; some assertions may already pass — run to see which).

- [ ] **Step 3: Write minimal implementation**

No production code is expected in this task — it is the acceptance gate. If an assertion fails, the failing behavior points back to the responsible task (1 = accessors, 6 = kinds, 8 = injection, 13 = resolution). Fix the responsible task, not this one. Only add fixture files here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_kotlin_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add tests/fixtures/mixed-jvm/ tests/test_kotlin_integration.py`
Run: `git commit -m "test: cross-language integration — merged graph edges + Spring parity"`

---

### Task 16: Docs update + same-FQN warning

**Files:**
- Modify: `docs/CODEBASE_REQUIREMENTS.md` (amend "Java only" — A.1 § Language & build)
- Modify: `README.md` (note Kotlin support in the scope/install sections)
- Modify: `docs/CONFIGURATION.md` / `docs/AGENT-GUIDE.md` if they assert Java-only
- Modify: `graph/build_ast_graph.py` (`_register_type` ~L970) — emit a warning on same-FQN collision
- Test: extend `tests/test_kotlin_resolution.py` or `test_build_ast_graph.py`

**Interfaces:**
- Produces: docs reflect that `.kt` is indexed alongside `.java` (mixed JVM repos), with the documented v1 limitations (extension-function Kotlin→Kotlin calls unresolved; Kotlin-native frameworks beyond Spring out of scope; generated-code classification Java-only). The graph builder emits a warning when two distinct source files register the same type FQN (the same-FQN Kotlin+Java collision case).
- Consumes: completed pipeline.

- [ ] **Step 1: Write the failing test**

A test that registers two `TypeDecl`s with identical `fqn` from different `file_path`s through `_register_type` (or the public graph-build entry) asserts a warning is emitted (use `pytest.warns` or a captured log), and that the build does not crash.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/ -k "same_fqn or collision" -v`
Expected: FAIL — no warning emitted.

- [ ] **Step 3: Write minimal implementation**

In `_register_type`, before `tables.types[decl.fqn] = entry` overwrites, if `decl.fqn` already exists with a different `file_path`, emit a warning (filename + fqn). Update the docs: `CODEBASE_REQUIREMENTS.md` A.1 now says Java **and** Kotlin are indexed (`.java` + `.kt`); state the v1 limitations. Update `README.md` scope + the Intel-Mac note (Kotlin indexing also gated if `tree-sitter-kotlin` has no x86_64 wheel).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/ -k "same_fqn or collision" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add docs/CODEBASE_REQUIREMENTS.md README.md src/java_codebase_rag/graph/build_ast_graph.py tests/`
Run: `git commit -m "docs+graph: document Kotlin support; warn on same-FQN collision"`

---

## Self-Review (run before handoff)

1. **Code scan:** No method bodies, algorithms, or test/impl code in the plan — only behavior, expected results, signatures, data shapes. ✓ (Verify each task step describes behavior, not code.)
2. **Self-containment:** Each task restates its Consumes/Produces with real field names; an implementer need not read the spec. (The Shared Contracts block + per-task Produces cover this.)
3. **Spec coverage:** Spec B1→Task 7; B2→Task 7; B3→Tasks 2–3; B4→Task 6; B5→Task 9; B6→Task 8; B7→Task 8; B8→Task 13; B9→Task 11; grammar contract→Tasks 5–10; provenance→Task 1; graceful degradation→Tasks 1/4 (conditional registration); zero-Java-regression→every task's regression step. Docs→Task 16.
4. **Placeholder scan:** No TBD/TODO; each error/edge case spelled out (private property → no accessor; unknown supertype → implements; missing tree-sitter-kotlin → skip; same-FQN → warn).
5. **Type consistency:** `parse_kotlin`, `KotlinBackend`, `backend_for`, `iter_source_files`, `merge_multifile_facades`, `use_site_target`, `kotlin_facade` capability, `language` field — names used consistently across tasks.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/active/2026-07-15-kotlin-support.md`.
