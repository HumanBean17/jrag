from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.cursor_propose_only.execute import CommandResult, execute_workflow, parse_agent_prompts


_PROMPTS_FIXTURE = """# Cursor task prompts — Demo

## PR-A1 — Add thing
**Branch:** `cursor/a1` off `master`.
**Base:** `master`.
**Plan section:** `plans/PLAN-DEMO.md` § PR-A1.

**Prompt:**

````
Implement A1.
````

## PR-A2 — Add second thing
**Branch:** `cursor/a2` off `master`.
**Base:** `master`.
**Plan section:** `plans/PLAN-DEMO.md` § PR-A2.

**Prompt:**

````
Implement A2.
````
"""


def test_parse_agent_prompts_extracts_ordered_tasks() -> None:
    tasks = parse_agent_prompts(_PROMPTS_FIXTURE)
    assert [t.task_id for t in tasks] == ["PR-A1", "PR-A2"]
    assert tasks[0].branch == "cursor/a1"
    assert tasks[1].base == "master"
    assert tasks[0].prompt_body == "Implement A1."


def test_parse_agent_prompts_requires_prompt_block() -> None:
    with pytest.raises(ValueError):
        parse_agent_prompts("## PR-A1 — Missing prompt\n**Branch:** `x`\n")


def test_execute_workflow_dry_run_stages_tasks(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompts_path = repo_root / "plans" / "AGENT-PROMPTS-DEMO.md"
    prompts_path.parent.mkdir(parents=True)
    prompts_path.write_text(_PROMPTS_FIXTURE, encoding="utf-8")

    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "job_id": "demo",
                        "agent_prompts_path": "plans/AGENT-PROMPTS-DEMO.md",
                        "plan_path": "plans/PLAN-DEMO.md",
                        "propose_path": "propose/DEMO-PROPOSE.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    workflow = execute_workflow(
        workflow_path=workflow_path,
        repo_root=repo_root,
        rounds=3,
        min_severity="medium",
        implementation_command="impl {task_prompt_file}",
        review_command="review {review_prompt_file}",
        merge_command=None,
        dry_run=True,
    )
    job = workflow["jobs"][0]
    assert job["execution_status"] == "dry_run_ready"
    assert len(job["execution_tasks"]) == 2
    assert job["execution_tasks"][0]["status"] == "ready_for_implementation"


def test_execute_workflow_run_mode_reviews_and_merges(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompts_path = repo_root / "plans" / "AGENT-PROMPTS-DEMO.md"
    prompts_path.parent.mkdir(parents=True)
    prompts_path.write_text(_PROMPTS_FIXTURE, encoding="utf-8")

    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "job_id": "demo",
                        "agent_prompts_path": "plans/AGENT-PROMPTS-DEMO.md",
                        "plan_path": "plans/PLAN-DEMO.md",
                        "propose_path": "propose/DEMO-PROPOSE.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_runner(template: str, variables: dict[str, str], _cwd: Path) -> CommandResult:
        if template.startswith("impl"):
            return CommandResult(
                success=True,
                command=template.format(**variables),
                returncode=0,
                stdout="impl ok https://github.com/acme/repo/pull/42",
                stderr="",
            )
        if template.startswith("review"):
            if variables["round"] == "1":
                return CommandResult(
                    success=True,
                    command=template.format(**variables),
                    returncode=0,
                    stdout="[HIGH] tighten acceptance criteria",
                    stderr="",
                )
            return CommandResult(
                success=True,
                command=template.format(**variables),
                returncode=0,
                stdout="APPROVED",
                stderr="",
            )
        if template.startswith("merge"):
            return CommandResult(
                success=True,
                command=template.format(**variables),
                returncode=0,
                stdout="merged",
                stderr="",
            )
        raise AssertionError(f"Unexpected template: {template}")

    workflow = execute_workflow(
        workflow_path=workflow_path,
        repo_root=repo_root,
        rounds=3,
        min_severity="medium",
        implementation_command="impl {round}",
        review_command="review {round}",
        merge_command="merge {pr_url}",
        dry_run=False,
        command_runner=fake_runner,
    )
    job = workflow["jobs"][0]
    task = job["execution_tasks"][0]
    assert task["status"] == "merged"
    assert task["pr_url"] == "https://github.com/acme/repo/pull/42"
    assert len(task["review_rounds"]) == 2
    assert task["review_rounds"][0]["approved"] is False
    assert task["review_rounds"][1]["approved"] is True
