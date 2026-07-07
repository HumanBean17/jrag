---
name: explore-codebase-cli
description: "MUST BE USED PROACTIVELY. Universal read-only codebase exploration via the `jrag` CLI — one command per engineering intent (callers, callees, routes, clients, producers, impact, search, inspect, flow, overview). Use for any exploration: locating code, tracing dependencies, finding patterns, 'where is X', 'who calls Y', 'find all controllers', 'trace the flow from A to B'. Combines graph navigation with file-system search (grep, glob, file reading). Do NOT use when the answer is already in open context or for a single known file — read that file directly."
---

# /explore-codebase-cli — Universal codebase exploration via `jrag`

Read-only exploration combining **graph navigation through the `jrag` CLI** with **broad file-system search**. `jrag` loads the same index as the MCP server but exposes one shell command per intent instead of five MCP tools.

Use any time you must search, locate, navigate, or explore. **Do NOT use when** the answer is already in context or for a single known file — read it directly.

## Core Principles

1. **Read-only.** Never edit, write, or modify any file.
2. **Names in, names out.** Every `<query>` is human-readable (FQN / simple name / route path / topic). Raw node IDs never required.
3. **One command per intent.** `jrag` collapses resolve + walk into one call — don't chain resolve→describe→neighbors manually.
4. **Stop when answered.** Don't prefetch unrelated subgraphs or directories.

**One surface per project.** This is the CLI surface; the MCP surface (`search`/`find`/`describe`/`neighbors`/`resolve`) is mutually exclusive — running both strands the agent in two vocabularies.

## Prerequisite: index must exist

`jrag` is a thin layer over the existing index. If unindexed, every command exits 2:

```
status: error
message: No index at <path>. Run: java-codebase-rag init --source-root <root>
```

Verify with `jrag status` when in doubt.

## Tool Inventory

### `jrag` command groups

Run `jrag --help` for the canonical list.

| Group | Commands |
| --- | --- |
| **Orientation** | `status`, `microservices`, `map`, `conventions`, `overview` |
| **Locate** | `find`, `search` |
| **Listings** | `http-routes`, `http-clients`, `producers`, `topics`, `jobs`, `listeners`, `entities` |
| **Traversal** | `callers`, `callees`, `hierarchy`, `implementations`, `subclasses`, `overrides`, `overridden-by`, `dependents`, `impact`, `flow`, `decompose`, `dependencies`, `connection` |
| **Inspection** | `inspect`, `outline`, `imports` |

### Common flags

```
--service <name>            Filter by microservice
--module <name>             Filter by module
--limit <N>                 Cap on results (default 20; 10 for fan-out)
--format text|json          Output format (default: text)
--detail brief|normal|full  How much of each node/edge is shown (default: normal);
                            orthogonal to --format. brief=name @service;
                            normal=+module/role/file/score; full=+signature/
                            annotations/snippet. inspect + orientation default to full.
--index-dir <path>          Index directory override (default: discovered from cwd)
```

`--offset` is supported **only** on `find` and `search`. Other commands emit `truncated: more results — narrow your query` when capped.

### File-system tools

`Grep` (content/regex), `Glob` (name/path patterns), `Read` (`offset`/`limit`). Plus `Bash` (read-only: `git log`, `git blame`, `ls`, `find`), `WebSearch`/`WebFetch`.

---

## Decision Framework

