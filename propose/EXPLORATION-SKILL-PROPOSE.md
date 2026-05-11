# EXPLORATION-SKILL — a standalone agent skill for exploring Java microservice estates

**Status**: approved — locked design for PR-EXPLORE-2 (§3–§7 authoritative).
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-11

## TL;DR

- **Frame**: `docs/AGENT-GUIDE.md` is a tool operating manual ("how to drive these four MCP tools without breaking them"). Agents driven only by it know **how** to call tools, but not **when** or **why** — they over-trust empty results, fish with `search`, treat the graph as ground truth when it's stale, and never fall back to `rg` / file reads / the CLI when MCP is the wrong layer. We need a second artefact that teaches **exploration strategy at the system level**.
- **Proposal**: add a new standalone agent skill `java-codebase-explore` shipped from this repo, distributable as a single markdown file at `docs/skills/java-codebase-explore.md` plus a **Perplexity-format** packaged `.zip` (the primary consumer). Other platforms (Claude Code, Cursor, etc.) are out of scope for v1 — added on demand once a real consumer needs them.
- **Standalone, not a sequel.** The skill is operable without `AGENT-GUIDE.md` open in the same context. It inlines a minimal cheat sheet (4 tools, 9 edge types, 3 node kinds, key slash aliases) and defers exact argument shapes to the MCP tool descriptions themselves, which every MCP client surfaces natively.
- **Scope boundary**: AGENT-GUIDE.md remains the operating manual (drop-in `CLAUDE.md` block, argument shapes, recovery playbook). The new skill is the **strategy guide** ("how to explore an unfamiliar Java microservices system end-to-end, with this MCP as one tool among several").
- **Activation**: explicit user phrases like *"explore this codebase"*, *"help me understand this system"*, *"map the call graph for X"*, *"plan a change to service Y"*, *"onboard onto this code"*. Not on every PR review and not on every `search` call.
- **Migration shape**: **2 PRs** — propose merge → ship the skill (markdown + zip + README integration: **§3** *Driving the MCP from an agent* bullet + **§9** *Further reading* table row, both linking the skill next to AGENT-GUIDE). No code changes. No ontology bump.
- **Maintenance discipline**: cheat-sheet appendix is the only place ontology strings appear in this skill; ontology version bumps update AGENT-GUIDE.md, the skill cheat sheet, and `README.md` in lockstep (already a documented invariant for AGENT-GUIDE.md).

---

## §1 — Frame: what is this skill, really?

This skill is **the strategy poster on the wall, not the operating manual on the bench.** AGENT-GUIDE.md tells the agent *"here is the lathe; here is where you put your hands."* The exploration skill tells the agent *"here is the workshop; here is the order in which you pick up tools when a customer walks in with an unfamiliar problem."*

The frame commits to one sentence: **understanding an unfamiliar Java microservices estate is a sequenced exploration problem, not a tool-selection problem.** Once you accept that, several design choices follow:

- The skill is organized by **mission** (understand a feature, plan a change, onboard, write a propose doc), not by tool.
- Each mission has a **canonical opening move** that is almost always `java-codebase-rag meta` or `find(kind=route|client, …)` — not `search`.
- Each mission has a **stopping rule** — agents are told explicitly when to put the tools down and answer, not "keep walking the graph until you feel sure."
- **Anti-capabilities** ("what this MCP cannot see") are first-class content, not a footnote. The single biggest failure mode in the wild is the agent treating an empty `search` result as ground truth.

This frame rules out:

- Turning the skill into "AGENT-GUIDE.md but longer". If the new content can be expressed as another rule of thumb for tool usage, it belongs in AGENT-GUIDE.md, not here.
- Encoding ontology details twice. The cheat sheet appendix re-states the 4 tools and 9 edges; everything else (argument shapes, recovery, slash aliases) is the operating manual's job and the skill links there by URL.
- Mission-specific runbooks for every situation. The skill ships **6 named missions** (count locked in §7) that cover the bulk of exploration intents; further missions are deferred to a v2 unless they materially differ in shape.

## §2 — Design principles

