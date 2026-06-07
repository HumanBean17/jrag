# CLI Install Command

## Status
Proposal — not yet implemented.

## Problem Statement

Users installing `java-codebase-rag` face multiple friction points that cause drop-off:

1. **Config file creation** — manually writing `.java-codebase-rag.yml` with correct keys and paths
2. **MCP server registration** — knowing the exact JSON format and file path for their agent host (`.claude.json`, `.qwen/settings.json`, `.gigacode/settings.json`)
3. **Agent/skill deployment** — copying skill files and agent files to host-specific directories with the right structure
4. **Project indexing** — running `init` with correct flags after config is set up
5. **Embedding model** — understanding whether to provide a local path or let it auto-download

These steps are documented across README, CONFIGURATION.md, AGENT-GUIDE.md, and mcp.json.example. Non-technical users give up before completing setup. The target audience includes people who may rarely use a terminal.

**Why now:** the tool is being distributed to a wider audience and the current multi-step manual process is the primary adoption blocker.

## Proposed Solution

### Two new subcommands

```
java-codebase-rag install [--non-interactive] [--agent {claude-code|qwen-code|gigacode}] [--scope {user|project}] [--model {auto|<path>}]
java-codebase-rag update [--force] [--dry-run]
```

- `install` — first-time interactive setup wizard (PR 1)
- `update` — refresh artifacts after `pip install --upgrade` (PR 2)

### Exit codes

| Code | Meaning | User action |
|------|---------|-------------|
| 0 | Success (all stages completed) | None — tool is ready |
| 1 | Partial success | Read the summary. Failed stages are listed with specific errors. Fix the reported issue (e.g. missing entrypoint, permission error) and re-run `install` — it will skip already-completed stages. |
| 2 | Fatal error (no Java files, required flag missing) | Fix the root cause (e.g. run from a Java project, pass `--agent` in non-interactive mode) and re-run. |

### `install` — interactive pipeline (6 stages)

All interactive prompts go through a `prompt()` helper in `java_codebase_rag/installer.py` that dispatches to `questionary` when `sys.stdin.isatty()` is True, or returns defaults when False. This helper is a first-class abstraction — every prompt call goes through it, not an afterthought.

**Stage 1: Java source detection**

Scan cwd recursively for directories containing `.java` files. Detection granularity: **top-level directories only** (immediate children of cwd). If cwd contains `service-a/src/main/java/...` and `service-b/src/main/java/...`, the checklist shows `service-a` and `service-b`, not nested package directories.

If no `.java` files are found anywhere under cwd: print "No Java source files found in `<cwd>`. Run this command from your Java project root." and exit with code 2.

Display as an interactive checklist (via `questionary.checkbox`) with all detected directories checked by default. User can:
- Uncheck individual directories
- Uncheck all, then selectively check some back
- Accept the default (all checked)

Resolution rules:
- **cwd** is always `source_root`
- If the user selects a subset of detected directories, those become `microservice_roots` in `.java-codebase-rag.yml`
- If the user accepts all (default), `microservice_roots` is omitted (the indexer walks the entire tree)

**Stage 2: Embedding model**

Prompt: *"Embedding model? Press Enter for auto-download (~90MB), or type a local path:"*

- Default: `auto` (= `sentence-transformers/all-MiniLM-L6-v2`, downloads on first `init`)
- If user provides a local path, validate it exists. If not found: prompt "Path not found: `<path>`. Continue anyway? (y/n)" — require explicit confirmation before proceeding with a bad path
- Path expansion: support `~`, `$HOME`, relative paths
- When `--model auto` flag is passed (non-interactive), skip this stage entirely

**Stage 3: Agent host selection**

Menu with 3 options:
1. Claude Code (`.claude`)
2. GigaCode (`.gigacode`)
3. Qwen Code (`.qwen`)

This determines the directory prefix for all artifact paths.

**Stage 4: Install scope**

Prompt: *"Install for this project only, or for all projects?"*

- **Project** → artifacts go to `<cwd>/.<host>/` (lives in the project repo)
- **User** → artifacts go to `~/.<host>/` (available globally across projects)

