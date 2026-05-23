# PR Open Examples

## Example section skeleton

```markdown
## Scope
Implements PR-XX from `plans/PLAN-TOPIC.md` by delivering <short scope>.

## What Changed
- Updated `module_a.py` to <behavior change>.
- Added `tests/test_topic.py` with focused regressions for <cases>.

## Semantics / Non-Goals
- Existing <behavior> matching semantics remain unchanged.
- No new MCP tools or schema columns in this PR.
```

## Example validation block

```markdown
## Validation
### Lint
- `ruff check .` ✅

### Tests
- `pytest tests/test_topic.py -v` ✅
- Result: 6 passed

### Additional checks
- `pytest tests -q` ✅
- Result: 302 passed, 4 skipped
```

## Example Definition of Done block

```markdown
## Definition of Done
- [ ] All deliverables 1-6 are shipped.
- [ ] `ruff check .` passes.
- [ ] `pytest tests/test_topic.py -v` passes.
- [ ] Sentinel checks return expected results.
- [ ] Only in-scope files are modified.
- [ ] PR is opened against `master` with agreed title and branch.
```