1. **Strategy not surface.** This skill teaches *when and why*, not *exact JSON shapes*. Argument-shape content stays in AGENT-GUIDE.md and the MCP tool descriptions.
2. **Standalone but not duplicative.** The skill is operable in a context that contains neither AGENT-GUIDE.md nor the project README. Only the minimum needed for operability is inlined; everything else is linked.
3. **MCP is one tool among several.** `rg`, file reads, `git log`, README, build files, and the `java-codebase-rag` CLI are first-class fallbacks. The skill teaches when each wins. An agent that uses MCP for every question is a failure mode, not a success mode.
4. **Anti-capabilities are stated explicitly.** A dedicated section enumerates what the MCP cannot see, cannot do, and cannot guarantee. "It didn't show up in `search`" must never be treated as proof of absence.
5. **Confidence and staleness are first-class.** When MCP and the open file disagree, the file wins. When `confidence` on a cross-service edge is low, that's a resolver gap signal, not a hallucination — and the agent should report it that way.
6. **Missions before tools.** Top-level content is organized by exploration goal (understand / plan / onboard / propose / debug), with the canonical tool sequence under each. Reverse-organized content (tool-by-tool) is anti-pattern — it produces the AGENT-GUIDE.md we already have.
7. **Stopping rules are mandatory per mission.** Every mission ends with an explicit "you have enough evidence when…" criterion. Unbounded walks are the second-biggest failure mode after over-trusting empty results.
8. **Activation specificity.** The skill activates on real exploration sessions, not on every search query or PR review. Over-activation dilutes its function (this guidance applies to skill metadata, not to the skill body itself).

## §3 — The proposed surface

### 3.1 Distribution shape

Two artefacts shipped from this repo:

- `docs/skills/java-codebase-explore.md` — the canonical, human-readable skill body. Under `docs/skills/` (same docs tree as `docs/AGENT-GUIDE.md`, not the same directory).
- `docs/skills/java-codebase-explore.zip` — a **Perplexity-format** skill bundle suitable for `save_custom_skill` (Perplexity Computer). Contains the `.md` plus a `SKILL.md` manifest in the Perplexity packaging convention. Other platforms (Claude Code, Cursor) are out of scope for v1 — see §8 risk row for the rationale.

Both are regenerated together by a small script (`scripts/build-explore-skill.sh`) so the `.zip` and the `.md` never drift. The script lives in this repo; running it is part of the release checklist that already exists for ontology bumps.

### 3.2 Skill body — section outline

The skill body is structured as follows. Each section is required unless marked optional. Section order is the order an agent reads on activation; later sections back-reference earlier ones.

1. **Activation note** (frontmatter / opening paragraph). Names the skill, states the activation phrases, and sets the scope boundary against AGENT-GUIDE.md in one sentence.
2. **Pre-flight: is the index built?** Canonical first call (`java-codebase-rag meta`), what to do if the project is unindexed, what to do if the graph is older than Lance (post-`increment` state).
3. **Map the seams first.** The canonical opening move for any new estate: `find(kind=route, …)`, `find(kind=client, …)`, possibly `find(kind=symbol, filter={"role":"CONTROLLER"})`. Rationale: you cannot reason about a system you have not enumerated.
4. **Mission catalogue.** Six named missions (count locked in §7 item 5; names and triggers in §3.3), each with a fixed shape (see §3.3 below).
5. **When MCP is the wrong layer.** Explicit fallback paths to `rg`, file reads, `git log`, README, build files, and the CLI. With one-line rules of thumb per fallback.
6. **What this MCP is NOT.** Anti-capabilities section (verbatim Appendix A; sketch table in §3.4).
7. **Confidence and staleness.** How to read `edge.attrs.confidence` / `attrs.strategy` / `attrs.match` (the wire field on the `Edge` payload is `attrs`); what to do when MCP disagrees with the open file; what `increment` means for graph freshness.
8. **Anti-patterns.** Numbered list of the 6–8 failure modes the skill is built to prevent. Mirrors and extends the anti-patterns list already in AGENT-GUIDE.md, with system-level additions (fishing-trip search, unbounded walks, treating empty as ground truth, asking MCP for non-MCP things).
9. **Cheat sheet appendix.** Minimal inline reference: 4 tools (one line each), 9 edge types (taxonomy table), 3 node kinds, the most-used slash aliases. Everything else links to AGENT-GUIDE.md by URL.

### 3.3 Mission shape (uniform across all missions)

