# Shared-module multi-attribution

Status: **draft — open for review**.

This is a **proposal**, not an implementable plan. After review and scoping
decisions (the §10 [TBD] list), an implementable plan will be derived under
`plans/PLAN-SHARED-MODULE-MULTI-ATTRIBUTION.md`. The shipping work is
expected to span 3-5 PRs.

## TL;DR

`Symbol.microservice` is currently a single `STRING` populated by the
**outermost build-marker ancestor** rule in `microservice_for_path`
(`graph_enrich.py:1438-1487`). For repos that follow the **shared-contracts
Maven module** pattern — a `*-contracts` / `*-shared` / `*-common` artifact
that lives physically inside one service's directory tree but is depended
on by other services via Maven coordinates — this attribution is wrong.
Symbols compiled into multiple JVM classpaths get attributed to exactly
one microservice, producing a class of false-positive cross-service
edges that PR-E3's CALLS invariant guard cannot detect.

This proposal moves attribution from a one-per-symbol scalar to a
**multi-attribution model** that tags each symbol with every microservice
whose runtime classpath contains its bytecode. The change closes the
shared-contracts gap for `CALLS`, `EXTENDS`, `IMPLEMENTS`, and `INJECTS`
without requiring a per-microservice resolution scope (the Tier-2
incremental rebuild concern).

## Why now (and not earlier)

1. **Real-world fixture confirmed it.** PR-E3's guard caught the
   FQN-collision failure mode but missed the shared-DTO case. The
   `bank-chat-system` fixture exposes 8 cross-service `CALLS` edges from
   `chat-assign` into `chat-core::AssignmentRequest` getters — 100% of
   the residual cross-service violations are the shared-contracts
   pattern (callees in `com.bank.chat.contracts.*`). On a real
   multi-service Java monorepo, the same pattern fires across more
   relationships and at higher absolute volume.
2. **The current attribution silently misleads downstream tools.**
   `graph_neighbors`, `impact_analysis`, and `analyze_pr` all use
   `Symbol.microservice` to compute "blast radius" and "cross-service
   callers". A symbol attributed to one service that's actually
   linked into N services produces both false-positive blast radius
   (impact analysis claims N microservices when only 1 is impacted)
   and false-negative impact (a change to the shared DTO doesn't
   propagate to the N-1 other services).
3. **PR-E3's invariant guard depends on it.** The guard skips when no
   same-microservice candidate exists in `_lookup_method_candidates`'s
   result. With multi-attribution, the shared-DTO method *will* have
   the caller's microservice in its membership set — so the guard
   becomes unnecessary for the legitimate shared-DTO case (it does
   the right thing automatically) and remains a defence-in-depth for
   the FQN-collision case.
4. **Incremental rebuild needs it.** The Tier-2 incremental rebuild
   proposal (`propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`) requires
   a stable `microservice` membership for closure rules. Multi-
   attribution must land before Tier-2 dispatch logic, not after,
   to avoid two schema bumps.

## Goals

1. **Symbols whose bytecode runs in multiple JVMs are attributed to
   all of them.** `AssignmentRequest#getConversationId()` should
   carry membership `{chat-core, chat-assign}` on
   `bank-chat-system`, not `{chat-core}`.
2. **Existing single-service symbols are unchanged.** A `@Service`
   class in `chat-assign/src/...` keeps membership `{chat-assign}`
   only.
3. **Cross-service CALLS/EXTENDS/IMPLEMENTS/INJECTS are eliminated**
   for the shared-DTO case. `audit_inter_service_edges.py` reports
   zero violations on `bank-chat-system` post-migration.
4. **PR-E3's guard remains correct.** Re-evaluated against the new
   membership semantics — should fire only for genuine FQN
   collisions (still the right behaviour).
5. **Backwards-compatible MCP surface.** `Symbol.microservice` (the
   scalar field on the existing DTO) remains available, populated
   with a deterministic "primary" choice, so existing tools keep
   working without changes. New tools / queries can opt into the
   multi-attribution view.
6. **No silent perf regression on single-service repos.** The fast
   path (one-attribution-per-symbol) stays the common case.

## Non-goals

