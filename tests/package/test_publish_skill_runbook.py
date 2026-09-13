"""Text-grep tests for the ``publish-pip`` skill runbook.

``.agents/skills/publish-pip/SKILL.md`` is the maintainer's release runbook for
this repo. It was rewritten from a purely-manual ``sed -i`` name-swap procedure
to reflect the tag-triggered CI release pipeline:

  - **Primary release path:** the maintainer runs ``scripts/release.sh``, which
    bumps root + shim pyprojects in lockstep, runs the install-artifact sync
    gate, commits ``bump version to X.Y.Z``, and pushes an annotated tag
    ``vX.Y.Z``; the ``release.yml`` workflow then builds, guards, and publishes
    BOTH PyPI names via OIDC Trusted Publishing, verifies both report the tag's
    version, and opens the GitHub Release.
  - **Manual fallback (demoted):** ``shim/pyproject.toml`` rebuilds the legacy
    ``java-codebase-rag`` name — NO ``sed`` name-swap.
  - **One-time prerequisite:** an OIDC Trusted Publisher must be configured on
    both ``jrag-cli`` and ``java-codebase-rag`` on pypi.org before the first
    tag-triggered release.
  - **Dual-publish policy (kept):** both names must report the same version.

These are plain substring-grep tests: they read the skill's markdown as text and
assert substrings present/absent with ``in`` / ``not in``, pinning the runbook's
shape against regression to the stale manual-only procedure.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _skill_path() -> Path:
    """Locate the publish-pip runbook (issue: rename fallout broke CI).

    The ``claude -> agents`` rename (f51b3d4) moved the skill to
    ``.agents/skills/publish-pip/``. A dev checkout may still carry a
    ``.claude`` symlink to ``.agents`` (see .gitignore) — resolve the current
    location first and fall back to the legacy path so both layouts work.
    """
    primary = REPO_ROOT / ".agents" / "skills" / "publish-pip" / "SKILL.md"
    if primary.is_file():
        return primary
    return REPO_ROOT / ".claude" / "skills" / "publish-pip" / "SKILL.md"


SKILL = _skill_path()


def _text() -> str:
    """Read the skill markdown as text (raises a clear error if the file is missing)."""
    return SKILL.read_text(encoding="utf-8")


def test_primary_path_is_tag_to_ci() -> None:
    """The primary release path is the tag-triggered CI workflow via ``release.sh``.

    The runbook names ``scripts/release.sh`` as the maintainer's entry point and
    frames the CI workflow (fired on tag push) as the primary release path — not
    a manual build+upload.
    """
    text = _text()
    assert "scripts/release.sh" in text, (
        "primary path must reference scripts/release.sh (the human-half orchestrator)"
    )
    assert "release.yml" in text, (
        "primary path must name the release.yml workflow (the CI half)"
    )
    assert "tag" in text.lower(), "runbook must reference the tag trigger"
    assert "primary" in text.lower(), (
        "runbook must frame CI as the primary path (not manual-only)"
    )


def test_no_stale_sed_nameswap() -> None:
    """The stale ``sed -i`` name-swap procedure is gone.

    The old dual-publish step swapped ``name = "jrag-cli"`` to
    ``name = "java-codebase-rag"`` in-place with ``sed -i.bak`` and rebuilt — a
    procedure made obsolete by the metadata-only ``shim/pyproject.toml``. Neither
    the ``sed -i`` command nor the ``name = "jrag-cli"`` swap literal may appear.
    """
    text = _text()
    assert "sed -i" not in text, (
        "stale sed -i name-swap procedure must be removed (the shim replaces it)"
    )
    assert 'name = "jrag-cli"' not in text, (
        "stale name-swap target literal 'name = \"jrag-cli\"' must be gone"
    )


def test_manual_fallback_uses_shim() -> None:
    """The manual fallback builds the shim in ``shim/`` — no root name-swap.

    The corrected manual fallback rebuilds the legacy ``java-codebase-rag`` name
    by running ``python -m build`` inside the ``shim/`` directory (against
    ``shim/pyproject.toml``) and guards it with ``check_dist_version.py`` pointed
    at the shim's pyproject.
    """
    text = _text()
    assert "shim/pyproject.toml" in text, (
        "manual fallback must reference shim/pyproject.toml (the shim build, not a name-swap)"
    )
    assert "shim" in text and "python -m build" in text, (
        "manual fallback must run python -m build against the shim"
    )
    assert "check_dist_version.py" in text, (
        "manual fallback must guard the shim dist with check_dist_version.py"
    )


def test_documents_trusted_publisher_prerequisite() -> None:
    """The runbook documents the one-time OIDC Trusted Publisher prerequisite.

    Before the first tag-triggered release, an OIDC Trusted Publisher must be
    configured on BOTH ``jrag-cli`` and ``java-codebase-rag`` on pypi.org (repo
    ``HumanBean17/jrag``, workflow ``release.yml``, environment ``release``).
    The first release fails closed until both are configured.
    """
    text = _text()
    lower = text.lower()
    assert "trusted publisher" in lower, (
        "runbook must document the OIDC Trusted Publisher prerequisite"
    )
    assert "oidc" in lower, "Trusted Publishing uses OIDC — name it"
    assert "pypi.org" in text, (
        "the prerequisite is configured on pypi.org — name the host"
    )
    assert "jrag-cli" in text and "java-codebase-rag" in text, (
        "both PyPI names are in scope of the prerequisite"
    )
    assert "release" in lower, (
        "the prerequisite names the 'release' environment / workflow"
    )


def test_retains_dual_publish_policy() -> None:
    """The dual-publish close-out invariant is retained.

    After every release, both ``jrag-cli`` and ``java-codebase-rag`` must report
    the same version on PyPI; never leave them diverged.
    """
    text = _text()
    assert "jrag-cli" in text and "java-codebase-rag" in text
    assert "same version" in text.lower(), (
        "runbook must state both names report the same version after a release"
    )


def test_shim_build_venv_reachable_after_cd() -> None:
    """The shim build reaches the repo-root venv from inside ``shim/``.

    Regression guard for a real bug: the shim build was written as
    ``( cd shim && ... && .venv/bin/python -m build )``. After ``cd shim`` the
    relative path ``.venv/bin/python`` resolves to ``shim/.venv/bin/python``,
    which does not exist — so the shim build fails with "No such file or
    directory", stranding the ``java-codebase-rag`` half of the manual dual
    publish and breaking the same-version invariant. The fixed form is
    ``../.venv/bin/python`` (the venv lives at repo root, one level up).

    The guard pins the bug *class*: a ``cd shim`` immediately followed, within
    the same command/subshell (no closing ``)`` or newline between), by a bare
    ``.venv/bin/{python,twine,pip}`` that is NOT prefixed with ``../``. The
    fixed form (``../.venv/bin/python``) fails the negative lookbehind and so
    does not match.
    """
    text = _text()
    # `cd shim` then, before a `)` or newline, a `.venv/bin/<tool>` NOT preceded
    # by `../` — i.e. an unreachable venv path from inside shim/.
    broken = re.compile(
        r"cd shim\b[^)\n]*?(?<!\.\./)\.venv/bin/(?:python|twine|pip)"
    )
    assert not broken.search(text), (
        "manual fallback invokes a bare `.venv/bin/...` after `cd shim` — after "
        "the cd that resolves to shim/.venv/bin/... (absent). Use `../.venv/bin/...`."
    )
    # Positive pin: the fixed reachable form is actually used for the shim build.
    assert "../.venv/bin/python -m build" in text, (
        "shim build must invoke the repo-root venv as `../.venv/bin/python -m build`"
    )