Each mission in §3.2.4 follows the same template:

```
### Mission: <name>

**When it applies**: <one-sentence trigger>
**Goal**: <what evidence you need to leave with>
**Opening move**: <one tool call, often `meta` or `find`>
**Sequence**: <ordered list of tool calls, with branching points>
**Stopping rule**: <explicit "you have enough when…" criterion>
**Fallbacks**: <when this mission needs `rg` / file reads / CLI instead>
```

Missions shipped in v1 (listed here; count locked in §7 item 5):

1. **Understand a feature** — user asks "how does feature X work?" or "explain how Y is implemented."
2. **Plan a change** — user asks "I need to modify Z; what's the blast radius?" Maps to `analyze-pr` for diff-bearing cases.
3. **Onboard onto an unfamiliar service** — user asks "I'm new to service S; orient me."
4. **Trace a cross-service flow** — user asks "what happens when route R is called?" or "follow the call chain from controller A to client B."
5. **Prepare to write a propose doc** — user is about to use the propose-doc-author skill; needs the evidence-gathering phase scoped.
6. **Debug a specific symptom** — user has a symptom (error, slow path, unexpected behaviour) and needs to find where in the estate it originates.

Six missions cover ~90% of the exploration intents we've seen in this repo's history. Adding a 7th mission requires a propose, not a drive-by PR (mirrors the cardinal-number discipline from CLI-SCENARIOS §6).

### 3.4 Anti-capabilities (sketch)

Appendix A ships as a **seven-item** bullet list in the skill body. The table below is only a structural sketch; the item cap matches the §8 “dumping ground” risk row.

| What agents wrongly expect | Reality |
| -------------------------- | ------- |
| MCP can see test files | No — only Java production code, SQL, YAML chunks. Use `rg` for test discovery. |
| MCP knows what's runtime-active | No — the index is static. For "is this code reachable", combine `neighbors(in, [CALLS])` with route/client analysis, then verify with `git log` / runtime checks. |
| Empty `search` = doesn't exist | No — could be: not indexed, not in default `java` table, project never `init`'d, or in `sql`/`yaml` table. Fall back to `rg` before claiming absence. |
| `neighbors(in, [CALLS])` returns all callers | Only resolved CALLS edges in indexed projects. Reflection, dynamic dispatch through unknown interfaces, and unindexed services are invisible. |
| Cross-service edges are exhaustive | `HTTP_CALLS` / `ASYNC_CALLS` depend on resolver match quality (`edge.attrs.confidence`, `attrs.strategy`, `attrs.match`). Low confidence is a resolver gap, not a hallucination. |
| MCP knows the build / run / deploy story | No — read `README.md`, `pom.xml` / `build.gradle`, `Dockerfile`, `.github/workflows/`, `docker-compose.yml`. |
| MCP can answer "when did X change" | No — `git log`. |

### 3.5 Skill metadata (for the package manifest)

```yaml
name: java-codebase-explore
title: Explore a Java microservices codebase with the java-codebase-rag MCP
description: |
  Use when exploring an unfamiliar Java microservices estate indexed by the
  java-codebase-rag MCP. Activates on "explore this codebase", "help me
  understand this system", "map the call graph", "plan a change to service",
  "onboard onto this code", "write a propose doc for redesign". Teaches
  exploration *strategy* — when to call MCP vs fall back to rg/file reads/CLI,
  how to read staleness and confidence, and a catalogue of named missions
  (understand / plan / onboard / trace / propose / debug) with stopping rules.
  Complements but does not require docs/AGENT-GUIDE.md.
when_to_load:
  - "explore this codebase / repo / service"
  - "help me understand this microservices system"
  - "map the call graph for <service>"
  - "plan a change to <service>"
  - "onboard onto this code"
  - "write a propose doc for redesign of <component>"
when_not_to_load:
  - routine PR review (use a review skill such as `cursor-pr-review` if you have it; example external skill, not shipped from this repo)
  - single-question lookups answerable by one MCP call
  - editing existing code where the agent is already oriented
```

## §4 — Use-case re-walk

Walking 16 realistic exploration intents through the proposed skill surface. Each row records the **opening move** and the **mission** invoked. If any case requires a mission not in the catalogue or a sequence not in the skill, the surface needs revision.

