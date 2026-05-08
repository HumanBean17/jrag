# Agent Skills and Commands — High-level intents over the 4-tool MCP

**Status**: draft
**Author**: Dmitry + Computer
**Date**: 2026-05-08

## TL;DR

- The 4-tool MCP API (`search`/`find`/`describe`/`neighbors`) traded surface area for composability — the convenience that v1 tools (`list_routes`, `where_used`, `callers_of`, `implementations_of`, `outbound_calls`, …) provided is gone from the MCP.
- That convenience belongs at a different layer: **agent-side skills and slash-commands**, not MCP tools and not CLI subcommands.
- Ship a single shared skill source — `agent-skills/` — that compiles into both `.claude/skills/<name>/SKILL.md` (Claude Code) and `.qwen/skills/<name>/SKILL.md` (Qwen Code). **Same SKILL.md format on both hosts** (YAML frontmatter + markdown body); only the install path differs.
- Tier 1 (high-leverage, low cost): 10 navigation skills covering the 10 most common query patterns from the existing `AGENT-GUIDE.md` slash-style alias section.
- Tier 2 (polish): 3 workflow skills that chain multi-step intents (`/explain-feature`, `/impact-of`, `/trace-request-flow`).
- Tier 3 (deferred): `user-rag dump-*` CLI helpers — out of scope here.
- Migration is **5 PRs**: (1) propose lock, (2) shared `agent-skills/` source + compile script, (3) Tier 1 skills, (4) Tier 2 workflow skills, (5) AGENT-GUIDE rewrite to point at the shipped skills instead of duplicating prose templates.

## §1 Frame: what is this thing, really?

**Skills and slash-commands are agent-side prompt scaffolding for high-level user intents — they are NOT a second MCP API and NOT a CLI.**

The MCP is a graph-and-vector navigator. It has 4 primitives because that's the smallest set that covers the access pattern matrix without bloating the tool-selection problem for weak models. But "smallest set of primitives" and "things a developer wants to ask" are different shapes. A developer thinks `"who calls ChatController#joinOperator?"`, not `"find then neighbors with direction=in and edge_types=[CALLS]"`. Skills are how that gap gets bridged without breaking the MCP design.

This frame rules things out:

- **Skills are not a second MCP.** They contain no new graph queries, no new vector backends, no new edge types. Every skill is a deterministic chain of existing MCP calls.
- **Skills are not CLI subcommands.** The `user-rag` CLI is for ops (refresh/meta/tables/diagnose-ignore/analyze-pr). Adding `user-rag list-routes` would give the same query three homes (MCP + skill + CLI) — pick one.
- **Skills are not the AGENT-GUIDE.** The guide is reference doc that the agent reads once. Skills are *invokable* — the user types `/callees ChatController#joinOperator(JoinOperatorRequest)` and the model executes a known chain. Same content, different actuation.
- **Skills are not free.** Each one is recurring tokens in the agent's context once invoked, plus maintenance cost when the MCP surface evolves. The set must be small and earn its place.

## §2 Design principles

1. **Single source of truth.** One markdown file per skill in `agent-skills/<skill-name>/SKILL.md`. Build step copies (or symlinks) to `.claude/skills/<name>/SKILL.md` and `.qwen/skills/<name>/SKILL.md`. No drift between hosts.
2. **Identical format across Claude Code and Qwen Code.** Both hosts accept `SKILL.md` with YAML frontmatter (`name` + `description`) and a markdown body. Verified May 2026 — see Appendix A.
3. **Every skill is a deterministic MCP chain.** No prose like "consider running `find` if appropriate" — the body must say exactly which calls to make and in what order.
4. **Skills wrap MCP, never replace it.** A skill body always names the underlying MCP tools used. This keeps the agent able to drop into raw MCP if the skill doesn't fit.
5. **Slash-name = skill-name = filename.** `/callees` ↔ `agent-skills/callees/SKILL.md`. No alias indirection. (Both Claude Code and Qwen Code derive the slash-name from the directory name, so this is structural, not a convention.)
6. **Skills carry their own argument contract.** Each skill's body specifies exactly what positional arguments it expects (e.g. `/callees <symbol_id>`) and what to do if the argument is the wrong shape (e.g. resolve via `find` first if it doesn't look like a `sym:` id).
7. **Skills are versioned with the MCP, not separately.** When `NodeFilter` keys change or edge types are added, skills get updated in the same PR. Lockstep with `AGENT-GUIDE.md` and `README.md`.
8. **No skill ships without a working example.** Every SKILL.md ends with a worked example using the bank-chat-system fixture, so a maintainer can verify the chain still works after MCP changes.

