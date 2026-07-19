# Phase 0 De-risk Findings

Living log of spikes that gate later phases. Each entry: what was checked, the
result, and any downstream implication.

## jqassistant injection coverage (Task 1)

**Verdict: COVERED.** See `bench/oracle/JQASSISTANT_COVERAGE.md`. The mechanical
oracle (`injects.cypher`, `upstream-consumers`) may use jqassistant; collection
injection needs a `DEPENDS_ON` intersection but is not a hard gap.

## Operator CLI build workflow + path resolution

The operator CLI (`java-codebase-rag`) splits index creation from the build:

- `init` only scaffolds the index dir (empty Ladybug graph). It refuses (exit 2)
  if the resolved index dir already holds a graph — so `rm -rf` first or use
  `erase --yes`.
- `reprocess` performs the full Lance vector reprocess + LadybugDB graph rebuild.

So a "build" = `init` + `reprocess`. C5 `build_time_s` below is the wall-clock
for both steps together.

**Path-resolution gotcha (load-bearing for Plan 2's driver):** `--index-dir` is
resolved **relative to `--source-root`**, not the process cwd. Passing a
relative `--index-dir` therefore lands the index *inside* the checkout
(`bench/checkouts/<corpus>/bench/indexes/...`). Always pass `--index-dir` as an
**absolute** path. `bench/checkouts/` and `bench/indexes/` are both gitignored.

## C4 determinism (n=2 of 3)

Two independent `init`+`reprocess` builds of bank-chat-system (different output
dirs) produce byte-identical node/edge counts:

| metric          | build 1 | build 2 |
|-----------------|---------|---------|
| files           | 130     | 130     |
| types           | 140     | 140     |
| members         | 606     | 606     |
| calls (edges)   | 684     | 684     |
| injects         | 94      | 94      |
| implements      | 21      | 21      |
| extends         | 18      | 18      |
| overrides       | 38      | 38      |
| routes          | 28      | 28      |
| http_calls      | 8       | 8       |
| async_calls     | 9       | 9       |
| ontology        | 19      | 19      |

`COUNTS IDENTICAL · EDGES IDENTICAL · ONT IDENTICAL`. The third rebuild for
n=3 is a Plan 2/3 stretch; n=2 already establishes the determinism seed (C4).
Full count tables for all three corpora are in `bench/corpora.yml` (build_id
ties each index to `name:sha:ontology_version`).

## C5 build-cost (per corpus, single build)

| corpus                        | files | types | build_time_s | on_disk_bytes | build_id           |
|-------------------------------|-------|-------|--------------|---------------|--------------------|
| bank-chat-system              | 130   | 140   | 24.31        | 19,579,624    | 359a90a1993004f6   |
| shopizer                      | 1167  | 1201  | 74.34        | 55,462,518    | 2cc6f3554cd35296   |
| spring-petclinic-microservices| 53    | 61    | 22.16        | 13,614,076    | 05746336a2fb08df   |

All builds: 0 parse errors, ontology_version 19. shopizer is the cost ceiling
(≈3× bank-chat build time, ~55 MB on disk). petclinic is smaller than the spec's
"~hundreds" (62 `.java` files in the checkout) but carries the Feign
cross-service seam (1 detected `http_calls` edge) and the multi-module shape.

## TODO (later spikes)

- **`claude -p` flag stability** (Plan 2 driver gate): confirm
  `--allowedTools`/`--disallowedTools` accept MCP tool names like
  `mcp__jrag__neighbors`, and that `--max-turns` caps as expected. Record here.
- **Ablation toggles** (Plan 2/3): which of role-ranking / cross-service-edges /
  graph-expansion jrag can actually disable at query/index time. Record here.

## shopizer Maven build is broken (Task 16 deviation)

shopizer's POM imports BOMs from `s01.oss.sonatype.org` (snapshots repo) that
are unresolvable in this environment — the build stalls on
`micrometer-bom`, `netty-bom`, `spring-data-bom`, etc. and produces **no**
`.class` files, so jqassistant cannot scan shopizer. Consequences, recorded
honestly:

- shopizer's 15 questions are all `oracle_source: "manual"` (structural answers
  derived by independent **source grep**, not jqassistant). blast-radius is
  depth-1 (direct importers), not depth-2, with rationale noting the manual
  derivation.
- bank-chat (calibration corpus) and petclinic (compiled cleanly via `mvn
  -DskipTests compile`, Java 17 on JDK 25) use the mechanical jqassistant oracle.
- The calibration gate (bank-chat, all-mechanical categories at 1.0) still
  validates the mechanical oracle. Re-pointing shopizer at jqassistant once its
  build is fixed (mirror the snapshots repo / pin a buildable commit) is a
  Plan 2/3 follow-up; the expected answers can then be regenerated mechanically
  and diffed against the manual truth recorded here.