- **Per-microservice resolution scoping in pass3.** Already deferred
  to Tier-2 incremental rebuild. Multi-attribution and per-service
  scoping are complementary but independent.
- **Rewriting `microservice_for_path`'s structural inference for the
  primary microservice.** The "outermost build-marker" rule stays
  as the **primary** attribution — the multi-attribution adds
  *additional* memberships on top.
- **Dependency-graph reconstruction from arbitrary build systems.**
  Maven `pom.xml` and Gradle `build.gradle(.kts)` covered;
  Bazel/Pants/sbt out of scope (flag with a TODO and fall back to
  primary-only).
- **Cross-repo attribution.** One source tree at a time, same as
  today.
- **Symbol.microservice schema rename.** The column stays. The
  *meaning* of the scalar shifts to "primary microservice"; the
  multi-attribution is a new sibling.

## 1. Current state

### 1.1. Attribution call site

Two places in `build_ast_graph.py` call `microservice_for_path`:

- **`build_ast_graph.py:347`** — pass1, when constructing every
  `Symbol` row. Uses `microservice_for_path(file_path, source_root)`
  to populate `Symbol.microservice`.
- **`build_ast_graph.py:1422`** — `_micro_factor` confidence
  multiplier in pass3. Returns `1.0` when a primary microservice is
  attributed and `0.85` when it isn't. This is independent of the
  multi-attribution work but should be re-evaluated under the new
  semantics (recommend keeping `microservice_for_path` here — the
  factor's job is to penalise unattributed symbols, not to express
  membership).

### 1.2. Failure mode (verified on bank-chat-system)

```
=== CALLS: 8 cross-service edges ===
  Top microservice pairs:
    chat-assign -> chat-core: 8 edges
  Likely shared-library/DTO pattern: 8/8 (100%)
  Top callee FQNs:
       3  com.bank.chat.contracts.AssignmentRequest#getConversationId()
       2  com.bank.chat.contracts.AssignmentRequest#getPriorityScore()
       …
```

The DTO `AssignmentRequest` is at
`chat-core/chat-contracts/src/main/java/com/bank/chat/contracts/AssignmentRequest.java`.
`microservice_for_path` returns the outermost marker (`chat-core`),
correctly identifying which directory owns the source code, but
incorrectly implying that **only** chat-core's JVM links the bytecode.

`chat-assign/pom.xml:29` has:

```xml
<dependency>
    <groupId>com.bank.chat</groupId>
    <artifactId>chat-contracts</artifactId>
    <version>${chat-contracts.version}</version>
</dependency>
```

So at runtime, `AssignmentRequest`'s bytecode runs inside both
chat-core's JVM **and** chat-assign's JVM. Multi-attribution should
give it membership `{chat-core, chat-assign}`.

### 1.3. PR-E3 guard interaction

PR-E3's pre-filter (`build_ast_graph.py:1074-1088`) drops
cross-microservice candidates only when at least one
same-microservice candidate exists. For shared-DTO calls, the
resolver returns `[chat-core::AssignmentRequest#getConversationId]`
— a single candidate, no same-ms peer for chat-assign — so the
guard skips and the cross-service edge gets emitted.

With multi-attribution:
- `AssignmentRequest#getConversationId.microservice` becomes
  `{chat-core, chat-assign}`.