| # | Intent | Mission | Opening move | Stopping rule satisfied? |
|---|---|---|---|---|
| UC1 | "Explain how the chat-core retry logic works." | Understand a feature | `search("retry chat-core")` → describe → `neighbors(out, [CALLS])` | Yes — feature shape known |
| UC2 | "I'm new to billing-service; orient me." | Onboard | `find(kind=route, filter={microservice:"billing-service"})` then `find(kind=client, …)` | Yes — service surface enumerated |
| UC3 | "If I rename method `X#foo`, what breaks?" | Plan a change | `find` X → `neighbors(in, [CALLS, INJECTS])` | Yes — caller set bounded |
| UC4 | "Trace what happens when POST /orders fires." | Trace cross-service flow | `find(kind=route, filter={path_prefix:"/orders", http_method:"POST"})` → `neighbors(in, [EXPOSES])` → walk CALLS | Yes — chain reaches a leaf or async boundary |
| UC5 | "Write a propose doc for replacing Feign with WebClient." | Prepare to write a propose doc | `find(kind=client, filter={client_kind:"feign_method"})` → enumerate scope | Yes — evidence inventory complete |
| UC6 | "Why is order-service slow on /checkout?" | Debug a symptom | `find` route → `neighbors(in, [HTTP_CALLS, EXPOSES])` → `git log` on handler | Yes or fallback engaged |
| UC7 | "Does anyone still use the legacy `LegacyClient`?" | Plan a change | `search("LegacyClient")` or `find` symbol → `neighbors(in, [INJECTS, CALLS])` | Yes — empty result handled per anti-capability rule (verify with `rg`) |
| UC8 | "Map the cross-service call graph between chat-core and notification." | Trace cross-service flow | `find(kind=client, filter={microservice:"chat-core", target_service:"notification"})` | Yes — service-to-service edge set complete |
| UC9 | "Where is the JWT validation logic?" | Understand a feature | `search("JWT validate")` → describe → CALLS out | Yes |
| UC10 | "I just cloned the repo, is the index even built?" | Pre-flight | `java-codebase-rag meta` (CLI) | Pre-flight outcome decides whether to proceed or `init` |
| UC11 | "Onboard onto a service the index has never seen." | Onboard + fallback | `meta` → see service absent → fall back to `rg` + README + `find` for adjacent services | Yes — anti-capability rule prevents pretending it's indexed |
| UC12 | "What handles route `/v1/payments`?" | Trace cross-service flow (short form) | `find(kind=route, filter={path_prefix:"/v1/payments"})` → `neighbors(in, [EXPOSES])` | Yes |
| UC13 | "Plan a change to extract billing-service into a library." | Plan a change | `find(kind=route, filter={microservice:"billing-service"})` to enumerate public seams → `find(kind=client, filter={target_service:"billing-service"})` for inbound dependencies | Yes — both seams enumerated |
| UC14 | "I see a stale CALLS edge from method A to B but the open file shows B is deleted. Who do I believe?" | (Confidence & staleness section, not a mission) | Read `edge.attrs.confidence`; trust file; check `meta` for last `reprocess` time | N/A — meta-skill content |
| UC15 | "Help me explore what 'cocoindex' actually does at runtime." | Fallback — not an MCP question | `rg "cocoindex"` + file reads + `git log` | Anti-capability rule triggers fallback |
| UC16 | "What does `analyze-pr` do, and when should I use it?" | Fallback — CLI help, not MCP | `java-codebase-rag analyze-pr --help` + CLI doc | Anti-capability rule triggers fallback |

**Result of the re-walk:**

- Every intent maps to either a mission (UC1–UC13) or to a meta-skill section (UC14) or to a fallback rule (UC11, UC15, UC16). No intent requires a 7th mission.
- The fallback rules in §3.2.5 ("When MCP is the wrong layer") and the anti-capability section in §3.2.6 are exercised by 4 of the 16 cases — confirming they are weight-bearing sections, not garnish.
- The pre-flight section in §3.2.2 is exercised by UC10 alone, but it's the first thing every other mission depends on, so it stays as section 2.

