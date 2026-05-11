# Agent Guide — `java-codebase-rag` MCP

> **How to use this file.** Copy the block between the `<!-- BEGIN/END
> java-codebase-rag MCP guide -->` markers below into your project's `QWEN.md`,
> `CLAUDE.md`, `AGENTS.md`, or equivalent. The block is self-contained:
> **four** MCP navigation tools, one shared **`NodeFilter`**, edge-type
> taxonomy, a forced reasoning preamble, a decision tree, a recovery
> playbook, and slash-style prompt aliases. Update by re-pulling from this
> repo when the ontology bumps.
>
> Why this exists: weak / mid models pick the wrong tool, omit required
> `neighbors` arguments, pass stringified JSON, or use vector search for
> questions the graph answers exactly. This guide keeps them on the rails.
>
> Calibrated against ontology version **11** (see `ast_java.ONTOLOGY_VERSION` /
> `java_ontology.py` valid sets). Design rationale:
> [`propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md`](../propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md).

---

<!-- BEGIN java-codebase-rag MCP guide -->

## java-codebase-rag MCP — agent operating manual

This MCP indexes Java enterprise projects into two stores:

- **LanceDB** — vector + optional hybrid (FTS + vector) search over Java / SQL / YAML chunks.
- **Kuzu graph** — exact structure: **node kinds** `Symbol`, `Route`, `Client` and **nine edge types** (see *Edge taxonomy* below).

**MCP surface (navigation only):** `search`, `find`, `describe`, `neighbors`.

**Operator / diagnostics (not MCP):** use the **`java-codebase-rag`** CLI — lifecycle (`init`, `increment`, `reprocess`, `erase`) plus `meta`, `tables`, `diagnose-ignore`, `analyze-pr`. Rebuilds are slow; the coding agent should not pretend it can reindex via MCP.

**Use this MCP when** you need whole-codebase context: who calls what, what handles a route, what a method invokes, where clients point, or fuzzy “where is concept X” entry points.

**Do NOT use this MCP when** the answer is already in the open file, or for third-party library trivia from training data alone. Prefer the smallest call that answers the question.

### What this MCP is NOT

The MCP indexes Java production code, SQL, and YAML — nothing else.
Treat the following as out of frame:

- **Test files, build files, deploy / runtime story** — read `pom.xml`,
  `build.gradle`, `Dockerfile`, `.github/workflows/`, README directly.
- **Reflection, dynamic dispatch, SPI lookups** — `CALLS` resolves
  static method calls only; the resolved caller set is a **lower bound**.
- **Unindexed services / repos** — verify with `java-codebase-rag meta`
  before treating an empty `search` result as proof of absence.
- **"When did X change", "who changed X"** — use `git log` / `git blame`.

When MCP disagrees with the open file, the file wins; report the
disagreement as evidence of staleness, not as a contradiction.

**Workflow (GPS model):**

1. **Locate** — `search` (natural language / fragment) or `find` (structured `NodeFilter`).
2. **Inspect** — `describe(id)` to see the full record and `edge_summary` (per-edge-type in/out counts).
3. **Walk** — `neighbors` in a loop with explicit **`direction`** and **`edge_types`** until you have enough evidence. Multi-hop “trace” and “impact” are **your** reasoning, not a separate tool.

### Forced reasoning preamble (every tool call)

