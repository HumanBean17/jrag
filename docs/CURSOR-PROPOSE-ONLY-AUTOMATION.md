# Cursor propose-only automation

This repository now includes a lightweight orchestration helper for a
"propose + review only" loop.

The helper does not execute implementation work. It generates prompt bundles,
tracks review rounds, and applies severity-based approval gating.

## Command

Use the script from repository root:

- `.venv/bin/python scripts/propose_only_orchestrator.py prepare ...`
- `.venv/bin/python scripts/propose_only_orchestrator.py evaluate ...`

## 1) Generate a propose-only workflow bundle

```bash
.venv/bin/python scripts/propose_only_orchestrator.py prepare \
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

## 2) Evaluate each reviewer response

Use one fresh reviewer session per round. Save each reviewer response to a file,
then evaluate it with severity gating.

```bash
.venv/bin/python scripts/propose_only_orchestrator.py evaluate \
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

## Review format convention

For consistent parsing, reviewer findings should follow:

- `[CRITICAL] ...`
- `[HIGH] ...`
- `[MEDIUM] ...`
- `[LOW] ...`
- `[TRIVIAL] ...`

When no actionable issues remain, reviewer should return:

- `APPROVED`
