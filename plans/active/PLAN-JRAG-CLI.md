# Plan: JRAG CLI — agent-facing command-line interface

Status: **active (planning)**. This plan implements
[`propose/JRAG-CLI-PROPOSE.md`](../../propose/JRAG-CLI-PROPOSE.md).

> **Grounded against current source (2026-07-04), then adversarially reviewed
> by a 5-subagent fan-out.** Every backend function, line range, and packaging
> claim was verified against `master` AND pressure-tested. The review caught 6
> blockers and ~7 highs that are folded in below (see "Revision log"). The
> proposal should be relocated to `propose/active/` to match `AGENTS.md` hygiene
> (out of scope for this plan; tracked at the end).

Depends on: nothing external. PR-JRAG-0a and PR-JRAG-0b are independent prep
refactors that unblock PR-JRAG-5 and PR-JRAG-1a respectively.

## Revision log (from the 5-reviewer fan-out)

Folded corrections (each verified against source):
- `outline` used `start_line=0` → `find_symbols_in_file_range` returns `[]`
  (guard rejects `<1`); now `start_line=1`.
- `overrides` (dispatch UP) was mapped to `override_axis_traversal_for`, which
  dispatches DOWN; now `neighbors_v2(out, ["OVERRIDES"])`.
- `overridden-by` was mapped to `override_axis_rollup_for`, which returns counts,
  not nodes; now `neighbors_v2(in, ["OVERRIDES"])` (= virtual `OVERRIDDEN_BY` out).
- `--offset` was global but **no `LadybugGraph` method takes `offset`** (only
  `find_v2`/`search_v2`/`neighbors_v2` do); now scoped to `find`/`search` only.
- PR-JRAG-5 now updates `tests/test_agent_skills_static.py` (hardcoded skill-dir
  set) and `run_update`'s tuple unpacking (return-type change).
- **Reversed the `resolve_operator_config` avoidance** — it does NOT import
  cocoindex (verified `config.py:387-465`); reusing it + `apply_to_os_environ()`
  is required for `search` to load the YAML-configured embedding model.
- Added `raise_fd_limit()` to `main`, pydantic→dict `model_dump()` at the envelope
  boundary, missing-index/ontology error envelopes, traceback-to-stderr.
- `find_route_callers`'s `microservice`/`method` kwargs are no-ops once `route_id`
  is set and it has no `limit` → `--service` is now a client-side post-filter +
  warning; truncation via client-side slice.
- Enum normalization now uses explicit lookup tables for `client_kind`/
  `producer_kind`/`source_layer` (their backend literals are lowercase snake +
  suffix, not UPPER_SNAKE).
- `callees` Producer target is `:Route` (`kafka_topic`), not `:Producer`.
- `flow` outbound intra-service is an index-time data property, not a query
  guarantee — reworded + the test validates the fixture's CALLS edges.
- PR-JRAG-1 and PR-JRAG-3 were each ~2× a reviewable size → split into 1a/1b and
  3a/3b (9 PRs total).

## Goal

- Ship a new `jrag` console script (separate from the `java-codebase-rag`
  operator CLI) that gives an AI coding agent **one command per engineering
  intent**, taking human-readable identifiers (FQN / simple name / route path /
  topic) and never raw node IDs.
- Make every common agent task achievable in one call by **internalizing
  resolve** (`resolve_v2`) as the first step of every `<query>`-accepting
  command, mapping its `one` / `many` / `none` contract onto a single output
  envelope.
- Build the CLI as a **thin compose-and-render layer** over the existing
  backend — `resolve_v2`, the MCP v2 handlers (`find_v2` / `search_v2` /
  `describe_v2` / `neighbors_v2`), `LadybugGraph` query methods, and
  `run_search`. No backend query logic is reimplemented.
- v1 loads the index **in-process** per call (no daemon), reusing the operator's
  index directory and config resolver.

## Principles (do not relitigate in review)

These were locked during the propose (`propose/JRAG-CLI-PROPOSE.md` §1, §2, §10).
If a reviewer wants to revisit one, they revisit the propose, not this plan.

- **Names in, names out; resolve-first.** Every traversal/inspect command takes
  a `<query>`; `resolve_v2` runs internally. Raw node IDs are never required or
  accepted. On `many` → return candidates and stop; on `none` → `not_found`.
  Auto-pick is forbidden.
- **Disambiguation flags narrow resolve, post-filter not push-down.** `--kind`
  maps to `resolve_v2`'s `hint_kind` (a true resolve input). `--java-kind`,
  `--role`, `--fqn-prefix` are **client-side post-filters** on resolve's
  node/candidate set — `resolve_v2(identifier, hint_kind, graph)` takes nothing
  else (`mcp_v2.py:1487`). If a post-filter collapses `many`→`one`, proceed; if
  it still leaves `many`, return the narrowed candidates.
- **Reuse, do not reimplement.** `find` → `find_v2` (`mcp_v2.py:990`); `search`
  → `search_v2` (`mcp_v2.py:907`); `inspect` → `describe_v2` (`mcp_v2.py:1088`);
  `callees` for Client/Producer → `resolve_v2` + `neighbors_v2(direction="out",
  edge_types=["HTTP_CALLS"|"ASYNC_CALLS"])` (`mcp_v2.py:1732`); `dependencies`
  → `neighbors_v2(direction="out", edge_types=["INJECTS"])`; `overrides` →
  `neighbors_v2(out, ["OVERRIDES"])`; `overridden-by` → `neighbors_v2(in,
  ["OVERRIDES"])`. Traversal commands with no composed path call the
  `LadybugGraph` method directly. **Config resolution reuses
  `resolve_operator_config` + `apply_to_os_environ`** (see Architecture).
- **`neighbors` is removed as a surface concept.** Every edge traversal gets a
  named engineering command. Agents never pass `direction` / `edge_types`.
- **One envelope; text default; JSON opt-in.** Default rendering is compact text;
  `--format json` emits the envelope verbatim. This is a **deliberate divergence**
  from the operator CLI's `sys.stdout.isatty()` heuristic
  (`java_codebase_rag/cli.py:218-220` → pprint-when-TTY / JSON-when-piped, no
  flag); `jrag` is agent-facing (non-TTY), so text-default-with-flag is the new
  convention.
- **`--help` is the spec.** Names guessable, grouped; flag/kind contradictions
  hard-error (`status: error`); inapplicable flags never silently ignored.
- **No ontology bump, no re-index** (`ontology_version` stays 17). **No daemon
  in v1.** **No cocoindex dependency** (the CLI never imports cocoindex; config
  resolution reuses the path layer, which is cocoindex-free).

## Architecture (where the CLI lives)

- **CLI module(s): inside the existing `java_codebase_rag` package**, as sibling
  modules to `cli.py`. Rationale: `java_codebase_rag` is already the one shipped
  package (`pyproject.toml:61`), so adding `.py` files inside it ships them with
  **zero packaging change** beyond one `[project.scripts]` line. Modules:
  - `java_codebase_rag/jrag.py` — argparse builder, `main(argv)`, and
    `_console_script_main()` (the `os._exit` wrapper the operator CLI uses at
    `cli.py:1031` — `jrag` loads lancedb + ladybug, so it needs the same wrapper).
  - `java_codebase_rag/jrag_envelope.py` — the `Envelope` dataclass, the
    resolve-first mapper, enum normalization (+ lookup tables), the +1-fetch
    `truncated` helper, and the pydantic→dict boundary.
  - `java_codebase_rag/jrag_render.py` — text rendering. Built fresh
    (`cli_format.py` is styling-primitives only — glyphs + ANSI, no renderers).
  - `java_codebase_rag/jrag_hints.py` — the **net-new** edge-label → CLI-command
    mapper for `agent_next_actions` (PR-JRAG-4).
- **Extracted resolve module: at repo root** as `resolve_service.py`, sibling to
  `mcp_v2.py` (PR-JRAG-0b). Shipped via `py-modules`. `mcp_v2.py` re-exports
  `resolve_v2` / `ResolveOutput` / `ResolveCandidate` / `ResolveStatus`.
