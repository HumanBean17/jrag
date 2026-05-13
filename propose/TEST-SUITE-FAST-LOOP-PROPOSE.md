# TEST-SUITE-FAST-LOOP — collapse repeated graph builds and ship per-PR test selection

**Status**: under review
**Author**: Dmitriy Teriaev + Computer
**Date**: 2026-05-12 (v3)

---

## TL;DR

- **Problem**: each `pytest` run rebuilds the AST graph 15+ times (15 of 18 graph-building tests bypass the session `kuzu_graph` fixture). Per-PR iteration pays this every loop.
- **Underlying honesty**: today there is **no mechanical merge gate** on `master`. `master` has no branch protection (`enabled: false`, no required status checks), no `.github/workflows/`, no pre-commit hooks. Whatever the author chooses not to run, doesn't run. v1–v2 of this propose used the word "gate" as if it meant enforcement; v3 corrects that.
- **Frame**: the suite is two artifacts — an *iteration loop* (fast subset, runs every change) and a *merge check* (full suite, runs before merge). Making the iteration loop faster is honest; calling the full suite a "gate" before CI exists is not.
- **Three shipped changes**:
  - **A. Session-scoped fixtures keyed by corpus** — collapse N rebuilds of the same input into one per session, plus per-fixture session graphs for the small smoke corpora. (PR-1, repo)
  - **B. Per-PR test selection convention** — author declares a `Tests to run` subset for iteration; reviewer demands evidence. This is a *convention*, not enforcement. (PR-2: repo **`plan-prompts`** skill + user **`pr-review`** skill)
  - **C. Make the merge gate real** — add `.github/workflows/test.yml` running the full suite on every PR + push to master, then enable branch protection on `master` requiring that check to pass. After this lands, the word "gate" in this propose stops being a polite fiction. (PR-3, repo)
- **What goes away**: ad-hoc `_build()` helpers in 11+ test files that re-run all four passes for every test; and the implicit "author remembers to run the full suite" merge model.
- **What stays**: tests that mutate `tmp_path` per-test (5 files) keep their pattern but call a faster pre-parsed builder helper.
- **3 work items**: PR-1 (repo, `tests/` + `tests/README.md`), PR-2 (**`plan-prompts`** in `.cursor/skills/plan-prompts/` + **`pr-review`** in the author’s Cursor user skill library), PR-3 (repo, CI workflow + branch protection). Order matters — see §8.

---

## §1 Frame

The propose-driven workflow has produced 8+ PRs in the last week, each opened as a draft and reviewed against a written plan. Two distinct frictions compound:

1. The cost of "run the full suite to verify a one-line parser tweak" — an *iteration speed* problem.
2. The fact that nothing mechanical stops a broken-test merge — a *safety* problem masked by the fact that the author has been disciplined so far.

v1–v2 conflated the two by using the word "gate" for what was really "author discipline". v3 separates them: **iteration speed** is fixed by PR-1 (fixture dedup) and PR-2 (subset convention). **Safety** is fixed by PR-3 (real CI + real branch protection). The propose is incomplete without all three; shipping only PR-1+PR-2 would *increase* drift risk by making it easier to skip the full suite without anything noticing.

This propose does not invent new test infrastructure beyond CI itself. It collapses redundant work in the existing fixture layer, codifies an iteration convention, and adds the enforcement that the v1–v2 framing falsely implied already existed.

---

## §2 Design principles

1. **No production code change.** PR-1 touches only `tests/` plus `tests/README.md`. PR-2 touches the repo **plan-prompts** Cursor skill (`.cursor/skills/plan-prompts/`, no production `*.py` outside `tests/`) and the user-scoped **pr-review** skill. PR-3 touches only `.github/workflows/` plus repo settings. Production code is out of scope for all three.
2. **"Gate" means enforcement, "convention" means discipline.** Words have to match what they mechanically do. The full suite becomes a real merge gate only after PR-3 lands (workflow + required status check). Before that, it is a convention the author follows.
3. **Session fixtures are corpus-scoped, not test-scoped.** Each distinct corpus tree gets exactly one parse per session, regardless of how many test files consume it.
4. **Per-test `tmp_path` mutation is a legitimate pattern.** Tests that copy stubs and write YAMLs per case stay per-test — they just stop re-importing `build_ast_graph` modules from scratch.
5. **Iteration subset is declared in the PR, not inferred.** No magic test-impact analysis. The author names the subset; reviewers can override.
6. **The subset is for iteration, the full suite is for merge.** After PR-3, the workflow runs the full suite on every push to a PR branch and on push to master; the subset is what humans (and agents) run locally during the loop.
7. **Skill changes carry their guardrail.** Adding the `Tests to run` contract to **plan-prompts**-generated prompts requires updating the **pr-review** skill so reviewers demand evidence the subset was actually run.
8. **No new dependencies in PR-1 or PR-2.** `pytest-xdist`, `pytest-testmon`, cross-session caching are explicitly deferred (§7). PR-3 only adds GitHub Actions + branch protection — no new Python deps.
9. **Migration is observable.** Before/after wall-time is part of the PR-1 description. After PR-3 lands, every PR shows a green/red status check; "did the suite pass?" stops being a question.

