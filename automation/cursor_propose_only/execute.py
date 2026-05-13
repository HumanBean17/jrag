from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PR_SECTION_RE = re.compile(r"^##\s+(PR-[^\n]+)$", re.MULTILINE)
_PROMPT_BLOCK_RE = re.compile(r"\*\*Prompt:\*\*\s*\n\s*(`{3,})\n(.*?)\n\1", re.DOTALL)
_FIELD_RE = re.compile(r"^\*\*(Branch|Base|Plan section):\*\*\s*(.+?)\s*$", re.MULTILINE)
_BACKTICK_VALUE_RE = re.compile(r"`([^`]+)`")
_DEFAULT_PR_URL_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")


@dataclass(frozen=True, slots=True)
class PromptTask:
    task_id: str
    heading: str
    branch: str
    base: str
    plan_section: str
    prompt_body: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    success: bool
    command: str
    returncode: int
    stdout: str
    stderr: str


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _evaluate_review(review_text: str, *, min_severity: str) -> dict[str, Any]:
    from automation.cursor_propose_only.workflow import evaluate_review

    return evaluate_review(review_text, min_severity=min_severity)


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-") or "task"


def parse_cursor_prompts(markdown_text: str) -> list[PromptTask]:
    matches = list(_PR_SECTION_RE.finditer(markdown_text))
    if not matches:
        return []
    tasks: list[PromptTask] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
        section_text = markdown_text[start:end]
        heading = match.group(1).strip()
        task_id = heading.split("—", 1)[0].strip()
        fields = {m.group(1).strip().lower(): m.group(2).strip() for m in _FIELD_RE.finditer(section_text)}
        branch = _extract_backtick_or_raw(fields.get("branch", ""))
        base = _extract_backtick_or_raw(fields.get("base", "master")) or "master"
        plan_section = fields.get("plan section", "")
        prompt_match = _PROMPT_BLOCK_RE.search(section_text)
        if prompt_match is None:
            raise ValueError(f"Missing Prompt block for section {heading!r}")
        prompt_body = prompt_match.group(2).strip()
        tasks.append(
            PromptTask(
                task_id=task_id,
                heading=heading,
                branch=branch,
                base=base,
                plan_section=plan_section.strip(),
                prompt_body=prompt_body,
            )
        )
    return tasks


def _extract_backtick_or_raw(value: str) -> str:
    if not value:
        return ""
    backtick_match = _BACKTICK_VALUE_RE.search(value)
    if backtick_match:
        return backtick_match.group(1).strip()
    return value.strip(" .")


