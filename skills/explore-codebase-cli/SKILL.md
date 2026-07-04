---
name: explore-codebase-cli
description: "MUST BE USED PROACTIVELY. Universal read-only codebase exploration via the `jrag` CLI — one command per engineering intent (callers, callees, routes, clients, producers, impact, search, inspect, flow, overview). Use for any exploration: locating code, tracing dependencies, finding patterns, 'where is X', 'who calls Y', 'find all controllers', 'trace the flow from A to B'. Combines graph navigation with file-system search (grep, glob, file reading). Do NOT use when the answer is already in open context or for a single known file — read that file directly."
---

# /explore-codebase-cli — Universal codebase exploration via `jrag`

Read-only exploration combining **graph navigation through the `jrag` CLI** with **broad file-system search**. This is the CLI surface of java-codebase-rag; it loads the same index used by the MCP server but exposes one shell command per engineering intent instead of five MCP tools.

## When to use

Any time you need to search, locate, navigate, or explore the codebase. **Do NOT use when** the answer is already in open context or for a single known file — read that file directly.

## Core Principles

1. **Read-only.** Never edit, write, or modify any file.
2. **Names in, names out.** Every `<query>` is human-readable (FQN / simple name / route path / topic). Raw node IDs are never required.
3. **One command per intent.** `jrag` collapses resolve + walk into one call. Pick the command that matches the intent; do not chain resolve→describe→neighbors manually.
4. **Stop when answered.** Don't prefetch unrelated subgraphs or directories.

## Why `jrag` (CLI) vs `java-codebase-rag` (MCP)

| Aspect | `jrag` CLI | MCP server (`java-codebase-rag-mcp`) |
| --- | --- | --- |
| Surface | Shell — one command per intent | 5 stdio MCP tools (`search` / `find` / `describe` / `neighbors` / `resolve`) |
| Resolve | **Internalized** — every `<query>` command runs `resolve_v2` first | Explicit — agent calls `resolve` then `describe` / `neighbors` |
| Output | Compact text by default; `--format json` for the envelope | JSON-RPC envelope |
| Host fit | Any agent that can run shell commands | MCP-aware hosts (Claude Code, Claude Desktop, Qwen Code, GigaCode) |
| Index | Reuses the operator's `~/.java-codebase-rag` / `.java-codebase-rag/` index | Same |

Pick **one** surface per project — running both strands the agent in two vocabularies. This skill is for the CLI surface.

## Prerequisite: index must exist

`jrag` is a thin compose-and-render layer over the existing index. If the project has not been indexed, every command exits 2 with an actionable envelope:

```
status: error
message: No index at <path>. Run: java-codebase-rag init --source-root <root>
```

Verify with `jrag status` first when in doubt.

## Tool Inventory

### `jrag` command groups

Run `jrag --help` for the canonical list. Groups (PR-JRAG-1a..4):

| Group | Commands |
| --- | --- |
| **Orientation** | `status`, `microservices`, `map`, `conventions`, `overview` |
| **Locate** | `find`, `search` |
| **Listings** | `routes`, `clients`, `producers`, `topics`, `jobs`, `listeners`, `entities` |
| **Traversal** | `callers`, `callees`, `hierarchy`, `implementations`, `subclasses`, `overrides`, `overridden-by`, `dependents`, `impact`, `flow`, `dependencies`, `connection` |
| **Inspection** | `inspect`, `outline`, `imports` |

### Common flags (every command)

```
--service <name>      Filter by microservice
--module <name>       Filter by module
--limit <N>           Cap on results (default 20; 10 for fan-out commands)
--format text|json    Output format (default: text)
--index-dir <path>    Index directory override (default: discovered from cwd)
```

`--offset` is supported **only** on `find` and `search` (they route through `find_v2` / `search_v2` which accept it). Other commands emit `truncated: more results — narrow your query` when capped.

### File-system tools

- **Grep** — content search by pattern/regex
- **Glob** — find files by name/path pattern (`**/*.java`, `**/*Controller*.java`, `**/application*.yml`)
- **Read** — read files (`offset`/`limit` for large files)

### Other: **Bash** (read-only: `git log`, `git blame`, `ls`, `find`), **WebSearch**/**WebFetch** (external lookups)

---

## Decision Framework