## §3 The three layers

This diagram lives at the top of the resulting README and is the canonical mental model:

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /show-callees, /find-controllers,     │
│   /impact-of, /list-routes-by-service                        │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: skills shipped as SKILL.md files in        │
│   .claude/skills/ and .qwen/skills/ (same format, same       │
│   source). Each skill expands to a deterministic chain of    │
│   MCP calls + light post-processing.                         │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — Composable primitives (the MCP API)                │
│   search, find, describe, neighbors                          │
│   ─────────────────────────────────────────────────────────  │
│   This is what shipped in MCP API v2. It stays at 4.         │
├──────────────────────────────────────────────────────────────┤
│ Layer 1 — Storage primitives                                 │
│   Kuzu Cypher + LanceDB tables                               │
└──────────────────────────────────────────────────────────────┘
```

**Why Layer 3 is the right home for `/list_routes` and friends:**

- It's per-host (Claude Code, Qwen Code) — meaning different model tunings can have slightly different chains tuned for their tool-selection behaviour.
- It doesn't pollute the MCP surface — the 4-tool count stays load-bearing for tool-selection on weak models.
- It doesn't duplicate the CLI's audience — CLI is ops; skills are queries.
- It compiles from one source, so Claude Code and Qwen Code stay in sync without 2× edits.

## §4 Audit by call site (who actually invokes these?)

Before deciding to ship a skill at all, list realistic callers. If the dominant caller isn't an interactive agent session, the feature belongs elsewhere.

| Skill candidate | Dominant caller | Verdict |
|---|---|---|
| `/callees`, `/callers`, `/handlers`, `/implements`, `/injects` | Interactive agent session ("show me X") | ✅ Ship — Tier 1 |
| `/controllers`, `/routes`, `/clients` | Interactive agent session ("list X in service Y") | ✅ Ship — Tier 1 |
| `/explain-feature`, `/impact-of`, `/trace-request-flow` | Interactive agent session, multi-step | ✅ Ship — Tier 2 (workflows) |
| `dump-routes`, `dump-symbols-by-role` | CI scripts, ad-hoc developer terminal | ❌ CLI helper, out of scope here |
| `who-changed-this`, `git-blame-via-mcp` | Interactive agent | ❌ No MCP primitive for it; out of scope until ontology covers VCS |
| `find-similar-code` | Interactive agent | ❌ Already covered by raw `search` — adding a skill is wasted surface |

Result: **13 skills total** — 10 Tier 1 + 3 Tier 2. No CLI work in this proposal.

## §5 The proposed skill set

### Tier 1 — Navigation (10 skills)

Direct wraps of the slash-style aliases already documented in `docs/AGENT-GUIDE.md` § "Slash-style aliases (prompt templates)". The aliases exist as prose templates today; this tier promotes them to first-class shipped skills.

| Slash | One-line purpose | MCP chain |
|---|---|---|
| `/nl <text>` | Natural-language to graph navigation | `search({query, limit:8})` → `describe(top_hit.id)` |
| `/controllers <ms?>` | List controllers (optionally per service) | `find({kind:symbol, filter:{role:CONTROLLER, microservice?:ms}})` |
| `/routes <ms?>` | List HTTP routes (optionally per service) | `find({kind:route, filter:{microservice?:ms}})` |
| `/clients <ms?>` | List outbound clients | `find({kind:client, filter:{microservice?:ms}})` |
| `/callers <id>` | Who calls this symbol/route | `neighbors({ids:id, direction:in, edge_types:[CALLS,HTTP_CALLS,ASYNC_CALLS]})` |
| `/callees <id>` | What does this symbol/route call | `neighbors({ids:id, direction:out, edge_types:[CALLS,HTTP_CALLS,ASYNC_CALLS]})` |
| `/handlers <route_id>` | Method that handles a route | `neighbors({ids:route_id, direction:in, edge_types:[EXPOSES]})` |
| `/who-hits-route <route_id>` | All inbound paths to a route (callers + handlers) | `neighbors({ids:route_id, direction:in, edge_types:[HTTP_CALLS,ASYNC_CALLS,EXPOSES]})` |
| `/implements <type_id>` | Concrete classes implementing an interface | `neighbors({ids:type_id, direction:in, edge_types:[IMPLEMENTS]})` |
| `/injects <type_id>` | Where a type is injected | `neighbors({ids:type_id, direction:in, edge_types:[INJECTS]})` |

Each skill body must include: trigger description for auto-discovery, exact MCP call(s) with required parameters, what to do when the argument is missing or the wrong shape (e.g. resolve via `find` first), worked example with the bank-chat-system fixture, expected output shape.

### Tier 2 — Workflow (3 skills)

Multi-step intents that compose Tier 1 with reasoning gates.

| Slash | Purpose | Chain shape |
|---|---|---|
| `/explain-feature <text>` | Understand how a feature works end-to-end | `search` → pick top 1-3 → `describe` each → walk outward with `neighbors` (small `edge_types` per step) until question answered |
| `/impact-of <id>` | What breaks if this changes | `neighbors(out, [CALLS,HTTP_CALLS,ASYNC_CALLS])` recursive depth 2 ∪ `neighbors(in, [INJECTS,EXTENDS,IMPLEMENTS])` recursive depth 2, dedupe, render impact graph |
| `/trace-request-flow <route_or_path>` | Follow a request from entrypoint to DB | `find(route, {path}) or find(symbol, …)` → `neighbors(out, [EXPOSES,CALLS,HTTP_CALLS])` recursive depth 4 → render as ordered sequence |

These are workflows, not single-call wrappers. Their bodies must specify: the recursion depth limit (always finite — no unbounded BFS), the dedup strategy, the stop condition, and how to render the result for the user.

### Tier 3 — CLI helpers (NOT in this proposal)

Out of scope. If the user later wants `user-rag dump-routes` for scripting, that's a separate proposal. Frame: "graph debug helper for CI/scripts," not "list_routes for users."

## §6 Layout and build

### Source layout

```
user-rag/
  agent-skills/                            ← source of truth
    callees/
      SKILL.md
    callers/
      SKILL.md
    controllers/
      SKILL.md
    ...
    explain-feature/
      SKILL.md
    impact-of/
      SKILL.md
    trace-request-flow/
      SKILL.md
    README.md                              ← describes the layout, the layer diagram, how to compile
    compile.py                             ← copies/symlinks to .claude/skills and .qwen/skills