- The guard's "is-cross-service" check (`member.microservice !=
  candidate.microservice`) becomes "is-disjoint" (set
  intersection). For the chat-assign caller, the candidate's
  membership *contains* chat-assign — not cross-service — so
  no guard fire is needed.

## 2. Design

### 2.1. Schema change

Two options. Decision in §10 [TBD-1].

**Option A — array column on `Symbol`:**
```sql
microservices STRING[]   -- all memberships
microservice  STRING     -- primary (kept for back-compat)
```
- Pros: one query, no joins.
- Cons: Kuzu's array semantics are less battle-tested in this repo;
  no test covers `STRING[]` filters; adds a `microservices` field to
  every Symbol DTO including the 95%+ that have a single membership.

**Option B — sidecar REL table `MEMBER_OF`:**
```sql
CREATE NODE TABLE Microservice(name STRING, PRIMARY KEY(name))
CREATE REL TABLE MEMBER_OF(FROM Symbol TO Microservice)
```
- Pros: clean schema, easy to query (`MATCH (s)-[:MEMBER_OF]->(m)
  WHERE m.name = ...`), idempotent additive migration.
- Cons: extra REL table, extra join in every "filter by microservice"
  query path (which today is just a column filter).

**Recommendation: Option B.** Aligns with the existing pattern of
sidecar tables for many-to-many relations (`EXPOSES`, `INJECTS`).
The `Symbol.microservice` scalar stays as the primary, populated
unchanged. Tools can opt into multi-attribution by joining
`MEMBER_OF`.

### 2.2. Membership derivation

Three signals, evaluated in order:

1. **Maven dependency graph** (`pom.xml`). Parse each service's
   `<dependencies>` block for `<groupId>:<artifactId>` references
   that match an in-repo module's coordinates. The matched module's
   source directory contributes its symbols to the depending
   service's membership set.
2. **Gradle dependency graph** (`build.gradle` /
   `build.gradle.kts`). Parse `dependencies { implementation
   project(':...') }` and `api project(':...')` references. Same
   semantics as Maven.
3. **Explicit override** (extension to the existing
   `microservice_overrides` config). New optional field
   `microservice_memberships`: a map of directory-name → list of
   service-names that should additionally include it.

The primary microservice (`Symbol.microservice` scalar) is unchanged
— still the outermost build-marker ancestor.

### 2.3. New helper

```python
def microservice_memberships_for_path(
    file_path: str, project_root: str | Path | None = None,
) -> set[str]:
    """All microservices whose runtime classpath contains the file.

    Returns at least {primary_microservice_for_path(...)}.
    For files inside a shared module (not itself a top-level
    microservice), returns the union of every service that
    declares a build-system dependency on the shared module.
    """
```

`microservice_for_path` (the existing primary-attribution function)
stays as-is and is **not deprecated**. Multi-attribution is purely
additive.

### 2.4. Build-graph parsing

A new module `build_graph.py` (or extension to `path_filtering.py`)
caches `pom.xml` / `build.gradle*` parses per service-root and
exposes:

```python
def project_dependencies(project_root: Path) -> dict[str, set[str]]:
    """Returns {service_dir_name: {dependent_module_dir_names}} for
    every service directory under project_root. Cached per project_root.
    """
```

Cache invalidation: keyed by (file_path, mtime) of the build files.
On incremental rebuild (Tier-2), changes to a `pom.xml` /
`build.gradle*` invalidate the cache and trigger a full
microservice-membership rebuild. Changes to source files don't.

### 2.5. PR-E3 guard generalisation

```python
# Today (single-attribution):
if member.microservice and candidate.microservice:
    same_ms = [c for c in candidates if c.microservice == member.microservice]
    if same_ms and len(same_ms) != len(candidates):
        for c in candidates:
            if c.microservice != member.microservice:
                ...

# With multi-attribution:
def _is_intra_jvm(caller_ms_set: set[str], callee_ms_set: set[str]) -> bool:
    """At least one shared JVM linkage."""
    if not caller_ms_set or not callee_ms_set:
        return True   # unknown attribution → don't guard
    return bool(caller_ms_set & callee_ms_set)

# Guard:
if member.microservices and \
        not any(_is_intra_jvm(member.microservices, c.microservices) for c in candidates):
    # Hard cross-service violation — every candidate is in a
    # disjoint JVM. Drop them all.
    stats.skipped_cross_service += len(candidates)
    return
elif member.microservices:
    intra_jvm = [c for c in candidates if _is_intra_jvm(member.microservices, c.microservices)]
    if len(intra_jvm) != len(candidates):
        # Some candidates linkable, some not — drop the unlinkable
        for c in candidates:
            if not _is_intra_jvm(member.microservices, c.microservices):
                ...
        candidates = intra_jvm
