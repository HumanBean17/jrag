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
> Calibrated against ontology version **12** (see `ast_java.ONTOLOGY_VERSION` /
> `java_ontology.py` valid sets): HTTP brownfield rename (`@CodebaseHttpClient`),
> shared `CodebaseHttpMethod` enum, inbound layer-C HTTP routes replace same-method
> built-in rows. **Design rationale:** navigation surface and tools —
> [`propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md`](../propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md);
> HTTP brownfield rename, `CodebaseHttpMethod`, and exclusivity —
> [`propose/HTTP-ROUTE-METHOD-ENUM-PROPOSE.md`](../propose/HTTP-ROUTE-METHOD-ENUM-PROPOSE.md).

---

<!-- BEGIN java-codebase-rag MCP guide -->

## java-codebase-rag MCP — agent operating manual

This MCP indexes Java enterprise projects into two stores:

- **LanceDB** — vector + optional hybrid (FTS + vector) search over Java / SQL / YAML chunks.
- **Kuzu graph** — exact structure: **node kinds** `Symbol`, `Route`, `Client` and **nine edge types** (see *Edge taxonomy* below).

**MCP surface (navigation only):** `search`, `find`, `describe`, `neighbors`.

**Operator / diagnostics (not MCP):** use the **`java-codebase-rag`** CLI — lifecycle (`init`, `increment`, `reprocess`, `erase`) plus `meta`, `tables`, `diagnose-ignore`, `analyze-pr`. Rebuilds are slow; the coding agent should not pretend it can reindex via MCP. For lifecycle commands, subprocess progress is written to **stderr** (use **`--quiet`** to suppress it); **stdout** is only the structured result payload.

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

### Brownfield HTTP annotations (exclusivity)

When a method carries **`@CodebaseHttpRoute`** or **`@CodebaseHttpClient`** (including plural containers), the extractor treats that annotation as the **only** source of truth for the facets it declares (`path`, HTTP verb, `targetService`, `clientKind`, etc.). Framework annotations on the **same** method that would normally drive route or client inference—Spring MVC/WebFlux mapping annotations, **`@FeignClient`**-scoped method mappings, JAX-RS verb annotations, and the like—are **bypassed** for that axis. Do not assume the graph “merges” brownfield with the framework row; for inbound HTTP, layer-C brownfield routes **replace** same-method built-in Spring rows in the graph.

**Observability:** If brownfield and shadowable framework annotations **co-exist** on a method, a **verbose** graph build emits a structured stderr line with **`event=brownfield-exclusivity-shadowing`** (severity INFO), listing which framework annotation simple names were skipped. Typical operator invocation: `.venv/bin/python build_ast_graph.py --source-root … --kuzu-path … --verbose`. Non-verbose builds may omit this traffic.

**UC10 (silent disagreement):** The brownfield annotation wins even when its HTTP verb or path disagrees with what Spring or Feign shows on the method (for example Feign **`@GetMapping`** vs brownfield **`CodebaseHttpMethod.POST`**). There is **no** merge-time warning for that mismatch—wrong assumptions surface at runtime (for example HTTP 405) or through code review. When auditing, prefer the indexed brownfield row and, if needed, the verbose shadowing log over the framework-only reading.

**Workflow (GPS model):**

1. **Locate** — `search` (natural language / fragment) or `find` (structured `NodeFilter`).
2. **Inspect** — `describe(id)` to see the full record and `edge_summary` (per stored edge label `in`/`out` counts, plus optional composed dot-keys for type Symbols — see `describe` below).
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

- **Purpose:** Full node payload + `edge_summary`: `in` / `out` counts **per stored graph edge label** (what exists as edges in Kuzu). For **type** Symbols only (`class`, `interface`, `enum`, `record`, `annotation`), the same map may also include **describe-time composed** dot-keys — summaries of member edges, not stored labels — see the next bullets (`DECLARES.DECLARES_CLIENT`, `DECLARES.EXPOSES`); those keys are **not** valid in `neighbors(edge_types=…)`. For **method** Symbols, the map may include **override-axis** virtual keys (`OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.EXPOSES`, `OVERRIDES`); see **Override-axis keys (method Symbols)** below — also not `EdgeType` literals.
- **Args:** `id` (symbol, route, or client id).

**Composed `edge_summary` keys (type Symbols).** Keys use dot notation: `<parent_relation>.<projected_relation>`. Two are emitted today:

