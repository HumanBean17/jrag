# Plan: test suite fast loop

Status: **active (planning)**. This plan implements
[`propose/TEST-SUITE-FAST-LOOP-PROPOSE.md`](../propose/TEST-SUITE-FAST-LOOP-PROPOSE.md).

Depends on: **none** (first landing is CI so later fixture work is mechanically validated).

## Goal

- Establish a **mechanical merge gate**: GitHub Actions runs the full default test suite on every PR and on pushes to `master`, with branch protection requiring the workflow check before merge (`JAVA_CODEBASE_RAG_RUN_HEAVY=0` in CI).
- **Collapse redundant graph builds** in pytest: one session build per static corpus (bank-chat-system + each small fixture tree used read-only), plus a shared test-layer builder for per-`tmp_path` corpora.
- **Codify iteration discipline**: authors declare a `Tests to run (iteration loop)` subset in task prompts; reviewers require evidence (subset command + exit code, and link to green CI after the gate exists). Skill updates ship **outside** this repo (user-scoped library); the repo documents the link in `tests/README.md`.

## Principles (do not relitigate in review)

- **"Gate" means enforcement** (required CI status check + branch protection); **"convention" means discipline** (declared pytest subset, pasted evidence). Do not use "gate" for author-only habits before CI lands.
- **No production code** in repo PRs: PR-3 touches `.github/workflows/` + a **minimal** `tests/README.md` stub; PR-1 **extends** that README section (same heading — no duplicate competing sections); PR-2 is not a repo PR.
- **Session DB pass-depth matches consumers**: the bank-chat session graph is **not** “whatever `conftest.py` happened to run first.” Before any Tier-1 migration, enumerate **every** test that will share a session DB and the **exact** `build_ast_graph` pass chain + `write_kuzu` it assumes today. **Widening** the session fixture (e.g. adding pass5/6) is allowed only if no consumer relied on an **intentionally weaker** graph (missing caller edges, missing match resolution, or different meta semantics). If requirements **conflict**, introduce **multiple** session-scoped bank fixtures (different pass suffixes) or keep the conflicting tests on **per-test** `tmp_path` builds — never silently point tests at a weaker or stronger graph than their current `_build` without updating assertions by explicit review.
- **No ontology bump, no re-index** — graph semantics unchanged; only test harness + CI.
- **Landing order is binding**: **PR-3 → PR-1 → PR-2**. Shipping PR-1 before PR-3 would make it easier to merge fixture regressions without mechanical detection; PR-2’s review checklist references green CI.
- **Session graphs are corpus-scoped**: each distinct static fixture directory is built at most once per pytest session; Tier-3 tests that mutate `tmp_path` keep per-test isolation.
- **Tier-2 migration is audit-gated**: each Tier-2 candidate file is verified read-only on fixture disk and session DB before conversion; failing audit **drops the file to Tier 3** (per propose decision #17).
- **No new pytest plugins** (`pytest-xdist`, `pytest-testmon`, etc.) in this plan; no cross-session Kuzu disk cache.
- **Subset is author-declared**, not inferred from `git diff`.

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-3** | GitHub Actions workflow + branch protection API/settings; short `tests/README.md` subsection (**CI gate only**) | No | `.github/workflows/test.yml`, `tests/README.md` (stub subsection) | Workflow red/green; optional dummy-failing commit on a branch before merge | Nothing |
| **PR-1** | Session fixtures per corpus; Tier 1/2/3 migration; `tests/_builders.py`; **extends** the same `tests/README.md` subsection (tiers, fixtures, iteration convention) | No | `tests/conftest.py`, `tests/_builders.py`, ~12 test modules, `tests/README.md` | Full `pytest tests` (must be green in CI after PR-3) | PR-3 merged |
| **PR-2** | `cursor-task-prompt` + `cursor-pr-review` skills (`Tests to run`, evidence steps) | N/A (skills not in repo) | Author skill library only | Human verification of skill text; optional dry-run prompt | PR-3 merged (so CI link in review skill is real) |

**Landing order: PR-3 → PR-1 → PR-2.**

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| CI Python | **3.11** (matches `requires-python` / ruff target). |
| CI install | Pinned **`requirements.txt`** + **editable install** `pip install -e .` so CLI tests and bundle layout match local `tests/README.md` guidance; do not omit `cocoindex` if tests skip when absent. |
| CI env | `JAVA_CODEBASE_RAG_RUN_HEAVY` unset or **`0`**; no heavy LanceDB e2e in default workflow. |
| Workflow triggers | `pull_request` (all branches) + `push` to **`master`**. |
| Branch protection | Required check for the workflow job; **`allow_force_pushes: false`** on **`master`**; narrow scope (no required approving reviews for sole maintainer); `enforce_admins: false` for documented break-glass (per propose §9 #23–24). The **`context` string** GitHub shows on the check run (derived from workflow name + job `name` / `id`) must be copied **verbatim** into `required_status_checks` — a mismatch blocks merges with a permanently missing check. |
| Bank-chat session pipeline | **Pre-PR-1 mandatory**: table every `kuzu_db_path` / `kuzu_graph` / `mcp_server` consumer and the passes its logic assumes. Today’s `conftest.py` builds **pass1–4 + `write_kuzu` only** — insufficient for tests that assert **`HTTP_CALLS` / `ASYNC_CALLS`** (pass5+) or pass6 match outcomes. **Either** extend the default session chain to the **least upper bound** of pass depth all shared consumers tolerate, **or** split named session fixtures (e.g. caller-edge graph vs graph without pass6) per the consumer matrix. |
| `test_call_edges_e2e.py` (bank-chat) | Current `_build` runs **pass1–5 + `write_kuzu`** and **does not** run `pass6_match_edges`. Do **not** migrate those bank-chat cases onto a session DB that runs pass6 unless the **“all matches stay `unresolved`”** (and related) assertions are revisited on purpose. Prefer a **dedicated** session fixture whose pipeline matches today’s `_build` for that file, or keep per-test `tmp_path` DBs for the incompatible cases. |
| `test_call_invariant.py` (bank-chat) | `test_call_invariant_inert_on_bank_chat_system` uses **`pass1–3 + `write_kuzu` only** (no pass4). Verify whether pointing it at a richer session graph is semantically equivalent for its assertions; if not, keep a **separate** slim session fixture or per-test build. |
| PR-3 validation | Before merging PR-3: push a **deliberately failing test** on a throwaway branch, confirm check fails and merge is blocked; revert before merge. |
| Tier-2 fixture set | Align session fixture names with **existing** dirs under `tests/fixtures/`: `call_graph_smoke`, `route_extraction_smoke`, `cross_service_smoke`, `fqn_collision_smoke`, `http_caller_smoke`, `capability_smoke`. |
| `tests/_builders.py` | Thin wrappers importing **production** `build_ast_graph` passes — **no copied logic**. Several tests today run **pass5/6** (`pass5_imperative_edges`, `pass6_match_edges`); the shipped helper(s) must match each call site (appendix in propose shows pass1–4 + `write_kuzu` only — extend as needed for Tier-2 session builds and any Tier-3 caller that needs the full pipeline). |
| Mixed files | **`test_ast_graph_build.py`**: most tests already use `kuzu_db_path`; two tests rebuild `route_extraction_smoke` into `tmp_path` — prefer **`kuzu_graph_route_extraction_smoke`** (session) if assertions are read-only, else Tier-3 helper. **`test_kuzu_queries.py`**: audit the `route_extraction_smoke` inline build (≈ line 403); same rule. |
| **`test_call_edge_matching.py`** | Mostly pure `_match_call_edge` / `graph_enrich` source reads; `_build_tables` hits **`cross_service_smoke`** read-only — candidate for **Tier 2** session materialization or a **`build_graph_tables(root)`** helper (no Kuzu) per audit, even though propose listed it under Tier 3. **Audit outcome wins** over the propose table row. |
| PR-2 delivery | **Not** a git PR to this repo; update user-scoped skills via `save_custom_skill` (or equivalent). **`tests/README.md` merged in PR-1** must read correctly **before** PR-2 lands: describe the iteration convention by pointing at **[`propose/TEST-SUITE-FAST-LOOP-PROPOSE.md`](../propose/TEST-SUITE-FAST-LOOP-PROPOSE.md)** (and this plan), and name the **`cursor-task-prompt` / `cursor-pr-review`** skills as living in the **author’s skill library** (updated in PR-2) — not “after PR-2” in a way that implies missing docs. |

---

# PR-3 — CI workflow + branch protection (ships first)

## File-by-file changes

### 1. `.github/workflows/test.yml` (new)

- Single job on **ubuntu-latest**, **Python 3.11**.
- Steps: `actions/checkout`, setup Python, `pip install -r requirements.txt`, `pip install -e .`, run **`pytest tests -v`** with `JAVA_CODEBASE_RAG_RUN_HEAVY=0` (or unset).
- Cache `pip` optional but not required for v1.

### 2. `tests/README.md` (PR-3 scope only)

- Add a **single** subsection (choose a stable heading, e.g. **“CI merge gate”**) containing only: pointer to **`.github/workflows/test.yml`**, that **`pytest tests`** is required on PR + `master` push, break-glass policy (`enforce_admins: false`), and one line that **fixture-tier / iteration-loop** detail will be expanded in PR-1 under the same heading.
- **Do not** duplicate tier tables here yet — PR-1 appends to this subsection.

### 3. Repository settings (not in git)

- Configure **`master`** branch protection via `gh api` (or GitHub UI): `required_status_checks` must list the **exact** check-run context string from a pilot workflow run (see step list below).

## Tests for PR-3

1. **Manual / procedural**: dummy failing test on a side branch → workflow **red** → merge blocked → revert dummy.
2. After merge: open any PR → workflow runs full suite automatically.

## Definition of done (PR-3)

- [ ] Workflow file present and passing on a real PR.
- [ ] `master` requires the check; force-push to `master` disabled.
- [ ] `tests/README.md` contains the PR-3 stub under a stable heading; no tier/fixture content that PR-1 will duplicate.
- [ ] Dummy-failure validation completed before merge (per propose).

## Implementation step list

| # | Step | File(s) / action | Done when |
| --- | --- | --- | --- |
| 1 | Add workflow YAML with an explicit job `name` (or rely on default) | `.github/workflows/test.yml` | `pytest tests` runs green on a test branch |
| 2 | Read check context from GitHub UI or API | pilot PR / `gh pr checks` | You have the **exact** string GitHub will require (e.g. `test / test` vs workflow filename — **verify on a real run**) |
| 3 | Validate failure mode | temporary commit on throwaway branch | Check goes red; merge disabled |
| 4 | Revert dummy | branch | Clean branch for merge |
| 5 | Apply branch protection | `gh api` / UI | `required_status_checks.contexts[]` matches step 2 **verbatim** |
| 6 | Document gate (stub) | `tests/README.md` | “CI merge gate” subsection exists; points to workflow + promises PR-1 expansion |

---

# PR-1 — Fixture refactor (Tier 1 + Tier 2 + Tier 3 helpers) (ships second)

## File-by-file changes

### 1. `tests/conftest.py`

- **Before editing builders**: complete a **bank-chat consumer matrix** (spreadsheet or markdown table in the PR-1 description): each test module / test function that today uses `kuzu_db_path`, `kuzu_graph`, `mcp_server`, or `corpus_root` together with a private `_build`, listing **pass1..passN + `write_kuzu`** in order. Resolve conflicts (see principles: pass5 without pass6 vs pass6 present; pass3-only write vs pass4+). Update **`kuzu_db_path`** (and dependents) to the agreed **superset** pipeline, **or** add parallel fixtures (e.g. `kuzu_db_path_bank_chat_callers` for pass1–5 + write only) and wire each consumer to the fixture that preserves its semantics.
- Keep / evolve **`corpus_root` → `kuzu_db_path` → `mcp_env` → `kuzu_graph` → `mcp_server`** for **bank-chat-system** once the matrix says what `kuzu_db_path` must run.
- Add **session-scoped** fixtures (pattern from propose): one named fixture per small fixture corpus, each using `tmp_path_factory` for the Kuzu DB path, building with the **exact** pass chain that corpus’s tests use today (often pass5 **and** pass6 — **do not assume** parity with the old bank-chat `kuzu_db_path`).
- Suggested public names (adjust to pytest naming style): `kuzu_graph_call_graph_smoke`, `kuzu_graph_route_extraction_smoke`, `kuzu_graph_cross_service_smoke`, `kuzu_graph_fqn_collision_smoke`, `kuzu_graph_http_caller_smoke`, `kuzu_graph_capability_smoke` (each returns a **`KuzuGraph`** or `(db_path, graph)` tuple consistent with existing tests — pick one pattern and document it in `tests/README.md`).

### 2. `tests/_builders.py` (new)

- Export **`build_kuzu_into(tmp: Path) -> Path`** for Tier-3 corpora that need pass1–4 + `write_kuzu` against a **mutable** tree under `tmp`.
- Add additional thin exports if needed (e.g. full pipeline through pass6, or in-memory `GraphTables` builder) — **no business logic**.

### 3. Tier 1 — consume session bank-chat graph (propose §3)

Only after the **consumer matrix** proves the target session fixture’s pass chain matches the test’s assumptions. Refactor inline `_build` / redundant `pass1_parse` chains to the appropriate **`kuzu_db_path`** / **`kuzu_graph`** (or a **new** named bank fixture) where the test body is read-only on the corpus:

- `tests/test_call_edges_e2e.py` — **bank-chat cases**: require pass5; **must not** pick up pass6 unless assertions are intentionally revised (see resolved decisions). **http_caller_smoke** cases may use the **`kuzu_graph_http_caller_smoke`** Tier-2 session fixture instead of bank session.
- `tests/test_call_invariant.py` — bank-chat row: **pass3-only + write** today; validate against richer session or keep slim fixture.
- `tests/test_ast_graph_build.py` (majority already; handle the two `route_extraction_smoke` tests per audit)
- `tests/test_kuzu_queries.py` (audit secondary builds)
- `tests/test_call_graph_receiver_resolution.py` — **audit**: module docstring contrasts session bank-chat with **per-test** tiny `tmp_path` graphs; there may be **no** Tier-1 win here unless individual tests are intentionally switched to a shared static corpus.
- `tests/test_lancedb_e2e.py` (respect `JAVA_CODEBASE_RAG_RUN_HEAVY` gating; still reuse bank-chat session when heavy runs)

### 4. Tier 2 — consume per-fixture session graphs (after per-file audit)

Target modules from propose (verify no writes to fixture dirs / session DB):

- `tests/test_call_graph_smoke_roundtrip.py`
- `tests/test_route_extraction.py` (imports `_normalize_path` / `_route_id` only today — confirm whether any test still needs a private build after fixture exists)
- `tests/test_cross_service_resolution_flag.py`
- `tests/test_feign_not_exposer.py`
- `tests/test_client_role_rename.py`
- `tests/test_client_hint_recovery.py`

### 5. Tier 3 — per-test `tmp_path`, shared helper

- `tests/test_brownfield_routes.py`
- `tests/test_brownfield_clients.py`
- `tests/test_client_node_extraction.py`
- `tests/test_assign_endpoint_client_extraction.py`
- `tests/test_call_edge_matching.py` (or Tier 2 / helper-only per audit — see resolved decisions)

### 6. `tests/README.md`

- **Extend the same “CI merge gate” / testing doc section PR-3 started** — append, do not create a second competing “how tests work” chapter.
- Document **three-tier model**, when to add a new session fixture, Tier-2 audit rule, **bank-chat consumer matrix** expectation for future edits, and the **iteration subset** convention with links to **[`propose/TEST-SUITE-FAST-LOOP-PROPOSE.md`](../propose/TEST-SUITE-FAST-LOOP-PROPOSE.md)** + this plan; name **`cursor-task-prompt`** and **`cursor-pr-review`** as maintained in the **author skill library** (PR-2 updates them — the README stays truthful on day one).
- Document **before/after** timing capture expectation for the PR-1 description (per propose §9 #10).

## Tests for PR-1

Entire default suite is in scope — **all existing tests** in `tests/` must pass under `JAVA_CODEBASE_RAG_RUN_HEAVY=0` in CI. No new test function names are mandatory beyond keeping current coverage; optional **timing note** in PR description (not a new automated test).

Representative high-signal modules to re-run locally during implementation (not exhaustive):

- `test_ast_graph_build.py`
- `test_call_graph_smoke_roundtrip.py`
- `test_cross_service_resolution_flag.py`
- `test_brownfield_routes.py`
- `test_brownfield_clients.py`
- `test_call_edges_e2e.py`
- `test_call_invariant.py`
- `test_kuzu_queries.py`

## Definition of done (PR-1)

- [ ] Full **`pytest tests -v`** green locally and on CI.
- [ ] No redundant full rebuild of the same static corpus across files in one session (verify with rough timing or pytest logging if useful).
- [ ] **Bank-chat consumer matrix** (pass chain per test / fixture) attached in PR-1 description and satisfied by `conftest.py` fixture layout.
- [ ] `tests/README.md` **extends** PR-3’s subsection: tiers + fixtures + propose/plan links + skill-library pointer + CI cross-reference (no duplicate gate sections).

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `_builders.py` thin helpers | `tests/_builders.py` | Tier-3 files can import shared builder |
| 2 | Bank-chat consumer matrix + fixture design | PR description + `tests/conftest.py` plan | Conflicting pass-depth requirements resolved (extra fixtures or per-test builds) |
| 3 | Implement `kuzu_db_path` / bank session chain | `tests/conftest.py` | Matrix’s required passes run once per session |
| 4 | Add session builders for fixture corpora | `tests/conftest.py` | Each fixture dir builds once per session |
| 5 | Tier-2 audits | six candidate modules | Written note: read-only OK or downgraded to Tier 3 |
| 6 | Migrate Tier 2 | those modules | No direct `pass1_parse` for static fixture roots in hot path |
| 7 | Migrate Tier 1 | six modules | Each test uses the session graph whose pipeline matches its matrix row |
| 8 | Migrate Tier 3 | five modules | Shared helper; per-test `tmp_path` preserved |
| 9 | Docs | `tests/README.md` | Appends to PR-3 subsection: tiers + matrix expectation + operational guidance |

---

# PR-2 — Skill library (ships last)

## Changes (outside this repository)

1. **`cursor-task-prompt`**: add **`## Tests to run (iteration loop)`** between deliverables and tests sections — bullet list of `tests/test_*.py` paths + one-line rationale; allow **empty list** for docs-only work (UC15).
2. **`cursor-pr-review`**: add verification requiring **(a)** pasted `pytest …` command + exit code for the declared subset, and **(b)** link to **green PR CI run** for the full suite (post PR-3).

## Tests for PR-2

- Human: skill text matches propose §5; `save_custom_skill` applied; dry-run one PR prompt/review cycle.

## Definition of done (PR-2)

- [ ] Both skills updated and saved in the user skill library.
- [ ] Review skill rejects “checkbox only” evidence for subset (per propose §9 #18).

## Implementation step list

| # | Step | Done when |
| --- | --- | --- |
| 1 | Draft `Tests to run` section in task prompt skill | Matches propose template intent |
| 2 | Draft evidence requirements in review skill | Subset + CI link both required post PR-3 |
| 3 | `save_custom_skill` / library publish | Skills live in user storage |
| 4 | Cross-check `tests/README.md` from PR-1 | Skill names + library location match saved skills; no stale “TBD” |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Fixture refactor mutates shared session state | High | Read-only contract; Tier-2 per-file audit; tests that write drop to Tier 3 |
| 2 | CI false-green (wrong command / wrong branch) | High | PR-3 dummy-failure branch validation before merge |
| 3 | `build_kuzu_into` omits pass5/6 while tests need them | Medium | Match each migrated module’s current import/call chain exactly |
| 4 | Session `kuzu_db_path` weaker than old per-test `_build` (missing pass5/6) | **High** | Consumer matrix + least-upper-bound or split fixtures; full CI green is necessary but not sufficient for this semantic trap |
| 5 | Session graph **stronger** than old `_build` (e.g. pass6 resolves edges tests expected `unresolved`) | **High** | Same matrix; never migrate `test_call_edges_e2e` bank cases onto pass6 without explicit assertion review |
| 6 | `test_ast_graph_build` / `test_kuzu_queries` partial inline builds missed | Medium | Explicit “mixed file” audit in PR-1 |
| 7 | Branch protection `required_status_checks` context mismatch | **High** | Step 2/5 in PR-3: copy verbatim from a real Actions run before enabling protection |
| 8 | Subset-only iteration misses failures | Medium | After PR-3, full suite still runs on every PR push |

# Out of scope

- Production code (`build_ast_graph.py`, `server.py`, indexer, ontology).
- **`pytest-xdist`**, **`pytest-testmon`**, pytest mark taxonomy, git-diff test selection.
- Cross-session Kuzu caching under `~/.cache/`.
- CI matrix across Python versions; **`JAVA_CODEBASE_RAG_RUN_HEAVY=1`** in default CI.
- Path-filtered workflows, self-hosted runners, required PR approvals from multiple reviewers.
- Committing Cursor skills into **`docs/skills/`** (that tree is for the separate `java-codebase-explore` artifact per propose).

# Whole-plan done definition

1. PR-3 merged: default CI runs **`pytest tests`** on PR + `master` push; branch protection blocks broken merges.
2. PR-1 merged: session-scoped graphs + `_builders.py` in use; **`tests/README.md`** documents tiers and CI; before/after timing noted in PR-1 body.
3. PR-2 complete: user skills updated; review flow uses **two evidences** (subset + CI).

# Tracking

- **PR-3 (CI + protection)**: _pending_
- **PR-1 (fixtures)**: _pending_
- **PR-2 (skills)**: _pending_
