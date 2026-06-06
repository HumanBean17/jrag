# Requirements.txt Cleanup Design

**Date:** 2026-06-06
**Status:** Approved

## Problem

`requirements.txt` is a raw `pip freeze` dump of all 144 packages (direct + transitive) with exact version pins. This is redundant because `pyproject.toml` already declares direct dependencies with proper version ranges. The frozen file is fragile — updating any dependency requires regenerating the entire file.

Additionally, `pydantic` is imported directly in the codebase but not declared in `pyproject.toml` (it works only as a transitive dependency of `mcp`). Dev tools (`pytest`, `ruff`) are also undeclared.

## Solution

Make `pyproject.toml` the single source of truth. Delete `requirements.txt` entirely.

### Changes

1. **Delete `requirements.txt`**

2. **Add `pydantic` to `dependencies`** — the codebase uses Pydantic v2 APIs (`BaseModel`, `model_validator`, `validate_call`, `TypeAdapter`, `ValidationError`) in `java_codebase_rag/config.py` and elsewhere. Declare it explicitly:
   ```
   "pydantic>=2.0,<3",
   ```

3. **Add `[project.optional-dependencies]` with `dev` group** — for development-only tools:
   ```toml
   [project.optional-dependencies]
   dev = [
       "pytest>=7",
       "ruff>=0.4",
   ]
   ```

### Developer workflow after change

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

One command installs the package (with all declared deps) plus dev tools.

## Scope

- No tool changes (stays with pip + venv)
- No lock file (no CI reproducibility requirements identified)
- No import audit tooling