| User asks… | First `jrag` command | Follow-up |
| ---------- | -------------------- | --------- |
| "Is the index fresh?" | `jrag status` | — |
| Identifier (FQN / simple name) | `jrag inspect <query>` | `callers` / `callees` |
| Fuzzy / NL "where is X" | `jrag search "<text>"` | `inspect <hit>` |
| All controllers in S | `jrag find --role CONTROLLER --service S` | `callees` |
| Interfaces in S | `jrag find --java-kind interface --service S` | `implementations` |
| HTTP / messaging entry points | `jrag http-routes [--framework …] [--method …]` | `inspect <route>` |
| Outbound HTTP clients | `jrag http-clients [--calls-service …]` | `callees <client>` |
| Outbound async producers | `jrag producers [--topic-contains …]` | `callees <producer>` |
| Topics + consumers/producers | `jrag topics [--topic-contains …]` | — |
| Who calls / what does M call? | `jrag callers <M>` / `jrag callees <M>` | `inspect` |
| What routes does a controller expose? | `jrag callers <controller>` | `inspect` (`DECLARES.EXPOSES`) |
| Who hits this route? | `jrag callers <route>` | — |
| Implementations / subtypes of T? | `jrag implementations <T>` / `jrag subclasses <T>` | — |
| Overriding / overridden methods? | `jrag overrides <method>` (UP) / `jrag overridden-by <method>` | — |
| Who injects / depends on T? | `jrag dependencies <T>` / `jrag dependents <T>` | — |
| Blast-radius of changing X? | `jrag impact <X>` (bounded fan-in) | `Grep` fallback |
| Trace request flow A→B | `jrag flow <route>` | `connection <A> <B>` |
| File outline / imports | `jrag outline <file>` / `jrag imports <file>` | `inspect <row>` |
| "Explain service S" | `jrag overview <service>` | `http-routes`/`http-clients`/`producers` |
| "Explain route /topic" | `jrag overview <subject>` | `flow` |
| Find files / text | `Glob` / `Grep` | `Read` |
| Who changed X and when? | Bash: `git log`/`git blame` | — |
| "How is this configured?" | `Glob` + `Grep`; `jrag search "<key>" --table yaml` | `Read` sections |

**Escalation:** ① Most targeted command first → ② fall back gracefully (`callers` empty → `Grep`) → ③ cross-validate (CLI vs file disagree → **trust the file** — index may be stale).

**Rules of thumb:** structure beats vector for exact questions (`find`/`inspect` + traversal); vector beats structure for fuzzy discovery (`search`); file-system beats stale index.

---

## Resolve-first contract (every `<query>` command)

Every `jrag` command that takes a `<query>` runs `resolve_v2` internally:

| `resolve_v2` status | `jrag` behavior |
| --- | --- |
| `one` | Run the traversal/listing against the resolved node. |
| `many` | Return the candidate list and stop. **No auto-pick.** Disambiguate with `--kind`/`--role`/`--fqn-contains`/`--service`; re-run. |
| `none` | `status: not_found` envelope (exit 0). Fall back to `search` or `Grep`. |

Never look up a raw node ID — pass an FQN, simple name, prior `sym:`/`route:`/`client:`/`producer:` id, route path, or topic. Only `--kind` is a true resolve input; `--role`/`--java-kind`/`--fqn-contains` post-filter client-side, while `--service`/`--module` are resolve-time filters on `inspect`/`callers` and result filters elsewhere.

## Output envelope

`--format` (text|json) picks the representation; `--detail` (brief|normal|full) picks how much of each node/edge shows — **both honor the same detail level**. Default: `text` + `normal`. `inspect` and orientation commands default to `full`. `--format json` emits the projected envelope (empty fields dropped):

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

`truncated` is computed via +1-fetch on `find`/`search` (use `--limit`, then `--offset`); other commands emit the `more results` message when capped. `agent_next_actions` (≤5) maps result edges to next commands — a starting point, not a directive. `file_location` populates only on `one`-hit resolve.

## Traversal direction reference

`jrag` abstracts away `direction`/`edge_types` — you name the intent, it picks the edges:

