---
name: publish-pip
description: Use when the user asks to publish or release the java-codebase-rag pip package to PyPI, bump + publish a new version, or cut a PyPI release. Also use when a manual publish is failing on missing build/twine tooling or an SSL verification error.
disable-model-invocation: true
---

# Publish Pip Package

Manual, from-worktree release of `java-codebase-rag` to PyPI. There is **no CI
release and no git tag** — the version lives only in `pyproject.toml`, and
releases are built + uploaded with the venv `build` / `twine` tools. PyPI uploads
are **permanent**: a version can be yanked but never overwritten, so verify the
version *before* uploading.

## When to use

- user says "publish/release the package", "bump version and publish",
  "cut a PyPI release"
- a prior publish failed partway (tool missing, SSL error, wrong version)

Do **not** use this for installing dev deps (`pip install -e ".[dev]"`) or for
adding a runtime dependency to `pyproject.toml`.

## Prerequisites

- `.venv` at repo root. Use **only** `.venv/bin/python`, `.venv/bin/pip`,
  `.venv/bin/twine` — the system Python shadows the venv CLI.
- `~/.pypirc` present with the PyPI upload token. Never print it; twine reads it
  automatically.

## Workflow

1. **Bump version** — the single source is `pyproject.toml` (`version = "X.Y.Z"`,
   near line 7). Read the current value, increment per request (patch = `Z+1`).
2. **Ensure tooling** — `build` and `twine` are not runtime deps and may be
   absent from a fresh worktree venv:
   ```bash
   .venv/bin/python -m build --version   # real PyPA build, else "No module named build"
   .venv/bin/twine --version
   .venv/bin/pip install build twine      # if either is missing
   ```
   ⚠️ Don't use `import build` to confirm the tool — it can succeed by resolving
   to a local `build/` namespace dir or a stale install even when the PyPA tool
   is absent. Always check via `-m build --version`.
3. **Clean old artifacts** — re-uploading an existing PyPI version is rejected,
   and you must never mix stale files into `dist/`:
   ```bash
   rm -rf dist build *.egg-info
   ```
4. **Sync agent artifacts** — ensure install_data copies match dev source:
   ```bash
   .venv/bin/python scripts/sync_agent_artifacts.py --check
   ```
   If this fails, run `.venv/bin/python scripts/sync_agent_artifacts.py` to sync,
   then commit the changes before publishing.
5. **Build** sdist + wheel:
   ```bash
   .venv/bin/python -m build
   ```
   Expect `dist/java_codebase_rag-<ver>-py3-none-any.whl` and `.tar.gz`.
6. **Verify the built version** before upload (catches a forgotten bump):
   ```bash
   .venv/bin/python -c "import zipfile,glob; w=glob.glob('dist/*.whl')[0]; z=zipfile.ZipFile(w); m=[n for n in z.namelist() if n.endswith('METADATA')][0]; print([l for l in z.read(m).decode().splitlines() if l.startswith('Version')][0])"
   ```
7. **Upload** (permanent — confirm the version is right first):
   ```bash
   .venv/bin/twine upload dist/*
   ```
   twine prints the live URL on success:
   `https://pypi.org/project/java-codebase-rag/<ver>/`.
8. **Verify on PyPI** via the JSON API. ⚠️ Python's `urllib`/`requests` SSL
   verification fails locally (missing CA bundle) — set `SSL_CERT_FILE`:
   ```bash
   CERT=$(.venv/bin/python -c "import certifi; print(certifi.where())")
   SSL_CERT_FILE="$CERT" .venv/bin/python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('https://pypi.org/pypi/java-codebase-rag/json')); print('latest:', d['info']['version'])"
   ```
9. **Commit + push the version bump** so the repo matches what was published
   (commit convention: `bump version to X.Y.Z`). `dist/`, `build/`, and
   `*.egg-info` are gitignored — do not commit them.

## Quick reference

| Step | Command |
|------|---------|
| Bump | edit `pyproject.toml` `version` |
| Tooling | `.venv/bin/pip install build twine` |
| Clean | `rm -rf dist build *.egg-info` |
| Sync | `.venv/bin/python scripts/sync_agent_artifacts.py --check` |
| Build | `.venv/bin/python -m build` |
| Verify wheel | read `Version:` from `dist/*.whl` METADATA |
| Upload | `.venv/bin/twine upload dist/*` |
| Verify live | `SSL_CERT_FILE="$(.venv/bin/python -m certifi)"` + pypi JSON API |
| Commit | `bump version to X.Y.Z` |

## Common mistakes

- **Re-uploading an existing version** → PyPI returns 400. Bump first; clean `dist/`.
- **`import build` succeeds but `python -m build` fails** → `import` resolved to
  a local `build/` namespace dir or stale module, not the PyPA tool. `pip install
  build`, then confirm with `-m build --version`.
- **PyPI verification SSL error** (`CERTIFICATE_VERIFY_FAILED`) →
  `SSL_CERT_FILE=$(.venv/bin/python -c "import certifi;print(certifi.where())")`.
- **Forgot to bump / uploaded the wrong version** → permanent. Always run the
  METADATA version check (step 5) before `twine upload`.
- **Used system `python` / `twine`** → wrong env / missing credentials. Always
  `.venv/bin/`.
- **Left the version bump uncommitted** → repo drifts from PyPI. Commit + push.

## Notes

- Release `0.6.6` (erase fix, PR #348) established this runbook; the gotchas
  above were all hit for real during that publish.
- If you publish from an unmerged feature branch, PyPI will be ahead of `master`
  until the branch merges — call that out.