No surface revisions triggered.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
| ------------------ | -------------- |
| Replace AGENT-GUIDE.md | Different scope (operating manual vs strategy guide). Both ship side-by-side. |
| Embed full argument-shape reference | Duplicates AGENT-GUIDE.md; high drift risk; cheat sheet is enough for operability. |
| Generate runtime / dynamic call traces | The MCP is static-only by frame. Skill teaches the boundary, doesn't try to extend it. |
| Ship a per-language version (Kotlin, Scala, Python explore skills) | Out of scope until the MCP itself supports those languages. One language per skill. |
| Auto-generate the skill from the codebase | Tempting (the ontology *is* the codebase) but defers iteration on the prose. v2 question. |
| Bundle multiple skills (review + propose + explore) into one mega-skill | Three skills, three scopes — same discipline as the three-artefact propose/plan/cursor-prompt flow. |
| Translate the skill body into Russian | The MCP audience is mixed-language; AGENT-GUIDE.md is English; consistency wins. User-facing prose can be translated downstream. |
| Auto-activate on every PR review | Skill activation is intent-scoped to exploration sessions. Routine review uses `cursor-pr-review`. |

## §6 — Migration plan — 2 PRs

### PR-EXPLORE-1 — propose merge

**Title**: `propose: java-codebase-explore skill`
**Purpose**: this document. Lock the design.
**Tests**: none (doc-only).

### PR-EXPLORE-2 — ship the skill

**Title**: `feat(docs): java-codebase-explore agent skill`
**Purpose**: add `docs/skills/java-codebase-explore.md`, the build script `scripts/build-explore-skill.sh`, and the generated `docs/skills/java-codebase-explore.zip`. **README:** add a short pointer in **§3** *Driving the MCP from an agent* (new bullet next to the AGENT-GUIDE bullet) **and** a row in **§9** *Further reading* so operators who skip §3 still find the skill.
**Tests**: none (doc-only). Acceptance check: a fresh agent given only the skill body must complete **UC2** (onboard onto an unfamiliar service) **and UC6** (debug a symptom — second smoke case to exercise the fallback rules) using the documented sequences without asking the user for tool-shape clarification. Verified by hand in the PR description, not in CI.

Total: 2 PRs.

## §7 — Decisions taken (no longer open)

1. **Skill name is `java-codebase-explore`.** Not `explore`, not `codebase-onboarding`. Brand-aligned with `java-codebase-rag`.
2. **Distribution is markdown + zip from this repo.** Both regenerated by `scripts/build-explore-skill.sh`. No separate skills repo.
3. **The skill is standalone.** Operable without AGENT-GUIDE.md or README in context. Cheat sheet appendix inlines the minimum.
4. **AGENT-GUIDE.md remains unchanged in scope.** Three surgical patches to it ("What this MCP is NOT", "Staleness & fallback", "Confidence calibration") are handled by a separate propose doc (`AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`).
5. **Mission count is locked at 6.** Adding a 7th mission requires a propose. (The cardinal-number-locking discipline mirrors [`propose/completed/CLI-SCENARIOS-PROPOSE.md`](completed/CLI-SCENARIOS-PROPOSE.md) §6.)
6. **Section order in the skill body is locked.** Pre-flight → seams → missions → fallbacks → anti-capabilities → confidence/staleness → anti-patterns → cheat sheet. Reordering requires a propose.
7. **Anti-capabilities are first-class.** They get their own section, not a footnote in a mission.
8. **No code changes.** No ontology bump. No schema bump.
9. **Activation phrases are intent-scoped.** Listed in §3.5; no auto-activation on every `search` call or PR review.
10. **Cheat sheet is the only place ontology strings appear.** Bumping the ontology updates AGENT-GUIDE.md, README, and this skill in lockstep. A maintenance note in `docs/AGENT-GUIDE.md` that names this skill is **out of scope for PR-EXPLORE-2** (that file stays unchanged here); it lands with [`AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`](./AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md) or a small follow-up doc PR after coordination — same split as `plans/PLAN-EXPLORATION-SKILL.md` states.

## §8 — Risks and how we mitigate