```

### SKILL.md template (verified compatible with Claude Code and Qwen Code)

```markdown
---
name: callees
description: Show what a symbol or route calls. Use when the user asks "what does X call", "callees of X", or "what does X invoke". Argument is a sym:/route:/client: id or a name to resolve.
---
# /callees — Show callees of a symbol or route

## Argument contract

Single positional argument: a graph node id with sym:/route:/client: prefix
(preferred) OR a bare name (will be resolved via `find` first).

## Steps

1. If the argument starts with `sym:`, `route:`, or `client:`, use it directly.
   Otherwise call `find({kind: "symbol", filter: {fqn_prefix: <arg>}, limit: 5})`
   and ask the user to disambiguate if more than one hit.
2. Call `neighbors({ids: <resolved_id>, direction: "out",
   edge_types: ["CALLS", "HTTP_CALLS", "ASYNC_CALLS"]})`.
3. Render results grouped by edge_type. Show fqn + microservice for each callee.

## Worked example

User: /callees ChatController#joinOperator(JoinOperatorRequest)
You: → find({kind:"symbol", filter:{fqn_prefix:"ChatController#joinOperator"}, limit:5})
   → resolves to sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
   → neighbors({ids:"sym:...", direction:"out",
                edge_types:["CALLS","HTTP_CALLS","ASYNC_CALLS"]})
   → returns 4 CALLS edges (to chat service methods) + 1 HTTP_CALLS edge

