# java-codebase-rag

## Python environment

Use `.venv/bin/python` and `.venv/bin/pip` (repo root) for all Python commands.
Invoke the `.venv/bin` executables directly — never system `python`/`pip`.

## Tests

- Erase stale manual indexes first — they hijack project-root discovery:
  `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`
- Tests build their own fresh index in a temp dir; never commit one under
  `tests/` (`.gitignore` un-ignores it there).
- The full suite is slow. Run only the subset relevant to your change during
  development; run the full suite once, at the end of the task.

## Docs

All files in `docs/` are **operator-facing**. No internal docs yet.

**Operator docs**
- `docs/CONFIGURATION.md` — env vars, project YAML, ontology, brownfield overrides, ignore patterns.
- `docs/JAVA-CODEBASE-RAG-CLI.md` — operator CLI playbook (workflows, exit codes, env alignment).
- `docs/AGENT-GUIDE.md` — agent-facing MCP operating manual (copy-paste into `AGENTS.md`/`CLAUDE.md`).
- `docs/EDGE-NAVIGATION.md` — MCP-traversable edges, directions, dot-key composition.
- `docs/MANUAL-VERIFICATION-CHECKLIST.md` — 7-phase post-index verification.
- `docs/CODEBASE_REQUIREMENTS.md` — assumptions about the target Java repo.
- `docs/PRODUCT-VISION.md` — long-term product direction.
- `docs/paper/paper.pdf` — architecture report (rationale, GPS metaphor, ontology).

**Internal docs** — none yet.

## Shipped artifacts

`skills/` and `agents/` are shipped consumer artifacts — deployed verbatim by
`install`/`update` to the user's agent host. This repo is the source of truth;
never hand-patch deployed copies.