| Risk | Mitigation |
| ---- | ---------- |
| Skill body and AGENT-GUIDE.md drift on the 4-tool / 9-edge surface | Cheat sheet is the only overlap; ontology-bump checklist explicitly lists both files. |
| Agents over-activate the skill (treat it as default for any search) | Activation phrases in §3.5 are intent-scoped; description explicitly states "use a review-oriented skill (example: `cursor-pr-review`) for routine PR review". `cursor-pr-review` is an example of such a skill (it lives in Dmitriy's user-skill library) and not shipped from this repo. |
| Six missions don't cover real-world intent distribution | Use-case re-walk covered 16 cases with 0 misses. v2 adds missions only if a real session needs one. |
| Skill body too long to be effective (skill bloat) | Strict section budget: target ≤ 800 lines including cheat sheet. Re-walked use cases use ≤ 5 calls each, so prose can stay tight. |
| The `.zip` package format diverges across target platforms (Claude Code vs Cursor vs Perplexity) | v1 ships **Perplexity format only** — the primary consumer. Adding Claude Code / Cursor variants is deferred until a real downstream consumer needs them. This is the single source of truth on package scope; §1 / §3.1 align with this row. |
| The "What this MCP is NOT" section becomes a dumping ground | Cap at **seven** Appendix A items; new entries require a propose amendment, mirroring the cardinal-number discipline from CLI-SCENARIOS §6. |
| README pointers rot when AGENT-GUIDE.md or skill is renamed | Two touchpoints (**§3** bullet + **§9** table row) ship in the same PR so discoverability stays aligned; update both together. |
| Skill body translation drift if user-rag becomes bilingual | English-only for shipped skill prose in v1 (see §5 translation non-goal); downstream translation is out of scope here. |

## Appendix A — Anti-capabilities draft (verbatim wording for the skill)

The shipped skill will contain this section verbatim. Reviewing here so the wording lands before implementation:

```
## What this MCP cannot see, cannot do, and cannot guarantee

If you find yourself surprised by an empty result, a missing edge, or a stale
fact, read this section first. The MCP is a static graph + vector index over
indexed Java production code. The following are out of frame:

- **Test files.** Not indexed. Use `rg` for test discovery.
- **Build, deploy, runtime story.** Not indexed. Read `README.md`, `pom.xml`,
  `build.gradle`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/`.
- **When something changed.** Use `git log` / `git blame`.
- **Reflection and dynamic dispatch.** `CALLS` edges resolve static method
  calls; reflective invocations, SPI lookups, and dynamic proxies are
  invisible. Treat the resolved caller set as a lower bound, not a complete
  set.
- **Unindexed services.** A service that was never `init`'d does not exist
  from MCP's point of view. Verify with `java-codebase-rag meta` before
  claiming absence.
- **Cross-service edge completeness.** `HTTP_CALLS` / `ASYNC_CALLS` depend on
  the resolver matching a client invocation to a route. Low `confidence` in
  `edge.attrs` (`attrs.confidence`, `attrs.strategy`, `attrs.match`) is a resolver gap signal, not a hallucination. Report it
  as such.
- **Stale graph after `increment`.** `increment` updates Lance but not Kuzu.
  A graph older than the source tree is normal mid-development. Check the
  last `reprocess` time via `meta`; when in doubt, the open file wins.

When MCP and the open file disagree, **the file wins.** Report the
disagreement as evidence of staleness, not as a contradiction.
```

## Appendix B — Cheat sheet draft (verbatim cheat-sheet appendix for the skill)

```
## Cheat sheet (inline reference)

Four MCP tools:

- `search(query, table, hybrid, limit, filter)` — fuzzy locate.
- `find(kind, filter, limit)` — structured listing; `filter` is required.
- `describe(id)` — full node + `edge_summary`.
- `neighbors(ids, direction, edge_types, filter, limit)` — one hop;
  `direction` and `edge_types` are required.

Three node kinds: `symbol`, `route`, `client`. Ids carry a prefix
(`sym:`, `route:` / `r:`, `client:` / `c:`).

Nine edge types:

| Group | Edges |
| ----- | ----- |
| Type wiring | `EXTENDS`, `IMPLEMENTS`, `INJECTS` |
| Containment | `DECLARES`, `DECLARES_CLIENT` |
| Method calls | `CALLS` |
| Service boundary | `EXPOSES` |
| Cross-service | `HTTP_CALLS`, `ASYNC_CALLS` |

For exact argument shapes, recovery playbook, and slash aliases see
`docs/AGENT-GUIDE.md` in the java-codebase-rag repo.
```