Before every MCP tool call, output **one short line**:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors>  Why: <≤8 words>
```

Then check *Argument shapes* (real JSON arrays/objects, required `neighbors` fields). If the call returns nothing useful, do not thrash — use the **Recovery playbook**.

### Edge taxonomy (nine labels)

Use these strings **verbatim** in `neighbors(..., edge_types=[...])`:

| Group | Edge types | Semantics |
| ----- | ---------- | --------- |
| Type wiring | `EXTENDS`, `IMPLEMENTS`, `INJECTS` | `in` = who depends on this type; `out` = what this type depends on |
| Containment | `DECLARES`, `DECLARES_CLIENT` | `in` = owner; `out` = owned member / client |
| Method calls | `CALLS` | `in` = callers; `out` = callees |
| Service boundary | `EXPOSES` | Symbol → Route (handler exposes route) |
| Cross-service | `HTTP_CALLS`, `ASYNC_CALLS` | Symbol → Route across services |

Symmetric: cross-service and intra-service questions use the **same** `neighbors` call with different `edge_types`.

### Argument shapes — what the parser actually wants

#### A. JSON, not stringified JSON

Pass **real** JSON types. Never pass a string containing JSON for arrays or objects.

| Param | Right | Wrong |
| ----- | ----- | ----- |
| `edge_types` | `["CALLS"]` | `"CALLS"` or `"[\"CALLS\"]"` |
| `exclude_roles` | `["DTO","OTHER"]` | stringified array |
| `filter` | `{"role":"CONTROLLER"}` | nested string JSON |
| `ids` (batch) | `["sym:…","sym:…"]` | comma-joined string |

**Rule:** `list[str]` → `["a","b"]`. `str` → `"a"`. Omit keys you do not need; empty string `""` is often a **real filter** that matches nothing.

#### B. Node ids

- **Symbol:** prefix `sym:` (stable graph id from `search.symbol_id`, `find`, `describe`, or `neighbors.other.id`).
- **Route:** `route:` or `r:` prefix (use the exact id from `find` / `describe`).
- **Client:** `client:` or `c:` prefix.

`describe` and `neighbors` accept these ids and dispatch by prefix.

#### C. Method / type needles (for interpreting `Symbol` FQNs)

When reading or comparing symbols, method identity uses **FQN + signature**:

```
<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,<SimpleType2>,…)
```

- Simple type names in parentheses (`String`, `List`), generics erased (`List<String>` → `List`).
- No spaces after commas. No-arg: `()`. Constructor: `#<init>(...)`.

Use `search` to recover the stored symbol id / FQN if you only have a simple name.

#### D. `neighbors` — required arguments

**There are no defaults.** Every call must include:

- `direction`: `"in"` or `"out"`.
- `edge_types`: non-empty list of edge labels from the taxonomy table.

Omitting them is a validation error. This is intentional: it prevents huge accidental fan-out.

Optional `filter` applies to the **other** endpoint node (same `NodeFilter` keys as `find`; keys irrelevant to that node kind are ignored).

#### E. Shared `NodeFilter` (for `find`, `search.filter`, `neighbors.filter`)

One object shape everywhere. **For `find`, `filter` is required** — use at least one key (e.g. `{"microservice":"chat-core"}`) or `{}` is valid Pydantic but may be expensive at scale; prefer narrowing keys.

| Keys | Applies to |
| ---- | ---------- |
| `microservice`, `module`, `source_layer` | All kinds (`source_layer` mainly **client**: `builtin` / brownfield) |
| `role`, `exclude_roles`, `annotation`, `capability`, `fqn_prefix`, `symbol_kind`, `symbol_kinds` | **symbol** (ignored for route/client) |
| `http_method`, `path_prefix`, `framework` | **route** |
| `client_kind`, `target_service`, `target_path_prefix`, `client_method` | **client** |

Exact allowed values for roles, capabilities, client kinds, etc. live in `java_ontology.py`.

### Decision tree

| User asks… | First step | Typical follow-up |
| ---------- | ---------- | ----------------- |
| Fuzzy / NL “where is X” | `search` | `describe` → `neighbors` |
| All controllers in service S | `find(kind="symbol", filter={"microservice":S,"role":"CONTROLLER"})` | `neighbors` for `CALLS` / `EXPOSES` |
| List interfaces in service S | `find(kind="symbol", filter={"microservice":S,"symbol_kind":"interface"})` | `neighbors` / `describe` |
| List HTTP or Kafka entry points | `find(kind="route", filter={...})` | `describe` |
| List Feign / HTTP clients | `find(kind="client", filter={...})` | `neighbors(..., out, ["HTTP_CALLS"])` if needed |
| Who calls method M? | Resolve id via `search` or `find` | `neighbors(ids=sym_id, direction="in", edge_types=["CALLS"])` |
| What does M call? | Same | `neighbors(..., direction="out", edge_types=["CALLS"])` |
| Who hits this route? | `find(kind="route", ...)` or route id from logs | `neighbors(ids=route_id, direction="in", edge_types=["HTTP_CALLS","ASYNC_CALLS","EXPOSES"])` |
| Handler for a route | Have `route_id` | `neighbors(ids=route_id, direction="in", edge_types=["EXPOSES"])` |
| Who implements interface T? | `find` symbol for T, or `search` | `neighbors(..., direction="in", edge_types=["IMPLEMENTS"])` |
| Who injects type T? | Symbol id for T | `neighbors(..., direction="in", edge_types=["INJECTS"])` |
| Impact / “what breaks if I change X”? | **No magic tool** — you loop | `neighbors` with `in` + relevant edge types (`CALLS`, `INJECTS`, …), repeat until bounded |
| Index health / ontology / counts | Not MCP | Shell: `java-codebase-rag meta` |
| Rebuild index | Not MCP | `java-codebase-rag reprocess` (full rebuild) or `java-codebase-rag increment` (Lance catch-up; graph may stay stale until `reprocess`) |
| PR blast radius | Not MCP | `java-codebase-rag analyze-pr --diff-file …` |