---

## §3 The three changes — overview

### A. Fixture refactor — three-tier model

Today's `tests/conftest.py` has one session graph (`kuzu_graph`) over the `bank-chat-system` corpus. The 18 graph-building tests fall into three categories:

| Tier | Pattern | Files | Action |
|---|---|---|---|
| **Tier 1: reuse session graph** | Parses `bank-chat-system`, no per-test mutation | `test_call_edges_e2e.py`, `test_call_invariant.py`, `test_ast_graph_build.py`, `test_kuzu_queries.py`, `test_call_graph_receiver_resolution.py`, `test_lancedb_e2e.py` | Switch helper to consume `kuzu_db_path` / `kuzu_graph` session fixtures |
| **Tier 2: per-corpus session graph** | Parses a small fixture corpus (`fixtures/<name>`), no per-test mutation | `test_call_graph_smoke_roundtrip.py`, `test_route_extraction.py`, `test_cross_service_resolution_flag.py`, `test_feign_not_exposer.py`, `test_client_role_rename.py`, `test_client_hint_recovery.py` | Add `kuzu_graph_<fixture_name>` session fixtures in `conftest.py` |
| **Tier 3: keep per-test, use a faster builder** | Copies stubs + writes per-test YAML to `tmp_path` | `test_brownfield_routes.py`, `test_brownfield_clients.py`, `test_client_node_extraction.py`, `test_assign_endpoint_client_extraction.py`, `test_call_edge_matching.py` | Keep per-test rebuild but extract a single `_build_kuzu(tmp)` helper and stop importing `build_ast_graph` symbols inside each `_build()` (cuts import overhead) |

Tier 1 and Tier 2 are the bulk of the win. Tier 3 stays per-test because the test inputs *are* the variations.

### B. Per-PR test selection convention

Each propose-derived PR carries a `Tests to run (iteration loop)` block in its execution prompt (typically inside `plans/CURSOR-PROMPTS-*.md`), naming the test subset relevant to the change. Three additions:

1. **Repo `plan-prompts` skill** (`.cursor/skills/plan-prompts/`) — when generating or auditing prompts, require the **`## Tests to run (iteration loop)`** section between **Deliverables** and **Tests** so authors name the subset whenever work is delegated from a plan.
2. **User `pr-review` skill** — verification requires *evidence*, not a checkbox: the reviewer must include the actual `pytest <files>` command + exit code, and (after PR-3) a link to the green CI run of the full suite. This decouples "subset ran during iteration" from "full suite passed before merge" — two evidences, two purposes.
3. **PR description template** — add a one-line section listing the iteration subset; the full-suite check becomes a green status check once PR-3 lands.

**`plan-prompts`** is versioned in this repository. **`pr-review`** is maintained in the author’s Cursor user skill library (`~/.cursor/skills/pr-review/` or `save_custom_skill`), not committed here. The repo’s `docs/skills/` tree holds the separate `java-codebase-explore` artifact and is unrelated.

Before PR-3 lands, B is a *convention*, not enforcement — the author can lie to the evidence requirement and nothing will catch it. After PR-3 lands, the full-suite half is mechanically enforced and the subset half remains a discipline question.

### C. Make the merge gate real — CI workflow + branch protection

The v1–v2 framing of "full suite is the merge gate" was aspirational. Today's state, verified against the GitHub API: `master` has `protected: true` but `protection.enabled: false`, zero required status checks, no `.github/workflows/`, no pre-commit hooks. Anyone with write access can merge a PR with red tests — the author has been disciplined, but discipline is not a mechanism.

PR-3 ships:

