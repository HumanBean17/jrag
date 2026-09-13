---
name: publish-pip
description: Use when the user asks to publish or release the jrag-cli / java-codebase-rag pip packages to PyPI, cut a PyPI release, or recover a failed/diverged release. Primary path is tag-triggered CI (release.sh → release.yml dual-publish); the manual fallback covers CI-down and PyPI-reconciliation cases.
disable-model-invocation: true
---

# Publish Pip Package

Dual-name PyPI release runbook for this repo. **Every release ships under BOTH
PyPI names** — `jrag-cli` (canonical) and `java-codebase-rag` (legacy shim) — at
the same version; never leave them diverged. PyPI uploads are **permanent**: a
version can be yanked but never overwritten, so the version is verified *before*
any upload.

Two paths, in priority order:

1. **Primary — tag → CI publishes both names.** The maintainer runs
   `scripts/release.sh`; the `release.yml` workflow builds, guards, and
   dual-publishes via OIDC Trusted Publishing, verifies both names, and opens the
   GitHub Release. This is the normal path.
2. **Manual fallback — when CI is down or reconciling a diverged PyPI state.**
   Build + upload both dists from a worktree with the venv `build` / `twine`
   tools. The legacy name comes from the `shim/pyproject.toml` rebuild, NOT a
   `sed` name-swap (that procedure is obsolete).

## Primary release path — tag → CI publishes both names

The maintainer runs the **human-half** orchestrator; CI does the rest.

```bash
.venv/bin/bash scripts/release.sh X.Y.Z
```

`release.sh` bumps root + `shim/pyproject.toml` in lockstep (via
`scripts/bump_version.py --apply`), runs the install-artifact sync gate, commits
`bump version to X.Y.Z`, lays an annotated tag `vX.Y.Z`, and pushes both to
origin (`--atomic`, so the commit and tag land together or not at all). It
builds and uploads nothing itself. `--dry-run` prints the plan without mutating;
`--no-push` commits+tags without pushing; `--skip-sync-check` bypasses the
artifact-sync gate (temp-repo only).

The **CI half** — `.github/workflows/release.yml`, fires on `v*` tags — then:

1. builds the canonical `jrag-cli` dist (`python -m build`),
2. guards it with `scripts/check_dist_version.py --dist dist --pyproject pyproject.toml`,
3. publishes `jrag-cli` to PyPI via OIDC Trusted Publishing (no stored token),
4. builds the shim inside `shim/` (`python -m build` with `working-directory: shim`),
5. guards it with `check_dist_version.py --dist shim/dist --pyproject shim/pyproject.toml`,
6. publishes `java-codebase-rag` (order is fixed: AFTER the canonical dist is
   live, so the shim's `jrag-cli==<ver>` dep pin resolves),
7. verifies BOTH names report the tag's version (fails the job if either is
   missing), and
8. opens a GitHub Release with auto-categorized notes (`.github/release.yml`).

Publish order is fixed: `jrag-cli` first, then the shim — a shim published before
its `jrag-cli==<ver>` dependency would fail to resolve on install.

### One-time prerequisite — OIDC Trusted Publisher on BOTH names

Before the **first** tag-triggered release, an OIDC Trusted Publisher must be
configured on **both** PyPI projects on https://pypi.org:

- `jrag-cli` **and** `java-codebase-rag`
- repo: `HumanBean17/jrag`
- workflow filename: `release.yml`
- environment: `release`

There is no stored API token — Trusted Publishing authenticates the workflow
via OIDC. The first tag-triggered release **fails closed** until both projects
are configured (the `release` environment + `id-token: write` permission are
already on the workflow). Configure both once, then every tag release is
automatic. To rehearse without uploading, run the workflow on
`workflow_dispatch` with `dry_run: true` — it exercises build + guard +
idempotency-check but skips the two uploads, the final verify, and the Release.

## Manual fallback — CI is down / reconciling a diverged PyPI state

Use this only when CI is unavailable or you're reconciling a PyPI state the
workflow can't (e.g. one name published, the other not). The version step uses
`bump_version.py --apply` so root + shim stay in lockstep — **never** a `sed`
name-swap. The legacy name is rebuilt from `shim/pyproject.toml`.

### Prerequisites (manual path only)

- `.venv` at repo root. Use **only** `.venv/bin/python`, `.venv/bin/pip`,
  `.venv/bin/twine` — the system Python shadows the venv CLI.
