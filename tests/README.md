# Tests for `java-codebase-rag`

These tests exercise:

1. `build_ast_graph.py` — the Tree-sitter Java -> Kuzu graph builder.
2. `kuzu_queries.py` — read-only Cypher helpers used by the MCP server.
3. The MCP tool surface in `server.py` (every `@mcp.tool` is hit at least once,
   either with a real Kuzu graph fixture or via its error-path when LanceDB
   isn't available).

The fixture corpus lives under `tests/bank-chat-system/`. It is a *toy* but
realistic two-service Spring Boot project:

```
bank-chat-system/
  chat-assign/                       single-module Maven service
                                     (RestTemplate + @FeignClient to chat-core)
  chat-core/                         multi-module Maven service
    chat-app/                        @SpringBootApplication, REST controllers, reporting
    chat-contracts/                  DTOs, ChatTopics, brownfield @Codebase* annotations
    chat-domain/                     JPA entities + Spring Data repositories
    chat-engine/                     EventProcessor strategies, kafka, notification, audit
```

**Brownfield on bank-chat (Tier-1):** The corpus embeds `@CodebaseHttpRoute` /
`@CodebaseHttpClient` / `@CodebaseProducer` / `@CodebaseAsyncRoute` definitions
under `chat-contracts/.../brownfield/` and uses them from assign/app/engine.
Session-graph assertions for that layout live in
[`test_bank_chat_brownfield_integration.py`](./test_bank_chat_brownfield_integration.py).
Minimal single-purpose stubs remain under `tests/fixtures/brownfield_*`.

## Running

```bash
cd /path/to/java-codebase-rag
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -v
```

**Kuzu Cypher:** When writing queries or asserting on edge filters, follow the pitfalls note in [`AGENTS.md`](../AGENTS.md) (avoid `label(e) IN $list` for type filters; be careful with typed union rel patterns).

## CI merge gate and fixture tiers

**Merge gate (mechanical):** [`.github/workflows/test.yml`](../.github/workflows/test.yml) always runs the `test` job on every pull request and on every push to `master`. When any **source** path changes (Python, deps, `pytest.ini`, `mcp.json.example`, `.gitignore`, workflows, non-markdown under `tests/` or `automation/`), it runs `pytest tests` with `JAVA_CODEBASE_RAG_RUN_HEAVY=0`. Documentation-only changes (`propose/`, `plans/`, `reports/`, `.agents/`, `docs/`, `**/*.md`, etc.) still produce a green `test` check but skip pytest. Branch protection on `master` requires the `test` status check to pass before merge and disables force-push. Break-glass policy: `enforce_admins: false` so the sole maintainer can bypass for emergency hotfixes — explain the bypass in the merge commit.

**Iteration subset (convention):** During implementation, authors name a `pytest` file subset inside each per-PR execution prompt (for example in `plans/AGENT-PROMPTS-*.md`). The repo **[`plan-prompts`](../.agents/skills/plan-prompts/SKILL.md)** skill (`.agents/skills/plan-prompts/`) requires a **`## Tests to run (iteration loop)`** section in that scaffold, placed **after Deliverables and before Tests**. Reviewers follow the repo **[`pr-review`](../.agents/skills/pr-review/SKILL.md)** skill (`.agents/skills/pr-review/`): pasted subset command + exit code, plus a green `test` CI link from the merge gate documented above (full pytest for code changes; pytest skipped for docs-only PRs). Canonical skill sources live under `.agents/skills/` (symlink as `.cursor` or `.claude` locally if your editor expects those paths); you may copy them into `~/.cursor/skills/` if your Cursor setup loads personal skills only. See [`propose/completed/TEST-SUITE-FAST-LOOP-PROPOSE.md`](../propose/completed/TEST-SUITE-FAST-LOOP-PROPOSE.md) and [`plans/completed/PLAN-TEST-SUITE-FAST-LOOP.md`](../plans/completed/PLAN-TEST-SUITE-FAST-LOOP.md).

**Fixture tiers (PR-1):**

| Tier | When | Pattern |
| --- | --- | --- |
| **1** | Read-only assertions against `tests/bank-chat-system/` | Use session `corpus_root` → `kuzu_db_path` → `kuzu_graph` / `mcp_server`. The session bank graph is built **once** per pytest process with pass1–5 + `write_kuzu` (**no pass6**), matching the bank caller-edge tests without strengthening pass6 match-resolution semantics. |
| **2** | Read-only use of a static tree under `tests/fixtures/<name>/` | Prefer session fixtures in `conftest.py` (for example `kuzu_db_path_call_graph_smoke`, `kuzu_db_path_route_extraction_smoke`, `kuzu_graph_route_extraction_smoke`, `kuzu_db_path_cross_service_smoke`, `kuzu_db_path_fqn_collision_smoke`, `kuzu_db_path_http_caller_smoke`) or `graph_tables_cross_service_smoke` when tests need in-memory `GraphTables`. **Audit each file:** if a test copies the fixture into `tmp_path` and mutates files or YAML, it stays Tier 3 — do not point it at a shared session DB or shared `GraphTables`. |
| **3** | Per-test corpora under `tmp_path` (brownfield stubs, generated YAML, etc.) | Keep per-test isolation; build via helpers in [`tests/_builders.py`](./_builders.py) (`build_kuzu_into`, `build_kuzu_imperative_into`, `build_kuzu_full_into`, or `build_graph_tables_to`) instead of duplicating `pass*` imports. |

**Consumer matrix (bank-chat and call invariant):** When changing the session bank pipeline (`kuzu_db_path`) or adding a parallel bank fixture, update the PR description with a short matrix of which tests depend on which pass depth. Conflicting requirements (for example pass6 changing HTTP_CALLS match rows that tests still expect as `unresolved`) must be resolved with a **separate** named session fixture or per-test builds — not by silently changing semantics.

| Test / area | Fixture / build | Pass depth | Semantics note |
| --- | --- | --- | --- |
| Session bank (`kuzu_db_path`, `kuzu_graph`, MCP) | `conftest` | pass1–5 + write, **no pass6** | Keeps bank `HTTP_CALLS` / `ASYNC_CALLS` matches `unresolved` for `test_call_edges_e2e`. |
| `test_call_invariant_inert_on_bank_chat_system` | `kuzu_graph` | same as session bank | **Was** pass1–3 + write only. `pass3_skipped_cross_service` is a pass3 counter persisted on `GraphMeta`; passes 4–5 do not re-run pass3 or rewrite that field, so the value `0` for bank-chat is unchanged vs the old per-test build. |
| `test_call_invariant_inert_on_clean_fixtures` | `kuzu_db_path_cross_service_smoke` | pass1–6 + write | **Was** pass1–3 + write on a fresh copy. Assertion is still `pass3_skipped_cross_service == 0` (pass3-only meta); later passes do not alter that counter for this fixture tree. |

**Tier-3 on copied `cross_service_smoke`:** `test_cross_service_resolution_flag.py` and `test_client_role_rename.py` copy the fixture into `tmp_path`, edit YAML/Java, then build. They **cannot** use the read-only session graph or shared `graph_tables_cross_service_smoke`; they call `build_graph_tables_to` / `build_kuzu_to` from `_builders.py` on each **mutable** copy so pass chains stay centralized.

**`test_mcp_v2.test_find_client_by_target_service`:** The seed row must come from `list_clients()` rows with a real `target_service` column. Using the first token of the display `fqn` was incorrect when `target_service` is empty (more client rows after pass5). That is a test bugfix, not a fixture-speed change — call it out in the PR.

**`test_mcp_v2.test_find_client_by_client_kind`:** Client display `fqn` is
`{target_service} {method} {path}` — it does not embed `client_kind`. After
Feign clients were added to bank-chat, verify `client_kind` via `list_clients()`
(or graph columns), not via substring checks on `fqn`.

**Timing:** Large fixture refactors should note rough wall-time before and after in the PR body (see the plan propose).

The session-scoped fixtures in `conftest.py` materialize Kuzu (and, where needed, in-memory `GraphTables`) under `tmp_path_factory` so the static trees under `tests/` are never written at test time.

The heavier end-to-end test that runs `cocoindex` + a real LanceDB index is
gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1` because it downloads the embedding
model on first run and indexes the corpus from scratch (~minute on a
warm cache, several minutes cold). The same gate applies to full
`java-codebase-rag` lifecycle subprocess checks in
`tests/test_cli_progress_stdout_invariant.py` and the cocoindex portion of
`tests/test_cli_quiet_parity.py` so default `pytest tests` stays fast when
`cocoindex` is installed.

```bash
JAVA_CODEBASE_RAG_RUN_HEAVY=1 .venv/bin/pytest tests -v
```

**`JAVA_CODEBASE_RAG_TEST_GRAPH_SLOW_SEC`:** optional float read by `build_ast_graph.py` in pass1 only. When set (e.g. `6`), pass1 sleeps that many seconds under `--verbose` so tests can assert heartbeat lines. Leave unset for normal `pytest` runs.

---

## ⚠️ Note for future contributors — DO NOT OVERFIT THE MCP TO THIS CORPUS

The bank-chat-system fixture exists so the test suite has a deterministic,
self-contained Java codebase to assert against. It is **not** the system the
MCP is meant to serve in production. Real-world repositories will look
different in dozens of ways — different package roots, different Spring
stereotypes, mixed Lombok / non-Lombok injection, generated code, etc.

When adding tests, please follow these rules:

1. **Assert on invariants, not on exact counts.** Prefer `>= 1`, `> 0`, or
   structural shape (a key exists, a list is non-empty) over `== 11`. The
   only place exact counts are reasonable is when you're proving the builder
   produced *both* sides of a known relationship in this fixture (e.g. that
   `EventProcessor` has at least the implementations we put in the fixture).

2. **Never tweak the production code path to hard-code names from the
   fixture.** For example, do not add a special role for
   `ChatManagementService` or a heuristic that only fires when the package
   starts with `com.bank.chat`. If you find yourself wanting to do that to
   make a test pass, the test is wrong, not the code.

3. **If a tool relies on LanceDB, prefer testing both paths separately:**
   the MCP tool's *contract* (validation, error message when the index is
   missing) can always be tested without LanceDB; the *integration* should
   be added to `test_lancedb_e2e.py` and gated behind
   `JAVA_CODEBASE_RAG_RUN_HEAVY`.

4. **When a test fails after a refactor, re-read the assertion first.** Most
   of the assertions here are intentionally loose (`>=`, `in`, `subset of`)
   so genuine regressions are loud and benign churn is silent. Tightening an
   assertion to chase a number is almost always wrong.

5. **Keep the fixture small.** If you need a new edge case (e.g. a Kotlin
   file, a `module-info.java`, a `@MapperScan`-style indirection), prefer
   adding a *minimal* file under `bank-chat-system/` that demonstrates only
   that case rather than enlarging the existing services.

6. **The fixture is "real-shaped", not exhaustive.** Examples of things that
   are still *uncommon* in the corpus and should not be added to `chat-core`
   without thought: reactive (`Mono`/`Flux`) controllers, gRPC stubs, MapStruct
   generated sources. **`chat-assign` includes a `@FeignClient`** (
   `ChatCoreFeignClient`) for cross-service reads; keep new Feign/gRPC experiments
   in assign or a tiny `tests/fixtures/` tree rather than enlarging engine modules.
   If the MCP needs an isolated edge case, prefer `tests/fixtures/` over growing
   `chat-engine` further.

7. **Call graph proposal §7.1 vs tests.** The checklist in
   `propose/completed/CALL-GRAPH-PROPOSE.md` §7.1 is distributed across
   `test_ast_java_calls.py`, `test_call_graph_smoke_roundtrip.py` (Kuzu build
   of `tests/fixtures/call_graph_smoke/` only), the session Kuzu build,
   `test_kuzu_queries.py`, `test_ast_graph_build.py`,
   `tests/fixtures/call_graph_smoke/`, and `test_call_graph_receiver_resolution.py`
   — not as a single enumerated matrix. For an edge case the bank corpus cannot
   isolate, add a minimal tmp_path fixture or a tiny extra tree under
   `tests/fixtures/`.

In short: this corpus is here to keep us honest, not to define what the MCP
must support. If the MCP becomes correct *only* for this corpus, the test
suite has failed at its job.
