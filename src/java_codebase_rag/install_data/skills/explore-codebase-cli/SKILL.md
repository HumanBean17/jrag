---
name: explore-codebase-cli
description: "MUST BE USED PROACTIVELY. Universal codebase exploration (CLI surface). Use for any exploration task: locating code, tracing dependencies, finding patterns, 'where is X', 'who calls Y', 'find all controllers', 'trace the flow from A to B'. Do NOT use when the answer is already in open context or for a single known file — read that file directly."
---

## Core Principles

1. **Smallest sufficient tool — both ways.** Pick the lightest tool that answers the question. Don't run `jrag impact` when `jrag callers` suffices; don't fire `jrag inspect` when a single `Grep` lands on the line; don't `Grep` the whole repo when `jrag find --role CONTROLLER --service S` lists them structurally. Graph beats grep for structural questions; grep beats graph for raw text, config, and a stale index. Neither is the default — match the tool to the question.
2. **Excerpts over dumps.** Read excerpts and relevant sections, not entire files. Summarize findings.
3. **Stop when answered.** Don't prefetch unrelated subgraphs or scan unrelated directories.

## Tool Inventory

- **Graph (`jrag` CLI):** one command per intent — `callers`, `callees`, `hierarchy`, `implementations`, `dependents`, `impact`, `flow`, `http-routes`, `http-clients`, `producers`, `topics`, `find`, `search`, `inspect`, `overview`, … Drives the same index as the MCP server. Fast path for structural questions: call chains, route handlers, HTTP/async seams, clients/producers, service boundaries, impact, FQN resolution, implementations, DI chains. Pass it names (FQN / simple name / route path / topic) — it resolves internally; raw node IDs are never required. Requires an index; if unindexed every command exits 2 (see **jrag surface**).
- **File-system:** `Grep` (content/regex), `Glob` (name/path patterns), `Read` (`offset`/`limit`). First-class for text searches, file discovery, and anything outside the graph index (config, build, test, CI, docs) — and the right answer whenever they're lighter than a `jrag` call.
- **Other:** `Bash` (read-only: `git log`, `git blame`, `ls`, `find`), `WebSearch`/`WebFetch`.

*CLI surface only — don't also drive the MCP tools (`search`/`find`/`describe`/`neighbors`/`resolve`) in the same session; the two vocabularies conflict.*

---

## Decision Framework