Note: both scopes are fully supported. Claude Code loads skills from both `~/.claude/skills/` (user) and `.claude/skills/` (project). Same for agents. Qwen Code and GigaCode follow the same pattern.

**Stage 5: Artifact deployment**

Deploy 3 artifacts to the resolved host directory:

| Artifact | Source (in pip package) | Destination |
|----------|------------------------|-------------|
| MCP config | generated JSON | Host-specific config file (see table below) |
| Skill | `java_codebase_rag/install_data/skills/explore-codebase/SKILL.md` | `<scope-path>/skills/explore-codebase/SKILL.md` |
| Agent | `java_codebase_rag/install_data/agents/explorer-rag-enhanced.md` | `<scope-path>/agents/explorer-rag-enhanced.md` |

MCP config behavior differs by host and scope:

| Host | Project-scope MCP file | User-scope MCP file | Merge behavior |
|------|----------------------|---------------------|----------------|
| Claude Code | `.mcp.json` | `~/.claude.json` | Read existing JSON → merge into `mcpServers` key → write back. `~/.claude.json` contains other keys (`numStartups`, `userID`, etc.) — preserve them all. |
| Qwen Code | `.qwen/settings.json` | `~/.qwen/settings.json` | Read existing JSON → merge into `mcpServers` key → write back. File may contain `security`, `model`, `$version` keys — preserve them. |
| GigaCode | `.gigacode/settings.json` | `~/.gigacode/settings.json` | Same as Qwen Code (fork, identical format). |

MCP entry to write (same for all hosts):
```json
{
  "java-codebase-rag": {
    "command": "java-codebase-rag-mcp"
  }
}
```

No env vars in MCP config — walk-up discovery resolves source root and index dir from `.java-codebase-rag.yml`.

When files already exist:
- **Skill/agent files**: ask before overwriting. Show file size and mtime. Options: overwrite / skip / abort.
- **MCP config files**: always merge (never overwrite the entire file). If `java-codebase-rag` entry already exists in `mcpServers`, update it in-place. Ask before modifying only if the entry already exists with a different config.

**Post-deploy validation:**