## Out of scope

- Recursive callees beyond depth 1 (use /trace-request-flow instead).
- Filtering by microservice (compose with /controllers if needed).
```

### Compile step

A simple script `agent-skills/compile.py` reads `agent-skills/*/SKILL.md` and writes:

- `.claude/skills/<name>/SKILL.md`
- `.qwen/skills/<name>/SKILL.md`

Symlink mode (development) and copy mode (CI / publish) both supported. Compile is idempotent. Run via `user-rag compile-skills` (added as a CLI subcommand) OR via `make skills` (whichever the team prefers — decision deferred to plan).

### Where they install for users

Per Claude Code and Qwen Code conventions:

| Host | Project-scoped | User-scoped |
|---|---|---|
| Claude Code | `.claude/skills/<name>/SKILL.md` | `~/.claude/skills/<name>/SKILL.md` |
| Qwen Code | `.qwen/skills/<name>/SKILL.md` | `~/.qwen/skills/<name>/SKILL.md` |

This proposal ships **project-scoped** skills checked into the repo. Users can copy them to their user-scope dir if they want them in every project.

## §7 Use-case re-walk

15 realistic use cases, walked through the proposed Layer-3 surface. Each row shows what the user types, which skill fires, and the resulting MCP chain.

| # | User intent | Slash | MCP chain | Calls |
|---|---|---|---|---|
| UC1 | List all controllers | `/controllers` | `find(symbol, {role:CONTROLLER})` | 1 |
| UC2 | List controllers in chat-core | `/controllers chat-core` | `find(symbol, {role:CONTROLLER, microservice:chat-core})` | 1 |
| UC3 | List all HTTP routes | `/routes` | `find(route, {})` | 1 |
| UC4 | Routes in chat-assign | `/routes chat-assign` | `find(route, {microservice:chat-assign})` | 1 |
| UC5 | Who calls ChatController#joinOperator | `/callers ChatController#joinOperator(...)` | `find` to resolve → `neighbors(in, [CALLS,HTTP_CALLS,ASYNC_CALLS])` | 2 |
| UC6 | What does ChatController#joinOperator call | `/callees ChatController#joinOperator(...)` | same resolve + `neighbors(out, [...])` | 2 |
| UC7 | What handles `POST /chat/join` | `/handlers route:POST:/chat/join` | `neighbors(in, [EXPOSES])` | 1 |
| UC8 | Concrete impls of `OperatorAssignmentService` | `/implements sym:...OperatorAssignmentService` | `neighbors(in, [IMPLEMENTS])` | 1 |
| UC9 | Where is `OperatorAssignmentService` injected | `/injects sym:...OperatorAssignmentService` | `neighbors(in, [INJECTS])` | 1 |
| UC10 | Outbound clients in chat-core | `/clients chat-core` | `find(client, {microservice:chat-core})` | 1 |
| UC11 | "How does operator assignment work?" | `/explain-feature operator assignment` | `search` → `describe` × 3 → `neighbors(out)` × N | 5–10 |
| UC12 | What breaks if I change `ChatRepository` | `/impact-of sym:...ChatRepository` | `neighbors(out)` ∪ `neighbors(in, [INJECTS,EXTENDS,IMPLEMENTS])` recursive depth 2 | 4–8 |
| UC13 | Trace `POST /chat/escalate` end-to-end | `/trace-request-flow POST /chat/escalate` | `find(route, {path})` → `neighbors(out, [EXPOSES,CALLS,HTTP_CALLS])` × 4 | 5–10 |
| UC14 | "Find authentication-related code" | `/nl authentication` | `search({query:"authentication"})` → `describe(top_hit)` | 2 |
| UC15 | All `@Scheduled` methods in chat-core | (no skill) — raw `find(symbol, {capability:SCHEDULED_TASK, microservice:chat-core})` | 1 | 1 |

UC15 deliberately has no skill — it's a one-shot `find` call. Adding a skill for every possible `NodeFilter` combination would defeat the point. The skill set covers high-frequency intents; raw MCP covers the long tail. **No use case requires a primitive that doesn't exist.**

## §8 What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Add MCP tools | Defeats v2's load-bearing 4-tool design. Skills do this work better. |
| Add CLI query subcommands | Different audience (ops vs. queries), different home. Tier 3 candidate, separate proposal. |
| Add skills for every possible filter combination | Long tail belongs in raw MCP. Skills are for high-frequency intents only. |
| Per-user customization of skill chains | Out of scope — ship one canonical chain per skill. Users can override at `~/.claude/skills/`. |
| Skill versioning independent of MCP | Skills track MCP. Lockstep updates only. |
| Cursor-specific skill format | Cursor uses `.cursor/rules/*.mdc` which already exist for *standing* guidance (e.g. `python-venv-only.mdc`). Slash-invokable skills are a different artifact — Cursor doesn't have a 1:1 equivalent today, so Cursor users get the AGENT-GUIDE.md path (which already covers the same intents as prose). |
| `/git-blame`, `/who-changed-this` | Requires VCS data not yet in the ontology. Out of scope until that's modeled. |
| Multi-host source generators (e.g. for VS Code, Continue) | Two hosts (Claude Code, Qwen Code) is enough to validate the shared-source model. Add hosts when there's a third real user. |
| Skills that reach into the CLI | Skills run at the agent layer; the agent calls MCP. CLI is for humans. Don't cross the streams. |
| Narrow `/callers-direct` / `/callees-direct` variants (CALLS-only, no HTTP/ASYNC) | Decision #13 widens `/callers`/`/callees` to `[CALLS, HTTP_CALLS, ASYNC_CALLS]` deliberately. If real usage shows users frequently want the in-process-only view, ship narrow variants in a follow-up — but the default semantics match the developer's mental model better. |

## §9 Migration plan — 5 PRs

### PR-S-1 — Lock the propose

Open this propose as a draft PR. Iterate. When merged, status flips to `locked` and the migration begins. No code yet.

**Test summary**: N/A.

### PR-S-2 — Shared `agent-skills/` source + compile script

Add the `agent-skills/` directory with `README.md` (architecture overview + Layer-3 diagram + how to compile), a **minimal-working `compile.py`** (≤ 80 lines: walk `agent-skills/*/SKILL.md`, write to `.claude/skills/<name>/SKILL.md` and `.qwen/skills/<name>/SKILL.md` with the `# AUTOGENERATED` banner; copy mode only — symlink mode lands in PR-S-3 if needed), and the `user-rag compile-skills` CLI subcommand. Skip the actual SKILL.md files — those land in PR-S-3/4.