```

The guard becomes set-disjoint instead of scalar-equal. For
`bank-chat-system` post-migration, the chat-assign → chat-contracts
DTO calls become intra-JVM (intersection {chat-assign} non-empty)
and pass through unguarded.

### 2.6. Downstream tool behaviour

- `graph_neighbors` / `impact_analysis` / `analyze_pr`: use
  `MEMBER_OF` for "list all microservices a symbol participates in"
  questions. Continue using `Symbol.microservice` (primary) for "what
  module owns this symbol" questions.
- New MCP tool (out of scope for the first PR, listed in §6 future
  work): `list_microservices_for_symbol(name)` returning the full
  membership set.
- `pr_analysis.py` cross-service-callers logic: replace the scalar
  comparison with intersection check on caller's membership set vs.
  callee's membership set.

## 3. Risks and mitigations

### 3.1. Build-file parsing fragility

**Risk:** Maven/Gradle parsing is heuristic. A misparsed `pom.xml`
could lose a dependency edge and re-introduce false-positive
cross-service edges.

**Mitigation:** Conservative fallback — when parsing fails or finds
no dependencies, every symbol gets primary-only membership (current
behaviour). New diagnostic field
`graph_meta.build_graph_parse_errors` counts parse failures per
build run. Logged at WARN. Test fixtures cover both well-formed and
malformed `pom.xml` cases.

### 3.2. Membership explosion

**Risk:** A "kitchen-sink" shared module depended on by 20+
services would tag every symbol with 20 memberships. Storage and
query cost grows.

**Mitigation:** None needed at our scale (< 50 services per
project). Document the limit. If a project hits real cost, the
opt-out is putting the shared module in a separate Maven repo
(out-of-tree) where it's not source-indexed and the issue
disappears.

### 3.3. False positive: build-time-only deps

**Risk:** Maven `<scope>provided</scope>` and `<scope>test</scope>`
deps don't ship in the runtime classpath but our parser would
treat them the same as `compile`.

**Mitigation:** Filter `provided` and `test` scopes during parsing.
Annotation-processor deps (`<scope>annotationProcessor</scope>`) also
filtered. Document the scope filter in the helper docstring.

### 3.4. Brownfield interaction

**Risk:** PRs PR-D1..D3 introduced brownfield role-recognition
overrides for `RestTemplate`/`WebClient` callers. If the brownfield
override sets a microservice that contradicts the multi-attribution,
which wins?

**Mitigation:** Brownfield overrides are caller-side replacements
for the **role** of a Symbol (HTTP client vs. business logic), not
its microservice attribution. They operate on a different axis.
Document and test that brownfield-tagged symbols still get correct
multi-attribution.

### 3.5. PR-E3 guard regression

**Risk:** Generalising the guard from scalar-equal to set-disjoint
introduces a bug — e.g. accidentally treating empty sets as a
cross-service violation.

**Mitigation:** Test matrix with all four combinations: empty/empty,
empty/non-empty, non-empty/empty, non-empty/non-empty. The
collision-fixture test from PR-E3 stays green; new tests cover the
shared-DTO case.

## 4. Verification

### 4.1. Determinism

The migration must be **bit-for-bit deterministic** on a fixed
source tree. Two independent rebuilds of `bank-chat-system` post-
migration produce identical `(s.fqn, sorted(memberships))` tuples
and identical CALLS edge hashes.

### 4.2. Equivalence on single-service repos

On any fixture where every symbol has primary-only membership, the
post-migration graph differs from pre-migration only in:
- Presence of `Microservice` nodes (one per microservice)
- Presence of `MEMBER_OF` edges (one per `(symbol, primary_ms)` pair)

All other tables byte-identical.

### 4.3. Shared-DTO fix on bank-chat-system

Post-migration:
- `chat-contracts/.../AssignmentRequest#*` symbols have
  `MEMBER_OF` edges to both `chat-core` and `chat-assign`.
- `audit_inter_service_edges.py` reports 0 cross-service `CALLS`
  edges.
- `g.meta()['pass3_skipped_cross_service'] == 0` (no guard fires).
- `graph_neighbors('AssignmentRequest', depth=2)` correctly
  returns symbols from both microservices (already does today, but
  for the wrong reason — post-migration, it's correct because
  both microservices legitimately member-link the DTO).

### 4.4. FQN-collision case still guarded

The `fqn_collision_smoke` fixture from PR-E3 stays green — the two
`SharedDto` classes in svc-x and svc-y are independent declarations
(no shared module), so their memberships are disjoint and the
guard fires as before. `pass3_skipped_cross_service >= 1` on this
fixture.

