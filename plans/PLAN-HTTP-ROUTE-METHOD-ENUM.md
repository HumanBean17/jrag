# Plan: HTTP brownfield enum method, `@CodebaseHttpClient` rename, inbound exclusivity

Status: **active (planning)**. This plan implements
[`propose/HTTP-ROUTE-METHOD-ENUM-PROPOSE.md`](../propose/HTTP-ROUTE-METHOD-ENUM-PROPOSE.md).

Depends on: **none** (lands on current `master`).

## Goal

- Ship a shared **`CodebaseHttpMethod`** enum (seven verbs: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`) used by **`@CodebaseHttpRoute`** and **`@CodebaseHttpClient`** source stubs; `method` is **mandatory** on clients; **no** string-typed `method` on annotations after cutover.
- **Rename** `@CodebaseClient` / `@CodebaseClients` → **`@CodebaseHttpClient`** / **`@CodebaseHttpClients`** with **no** backward-compat alias in the extractor.
- **Brownfield-exclusivity** for inbound HTTP: change `_merge_layer_c_codebase_routes` so layer-C HTTP routes **replace** same-method built-in HTTP rows (mirror the existing async branch), closing the merge-vs-replace asymmetry documented in the propose §6 Q4.
- **Observability**: structured **`brownfield-exclusivity-shadowing`** (INFO) when a method has brownfield HTTP annotations **and** shadowable framework annotations on the same method (**extractor co-presence** in `ast_java.py`, PR-2); structured **`brownfield-method-string-literal`** (WARN) when `method` is still a string literal mid-migration. **`_merge_layer_c_codebase_routes`** implements inbound replace **only** — it does **not** emit INFO shadowing (avoids merge-only triggers and double logs). PR-1 ships the shared emitter machinery **without** production call sites; PR-2 wires both events.
- **Wire format unchanged**: `Route.attrs.http_method` / `Client.attrs.http_method` remain **strings** (enum `.name()`); YAML `method` keys remain **strings**.
- **Docs**: operator/agent-facing text updated for rename, enum, exclusivity, UC10 caveat; completed v2 propose gets an **addendum** file (immutable parent + new file).

## Principles (do not relitigate in review)

- **Brownfield-exclusivity.** When `@CodebaseHttpRoute` or `@CodebaseHttpClient` is present, framework introspection for the facets that annotation declares is **not merged** with brownfield for that axis; inbound HTTP alignment is **replace**, not field-by-field merge onto a surviving built-in row.
- **No `INHERIT` / `ANY` / string `method` on the annotation surface after PR-2.** Closed sets use enums; YAML stays string-typed by explicit decision.
- **Breaking changes only** — no soft-deprecation, no `@CodebaseClient` alias in `ast_java.py`.
- **PR-1 is zero behaviour change** (enum stub + structured-log **emitter machinery** exists but is **not** called from `ast_java.py` / `graph_enrich.py` / CLI). PR-2 is the single atomic behaviour commit (rename + parsers + merge fix + wiring + tests). PR-1 unit test may call the emitter **directly**; no `--verbose` or other flag implies shadowing runs in production in PR-1.
- **Two events, two severities** (propose §9 #18): `brownfield-exclusivity-shadowing` = INFO; `brownfield-method-string-literal` = WARN; do not collapse into one.
- **Single INFO trigger for shadowing:** extractor co-presence only — not “each dropped built-in row” in `graph_enrich.py`.
- **Re-index required after PR-2**: `meta_chain` and annotation simple names in stored metadata must match post-rename code; mixing PR-1 index with PR-2 code without rebuild is unsupported.

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-1** | `CodebaseHttpMethod.java` stub under route fixtures; **parameterized** structured stderr emitter (new small module and/or `build_ast_graph.py`) — **no production call sites** | **none** | `tests/fixtures/brownfield_route_stubs/...`, emitter module, **1** new test | Unit test exercises emitter directly (INFO + WARN shapes as needed) | none |
| **PR-2** | Rename stubs + route stub field type; `ast_java.py` recognition + client `method` enum parse + **extractor-time** INFO shadowing + WARN on string `method`; `graph_enrich.py` HTTP branch replace **without** merge-time shadowing + `meta_chain` / log strings; tighten `test_23`; **new** inbound exclusivity test; README + `CODEBASE_REQUIREMENTS.md` + any other doc hits for examples | **11 → 12** (`ast_java.ONTOLOGY_VERSION`; README / `AGENTS.md` callouts) | `ast_java.py`, `graph_enrich.py`, structured-log module (see PR-2 §4), stubs, `tests/test_*.py` listed below, `README.md`, `CODEBASE_REQUIREMENTS.md`, `build_ast_graph.py` if comment at ~1904 | Full `pytest tests`; new exclusivity + optional shadowing log test | PR-1 merged |
| **PR-3** | Agent docs + v2 addendum only | **none** | `docs/AGENT-GUIDE.md`, `docs/skills/java-codebase-explore.md` (if needed), `propose/completed/BROWNFIELD-ANNOTATIONS-V2-ADDENDUM-HTTP-METHOD-ENUM.md` | Docs-only CI | PR-2 merged |

Landing order: **PR-1 → PR-2 → PR-3**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Enum placement | Primary stub: `tests/fixtures/brownfield_route_stubs/com/example/rag/CodebaseHttpMethod.java`. If client stub compilation in tests requires the enum on the client fixture classpath, duplicate or symlink per build reality (propose §8 PR-1). |
| Python constant name | Prefer renaming `CODEBASE_CLIENT_ANNOTATIONS` → `CODEBASE_HTTP_CLIENT_ANNOTATIONS` so the PR-2 pre-merge grep for legacy Java simple names does not false-positive on the constant identifier. |
| `test_23_layer_c_wins_over_get_mapping` | Today uses a loose assertion on `/a`; after replace semantics, assert **no** same-method built-in HTTP row remains (align with new exclusivity test). |
| Ontology | Bump **12** on PR-2: route cardinality / `route_source_layer` for annotated controllers changes vs previous merge behaviour — operators should re-index. |
| Shadowing INFO | **Only** `ast_java.py` co-presence check; **not** `_merge_layer_c_codebase_routes`. |
| Structured logging implementation | One **parameterized** emitter, e.g. `_emit_structured_brownfield_event(event, severity, **fields)`, plus optional thin wrappers so WARN never emits `event=brownfield-exclusivity-shadowing`. Resolve `ast_java` ↔ `build_ast_graph` import cycles (dedicated small module or lazy import). |

---

# PR-1 — Enum stub + shadowing log helper (no wiring)

## File-by-file changes

### 1. `tests/fixtures/brownfield_route_stubs/com/example/rag/CodebaseHttpMethod.java`

- New enum file exactly as in propose Appendix A (package `com.example.rag`, seven values, doc comment).

### 2. `brownfield_events.py` (recommended) and/or `build_ast_graph.py`

- Add **`_emit_structured_brownfield_event(event: str, severity: str, ...)`** (exact signature up to implementer) that prints one structured stderr record per call. PR-1 must support at least **`event=brownfield-exclusivity-shadowing`** at **INFO** for the unit test contract; designing the signature so PR-2 can pass **`brownfield-method-string-literal`** / **WARN** without a second copy-pasted formatter is **required**.
- **Do not** call this from `ast_java.py` or `graph_enrich.py` in PR-1. **Do not** add CLI flags whose only purpose is to invoke shadowing in PR-1.

### 3. `build_ast_graph.py` (optional)

- Re-export or thin-delegate to the emitter if the team prefers a single import path for `build_ast_graph` callers in PR-2.

### 4. `tests/test_<new>.py` (single new module acceptable)

- One test that invokes the helper (or a thin wrapper) and asserts the emitted payload / log record contains the expected `event` and required fields (stable contract for PR-2 wiring).

## Tests for PR-1

1. New test function (exact name chosen by implementer) — asserts **INFO** record for `brownfield-exclusivity-shadowing` (and optionally one line proving **WARN** path accepts `brownfield-method-string-literal` without reusing the wrong `event=`).

## Definition of done (PR-1)

- [ ] `CodebaseHttpMethod.java` exists under route stubs; `ruff check .` clean.
- [ ] Parameterized emitter exists; **zero** production call sites in PR-1 (only unit test + optional re-export glue).
- [ ] `pytest tests -v` green (existing suite + one new test).
- [ ] No `ONTOLOGY_VERSION` change.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add enum stub | `tests/fixtures/brownfield_route_stubs/.../CodebaseHttpMethod.java` | File matches propose Appendix A |
| 2 | Implement `_emit_structured_brownfield_event` | `brownfield_events.py` (or chosen path) | Callable, stderr-only; supports ≥2 `event=` values |
| 3 | Unit test | `tests/...` | Asserts event schema |
| 4 | Validate | — | `ruff` + `pytest` |

---

# PR-2 — Atomic rename, enum `method`, exclusivity, README/requirements

## File-by-file changes

### 1. `tests/fixtures/brownfield_route_stubs/com/example/rag/CodebaseHttpRoute.java`

- Change `method` from `String` to `CodebaseHttpMethod` (import enum).

### 2. `tests/fixtures/brownfield_client_stubs/com/example/rag/CodebaseClient.java` → `CodebaseHttpClient.java`

- Rename type; `@Repeatable(CodebaseHttpClients.class)`; `CodebaseHttpMethod method();` **without** default.

### 3. `tests/fixtures/brownfield_client_stubs/com/example/rag/CodebaseClients.java` → `CodebaseHttpClients.java`

- Plural container references `CodebaseHttpClient[]`.

### 4. `ast_java.py`

- `CODEBASE_HTTP_CLIENT_ANNOTATIONS` (or renamed set): `CodebaseHttpClient`, `CodebaseHttpClients` only — remove `CodebaseClient` / `CodebaseClients`.
- `_parse_codebase_client_annotation`: parse `method` via enum branch (mirror route parser); on non-enum `method`, call the shared emitter with **`event=brownfield-method-string-literal`**, **`severity=WARN`**, plus parse context fields — **never** pass `brownfield-exclusivity-shadowing` for this path.
- **Extractor-time INFO shadowing (UC9, propose §3):** after resolving method-level annotations, if the method has `@CodebaseHttpRoute` / `@CodebaseHttpClient` (or plural containers) **and** at least one **shadowable** framework annotation on the same method (locked set: Spring MVC/WebFlux mapping annotations, `@FeignClient`, JAX-RS HTTP verbs, etc. — enumerate in code + test), call the shared emitter once per method with **`event=brownfield-exclusivity-shadowing`**, **`severity=INFO`**, `method_fqn`, and a stable list of bypassed framework annotation simple names. Gate volume behind the same **verbose** diagnostic convention the graph build already uses for parser noise (if no flag exists yet, follow `build_ast_graph.py --verbose` plumbing from call site into `ast_java` parse options).
- All string switches / docstrings referencing `CodebaseClient` updated (~1540, ~1725–1744 and any other hits).
- Route inline tests: `method = "GET"` → `method = CodebaseHttpMethod.GET` where Java snippets embed the annotation.

### 5. `graph_enrich.py`

- **`_merge_layer_c_codebase_routes`**: For methods that have layer-C **HTTP** routes, **remove** same-`method_fqn` built-in HTTP rows from `merged` first, then append layer-C rows (same structure as async block ~963–977). Update docstring that currently says HTTP merges (~953–955).
- **Do not** emit `brownfield-exclusivity-shadowing` from this function — extractor owns that signal (single trigger, no double logs).
- **`meta_chain` walker** (~1300–1305): replace `"CodebaseClient"` / `"CodebaseClients"` keys with **`CodebaseHttpClient`** / **`CodebaseHttpClients`** for `in chain` checks and `annotation_to_http_client_hint` lookup key consistency.
- Deprecation / user string at ~1097: update to `CodebaseHttpClient` wording.

### 6. `build_ast_graph.py`

- Thread verbose / diagnostics flag into `ast_java` parsing if required for shadowing INFO volume control; survey comments (e.g. ~1904) for `CodebaseClient` → `CodebaseHttpClient`.

### 7. Tests (Java string snippets)

- `tests/test_brownfield_routes.py` — all `@CodebaseHttpRoute(..., method = "…")` → enum; **tighten** `test_23_layer_c_wins_over_get_mapping`; add **`test_<name>_layer_c_http_replaces_builtin`** (exact name TBD): `@RestController` + `@GetMapping("/x")` + `@CodebaseHttpRoute(path="/x", method=CodebaseHttpMethod.GET)` → exactly **one** HTTP route row for that method with `route_source_layer=layer_c_source` and **no** parallel built-in `spring_mvc` row (query shape as existing helpers in file allow).
- `tests/test_brownfield_clients.py` — rename annotations; `method="GET"` → `method=CodebaseHttpMethod.GET`; meta fixture strings that declare `CodebaseClient` → `CodebaseHttpClient`.
- `tests/test_route_extraction.py` — imports and snippets for cases 6b, 6d, etc.
- `tests/test_client_node_extraction.py` — all `@CodebaseClient` / method strings.
- `tests/test_assign_endpoint_client_extraction.py` — Feign + JAX-RS mirror; `method = HttpMethod.POST` may need alignment with **`CodebaseHttpMethod.POST`** on brownfield annotation per surface rules.
- `tests/test_cross_service_resolution_flag.py` — generated Java strings.

### 8. `README.md` and `CODEBASE_REQUIREMENTS.md`

- Replace annotation names and examples with `@CodebaseHttpClient` / enum `method`; add **Re-index required** callout for PR-2 + ontology **12**.

### 9. `AGENTS.md` / `.cursor/rules` (if they state current ontology)

- Bump cited `ontology_version` to **12** where the repo documents the current number (keep in sync with `ast_java.ONTOLOGY_VERSION`).

## Tests for PR-2 (minimum set — entire `pytest tests` must pass)

**Regression rewrites (non-exhaustive; grep-driven):**

1. `test_23_layer_c_wins_over_get_mapping` — strengthened assertions for replace semantics.
2. `test_22_layer_c_codebase_route` (and other `test_*` in `test_brownfield_routes.py` using string `method`).
3. All tests in `tests/test_brownfield_clients.py` that reference `CodebaseClient` / `CodebaseClients`.
4. `test_case6b_codebase_client_string_literal_kind_not_treated_as_enum` (and related) in `tests/test_route_extraction.py` — update for new annotation names + enum `method` expectations.
5. `test_case6d_codebase_client_on_interface_abstract_method` (rename only if test name is contractually frozen — otherwise update body only).
6. All `tests/test_client_node_extraction.py` functions touching `@CodebaseClient`.
7. `tests/test_assign_endpoint_client_extraction.py` — full file green.
8. `tests/test_cross_service_resolution_flag.py` — brownfield Java snippets.

**New:**

9. New exclusivity test (see PR-2 §7 `test_brownfield_routes.py` bullet).
10. (Recommended) One test or subprocess assertion that **verbose** parse / graph build emits **`brownfield-exclusivity-shadowing`** for a minimal `@GetMapping` + `@CodebaseHttpRoute` or Feign + `@CodebaseHttpClient` snippet.

**Global:**

11. `tests/test_call_edges_e2e.py::test_ontology_version_matches_graph_meta` (ontology 12 after bump).

## Definition of done (PR-2)

- [ ] Pre-merge grep from propose returns **no** stale Java simple names:  
  `rg -n 'CodebaseClient\b|CodebaseClients\b' --glob '*.py' --glob '*.md' --glob '*.java'`  
  (Adjust if `CODEBASE_CLIENT_ANNOTATIONS` retained — then document exception; **prefer** Python constant rename.)
- [ ] `ONTOLOGY_VERSION = 12` in `ast_java.py`; README re-index callout updated; `AGENTS.md` / rules ontology line updated if present.
- [ ] `_merge_layer_c_codebase_routes` HTTP path matches async **replace** pattern; **no** INFO shadowing emitted from `graph_enrich.py`.
- [ ] With verbose diagnostics enabled, **`brownfield-exclusivity-shadowing`** (INFO) fires from **`ast_java.py`** on co-presence fixtures (e.g. Feign + `@CodebaseHttpClient`, Spring mapping + `@CodebaseHttpRoute`); **`brownfield-method-string-literal`** (WARN) fires on intentional string-`method` fixtures if any remain for migration coverage.
- [ ] `ruff check .` and `pytest tests -v` green without `JAVA_CODEBASE_RAG_RUN_HEAVY`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Stubs + enum classpath | fixture dirs | All Java fixtures compile in test harness |
| 2 | `ast_java.py` recognition + parsers + shadowing | `ast_java.py` (+ emitter import) | New names; enum `method`; INFO co-presence; WARN string `method` |
| 3 | Merge HTTP replace (behaviour only) | `graph_enrich.py` | Replace pattern; **zero** shadowing logs from this file |
| 4 | Verbose plumbing if needed | `build_ast_graph.py` → parse entry | Shadowing INFO respects volume gate |
| 5 | `meta_chain` + log strings | `graph_enrich.py` | Grep clean for old simple names |
| 6 | Tests + docs | `tests/*`, `README.md`, `CODEBASE_REQUIREMENTS.md` | Full pytest; doc examples match stubs |
| 7 | Ontology + meta test | `ast_java.py`, `tests/test_call_edges_e2e.py` | `meta()` reports 12 |

---

# PR-3 — Documentation + completed-propose addendum

## File-by-file changes

### 1. `docs/AGENT-GUIDE.md`

- Subsection: brownfield-exclusivity (annotate ⇒ bypass framework for that facet); UC10 silent Feign disagreement; pointer to verbose build log for INFO events.

### 2. `docs/skills/java-codebase-explore.md`

- Update cheat sheet / trailer only if it names `@CodebaseClient` or string `method`.

### 3. `propose/completed/BROWNFIELD-ANNOTATIONS-V2-ADDENDUM-HTTP-METHOD-ENUM.md` (new)

- Short cross-reference to `HTTP-ROUTE-METHOD-ENUM-PROPOSE.md` and summary of landed rename + enum + exclusivity. **Do not** edit `BROWNFIELD-ANNOTATIONS-V2-PROPOSE.md` body (immutable); addendum only.

## Tests for PR-3

- None (docs only).

## Definition of done (PR-3)

- [ ] Agent guide documents exclusivity + UC10.
- [ ] Addendum file exists and links parent propose + this plan.
- [ ] No Python behaviour change in PR-3.

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Stale `"CodebaseClient"` in `meta_chain` breaks meta-annotation resolution silently | High | PR-2 grep + code review checklist; explicit row in PR-2 DoD |
| 2 | PR-1 helper wrong shape → PR-2 churn | Medium | PR-1 unit test locks JSON/log fields |
| 3 | Client/route fixture split: enum not on classpath for client tests | Medium | Symlink or duplicate enum stub; verify first CI run |
| 4 | Operators run PR-2 code on PR-1-era index | Medium | README + PR description: mandatory `java-codebase-rag reprocess` / full rebuild |
| 5 | `test_23` too weak; merge bug slips | Medium | New dedicated exclusivity test with strict row count / layer |
| 6 | Duplicate INFO if merge path also logs | Low | Plan locks extractor-only INFO for `brownfield-exclusivity-shadowing` |

# Out of scope

- `@CodebaseAsyncRoute`, `@CodebaseProducer`, or new gRPC/MQ annotations.
- APT / annotation processors.
- Changing Kuzu `Route` / `Client` column types for `http_method`.
- YAML schema or key renames (`method` stays string).
- Compile-time validation that Feign verb matches brownfield verb.
- Hard errors on framework shadowing (INFO only per propose).

# Whole-plan done definition

1. All three PRs merged in order; `master` has five HTTP stubs as in propose TL;DR.
2. `ONTOLOGY_VERSION` is **12**; README documents re-index for this rollout.
3. No remaining `CodebaseClient` / `CodebaseClients` in production Python, tests, or fixture Java per grep contract.
4. Completed propose addendum exists under `propose/completed/`.

# Tracking

- `PR-1`: _pending_
- `PR-2`: _pending_
- `PR-3`: _pending_

# Optional companion

For Cursor per-PR handoffs mirroring `plans/completed/CURSOR-PROMPTS-TIER1B.md`, add `plans/CURSOR-PROMPTS-HTTP-ROUTE-METHOD-ENUM.md` with branch naming, sentinel greps, and exact pytest invocations once PR-1 lands.