**Rules of thumb:**

1. **Graph beats vector for exact structural questions** — do not `search` for “who calls `Foo#bar`” if you can use `find` + `neighbors(in, [CALLS])`.
2. **Vector beats graph for fuzzy discovery** — `search` first, then pivot to `describe` / `neighbors`.

### Tool reference — four tools

#### `search`

- **Purpose:** Locate chunk hits by NL or code fragment; use `symbol_id` when present to jump into the graph.
- **Args:** `query`, `table` (`java`|`sql`|`yaml`|`all`, default `java`), `hybrid` (bool), `limit` (default 5), `offset`, `path_contains`, optional `filter` (`NodeFilter` — post-filters hits using symbol-oriented fields on the row).
- **Tip:** For behaviour questions, narrow noise with `filter.exclude_roles` or `filter.role` when you know the shape you want.

#### `find`

- **Purpose:** List nodes of one kind matching structured filters.
- **Args:** `kind` (`symbol`|`route`|`client`), **`filter`** (object, required), `limit`, `offset`.
- **Returns:** `NodeRef` rows with `id`, `kind`, `fqn`, `microservice`, `module`, `role` (symbols only).

#### `describe`

- **Purpose:** Full node payload + `edge_summary` (counts only: per edge type, `in` / `out`).
- **Args:** `id` (symbol, route, or client id).

#### `neighbors`

- **Purpose:** One hop over explicit edge types; returns **edges** with attributes (`confidence`, `strategy`, `match`, …) and the **`other`** node.
- **Args:** `ids` (string or array — batch allowed), **`direction`** (`in`|`out`), **`edge_types`** (non-empty list), `limit`, `offset`, optional `filter` on the other node.
- **Batching:** Multiple origins are expanded; pagination slices the **combined** edge list — use larger `limit` when batching many ids.
- **Confidence:** Cross-service edges (`HTTP_CALLS`, `ASYNC_CALLS`)
  carry confidence, strategy, and match metadata on `edge.attrs`
  (`attrs.confidence`, `attrs.strategy`, `attrs.match`). Low
  confidence means the resolver had to guess at the route binding —
  treat it as a **resolver gap signal**, not a hallucination. Report
  low-confidence edges with their confidence value, not as facts.
  Intra-service edges (`CALLS`, `INJECTS`, `IMPLEMENTS`, `EXTENDS`,
  `DECLARES`, `DECLARES_CLIENT`, `EXPOSES`) faithfully represent
  the static graph; the resolved set is still a **lower bound** under
  reflection / dynamic dispatch (see *What this MCP is NOT*).

### Ontology glossary (version 11)

Source of truth: `java_ontology.py`. Strings are case-sensitive.

**Roles:** `CONTROLLER`, `SERVICE`, `REPOSITORY`, `COMPONENT`, `CONFIG`, `ENTITY`, `CLIENT`, `MAPPER`, `DTO`, `OTHER`.

**Capabilities:** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.

**Route `framework` (examples):** `spring_mvc`, `webflux`, `kafka`, `codebase_async_route`, … — see graph / README for full set.

**Client kinds (`Client` nodes):** `feign_method`, `rest_template`, `web_client` (`VALID_CLIENT_KINDS`).