| User asks… | First step | Follow-up |
| ---------- | ---------- | --------- |
| "Is the index fresh?" | `jrag status` | — |
| Identifier (FQN / simple name) | `jrag inspect <query>` | `callers` / `callees` |
| Fuzzy / NL "where is X" | `jrag search "<text>"` | `inspect <hit>` |
| Raw text, a string literal, a config key | `Grep` | `Read` the hits |
| All controllers in S | `jrag find --role CONTROLLER --service S` | `callees` |
| Interfaces in S | `jrag find --java-kind interface --service S` | `implementations` |
| HTTP / messaging entry points | `jrag http-routes [--framework …] [--method …]` | `inspect <route>` |
| Outbound HTTP clients | `jrag http-clients [--calls-service …]` | `callees <client>` |
| Outbound async producers | `jrag producers [--topic-contains …]` | `callees <producer>` |
| Topics + consumers/producers | `jrag topics [--topic-contains …]` | — |
| Cross-service seams of S | `jrag connection <S> [--inbound/--outbound/--both]` | — |
| Who calls / what does M call? | `jrag callers <M>` / `jrag callees <M>` | `inspect` |
| What routes does a controller expose? | `jrag callers <controller>` (folds in its `EXPOSES` routes) | `inspect` |
| Who hits this route? | `jrag callers <route>` | — |
| Implementations / subtypes of T? | `jrag implementations <T>` / `jrag subclasses <T>` | — |
| Overriding / overridden methods? | `jrag overrides <method>` (UP) / `jrag overridden-by <method>` | — |
| Who injects / depends on T? | `jrag dependencies <T>` / `jrag dependents <T>` | — |
| Blast-radius of changing X? | `jrag impact <X>` (bounded fan-in) | `Grep` fallback |
| Trace request flow A→B | `jrag flow <route-A>` | `connection <microservice>` (service's cross-service seams) |
| File outline / imports | `jrag outline <file>` / `jrag imports <file>` | `inspect <row>` |
| Find files by name/path | `Glob` | `Read` |
| "Explain service S" | `jrag overview <service>` | `http-routes`/`http-clients`/`producers` |
| "Explain route / topic" | `jrag overview <subject>` | `flow` |
| Who changed X and when? | Bash: `git log`/`git blame` | — |
| "How is this configured?" | `Glob` + `Grep`; `jrag search "<key>" --table yaml` | `Read` sections |

**Escalation:** ① Most targeted tool first (identifier → `jrag inspect`; structural → matching `jrag` traversal; raw text / config / history → `Grep`/`Glob`/`Bash`). ② Fall back gracefully (`jrag` empty / `not_found` / exit 2 → `Grep`/`Glob`). ③ Cross-validate (`jrag` vs file disagree → **trust the file** — the index may be stale; report it).

**Rules of thumb:** structure beats vector for exact questions (`jrag find`/`inspect` + traversal); vector beats structure for fuzzy discovery (`jrag search`); raw text / config / history beats both (`Grep`/`Glob`/`Bash`); file-system beats a stale index.

---

## Workflow Patterns

- **"Explain feature X":** `jrag search "X"` → pick 1–3 hits → `jrag inspect <hit>` → targeted traversal (`callees`/`implementations`) → stop when answered.
- **"Where is X used?":** `jrag inspect <X>` → `jrag callers <X>` + `jrag dependents <X>` → `Grep` the symbol name as fallback → report sites with file:line.
- **"Find all Y":** structural → `jrag find --role <ROLE> [--service <S>]`; textual → `Grep`; broad → `Glob`+`Grep`. Summarize, don't dump.
- **"Trace flow A→B":** `jrag flow <route-A>` → `jrag connection <microservice>` (cross-service seams) → `Grep` the gaps → report with file:line.
- **"How is this configured?":** `Glob` `**/application*.yml` → `Grep` the key → `Read` sections → `jrag search "<key>" --table yaml`.
- **"Orient in a new service":** `jrag overview <S>` → `jrag conventions --service <S>` → `jrag map --service <S>` → `jrag http-routes --service <S>`.

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

## jrag surface — `--help` is the spec

`jrag` is self-documenting and the canonical, always-fresh source for commands, flags, and valid enum values — so this skill doesn't duplicate them. Don't memorize the surface:

- `jrag --help` — every command, grouped by intent, with one-line descriptions.
- `jrag <command> --help` — that command's flags and accepted values. Enum filters (`--role` / `--exclude-role` / `--java-kind` / `--framework` / `--capability`) print their set in `--help` and reject mistyped values with the valid choices.

The Decision Framework above tells you *which* command; reach for `--help` only when you need exact flags or enum values.

**Prerequisite.** `jrag` needs an index — unindexed, every command exits 2 (`jrag status` checks; the file-system tools work without one).

**Resolve-first contract.** Every `<query>` command resolves the identifier first, then maps `one` / `many` / `none` onto one envelope: `one` → run; `many` → return candidates and stop, **no silent guess across distinct types** (a class sharing its simple name with its own constructor still resolves to the type — narrow with `--kind` / `--role` / `--fqn-contains` / `--service`); `none` → `status: not_found` (exit 0), fall back to `search` or `Grep`. Pass names (FQN / simple name / route path / topic) or prior `sym:`/`route:`/`client:`/`producer:` ids — never raw node ids. `--kind` is a true resolve input; `--role` / `--java-kind` / `--fqn-contains` post-filter client-side.

**Output.** Default is compact text; `--format json` emits `{status, nodes, edges, candidates, truncated, agent_next_actions, file_location}` (empty fields dropped; `file_location` is a `filename:line` string; `agent_next_actions` suggests ≤5 next commands). `truncated` pages via `--limit` / `--offset` (`find` / `search` only). Output-shaping flags (every query / listing / traversal command — not `status` / `microservices` / `vocab-index`, which reject them): `--count` prints just the result count (bare int in text; `{"status","count"}` in json), `--exists` prints `true`/`false` (`{"status","exists"}` in json) and exits 0 on a hit / 2 on a miss (scriptable existence gate — `find X --exists`, `inspect X --exists`), `--fields fqn,role,…` projects each node to a comma-separated field allowlist (overrides `--detail`; ignored with `--count`/`--exists`; primarily a `--format json` lever).

**Edge semantics `--help` doesn't spell out.** `callers` / `callees` = `CALLS` in/out (on a controller/entry-point type, `callers` also lists the routes its methods `EXPOSE`). `impact` = bounded fan-in over `INJECTS` / `IMPLEMENTS` / `EXTENDS` (default depth 2; raise with `--depth`). `flow <route>` follows `EXPOSES` → `HTTP_CALLS` / `ASYNC_CALLS` → `CALLS`. `connection <microservice>` = inbound/outbound cross-service seams (its positional is a literal service name, not a query). Per-command edge mappings and the rest of the flag surface live in each command's `--help`.

**Node id prefixes (from prior results):** `sym:` (Symbol), `route:`/`r:` (Route), `client:`/`c:` (Client), `producer:`/`p:` (Producer). **Symbol FQN:** `<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,…)` — generics erased, no spaces after commas, no-arg `()`, constructor `#<init>(...)`.

**Ontology.** Role / symbol-kind / framework / capability values are enumerated in `--help`; client/producer kinds and source layers validate at runtime and surface the accepted set on a typo.