def run_shell_command(
    command_template: str,
    variables: dict[str, str],
    *,
    working_directory: Path,
) -> CommandResult:
    command = command_template.format(**variables)
    completed = subprocess.run(
        ["/bin/bash", "-lc", command],
        cwd=str(working_directory),
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


def _find_pr_url(text: str, *, regex: str | None = None) -> str:
    pattern = re.compile(regex) if regex else _DEFAULT_PR_URL_RE
    match = pattern.search(text)
    return match.group(0) if match else ""


def _render_impl_fix_prompt(base_prompt: str, actionable_issues: list[dict[str, str]]) -> str:
    bullets = "\n".join(f"- [{it['severity'].upper()}] {it['summary']}" for it in actionable_issues)
    return (
        f"{base_prompt}\n\n"
        "## Reviewer findings to fix before continuing\n"
        f"{bullets}\n\n"
        "Address only these actionable findings while staying inside the original scope contract."
    )


def _ensure_job_execution_state(job: dict[str, Any], tasks: list[PromptTask]) -> None:
    existing = {item.get("task_id"): item for item in job.get("execution_tasks", [])}
    merged: list[dict[str, Any]] = []
    for task in tasks:
        if task.task_id in existing:
            merged.append(existing[task.task_id])
            continue
        merged.append(
            {
                "task_id": task.task_id,
                "heading": task.heading,
                "branch": task.branch,
                "base": task.base,
                "status": "pending_implementation",
                "pr_url": "",
                "history": [],
                "review_rounds": [],
            }
        )
    job["execution_tasks"] = merged
    if "execution_status" not in job:
        job["execution_status"] = "pending"


def _write_task_prompt(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def execute_workflow(
    *,
    workflow_path: Path,
    repo_root: Path,
    rounds: int,
    min_severity: str,
    implementation_command: str,
    review_command: str,
    merge_command: str | None,
    dry_run: bool,
    command_runner: Callable[[str, dict[str, str], Path], CommandResult] | None = None,
    pr_url_regex: str | None = None,
) -> dict[str, Any]:
    workflow = _json_load(workflow_path)
    runner = command_runner or (lambda tpl, vars_, cwd: run_shell_command(tpl, vars_, working_directory=cwd))
    output_root = workflow_path.parent / "execution"
    output_root.mkdir(parents=True, exist_ok=True)

    for job in workflow.get("jobs", []):
        job_id = str(job.get("job_id", "job"))
        job_root = output_root / _safe_name(job_id)
        planning_status = str(job.get("planning_status", ""))
        if planning_status and planning_status != "ready_to_execute":
            if planning_status.startswith("blocked_"):
                job["execution_status"] = "skipped_planning_blocked"
            else:
                job["execution_status"] = "skipped_planning_pending"
            continue
        cursor_prompts_path = repo_root / str(job.get("cursor_prompts_path", ""))
        if not cursor_prompts_path.is_file():
            job["execution_status"] = "blocked_missing_cursor_prompts"
            continue

        tasks = parse_cursor_prompts(cursor_prompts_path.read_text(encoding="utf-8"))
        _ensure_job_execution_state(job, tasks)
        task_map = {task.task_id: task for task in tasks}

        for task_state in job.get("execution_tasks", []):
            task_id = str(task_state.get("task_id"))
            task = task_map.get(task_id)
            if task is None:
                task_state["status"] = "skipped_missing_task_definition"
                continue
            if task_state.get("status") in {"merged", "ready_to_merge"}:
                continue

            task_root = job_root / _safe_name(task_id)
            impl_prompt_path = _write_task_prompt(task_root / "implementation_prompt.md", task.prompt_body)
            variables = {
                "job_id": job_id,
                "task_id": task.task_id,
                "branch": task.branch,
                "base": task.base,
                "plan_section": task.plan_section,
                "cursor_prompts_path": str(cursor_prompts_path),
                "propose_path": str(job.get("propose_path", "")),
                "plan_path": str(job.get("plan_path", "")),
                "task_prompt_file": str(impl_prompt_path),
                "round": "0",
                "pr_url": str(task_state.get("pr_url", "")),
                "review_prompt_file": "",
                "review_output_file": "",
                "issues_file": "",
            }

            if dry_run:
                task_state["status"] = "ready_for_implementation"
                task_state.setdefault("history", []).append(
                    {
                        "phase": "implementation",
                        "dry_run": True,
                        "command_template": implementation_command,
                        "at": _now_utc(),
                    }
                )
                continue

            impl_result = runner(implementation_command, variables, repo_root)
            impl_log_path = task_root / "implementation_round0.log"
            _write_task_prompt(
                impl_log_path,
                f"$ {impl_result.command}\n\n[stdout]\n{impl_result.stdout}\n\n[stderr]\n{impl_result.stderr}\n",
            )
            task_state.setdefault("history", []).append(
                {
                    "phase": "implementation",
                    "returncode": impl_result.returncode,
                    "success": impl_result.success,
                    "log_path": str(impl_log_path),
                    "at": _now_utc(),
                }
            )
            if not impl_result.success:
                task_state["status"] = "blocked_implementation_failed"
                break

            detected_pr = _find_pr_url(f"{impl_result.stdout}\n{impl_result.stderr}", regex=pr_url_regex)
            if detected_pr:
                task_state["pr_url"] = detected_pr

            review_passed = False
            for round_number in range(1, rounds + 1):
                review_prompt = (
                    "You are reviewing a PR implementation result.\n\n"
                    f"Task: {task.heading}\n"
                    f"Branch: {task.branch}\n"
                    f"PR URL: {task_state.get('pr_url', '')}\n\n"
                    f"Report only `{min_severity}` or higher issues.\n"
                    "Output findings as `[SEVERITY] summary`.\n"
                    "If no actionable issues remain, return exactly `APPROVED`.\n"
                )
                review_prompt_path = _write_task_prompt(
                    task_root / f"review_prompt_round{round_number}.md", review_prompt
                )
                review_output_file = task_root / f"review_output_round{round_number}.md"
                variables.update(
                    {
                        "round": str(round_number),
                        "pr_url": str(task_state.get("pr_url", "")),
                        "review_prompt_file": str(review_prompt_path),
                        "review_output_file": str(review_output_file),
                    }
                )
                review_result = runner(review_command, variables, repo_root)
                _write_task_prompt(
                    review_output_file,
                    review_result.stdout or review_result.stderr or "No review output captured.\n",
                )
                if not review_result.success:
                    task_state["status"] = "blocked_review_failed"
                    task_state.setdefault("review_rounds", []).append(
                        {
                            "round_number": round_number,
                            "status": "review_command_failed",
                            "returncode": review_result.returncode,
                            "at": _now_utc(),
                        }
                    )
                    break

                review_eval = _evaluate_review(
                    review_output_file.read_text(encoding="utf-8"), min_severity=min_severity
                )
                task_state.setdefault("review_rounds", []).append(
                    {
                        "round_number": round_number,
                        "review_file": str(review_output_file),
                        **review_eval,
                        "at": _now_utc(),
                    }
                )
                if review_eval["approved"]:
                    review_passed = True
                    break

                if round_number < rounds:
                    issues_file = _write_task_prompt(
                        task_root / f"review_issues_round{round_number}.json",
                        json.dumps(review_eval.get("actionable_issues", []), indent=2, sort_keys=True),
                    )
                    fix_prompt = _render_impl_fix_prompt(
                        task.prompt_body, review_eval.get("actionable_issues", [])
                    )
                    fix_prompt_file = _write_task_prompt(
                        task_root / f"implementation_fix_prompt_round{round_number}.md", fix_prompt
                    )
                    variables.update(
                        {
                            "task_prompt_file": str(fix_prompt_file),
                            "issues_file": str(issues_file),
                            "round": str(round_number),
                        }
                    )
                    fix_result = runner(implementation_command, variables, repo_root)
                    fix_log_path = task_root / f"implementation_fix_round{round_number}.log"
                    _write_task_prompt(
                        fix_log_path,
                        f"$ {fix_result.command}\n\n[stdout]\n{fix_result.stdout}\n\n[stderr]\n{fix_result.stderr}\n",
                    )
                    task_state.setdefault("history", []).append(
                        {
                            "phase": "implementation_fix",
                            "round_number": round_number,
                            "returncode": fix_result.returncode,
                            "success": fix_result.success,
                            "log_path": str(fix_log_path),
                            "at": _now_utc(),
                        }
                    )
                    if not fix_result.success:
                        task_state["status"] = "blocked_fix_failed"
                        break
                    detected_pr = _find_pr_url(f"{fix_result.stdout}\n{fix_result.stderr}", regex=pr_url_regex)
                    if detected_pr:
                        task_state["pr_url"] = detected_pr
                else:
                    task_state["status"] = "blocked_after_reviews"

            if task_state.get("status", "").startswith("blocked_"):
                break

            if review_passed:
                if merge_command:
                    merge_vars = dict(variables)
                    merge_vars["round"] = str(rounds)
                    merge_result = runner(merge_command, merge_vars, repo_root)
                    merge_log_path = task_root / "merge.log"
                    _write_task_prompt(
                        merge_log_path,
                        f"$ {merge_result.command}\n\n[stdout]\n{merge_result.stdout}\n\n[stderr]\n{merge_result.stderr}\n",
                    )
                    task_state.setdefault("history", []).append(
                        {
                            "phase": "merge",
                            "returncode": merge_result.returncode,
                            "success": merge_result.success,
                            "log_path": str(merge_log_path),
                            "at": _now_utc(),
                        }
                    )
                    task_state["status"] = "merged" if merge_result.success else "ready_to_merge"
                else:
                    task_state["status"] = "ready_to_merge"
            else:
                task_state["status"] = "blocked_after_reviews"

        statuses = {task.get("status") for task in job.get("execution_tasks", [])}
        if statuses and statuses <= {"merged"}:
            job["execution_status"] = "all_merged"
        elif "blocked_after_reviews" in statuses or any(s.startswith("blocked_") for s in statuses if s):
            job["execution_status"] = "blocked"
        elif "ready_to_merge" in statuses:
            job["execution_status"] = "ready_to_merge"
        elif "ready_for_implementation" in statuses:
            job["execution_status"] = "dry_run_ready"
        else:
            job["execution_status"] = "in_progress"

    workflow["execution_updated_at_utc"] = _now_utc()
    _json_dump(workflow_path, workflow)
    return workflow


def _load_command_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute per-PR implementation + review loops from CURSOR-PROMPTS plans."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument(
        "--workflow",
        default="reports/propose_automation/workflow.json",
        help="Path to workflow.json from prepare step.",
    )
    parser.add_argument("--rounds", type=int, default=3, help="Review rounds per PR task.")
    parser.add_argument(
        "--min-severity",
        choices=("trivial", "low", "medium", "high", "critical"),
        default="medium",
        help="Actionable severity threshold for review gating.",
    )
    parser.add_argument(
        "--implementation-command",
        default="",
        help=(
            "Shell command template used to run implementation agent. Supports placeholders: "
            "{task_prompt_file} {branch} {base} {task_id} {job_id} {round} {issues_file} {pr_url}."
        ),
    )
    parser.add_argument(
        "--review-command",
        default="",
        help=(
            "Shell command template used to run reviewer agent. Supports placeholders: "
            "{review_prompt_file} {review_output_file} {pr_url} {task_id} {job_id} {round}."
        ),
    )
    parser.add_argument(
        "--merge-command",
        default="",
        help=(
            "Optional shell command template used after approval. Supports placeholders: "
            "{pr_url} {branch} {task_id} {job_id}."
        ),
    )
    parser.add_argument("--implementation-command-file", default="", help="Read implementation command template from file.")
    parser.add_argument("--review-command-file", default="", help="Read review command template from file.")
    parser.add_argument("--merge-command-file", default="", help="Read merge command template from file.")
    parser.add_argument("--pr-url-regex", default="", help="Custom regex for extracting PR URL from command output.")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute commands. Without this flag, runner performs a dry-run and only stages task prompts.",
    )
    return parser


def _resolve_command(cli_value: str, file_value: str, *, name: str) -> str:
    if file_value:
        return _load_command_file(Path(file_value))
    if cli_value:
        return cli_value
    raise ValueError(f"Missing required {name}: pass --{name.replace('_', '-')} or --{name.replace('_', '-')}-file")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        implementation_command = _resolve_command(
            args.implementation_command, args.implementation_command_file, name="implementation_command"
        )
        review_command = _resolve_command(args.review_command, args.review_command_file, name="review_command")
        merge_command = (
            _resolve_command(args.merge_command, args.merge_command_file, name="merge_command")
            if (args.merge_command or args.merge_command_file)
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    workflow = execute_workflow(
        workflow_path=Path(args.workflow).resolve(),
        repo_root=Path(args.repo_root).resolve(),
        rounds=int(args.rounds),
        min_severity=args.min_severity,
        implementation_command=implementation_command,
        review_command=review_command,
        merge_command=merge_command,
        dry_run=not bool(args.run),
        pr_url_regex=(args.pr_url_regex or None),
    )
    print(json.dumps(workflow, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