- `~/.pypirc` present with the PyPI upload token. Never print it; twine reads it
  automatically. (The CI path needs no token — it uses OIDC.)
- `build` and `twine` installed in the venv (not runtime deps; may be absent
  from a fresh worktree venv):
  ```bash
  .venv/bin/python -m build --version   # real PyPA build, else "No module named build"
  .venv/bin/twine --version
  .venv/bin/pip install build twine      # if either is missing
  ```
  ⚠️ Don't use `import build` to confirm the tool — it can succeed by resolving
  to a local `build/` namespace dir or a stale install even when the PyPA tool
  is absent. Always check via `-m build --version`.

### Steps

1. **Bump version (root + shim in lockstep)** — do NOT hand-edit, do NOT swap
   names. `bump_version.py` writes both pyprojects and the shim's `jrag-cli==<ver>`
   pin atomically:
   ```bash
   .venv/bin/python scripts/bump_version.py --check X.Y.Z    # validate first (no writes)
   .venv/bin/python scripts/bump_version.py --apply X.Y.Z
   ```

2. **Sync gate (install artifacts)** — the same gate `release.sh` runs before
   its bump. `bump_version.py` only writes the two pyprojects; it does NOT sync
   the shipped `install_data` copies to dev source. For a fresh release this
   MUST pass before building, or the release ships stale agent artifacts:
   ```bash
   .venv/bin/python scripts/sync_agent_artifacts.py --check
   ```
   If it fails, run `.venv/bin/python scripts/sync_agent_artifacts.py` to sync,
   commit the changes, then re-check. (Skip only when re-uploading an
   already-committed release whose source is unchanged.)

3. **Clean old artifacts** — re-uploading an existing PyPI version is rejected,
   and you must never mix stale files into `dist/`. Use `find` for `*.egg-info`,
   **not** a bare glob: under zsh (default `NOMATCH`), `rm -rf dist build *.egg-info`
   with no `.egg-info` match aborts the *whole* command, so `dist/` is never
   cleared and stale files leak into the upload:
   ```bash
   rm -rf dist build
   find . -maxdepth 2 -name '*.egg-info' -exec rm -rf {} +
   ```

4. **Build + guard + upload `jrag-cli` (canonical dist):**
   ```bash
   .venv/bin/python -m build
   .venv/bin/python scripts/check_dist_version.py          # reads version from pyproject.toml
   .venv/bin/twine upload dist/*                            # permanent — confirm version first
   ```

5. **Build + guard + upload `java-codebase-rag` (legacy shim).** Build runs
   inside `shim/` against `shim/pyproject.toml` — the shim is a metadata-only
   package that depends on `jrag-cli==<ver>`, so its dist files are named
   `java_codebase_rag-<ver>.*` with no name-swap required. Guard the shim
   against the shim's own pyproject (NOT the root's), and run the guard + upload
   from repo root. ⚠️ Inside the `cd shim` subshell the repo-root venv is
   reached via `../.venv/bin/python` (the venv lives at repo root, not `shim/`):
   ```bash
   ( cd shim && rm -rf dist build && ../.venv/bin/python -m build )
   .venv/bin/python scripts/check_dist_version.py --dist shim/dist --pyproject shim/pyproject.toml
   .venv/bin/twine upload shim/dist/*                        # permanent — confirm version first
   ```
   Upload the shim AFTER `jrag-cli` is live, so its `jrag-cli==<ver>` dep pin
   resolves on PyPI (same fixed order as CI).

6. **Verify both names on PyPI** via the JSON API. ⚠️ Python's `urllib`/`requests`
   SSL verification fails locally (missing CA bundle) — set `SSL_CERT_FILE`:
   ```bash
   CERT=$(.venv/bin/python -c "import certifi; print(certifi.where())")
   SSL_CERT_FILE="$CERT" .venv/bin/python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('https://pypi.org/pypi/jrag-cli/json')); print('jrag-cli:', d['info']['version'])"
   SSL_CERT_FILE="$CERT" .venv/bin/python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('https://pypi.org/pypi/java-codebase-rag/json')); print('java-codebase-rag:', d['info']['version'])"
   ```
   Both must report `X.Y.Z` before the release is considered done.

