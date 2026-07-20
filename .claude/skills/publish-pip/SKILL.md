---
name: publish-pip
description: Use when the user asks to publish or release the java-codebase-rag pip package to PyPI, bump + publish a new version, or cut a PyPI release. Also use when a manual publish is failing on missing build/twine tooling or an SSL verification error.
disable-model-invocation: true
---

# Publish Pip Package

Manual, from-worktree release to PyPI. There is **no CI release and no git tag**
— the version lives only in `pyproject.toml`, and releases are built + uploaded
with the venv `build` / `twine` tools. PyPI uploads are **permanent**: a version
can be yanked but never overwritten, so verify the version *before* uploading.

## Dual publish — both PyPI names, every release

**Every release must be published under BOTH PyPI project names**, in sync, same
version:

- `jrag-cli` — the current name (`[project].name` in `pyproject.toml`).
- `java-codebase-rag` — the legacy name; existing users run
  `pip install -U java-codebase-rag` and must not be stranded.

PyPI project names are permanent and cannot alias each other, so a single upload
only reaches one project. Skipping the legacy name freezes those users at the
last version published there. The full dual-publish procedure is step 10 below.

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
   and you must never mix stale files into `dist/`. Use `find` for `*.egg-info`,
   **not** a bare glob: under zsh (default `NOMATCH`), `rm -rf dist build *.egg-info`
   with no `.egg-info` match aborts the *whole* command, so `dist/` is never
   cleared and stale files leak into the upload:
   ```bash
   rm -rf dist build
   find . -maxdepth 2 -name '*.egg-info' -exec rm -rf {} +
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
   Expect `dist/jrag_cli-<ver>-py3-none-any.whl` and `.tar.gz` (legacy rebuild in step 10 produces `java_codebase_rag-<ver>.*`).
6. **Guard the upload** — assert *every* file in `dist/` matches the version in
   `pyproject.toml` (filename + wheel METADATA). This is the hard stop before a
   permanent upload: it catches a forgotten bump **and** stale files from a prior
   build that cleanup missed (the `0.10.0` release shipped `0.9.7` artifacts
   because of exactly this):
   ```bash
   .venv/bin/python scripts/check_dist_version.py
   ```
   The script reads the target from `pyproject.toml` itself (no `--version`
   arg to get wrong) and exits non-zero if `dist/` is empty, holds a foreign
   version, or the wheel METADATA disagrees. Do not proceed to upload unless it
   prints `✓ dist/ clean`.
7. **Upload `jrag-cli`** (permanent — confirm the version is right first):
   ```bash
   .venv/bin/twine upload dist/*
   ```
   twine prints the live URL on success:
   `https://pypi.org/project/jrag-cli/<ver>/`.
8. **Verify on PyPI** via the JSON API. ⚠️ Python's `urllib`/`requests` SSL
   verification fails locally (missing CA bundle) — set `SSL_CERT_FILE`:
   ```bash
   CERT=$(.venv/bin/python -c "import certifi; print(certifi.where())")
   SSL_CERT_FILE="$CERT" .venv/bin/python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('https://pypi.org/pypi/jrag-cli/json')); print('latest:', d['info']['version'])"
   ```
9. **Commit + push the version bump** so the repo matches what was published
   (commit convention: `bump version to X.Y.Z`). `dist/`, `build/`, and
   `*.egg-info` are gitignored — do not commit them.
10. **Dual publish — upload `java-codebase-rag` too.** `[project].name` in
    `pyproject.toml` is `jrag-cli`, so the legacy name needs a rebuild with the
    name swapped. The import package (`java_codebase_rag`) and entry points are
    unchanged — only the distribution name differs:
    ```bash
    # 1. Swap the project name in-place (do NOT commit this).
    sed -i.bak 's/^name = "jrag-cli"/name = "java-codebase-rag"/' pyproject.toml
    # 2. Clean + rebuild — artifacts will be java_codebase_rag-<ver>.*.
    rm -rf dist build && find . -maxdepth 2 -name '*.egg-info' -exec rm -rf {} +
    .venv/bin/python -m build
    # 3. Guard (still reads version from pyproject — name swap doesn't affect it).
    .venv/bin/python scripts/check_dist_version.py
    # 4. Upload the legacy name.
    .venv/bin/twine upload dist/*
    # 5. Revert the name swap and clean up the backup.
    mv pyproject.toml.bak pyproject.toml && rm -rf dist build
    ```
    Verify the legacy project too:
    ```bash
    SSL_CERT_FILE="$CERT" .venv/bin/python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('https://pypi.org/pypi/java-codebase-rag/json')); print('latest:', d['info']['version'])"
    ```
    Both projects must report the same `<ver>` before the release is considered
    done. If step 4 fails (e.g. version already exists on the legacy name), fix
    and retry — never leave the two projects at different versions.

## Quick reference

| Step | Command |
|------|---------|
| Bump | edit `pyproject.toml` `version` |
| Tooling | `.venv/bin/pip install build twine` |
| Clean | `rm -rf dist build` then `find . -maxdepth 2 -name '*.egg-info' -exec rm -rf {} +` |
| Sync | `.venv/bin/python scripts/sync_agent_artifacts.py --check` |
| Build | `.venv/bin/python -m build` |
| Guard | `.venv/bin/python scripts/check_dist_version.py` (stops upload on mismatch) |
| Upload | `.venv/bin/twine upload dist/*` (then dual-publish — step 10) |
| Verify live | `SSL_CERT_FILE="$(.venv/bin/python -m certifi)"` + pypi JSON API (both names) |
| Commit | `bump version to X.Y.Z` |

## Common mistakes

- **Re-uploading an existing version** → PyPI returns 400. Bump first; clean `dist/`.
- **`rm -rf dist build *.egg-info` doesn't clean anything (zsh)** → with no
  `.egg-info` match, zsh's default `NOMATCH` aborts the *whole* command, so
  `dist/`/`build/` survive and stale files ship in the next upload. The `0.10.0`
  release leaked `0.9.7` artifacts this way. Use the `find`-based cleanup in
  step 3 — and rely on the guard (step 6) as the backstop regardless.
- **`import build` succeeds but `python -m build` fails** → `import` resolved to
  a local `build/` namespace dir or stale module, not the PyPA tool. `pip install
  build`, then confirm with `-m build --version`.
- **PyPI verification SSL error** (`CERTIFICATE_VERIFY_FAILED`) →
  `SSL_CERT_FILE=$(.venv/bin/python -c "import certifi;print(certifi.where())")`.
- **Forgot to bump / stale files in `dist/`** → permanent. Always run the guard
  (step 6) before `twine upload`; it exits non-zero if anything in `dist/`
  doesn't match `pyproject.toml`.
- **Used system `python` / `twine`** → wrong env / missing credentials. Always
  `.venv/bin/`.
- **Left the version bump uncommitted** → repo drifts from PyPI. Commit + push.
- **Forgot the dual publish** → `java-codebase-rag` users stranded at an old
  version. Every release ships under both names (step 10); both must report the
  same version on PyPI. Don't commit the name swap from step 10 — `mv pyproject.toml.bak`
  reverts it.

## Notes

- Release `0.6.6` (erase fix, PR #348) established this runbook; the gotchas
  above were all hit for real during that publish.
- Release `0.10.0` leaked `0.9.7` artifacts to PyPI because zsh's `NOMATCH`
  aborted the cleanup glob. That incident added the `find`-based cleanup (step 3)
  and the `check_dist_version.py` guard (step 6); the guard is the definitive
  defense since it catches stale files no matter how they survived.
- If you publish from an unmerged feature branch, PyPI will be ahead of `master`
  until the branch merges — call that out.