| User asks… | First `jrag` command | Follow-up |
| ---------- | -------------------- | --------- |
| "Is the index fresh?" | `jrag status` | — |
| Identifier-shaped string (FQN / simple name) | `jrag inspect <query>` | `callers` / `callees` |
| Fuzzy / NL "where is X" | `jrag search "<text>"` | `inspect <hit>` |
| All controllers in service S | `jrag find --role CONTROLLER --service S` | `callees` |
| Interfaces in service S | `jrag find --java-kind interface --service S` | `implementations` |
| HTTP / messaging entry points | `jrag routes [--framework …] [--method …]` | `inspect <route>` |
| Outbound HTTP clients | `jrag clients [--calls-service …]` | `callees <client>` |
| Outbound async producers | `jrag producers [--topic-prefix …]` | `callees <producer>` |
| Topics + consumers/producers | `jrag topics [--topic-prefix …]` | — |
| Who calls method M? | `jrag callers <M>` | `inspect <caller>` |
| What does M call? | `jrag callees <M>` | `inspect <callee>` |
| Who hits this route? | `jrag callers <route>` | — |
| Who implements interface T? | `jrag implementations <T>` | — |
| Subtypes of class C? | `jrag subclasses <C>` | — |
| Overriding methods? | `jrag overrides <method>` (dispatch UP) | — |
| Methods that override me? | `jrag overridden-by <method>` | — |
| Who injects T? | `jrag dependencies <T>` | — |
| Who depends on T? | `jrag dependents <T>` | — |
| Blast-radius of changing X? | `jrag impact <X>` (bounded fan-in) | `Grep` fallback |
| Trace request flow A→B | `jrag flow <route>` | `connection <A> <B>` |
| File outline | `jrag outline <file>` | `inspect <row>` |
| File imports | `jrag imports <file>` | — |
| "Explain service S" | `jrag overview <service>` | `routes` / `clients` / `producers` |
| "Explain route /topic" | `jrag overview <subject>` | `flow` |
| Find files matching pattern | `Glob` | `Read` |
| Search for text in files | `Grep` | `Read` |
| Who changed X and when? | Bash: `git log`/`git blame` | — |
| "How is this configured?" | `Glob` + `Grep` for config keys; `jrag search "<key>" --table yaml` | `Read` sections |

**Escalation:** ① Most targeted command first → ② Fall back gracefully (`callers` empty → `Grep`) → ③ Cross-validate (CLI vs file disagree → **trust the file** — index may be stale).

**Rules of thumb:** Structure beats vector for exact questions (`find` / `inspect` + traversal); vector beats structure for fuzzy discovery (`search`); file-system beats stale index.

---

## Resolve-first contract (every `<query>` command)

Every `jrag` command that takes a `<query>` runs `resolve_v2` internally and maps the contract onto the envelope:

| `resolve_v2` status | `jrag` behavior |
| --- | --- |
| `one` | Run the traversal/listing against the resolved node. |
| `many` | Return the candidate list and stop. **No auto-pick.** Disambiguate with `--kind`, `--role`, `--fqn-prefix`, etc. |
| `none` | Emit `status: not_found` envelope (exit 2). Fall back to `search` or `Grep`. |

You never need to look up a raw node ID. Pass an FQN, simple name, `sym:`/`route:`/`client:`/`producer:` id (from a prior call), route path, topic, etc.

### Disambiguation flags

Only `--kind` is a true resolve input (`hint_kind`). The other narrowing flags (`--role`, `--java-kind`, `--fqn-prefix`, `--service`, `--module`) post-filter the resolve result client-side. If a post-filter collapses `many` → `one`, the command proceeds; if it still leaves `many`, the narrowed candidates are returned.

---

## Output envelope

Default is compact text. `--format json` emits the envelope verbatim:

```json
{
  "status": "ok|not_found|error",
  "nodes": {"<id>": {...}},
  "edges": [{...}],
  "candidates": [{...}],
  "truncated": false,
  "agent_next_actions": ["jrag callers <id>", "..."],
  "file_location": {"filename": "...", "start_line": 123}
}
```

