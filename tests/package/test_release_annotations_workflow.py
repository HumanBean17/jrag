"""Structural tests for the manual-dispatch Maven Central release workflow.

``.github/workflows/release-annotations.yml`` publishes
``io.github.humanbean17:jrag-annotations`` to the Sonatype Central Portal.
Unlike ``release.yml`` (tag-triggered, dual PyPI, same version as the Python
package), this channel is **independently versioned**: it fires only by
manual dispatch with an explicit ``version`` input, only when the annotation
surface changed (the ``_meta`` consistency test is the drift signal), and
records releases under the ``annotations-v*`` tag namespace — disjoint from
the Python ``v*`` tags.

These are *structural* tests: they assert on the keys that encode the
invariants (dispatch-only trigger with version input, Central idempotency
guard before any build, ``release`` environment, release profile +
``-Drevision``, ``annotations-v`` tag prefix, the four required secrets), not
on formatting. Same PyYAML 1.1 gotcha as ``test_release_workflow.py``: a bare
``on:`` key parses as boolean ``True``; ``_triggers()`` reads both spellings.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-annotations.yml"

REQUIRED_SECRETS = (
    "CENTRAL_PORTAL_USERNAME",
    "CENTRAL_PORTAL_TOKEN",
    "GPG_PRIVATE_KEY",
    "GPG_PASSPHRASE",
)


def _load(path: Path) -> dict:
    """``yaml.safe_load`` the file (caller asserts it exists first)."""
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _triggers(workflow: dict) -> dict:
    """Return the workflow's trigger config, tolerating the ``on``/``True`` alias."""
    return workflow.get("on") or workflow.get(True) or workflow.get("on") or {}


def _steps(workflow: dict) -> list[dict]:
    jobs = workflow["jobs"]
    (job,) = jobs.values()
    return job["steps"]


def test_workflow_yaml_parses() -> None:
    """Workflow exists and parses to a top-level mapping."""
    assert WORKFLOW.exists(), f"missing {WORKFLOW}"
    workflow = _load(WORKFLOW)
    assert isinstance(workflow, dict)


def test_manual_dispatch_only_with_version_input() -> None:
    """Dispatch-only trigger (no push/tag/PR) with a required version input."""
    workflow = _load(WORKFLOW)
    triggers = _triggers(workflow)
    assert "workflow_dispatch" in triggers, "must be manually dispatched"
    for forbidden in ("push", "pull_request", "schedule", "release"):
        assert forbidden not in triggers, f"trigger {forbidden!r} must not be set"
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["version"]["required"] is True, "version input must be required"


def test_release_environment_and_permissions() -> None:
    """Publishing job runs in the protected ``release`` environment with write access."""
    workflow = _load(WORKFLOW)
    (job,) = workflow["jobs"].values()
    assert job["environment"] == "release"
    permissions = workflow.get("permissions", {})
    assert permissions.get("contents") == "write"


def test_guard_precedes_build() -> None:
    """The Central idempotency guard runs before any build/setup step."""
    steps = _steps(_load(WORKFLOW))
    guard_idx = next(
        (i for i, s in enumerate(steps) if "search.maven.org" in json.dumps(s)),
        None,
    )
    assert guard_idx is not None, "no step queries search.maven.org"
    build_idx = next(
        (i for i, s in enumerate(steps) if "setup-java" in str(s.get("uses", ""))),
        None,
    )
    assert build_idx is not None, "no setup-java step"
    assert guard_idx < build_idx, "Central guard must precede setup-java/build"


def test_build_uses_release_profile_and_revision() -> None:
    """The mvn step activates the release profile and pins the input version."""
    steps = _steps(_load(WORKFLOW))
    run_steps = [s for s in steps if "run" in s]
    mvn_runs = [s["run"] for s in run_steps if "mvn" in s["run"]]
    assert mvn_runs, "no mvn run step"
    assert any("-P release" in r for r in mvn_runs), "mvn must use -P release"
    assert any("-Drevision=" in r for r in mvn_runs), "mvn must pin -Drevision"


def test_release_tag_prefix() -> None:
    """Release recording uses the annotations-v tag namespace."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "annotations-v" in text, "release step must reference annotations-v tags"


def test_secrets_present() -> None:
    """All four required secrets are referenced by the workflow."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for secret in REQUIRED_SECRETS:
        assert secret in text, f"workflow must reference {secret}"