1. `.github/workflows/test.yml` running `pytest tests` (with `JAVA_CODEBASE_RAG_RUN_HEAVY=0`) on every PR push and on push to master. Uses `actions/checkout`, a minimum-stable Python (3.11), the sandbox's pinned dependency install path, and the same test command used locally.
2. Branch protection on `master` requiring the `test` workflow check to pass before merge. Force-push to `master` disallowed. Self-review allowed (you're the sole maintainer).
3. Optional: matrix over Python versions deferred. Heavy tests deferred (still gated by `RUN_HEAVY=1`).

This is what makes the *word* "merge gate" mean something mechanical. Without C, A and B together would actually *increase* the risk of a broken merge — they normalise running less of the suite. C closes that hole.

---

## §4 The fixture refactor — proposed surface

### Current `tests/conftest.py`

One session-scoped chain: `corpus_root → kuzu_db_path → mcp_env → kuzu_graph → mcp_server`. All built off `tests/bank-chat-system`.

### Proposed `tests/conftest.py`

Add a parametrised builder factory and per-fixture session graphs:

```python
# Existing bank-chat-system session graph stays.

# New: per-fixture session graphs.
# Each takes a fixture directory name and returns (db_path, KuzuGraph).
_FIXTURE_GRAPHS = (
    "call_graph_smoke",
    "route_extraction_smoke",
    "cross_service_smoke",
    "fqn_collision_smoke",
    "http_caller_smoke",
    "capability_smoke",
)

@pytest.fixture(scope="session", params=_FIXTURE_GRAPHS)
def fixture_corpus_root(request) -> Path:
    return TESTS_DIR / "fixtures" / request.param
# (then a session-scoped builder factory keyed on the fixture name)
```

A cleaner alternative — and the one we'll ship — is **named fixtures per corpus** (not parametrised), because tests want to depend on *a specific* fixture, not iterate over all of them:

```python
@pytest.fixture(scope="session")
def kuzu_graph_call_graph_smoke(tmp_path_factory) -> KuzuGraph: ...
@pytest.fixture(scope="session")
def kuzu_graph_route_extraction_smoke(tmp_path_factory) -> KuzuGraph: ...
# ... one per fixture corpus directory
```

Each fixture builds its corpus once and returns a read-only `KuzuGraph` handle. Tests in Tier 2 swap their inlined `pass1..pass4 → write_kuzu` for the fixture.

### Tier 3 — shared builder helper

Add `tests/_builders.py`:

```python
def build_kuzu_into(tmp: Path) -> Path:
    """Run all four passes + write_kuzu against tmp. Returns db path.
    Tier-3 tests use this to avoid re-importing build_ast_graph symbols per test."""
    from build_ast_graph import (
        GraphTables, pass1_parse, pass2_edges, pass3_calls, pass4_routes, write_kuzu,
    )
    ...
```

Five test files stop redefining their local `_build()` and import `build_kuzu_into` instead. The wall-time win here is small (~100ms per test from cached imports) but the dedupe is worth it.

---

## §5 The per-PR selection contract — proposed surface

### `plan-prompts` skill (repo) — iteration section

The **plan-prompts** skill governs `plans/CURSOR-PROMPTS-*.md`. Its per-PR **Prompt** scaffold has sections: scope, deliverables, sentinel greps, tests, definition-of-done, out-of-scope. Insert a new section **between *deliverables* and *tests***:

```markdown
## Tests to run (iteration loop)

Run only these during iteration. Full suite is the merge gate.

- tests/test_<file_a>.py
- tests/test_<file_b>.py

Rationale: <one-line "why these"; e.g. "PR touches _parse_codebase_client_annotation; brownfield_clients + ast_java_calls exercise it">.
```

### `pr-review` skill (user-scoped) — evidence checklist

The **pr-review** skill (author’s Cursor user library) adds a review gate **before** generic “manual evidence reproduced”:

- Require the **exact** `pytest <files>` command + **exit code** (or pytest summary) for the files listed under **`## Tests to run (iteration loop)`** in the task prompt. **Checkbox-only** lines (“subset ran [x]”) are **not** sufficient.
- After PR-3: require a **link** to the **green** GitHub Actions run for the **full** suite on this PR at the reviewed commit.

(An earlier draft used bare checkboxes here; the shipped **pr-review** skill replaces that with pasteable evidence — see §9 #18.)

### PR description convention

Propose-doc PRs include in their "verification" section:

```
**Tests to run (iteration)**: tests/test_X.py tests/test_Y.py
**Full suite**: gate at merge time
```

---

## §6 Use-case re-walk

| # | Use case | Today | After A | After B | After C |
|---|---|---|---|---|---|
| UC1 | Run full suite locally | ~30-45s baseline (estimate) | ~15-25s (one parse instead of 15) | unchanged | unchanged; also runs in CI per push |
| UC2 | Run `tests/test_brownfield_routes.py` alone | ~5s | ~5s (Tier 3 stays per-test) | unchanged | unchanged |
| UC3 | Run `tests/test_call_invariant.py` alone | ~5s (own build) | ~5s alone, free when combined with other Tier 1 tests | unchanged | unchanged |
| UC4 | Run `tests/test_kuzu_queries.py` alone | ~3s setup + tests | unchanged (already uses session fixture) | unchanged | unchanged |
| UC5 | PR-1 of #85 (enum stub + helper, no wiring) | Run full suite → ~30s | Full suite → ~15s | Subset (`test_ast_java_calls.py` + `test_brownfield_routes.py`) → ~7s locally | Subset locally + CI runs full suite green automatically before merge |
| UC6 | PR-2 of #85 (parser rewrite + merge→replace) | Full suite → ~30s | Full suite → ~15s | Subset (`test_brownfield_clients.py`, `test_brownfield_routes.py`, `test_ast_java_calls.py`, `test_call_invariant.py`) → ~10s | Same + CI green check is required by branch protection |
| UC7 | Cursor delegated implementation per PR | No selection guidance | unchanged | Cursor follows prompt template; runs declared subset | Plus CI status check is the safety net if subset was wrong |
| UC8 | Reviewer verifies subset was run | No formal check | unchanged | Reviewer demands pasted command + exit code | Reviewer also demands link to green CI run |
| UC9 | New test added — which tier? | Author decides ad-hoc | Documented criteria in `tests/README.md` | unchanged | unchanged |
| UC10 | Fixture corpus added (new `tests/fixtures/foo_smoke/`) | Test writes own `pass1..pass4` | Author adds `kuzu_graph_foo_smoke` session fixture | unchanged | unchanged |
| UC11 | Heavy LanceDB e2e | Gated by `RUN_HEAVY=1`, fresh corpus build | Tier 1; reuses bank-chat-system session graph when RUN_HEAVY is set | unchanged | Still `RUN_HEAVY=1` gated; CI sets `RUN_HEAVY=0` by default |
| UC12 | Author rushed and merged a PR with broken tests | Possible (no protection) | Possible | Convention says don't, but possible | **Blocked**: branch protection requires green `test` check |
| UC13 | Local sandbox missing `cocoindex` | Tests `skipif`-skip | unchanged | unchanged | CI environment installs cocoindex; `skipif` only fires when truly absent |
| UC14 | PR with red tests opened, force-pushed clean | Could be merged | Could be merged | Could be merged (subset hid the failure) | Can't be merged — CI re-runs on push, required check stays red |
| UC15 | PR touches only docs (e.g. propose amend) | Full suite still runs | unchanged | Subset = `[]`; "no tests required" valid | CI still runs full suite — cheap, and confirms docs-only really was docs-only |
| UC16 | Refactor touching `graph_enrich.py:1300-1305` `meta_chain` | Hard to know which tests to run | unchanged | Author/reviewer name `test_cross_service_resolution_flag.py` + `test_call_invariant.py` | Same + CI guarantees no other test silently broke |

UC12 and UC14 are the use cases that v1–v2 framed as "protected by the merge gate" but in fact were not protected at all. v3 makes them honest.

No use case requires a primitive that's missing.

---

## §7 What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Install `pytest-xdist` for parallel runs | Worth doing later, but session fixtures rebuild per worker. Layer on top after A lands so each worker pays the parse once for the same fixture. |
| Install `pytest-testmon` for automatic impact analysis | Real value but real complexity; testmon's cache is fragile across branch switches. Per-PR manual selection (B) is cheap and dependable. |
| Cache the built Kuzu DB to `~/.cache/` across sessions | Worth a separate propose. Session-scoped caching (A) already gets one parse per `pytest` invocation; cross-session is incremental win that requires hashing. |
| Add a `@pytest.mark.unit/graph/e2e/heavy` taxonomy | Premature. The two-bucket model (subset for iteration / full suite at merge) is enough until the suite is much larger. |
| Auto-detect impacted tests from `git diff` | Tempting but unreliable for parser changes — too many indirect dependencies. Manual declaration is honest. |
| Migrate Tier 3 tests to share a base parse + delta-mutate | Tier 3's whole point is per-test inputs. Forcing them to share is a fixture-design nightmare for a small budget. |
| Move tests out of `tests/` into nearer-to-source directories | Scope creep. The structural reform belongs in its own propose. |
| Python-version matrix in CI | PR-3 ships one Python version (3.11). Multi-version matrix adds runtime cost without immediate value — add when a 3.12-specific bug actually appears. |
| Heavy-test execution in CI | PR-3 keeps `RUN_HEAVY=0` in the workflow. cocoindex / torch / transformers caching is a separate optimisation; not blocking. |
| Self-hosted runners | GitHub-hosted is fine for current suite size. Revisit if walltime in CI exceeds ~5 minutes consistently. |
| Requiring code-review approvals before merge | Out of scope. Sole maintainer means self-review is the model. Add when team grows. |
| PR-specific CI label or path-filter optimisation | Premature — the full suite is fast enough after PR-1 lands. Skipping CI on docs-only changes can be added later if walltime becomes painful. |

---

## §8 Migration plan — 3 work items

**Order matters**. PR-3 ships *first* (before PR-1) because:
- The CI workflow validates PR-1 mechanically rather than by author memory.
- PR-1 ships a green wall-time comparison that's verifiable in CI logs, not just locally.
- B (PR-2) references CI in its evidence requirement — the skill update is forward-correct only after C exists.

### PR-3 — CI workflow + branch protection (ships FIRST)

**Purpose**: make the full suite a real merge gate. Until this lands, every claim of "gate" in the propose is a convention.

**Changes** (repo + repo settings):
1. `.github/workflows/test.yml` — single job, Ubuntu latest, Python 3.11, installs from `pyproject.toml` (no `RUN_HEAVY`), runs `pytest tests`. Triggers: `pull_request` (all branches) and `push` to `master`.
2. Branch protection on `master`: `required_status_checks` includes the `test` workflow check, `enforce_admins: false` (sole maintainer can break-glass if needed), `allow_force_pushes: false`. Configured via `gh api PUT /repos/HumanBean17/java-codebase-rag/branches/master/protection`.
3. `tests/README.md` cross-reference: "Full suite enforced via `.github/workflows/test.yml` and required `test` status check."

**Test summary**: the workflow itself is the test. Before merging PR-3, push a dummy failing test to a feature branch to confirm the check fails red and the merge button is blocked. Revert the dummy before merging PR-3.

**Test subset for iteration**: none for the workflow file; PR-3 is mostly YAML and a `gh api` call.

### PR-1 — fixture refactor (tier 1 + tier 2 + tier 3 builder helper) — ships SECOND

**Purpose**: collapse repeated AST graph builds; introduce per-fixture session graphs; share Tier 3 builder helper. Lands second so the CI from PR-3 catches any fixture regression mechanically.

**Changes**:
1. `tests/conftest.py` grows session fixtures for each fixture corpus (`kuzu_graph_call_graph_smoke`, `kuzu_graph_route_extraction_smoke`, `kuzu_graph_cross_service_smoke`, `kuzu_graph_fqn_collision_smoke`, `kuzu_graph_http_caller_smoke`, `kuzu_graph_capability_smoke`).
2. `tests/_builders.py` new — exports `build_kuzu_into(tmp: Path) -> Path` consolidating the four-pass + write helper.
3. Tier 1 files (6 files): replace inline `_build()` with `kuzu_db_path` / `kuzu_graph` fixture consumption.
4. Tier 2 files (6 files): replace inline `pass1..pass4` chain with the new per-fixture `kuzu_graph_<name>` fixtures. **Mandatory pre-merge audit per file**: confirm no test writes to the session graph or the fixture directory (see decision #17).
5. Tier 3 files (5 files): replace inline `_build()` with `build_kuzu_into(tmp)` import; tests stay per-test.
6. `tests/README.md` — document the three-tier model, when to add a new session fixture, and the cross-reference to the repo **`plan-prompts`** skill and the user **`pr-review`** skill.

**Test summary**: CI status check (from PR-3) must be green; before/after wall-time must be in the PR description.

**Test subset for iteration**: the full suite — this PR *is* the fixture layer, every test is in-scope.

### PR-2 — `plan-prompts` + `pr-review` (ships LAST)

**Purpose**: codify the `Tests to run` convention in **plan-prompts** (repo) and the evidence checklist in **pr-review** (user). Ships last because the **pr-review** full-suite evidence requirement references the CI status check from PR-3.

**Split delivery:**
1. **Repo — `plan-prompts`** (`.cursor/skills/plan-prompts/`): `SKILL.md` / `reference.md` / `examples.md` require the `## Tests to run (iteration loop)` template between **Deliverables** and **Tests** in every generated per-PR prompt (`plans/CURSOR-PROMPTS-*.md`).
2. **User library — `pr-review`**: verification requires (a) pasted `pytest <files>` command + exit code (subset evidence) and (b) a link to the green CI run on the PR (full-suite enforcement). Publish via `save_custom_skill` or install under `~/.cursor/skills/pr-review/`.

**Cross-reference:** `tests/README.md` (PR-1) names **`plan-prompts`** (this repo) and **`pr-review`** (user) so contributors know where each contract lives.

**Verification**: human review of **plan-prompts** in git + **pr-review** in the user library against this propose.

**Test subset for iteration**: none (skill / doc surface, not `tests/` code).

---

## §9 Decisions taken (no longer open)

1. **Three work items in strict order**: PR-3 (CI + branch protection) first, PR-1 (fixture refactor) second, PR-2 (**plan-prompts** repo + **pr-review** user) last. Reversing this order would mean the propose ships its "merge gate" language before the merge gate exists. v3 fixes that.
2. **Three-tier classification is canonical**: Tier 1 reuses bank-chat-system session, Tier 2 reuses per-fixture session, Tier 3 stays per-test with a shared builder helper.
3. **Per-fixture session fixtures are named, not parametrised.** Tests depend on a specific corpus.
4. **No new pytest plugins** in this propose. `xdist`/`testmon` deferred.
5. **No cross-session caching** in this propose. `~/.cache/` Kuzu DB hashing is its own design call.
6. **No automatic test impact analysis.** Subset is author-declared.
7. **The full suite becomes a real merge gate only after PR-3 lands**, via `.github/workflows/test.yml` + required `test` status check on `master`. Before PR-3, it is a convention. v3 corrects v1–v2's mislabeling.
8. **Tier 3 keeps per-test rebuild.** The pattern is correct for tests whose inputs vary; sharing a base parse is harder than it looks and would add fragility.
9. **No production code touched.** This propose is exclusively `tests/` + skills.
10. **Before/after timings are mandatory in the PR description.** Decision-anchoring measurement.
11. **`build_kuzu_into` lives in `tests/_builders.py`**, not in production code. Production code already exports the passes; the helper is test-facing convenience.
12. **`tests/README.md` is the user-facing doc**, updated in PR-1 with the three-tier model.
13. **`Tests to run` is a top-level section in the plan-prompts per-PR prompt scaffold**, between Deliverables and Tests — not buried inside the full **Tests** section — it changes what the agent runs during iteration, not only what it asserts at the end.
14. **An empty `Tests to run` list is valid** for docs-only PRs (covered by UC15).
15. **Skill update covers both prompt and review** symmetrically — adding the field in the prompt without adding the review check would let it rot.
16. **CI is in scope (as PR-3), not deferred.** v2's decision to defer CI to a follow-up propose was a mistake — it left the v1–v2 "merge gate" language unsupported by any mechanism. v3 promotes CI into the propose as PR-3 and ships it first.
17. **Tier-2 audit is a merge gate for PR-1**, not just a §10 risk. Each of the 6 Tier-2 files must be audited for write-to-fixture or write-to-DB behaviour before its migration is included. If a file fails the audit, it drops to Tier 3.
18. **PR-2 review evidence is mandatory and concrete**: pasted `pytest <files>` command + exit code (iteration discipline) **and** a link to the green CI run (mechanical merge gate from PR-3). Two evidences, two purposes. Checkbox alone is rejected.
19. **PR-2 is split across repo and user.** The **`plan-prompts`** skill lives in **this repo** under `.cursor/skills/plan-prompts/` and encodes the `## Tests to run (iteration loop)` contract for `plans/CURSOR-PROMPTS-*.md` (and matching ad-hoc prompts). The **`pr-review`** skill lives in the author’s **Cursor user skill library** (for example `~/.cursor/skills/pr-review/`) and is not committed here. The `docs/skills/` tree holds the unrelated `java-codebase-explore` artifact.
20. **TL;DR language tightened**: "no production-code change" replaces "both green-field on `tests/`" because PR-1 also touches `tests/README.md` (test-operator docs, not production).
21. **Word discipline locked**: "gate" means mechanical enforcement (CI status check, branch protection). "Convention" means author discipline. v1–v2 misused "gate" for what was actually convention. v3 audits every occurrence; principle #2 in §2 is the binding rule.
22. **PR-3 ships first because order matters**: the CI workflow validates PR-1 mechanically and gives PR-2's evidence requirement something real to reference. Reverse order would mean shipping the *language* before the *enforcement*.
23. **Branch protection scope is intentionally narrow**: required `test` check + force-push disabled. No "require PR review" (sole maintainer); no "dismiss stale reviews". Tighten later if team grows.
24. **Break-glass policy**: `enforce_admins: false` so the sole maintainer can bypass for one-off emergency hotfixes. Documented in `tests/README.md` as "if you bypass, explain why in the merge commit".

---

## §10 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Tier 1 conversion shares state across tests; a test mutates the session graph | The session `KuzuGraph` is read-only by contract (`KuzuGraph._instance` cached); existing tests already share it without issues. Add an assertion in the fixture that the singleton is the same across the test session. |
| Tier 2 fixture corpus has hidden per-test mutation (e.g. a test writes to the fixture directory) | **Mandatory audit per Tier-2 file before migration** (locked as decision #17). If any test writes to the fixture directory or to the session DB, the file drops to Tier 3. PR-1 cannot merge until each Tier-2 file passes its audit. |
| Author skips the full local run before merging because the subset passed | After PR-3 lands, this risk is mechanically eliminated for `master` merges — branch protection requires the `test` status check. Pre-PR-3 (in the brief window after PR-1 but before PR-3 if order is broken), the pr-review convention is the only mitigation. Decision #22 locks the strict PR-3 → PR-1 → PR-2 order to close this window. |
| PR-3 ships but the workflow is misconfigured (false-green) | Validate before merging PR-3: push a deliberately failing test to a side branch, confirm the check is red and the merge button is disabled. Revert the failing test, then merge PR-3. Documented in PR-3's §8 summary. |
| Author uses `enforce_admins: false` to bypass routinely | Documented as break-glass only (decision #24). If routine bypass starts happening, flip `enforce_admins: true` in a follow-up. |
| CI walltime grows beyond acceptable | Out of immediate scope. PR-1's fixture dedup brings full-suite walltime down; if it climbs back, layer `pytest-xdist` (deferred items §7) onto the CI job. |
| `build_kuzu_into` helper diverges from production passes over time | The helper is a thin wrapper that imports and calls the production passes — no logic of its own. Drift only happens if pass signatures change, which would break the helper loudly. |
| Per-fixture session fixtures bloat conftest.py to unreadable size | Six new fixtures is fine. If it grows past ~10 corpora, factor into `tests/conftest_fixtures.py`. Out of scope for this propose. |
| Author-declared `Tests to run` is wrong; relevant tests are missed | The full suite at merge is the safety net. Reviewers can challenge the declared subset during review. |
| Cursor follows the subset, ships the PR, full suite then fails at merge | After PR-3 lands, this is automatic: CI re-runs the full suite on the PR; if it fails, the required `test` check stays red and the merge button is blocked. The PR cannot land until the failure is fixed. |
| `pytest-xdist` becomes attractive later but Tier 2 fixtures don't survive worker boundaries | Worker-scoped fixtures + `--dist=loadscope` solves this when we get there. Out of scope here. |

---

## Appendix A — `tests/_builders.py` reference

```python
"""Shared test-layer builder for tier-3 tests that need per-test corpus mutation.

Tier 1 and tier 2 tests should consume session-scoped fixtures from conftest.py instead.
"""
from __future__ import annotations
from pathlib import Path


def build_kuzu_into(tmp: Path) -> Path:
    """Run pass1..pass4 + write_kuzu against `tmp`. Returns the kuzu DB path.

    Use only when the test mutates `tmp` per-case (writes YAML, copies stubs, etc.).
    For tests that re-use a static corpus, depend on the session-scoped
    kuzu_graph / kuzu_graph_<fixture_name> fixtures.
    """
    from build_ast_graph import (
        GraphTables, pass1_parse, pass2_edges, pass3_calls, pass4_routes, write_kuzu,
    )
    tables = GraphTables()
    asts = pass1_parse(tmp, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    pass4_routes(tables, asts, source_root=tmp, verbose=False)
    db_path = tmp / "g.kuzu"
    write_kuzu(db_path, tables, source_root=tmp, verbose=False)
    return db_path
```

## Appendix B — v1 → v2 → v3 traceability

### v2 → v3 (this revision)

v3 is a structural correction in response to the question "can you prove these changes will not lead to bypassing failed tests to master?" The honest answer to the v2 doc was "no" — there was no mechanism, only convention. v3 fixes the doc to either prove enforcement (after PR-3) or admit it's a convention (before PR-3).

| # | Change | Driver |
|---|---|---|
| 1 | Status stays `under review`; date marked `(v3)` | meta |
| 2 | TL;DR rewritten: added "Underlying honesty" bullet exposing the no-protection state; `"2 work items" → "3 work items"`; added PR-3 to the bulleted shipped changes | v2 "merge gate" was unsupported |
| 3 | §1 Frame split into two frictions (iteration speed vs safety); v3 admits v1–v2 conflated them | v2 framing was misleading |
| 4 | §2 principle #2 NEW: "gate means enforcement, convention means discipline" — binding rule | word discipline |
| 5 | §2 principle #5 reworded: full suite for merge is mechanical only after PR-3 | v2 dishonest framing |
| 6 | §2 principle count 8 → 9 | adds principle on word discipline |
| 7 | §3 retitled "two changes" → "three changes"; added section C describing CI workflow + branch protection | new work item |
| 8 | §6 UC table grows fifth column "After C"; UC12 and UC14 (broken-tests-merged scenarios) now visibly transition from "possible" to "blocked" | proof requirement |
| 9 | §7 deferred-items table replaced: CI is no longer deferred; what remains deferred is matrix/heavy/self-hosted/review-approvals/path-filter | CI promoted into scope |
| 10 | §8 retitled "2 PRs" → "3 work items"; added PR-3 (ships FIRST) with workflow file + `gh api` branch protection + validate-with-dummy-failure step; reordered PR-1/PR-2 to ships SECOND/LAST | mechanical proof |
| 11 | §9 decisions #16 reworded (CI in scope, not deferred); #18 reworded (CI link instead of "author ran full suite locally"); added #21 word discipline, #22 strict order, #23 narrow protection scope, #24 break-glass policy. Total 20 → 24 | new commitments |
| 12 | §10 risks: "author skips local run" mitigation reframed (mechanical after PR-3, soft before); added 3 new rows: workflow misconfigured (false-green), routine `enforce_admins` bypass, CI walltime growth | enforcement-aware |

What **didn't change** (and why):
- Three-tier fixture classification (Tier 1 / Tier 2 / Tier 3) — the fix to iteration speed is unchanged.
- Tier-2 audit as a merge gate for PR-1 (decision #17) — still required.
- The use-case set (16 UCs) — v3 adds a column, not new use cases. UC12/UC14 are the same use cases but with honest outcomes.
- PR-2 remains a distinct work item after PR-1; it ships as **plan-prompts** updates under `.cursor/skills/plan-prompts/` (repo) plus **pr-review** in the author’s Cursor user skill library (see §8 PR-2 and decision #19).
- The deferral of `pytest-xdist`, `pytest-testmon`, and cross-session caching.

### v1 → v2 (prior revision)

Amendments responding to review comment id `4433175457` on PR #93.

| # | Change | Reviewer point |
|---|---|---|
| 1 | Status `draft → under review`, date marked `(v2)` | meta |
| 2 | TL;DR rewritten: "full suite remains the merge gate" → "author running `pytest tests` locally before merging (no CI yet)". "2 PRs" → "2 work items: PR-1 repo PR, PR-2 skill-library update" | 1, 5 |
| 3 | §1 Frame reworded: merge gate is local (no CI) | 1 |
| 4 | §2 principle #1 expanded: PR-1 = `tests/` + `tests/README.md`; PR-2 = skill-library, not repo | 4, 5 |
| 5 | §2 principle #5 reworded: full suite is locally-run by author before merging | 1 |
| 6 | §5 B section: review step requires *evidence* (pasted command + exit code), not a checkbox | 2 |
| 7 | §5 B section: explicit "skills live in user-scoped Perplexity Computer storage, not `java-codebase-rag`"; `docs/skills/` clarified as unrelated | 5 |
| 8 | §7 row added: GitHub Actions CI is explicitly deferred, with the specific design questions enumerated | 1 |
| 9 | §8 PR-1 step 4: mandatory pre-merge audit per Tier-2 file (was a §10 risk; now a gate) | 3 |
| 10 | §8 PR-1 step 6: `tests/README.md` cross-references the skills (this is what ties the repo PR to the skill-library work) | 4 |
| 11 | §8 PR-2 reframed: not a repo PR; ships via `save_custom_skill`; review evidence requirement stated | 2, 5 |
| 12 | §9 new decisions #16–20 added: no CI in this propose / Tier-2 audit as merge gate / mandatory evidence in PR-2 review / PR-2 is not a repo PR / TL;DR language tightened | 1, 2, 3, 4, 5 |
| 13 | §10 risks: Tier-2 mitigation upgraded to merge gate; added 2 rows for "author skips local full run" and "no CI means regressions slip" | 1, 2, 3 |

What **didn't change** (and why):
- Three-tier classification (Tier 1 / Tier 2 / Tier 3) — reviewer endorsed it.
- Three numbered work items (PR-3, PR-1, PR-2) and their landing order are unchanged; **plan-prompts** / **pr-review** naming only clarifies where each contract lives (repo vs user skill library).
- The decision to defer `pytest-xdist` / `pytest-testmon` / cross-session caching — reviewer agreed it was reasonable.
- Use-case re-walk — the 16 UCs remain valid against the v2 surface; no UC referenced a primitive that changed.

---

## Appendix C — `plan-prompts` per-PR prompt template diff (illustrative, not the final wording)

Inserted in each **Prompt** block between **Deliverables** and **Tests** (see `.cursor/skills/plan-prompts/SKILL.md` for the canonical scaffold):

```markdown
## Tests to run (iteration loop)

Run **only these tests** during your implementation loop. The full suite runs in CI on every push and is the mechanical merge gate (after PR-3 lands).

- tests/test_<file_a>.py
- tests/test_<file_b>.py

Rationale: <one-line why these specifically>.

If any of these fail, the PR is not ready. If the CI full-suite check fails after this subset passes, that is a separate (legitimate) failure mode — fix and re-push; the merge button stays disabled until CI is green.
```