- `truncated` is computed via +1-fetch on `find`/`search` (pass `--limit`, observe `truncated`, narrow or page with `--offset`); other commands emit `truncated: more results — narrow your query` when capped (no `--offset`).
- `agent_next_actions` is a CLI-native hint list (≤5) mapping the current result's edge labels to the next `jrag` command — use it as a starting point, not a directive.
- `file_location` is populated only on `one`-hit resolve (carries the resolved node's `filename` + `start_line`).

---

## Traversal direction reference

`jrag` abstracts away `direction` and `edge_types` — you name the intent, it picks the edges. For reference, the mapping is:

| Intent (command) | Underlying edges |
| --- | --- |
| `callers` | `CALLS` direction=in |
| `callees` | `CALLS` direction=out |
| `hierarchy` | `EXTENDS` + `IMPLEMENTS` direction=out |
| `implementations` | `IMPLEMENTS` direction=in |
| `subclasses` | `EXTENDS` direction=in |
| `overrides` | `OVERRIDES` direction=out (subtype → supertype) |
| `overridden-by` | `OVERRIDES` direction=in (virtual `OVERRIDDEN_BY` out) |
| `dependencies` | `INJECTS` direction=out |
| `dependents` | `INJECTS` direction=in |
| `impact` | bounded fan-in: `CALLS`/`INJECTS`/`IMPLEMENTS`/`EXTENDS` direction=in (depth ≤2) |
| `flow <route>` | `trace_request_flow`: `EXPOSES`/`HTTP_CALLS`/`ASYNC_CALLS`/`CALLS` |
| `connection A B` | bounded search over the same edge set between A and B |

### Node id prefixes (from prior results)

`sym:` (Symbol), `route:`/`r:` (Route), `client:`/`c:` (Client), `producer:`/`p:` (Producer). Pass these verbatim if you have them; otherwise use the human-readable name.

### Symbol FQN shape

`<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,<SimpleType2>,…)`. Generics erased, no spaces after commas. No-arg: `()`. Constructor: `#<init>(...)`.

---

## Ontology glossary

**Roles:** `CONTROLLER` | `SERVICE` | `REPOSITORY` | `COMPONENT` | `CONFIG` | `ENTITY` | `CLIENT` | `MAPPER` | `DTO` | `OTHER`.

**Capabilities:** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.

**Symbol kinds:** `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`.

**Route frameworks:** `spring_mvc`, `webflux`. Route *kinds*: `http_endpoint`, `http_consumer`, `kafka_topic`, `rabbit_queue`, `jms_destination`, `stream_binding`.

**Client kinds:** `feign_method`, `rest_template`, `web_client`. **Producer kinds:** `kafka_send`, `stream_bridge_send`. **Source layers (client/producer):** `builtin`, `layer_a_meta`, `layer_b_ann`, `layer_b_fqn`, `layer_c_source`.

---

## Recovery Playbook

**After two failed attempts on the same intent, stop and report command, args, and result snippet.**

| Symptom | Fix |
| ------- | --- |
| `status: error` "No index at …" | Run `java-codebase-rag init --source-root <root>` then retry |
| `status: not_found` | Try `jrag search "<query>"`; or `find --fqn-prefix …`; fallback `Grep` |
| `many` candidates returned | Add `--kind`/`--role`/`--fqn-prefix`/`--service`; re-run |
| `find` returns too much | Add `--service`, `--fqn-prefix`, `--path-prefix`, `--topic-prefix` |
| Empty `search` | Try `--table all`; `find --fqn-prefix`; `Grep` directly |
| `truncated: true` | Narrow the query, or page with `--offset` (`find`/`search` only) |
| Empty results across commands | Index missing/stale → `Grep`/`Glob`/`Read`; ask operator to rebuild (`java-codebase-rag reprocess`) |
| CLI vs file disagree | **Trust the file**; report stale index |
| `--offset` rejected | Only `find`/`search` accept it; other commands narrow via filters |
| Wrong node picked | Resolve must be ambiguous — pass `--kind` to narrow |

---

## Workflow Patterns

**"Explain feature X":** `jrag search "X"` → pick 1–3 hits → `jrag inspect <hit>` → targeted traversal (`callees`/`implementations`) → stop when answered.

**"Where is X used?":** `jrag inspect <X>` (resolves) → `jrag callers <X>` and `jrag dependents <X>` → `Grep` fallback → report all sites with file:line.

**"Find all Y":** Structural → `jrag find --role <ROLE> [--service <S>]`. Textual → `Grep`. Broad → `Glob` + `Grep`. Summarize, don't dump.

**"Trace flow from A to B":** `jrag flow <route-A>` to trace the request → `jrag connection A B` to confirm a path → `Grep` gaps → report with file:line.

**"How is this configured?":** `Glob` for `**/application*.yml` → `Grep` for the key → `Read` sections → `jrag search "<key>" --table yaml` supplement.

**"Orient in a new service":** `jrag overview <service>` (bundle) → `jrag conventions --service <service>` (dominant roles) → `jrag map --service <service>` (counts) → `jrag routes --service <service>` (entry points).