The compile script must be functional from this PR onwards (not stubbed). It will simply have nothing to compile until PR-S-3 adds source files. This makes PR-S-2 self-contained: a reviewer can run `user-rag compile-skills` on an empty source dir and see two empty output dirs (or no-op), and run it again with a single hand-placed fixture skill and see it copied correctly. **No `NotImplementedError` placeholders.**

**Acceptance criterion (verified during PR review, not a checked-in test)**: project-scoped install at `.claude/skills/<name>/SKILL.md` takes precedence over user-scoped `~/.claude/skills/<name>/SKILL.md` on both Claude Code and Qwen Code when names collide. Verified by a one-time manual collision probe documented in `agent-skills/README.md` (place a stub skill at both scopes with a distinguishable description; invoke; record which one fires). Result is recorded in the PR description; no automated test ships, since we don't host-mock the agent runtime.

**Test summary**: 3 tests. (1) `compile.py` is idempotent on an empty source dir (runs twice, output dirs unchanged). (2) `compile.py` correctly copies a single 1-skill fixture to both `.claude/skills/` and `.qwen/skills/` with the `# AUTOGENERATED` banner prepended. (3) `user-rag compile-skills` CLI subcommand exists and invokes the same code path.

### PR-S-3 — Tier 1 navigation skills (10 skills)

Add `agent-skills/<slash>/SKILL.md` for each of the 10 Tier 1 entries. Run compile. Commit `.claude/skills/` and `.qwen/skills/` outputs. Each SKILL.md must include the worked-example block.

