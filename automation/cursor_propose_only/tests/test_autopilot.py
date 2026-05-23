from __future__ import annotations

import json
from pathlib import Path

from automation.cursor_propose_only.autopilot import CommandResult, run_autopilot


def test_run_autopilot_dry_run_stages_planning_and_execution(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    propose_dir = repo_root / "propose"
    propose_dir.mkdir(parents=True)
    (propose_dir / "DEMO-PROPOSE.md").write_text("# demo\n", encoding="utf-8")

    workflow = run_autopilot(
        repo_root=repo_root,
        proposal_dir=propose_dir,
        output_dir=repo_root / "reports" / "auto",
        selected_proposals=["DEMO-PROPOSE.md"],
        proposal_glob="*-PROPOSE.md",
        include_completed=False,
        planning_rounds=2,
        planning_min_severity="medium",
        implementation_rounds=2,
        implementation_min_severity="medium",
        planner_command="echo planner {planner_prompt_file}",
        planning_review_command="echo APPROVED",
        implementation_command="echo impl {task_prompt_file}",
        implementation_review_command="echo APPROVED",
        merge_command=None,
        run=False,
    )
    job = workflow["jobs"][0]
    assert job["planning_status"] == "ready_for_planner"
    assert job["execution_status"] == "skipped_planning_pending"


def test_run_autopilot_run_mode_completes_single_task(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    propose_dir = repo_root / "propose"
    plans_dir = repo_root / "plans"
    propose_dir.mkdir(parents=True)
    plans_dir.mkdir(parents=True)
    (propose_dir / "DEMO-PROPOSE.md").write_text("# demo\n", encoding="utf-8")

    def fake_runner(template: str, variables: dict[str, str], _cwd: Path) -> CommandResult:
        command = template.format(**variables)
        if template == "planner":
            agent_prompt_path = repo_root / variables["agent_prompts_path"]
            agent_prompt_path.parent.mkdir(parents=True, exist_ok=True)
            agent_prompt_path.write_text(
                """# Demo

## PR-A1 — demo
**Branch:** `cursor/demo-a1` off `master`.
**Base:** `master`.
**Plan section:** `plans/PLAN-DEMO.md` § PR-A1.

**Prompt:**

````
Implement demo.
````
""",
                encoding="utf-8",
            )
            (repo_root / variables["plan_path"]).write_text("# plan\n", encoding="utf-8")
            return CommandResult(True, command, 0, "planned", "")
        if template == "planning-review":
            return CommandResult(True, command, 0, "APPROVED", "")
        if template == "impl":
            return CommandResult(True, command, 0, "impl ok https://github.com/acme/repo/pull/88", "")
        if template == "impl-review":
            return CommandResult(True, command, 0, "APPROVED", "")
        if template == "merge":
            return CommandResult(True, command, 0, "merged", "")
        raise AssertionError(f"Unexpected template {template}")

    workflow = run_autopilot(
        repo_root=repo_root,
        proposal_dir=propose_dir,
        output_dir=repo_root / "reports" / "auto",
        selected_proposals=["DEMO-PROPOSE.md"],
        proposal_glob="*-PROPOSE.md",
        include_completed=False,
        planning_rounds=2,
        planning_min_severity="medium",
        implementation_rounds=2,
        implementation_min_severity="medium",
        planner_command="planner",
        planning_review_command="planning-review",
        implementation_command="impl",
        implementation_review_command="impl-review",
        merge_command="merge",
        run=True,
        command_runner=fake_runner,
    )

    job = workflow["jobs"][0]
    assert job["planning_status"] == "ready_to_execute"
    assert job["execution_status"] == "all_merged"
    task = job["execution_tasks"][0]
    assert task["status"] == "merged"
    assert task["pr_url"] == "https://github.com/acme/repo/pull/88"
    persisted = json.loads((repo_root / "reports" / "auto" / "workflow.json").read_text(encoding="utf-8"))
    assert persisted["jobs"][0]["execution_status"] == "all_merged"
