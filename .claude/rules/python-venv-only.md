# Python Environment Rule

- Use only the repository `.venv/bin/python` for Python commands (repo root).
- Use only `.venv/bin/pip` for package install and dependency commands.
- Do not use system `python`, `python3`, `pip`, or `pip3` for this repo unless you have explicitly activated `.venv` and that is what those resolve to.
- When running tests, linters, or scripts, invoke the `.venv/bin` executables directly.
- Examples:
  - `.venv/bin/python -m pytest tests -q`
  - `.venv/bin/ruff check .`
  - `.venv/bin/pip install -r requirements.txt`