**Test summary**: 1 frontmatter test per skill (10 total) verifying YAML frontmatter (`name` + `description`) is present and well-formed. 1 integration test that runs `compile.py` and checks both output dirs match expected file count. **Plus 1 cross-skill static MCP-call validator** (~50 lines) that parses each SKILL.md body, extracts the named MCP tool calls (`search`/`find`/`describe`/`neighbors`), and asserts: (a) every tool name resolves to one of the 4 v2 tools; (b) every `kind:` value used in `find` calls is one of the 4 known kinds (`symbol`/`route`/`client`/`text`); (c) every value in `direction:` is `in` or `out`; (d) every value in `edge_types:` is one of the 9 known edge types. This catches stale skill bodies the day a kind/direction/edge_types value changes — the lockstep enforcement promised in decision #10.

### PR-S-4 — Tier 2 workflow skills (3 skills)

Add `/explain-feature`, `/impact-of`, `/trace-request-flow`. These have multi-step bodies — testing focuses on schema validity (`name` + `description` present, body has the required H2 sections per template).

**Test summary**: 3 frontmatter tests + 3 body-structure tests (assert each skill body has `## Steps`, `## Worked example`, `## Out of scope`). **Extend the static MCP-call validator from PR-S-3** to also cover the workflow skill bodies — same enforcement (tool names, `kind`, `direction`, `edge_types` enum values), now over all 13 skills. +6 net behavioural tests; the validator is one extended test, not three new ones.

### PR-S-5 — `AGENT-GUIDE.md` rewrite

The slash-style aliases section in `AGENT-GUIDE.md` becomes a *pointer* to `agent-skills/` instead of a duplicate prose copy. The forced reasoning preamble, decision tree, and edge taxonomy stay in AGENT-GUIDE.md (those are reference material, not invokable). Update the README to include the Layer-3 diagram from §3. Update the navigation in `docs/AGENT-GUIDE.md` to call out that the slash-style content has moved to shipped skills.

**Test summary**: no new tests; `tests/test_agent_guide_consistency.py` (if it exists, otherwise add it) gets +1 assertion that the slash-aliases section now references `agent-skills/` rather than embedding the bullet list.

**5 PRs total.** No ontology bump, no schema delta, no MCP surface change. Pure additive markdown + a small compile script.

## §10 Decisions taken (no longer open)

1. **Skills live at Layer 3 (agent-side prompt scaffolding), not Layer 2 (MCP) or Layer 1.5 (CLI).**
2. **Single source of truth at `agent-skills/<name>/SKILL.md`** in this repo. Two host outputs: `.claude/skills/` and `.qwen/skills/`. Compile is one-way (source → host).
3. **Identical SKILL.md format across both hosts.** YAML frontmatter (`name` + `description`) + markdown body. Verified both Claude Code and Qwen Code parsers accept this format as of May 2026 (see Appendix A).
4. **Project-scoped install by default.** Skills are checked into the repo at `.claude/skills/` and `.qwen/skills/`. Users who want them globally can copy to their `~/.claude/skills/` and `~/.qwen/skills/`.
5. **Slash name = skill name = directory name.** No alias indirection.
6. **No CLI query subcommands in this proposal.** Tier 3 deferred indefinitely.
7. **No new MCP tools and no MCP surface changes** as part of skill rollout.
8. **Cursor support deferred** — `.cursor/rules/*.mdc` are for standing guidance, not slash-invokable. Cursor users continue using AGENT-GUIDE.md prose.
9. **Skill set = 10 Tier 1 + 3 Tier 2 = 13 total.** UC15 (`@Scheduled` methods) is the canonical example of "this is raw MCP, not a skill."
10. **Skills are versioned in lockstep with the MCP.** When `NodeFilter` keys, `edge_types`, or `kind` values change, every affected skill ships an update in the same PR.
11. **Compile script lives in `agent-skills/compile.py`** and is invoked via `user-rag compile-skills` (new CLI subcommand). Adds one ops subcommand to the CLI; doesn't add query subcommands.
12. **Skills are tested at three levels: schema, static MCP-call validation, NOT behavior.** (a) **Schema**: every SKILL.md has well-formed YAML frontmatter with `name` + `description`. (b) **Static MCP-call validation**: every MCP call referenced in a skill body uses real tool names (`search`/`find`/`describe`/`neighbors`), real `kind` values (`symbol`/`route`/`client`/`text`), real `direction` values (`in`/`out`), and real `edge_types` (the 9 v2 values). (c) **NOT behavior**: tests do not run the MCP chains end-to-end against a fixture graph; behavioral correctness is the human-eval loop on real codebases. Levels (a) and (b) catch the lockstep-update violations that drift between MCP and skills; level (c) is deliberately skipped because it would require host-runtime mocking that doesn't justify its maintenance cost.

