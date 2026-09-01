# jrag prime — replace skill/agent artifacts with a SessionStart priming hook

**Status:** in_progress

## Context

Issue #464: bench condition D (jrag full) over-explores — the agent graph-walks
(`microservices → http-routes → flow → inspect → callees → …`, 14 calls on
`bc-sem-01_D`) where condition A reads-and-answers with grep. Answer quality is
equal when D finishes; the failure mode is procedure, not tool quality.

Hypothesis (this spec): the shipped teaching artifacts — two skills and two
subagents (~45 KB of decision frameworks, workflow patterns, recovery
playbooks, all "MUST BE USED PROACTIVELY") — teach agents an elaborate
exploration procedure and thereby bias them toward over-exploration. Modern
LLMs need only know that the tool exists, what it can do, and what state it is
in; the CLI already self-documents (`--help` prints commands, flags, enum
values) and the MCP surface carries just-in-time hints
(`agent_next_actions`, `hints_structured`).

Nuance the bench makes explicit: condition D does not use the skill — its own
prompt enumerates verbs and still over-walks. So the testable claim is "less
teaching surface → less over-exploration," which the bench can measure
directly. The distinction this design draws is **capability description vs
procedure teaching**: prime describes what jrag is and what it can do; it does
not teach how to explore.

## Goal

