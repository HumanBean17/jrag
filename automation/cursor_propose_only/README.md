# Propose-only automation (Cursor and Claude Code)

This workflow is intentionally isolated under `automation/cursor_propose_only/`
so orchestration sources are not mixed with production runtime code, main docs,
or the primary test suite.

Command templates are **host-specific** (`cursor-agent` vs `claude -p`). The Python
orchestration (`prepare`, `evaluate`, `execute`, `autopilot`) is host-agnostic;
only the `--*-command` strings change.

## Commands

From repository root:

- `.venv/bin/python automation/cursor_propose_only/cli.py prepare ...`
- `.venv/bin/python automation/cursor_propose_only/cli.py evaluate ...`
- `.venv/bin/python automation/cursor_propose_only/execute.py ...`
- `.venv/bin/python automation/cursor_propose_only/autopilot.py ...`

## Select specific proposals

If you only want a subset, pass `--proposal` multiple times:

```bash
.venv/bin/python automation/cursor_propose_only/cli.py prepare \
  --repo-root . \
  --proposal-dir propose \
  --output-dir reports/propose_automation_selected \
  --proposal HTTP-ROUTE-METHOD-ENUM-PROPOSE.md \
  --proposal ENHANCED-ROLE-RECOGNITION-PROPOSE.md \
  --rounds 3 \
  --min-severity medium
```

Notes:

- `--proposal` paths may be absolute or relative to `--proposal-dir`
- when `--proposal` is provided, `--glob` is ignored
- add `--include-completed` if selected files can come from `propose/completed/`

## Generate a propose-only workflow bundle

```bash
.venv/bin/python automation/cursor_propose_only/cli.py prepare \
  --repo-root . \
  --proposal-dir propose \
  --output-dir reports/propose_automation \
  --rounds 3 \
  --min-severity medium
```

Generated artifacts:

- `reports/propose_automation/workflow.json`
- `reports/propose_automation/jobs/<job-id>/planner_prompt.md`
- `reports/propose_automation/jobs/<job-id>/reviewer_prompt_round1.md`
- `reports/propose_automation/jobs/<job-id>/reviewer_prompt_round2.md`
- `reports/propose_automation/jobs/<job-id>/reviewer_prompt_round3.md`

## Evaluate each reviewer response

Use one fresh reviewer session per round. Save each reviewer response to a file,
then evaluate it with severity gating.

```bash
.venv/bin/python automation/cursor_propose_only/cli.py evaluate \
  --workflow reports/propose_automation/workflow.json \
  --job-id <job-id> \
  --round 1 \
  --review-file /path/to/review_round1.md \
  --min-severity medium \
  --write
```

Status transitions:

- actionable issue found -> `needs_fixes`
- approved with no actionable issues -> `ready_to_merge`
- final round still failing -> `blocked_after_reviews`

## Automate implementation after plans are ready

When `plans/CURSOR-PROMPTS-<TOPIC>.md` exists, run `execute.py` to iterate PR
sections in order, run implementation command(s), run review loops, and mark
tasks as `ready_to_merge` / `merged`.

```bash
.venv/bin/python automation/cursor_propose_only/execute.py \
  --repo-root . \
  --workflow reports/propose_automation/workflow.json \
  --rounds 3 \
  --min-severity medium \
  --implementation-command 'cursor-agent run --model auto --prompt-file {task_prompt_file}' \
  --review-command 'cursor-agent run --model auto --prompt-file {review_prompt_file}' \
  --merge-command 'gh pr merge {pr_url} --squash --delete-branch' \
  --run
```

### Claude Code equivalents

Use non-interactive Claude Code with the staged prompt files (from repo root; requires
[`.claude/settings.json`](../../.claude/settings.json) or your user permissions):

```bash
.venv/bin/python automation/cursor_propose_only/execute.py \
  --repo-root . \
  --workflow reports/propose_automation/workflow.json \
  --rounds 3 \
  --min-severity medium \
  --implementation-command 'claude -p "$(cat {task_prompt_file})" --output-format json' \
  --review-command 'claude -p "$(cat {review_prompt_file})" --output-format json' \
  --merge-command 'gh pr merge {pr_url} --squash --delete-branch' \
  --run
```

Planning / autopilot: substitute the same pattern for `{planner_prompt_file}`,
`{review_prompt_file}`, and `{task_prompt_file}` on `autopilot.py` (`--planner-command`,
`--planning-review-command`, `--implementation-command`, `--implementation-review-command`).

Example planner line:

```bash
--planner-command 'claude -p "$(cat {planner_prompt_file})" --output-format json'
```

Notes:

- without `--run`, `execute.py` performs a dry-run and only stages prompts/state
- command templates support placeholders such as `{task_prompt_file}`,
  `{review_prompt_file}`, `{pr_url}`, `{branch}`, `{base}`, `{round}`
- workflow state is persisted in `reports/propose_automation/workflow.json`

## Fully automated (single command)

If you want: "I provide propose(s), workflow runs, I come back to ready PRs",
use `autopilot.py`.

```bash
.venv/bin/python automation/cursor_propose_only/autopilot.py \
  --repo-root . \
  --proposal-dir propose \
  --output-dir reports/propose_automation_selected \
  --proposal TIER2-INCREMENTAL-REBUILD-PROPOSE.md \
  --planning-rounds 2 \
  --planning-min-severity medium \
  --implementation-rounds 2 \
  --implementation-min-severity medium \
  --planner-command 'cursor-agent run --model auto --prompt-file {planner_prompt_file}' \
  --planning-review-command 'cursor-agent run --model auto --prompt-file {review_prompt_file}' \
  --implementation-command 'cursor-agent run --model auto --prompt-file {task_prompt_file}' \
  --implementation-review-command 'cursor-agent run --model auto --prompt-file {review_prompt_file}' \
  --merge-command 'gh pr merge {pr_url} --squash --delete-branch' \
  --run
```

Behavior:

1. Generates workflow bundle (`prepare` equivalent)
2. Runs planner command for each selected proposal
3. Runs planning review rounds with severity gating and planner-fix loops
4. Parses generated `plans/CURSOR-PROMPTS-*.md`
5. Runs implementation and implementation-review loops per PR section
6. Marks tasks `ready_to_merge` or `merged` (if merge command is provided)

## Reviewer format convention

For consistent parsing, reviewer findings should follow:

- `[CRITICAL] ...`
- `[HIGH] ...`
- `[MEDIUM] ...`
- `[LOW] ...`
- `[TRIVIAL] ...`

When no actionable issues remain, reviewer should return:

- `APPROVED`
