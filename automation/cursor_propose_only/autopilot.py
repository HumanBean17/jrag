#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True, slots=True)
class CommandResult:
    success: bool
    command: str
    returncode: int
    stdout: str
    stderr: str


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _run_shell_command(command_template: str, variables: dict[str, str], cwd: Path) -> CommandResult:
    command = command_template.format(**variables)
    completed = subprocess.run(
        ["/bin/bash", "-lc", command],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        success=completed.returncode == 0,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_command_log(path: Path, result: CommandResult) -> Path:
    return _write_text(
        path,
        f"$ {result.command}\n\n[stdout]\n{result.stdout}\n\n[stderr]\n{result.stderr}\n",
    )


def _render_planner_fix_prompt(base_prompt: str, actionable_issues: list[dict[str, str]]) -> str:
    bullets = "\n".join(f"- [{issue['severity'].upper()}] {issue['summary']}" for issue in actionable_issues)
    return (
        f"{base_prompt}\n\n"
        "## Reviewer findings to fix\n"
        f"{bullets}\n\n"
        "Update plan and cursor prompts to resolve all actionable findings."
    )


def _load_workflow(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_workflow(path: Path, workflow: dict[str, Any]) -> None:
    path.write_text(json.dumps(workflow, indent=2, sort_keys=True), encoding="utf-8")


def _resolve_command(cli_value: str, file_value: str, *, name: str) -> str:
    if file_value:
        return Path(file_value).read_text(encoding="utf-8").strip()
    if cli_value:
        return cli_value
    raise ValueError(f"Missing required {name}: pass --{name.replace('_', '-')} or --{name.replace('_', '-')}-file")


def run_autopilot(
    *,
    repo_root: Path,
    proposal_dir: Path,
    output_dir: Path,
    selected_proposals: list[str],
    proposal_glob: str,
    include_completed: bool,
    planning_rounds: int,
    planning_min_severity: str,
    implementation_rounds: int,
    implementation_min_severity: str,
    planner_command: str,
    planning_review_command: str,
    implementation_command: str,
    implementation_review_command: str,
    merge_command: str | None,
    run: bool,
    command_runner: Callable[[str, dict[str, str], Path], CommandResult] | None = None,
    pr_url_regex: str | None = None,
) -> dict[str, Any]:
    from automation.cursor_propose_only.execute import execute_workflow
    from automation.cursor_propose_only.workflow import evaluate_review, prepare_bundle

    runner = command_runner or _run_shell_command
    workflow = prepare_bundle(
        repo_root=repo_root,
        proposal_dir=proposal_dir,
        output_dir=output_dir,
        rounds=planning_rounds,
        min_severity=planning_min_severity,
        pattern=proposal_glob,
        include_completed=include_completed,
        selected_proposals=selected_proposals,
    )
    workflow_path = output_dir / "workflow.json"

    for job in workflow.get("jobs", []):
        job_id = str(job.get("job_id", "job"))
        job_dir = output_dir / "jobs" / job_id
        planner_prompt_file = repo_root / str(job.get("planner_prompt_path", ""))
        planner_prompt_text = planner_prompt_file.read_text(encoding="utf-8")

        if not run:
            job["planning_status"] = "ready_for_planner"
            job.setdefault("planning_history", []).append(
                {
                    "phase": "planner",
                    "dry_run": True,
                    "command_template": planner_command,
                    "at": _now_utc(),
                }
            )
            job["execution_status"] = "skipped_planning_pending"
            continue

        variables = {
            "job_id": job_id,
            "round": "0",
            "propose_path": str(job.get("propose_path", "")),
            "plan_path": str(job.get("plan_path", "")),
            "agent_prompts_path": str(job.get("agent_prompts_path", "")),
            "planner_prompt_file": str(planner_prompt_file),
            "review_prompt_file": "",
            "review_output_file": "",
            "issues_file": "",
        }
        planner_result = runner(planner_command, variables, repo_root)
        planner_log = _write_command_log(job_dir / "planning_command_round0.log", planner_result)
        job.setdefault("planning_history", []).append(
            {
                "phase": "planner",
                "returncode": planner_result.returncode,
                "success": planner_result.success,
                "log_path": str(planner_log),
                "at": _now_utc(),
            }
        )
        if not planner_result.success:
            job["planning_status"] = "blocked_planner_failed"
            continue

        planning_approved = False
        for round_number in range(1, planning_rounds + 1):
            review_prompt_file = repo_root / str(job["reviewer_prompt_paths"][round_number - 1])
            review_output_file = job_dir / f"planning_review_output_round{round_number}.md"
            variables.update(
                {
                    "round": str(round_number),
                    "review_prompt_file": str(review_prompt_file),
                    "review_output_file": str(review_output_file),
                }
            )
            review_result = runner(planning_review_command, variables, repo_root)
            _write_text(
                review_output_file, review_result.stdout or review_result.stderr or "No planning review output captured.\n"
            )
            job.setdefault("planning_history", []).append(
                {
                    "phase": "planning_review",
                    "round_number": round_number,
                    "returncode": review_result.returncode,
                    "success": review_result.success,
                    "review_output_file": str(review_output_file),
                    "at": _now_utc(),
                }
            )
            if not review_result.success:
                job["planning_status"] = "blocked_planning_review_failed"
                break

            review_eval = evaluate_review(review_output_file.read_text(encoding="utf-8"), min_severity=planning_min_severity)
            job.setdefault("reviews", []).append(
                {
                    "round_number": round_number,
                    "review_file": str(review_output_file),
                    **review_eval,
                }
            )
            if review_eval["approved"]:
                planning_approved = True
                break

            if round_number < planning_rounds:
                issues_file = _write_text(
                    job_dir / f"planning_review_issues_round{round_number}.json",
                    json.dumps(review_eval.get("actionable_issues", []), indent=2, sort_keys=True),
                )
                fix_prompt = _render_planner_fix_prompt(planner_prompt_text, review_eval.get("actionable_issues", []))
                fix_prompt_file = _write_text(job_dir / f"planner_fix_prompt_round{round_number}.md", fix_prompt)
                variables.update(
                    {
                        "planner_prompt_file": str(fix_prompt_file),
                        "issues_file": str(issues_file),
                        "round": str(round_number),
                    }
                )
                fix_result = runner(planner_command, variables, repo_root)
                fix_log = _write_command_log(job_dir / f"planning_fix_command_round{round_number}.log", fix_result)
                job.setdefault("planning_history", []).append(
                    {
                        "phase": "planner_fix",
                        "round_number": round_number,
                        "returncode": fix_result.returncode,
                        "success": fix_result.success,
                        "log_path": str(fix_log),
                        "at": _now_utc(),
                    }
                )
                if not fix_result.success:
                    job["planning_status"] = "blocked_planner_fix_failed"
                    break
            else:
                job["planning_status"] = "blocked_after_planning_reviews"

        if planning_approved:
            job["planning_status"] = "ready_to_execute"
        elif "planning_status" not in job:
            job["planning_status"] = "blocked_after_planning_reviews"

    _save_workflow(workflow_path, workflow)

    if run:
        workflow = execute_workflow(
            workflow_path=workflow_path,
            repo_root=repo_root,
            rounds=implementation_rounds,
            min_severity=implementation_min_severity,
            implementation_command=implementation_command,
            review_command=implementation_review_command,
            merge_command=merge_command,
            dry_run=False,
            command_runner=runner,
            pr_url_regex=pr_url_regex,
        )
    else:
        workflow = execute_workflow(
            workflow_path=workflow_path,
            repo_root=repo_root,
            rounds=implementation_rounds,
            min_severity=implementation_min_severity,
            implementation_command=implementation_command,
            review_command=implementation_review_command,
            merge_command=merge_command,
            dry_run=True,
            command_runner=runner,
            pr_url_regex=pr_url_regex,
        )
    workflow["autopilot_updated_at_utc"] = _now_utc()
    _save_workflow(workflow_path, workflow)
    return workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Single-command proposal -> planning -> implementation -> review automation."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument("--proposal-dir", default="propose", help="Proposal directory.")
    parser.add_argument("--output-dir", default="reports/propose_automation", help="Workflow output directory.")
    parser.add_argument("--glob", default="*-PROPOSE.md", help="Proposal glob pattern.")
    parser.add_argument("--proposal", action="append", default=[], help="Specific proposal file (repeatable).")
    parser.add_argument("--include-completed", action="store_true", help="Allow selecting from propose/completed.")
    parser.add_argument("--planning-rounds", type=int, default=3, help="Planning review rounds.")
    parser.add_argument(
        "--planning-min-severity",
        choices=("trivial", "low", "medium", "high", "critical"),
        default="medium",
        help="Planning actionable severity threshold.",
    )
    parser.add_argument("--implementation-rounds", type=int, default=3, help="Implementation review rounds.")
    parser.add_argument(
        "--implementation-min-severity",
        choices=("trivial", "low", "medium", "high", "critical"),
        default="medium",
        help="Implementation actionable severity threshold.",
    )
    parser.add_argument("--planner-command", default="", help="Planner command template.")
    parser.add_argument("--planner-command-file", default="", help="File containing planner command template.")
    parser.add_argument("--planning-review-command", default="", help="Planning reviewer command template.")
    parser.add_argument(
        "--planning-review-command-file",
        default="",
        help="File containing planning reviewer command template.",
    )
    parser.add_argument("--implementation-command", default="", help="Implementation command template.")
    parser.add_argument(
        "--implementation-command-file",
        default="",
        help="File containing implementation command template.",
    )
    parser.add_argument("--implementation-review-command", default="", help="Implementation reviewer command template.")
    parser.add_argument(
        "--implementation-review-command-file",
        default="",
        help="File containing implementation reviewer command template.",
    )
    parser.add_argument("--merge-command", default="", help="Optional merge command template.")
    parser.add_argument("--merge-command-file", default="", help="Optional file containing merge command template.")
    parser.add_argument("--pr-url-regex", default="", help="Optional custom PR URL extraction regex.")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run full automation. Without this flag, runner performs dry-run state staging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        planner_command = _resolve_command(args.planner_command, args.planner_command_file, name="planner_command")
        planning_review_command = _resolve_command(
            args.planning_review_command, args.planning_review_command_file, name="planning_review_command"
        )
        implementation_command = _resolve_command(
            args.implementation_command, args.implementation_command_file, name="implementation_command"
        )
        implementation_review_command = _resolve_command(
            args.implementation_review_command,
            args.implementation_review_command_file,
            name="implementation_review_command",
        )
        merge_command = (
            _resolve_command(args.merge_command, args.merge_command_file, name="merge_command")
            if (args.merge_command or args.merge_command_file)
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    workflow = run_autopilot(
        repo_root=Path(args.repo_root).resolve(),
        proposal_dir=Path(args.proposal_dir).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        selected_proposals=list(args.proposal or []),
        proposal_glob=args.glob,
        include_completed=bool(args.include_completed),
        planning_rounds=int(args.planning_rounds),
        planning_min_severity=args.planning_min_severity,
        implementation_rounds=int(args.implementation_rounds),
        implementation_min_severity=args.implementation_min_severity,
        planner_command=planner_command,
        planning_review_command=planning_review_command,
        implementation_command=implementation_command,
        implementation_review_command=implementation_review_command,
        merge_command=merge_command,
        run=bool(args.run),
        pr_url_regex=(args.pr_url_regex or None),
    )
    print(json.dumps(workflow, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