7. **Commit + push the version bump** (if `release.sh` wasn't used) so the repo
   matches what was published (commit convention: `bump version to X.Y.Z`).
   `dist/`, `build/`, and `*.egg-info` are gitignored — do not commit them.
   If `release.sh` already ran this step, the repo is already at the tag and
   this is a no-op.

## Dual-publish policy — both names, every release

**Every release must be published under BOTH PyPI project names**, in sync, same
version:

- `jrag-cli` — the canonical name (`[project].name` in the root `pyproject.toml`).
- `java-codebase-rag` — the legacy name; existing users run
  `pip install -U java-codebase-rag` and must not be stranded. Rebuilt from
  `shim/pyproject.toml` (a metadata-only package depending on `jrag-cli==<ver>`).

PyPI project names are permanent and cannot alias each other, so a single upload
only reaches one project. Skipping the legacy name freezes those users at the
last version published there. **The close-out invariant: both names must report
the same version on PyPI after every release — never leave them diverged.** If
either upload fails (e.g. version already exists on one name), fix and retry —
do not ship a release where the two projects disagree.

## Common mistakes

- **Re-uploading an existing version** → PyPI returns 400. Bump first; clean `dist/`.
- **`rm -rf dist build *.egg-info` doesn't clean anything (zsh)** → with no
  `.egg-info` match, zsh's default `NOMATCH` aborts the *whole* command, so
  `dist/`/`build/` survive and stale files ship in the next upload. The `0.10.0`
  release leaked `0.9.7` artifacts this way. Use the `find`-based cleanup in
  step 3 of the manual fallback — and rely on the `check_dist_version.py` guard
  as the backstop regardless.
- **`import build` succeeds but `python -m build` fails** → `import` resolved to
  a local `build/` namespace dir or stale module, not the PyPA tool. `pip install
  build`, then confirm with `-m build --version`.
- **PyPI verification SSL error** (`CERTIFICATE_VERIFY_FAILED`) →
  `SSL_CERT_FILE=$(.venv/bin/python -c "import certifi;print(certifi.where())")`.
- **Forgot to bump / stale files in `dist/`** → permanent. Always run the guard
  before `twine upload`; it exits non-zero if anything in `dist/` doesn't match
  the pyproject version.
- **Used system `python` / `twine`** → wrong env / missing credentials. Always
  `.venv/bin/` (and `../.venv/bin/` from inside `shim/`).
- **`.venv/bin/python` after `cd shim` → "No such file or directory"** → the
  venv lives at repo root, so inside `shim/` it is `../.venv/bin/python`, not
  `.venv/bin/python`. The shim build subshell uses `../.venv/bin/python -m build`;
  the guard and upload run from repo root with plain `.venv/bin/...`.
- **Skipped the sync gate** → `bump_version.py` writes only the two pyprojects;
  it does not sync `install_data`. Run `sync_agent_artifacts.py --check` before
  building (step 2) or the release ships stale agent artifacts.
- **Guarded the shim against the root pyproject** → the guard reads the project
  name + version from the pyproject you pass it. For the shim build, point it at
  `shim/pyproject.toml` explicitly (`--dist shim/dist --pyproject shim/pyproject.toml`),
  else it looks for `jrag_cli-*` files in a `java_codebase_rag-*` dist and fails.
- **Forgot the dual publish** → `java-codebase-rag` users stranded at an old
  version. Every release ships under both names; both must report the same
  version on PyPI. Never leave them diverged.
- **First tag release failed with an OIDC / permission error** → the Trusted
  Publisher isn't configured on both PyPI projects yet (see the one-time
  prerequisite above). The release fails closed until both are set up.

## Notes

- The **primary** release path is tag-triggered CI (`release.sh` → `release.yml`);
  the manual fallback above exists for CI-down days and PyPI reconciliation, not
  for routine releases.
- The legacy in-place `sed` name-swap dual-publish procedure is **obsolete** —
  the `shim/pyproject.toml` metadata-only package replaces it. Never reintroduce
  a name-swap; the shim rebuild is the supported mechanism for both CI and the
  manual fallback.
- Release `0.6.6` (erase fix, PR #348) established the original manual runbook;
  the gotchas above were all hit for real during that publish.
- Release `0.10.0` leaked `0.9.7` artifacts to PyPI because zsh's `NOMATCH`
  aborted the cleanup glob. That incident added the `find`-based cleanup and the
  `check_dist_version.py` guard; the guard is the definitive defense since it
  catches stale files no matter how they survived.
- If you publish from an unmerged feature branch, PyPI will be ahead of `master`
  until the branch merges — call that out.