- **Index + config resolution reuses the operator's resolver, exactly.** Call
  `resolve_operator_config(source_root=<discovered>, cli_index_dir=args.index_dir)`
  (same as `_resolved_from_ns` at `cli.py:237-244`), then `cfg.apply_to_os_environ()`
  — this sets `SBERT_MODEL` so `jrag search` loads the YAML-configured embedding
  model, not the default (without it, `run_search` reads the default model via
  `resolved_sbt_model_for_process_env`, `config.py:120-129` → silently wrong
  results). Pass `cfg.ladybug_path` to `LadybugGraph.get(...)`. **Verified
  cocoindex-free**: `resolve_operator_config` (`config.py:387-465`) only builds a
  `cocoindex.db` Path string; it never imports cocoindex. (The earlier "may pull
  cocoindex glue" rationale was wrong and is deleted.)
- **`main()` robustness:** first line `raise_fd_limit()` (from
  `java_codebase_rag._fdlimit`; the operator `main()` does this at `cli.py:1004`
  — lancedb's merge-insert opens many handles and macOS GUI/IDE soft limit is
  256). `_load_graph` calls `LadybugGraph.exists(ladybug_path)` first; on `False`
  → `status: error, message="No index at <path>. Run: java-codebase-rag init
  --source-root <root>"`; wraps `LadybugGraph.get()` in `try/except RuntimeError`
  → ontology-mismatch rebuild hint (`ladybug_queries.py:372-378`). The top-level
  handler emits the `status: error` envelope to stdout AND
  `traceback.format_exc()` to stderr before returning 2 (the operator CLI
  swallows tracebacks — `cli.py:1024-1028` — do NOT copy that).
- **Pydantic→dict boundary:** every backend handler returns pydantic v2 models
  (`FindOutput`, `DescribeOutput`, `NeighborsOutput`, `ResolveOutput`,
  `SearchOutput`). The envelope holds plain `dict` (`nodes: dict[str, dict]`,
  `edges: list[dict]`, `candidates: list[dict]`). Conversion is via
  `.model_dump()` **at the envelope boundary, once**; the renderer and
  `to_json()` operate on dicts only.
- **Lazy imports:** `ladybug_queries`, `mcp_v2`, `search_lancedb`,
  `resolve_service`, and `resolve_operator_config` are imported **inside command
  handlers**, and `build_parser()` imports no backend modules at all — so
  `jrag --help` stays fast (matches `cli.py:1-4`, `build_parser` at `:796`). PR-4
  pins this with a `sys.modules` sentinel.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-JRAG-0a** | Single source of truth for shipped skill/agent docs: `scripts/sync_agent_artifacts.py` syncs the **shipped subtrees only** (`skills/explore-codebase/` + `agents/*.md`) and asserts equality; drift test gates it. | none | `skills/README.md` is dev-only (NOT in `install_data` today) — the sync must mirror only what `package-data` ships, or it will copy+ship README.md. Publish is manual — sync runs from the publish runbook. | `tests/test_install_data_sync.py` | — |
| **PR-JRAG-0b** | Extract `resolve_v2` + its pipeline + resolve-only models into root `resolve_service.py`; `mcp_v2.py` re-exports. | none | `NodeRef` (`mcp_v2.py:449`) is shared by `Edge.other` (and constructed-from in `describe_v2`) — it STAYS in `mcp_v2.py`; only resolve-specific models move. Gate: existing resolve tests in `test_mcp_v2.py` + `test_mcp_hints.py`. | `tests/test_resolve_service.py` | — |
| **PR-JRAG-1a** | Entry point + envelope + render foundation + resolve-first + `status`. The frozen contract every later PR depends on. | none | Defines the envelope + resolve-first + render contract. `+1-fetch` truncated; enum lookup tables (client_kind/producer_kind/source_layer are lowercase-snake+suffix, NOT UPPER_SNAKE); pydantic→dict `model_dump()` boundary; `resolve_operator_config` + `apply_to_os_environ` + `raise_fd_limit` + missing-index error envelopes; `--offset` is NOT global. | `test_jrag_envelope.py`, `test_jrag_render.py`, `test_jrag_status.py` | PR-JRAG-0b |
| **PR-JRAG-1b** | `find` (query-mode via `find_by_name_or_fqn` + `--fuzzy` fallback; filter-mode via `find_v2` + `NodeFilter`; kind-inference; contradiction-error; **`--offset` supported here**) + `inspect` (`describe_v2` + `edge_summary`). | none | `find` has two modes (positional `<query>` vs pure flags) — different backends; `--limit` effectively capped at 499 (so `limit+1` fits the 500 backend clamp); `NodeRef` has no `name` → renderer derives it from FQN. Both wire a no-op `next_actions` hook for PR-4. | `test_jrag_locate.py` | PR-JRAG-1a |
| **PR-JRAG-2** | Listing tier: `routes`, `clients`, `producers`, `topics`, `jobs`, `listeners`, `entities` + globals. | none | `--offset` NOT supported (no offset param on `list_*`); `topics` are `:Producer` rows (no `:Topic` node) and `--consumer-in` resolves via `neighbors_v2(producer_ids, "in", ["ASYNC_CALLS"])`; enum lookup tables; truncated via +1-fetch (cap 499). | `test_jrag_listing.py` | PR-JRAG-1a |
| **PR-JRAG-3a** | Direct-backend traversals: `callers`, `callees`(symbol), `hierarchy`, `implementations`, `subclasses`, `overrides`, `overridden-by`, `dependents`, `impact`, `decompose`, `flow`. | none | `--offset` NOT supported; `overrides`/`overridden-by` via `neighbors_v2` (not the rollup/traversal fns — those go the wrong way / return counts); `find_route_callers` `--service` is a client-side post-filter + warning (kwarg ignored once `route_id` set) and has no `limit` → client-side slice; `flow` intra-service is an index-time data property; `--include-external` symmetric on callers+callees. | `test_jrag_traversal_direct.py` | PR-JRAG-1a |
| **PR-JRAG-3b** | Compose commands + file inspection: `callees`(client/producer via `resolve_v2`+`neighbors_v2`), `dependencies` (`neighbors_v2` out INJECTS), `connection` (microservice positional; `--inbound`/`--outbound`/`--both`/`--http-method`/`--calls-service`), `outline` (`find_symbols_in_file_range`, `start_line=1`), `imports` (tree-sitter `import_declaration` + `resolve_v2`). | none | `callees` Producer target is `:Route` (`kafka_topic`), not `:Producer`; `outline`/`imports` have no `limit` → documented unbounded; `connection` first positional is a microservice name (resolve-first exception). | `test_jrag_traversal_compose.py` | PR-JRAG-3a |
| **PR-JRAG-4** | Orientation + search + `agent_next_actions` + packaging: `microservices`, `map`, `conventions`, `overview` (`--as`); `search` (`search_v2`, `--offset`, `--table all`, `--hybrid`, `--fuzzy` rejected in-handler); `jrag_hints.next_actions` (edge_summary optional, zero-direction suppression, dot-keys); wire `next_actions` into all commands; README; version bump; token-budget assertion; `build_parser` lazy sentinel. | none | `search` reuses `search_v2` (map flags → `NodeFilter`); `next_actions` is NET-NEW (`mcp_hints` maps every edge to `tool="neighbors"`); `edge_summary` is `None` for traversal roots → fall back to `result_edges`; `--fuzzy` must be registered + rejected in-handler (not argparse-exit) to yield `status: error`. | `test_jrag_orientation.py`, `test_jrag_token_budget.py` | PR-JRAG-1a, PR-JRAG-3b |
| **PR-JRAG-5** | Agent host integration: `Surface` dimension + `ArtifactManifest`; `select_surface` wizard step + `--surface mcp\|cli` (default `mcp`); marker-file `detect_configured_hosts` fix (NamedTuple return + `run_update` unpacking); surface-conditional `resolve_mcp_command` (incl. interactive prompt); ship CLI skill + subagent; update `test_agent_skills_static.py`, `AGENTS.md`, `skills/README.md`, README three-layer section. | none | Installer coupling — 4 functions + 2 tests + 3 docs touched; `deploy_artifacts`/`refresh_artifacts` gain `surface="mcp"` kw default (back-comat with 8 direct-call tests); CLI-only install must not regress `update`; depends on PR-JRAG-0a. | `tests/test_installer_surface.py` (+ updates to `tests/test_installer.py`, `tests/test_agent_skills_static.py`) | PR-JRAG-0a (hard), PR-JRAG-4 (soft) |

Landing order: **0a → 0b → 1a → 1b → 2 → 3a → 3b → 4 → 5**.
- **0a** and **0b** are independent (different files); may land in either order.
- **1a** depends on **0b**; **1b** depends on **1a**.
- **2** and **3a** depend on **1a** (envelope/render/resolve-first); independent
  of **1b** and of each other — may land in parallel after 1a.
- **3b** depends on **3a** (traversal patterns + resolve-first reuse).
- **4** depends on **1a** (hard) and **3b** (hard — `agent_next_actions` suggests
  traversal commands that must exist).
- **5** depends on **0a** (hard) and **4** (soft — the CLI skill mirrors the
  shipped grammar).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| CLI location | Inside `java_codebase_rag/` package (sibling modules to `cli.py`); entry `jrag = "java_codebase_rag.jrag:_console_script_main"`. Zero packaging change beyond the script line. |
| Resolve extraction target | Root-level `resolve_service.py` (sibling to `mcp_v2.py`); `NodeRef` stays in `mcp_v2.py`; `mcp_v2` re-exports resolve symbols. |
| Config resolution | Reuse `resolve_operator_config` + `apply_to_os_environ()` (cocoindex-free, verified); pass `cfg.ladybug_path` to `LadybugGraph.get()`. |
| Envelope model | Lean `@dataclass` (not pydantic — avoids validation overhead); backend pydantic outputs converted via `model_dump()` at the boundary; `to_json()` via `json.dumps`. Omits empty optionals. |
| `truncated` | +1-fetch trick: pass `limit+1`; `truncated = len(rows) > limit`; drop the +1th row. `total_count` / "M of N" deferred. |
| `--offset` scope | NOT global. Supported only on `find`/`search` (they route through `find_v2`/`search_v2`, which accept `offset`). Traversal/listing commands (direct `LadybugGraph` methods — none take `offset`) emit `truncated: more results — narrow your query` instead. `--limit` on find/search effectively capped at 499 (so `limit+1` fits the 500 backend clamp on `list_*`). |
| Disambiguation flags | Only `--kind` is a resolve input (`hint_kind`); `--java-kind`/`--role`/`--fqn-prefix` post-filter the resolve node/candidate set client-side. |
| `--service` push-down vs post-filter | Pushed down where the method takes `microservice` (`find_callers`, `find_callees`, `find_implementors`, `find_subclasses`, `find_injectors`, `list_*`, `trace_flow`). NOT pushed (client-side post-filter + `warnings[]`) on: `impact` (no param), `find_route_callers` (kwarg ignored once `route_id` set). |
| Enum normalization | `normalize_enum()` for `role`/`capability`/`framework`/`java_kind` (case + kebab→UPPER_SNAKE). Explicit **lookup tables** for the lowercase-snake+suffix kinds: `client_kind` (`feign`→`feign_method`, `rest-template`→`rest_template`, `web-client`→`web_client`), `producer_kind` (`kafka`→`kafka_send`, `stream-bridge`→`stream_bridge_send`), `source_layer` (`builtin`→`builtin`, `layer-a`→`layer_a_meta`, `layer-b-ann`→`layer_b_ann`, `layer-b-fqn`→`layer_b_fqn`, `layer-c`→`layer_c_source`; confirm literals against `java_ontology`/`graph_enrich` at impl). |
| Text rendering | Built fresh in `jrag_render.py`; inspect renderer sorts ALL dict keys alphabetically (snapshot stability); `simple_name(node) = node.fqn.rsplit('.', 1)[-1]` (NodeRef has no `name`); `conf:` only on CALLS-family; zero-vs-`not_found` distinct; ambiguous candidates carry `reason`. |
| `overrides` / `overridden-by` | `overrides` → `neighbors_v2([id], "out", ["OVERRIDES"])` (overrider→declaration = dispatch UP); `overridden-by` → `neighbors_v2([id], "in", ["OVERRIDES"])` (= virtual `OVERRIDDEN_BY` out). The `override_axis_*` functions are NOT used for these listings (wrong direction / counts-only); `override_axis_rollup_for` feeds `inspect`'s `edge_summary` only. |
| `agent_next_actions` | NEW mapper in `jrag_hints.py`; `next_actions(*, root, edge_summary=None, result_edges, graph)`; for traversal commands pass `edge_summary=None` (fall back to `result_edges`); for each `(label, counts)` emit only where `counts[d] > 0`; ≤5; covers dot-keys. |
| `file_location` | Populated by `resolve_query` from the resolved node's `filename` + `start_line` when `status="one"`; omitted otherwise. |
| Output format | `--format text\|json`, default `text`. New convention (diverges from operator CLI isatty). |
| Daemon / `jrag source` / raw IDs | Deferred / not shipped / never required. |

---

# PR-JRAG-0a — Single source of truth for shipped agent artifacts

**Goal:** collapse the byte-identical, hand-synced dual copies of the skill and
agent docs into **one canonical dev source** with a derived `install_data` copy,
so PR-JRAG-5 does not create four hand-synced copies when the CLI variants land.

**Key facts (verified):** `skills/explore-codebase/SKILL.md` and
`agents/explorer-rag-enhanced.md` are byte-identical to their
`java_codebase_rag/install_data/...` counterparts. They ship via
`[tool.setuptools.package-data] "java_codebase_rag" =
["install_data/skills/**/*", "install_data/agents/**/*"]` (`pyproject.toml:85-86`)
and are read at runtime by `_read_package_artifact`
(`java_codebase_rag/installer.py:550`) via
`importlib.resources.files("java_codebase_rag.install_data")`. No build-time
generation; no `MANIFEST.in`. **`skills/README.md` exists ONLY in dev-root** (not
shipped) — the sync must mirror only the shipped subtrees, not the whole
`skills/` directory, or it will copy+ship README.md. Publishing is manual.

## File-by-file changes

### 1. New `scripts/sync_agent_artifacts.py`
- Sync ONLY the package-data-shipped subtrees: `skills/explore-codebase/` →
  `java_codebase_rag/install_data/skills/explore-codebase/` and
  `agents/*.md` → `java_codebase_rag/install_data/agents/`. Do **not** mirror
  `skills/README.md` (dev-only index).
- After copying, assert every shipped destination file is byte-equal to its
  source; exit non-zero with a diff on mismatch.
- `--check` mode: verify only (no copy), for CI / pre-commit.

### 2. `.agents/skills/publish-pip/SKILL.md` — runbook update
- Insert the sync step before `.venv/bin/python -m build`: invoke
  `.venv/bin/python scripts/sync_agent_artifacts.py` (fail the publish on drift).

### 3. `tests/test_install_data_sync.py` (new)
- `test_install_data_artifacts_in_sync_with_dev_source` — `--check` passes at HEAD.
- `test_sync_script_detects_drift` — mutate a dev source byte, assert `--check`
  exits non-zero and names the file; restore via tempfile shadowing.

## Definition of done (PR-JRAG-0a)

- [ ] `scripts/sync_agent_artifacts.py` mirrors only shipped subtrees (excludes
      `skills/README.md`); `--check` passes at HEAD.
- [ ] `publish-pip` runbook invokes the sync step before `python -m build`.
- [ ] `tests/test_install_data_sync.py` present and passing.
- [ ] No change to shipped artifact contents (the shipped set is unchanged).
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `chore(install): single-source agent artifacts + sync check (PR-JRAG-0a)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Write `scripts/sync_agent_artifacts.py` (shipped-subtree copy + `--check`; exclude `skills/README.md`) | `scripts/sync_agent_artifacts.py` | `--check` passes at HEAD; README.md not mirrored |
| 2 | Add drift + detection tests | `tests/test_install_data_sync.py` | both pass |
| 3 | Wire sync into the publish runbook | `.agents/skills/publish-pip/SKILL.md` | step before `python -m build` |
| 4 | ruff + full suite | repo | clean + green |

---

# PR-JRAG-0b — Extract `resolve_v2` into `resolve_service.py`

**Goal:** lift the resolve pipeline out of `mcp_v2.py` into a neutral-named,
transport-agnostic root module so the CLI's resolve-first layer imports
`resolve_service` and cannot silently re-implement the pipeline.

**Key facts (verified):** `mcp_v2.py` imports **zero MCP SDK** (only local
`mcp_hints`, plus `ladybug_queries`, `search_lancedb`, `java_ontology`,
`index_common`, `java_codebase_rag.config`). `resolve_v2(identifier, hint_kind,
graph) -> ResolveOutput` is at `mcp_v2.py:1487`; `ResolveOutput` at `:602`,
`ResolveCandidate` at `:594`, `ResolveStatus = Literal["one","many","none"]` at
`:544`. `NodeRef` (`:449`) is shared by `Edge.other` (`:488`) and constructed-from
in `describe_v2` — it stays in `mcp_v2.py`.

## File-by-file changes

### 1. New `resolve_service.py` (repo root)
- Move `resolve_v2` + its private pipeline (identifier parse → candidate
  collectors → dedupe → rank → finalize) into this module.
- Move `ResolveOutput`, `ResolveCandidate`, `ResolveStatus` here.
- **Import** `NodeRef` from `mcp_v2` (do not move — shared by non-resolve models).
  Import `ResolveReason` from `java_ontology`; `LadybugGraph` from `ladybug_queries`.

### 2. `mcp_v2.py` — re-export + deduplicate
- `from resolve_service import resolve_v2, ResolveOutput, ResolveCandidate, ResolveStatus`
  so every existing call site is unchanged. Remove the duplicated private helpers.

### 3. `pyproject.toml`
- Add `resolve_service` to `[tool.setuptools] py-modules` (`:62-79`).

### 4. `tests/test_resolve_service.py` (new)
- Direct-import parity tests (see below).

## Tests for PR-JRAG-0b

1. `test_resolve_service_importable_and_one_match` —
   `from resolve_service import resolve_v2, ResolveOutput`; unique FQN → `status=="one"`.
2. `test_resolve_service_many_returns_candidates`
3. `test_resolve_service_none_is_not_found`
4. **Must-still-pass:** `tests/test_mcp_v2.py` and `tests/test_mcp_hints.py` (the
   two existing resolve-symbol importers) unchanged.

## Definition of done (PR-JRAG-0b)

- [ ] `resolve_v2`/`ResolveOutput`/`ResolveCandidate`/`ResolveStatus` in `resolve_service.py`; `NodeRef` remains in `mcp_v2.py`.
- [ ] `mcp_v2.py` re-exports; no call site changed; still imports zero MCP SDK.
- [ ] `resolve_service` in `py-modules`.
- [ ] New + existing resolve tests green.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] Sentinel: `grep -nE "^from mcp import|^import mcp|FastMCP" mcp_v2.py` returns 0.
- [ ] PR title: `refactor(resolve): extract resolve_v2 to resolve_service.py (PR-JRAG-0b)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Create `resolve_service.py`; move resolve symbols + pipeline; import `NodeRef` from `mcp_v2` | `resolve_service.py`, `mcp_v2.py` | `resolve_v2` callable from new module |
| 2 | Re-export from `mcp_v2`; delete duplicated helpers | `mcp_v2.py` | existing imports still resolve |
| 3 | Add `resolve_service` to `py-modules` | `pyproject.toml` | `pip install -e .` ships it |
| 4 | Parity tests + ruff + full suite | `tests/test_resolve_service.py`, repo | green |

---

# PR-JRAG-1a — Entry point + envelope/render foundation + resolve-first + status

**Goal:** the frozen contract every later PR builds on: the `jrag` console
script, the `Envelope` + resolve-first layer + text renderer, the config/index
loader (with error envelopes), and `status`. No command logic beyond `status`.

## File-by-file changes

### 1. `java_codebase_rag/jrag_envelope.py` (new)
- `@dataclass class Envelope`: `status` (`Literal["ok","ambiguous","not_found","error"]`),
  `nodes: dict[str, dict]`, `edges: list[dict]`, `root: str | None`,
  `candidates: list[dict]`, `agent_next_actions: list[str]`, `warnings: list[str]`,
  `truncated: bool`, `file_location: str | None`. `to_dict()` omits empty
  optionals; `to_json()` = `json.dumps(to_dict())`.
- `resolve_query(identifier, *, hint_kind, java_kind, role, fqn_prefix, cfg) ->
  tuple[NodeRef | None, Envelope]`:
  - `g = LadybugGraph.get(cfg.ladybug_path)` (caller passes the loaded graph).
  - Calls `resolve_v2(identifier, hint_kind=hint_kind, graph=g)`.
  - `"one"`: apply post-filters (`java_kind`/`role`/`fqn_prefix`) to the node; if
    pass → `(node, env ok)` and set `env.file_location` from `node.filename` +
    `node.start_line`; if fail → `(None, not_found)`.
  - `"many"`: post-filter candidates; if one survives → treat as one; else
    `(None, ambiguous)` with capped-at-10 candidates each carrying `reason`.
  - `"none"`: `(None, not_found)` with `message` mentioning `jrag search`.
- `normalize_enum(value, *, kind)` — case+kebran→UPPER_SNAKE for
  role/capability/framework/java_kind; routes client_kind/producer_kind/source_layer
  through their lookup tables (see Resolved decisions).
- `mark_truncated(rows, limit) -> tuple[list, bool]` — +1-fetch helper.
- `simple_name(node_dict) -> str` — `fqn.rsplit('.', 1)[-1]`.
- `to_envelope_rows(pydantic_results)` — `.model_dump()` each (the boundary).

### 2. `java_codebase_rag/jrag_render.py` (new)
- `render(envelope, *, fmt, noun="")` dispatches (`text` default; `json`→`to_json`).
- Shapes: `_render_listing`, `_render_traversal` (`root:` + edge rows; `conf:`
  only on CALLS-family), `_render_graph` (`d=N`), `_render_inspect` (kv-block +
  indented `edge_summary`, ALL keys alphabetical), `_render_ambiguous` (count +
  narrowing legend + `reason`; no file/score; ≤2 `next:` hints; no auto-pick),
  `_render_scalar`.
- `tiered_name(node_id, nodes)` — simple name → `name @service` → FQN (via `simple_name`).
- Zero-results: `0 <noun>  <fqn>  @<service>`; `not_found`: `not found: <msg>`.
- Non-offset commands: `truncated: more results — narrow your query`. Offset
  commands (`find`/`search`): `truncated: more results — use --offset <offset+limit>`.

### 3. `java_codebase_rag/jrag.py` (new)
- `build_parser()` — argparse + subparsers (`dest="command"`). Globals per
  command via a parent parser: `--service`, `--module`, `--limit` (default 20;
  10 fan-out), `--index-dir`, `--format text|json`, `--brief`, `--fields`,
  `--count`/`--exists`. **`--offset` is added ONLY to `find`/`search` subparsers
  (PR-1b/PR-4), not as a global.** No backend imports at module top.
- `_load_graph(cfg) -> LadybugGraph` — `LadybugGraph.exists(cfg.ladybug_path)`
  first → on False raise `_IndexNotFound` (caught in `main` → actionable
  envelope); else `LadybugGraph.get(cfg.ladybug_path)` wrapped in
  `try/except RuntimeError` (ontology mismatch → `_IndexStale`).
- `_resolve_cfg(args) -> ResolvedOperatorConfig` —
  `cfg = resolve_operator_config(source_root=discover_project_root(Path.cwd()),
  cli_index_dir=args.index_dir)`; `cfg.apply_to_os_environ()`; return `cfg`.
  (Lazy import of `resolve_operator_config` + `discover_project_root`.)
- `main(argv=None) -> int` — first line `raise_fd_limit()`; parse; dispatch; the
  top-level handler emits `status: error` envelope to stdout AND
  `traceback.format_exc()` to stderr; returns 2 on error / 1 on usage / 0 on ok.
- `_console_script_main()` — `os._exit(main())` wrapper.
- `status` command — `cfg` + `LadybugGraph`; render `meta()` + counts (ontology
  version, index dir, freshness, loaded counts, source root).

### 4. `pyproject.toml`
- Add `[project.scripts]` `jrag = "java_codebase_rag.jrag:_console_script_main"`.

### 5. `README.md` — preview subsection (`## jrag (agent CLI, preview)`).

## Tests for PR-JRAG-1a

`tests/test_jrag_envelope.py`:
1. `test_envelope_to_dict_omits_empty_optionals`
2. `test_pydantic_results_converted_via_model_dump` — pass a pydantic `NodeRef`,
   assert the envelope holds a plain dict.
3. `test_resolve_query_one_proceeds_and_sets_file_location`
4. `test_resolve_query_many_returns_candidates_with_reason`
5. `test_resolve_query_many_post_filter_collapses_to_one`
6. `test_resolve_query_none_is_not_found_with_search_hint`
7. `test_normalize_enum_role_uppercase` (`controller`/`Controller`/`CONTROLLER`→`CONTROLLER`)
8. `test_normalize_enum_client_kind_lookup` (`feign`→`feign_method`, `rest-template`→`rest_template`)
9. `test_normalize_enum_producer_kind_lookup` (`kafka`→`kafka_send`)
10. `test_mark_truncated_flags_and_clips`

`tests/test_jrag_render.py`:
11. `test_render_listing_omits_fqn`
12. `test_render_traversal_conf_only_on_calls`
13. `test_render_inspect_edge_summary_alphabetical`
14. `test_render_ambiguous_lists_reason_no_file`
15. `test_render_zero_results_vs_not_found_distinct`
16. `test_render_truncated_narrow_query_for_non_offset_commands`
17. `test_render_truncated_offset_hint_for_offset_commands`
18. `test_render_json_emits_envelope_verbatim`
19. `test_simple_name_derived_from_fqn` (NodeRef has no `name`)

`tests/test_jrag_status.py`:
20. `test_status_reports_ontology_version_and_counts` (ontology 17).
21. `test_missing_index_returns_actionable_error` — point at an empty dir →
    `status: error`, message mentions `java-codebase-rag init`.
22. `test_offset_is_not_a_global_flag` — `jrag callers --offset 5` → usage error
    (offset not registered on traversal commands).

Plus one subprocess smoke test: `.venv/bin/jrag status` exits 0; `.venv/bin/jrag --help`
completes and lists `status`.

## Definition of done (PR-JRAG-1a)

- [ ] `jrag.py`/`jrag_envelope.py`/`jrag_render.py` present; `[project.scripts] jrag` added.
- [ ] `resolve_operator_config` + `apply_to_os_environ` reused; `raise_fd_limit()` in `main`.
- [ ] Missing-index + ontology-mismatch → actionable `status: error` envelopes;
      top-level handler logs traceback to stderr.
- [ ] Pydantic→dict `model_dump()` boundary; envelope omits empty optionals.
- [ ] Enum lookup tables for client_kind/producer_kind/source_layer.
- [ ] `--offset` is NOT global (only find/search get it later).
- [ ] All named tests green; full suite green; `jrag --help` fast.
- [ ] Sentinels: `grep -nE "^from mcp import|^import mcp" java_codebase_rag/jrag*.py` → 0;
      `grep -n "import cocoindex\|java_index_flow_lancedb" java_codebase_rag/jrag*.py` → 0;
      `python -c "import java_codebase_rag.jrag as j; j.build_parser()"` imports no torch/sentence_transformers (check `sys.modules`).
- [ ] PR title: `feat(cli): jrag entry point + envelope/render + status (PR-JRAG-1a)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Envelope dataclass + model_dump boundary + lean-omit | `jrag_envelope.py` | tests 1–2 pass |
| 2 | `resolve_query` + post-filter + `file_location`; `normalize_enum`+tables; `mark_truncated`; `simple_name` | `jrag_envelope.py` | tests 3–10 pass |
| 3 | Renderer (all shapes + tiered_name + truncated variants) | `jrag_render.py` | tests 11–19 pass |
| 4 | `jrag.py`: parser (no global offset) + `_resolve_cfg` (reuse operator config) + `_load_graph` (exists + error envelopes) + `main` (`raise_fd_limit`, stdout+stderr handler) + `_console_script_main` + `status` | `jrag.py` | tests 20–22 pass |
| 5 | `[project.scripts] jrag`; README preview | `pyproject.toml`, `README.md` | `jrag --help` works; link resolves |
| 6 | ruff + full suite + subprocess smoke + sentinels | repo | clean + green |

---

# PR-JRAG-1b — `find` + `inspect`

**Goal:** the first real commands, over `find_v2` / `find_by_name_or_fqn` /
`describe_v2`. Both leave a no-op `next_actions` hook for PR-4.

## File-by-file changes

### 1. `java_codebase_rag/jrag.py` — add `find` + `inspect`
- `find` has **two modes**:
  - **Query mode** (positional `<query>`): call
    `g.find_by_name_or_fqn(query, kinds=<inferred kinds>, module=..., microservice=..., limit=limit+1)`.
    `--fuzzy` enables a jrag-side fallback (exact → prefix → contains on the
    identifier string) if exact returns nothing (NOT semantic; `find_by_name_or_fqn`
    has no fuzzy param). `--role`/`--java-kind`/`--exclude-role`/`--annotation`/
    `--capability`/`--framework`/`--source-layer` post-filter the rows.
  - **Filter mode** (no positional): build a `NodeFilter` from flags and call
    `find_v2(kind, filter, limit=limit+1, offset=args.offset, graph=g)`.
  - **Kind inference** (when `--kind` omitted): `--http-method`/`--path-prefix`⇒route,
    `--client-kind`/`--calls-service`/`--calls-path-prefix`⇒client,
    `--producer-kind`/`--topic-prefix`⇒producer, else symbol. A domain flag
    contradicting explicit `--kind` → `status: error` naming the pair.
  - `--offset` IS supported (passes to `find_v2`); render offset-hint truncated.
    `--limit` effectively capped at 499.
  - Flag→`NodeFilter`/post-filter map (all proposal §5 find flags handled):
    `--role`→role, `--exclude-role`→exclude_roles, `--annotation`→annotation,
    `--capability`→capability, `--fqn-prefix`→fqn_prefix, `--java-kind`→symbol_kind,
    `--framework`→framework, `--source-layer`→source_layer, `--http-method`→http_method,
    `--path-prefix`→path_prefix, `--client-kind`→client_kind, `--calls-service`→target_service,
    `--calls-path-prefix`→target_path_prefix, `--producer-kind`→producer_kind,
    `--topic-prefix`→topic_prefix.
- `inspect <query>` — `resolve_query(...)`; on one, `describe_v2(id=node.id,
  graph=g)`; place `NodeRecord.model_dump()` (incl. `edge_summary`) in `nodes`;
  render inspect. Call `next_actions_hook(...)` (no-op stub for now).
- Both call `next_actions_hook(envelope, root, edge_summary=None, result_edges=...)`
  defined as a no-op in `jrag_envelope` (PR-4 fills it).

## Tests for PR-JRAG-1b

`tests/test_jrag_locate.py` (bank-chat fixture):
1. `test_find_by_fqn_exact` (query mode)
2. `test_find_filter_mode_by_role` (filter mode, `--role controller`)
3. `test_find_by_capability` (`--capability scheduled-task`, symbol inferred)
4. `test_find_kind_inference_from_http_method` (route inferred)
5. `test_find_kind_contradiction_is_error` (`--kind symbol --http-method GET`)
6. `test_find_fuzzy_falls_back_to_prefix`
7. `test_find_annotation_flag_filters`
8. `test_find_exclude_role_flag_filters`
9. `test_find_offset_paginates` (`--offset` works on find)
10. `test_find_limit_capped_under_500` (`--limit 600` → behaves as ≤499)
11. `test_inspect_returns_edge_summary_with_composed_keys` (`OVERRIDDEN_BY` virtual key)
12. `test_inspect_ambiguous_returns_candidates`
13. `test_inspect_populates_file_location`

## Definition of done (PR-JRAG-1b)

- [ ] `find` (both modes) + `inspect` implemented; all §5 find flags mapped.
- [ ] `--offset` on `find`; `--limit` cap-at-499 documented.
- [ ] `next_actions_hook` stub present (no-op).
- [ ] All named tests green; full suite green.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `feat(cli): jrag find + inspect (PR-JRAG-1b)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | `find` query mode (`find_by_name_or_fqn` + `--fuzzy` fallback + post-filters) | `jrag.py` | tests 1,6,7,8 pass |
| 2 | `find` filter mode (`find_v2` + NodeFilter + kind-inference + contradiction + offset) | `jrag.py` | tests 2–5,9,10 pass |
| 3 | `inspect` (`describe_v2` + edge_summary + file_location) + `next_actions_hook` stub | `jrag.py`, `jrag_envelope.py` | tests 11–13 pass |
| 4 | ruff + full suite | repo | clean + green |

---

# PR-JRAG-2 — Listing tier

**Goal:** all-nodes-of-a-kind commands. Globals except `--offset` (not supported
here — `list_*` methods take no offset).

## File-by-file changes

### 1. `java_codebase_rag/jrag.py` — listing subcommands
Each builds kwargs, calls the `LadybugGraph` method with `limit+1` (capped so
`limit+1 ≤ 500`), `mark_truncated`, renders listing. Enum flags via lookup tables.
- `routes` → `g.list_routes(microservice=..., framework=..., path_prefix=..., method=..., limit=...)`.
- `clients` → `g.list_clients(microservice=..., client_kind=..., target_service=<--calls-service>, path_prefix=..., limit=...)`.
- `producers` → `g.list_producers(microservice=..., producer_kind=..., topic_prefix=..., limit=...)`.
- `topics` → group `list_producers(topic_prefix=...)` by topic name. `--producer-in`
  scopes producers by their `microservice`; `--consumer-in <svc>` resolves
  consumers via `neighbors_v2(producer_ids, direction="in", edge_types=["ASYNC_CALLS"])`
  across producers sharing the topic, filtered to `<svc>`. (No `:Topic` node.)
- `jobs` → `g.list_by_capability("SCHEDULED_TASK", ...)`.
- `listeners` → `g.list_by_capability("MESSAGE_LISTENER", ...)` + optional `--topic-prefix`.
- `entities` → `g.list_by_role("ENTITY", ...)`.

## Tests for PR-JRAG-2

`tests/test_jrag_listing.py`:
1. `test_routes_returns_route_kind`
2. `test_clients_filters_by_calls_service`
3. `test_producers_filter_by_topic_prefix`
4. `test_topics_groups_producers_by_topic` (no `:Topic` node assumed)
5. `test_topics_consumer_in_uses_neighbors_in_async_calls`
6. `test_jobs_lists_scheduled_task`
7. `test_listeners_lists_message_listener`
8. `test_entities_lists_entity_role`
9. `test_listing_service_scope_pushes_down`
10. `test_listing_truncated_fires_at_limit` (+1-fetch)
11. `test_listing_client_kind_enum_lookup` (`--client-kind feign` → `feign_method`)
12. `test_listing_rejects_offset` (`--offset` not registered → usage error)

## Definition of done (PR-JRAG-2)

- [ ] All 7 listing commands; globals supported; `--offset` rejected.
- [ ] `topics --consumer-in` via `neighbors_v2(in, ASYNC_CALLS)`; client/producer
      kinds via lookup tables.
- [ ] All named tests green; full suite green.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `feat(cli): jrag listing tier (PR-JRAG-2)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | 7 listing subcommands + flags (no offset) | `jrag.py` | tests 1–4,6–9 pass |
| 2 | `topics` producer-grouped + `--consumer-in` via neighbors_v2 | `jrag.py` | tests 4,5 pass |
| 3 | Enum lookups + truncation + offset-rejection | `jrag.py` | tests 10–12 pass |
| 4 | ruff + full suite | repo | clean + green |

---

# PR-JRAG-3a — Direct-backend traversals

**Goal:** the traversals that call `LadybugGraph` methods (or `neighbors_v2` for
the override axis) directly. `--offset` NOT supported.

## File-by-file changes

### 1. `java_codebase_rag/jrag.py` — traversal subcommands
Each: `resolve_query(...)` → on one, call backend → envelope → render. `--limit`
via +1-fetch where the method takes `limit`; client-side slice otherwise.
- `callers` — Symbol → `g.find_callers(node.fqn, *, depth, limit=limit+1,
  min_confidence, exclude_external=not --include-external, module, microservice)`;
  Route → `g.find_route_callers(route_id=node.id)` then **client-side** filter by
  `--service` on `RouteCaller.caller_microservice` (+ `warnings[]` like `impact`)
  and client-side slice for truncation (no backend `limit`).
- `callees` (Symbol) → `g.find_callees(...)` (`--include-external` symmetric).
- `hierarchy` → `neighbors_v2([id], "in", ["EXTENDS","IMPLEMENTS"])` + `"out"`;
  render `↑`/`↓` tree.
- `implementations` → `g.find_implementors(node.fqn, *, microservice, module,
  limit=limit+1)`; `--capability` is a **client-side post-filter** on returned
  implementors' capabilities (the method has no `capability` kwarg).
- `subclasses` → `g.find_subclasses(...)`.
- `overrides` → `neighbors_v2([id], "out", ["OVERRIDES"])` (dispatch UP:
  overrider→declaration).
- `overridden-by` → `neighbors_v2([id], "in", ["OVERRIDES"])` (= virtual
  `OVERRIDDEN_BY` out; dispatch DOWN).
- `dependents` → `g.find_injectors(node.fqn, *, microservice, module, limit=limit+1)`.
- `impact` → `g.impact_analysis(node.fqn, *, depth, limit=limit+1)`; `--service`
  client-side post-filter + `warnings[]`.
- `decompose` → `g.trace_flow(seed_fqns=[node.fqn], *, depth=clamp(1..3),
  follow_calls=--follow-calls, stage_limit=--max-stage, microservice, module,
  min_call_confidence, exclude_external)`; role-waterfall render.
- `flow` → requires Route root; `g.trace_request_flow(entry_route_id=node.id,
  max_hops=clamp(1..8))`. Inbound = cross-service callers; outbound follows CALLS
  hops. **Intra-service is an index-time property** (CALLS edges are intra-service
  by construction; the query has no microservice predicate) — the test validates
  the fixture's data, not a query constraint.
- All call `next_actions_hook(...)` (no-op stub until PR-4).

## Tests for PR-JRAG-3a

`tests/test_jrag_traversal_direct.py`:
1. `test_callers_symbol_uses_find_callers`
2. `test_callers_route_service_is_post_filter_with_warning` (`--service` filters
   client-side + emits warning; not pushed down)
3. `test_callees_symbol_uses_find_callees`
4. `test_callers_and_callees_support_include_external` (symmetric)
5. `test_hierarchy_renders_tree_both_directions`
6. `test_implementations_uses_find_implementors`
7. `test_implementations_capability_post_filter`
8. `test_subclasses_uses_find_subclasses`
9. `test_overrides_dispatches_up_via_neighbors_out_overrides`
10. `test_overridden_by_dispatches_down_via_neighbors_in_overrides`
11. `test_dependents_uses_find_injectors`
12. `test_impact_runs_fleet_wide_without_service`
13. `test_impact_service_post_filter_emits_warning`
14. `test_decompose_renders_role_waterfall`
15. `test_flow_outbound_intra_service_on_fixture` (validates fixture CALLS edges)
16. `test_traversal_resolve_ambiguous_stops`
17. `test_traversal_rejects_offset`

## Definition of done (PR-JRAG-3a)

- [ ] 11 direct traversals implemented; `overrides`/`overridden-by` via `neighbors_v2`.
- [ ] `--service` post-filter + warning on `callers` Route and `impact`;
      `--include-external` symmetric; `--capability` post-filter on `implementations`.
- [ ] `--offset` rejected; `flow` intra-service framed as a data property.
- [ ] All named tests green; full suite green.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `feat(cli): jrag direct-backend traversals (PR-JRAG-3a)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Symbol/Route callers + callees-symbol + include-external + route-caller post-filter | `jrag.py` | tests 1–4 pass |
| 2 | hierarchy + implementations(+cap) + subclasses | `jrag.py` | tests 5–8 pass |
| 3 | overrides/overridden-by (neighbors_v2) + dependents | `jrag.py` | tests 9–11 pass |
| 4 | impact (+post-filter) + decompose + flow (data-property framing) | `jrag.py` | tests 12–15 pass |
| 5 | resolve-stop + offset-rejection + next_actions hook | `jrag.py` | tests 16,17 pass |
| 6 | ruff + full suite | repo | clean + green |

---

# PR-JRAG-3b — Compose commands + file inspection

**Goal:** the `neighbors_v2`-compose traversals + `connection` + `outline`/`imports`.

## File-by-file changes

### 1. `java_codebase_rag/jrag.py`
- `callees` — Symbol (handled in 3a); Client → `resolve_v2` gave the node →
  `neighbors_v2([node.id], "out", ["HTTP_CALLS"], limit=limit+1, graph=g)` reaching
  the `:Route`; Producer → `neighbors_v2([...], "out", ["ASYNC_CALLS"])` reaching
  the `:Route` (`kafka_topic`) that consumes this producer's topic. `--include-external`.
- `dependencies` → `neighbors_v2([node.id], "out", ["INJECTS"], limit=limit+1, graph=g)`.
- `connection <microservice>` — first positional is a microservice NAME (resolve-first
  exception; documented loudly). `--inbound` (default), `--outbound`, `--both`;
  `--http-method` (filter routes); `--calls-service`. Inbound: clients/producers
  targeting this service (`list_clients(target_service=...)` + producers whose
  ASYNC_CALLS consumers are external) + `find_route_callers` for hit routes.
  Outbound: this service's clients/producers and the routes/topics they call.
  Render `inbound:`/`outbound:` sections.
- `outline <file>` → `find_symbols_in_file_range(graph=g, filename=file,
  start_line=1, end_line=2**31-1)` (1-based; `<1` returns `[]`). Documented
  unbounded (no `limit`).
- `imports <file>` → tree-sitter Java parse (`ast_java` grammar); walk
  `import_declaration` nodes (cf. `_import_declaration_is_static`, `ast_java.py:905`);
  resolve each imported FQN via `resolve_v2`; render with resolved node refs.
- All call `next_actions_hook(...)` (no-op stub until PR-4).

## Tests for PR-JRAG-3b

`tests/test_jrag_traversal_compose.py`:
1. `test_callees_client_reaches_route_via_http_calls` (Client root → `:Route`)
2. `test_callees_producer_reaches_route_topic_via_async_calls` (Producer root → `:Route` of `kafka_topic`)
3. `test_dependencies_composes_neighbors_out_injects`
4. `test_connection_inbound_lists_external_callers`
5. `test_connection_outbound_lists_this_service_clients`
6. `test_connection_both_default`
7. `test_connection_http_method_filter`
8. `test_connection_first_positional_is_microservice_not_query`
9. `test_outline_lists_file_symbols` (`start_line=1`)
10. `test_outline_empty_for_missing_file` (graceful, not crash)
11. `test_imports_resolves_graph_nodes`
12. `test_outline_and_import_reject_offset_or_document_unbounded`

## Definition of done (PR-JRAG-3b)

- [ ] `callees` Client/Producer + `dependencies` + `connection` + `outline` + `imports`.
- [ ] `callees` Producer target documented as `:Route` (`kafka_topic`);
      `outline` uses `start_line=1`; unbounded documented.
- [ ] All named tests green; full suite green.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `feat(cli): jrag compose traversals + connection + outline/imports (PR-JRAG-3b)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | `callees` Client/Producer (neighbors_v2 → :Route) + `dependencies` | `jrag.py` | tests 1–3 pass |
| 2 | `connection` (positional svc; inbound/outbound/both; http-method) | `jrag.py` | tests 4–8 pass |
| 3 | `outline` (start_line=1) + `imports` (tree-sitter + resolve) | `jrag.py` | tests 9–11 pass |
| 4 | ruff + full suite | repo | clean + green |

---

# PR-JRAG-4 — Orientation + search + `agent_next_actions` + packaging

**Goal:** orientation bundle, semantic search, the new edge→command hint mapper
(wired into all commands), README finalize, token-budget guard, build_parser sentinel.

## File-by-file changes

### 1. `java_codebase_rag/jrag.py` — orientation + search
- `microservices` → `g.microservice_counts()`.
- `map [--service] [--module]` → counts per kind per service/module.
- `conventions [--service]` → dominant roles + framework tallies.
- `overview <microservice|route-path|topic> [--as ...]` → dispatch on type:
  microservice bundle / route flow / topic producers+consumers.
- `search <query>` → build `NodeFilter` from flags; `search_v2(query,
  table=<--table>, hybrid=<--hybrid>, limit=limit+1, offset=args.offset,
  path_contains=<--path-contains>, filter=filter, graph=g)`. `--table all` →
  java+sql+yaml. `--offset` supported. **`--fuzzy` rejected in-handler** →
  `status: error, message="search is semantic; --fuzzy is implicit"` (register
  the flag, do not let argparse exit 2).

### 2. `java_codebase_rag/jrag_hints.py` (new)
- `next_actions(*, root, edge_summary=None, result_edges, graph) -> list[str]` (≤5).
  For each `(label, counts)` in `edge_summary.items()`: emit `jrag <cmd> <fqn>`
  for direction `d` **only when `counts[d] > 0`** (zero-suppression). Label→cmd
  map: CALLS in→callers / out→callees; IMPLEMENTS in→implementations / out→hierarchy;
  EXTENDS in→subclasses / out→hierarchy; INJECTS in→dependents / out→dependencies;
  OVERRIDES out→overrides; OVERRIDDEN_BY in→overridden-by; HTTP_CALLS/ASYNC_CALLS
  out→callees. Composed dot-keys (`DECLARES.*`, `OVERRIDDEN_BY.*`) handled via the
  same label sets `mcp_hints` recognizes; canonical labels from `EDGE_SCHEMA`
  (`java_ontology.py:174`). When `edge_summary is None` (traversal roots), fall
  back to `result_edges` labels. De-dup; cap 5. `<fqn>` from `root.fqn`.
  Import `EDGE_SCHEMA` lazily inside the function (keep `build_parser` pure).

### 3. `java_codebase_rag/jrag_envelope.py` — fill the `next_actions_hook`
- Replace the no-op stub with a call to `jrag_hints.next_actions(...)`; every
  command's existing hook call now populates `envelope.agent_next_actions`
  (omitted when empty).

### 4. `README.md` — full `## jrag — agent CLI` section (replace preview).
### 5. `pyproject.toml` — version bump (release prep; manual publish out of scope).
### 6. `tests/test_jrag_token_budget.py` (new) — token-budget guard (§14).

## Tests for PR-JRAG-4

`tests/test_jrag_orientation.py`:
1. `test_microservices_lists_counts`
2. `test_map_returns_non_empty_counts_per_service`
3. `test_conventions_reports_dominant_roles`
4. `test_overview_microservice_bundle`
5. `test_overview_route_uses_flow`
6. `test_overview_topic_lists_producers_and_consumers`
7. `test_overview_as_overrides_polymorphic_inference`
8. `test_search_returns_ranked_hits`
9. `test_search_hybrid_calls_hybrid_path`
10. `test_search_table_all_runs_three_tables`
11. `test_search_offset_paginates`
12. `test_search_fuzzy_rejected_in_handler_as_status_error`
13. `test_next_actions_valid_runnable_commands_capped_at_5`
14. `test_next_actions_zero_direction_suppressed` (a leaf `INJECTS in:0,out:3` →
    no `jrag dependents` suggestion; `jrag dependencies` suggested)
15. `test_next_actions_covers_composed_dot_keys` (`OVERRIDDEN_BY.DECLARES_CLIENT`)
16. `test_next_actions_falls_back_to_result_edges_when_no_edge_summary`
17. `test_next_actions_omitted_when_empty`
18. `test_build_parser_imports_no_backend_modules` (`sys.modules` has no
    torch/sentence_transformers/mcp_v2 after `build_parser()`)

`tests/test_jrag_token_budget.py`:
19. `test_no_default_output_exceeds_token_ceiling`

## Definition of done (PR-JRAG-4)

- [ ] Orientation + `search` (offset, table all, hybrid, fuzzy-rejected) implemented.
- [ ] `jrag_hints.next_actions` ships; wired into all commands via the hook;
      ≤5; zero-direction suppressed; dot-keys covered; falls back to result_edges.
- [ ] `build_parser` lazy-import sentinel green; README full section; version bumped.
- [ ] Token-budget assertion green.
- [ ] All named tests green; full suite green.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `feat(cli): jrag orientation + search + hints + packaging (PR-JRAG-4)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Orientation (microservices/map/conventions/overview) | `jrag.py` | tests 1–7 pass |
| 2 | `search` over `search_v2` (filter, table all, offset, fuzzy-reject) | `jrag.py` | tests 8–12 pass |
| 3 | `jrag_hints.next_actions` (zero-suppress, dot-keys, fallback) + fill hook | `jrag_hints.py`, `jrag_envelope.py` | tests 13–17 pass |
| 4 | `build_parser` sentinel test + README + version bump | `jrag.py`, `README.md`, `pyproject.toml` | test 18 passes; links resolve |
| 5 | Token-budget guard | `tests/test_jrag_token_budget.py` | test 19 passes |
| 6 | ruff + full suite | repo | clean + green |

---

# PR-JRAG-5 — Agent host integration (install branching, skill, subagent)

**Goal:** `java-codebase-rag install` chooses an MCP or CLI surface; ship a
CLI-flavored skill + subagent; fix the `update` regression for CLI-only installs.

**Key facts (verified):** `HostConfig` (`installer.py:43-73`) is paths-only;
`HOSTS` (`:75-94`) registers claude-code/qwen-code/gigacode; `deploy_artifacts`
(`:558`) and `refresh_artifacts` (`:1049`) hardcode parallel 3-artifact lists;
`detect_configured_hosts` (`:1001`) returns `list[tuple[HostConfig, str]]`
(host, scope) and scans MCP entries only (via `_has_java_codebase_rag_entry`
`:1027`), writing no marker → CLI-only install invisible to `update` (fatal at
`:1312-1315`); `run_update` unpacks the 2-tuple at `:1321`; `resolve_mcp_command`
(`:424`) hard-fails (`SystemExit(2)` at `:447`) when the MCP binary is missing
(non-interactive) and its interactive prompt hardcodes `java-codebase-rag-mcp`
(`:453,467,470`); `_refresh_mcp_config` (`:1167`) calls `resolve_mcp_command` at
`:1189` but is reached only on the MCP manifest path. README (`:150`) says "Pick
one of two options (not both)".

## File-by-file changes

### 1. `java_codebase_rag/installer.py`
- **`Surface = Literal["mcp", "cli"]`**; `HostConfig` unchanged (surface is
  orthogonal). Introduce a `ConfiguredHost` NamedTuple `(host, scope, surface)`;
  `detect_configured_hosts` returns `list[ConfiguredHost]` (read the marker file;
  fall back to the MCP-entry scan + `surface="mcp"` for back-comat with
  pre-marker installs).
- **`ArtifactManifest`** keyed by surface, iterated by both `deploy_artifacts`
  (`:558`) and `refresh_artifacts` (`:1049`):
  - `mcp` → [(mcp-config), (skill: explore-codebase), (agent: explorer-rag-enhanced)]
  - `cli` → [(skill: explore-codebase-cli), (agent: explorer-rag-cli)] (no MCP entry)
- `deploy_artifacts` and `refresh_artifacts` gain `surface: Surface = "mcp"`
  (keyword-only default; preserves back-comat with the 8 direct-call sites in
  `tests/test_installer.py`).
- **`run_update` loop** (`installer.py:1321`): unpack `(host, scope, surface)` and
  pass `surface=surface` to `refresh_artifacts`.
- **`select_surface`** wizard step in `run_install` (`:1454-1575`) at/with
  `select_hosts` (`:1513`). On re-run (`handle_rerun`, `:950`), `select_surface`
  pre-fills from the marker file and offers keep/switch.
- **Marker file** `.java-codebase-rag.hosts`: written at install (host/scope/surface
  set); read by `detect_configured_hosts`.
- **`resolve_mcp_command`** (`:424`) surface-conditional: on `cli`, resolve the
  `jrag` binary and parameterize the interactive prompt (`:453,467,470`) +
  `shutil.which` target; skip the MCP-binary `SystemExit(2)` (`:447`). On `mcp`,
  today's behavior. (`_refresh_mcp_config` is MCP-manifest-only — never reached on
  CLI surface — make that explicit with a comment.)

### 2. Non-interactive flag
- `--surface mcp|cli` (default `mcp`) on the `install` subparser alongside
  `--agent`/`--scope`/`--model` (`java_codebase_rag/cli.py:844-867`).

### 3. CLI skill + subagent (dev-root canonical; sync via PR-JRAG-0a)
- `skills/explore-codebase-cli/SKILL.md` + `agents/explorer-rag-cli.md`. Run
  `scripts/sync_agent_artifacts.py`.

### 4. Tests + docs
- **`tests/test_agent_skills_static.py`**: add `explore-codebase-cli` to
  `EXPECTED_SKILL_DIRS`; gate the MCP-vocabulary static-validation tests
  (tool-ref/kind/edge allowlists) to `explore-codebase` only (they don't apply
  to the CLI skill's shell vocabulary).
- **`tests/test_installer.py`**: the 8 direct `deploy_artifacts`/`refresh_artifacts`
  callers keep working via the `surface="mcp"` default; add CLI-surface cases.
- `AGENTS.md:17-18,59-60`, `skills/README.md:10,13,33-34`, `README.md:174`
  (three-layer section): add the CLI variants.

## Tests for PR-JRAG-5

`tests/test_installer_surface.py`:
1. `test_surface_cli_deploys_cli_skill_and_agent_no_mcp_entry`
2. `test_surface_mcp_reproduces_today_behavior`
3. `test_marker_file_round_trips_host_scope_surface`
4. `test_detect_configured_hosts_returns_configured_host_namedtuple` (3-field)
5. `test_update_after_cli_only_install_refreshes_cli_skill` (no fatal exit)
6. `test_run_update_unpacks_surface_and_passes_to_refresh`
7. `test_resolve_mcp_command_resolves_jrag_on_cli_surface` (no `SystemExit(2)`;
   prompt + which target are `jrag`)
8. `test_deploy_refresh_surface_defaults_to_mcp_back_compat` (existing direct
   callers unchanged)
9. `test_handle_rerun_prefills_surface_from_marker`
10. `test_artifact_manifest_single_source_for_deploy_and_refresh`

Plus: `tests/test_agent_skills_static.py` updated and green.

## Definition of done (PR-JRAG-5)

- [ ] `Surface` + `ArtifactManifest` (both entry points iterate it); `surface="mcp"` default.
- [ ] `ConfiguredHost` NamedTuple; `run_update` unpacks surface; marker file round-trips.
- [ ] `detect_configured_hosts` reads marker → CLI-only install visible to `update`.
- [ ] `resolve_mcp_command` surface-conditional (CLI resolves `jrag`; prompt parameterized).
- [ ] `select_surface` + `--surface` flag; `handle_rerun` pre-fills from marker.
- [ ] CLI skill + subagent shipped (sync via PR-JRAG-0a); `test_agent_skills_static.py` updated.
- [ ] `AGENTS.md`, `skills/README.md`, README three-layer section updated.
- [ ] All named tests + updated `test_installer.py`/`test_agent_skills_static.py` green; full suite green.
- [ ] `.venv/bin/ruff check .` clean.
- [ ] PR title: `feat(install): --surface mcp|cli branching + CLI skill/subagent (PR-JRAG-5)`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | `Surface` + `ConfiguredHost` NamedTuple + `ArtifactManifest`; refactor deploy/refresh (surface kw default) | `installer.py` | tests 8,10 pass; mcp parity |
| 2 | Marker file write + `detect_configured_hosts` reads it (3-field return) | `installer.py` | tests 3,4 pass |
| 3 | `run_update` unpacks surface → refresh | `installer.py` | tests 5,6 pass |
| 4 | `select_surface` wizard + `--surface` flag + `handle_rerun` prefill | `installer.py`, `cli.py` | tests 1,2,9 pass |
| 5 | `resolve_mcp_command` surface-conditional (incl. prompt) | `installer.py` | test 7 passes |
| 6 | Author CLI skill + subagent; sync; update `test_agent_skills_static.py` + docs | `skills/`, `agents/`, tests, `AGENTS.md`, `skills/README.md`, `README.md` | artifacts in sync; tests green |
| 7 | ruff + full suite | repo | clean + green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | PR-JRAG-0b extraction orphans `NodeRef` / breaks `mcp_v2` models | High | `NodeRef` stays in `mcp_v2.py`; only resolve models move; re-export; `test_mcp_v2.py`+`test_mcp_hints.py` gate. |
| 2 | Envelope/resolve-first contract churns after PR-1a | High | PR-1a is dedicated to the frozen contract; later PRs start only after it lands; signature frozen by tests. |
| 3 | `--offset` silently dropped/TypeErrors on traversal/listing | High | `--offset` registered ONLY on `find`/`search`; other commands reject it (test 22 / 17 / 12) and emit "narrow your query". |
| 4 | `jrag search` loads the wrong embedding model | High | Reuse `resolve_operator_config` + `apply_to_os_environ()` (sets `SBERT_MODEL`); test on a YAML-overridden-model fixture. |
| 5 | lancedb EMFILE flakiness | Medium | `raise_fd_limit()` is the first line of `main()`. |
| 6 | Pydantic objects leak into the dict envelope | Medium | `.model_dump()` at the boundary (one place); renderer + `to_json` are dict-only (test 2). |
| 7 | Missing/stale index → opaque error | Medium | `LadybugGraph.exists()` pre-check + ontology-mismatch hint (test 21). |
| 8 | `overrides`/`overridden-by` go the wrong way | High | Both via `neighbors_v2` on the stored `OVERRIDES` edge (out=UP, in=DOWN); tests 9,10. |
| 9 | `find_route_callers` `--service` silently ignored + no truncation | Medium | Client-side post-filter + warning + slice (test 2). |
| 10 | `callees` Producer target mis-typed | Low | Documented as `:Route` (`kafka_topic`); test 2 in PR-3b. |
| 11 | `flow` intra-service claimed as query-enforced | Low | Framed as index-time data property; test validates fixture (PR-3a test 15). |
| 12 | `agent_next_actions` suggests zero-result / wrong commands | Medium | Zero-direction suppression (PR-4 test 14); dot-keys covered; ≤5; fallback to result_edges. |
| 13 | PR-4↔PR-3 wiring leaves traversal commands without `next_actions` | Medium | PR-4 hard-depends on PR-3b; commands leave a `next_actions_hook` from PR-1b. |
| 14 | `jrag --help` slow (torch/sentence_transformers loaded) | Medium | `build_parser()` imports no backend modules; PR-4 `sys.modules` sentinel (test 18). |
| 15 | Snapshot flake on inspect rendering | Low | Inspect renderer sorts all dict keys alphabetically. |
| 16 | CLI-only install strands `update` | High | Marker file + `ConfiguredHost` 3-field return + `run_update` unpacking (PR-5 tests 3–6). |
| 17 | PR-5 breaks `test_agent_skills_static.py` / direct-call installer tests | High | Update `EXPECTED_SKILL_DIRS`; `surface="mcp"` kw default (PR-5 tests 8,10). |
| 18 | Dual-copy artifacts drift when CLI skill/subagent land | Medium | PR-0a first (single-source + drift test); PR-5 depends on it. |
| 19 | Enum kinds (`client_kind`/`producer_kind`/`source_layer`) reject | Medium | Lookup tables (not case conversion); tests 8,9,11. |
| 20 | Token budget regresses as fields accrete | Low | PR-4 token-budget assertion on the fixture. |

# Out of scope

- **Daemon**; negative/absence filters; `diff-impact`/`changed`; `todos`/`unreferenced`;
  `drift`; batch input; `--role` multi-value; `total_count`/"M of N" pagination
  (only +1-fetch); a dedicated `LadybugGraph.client_calls_route`/`producer_calls_topic`
  method (v1 composes `neighbors_v2`); standalone `jrag resolve`; `jrag source`;
  moving operator lifecycle commands into `jrag`; ontology bump/re-index; the
  actual PyPI publish (PR-JRAG-4 bumps version only).
- **`--fuzzy` on `find`** (faithful name-prefix/name-contains fallback). The backend
  `find_by_name_or_fqn` is Symbol-only and exact-only
  (`MATCH (s:Symbol) WHERE s.name=$needle OR s.fqn=$needle`); `NodeFilter` only has
  `fqn_prefix` (FQN STARTS WITH), with no name-prefix/contains anywhere. Implementing
  the brief's exact→prefix→contains fallback would require backend changes that are
  out of scope for the thin-CLI PRs. The `--fuzzy` flag was removed from the `find`
  subparser; tracked as a GitHub follow-up issue for the real implementation.

# Whole-plan done definition

1. `pip install java-codebase-rag` provides `jrag`; `--help` lists orientation /
   locate / listings / traversal / inspection / search / health groups.
2. Every `<query>`-accepting command honors resolve-first (`one`→run, `many`→
   candidates+stop, `none`→`not_found`); raw IDs never required.
3. Every command emits the canonical envelope (`--format json`) + token-lean text
   by default; `truncated` via +1-fetch (or "narrow" for non-offset commands);
   `agent_next_actions` ≤5.
4. `--offset` works only on `find`/`search`; all other commands reject it.
5. `jrag search` loads the YAML-configured embedding model (via `apply_to_os_environ`).
6. `java-codebase-rag install --surface cli` deploys the CLI skill + subagent and
   `update` refreshes them (no fatal exit); `--surface mcp` reproduces today.
7. No ontology bump, no re-index, no cocoindex dependency in the CLI; full suite green.
8. Propose → `propose/completed/`; plan → `plans/completed/` once all PRs land.

# Tracking

- `PR-JRAG-0a`: _pending_
- `PR-JRAG-0b`: _pending_
- `PR-JRAG-1a`: _pending_ (blocked by PR-JRAG-0b)
- `PR-JRAG-1b`: _pending_ (blocked by PR-JRAG-1a)
- `PR-JRAG-2`: _pending_ (blocked by PR-JRAG-1a)
- `PR-JRAG-3a`: _pending_ (blocked by PR-JRAG-1a)
- `PR-JRAG-3b`: _pending_ (blocked by PR-JRAG-3a)
- `PR-JRAG-4`: _pending_ (blocked by PR-JRAG-1a, PR-JRAG-3b)
- `PR-JRAG-5`: _pending_ (blocked by PR-JRAG-0a; soft-depends on PR-JRAG-4)

# Notes

- **Proposal relocation:** `propose/JRAG-CLI-PROPOSE.md` sits at `propose/` root;
  `AGENTS.md` says in-flight proposes live in `propose/active/`. Relocate as part
  of opening PR-JRAG-0a (or when the propose merges).
- **Companion `AGENT-PROMPTS-JRAG-CLI.md`:** not yet written; generate on request
  (one prompt per PR, modeled on `plans/completed/AGENT-PROMPTS-INIT-INCREMENT-PERF.md`).
