# Tests for `mcp_lancedb_bundle`

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
  chat-core/                         multi-module Maven service
    chat-app/                        @SpringBootApplication, REST controllers
    chat-contracts/                  request/response DTOs, EventType enum
    chat-domain/                     JPA entities + Spring Data repositories
    chat-engine/                     orchestration + EventProcessor strategies
```

## Running

```bash
cd mcp_lancedb_bundle
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests -v
```

The session-scoped `kuzu_graph` fixture in `conftest.py` builds the Kuzu DB
from the bank-chat-system corpus exactly once per pytest run, into a
`tmp_path_factory` directory, and points `KUZU_DB_PATH` (and a *fake*
`LANCEDB_URI`) at it for the duration of the session.

The heavier end-to-end test that runs `cocoindex` + a real LanceDB index is
gated behind `LANCEDB_MCP_RUN_HEAVY=1` because it downloads the embedding
model on first run and indexes the corpus from scratch (~minute on a
warm cache, several minutes cold).

```bash
LANCEDB_MCP_RUN_HEAVY=1 .venv/bin/pytest tests -v
```

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
   `LANCEDB_MCP_RUN_HEAVY`.

4. **When a test fails after a refactor, re-read the assertion first.** Most
   of the assertions here are intentionally loose (`>=`, `in`, `subset of`)
   so genuine regressions are loud and benign churn is silent. Tightening an
   assertion to chase a number is almost always wrong.

5. **Keep the fixture small.** If you need a new edge case (e.g. a Kotlin
   file, a `module-info.java`, a `@MapperScan`-style indirection), prefer
   adding a *minimal* file under `bank-chat-system/` that demonstrates only
   that case rather than enlarging the existing services.

6. **The fixture is "real-shaped", not exhaustive.** Examples of things that
   are *deliberately* not present and should not be added without thought:
   reactive (`Mono`/`Flux`) controllers, gRPC stubs, MapStruct generated
   sources, `@FeignClient` bridges. If your change to the MCP needs one of
   those to be tested, add a small dedicated module — don't graft it onto
   `chat-core`.

In short: this corpus is here to keep us honest, not to define what the MCP
must support. If the MCP becomes correct *only* for this corpus, the test
suite has failed at its job.
