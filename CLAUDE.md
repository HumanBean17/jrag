# jrag

## Python environment

Use `.venv/bin/python` and `.venv/bin/pip` (repo root) for all Python commands.
Invoke the `.venv/bin` executables directly — never system `python`/`pip`.

Editable install only. If `jrag`/`java-codebase-rag` serve stale behavior
while pytest passes, run `.venv/bin/pip install -e ".[dev]"` — don't report it.
`tests/conftest.py` enforces this.

## Tests

- Erase stale manual indexes first — they hijack project-root discovery:
  `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`
- Tests build their own fresh index in a temp dir; never commit one under
  `tests/` (`.gitignore` un-ignores it there).
- The full suite is slow. Run only the subset relevant to your change during
  development; run the full suite once, at the end of the task.
- On-disk `.java-codebase-rag*` names (index dir, project YAML, hosts) and
  `JAVA_CODEBASE_RAG_*` env vars are intentionally retained for backward
  compatibility — do not "fix" them.

## Docs

Most files in `docs/` are **operator-facing**. The two flagged below are **internal** (contributor) docs.

**Operator docs**
- `docs/CONFIGURATION.md` — env vars, project YAML, ontology, brownfield overrides, ignore patterns.
- `docs/JRAG-CLI.md` — operator CLI playbook (workflows, exit codes, env alignment).
- `docs/MIGRATION.md` — `java-codebase-rag` → `jrag` rename map (commands, package, untouched on-disk state).
- `docs/AGENT-GUIDE.md` — agent-facing MCP operating manual (copy-paste into `AGENTS.md`/`CLAUDE.md`).
- `docs/EDGE-NAVIGATION.md` — MCP-traversable edges, directions, dot-key composition.
- `docs/MANUAL-VERIFICATION-CHECKLIST.md` — 7-phase post-index verification.
- `docs/CODEBASE_REQUIREMENTS.md` — assumptions about the target Java repo.
- `docs/PRODUCT-VISION.md` — long-term product direction.
- `docs/paper/paper.pdf` — architecture report (rationale, GPS metaphor, ontology).

**Internal docs** (contributors working on this repo)
- `docs/DESIGN.md` — WHAT/WHY: core principles, what's indexed, surfaces, non-goals.
- `docs/ARCHITECTURE.md` — HOW: pipeline, module map, write/read paths, stores, extension points.

## Shipped artifacts

`skills/` and `agents/` are shipped consumer artifacts — deployed verbatim by
`install`/`update` to the user's agent host. This repo is the source of truth;
never hand-patch deployed copies.

## Publishing (PyPI)

Every release is published under **both** PyPI names, in sync, same version:
`jrag-cli` (current, `[project].name` in `pyproject.toml`) and `java-codebase-rag`
(legacy — existing users run `pip install -U java-codebase-rag` and must not be
stranded). PyPI names are permanent and don't alias, so a single upload reaches
only one project. Follow `.claude/skills/publish-pip/SKILL.md` end-to-end,
including the dual-publish step — both projects must report the same version.