13. **`/callers` and `/callees` follow the broader edge set `[CALLS, HTTP_CALLS, ASYNC_CALLS]` rather than CALLS-only.** This is a deliberate semantic widening from the v1 `callers_of`/`outbound_calls` tools, which only returned in-process Java method calls. The widened set treats cross-service HTTP calls and async (Kafka/RabbitMQ) calls as first-class call edges from the developer's perspective — when someone asks "who calls X" they almost always mean "all callers, regardless of transport." Narrow variants (`/callers-direct`, `/callees-direct`) for CALLS-only are deferred to §8 out-of-scope.

## §11 Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Skills drift from MCP behaviour | Lockstep update rule (decision #10) + static MCP-call validator from PR-S-3 (decision #12) catches stale tool/kind/direction/edge_types values |
| Compile produces stale output (someone edits `.claude/skills/` directly instead of source) | `compile.py` runs in CI; output files are checked in but with a `# AUTOGENERATED` banner; consistency check fails if banner missing |
| Tier 2 workflow skills hallucinate when chain depth blows up | Recursion depth is fixed in each skill body; `/impact-of` and `/trace-request-flow` cap at depth 2 and depth 4 respectively. Beyond that, skill says "use raw MCP" |
| Slash-name collisions across project- and user-scoped installs behave unpredictably | Verified once during PR-S-2 review via the manual collision probe (see PR-S-2 acceptance criterion); result documented in `agent-skills/README.md`. We do not ship an automated test because we don't host-mock the agent runtime |
| Weak models pick the wrong skill | Each skill's `description` includes specific trigger words ("when the user asks 'who calls X' or 'callers of X'") to help auto-discovery; verified empirically on Qwen Code |
| Semantic widening of `/callers`/`/callees` (CALLS+HTTP+ASYNC vs v1 CALLS-only) confuses users expecting v1 behaviour | Decision #13 documents the widening explicitly; AGENT-GUIDE.md PR-S-5 rewrite calls it out; narrow variants `/callers-direct`/`/callees-direct` are an explicit out-of-scope item (§8) so the door is open if real usage demands them |
| Maintenance cost (13 skills × MCP changes) | Lockstep + propose §10 decision #10 — every MCP-touching PR includes the relevant skill updates. CI enforces via the static validator (decision #12) |
| Adding a new host (Cursor, VS Code) requires N×duplication | Solved by single-source design — add a new compile target, not new content |

## Appendix A — Concrete artefacts

### A.1 SKILL.md format compatibility (verified May 2026)

Both **Claude Code** and **Qwen Code** accept this exact frontmatter shape:

```yaml
---
name: <slug>
description: <what + when>
---
```

