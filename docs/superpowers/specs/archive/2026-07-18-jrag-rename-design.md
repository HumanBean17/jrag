# `jrag` rename — `java-codebase-rag` → `jrag` (external surfaces only, with backward compatibility)

## 1. Overview

`jrag` becomes the canonical brand and command for this tool. Every existing
user — regardless of when they installed, and whether they invoke the operator
CLI, the agent CLI, or the MCP server — keeps working without manual
intervention. The rename is **external surfaces only**: the internal Python
module (`java_codebase_rag`) and all on-disk persisted state
(`.java-codebase-rag*` files/dirs, `JAVA_CODEBASE_RAG_*` env vars) are
deliberately left untouched.

The hard external constraint that shapes this design: **the PyPI name `jrag` is
already taken by a third party** (Cormac Rynne, "Convert json to string for RAG
applications", v0.0.1). We cannot publish a package named `jrag`. The resolution
relies on the fact that the **pip package name, the console-script command name,
and the import-module name are three independent things** — so we publish under
an available package name (`jrag-cli`) while still shipping a `jrag` command and
keeping the `java_codebase_rag` import module.

## 2. Goals

- `jrag` is the single canonical command (operator + agent verbs unified) and
  `jrag-mcp` is the canonical MCP server command.
- `pip install jrag-cli` is the canonical install path; it provides the `jrag`
  and `jrag-mcp` commands.
- Existing users who run `pip install -U java-codebase-rag` land on a shim that
  transparently pulls in `jrag-cli`; their `java-codebase-rag` and
  `java-codebase-rag-mcp` commands keep resolving.
- Existing on-disk indexes (`.java-codebase-rag/`), configs
(`.java-codebase-rag.yml`), markers (`.java-codebase-rag.hosts`), and env vars
(`JAVA_CODEBASE_RAG_*`) load unchanged — no re-index, no config edit, no
re-install.
- Branding (README, docs, GitHub URLs, deployed skills/agents) reflects `jrag`.

## 3. Background (current state, from the code map)

The rename is already partially underway. The current naming surfaces:

| Surface | Current name | Location |
|---|---|---|
| PyPI / dist package | `java-codebase-rag` | `pyproject.toml` `[project].name`; wheel `java_codebase_rag-0.11.2` |
| Python import module | `java_codebase_rag` | `src/java_codebase_rag/` (~168 internal refs) |
| CLI — operator | `java-codebase-rag` | entry point → `java_codebase_rag.cli:_console_script_main`; verbs: `init`, `install`, `update`, `increment`, `reprocess`, `erase`, `meta`, `tables`, `diagnose`, `analyze-pr`, `unresolved` |
| CLI — agent | `jrag` (already exists) | entry point → `java_codebase_rag.jrag:_console_script_main`; 32 verbs (`find`, `search`, `inspect`, `callers`, `callees`, `hierarchy`, `watch`, …) |
| CLI — MCP server | `java-codebase-rag-mcp` | entry point → `java_codebase_rag.mcp.server:main` |
| On-disk index dir | `.java-codebase-rag/` | referenced in `config.py`, `installer.py`, `graph/path_filtering.py`, `graph/build_ast_graph.py` |
| On-disk config | `.java-codebase-rag.yml/.yaml` | `config.py` `YAML_CONFIG_FILENAMES` |
| On-disk marker | `.java-codebase-rag.hosts` | `installer.py` `_MARKER_FILE_NAME` |
| On-disk ignore | `.java-codebase-rag/ignore` | `graph/path_filtering.py` |
| Env vars | `JAVA_CODEBASE_RAG_INDEX_DIR`, `JAVA_CODEBASE_RAG_SOURCE_ROOT`, … | `mcp.json.example`, `config.py` |
| GitHub repo | `HumanBean17/java-codebase-rag` (canonical legacy) **and** `HumanBean17/jrag` (already exists, pushed 2026-07-17, identical description) | `pyproject.toml` `[project.urls]` |
| Docs | `docs/JAVA-CODEBASE-RAG-CLI.md` + many `docs/*.md` | `docs/` |
| Consumer artifacts | `skills/`, `agents/` (dev source) mirrored to `src/java_codebase_rag/install_data/` | repo root + `install_data/`; sync via `scripts/sync_agent_artifacts.py` |

Verified facts that constrain the design:

- **PyPI `jrag` is taken** (third party, no console scripts — so the `jrag`
  *command* is safe; only the *package* name is unavailable).
- **PyPI `java-codebase-rag` is ours** (HumanBean17, 33 releases up to 0.11.2).
- **15 alternative names are free** (`jrag-cli`, `jrag-java`, `jrag-code`, …).
- **Operator and agent verb sets are fully disjoint** — no collisions, so a
  unified `jrag` dispatcher can route by subcommand unambiguously.
- **`HumanBean17/jrag` already exists on GitHub** and is the canonical repo
  going forward (confirmed); only URL pointers need updating.
- **No PyPI publish workflow** — releases are manual (local build → `twine`);
  only `.github/workflows/test.yml` exists, so CI is unaffected.
- **`_version.py` reads the dist version via `importlib.metadata.version`**
  with `_PACKAGE = "java-codebase-rag"` hardcoded — this breaks on rename and
  must move to `"jrag-cli"`.
- **`tests/conftest.py`'s editable-install check verifies the import path**
  (`from java_codebase_rag import jrag`), not the dist name — so it survives the
  package rename; only its prose mentions need updating.

## 4. Design

### 4.1 Three governing decisions

| Axis | Decision |
|---|---|
| Depth | External surfaces only. Internal module stays `java_codebase_rag`. No on-disk or env-var migration. |
| Distribution | Publish `jrag-cli` as the canonical package; `java-codebase-rag` becomes a version-locked shim depending on `jrag-cli`. |
| CLI surface | Unify under one `jrag` command (operator + agent verbs) + `jrag-mcp`; old command names become aliases. |

### 4.2 Two packages, one repo

The repo keeps its current single-source layout (code in `src/java_codebase_rag/`).
The distribution is split into two build targets:

| Package | Location | Contents |
|---|---|---|
| `jrag-cli` (canonical) | repo-root `pyproject.toml` (renamed from `java-codebase-rag`) | All code; all four console scripts; version `0.12.0`. Internal import module stays `java_codebase_rag` (`[tool.setuptools.packages.find]` `where=["src"]`). |
| `java-codebase-rag` (shim) | new `shim/pyproject.toml` | No code, no scripts. `dependencies = ["jrag-cli==0.12.0"]` (exact pin). A README noting the rename. Version tracks `jrag-cli`. |

The internal import module stays `java_codebase_rag`. Installing `jrag-cli`
provides a package named `jrag-cli` that ships the `java_codebase_rag` import
module plus the four console scripts. Package name, command name, and import
module name remain cleanly decoupled.

### 4.3 CLI surface — entry-point contract

`jrag-cli`'s `[project.scripts]`:

```toml
[project.scripts]
jrag                  = "java_codebase_rag.cli_dispatch:_console_script_main"   # unified: agent + operator verbs
jrag-mcp              = "java_codebase_rag.mcp.server:main"                     # MCP server (new canonical)
java-codebase-rag     = "java_codebase_rag.cli_dispatch:_console_script_main"   # compat alias → unified
java-codebase-rag-mcp = "java_codebase_rag.mcp.server:main"                     # compat alias → MCP
```

A new `cli_dispatch` module owns routing. Because operator and agent verb sets
are disjoint, routing by subcommand is unambiguous. **Contract:**

- `jrag <operator-verb>` behaves byte-identically to today's
  `java-codebase-rag <operator-verb>`.
- `jrag <agent-verb>` behaves identically to today's `jrag <agent-verb>`.
- `jrag --help` lists all verbs from both modules.
- `jrag --version` reports the version (see 4.6).
- The legacy alias `java-codebase-rag` routes through the **same** unified
  dispatcher (per the entry-point table), so `java-codebase-rag <verb>` ≡
  `jrag <verb>`. For backward compatibility the only requirement is that
  operator verbs behave identically to before — which holds. Gaining the agent
  verbs under the alias is a harmless superset, not a regression.

**Implementation choice deferred to the plan** (contract holds either way):
either (a) build one merged `argparse` parser registering subcommands from both
`cli.build_parser` and `jrag.build_parser` behind a shared global-flags layer
(cleanest `--help`, but the two parsers have independent globals that must be
reconciled), or (b) a thin dispatcher that parses shared globals then delegates
to one of the two existing `main`s.

### 4.4 Deprecation nudge (not a break)

When invoked through a legacy alias (`java-codebase-rag`,
`java-codebase-rag-mcp`), emit one line to **stderr**:

> `jrag: 'java-codebase-rag' is now 'jrag'; this alias continues to work. Set JRAG_NO_DEPRECATION=1 to silence.`

Gated so it never pollutes automation: suppressed when `JRAG_NO_DEPRECATION=1` is
set **or** stderr is not a TTY. The MCP alias runs non-interactive, so it is
auto-silent. Direct `jrag` / `jrag-mcp` invocations never emit it. No removal
date is announced — aliases persist indefinitely.

### 4.5 On-disk artifacts & env vars — no rename

The string constants stay exactly as-is; no code reads or writes `.jrag*`, ever:

- `config.py`: `YAML_CONFIG_FILENAMES`, the `.java-codebase-rag/` index-dir
  default, `CONFIG_SOURCE_FILENAME`.
- `installer.py`: `_MARKER_FILE_NAME`, the `.gitignore` entry it writes.
- `graph/path_filtering.py`: `.java-codebase-rag/ignore` lookups.
- Env vars `JAVA_CODEBASE_RAG_INDEX_DIR` / `JAVA_CODEBASE_RAG_SOURCE_ROOT` (and
  any other `JAVA_CODEBASE_RAG_*`): **unchanged**. These live in users' MCP
  configs and shell profiles; renaming them is the same risk class as on-disk
  migration and is rejected for the same reason.

Accepted tradeoff: a `ls -a` shows `.java-codebase-rag/` on a tool branded
`jrag` — a cosmetic mismatch, zero functional impact.

### 4.6 Code references that must move

| File | Change |
|---|---|
| `_version.py` | `_PACKAGE = "java-codebase-rag"` → `"jrag-cli"` (else `--version` reports `unknown`) |
| `pyproject.toml` | `[project].name` → `jrag-cli`; `[project.urls]` → `HumanBean17/jrag`; `[project.scripts]` per 4.3; keywords/description |
| `conftest.py` | Prose only (docstring mentions of command names); the editable check itself is unchanged |
| `scripts/check_dist_version.py` + `tests/package/test_check_dist_version.py` | dist-name reference → `jrag-cli` |
| `tests/package/test_version_flag.py` | verify/repair any hardcoded dist name |
| new `shim/pyproject.toml` | the shim package (4.2) |

No other source changes for naming — the `java_codebase_rag` import path is
untouched, so the ~150 other references (imports, test files named
`test_java_codebase_rag_*` and `test_jrag_*`) keep working as-is.

### 4.7 Docs, repo, and consumer-artifact rebrand

- `README.md`: headline `# jrag`; install → `pip install jrag-cli`; examples →
  `jrag …`; a "Renamed from java-codebase-rag" callout (old commands and old
  package still work).
- `docs/JAVA-CODEBASE-RAG-CLI.md` → renamed to `docs/JRAG-CLI.md`; content
  rebranded.
- Remaining `docs/*.md` (`CONFIGURATION`, `AGENT-GUIDE`, `EDGE-NAVIGATION`,
  `MANUAL-VERIFICATION-CHECKLIST`, `CODEBASE_REQUIREMENTS`, `PRODUCT-VISION`,
  `DESIGN`, `ARCHITECTURE`): rebrand prose + command examples.
- Project `CLAUDE.md`: update the doc map (currently lists
  `docs/JAVA-CODEBASE-RAG-CLI.md`) and CLI references; note `.java-codebase-rag`
  on-disk names are intentionally retained.
- `mcp.json.example`: server key → `"jrag"`, command → `jrag-mcp` (with a
  comment that `java-codebase-rag-mcp` still works); env-var keys stay
  `JAVA_CODEBASE_RAG_*`.
- `[project.urls]` Homepage / Repository / Issues → `https://github.com/HumanBean17/jrag`.
- Consumer artifacts: rebrand command references in the four dev-source files
  (`skills/explore-codebase/SKILL.md`, `skills/explore-codebase-cli/SKILL.md`,
  `agents/explorer-rag-cli.md`, `agents/explorer-rag-enhanced.md`), then run
  `scripts/sync_agent_artifacts.py` to propagate into
  `src/java_codebase_rag/install_data/`. Already-deployed copies on users'
  machines keep working via the old command aliases; the next `jrag install` /
  `jrag update` redeploys the rebranded versions.
- User-visible strings sweep: error messages, hints, stdout banners in code that
  say "java-codebase-rag" → "jrag" **where displayed**. Internal identifiers and
  the `java_codebase_rag` module path untouched.

## 5. Error handling

- **`cli_dispatch` unknown verb**: print a clear error listing the nearest valid
  verbs and exit non-zero (mirror existing argparse behavior).
- **Shim install failure** (if a user installs the shim before `jrag-cli` is on
  PyPI): pip's standard "could not find jrag-cli" error. Mitigated by publish
  ordering (7.1) — the shim is never published before `jrag-cli`.
- **Stale-script upgrade**: if upgrading `java-codebase-rag` 0.11.2 → 0.12.0
  (shim) leaves orphaned or missing script files, document
  `pip install --force-reinstall jrag-cli` as recovery.
- **Wrong-package trap**: `pip install jrag` installs the third-party library.
  Mitigation: never document `pip install jrag`; `jrag-cli` description/keywords
  lead with "jrag (formerly java-codebase-rag)".

## 6. Testing

- **`cli_dispatch` unit tests**: each operator verb routes to `cli` main; each
  agent verb routes to `jrag` main; unknown verb errors; `jrag --help` lists all
  verbs from both; legacy alias emits the deprecation line, suppressible via
  `JRAG_NO_DEPRECATION=1`, silent when stderr is not a TTY.
- **Entry-point tests**: all four console scripts resolve to the declared
  targets.
- **Behavior regression**: existing `java-codebase-rag <verb>` and `jrag <verb>`
  outputs are byte-identical pre/post rename (extend existing golden tests under
  `tests/package/`).
- **Package-shape tests** (new, under `tests/package/`): the `jrag-cli` wheel
  ships the `java_codebase_rag` module + `install_data`; the `java-codebase-rag`
  shim wheel ships **no** modules and its `requires_dist` is exactly
  `jrag-cli==<ver>`.
- **Version test**: `--version` reports `0.12.0` (not `unknown`) under the
  `jrag-cli` editable install.
- The full existing `tests/` suite runs unchanged (internal module name
  unchanged).

## 7. Rollout, versioning & communications

### 7.1 Versioning

- `jrag-cli` debuts at `0.12.0` (minor bump; additive/backward-compatible, not a
  behavior break).
- `java-codebase-rag` shim also at `0.12.0`, `dependencies = ["jrag-cli==0.12.0"]`
  (exact pin). Both bump in lockstep forever.

### 7.2 Publish order (load-bearing)

1. Land all code/doc/artifact changes; bump to `0.12.0`.
2. Build `jrag-cli-0.12.0` wheel + sdist, and `java-codebase-rag-0.12.0` shim
   wheel + sdist.
3. **Publish `jrag-cli` to PyPI first.**
4. **Then publish the `java-codebase-rag` shim** (depends on `jrag-cli`, which
   must already exist).
5. Verify end-to-end (§6).
6. Update `[project.urls]`/doc links to `HumanBean17/jrag`; tag `v0.12.0`;
   GitHub release notes carry the migration note.

### 7.3 Migration communications

- PyPI `jrag-cli`: description "jrag (formerly java-codebase-rag) — …".
- PyPI `java-codebase-rag` shim: description "Renamed to jrag-cli; this package
  only depends on jrag-cli. New setups: `pip install jrag-cli`."
- README callout + a short `docs/MIGRATION.md` (or README section): old→new
  command map, old→new pip name, and the explicit reassurance that indexes,
  configs, and env vars are untouched.
- GitHub release notes for `v0.12.0`.

## 8. Non-goals

- **Renaming the internal Python module** (`java_codebase_rag` → `jrag`).
  `import jrag` is explicitly not a goal. No rewrite of the ~168 internal
  references.
- **Migrating on-disk artifacts** to `.jrag/`, `.jrag.yml`, `.jrag.hosts`, or
  reading them as aliases. The old on-disk names are read and written forever.
- **Renaming env vars** `JAVA_CODEBASE_RAG_*` → `JRAG_*`.
- **Acquiring the `jrag` PyPI name** from its current owner. Best-effort outreach
  may happen out-of-band, but the design does not depend on it.
- **Removing the legacy command aliases.** No removal date; they persist.
- **Adding a PyPI publish workflow.** Releases remain manual.

## 9. Verified during planning

- PyPI `jrag` is taken (third party, no console scripts); `java-codebase-rag` is
  ours; 15 alternative names are free.
- Operator vs agent verb sets are disjoint (no routing collisions).
- `HumanBean17/jrag` GitHub repo exists and is canonical.
- `_version.py` dist-name lookup is the one code path that breaks on rename.
- `conftest.py` editable check (import-path based) survives the rename.
- No publish workflow exists; CI is test-only.

## 10. Known limitations & follow-ups

- The install command (`pip install jrag-cli`) does not match the bare brand
  (`jrag`) — an accepted consequence of the taken PyPI name. Documented
  prominently.
- Cosmetic on-disk mismatch (`.java-codebase-rag/` under a `jrag` brand).
- If the `jrag` PyPI name is ever acquired, a future spec can republish the
  canonical package as `jrag` and retire `jrag-cli` (with `jrag-cli` becoming a
  shim) — the decoupled naming makes this a low-risk future swap.
- Consolidating the legacy `HumanBean17/java-codebase-rag` repo (archive vs
  delete) is left to the maintainer's discretion.

## TL;DR

Rename the tool's external brand to `jrag` without touching internals or user
state. Publish the code as a new PyPI package **`jrag-cli`** (the bare name
`jrag` is taken by a third party), which ships a unified **`jrag`** command
(merging today's separate operator and agent CLIs — their verb sets don't
overlap) plus **`jrag-mcp`**, and keeps **`java-codebase-rag`** /
**`java-codebase-rag-mcp`** as working aliases. The old **`java-codebase-rag`**
PyPI package becomes a shim that depends on `jrag-cli`, so `pip install -U
java-codebase-rag` transparently upgrades existing users. The internal module
`java_codebase_rag`, all `.java-codebase-rag*` on-disk files, and all
`JAVA_CODEBASE_RAG_*` env vars are left exactly as-is — zero re-index, zero
config edits. Bump to `0.12.0`; publish `jrag-cli` before the shim; rebrand
README/docs/GitHub URLs/consumer artifacts. Backward-compat guarantee: an
existing user runs one `pip install -U java-codebase-rag` and everything they
had — index, config, commands, MCP registration — keeps working.