Replace the four shipped skill/agent artifacts with `jrag prime` — a compact,
state-derived orientation payload injected via a SessionStart hook on the CLI
surface (beads `bd prime --hook-json` model). Validate the hypothesis in the
bench (#464 slice) *before* the breaking removal ships.

## Non-goals

- Changing the MCP surface's tools, descriptions, or hints.
- Any coaching in the prime payload — no decision tables, workflow patterns,
  escalation rules, recovery playbooks, or "stop early" instruction.
- Managed sections in AGENTS.md/CLAUDE.md (no beads-style pointer section).
- Renaming or touching on-disk `.java-codebase-rag*` state or env vars.

## Design

### `jrag prime` command

New read-only subcommand (registered in `jrag.py` alongside `status`). Default
output: bare markdown. `--hook-json`: the same markdown wrapped in the Claude
Code SessionStart envelope (`hookSpecificOutput.hookEventName: "SessionStart"`,
payload in `additionalContext`; qwen-code consumes the same shape).

Payload contract — navigation framing, four parts:

1. **Identity.** `jrag` is a map, not an oracle — a CLI over a prebuilt
   structural index of this Java/Kotlin repo that resolves names to files,
   walks who-calls-whom and dependency edges, and surfaces entry points and
   service boundaries. Closing line of the identity paragraph: "You are the
   explorer; jrag is the map."
2. **Command surface — embedded from the real `--help` output.** Two blocks,
   verbatim from `jrag --help`: the `Commands by group` block (with `routes,
   clients` normalized to the real command names `http-routes`, `http-clients`)
   and the per-command one-line reference. Excluded: the argparse usage dump,
   the `{...}` positional dump, the options block, and the operator-commands
   epilogue (`init`, `install`, `update`, `reprocess`, `erase`, `meta`,
   `tables`, `diagnose-ignore`). Embedding the real help keeps the payload
   from drifting as the surface evolves; the descriptions are capability
   information an agent needs to one-shot the right verb. Standalone closing
   line: run `jrag <command> --help` for flags.
3. **One trust rule.** If jrag and the files disagree, trust the files — the
   index may lag the working tree.
4. **Live state.** Freshness (fresh / stale with changed-file count, last
   increment age), service count and names (truncated), symbol/route/client/
   producer counts, watch daemon running or not.

Parts 1–3 are a static template (module constant in the source tree, not
`install_data`), with the embedded help blocks regenerated in lockstep with
the help text; part 4 is computed.

Canonical template (selected from five parallel candidate drafts; `{…}` are
computed slots):

```markdown
`jrag` is a CLI over a prebuilt structural index of this Java/Kotlin repo — a map, not an oracle.
It resolves names to files, walks who-calls-whom and dependency edges, and surfaces entry points
and service boundaries — structure you'd otherwise grep for. You are the explorer; jrag is the map.

**Trust rule:** if jrag and the files disagree, trust the files — the index may lag the working tree.

**Index state**

- Index {freshness} (incremented {age} ago) · watch daemon {running|not running}
- {n} services ({names, truncated}) · {n} symbols
- {n} routes · {n} clients · {n} producers

**Commands by group** (from `jrag --help`)

    health:      status
    locate:      find, inspect
    listings:    http-routes, http-clients, producers, topics, jobs, listeners, entities
    traversal:   callers, callees, hierarchy, implementations, subclasses,
                 overrides, overridden-by, dependents, impact, decompose,
                 flow, dependencies, connection, outline, imports
    orientation: microservices, map, conventions, overview
    search:      search

**Command reference**

- `status` — Print index freshness, ontology version, and counts.
- `find` — Find nodes by query or filter.
- `inspect` — Inspect a node by query.
- `http-routes` — List HTTP routes.
- `http-clients` — List HTTP clients.
- `producers` — List async message producers.
- `topics` — List message topics (producer-grouped).
- `jobs` — List scheduled tasks.
- `listeners` — List message listeners.
- `entities` — List JPA entities.
- `callers` — Who calls this symbol or route?
- `callees` — What does this symbol call?
- `hierarchy` — Type hierarchy (parents and children).
- `implementations` — Classes implementing an interface.
- `subclasses` — Classes extending a type.
- `overrides` — Methods this method overrides (dispatch UP to declaration).
- `overridden-by` — Methods overriding this one (dispatch DOWN to overriders).
- `dependents` — Who injects this type?
- `impact` — Fleet-wide blast radius (INJECTS/IMPLEMENTS/EXTENDS reverse closure).
- `decompose` — Role-waterfall flow from an entrypoint.
- `flow` — Request flow through a route (inbound callers + outbound CALLS hops).
- `dependencies` — Types this Symbol injects (INJECTS out).
- `connection` — Cross-service connections for a microservice (inbound/outbound).
- `outline` — List symbols declared in a file.
- `imports` — List imports declared in a file (tree-sitter parse + resolve_v2).
- `microservices` — List microservices with resolved type counts.
- `map` — Symbol counts per kind, grouped by service or module.
- `conventions` — Dominant roles + framework tallies.
- `overview` — Bundle for a microservice, route, or topic.
- `search` — Semantic search over Lance tables.
- `vocab-index` — Rebuild the vocabulary index (absence diagnosis).
- `watch` — keep the index fresh and serve warm queries while running

Run `jrag <command> --help` for flags.
```

States and degradation:

| State | stdout | exit |
|---|---|---|
| Indexed (fresh or stale) | full payload | 0 |
| No project YAML / no index discovered | nothing | 0 |
| Internal error (unreadable meta, corrupt YAML) | nothing; one stderr line | 0 |

Silence when unindexed is what makes a user-scope hook tolerable — prime fires
in every session of every repo and must never nag repos it does not index.
Prime is hook-safe by construction: every soft state degrades to empty output.

Latency constraint: SessionStart fires on start, resume, and after compaction.
The freshness walk is filesystem metadata only — project-root discovery,
index-dir mtimes, a bounded `.java`/`.kt` mtime count (`prime.py`
`_staleness_since`), the watch daemon state file (`watch/paths.py`). Prime
does open the Ladybug graph, dependency-light: `graph/ladybug_queries.py`
imports neither LanceDB nor sentence-transformers, and the open buys exactly
two reads — `graph.meta()` and `graph.microservice_counts()` (the service
bullets). What is forbidden is the vector stack — torch,
sentence_transformers, lancedb, pyarrow, cocoindex — pinned by the import-set
test in `tests/package/test_jrag_prime.py`. The budget is unchanged by that
concession; coverage counts that would require a store beyond those two reads
are dropped from the payload rather than paid for.

### Surfaces

- **CLI surface** (`--surface cli`): prime + SessionStart hook is the only
  discovery mechanism. No skill, no subagent.
- **MCP surface** (`--surface mcp`): MCP server entry as today, tools only.
  The tool list self-announces; no prime, no artifacts.

### Installer / wizard

- `jrag install --surface cli` writes a SessionStart hook
  (`jrag prime --hook-json`) into each selected host's settings: claude-code →
  `.claude/settings.json` (project) / `~/.claude/settings.json` (user);
  qwen-code / gigacode → their `HostConfig` settings paths if they support
  SessionStart hooks — otherwise warn and skip (manual wiring documented in
  JRAG-CLI.md). Merge follows the `merge_mcp_config` pattern: idempotent,
  keyed on the command, write only on change, never touch unrelated hooks.
- `jrag update`: refreshes the hook's command path; removes all four
  previously deployed artifact files wherever they exist in the scope
  (existing per-file removal + directory-cleanup machinery in `installer.py`),
  covering upgrades from any 0.12.x; handles surface switching both directions
  (hook ⇄ MCP entry). The install marker records hook presence via its
  existing per-host `surface` field (`mcp`|`cli`) — no new record shape.
- `INSTALL_TARGETS`, `install_data` packaging, and `--surface` help text
  updated to the hook model.

### Artifact removal (Phase B)

Deleted from the repo: `skills/explore-codebase/`, `skills/explore-codebase-cli/`,
`skills/README.md`, `agents/explorer-rag-enhanced.md`, `agents/explorer-rag-cli.md`.

### Bench revision (Phase A — runs first)

- `bench/prompts/D_jrag_full.md` keeps the `_shared_skeleton.md` structure;
  its hand-written tool enumeration is replaced by the real prime output,
  generated at run time by the bench harness — the bench tests the shipped
  artifact, not a drifting copy.
- Slice per #464: glm-4.7, seed 0, bank-chat; conditions A + revised-D, plus
  old-D for the before/after delta. Recorded in `bench/PREREGISTRATION.md`
  before the run.
- Gate: revised-D cap rate on call-trace + semantic ≤ A's (~≤2); C1/C2 improve
  without regressing blast-radius.

### Docs

- `docs/JRAG-CLI.md`: prime (payload states, `--hook-json`, silence rule);
  install flows rewritten (cli = hook, mcp = entry, neither deploys files);
  exit-code table gains prime.
- `docs/AGENT-GUIDE.md`: repositioned as a human reference for the MCP surface
  and hook-less hosts; the "copy-paste into AGENTS.md/CLAUDE.md" mandate is
  removed.
- `docs/DESIGN.md` / `docs/ARCHITECTURE.md`: surfaces sections move from
  skill/agent artifacts to prime + hook.
- Repo `CLAUDE.md` "Shipped artifacts" section and `README.md` claims updated.
- `docs/MIGRATION.md`, `docs/CONFIGURATION.md`: unchanged.

## Testing

- prime: golden payloads (fresh / stale / unindexed-silent / daemon on-off);
  `--hook-json` envelope schema validity; an import-set guard proving the
  prime path pulls no vector-stack modules — torch, sentence_transformers,
  lancedb, pyarrow, cocoindex (protects the latency budget); empty stdout +
  exit 0 on soft states.
- installer: hook merge idempotency (run twice → one entry), unrelated hooks
  preserved, unparseable settings → warn and skip without writing; `update`
  removes all four artifact files from a fixture mimicking a 0.12.x
  deployment; surface switch both directions.
- bench: the Phase A slice per the preregistration.
- Full suite once at the end (project rules; editable install enforced by
  `tests/conftest.py`).

## Rollout

The Phase A gate failed. P1 (the gate): revised-D capped 1/3 on call-trace +
semantic vs A's 0/3. P2 directional fail: D mean tool calls 6.8 → 8.0, caps
2/20 → 3/20. P3 held: blast-radius 0/2 caps, 7.0 = 7.0. Runs live in
`bench/results/20260831T230727` (baseline) / `20260831T231733` (revised);
prereg amendment 2026-08-31 in `bench/PREREGISTRATION.md`. Per that
pre-committed rule, the branch ships prime + hook wiring + artifact removal
anyway and 0.13.0 must not be tagged. Whether to defer the removal per this
spec's original contingency is the PR review's call. If a release does happen,
notes carry the breaking-change line (skills/agents removed; `jrag update`
cleans up deployed copies) and the dual PyPI publish (`jrag-cli` +
`java-codebase-rag`, same version) per the publish-pip skill.

## Open Questions

1. Do qwen-code / gigacode support SessionStart hooks, and in what settings
   shape? Verify in Phase B; unsupported hosts warn and skip.
2. Which coverage counts are obtainable from metadata alone vs requiring a
   store open? Resolved during implementation by the drop-don't-pay rule.
3. Is the freshness computation in `_cmd_status` reusable as-is, or does it
   need extraction into a shared helper? Implementation detail, resolved in
   Phase A.

## TLDR

Remove all four shipped skill/agent artifacts (teaching causes over-exploration,
#464); replace with `jrag prime --hook-json` — a navigation-framed orientation
(~55 lines / ~500 tokens: what jrag is, the real `--help` command surface
embedded verbatim, trust-the-files, live index state) injected by a SessionStart
hook wired through the install wizard. CLI surface
only; MCP tools self-announce. Bench Phase A rewrites the D prompt to
runtime-generated prime output and runs the #464 slice; Phase B (removal +
hook wiring, one 0.13.0 release) proceeds only if revised-D caps drop to ≤ A's
(gate subsequently FAILED — see Rollout; merge decision deferred to PR review).