| Intent (command) | Underlying edges |
| --- | --- |
| `callers` / `callees` | `CALLS` in / out. On a controller/entry-point type, `callers` also folds in the routes its methods `EXPOSE` (they are the inbound callers) — use it to list a controller's endpoints. `decompose` defaults to `--follow-calls`; `--per-stage-limit` caps symbols per stage (not stage count) |
| `hierarchy` | `EXTENDS` + `IMPLEMENTS`, both directions (parents + children) |
| `implementations` / `subclasses` | `IMPLEMENTS` / `EXTENDS` in |
| `overrides` / `overridden-by` | `OVERRIDES` out (subtype→supertype) / in |
| `dependencies` / `dependents` | `INJECTS` out / in |
| `impact` | bounded fan-in: `INJECTS`/`IMPLEMENTS`/`EXTENDS` in (depth ≤2) |
| `flow <route>` | `EXPOSES`/`HTTP_CALLS`/`ASYNC_CALLS`/`CALLS` |
| `connection A B` | bounded search over the same edge set |

**Node id prefixes (from prior results):** `sym:` (Symbol), `route:`/`r:` (Route), `client:`/`c:` (Client), `producer:`/`p:` (Producer). **Symbol FQN:** `<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,…)` — generics erased, no spaces after commas, no-arg `()`, constructor `#<init>(...)`.

## Ontology glossary

**Roles:** `CONTROLLER` | `SERVICE` | `REPOSITORY` | `COMPONENT` | `CONFIG` | `ENTITY` | `CLIENT` | `MAPPER` | `DTO` | `OTHER`.
**Capabilities:** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.
**Symbol kinds:** `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`.
**Route frameworks:** `spring_mvc`/`webflux` (HTTP), `kafka`/`rabbitmq`/`jms`/`stream` (messaging), `feign` (client mirrors). Route *kinds*: `http_endpoint`, `http_consumer`, `kafka_topic`, `rabbit_queue`, `jms_destination`, `stream_binding`. **Client kinds:** `feign_method`, `rest_template`, `web_client`. **Producer kinds:** `kafka_send`, `stream_bridge_send`. **Source layers:** `builtin`, `layer_a_meta`, `layer_b_ann`, `layer_b_fqn`, `layer_c_source`.

---

## Recovery Playbook

**After two failed attempts on the same intent, stop and report command, args, and result snippet.**

| Symptom | Fix |
| ------- | --- |
| `status: error` "No index at …" | Run `java-codebase-rag init --source-root <root>`; retry |
| `status: not_found` | `jrag search "<query>"`; or `find --fqn-contains …`; fallback `Grep` |
| `many` candidates | Add `--kind`/`--role`/`--fqn-contains`/`--service`; re-run |
| `find` too broad | Add `--service`, `--fqn-contains`, `--path-contains`, `--topic-contains` |
| Empty `search` | Try `--table all`; `find --fqn-contains`; `Grep` |
| `truncated: true` | Narrow, or page with `--offset` (`find`/`search` only) |
| Empty across commands | Index missing/stale → `Grep`/`Glob`/`Read`; ask operator to rebuild (`java-codebase-rag reprocess`) |
| CLI vs file disagree | **Trust the file**; report stale index |
| `--offset` rejected | Only `find`/`search` accept it; others narrow via filters |
| Wrong node picked | Resolve ambiguous — pass `--kind` |

---

## Workflow Patterns

- **"Explain feature X":** `jrag search "X"` → pick 1–3 hits → `jrag inspect <hit>` → targeted traversal (`callees`/`implementations`) → stop when answered.
- **"Where is X used?":** `jrag inspect <X>` → `jrag callers <X>` + `jrag dependents <X>` → `Grep` fallback → report sites with file:line.
- **"Find all Y":** structural → `jrag find --role <ROLE> [--service <S>]`; textual → `Grep`; broad → `Glob`+`Grep`. Summarize, don't dump.
- **"Trace flow A→B":** `jrag flow <route-A>` → `jrag connection A B` → `Grep` gaps → report with file:line.
- **"How is this configured?":** `Glob` `**/application*.yml` → `Grep` the key → `Read` sections → `jrag search "<key>" --table yaml`.
- **"Orient in a new service":** `jrag overview <S>` → `jrag conventions --service <S>` → `jrag map --service <S>` → `jrag http-routes --service <S>`.