**Cross-service edge client/strategy metadata** on neighbor results uses the same vocabulary as `java_ontology` (`VALID_HTTP_CALL_STRATEGIES`, `VALID_ASYNC_CALL_STRATEGIES`, `VALID_HTTP_CALL_MATCHES`).

### Recovery playbook

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `neighbors` validation error | Missing `direction` or `edge_types` | Add both explicitly |
| Empty `neighbors` | Wrong edge type for the node kind, or wrong direction | Check `describe.edge_summary`; `EXPOSES` is Symbol↔Route — direction matters |
| Cannot find symbol | Wrong id or stale index | `search` with distinctive string; verify `java-codebase-rag meta` (CLI) |
| `find` returns too much | Over-broad filter | Add `microservice`, `fqn_prefix`, `path_prefix`, etc. |
| Route not found | Path mismatch | Use `path_prefix` on `find(kind="route", …)`; check README brownfield routes |
| Need ontology / rebuild / PR analysis | Wrong layer | Use **`java-codebase-rag`** CLI, not MCP |
| Result disagrees with the open file | Index is stale (typical after `increment`-only catch-up) | Trust the file. Confirm staleness with `java-codebase-rag meta` (last `reprocess` time). Report as staleness, not contradiction. |
| Empty `search` result on a string you can read in the open file | Project not indexed, wrong `table` (try `all`), or chunking missed it | Try `find(kind=symbol, filter={"fqn_prefix": …})`. Fall back to `rg` in the project tree if still empty. |

After two failed attempts on the same intent, stop and report tool name, args, and response.

**Staleness rule:** after `java-codebase-rag increment`, Lance is fresh
but Kuzu may be stale (see `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`).
A graph older than the source tree is normal mid-development. When in
doubt, run `meta` and compare against your working tree.

### Slash-style aliases (prompt templates)

- `/nl <text>` → `search({"query":"<text>","limit":8})` then `describe` on best `symbol_id`.
- `/controllers <ms>` → `find({"kind":"symbol","filter":{"microservice":"<ms>","role":"CONTROLLER"}})`.
- `/routes <ms>` → `find({"kind":"route","filter":{"microservice":"<ms>"}})`.
- `/clients <ms>` → `find({"kind":"client","filter":{"microservice":"<ms>"},"limit":100})` — narrow with `client_kind`, `target_service`.
- `/callers <sym_id>` → `neighbors({"ids":"<sym_id>","direction":"in","edge_types":["CALLS"]})`.
- `/callees <sym_id>` → `neighbors({"ids":"<sym_id>","direction":"out","edge_types":["CALLS"]})`.
- `/handlers <route_id>` → `neighbors({"ids":"<route_id>","direction":"in","edge_types":["EXPOSES"]})`.
- `/who-hits-route <route_id>` → `neighbors({"ids":"<route_id>","direction":"in","edge_types":["HTTP_CALLS","ASYNC_CALLS","EXPOSES"]})`.
- `/implements <type_sym_id>` → `neighbors({"ids":"<type_sym_id>","direction":"in","edge_types":["IMPLEMENTS"]})`.
- `/injects <type_sym_id>` → `neighbors({"ids":"<type_sym_id>","direction":"in","edge_types":["INJECTS"]})`.
- `/health` → (CLI) `java-codebase-rag meta` — not an MCP call.

### Canonical workflow: “explain feature X”

1. `search` with a short NL query; pick 1–3 hits with strong `symbol_id` / role fit.
2. `describe` on the chosen id; read `edge_summary`.
3. Walk outward with `neighbors` using **small** `edge_types` sets (e.g. start `CALLS` out, or `EXPOSES` / cross-service edges for boundaries).
4. Stop when you can answer; do not prefetch unrelated subgraphs.

<!-- END java-codebase-rag MCP guide -->

---

## Maintenance notes (for the repo, not the agent)

- Bump the ontology version sentence when `ONTOLOGY_VERSION` changes in `ast_java.py`.
- When MCP behaviour or `NodeFilter` keys change, update this guide and `README.md` in lockstep.
- New edge types require taxonomy table + `java_ontology.py` + README “Re-index required” if schema changes.