## 5. Suggested PR breakdown

The shipping work is expected to span **5 PRs**. Sized to be
independently mergeable; each adds value even if the next is
delayed.

### PR-1: Build-graph parser (Maven)

- Add `build_graph.py` with `project_dependencies(...)` for
  Maven (`pom.xml` only).
- New function `microservice_memberships_for_path(...)` returning a
  `set[str]`. For now it just returns
  `{microservice_for_path(...)}` (no behaviour change yet).
- Tests with `bank-chat-system` and 2-3 minimal Maven fixtures
  (single-service, two-service-with-shared-contracts,
  malformed-pom).
- Schema: no change.
- ~250 LOC, ~8 tests.

### PR-2: Schema + write path

- Add `Microservice` node table and `MEMBER_OF` rel table.
- Wire `microservice_memberships_for_path` into pass1's symbol
  emission to populate the new tables.
- `Symbol.microservice` (primary scalar) unchanged.
- `KuzuGraph.meta()` adds `microservices_total` and
  `member_of_total` fields with tiered fallback (new
  `_META_PR_F1`).
- Fail-soft: if the shared-module parsing returns nothing, the
  graph still populates `MEMBER_OF` with one edge per symbol's
  primary attribution (so the table is never empty).
- Schema bump: yes, `ast_java.ONTOLOGY_VERSION` 7 → 8. Documented in plan.
- ~300 LOC, ~5 tests.

### PR-3: PR-E3 guard generalisation

- Replace scalar-equal with set-disjoint check.
- Update PR-E3's `_is_intra_jvm` helper.
- `fqn_collision_smoke` stays green (memberships still disjoint).
- New `shared_dto_smoke` fixture (mirror of bank-chat-system but
  trimmed to 2 files) explicitly tests shared-module
  multi-attribution.
- `audit_inter_service_edges.py` on `bank-chat-system` returns 0
  cross-service CALLS edges (down from 8).
- ~80 LOC, ~3 tests.

### PR-4: Gradle support

- Extend `build_graph.py` with Gradle build-file parser.
- New fixture `gradle_shared_module_smoke` (kts and groovy
  variants, one each).
- ~150 LOC, ~3 tests.

### PR-5: MCP surface + downstream tools

- New tool `list_microservices_for_symbol(name)` returning the
  full membership set.
- Update `analyze_pr.cross_service_callers` logic to use the
  intersection check instead of scalar comparison.
- Update `impact_analysis` to deduplicate cross-service callers
  by membership-set rather than by primary microservice.
- ~150 LOC, ~5 tests.

**Total estimated diff:** ~930 LOC across 5 PRs, ~24 new tests,
1 schema bump (PR-2 only). Each PR self-contained with a clear
manual-evidence command.

## 6. Future work (out of scope for this proposal)

- **Watch-mode interaction.** A future watch-mode that re-parses
  `pom.xml` on save needs to invalidate the build-graph cache.
- **Diagnostic MCP tool.** `audit_inter_service_edges` as a
  built-in tool rather than a workspace script.
- **Cross-repo memberships.** When the shared module lives in a
  different git repo (not source-indexed). Probably out-of-scope
  forever — handled by treating the dep as external (current
  behaviour).
- **Visualisation.** A "service membership map" diagram in the
  diagnostic surface.

## 7. Backwards compatibility

- `Symbol.microservice` scalar **unchanged**. Same primary
  attribution rule. All current tools continue to work.
- New tables (`Microservice`, `MEMBER_OF`) are additive.
- `KuzuGraph.meta()` tiered read adds a new `_META_PR_F1` tier on
  top; older graphs fall back to the existing `_META_PR_E3`
  query.
- MCP tool surface: existing tools unchanged. New tools are
  additive.
- CLI: existing flags unchanged. No new required flags.

A graph built before this proposal is **read-compatible** with
post-proposal code (queries returning 0 rows from `MEMBER_OF`
gracefully degrade). Downstream tools that opt into multi-
attribution should `if not memberships: memberships = {primary}`.

## 8. Decision-engine interaction (Tier-2 incremental)

