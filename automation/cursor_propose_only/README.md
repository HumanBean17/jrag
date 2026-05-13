# Cursor propose-only automation

This workflow is intentionally isolated under `automation/cursor_propose_only/`
so orchestration sources are not mixed with production runtime code, main docs,
or the primary test suite.

## Commands

From repository root:

- `.venv/bin/python automation/cursor_propose_only/cli.py prepare ...`
- `.venv/bin/python automation/cursor_propose_only/cli.py evaluate ...`

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

## Reviewer format convention

For consistent parsing, reviewer findings should follow:

- `[CRITICAL] ...`
- `[HIGH] ...`
- `[MEDIUM] ...`
- `[LOW] ...`
- `[TRIVIAL] ...`

When no actionable issues remain, reviewer should return:

- `APPROVED`