After writing all artifacts, verify that `java-codebase-rag-mcp` is discoverable on PATH by running `shutil.which("java-codebase-rag-mcp")`. If not found:
- Print: "Warning: `java-codebase-rag-mcp` not found on PATH. Your agent host may fail to start the MCP server. Ensure `java-codebase-rag` is installed: `pip install java-codebase-rag`"
- Continue (don't abort) — the entrypoint may work from the agent host's shell even if not found from the installer's shell (e.g. different PATH in GUI launchers vs terminal)

Also verify the target directories are writable before attempting to write. If a directory is not writable:
- Print the specific path and error
- Skip that artifact, continue with others
- Exit with code 1 if any artifact failed to write

**Stage 6: Index + finish**

1. Generate `.java-codebase-rag.yml` from collected answers (source_root, model, microservice_roots)
2. Auto-add `.java-codebase-rag/` to `.gitignore` if not already present:
   - If `.gitignore` doesn't exist: create it
   - If cwd is not a git repo: skip silently (no warning)
   - Check for `.java-codebase-rag` or `.java-codebase-rag/` variants (pattern-aware, not just string-equal)
3. Run existing `init` command internally using the resolved config
   - If index directory already has artifacts (re-run scenario): skip `init` and print "Index already exists. Run `java-codebase-rag reprocess` to rebuild."
4. Print summary:
   - Files written (with paths)
   - Index directory location
   - Agent host configured
   - "Your coding agent is ready. Open a new session to use java-codebase-rag."

### Re-running `install`

When `install` is run in an already-configured project:
- If `.java-codebase-rag.yml` exists: read it and show current values to the user. Offer two options:
  1. **Update** — re-run the pipeline pre-filled with existing values. The user can change individual fields. Existing keys in the YAML that the installer doesn't manage (e.g. `brownfield_overrides`, custom `embedding.device`) are preserved verbatim.
  2. **Start fresh** — overwrite the config from scratch (after confirmation).
- Existing MCP entries are updated in-place (merged, not duplicated)
- Existing skill/agent files trigger overwrite confirmation

### `update` — post-upgrade refresh (PR 2)

Non-interactive by default. Runs after `pip install --upgrade java-codebase-rag`:

1. Detect previously configured agent hosts:
   - Scan project-level: `.mcp.json`, `.qwen/settings.json`, `.gigacode/settings.json`
   - Scan user-level: `~/.claude.json`, `~/.qwen/settings.json`, `~/.gigacode/settings.json`
   - Check each for `java-codebase-rag` entry in `mcpServers`
2. Refresh skill and agent files (overwrite without asking — these are versioned assets from the package)
3. Run `increment` on the index (LanceDB catch-up; graph stays stale until explicit `reprocess`). Print the same warning as `_INCREMENT_WARNING_LINES` about graph staleness.
4. Skip MCP config if the entry already exists and is correct
5. Print what was refreshed

Flags: `--force` (reinstall even if files match), `--dry-run`.

### Non-interactive mode (`install --non-interactive`)

When `--non-interactive` is passed or stdin is not a TTY:
- Use defaults for all prompts
- Requires `--agent` flag (error if missing, exit code 2)
- Defaults to `--scope project` unless overridden
- `--model auto` is the default (skip model prompt)
- No confirmation prompts (CI-friendly)

### Host config mapping

| Host | Dir name | MCP config (project) | MCP config (user) | Skills (project) | Skills (user) | Agents (project) | Agents (user) |
|------|----------|---------------------|-------------------|-----------------|---------------|-----------------|---------------|
| Claude Code | `.claude` | `.mcp.json` | `~/.claude.json` | `.claude/skills/` | `~/.claude/skills/` | `.claude/agents/` | `~/.claude/agents/` |
| Qwen Code | `.qwen` | `.qwen/settings.json` | `~/.qwen/settings.json` | `.qwen/skills/` | `~/.qwen/skills/` | `.qwen/agents/` | `~/.qwen/agents/` |
| GigaCode | `.gigacode` | `.gigacode/settings.json` | `~/.gigacode/settings.json` | `.gigacode/skills/` | `~/.gigacode/skills/` | `.gigacode/agents/` | `~/.gigacode/agents/` |

**Platform notes:**
- All paths use `pathlib.Path` — no hardcoded separators. Works on macOS, Linux, and Windows.
- `~` is expanded via `Path.home()`, not shell expansion.
- Writability is checked before each write attempt. Permission errors are caught and reported per-artifact (exit code 1 for partial failure).

### Implementation stack

- **Interactive prompts**: `questionary` (new dependency, depends on `prompt_toolkit`) for checkboxes, menus, and confirm dialogs. All calls go through a `prompt()` helper in `installer.py` that checks `sys.stdin.isatty()` and falls back to default values when False.
- **Styling/output**: `rich` (explicit dependency in `pyproject.toml`) for progress display, tables, and summary formatting.
- **Shipped artifacts**: Skill and agent files live at `java_codebase_rag/install_data/skills/` and `java_codebase_rag/install_data/agents/` inside the package directory. Included via `package_data` in `pyproject.toml`. The build process copies them from the repo-root `skills/` and `agents/` directories.
- **New module**: `java_codebase_rag/installer.py` (host config mapping, prompt helper, artifact deployment, MCP merge logic)
- **New subcommand handler**: `_cmd_install()` and `_cmd_update()` in `cli.py`
- **New dependencies**: `questionary>=2.0`, `rich>=13.0` added to `pyproject.toml` `dependencies`

## Scope

**In scope (PR 1 — `install`):**
- New `install` subcommand with 6-stage interactive pipeline
- New `java_codebase_rag/installer.py` module with `prompt()` TTY-abstraction helper
- Move skill/agent files into `java_codebase_rag/install_data/` and register as `package_data`
- Non-interactive mode with `--non-interactive` and flag overrides
- Per-host MCP config merge logic (handles `~/.claude.json` with other keys, `settings.json` with other sections)
- Host config mapping for Claude Code, Qwen Code, GigaCode
- `.gitignore` auto-update (create if missing, skip if not a git repo, pattern-aware check)
- YAML config generation from installer answers
- Internal `init` invocation (skip if index already exists)
- Re-run detection (existing `.java-codebase-rag.yml`)

**In scope (PR 2 — `update`):**
- New `update` subcommand
- Host detection from both project-level and user-level config files
- Artifact refresh logic
- `--force` and `--dry-run` flags
- Graph staleness warning after `increment`

**Explicitly out of scope:**
- Claude Desktop support (different config location — can be added later)
- IDE-specific integrations (VS Code, JetBrains)
- Docker/container setup
- Python version checking (the user already installed the package)
- Automatic `pip install`
- `microservice_roots` advanced configuration (basic top-level detection in stage 1; manual YAML editing for complex multi-service layouts)

## Schema / Ontology / Re-index impact
- Ontology bump: not required
- Re-index required: no
- Config/tool surface changes: two new subcommands (`install`, `update`); existing commands unchanged

## Tests / Validation

**PR 1 tests:**
- Unit: host config mapping produces correct paths for each host × scope combination
- Unit: YAML generation from installer answers matches expected `.java-codebase-rag.yml` shape
- Unit: MCP JSON merge: adds `java-codebase-rag` to empty `mcpServers`
- Unit: MCP JSON merge: adds to existing `mcpServers` with other servers (preserves them)
- Unit: MCP JSON merge: updates existing `java-codebase-rag` entry without touching other keys
- Unit: MCP JSON merge: preserves non-`mcpServers` keys in `~/.claude.json` and `settings.json`
- Unit: artifact file writing with overwrite confirmation logic
- Unit: `.gitignore` update: creates file if missing, appends if not present, skips if already present (including variant patterns)
- Unit: `.gitignore` update: skips silently if not a git repo
- Unit: `prompt()` helper dispatches to `questionary` when TTY, returns defaults when not
- Unit: re-run detection: existing `.java-codebase-rag.yml` triggers informative message
- Unit: empty cwd (no `.java` files) exits with code 2
- Unit: `source_root` is always cwd; subset of directories → `microservice_roots`
- Unit: post-deploy validation detects missing `java-codebase-rag-mcp` on PATH
- Unit: model path validation prompts for confirmation when path doesn't exist
- Unit: re-run "update" preserves unmanaged YAML keys (e.g. `brownfield_overrides`)
- Unit: permission error on target directory is caught and reported, other artifacts continue
- Integration: `install --non-interactive --agent claude-code` from bank-chat fixture → verify files written, MCP config valid, `init` succeeds, index is queryable

**PR 2 tests:**
- Unit: host detection scans both project-level and user-level paths
- Unit: artifact refresh detects and updates stale files
- Integration: install then update cycle

## Open Questions ([TBD])

1. **Existing MCP registration update** — Should the installer detect and update existing `java-codebase-rag` MCP entries in-place? Recommended: yes, merge/update the existing entry rather than duplicating. (Implemented as per-host deep merge described in Stage 5.)
2. **User-scope with no project config** — If the user picks "user" scope, MCP registration goes global but `.java-codebase-rag.yml` is still written to cwd. If they later open a different project without a config file, the globally registered MCP server starts but walk-up discovery finds no config. Recommended: user-scope install prints a note explaining that each Java project needs its own `.java-codebase-rag.yml` (the installer creates one in cwd, but other projects need one too).

## Out of scope

- Claude Desktop config editing (different location, different format — future work)
- IDE-specific integrations (VS Code, JetBrains extensions)
- Docker / containerized setup
- Python version checking or virtual environment creation
- `microservice_roots` advanced configuration (basic detection only; manual YAML editing for complex multi-service layouts)
- `reprocess` / `erase` orchestration (user runs these manually)

## Sequencing / Follow-ups

- **PR 1**: `install` subcommand (new module, CLI handler, package data, prompt helper, tests)
- **PR 2**: `update` subcommand (host detection, artifact refresh, tests)
- **Future**: Claude Desktop support, IDE integrations, `microservice_roots` guided setup
