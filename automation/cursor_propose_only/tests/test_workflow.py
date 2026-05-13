from __future__ import annotations

import json
from pathlib import Path

from automation.cursor_propose_only.workflow import apply_review_result, evaluate_review, prepare_bundle


def test_prepare_bundle_writes_manifest_and_prompts(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    proposal_dir = repo_root / "propose"
    output_dir = repo_root / "reports" / "propose_automation"
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "EXAMPLE-PROPOSE.md").write_text("# Example\n", encoding="utf-8")

    workflow = prepare_bundle(
        repo_root=repo_root,
        proposal_dir=proposal_dir,
        output_dir=output_dir,
        rounds=3,
        min_severity="medium",
        pattern="*-PROPOSE.md",
        include_completed=False,
    )

    assert workflow["review_rounds"] == 3
    assert workflow["min_severity"] == "medium"
    assert len(workflow["jobs"]) == 1
    job = workflow["jobs"][0]
    assert job["status"] == "pending_planner"
    assert job["plan_path"] == "plans/PLAN-EXAMPLE.md"
    assert len(job["reviewer_prompt_paths"]) == 3

    workflow_path = output_dir / "workflow.json"
    assert workflow_path.exists()
    persisted = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert persisted["jobs"][0]["job_id"] == "example"

    planner_prompt = repo_root / job["planner_prompt_path"]
    assert planner_prompt.exists()
    assert "Do not implement production code." in planner_prompt.read_text(encoding="utf-8")


def test_evaluate_review_threshold_filtering() -> None:
    review_text = """
    [LOW] Minor wording issue.
    [HIGH] Missing out-of-scope guardrail.
    APPROVED
    """
    result = evaluate_review(review_text, min_severity="medium")
    assert result["approved"] is False
    assert result["issue_count"] == 2
    assert result["actionable_issue_count"] == 1
    assert result["actionable_issues"][0]["severity"] == "high"


def test_apply_review_result_updates_job_status(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    proposal_dir = repo_root / "propose"
    output_dir = repo_root / "reports" / "propose_automation"
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "EXAMPLE-PROPOSE.md").write_text("# Example\n", encoding="utf-8")
    prepare_bundle(
        repo_root=repo_root,
        proposal_dir=proposal_dir,
        output_dir=output_dir,
        rounds=3,
        min_severity="medium",
        pattern="*-PROPOSE.md",
        include_completed=False,
    )
    workflow_path = output_dir / "workflow.json"

    failing_review = output_dir / "review-round-1.md"
    failing_review.write_text("[MEDIUM] Missing risk section.\n", encoding="utf-8")
    apply_review_result(
        workflow_path=workflow_path,
        review_file=failing_review,
        job_id="example",
        round_number=1,
        min_severity="medium",
    )
    after_fail = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert after_fail["jobs"][0]["status"] == "needs_fixes"

    approved_review = output_dir / "review-round-2.md"
    approved_review.write_text("APPROVED\n", encoding="utf-8")
    apply_review_result(
        workflow_path=workflow_path,
        review_file=approved_review,
        job_id="example",
        round_number=2,
        min_severity="medium",
    )
    after_approve = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert after_approve["jobs"][0]["status"] == "ready_to_merge"