When Tier-2 incremental rebuild lands:
- Changes to a `.java` file inside a shared module invalidate
  every dependent service's symbol membership for that file's
  symbols (but only those — neighbouring symbols in the same
  shared module are untouched).
- Changes to a `pom.xml` / `build.gradle*` invalidate the
  build-graph cache and force full rebuild of `MEMBER_OF` (but
  not `Symbol` itself, which is content-addressable).
- The decision engine treats `pom.xml` / `build.gradle*` changes
  as a **structural** signal — same as schema changes, force
  full rebuild.

This proposal **must land before** Tier-2 incremental rebuild to
avoid two schema bumps (one for ontology 7→8 here, another for the
8→9 in Tier-2).

## 9. Why not the alternatives

**Why not "primary attribution = innermost build marker"?** The
innermost rule (`chat-contracts` for the shared DTO) loses the
service identity entirely — every shared symbol gets attributed to
a non-microservice directory and disappears from
`graph_neighbors('chat-core', ...)` queries. Multi-attribution
preserves both the service identity *and* the membership.

**Why not "post-process: rewrite cross-service CALLS edges
post-hoc"?** Doable but coupled. A whitelist of "shared module
patterns" (`*-contracts`, `*-shared`, etc.) re-attributing
post-emission would work for CALLS but not for symbol-level
queries (`graph_neighbors`, `impact_analysis`). Multi-attribution
is the correct abstraction at the source.

**Why not "ditch microservice attribution entirely"?** It's load-
bearing for `analyze_pr.cross_service_callers`, the cross-service
matcher (PR-D3), and the ImpactAnalysis output's
`cross_service_callers` deduplication. Removing it would invalidate
half a dozen MCP tools.

**Why not "wait for Tier-2 incremental rebuild"?** Tier-2 needs
this. Doing it inside Tier-2 doubles the schema bumps and bundles
two unrelated concerns. Better to land here, deterministically,
with a focused review.

## 10. [TBD]

| # | Decision | Notes |
|---|----------|-------|
| 1 | Schema: array column vs sidecar table | Recommendation §2.1 favours sidecar (Option B). Confirm before PR-2. |
| 2 | Build-graph parser library: hand-rolled vs `pom.xml` lib | Maven is pure XML, hand-rolled is ~80 LOC. Gradle is harder — `build.gradle.kts` is Kotlin DSL, parsing requires either a Kotlin parser or a heuristic regex pass. Decision: hand-rolled regex for both, fail-soft on parse error. Revisit if accuracy is bad. |
| 3 | What happens to symbols outside any service marker? | Today: empty `microservice`. Post-migration: empty membership set or `{""}` sentinel. Recommend keeping the empty-set semantics — empty means "not attributed". |
| 4 | Annotation-processor classpath | Lombok-generated symbols don't appear in source. Out of scope (we don't index generated bytecode). |
| 5 | Should the primary microservice always be in the membership set? | Yes — invariant: `primary in memberships`. Asserted in pass1 emission. |
| 6 | Do we backfill `MEMBER_OF` on existing brownfield-tagged symbols? | Yes — brownfield is a role overlay, not a microservice override. The membership derivation runs on every symbol regardless of role. Test coverage in PR-3. |
| 7 | Backwards-compat MCP surface for membership queries | New tool `list_microservices_for_symbol` in PR-5, or extend `graph_get_symbol` to include the membership list? Decision: extend `graph_get_symbol` (less surface area). |
| 8 | Performance budget for build-graph parsing | Target: < 100ms total for `bank-chat-system` (2 services). Real-world: re-evaluate after PR-1 lands and the user benchmarks on a real repo. |

## 11. References

- `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md` — the larger
  refactor this proposal feeds into.
- `plans/PLAN-POST-TIER1B-FOLLOWUPS.md` § PR-E3 — the guard this
  generalises.
- `graph_enrich.py:1438-1487` — current `microservice_for_path`.
- `build_ast_graph.py:347, 1422` — call sites for primary
  attribution.
- `tests/bank-chat-system/chat-assign/pom.xml` — the
  `<dependency>` declaration that motivates this proposal.
- PR #22 review observation #1 — flagged the gap that this
  closes.
