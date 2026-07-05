# java-codebase-rag

## Python environment

- Use only the repository `.venv/bin/python` for Python commands (repo root).
- Use only `.venv/bin/pip` for package install and dependency commands.
- Do not use system `python`, `python3`, `pip`, or `pip3` for this repo
  unless you have explicitly activated `.venv` and that is what those
  resolve to.
- When running tests, linters, or scripts, invoke the `.venv/bin`
  executables directly.

## Before running tests

- Erase stale manual indexes first — they hijack project-root discovery:
  `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`
- Tests build their own fresh index in a temp dir; never commit one under
  `tests/` (`.gitignore` un-ignores it there, so git won't stop you).