Sources:
- Claude Code: [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills) — "Every skill needs a `SKILL.md` file with two parts: YAML frontmatter between `---` markers... and markdown content"
- Qwen Code: [qwenlm.github.io/qwen-code-docs/en/users/features/skills](https://qwenlm.github.io/qwen-code-docs/en/users/features/skills/) — "Each Skill consists of a `SKILL.md` file with instructions... `name` is a non-empty string... `description` is a non-empty string"

Both hosts also merge `commands/` with `skills/` namespace (Claude Code: *"Custom commands have been merged into skills. A file at `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md` both create `/deploy`"*).

Optional fields the source can use **iff both hosts accept them**:
- `paths:` (Qwen Code: gate skill on file globs) — Claude Code may ignore; non-fatal.
- `disable-model-invocation:` (Claude Code) — Qwen Code may ignore; non-fatal.

Decision: source uses only `name` + `description`. Host-specific fields can be added in compile-time post-processing if needed.

### A.2 Layer diagram (canonical, copy verbatim into README §1)

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /show-callees, /find-controllers,     │
│   /impact-of, /list-routes-by-service                        │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: skills shipped as SKILL.md files in        │
│   .claude/skills/ and .qwen/skills/ (same format, same       │
│   source). Each skill expands to a deterministic chain of    │
│   MCP calls + light post-processing.                         │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — Composable primitives (the MCP API)                │
│   search, find, describe, neighbors                          │
│   ─────────────────────────────────────────────────────────  │
│   This is what shipped in MCP API v2. It stays at 4.         │
├──────────────────────────────────────────────────────────────┤
│ Layer 1 — Storage primitives                                 │
│   Kuzu Cypher + LanceDB tables                               │
└──────────────────────────────────────────────────────────────┘
```

### A.3 SKILL.md template

See §6 — the `/callees` example body is the canonical template. Tier 1 skills follow the same structure (Argument contract → Steps → Worked example → Out of scope). Tier 2 workflow skills add `## Stop conditions` and `## Recursion limit` sections.

## Appendix B — What changed (traceability)

### What stayed unchanged from the original draft

- The 3-layer mental model and ASCII diagram (§3 + Appendix A.2) — locked.
- 13 skills total (10 Tier 1 + 3 Tier 2) — locked.
- 5 PRs migration shape — locked.
- Single `agent-skills/` source compiling to `.claude/skills/` + `.qwen/skills/` — locked.
- Lockstep versioning with the MCP (decision #10) — locked.

### What changed and why (revision 1, 2026-05-08)

1. **§9 PR-S-2** rewritten from "empty `compile.py`" to a **minimal-working** compile.py spec (≤ 80 lines, idempotent on empty + 1-skill fixture). Reason: a stub `compile.py` plus a checked-in CLI subcommand that calls it would have shipped a contradictory contract — "the script exists and runs" vs "the script does nothing." Reviewer flagged this as a High finding. Fixed by making PR-S-2 self-contained: the script is real from day one; it just has nothing to compile until PR-S-3 lands the source files.
2. **§9 PR-S-2** gained an explicit acceptance criterion for the project-scoped-overrides-user-scoped collision behaviour, verified by a one-time manual collision probe documented in `agent-skills/README.md`. Reason: the original draft asserted this in §11 risks without verification path; reviewer Medium finding asked us to either verify or downgrade the claim. We verify once during PR-S-2 review (manual probe) and ship the result; we do not maintain an automated test.
3. **§9 PR-S-3** test summary extended with a cross-skill **static MCP-call validator** (~50 lines) that asserts every tool name, `kind`, `direction`, and `edge_types` value used in skill bodies resolves to a real v2 value. Reason: reviewer Medium finding noted that pure frontmatter+section-heading tests are too weak to catch the lockstep-update failures decision #10 promises to prevent. The validator is the cheapest enforcement that catches the realistic drift class (stale enum values) without host-mocking.
4. **§9 PR-S-4** test summary extended to cover workflow skill bodies with the same static validator (now spanning all 13 skills).
5. **§10 decision #12** rewritten to enumerate three test levels: (a) schema, (b) static MCP-call validation, (c) NOT behavior. Reason: the original "documentation-tested, not behavior-tested" wording was binary and undersold the static validation tier the reviewer asked for.
6. **§10 new decision #13** locks `/callers`/`/callees` semantics as the broader `[CALLS, HTTP_CALLS, ASYNC_CALLS]` edge set rather than v1's CALLS-only behaviour. Reason: reviewer Low finding noted this semantic widening was implicit in §5/§7 but not flagged as a deliberate change. Locking it prevents relitigation; calling out narrow variants as out-of-scope (§8) leaves the door open.
7. **§11 risks** restructured: "slash-name collisions" row reframed around the manual verification path; new row added for the `/callers`/`/callees` semantic widening; "skills drift from MCP" row updated to credit the static validator from decision #12.
8. **§8 out-of-scope** added a row for narrow `/callers-direct` / `/callees-direct` variants — corresponds to decision #13's deferred narrow versions.