- `DECLARES.DECLARES_CLIENT` — the type's methods declare brownfield HTTP clients (count is the number of `Client` rows reached through `DECLARES → DECLARES_CLIENT`). To enumerate them: `neighbors(ids=<class_id>, direction="out", edge_types=["DECLARES"])` → for each method id, `neighbors(ids=<method_id>, direction="out", edge_types=["DECLARES_CLIENT"])`.
- `DECLARES.EXPOSES` — the type's methods expose routes. Same walk shape with `EXPOSES`.

Composed keys are **read-only**: they cannot be passed to `neighbors(edge_types=…)` (the dot is not a valid `EdgeType` literal — the call fails with a Pydantic `ValidationError`). Use them as a hop affordance only.

Note on counting semantics: composed counts measure **edge rows**, not distinct member methods. One method that declares multiple `Client` rows (e.g. a `rest_template` method with several call sites) contributes its full edge count to `DECLARES.DECLARES_CLIENT`. The "does this class have any clients?" predicate is answered by `count > 0`; the count itself is an affordance for how rich the downstream walk will be.

**Override-axis keys (method Symbols).** These name dispatch-axis virtual relations (computed at describe-time from `IMPLEMENTS` / `EXTENDS` plus matching `Symbol.signature`; not stored edges):

- `OVERRIDDEN_BY` — on declarations reachable from implementing / extending classes in one hop: count of **distinct** concrete override methods with the same `signature` string as the described method (not counting the declaration itself).
- `OVERRIDDEN_BY.DECLARES_CLIENT` / `OVERRIDDEN_BY.EXPOSES` — same dispatch-down walk, then count outgoing `DECLARES_CLIENT` / `EXPOSES` edges from those override methods. Counts are **edge rows** on overrides (not distinct methods): one override with multiple client edges contributes the full row count. Omitted when zero.
- `OVERRIDES` — on a concrete method: count of **distinct** upstream declarations (interface / superclass methods with the same `signature`) one `IMPLEMENTS`/`EXTENDS` hop from the declaring class. A class implementing two interfaces that both declare the same signature yields `out: 2` (two declaration symbols).

Walk recipe (declaration side): `neighbors(ids=<method_id>, direction="in", edge_types=["DECLARES"])` → declaring type → `neighbors(ids=<type_id>, direction="in", edge_types=["IMPLEMENTS","EXTENDS"])` → each subtype class → `neighbors(ids=<class_id>, direction="out", edge_types=["DECLARES"])` and filter rows where `signature` matches the interface method.

Static methods suppress the entire override-axis rollup. Constructors do not receive these keys.

These keys are **not** valid `EdgeType` literals — `neighbors(edge_types=["OVERRIDDEN_BY"])` fails at the Pydantic boundary. Use them as hop affordances only.

#### `neighbors`

- **Purpose:** One hop over explicit edge types; returns **edges** with attributes (`confidence`, `strategy`, `match`, …) and the **`other`** node.
- **Args:** `ids` (string or array — batch allowed), **`direction`** (`in`|`out`), **`edge_types`** (non-empty list), `limit`, `offset`, optional `filter` on the other node.
- **Batching:** Multiple origins are expanded; pagination slices the **combined** edge list — use larger `limit` when batching many ids.
- **Confidence:** Cross-service edges (`HTTP_CALLS`, `ASYNC_CALLS`) carry confidence, strategy, and match metadata on `edge.attrs` (`attrs.confidence`, `attrs.strategy`, `attrs.match`). Low confidence means the resolver had to guess at the route binding — treat it as a **resolver gap signal**, not a hallucination. Report low-confidence edges with their confidence value, not as facts. Intra-service edges (`CALLS`, `INJECTS`, `IMPLEMENTS`, `EXTENDS`, `DECLARES`, `DECLARES_CLIENT`, `EXPOSES`) faithfully represent the static graph; the resolved set is still a **lower bound** under reflection / dynamic dispatch (see *What this MCP is NOT*).

### Ontology glossary (version 12)

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

**Staleness rule:** after `java-codebase-rag increment`, Lance is fresh but Kuzu may be stale (see https://github.com/HumanBean17/java-codebase-rag/blob/master/propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md). A graph older than the source tree is normal mid-development. When in doubt, run `meta` and compare against your working tree.

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
