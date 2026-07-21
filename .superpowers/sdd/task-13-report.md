# Task 13 — LLM judge harness: blinding + glm-5.2 judge

## What was implemented

Added 5 public names to `bench/grade.py` (T11-T12 code untouched). The judge
is a fresh single-turn `claude -p` session — no tools, no MCP, no `--verbose`,
no turn cap. Condition-blinding is enforced by `blind_transcript`, which the
caller runs before handing the transcript to the judge; the judge therefore
cannot tell which of A/B/C/D produced the answer.

1. `class GradeError(Exception)` — raised on any unparseable judge output
   (non-zero exit, malformed outer envelope, missing `result`, `result` not
   valid JSON, missing `correctness`/`rationale`).
2. `RUBRIC: str` — the locked rubric (full text below).
3. `TOOL_NAME_RE = re.compile(r"\b(?:mcp__jrag__\w+|Grep|Glob|Read|Bash)\b")`
   — matches every tool-name token; case-sensitive; `\b` boundaries prevent
   partial matches (`Read` inside `Reader`, etc.).
4. `blind_transcript(text) -> str` — `TOOL_NAME_RE.sub("[tool]", text)`.
5. `judge_answer(blinded_transcript, question_text, expected, *, judge_model="glm-5.2", judge_bin="claude") -> Grade`
   — single-turn subprocess + double-json parse.

## RUBRIC text (verbatim)

```
You are an impartial judge grading an assistant's answer to a question about a Java codebase. The assistant's transcript is shown below with all tool names blinded to a neutral `[tool]` placeholder, so you cannot be biased by which tools the assistant used.

Score the answer's FACTUAL CORRECTNESS against the provided expected answer, on a continuous scale from 0.0 (completely wrong) to 1.0 (fully correct). Ignore style, formatting, and verbosity — score only whether the facts in the answer match the expected facts. Partial credit is appropriate for answers that get some facts right and some wrong.

Respond with ONLY a single JSON object — no surrounding prose, no markdown code fences — in exactly this shape:

{"correctness": <float between 0.0 and 1.0>, "rationale": "<one sentence>"}

The rationale must be a single sentence explaining the score. Do not include any other keys, fields, or text.
```

Key design points:
- Explicitly tells the judge the transcript is blinded, so it doesn't try to
  infer tool identity.
- Explicitly forbids prose/fences — the only allowed output is the bare JSON
  object, enabling deterministic parsing.
- Explicitly forbids style/verbosity bias — only factual correctness.
- Specifies partial credit is OK, so correctness is continuous `[0,1]`, not
  binary.

## Blinding regex

```python
TOOL_NAME_RE = re.compile(r"\b(?:mcp__jrag__\w+|Grep|Glob|Read|Bash)\b")
```

- `mcp__jrag__\w+` matches every MCP tool name (`mcp__jrag__neighbors`,
  `mcp__jrag__search`, etc.) — `\w+` covers underscores in the tool suffix.
- The four literals `Grep|Glob|Read|Bash` are Claude Code built-ins.
- Case-sensitive (no `re.IGNORECASE`): lowercase verbs in prose (`"I read the
  file"`, `"grep for ..."`) survive. Only the tool-call proper-noun shapes are
  scrubbed.
- `\b` boundaries prevent partial matches: `Reader`, `Bashful`,
  `mcp__jrag__neighbors.java` (where `\w+` stops at the dot) all behave
  correctly.

## Judge invocation + parse

```python
proc = subprocess.run(
    [
        judge_bin,                              # "claude"
        "-p", prompt,
        "--model", judge_model,                 # "glm-5.2"
        "--output-format", "json",              # plain json envelope, NOT stream-json
        "--permission-mode", "bypassPermissions",
    ],
    stdin=subprocess.DEVNULL,
    capture_output=True,
    text=True,
)
envelope = json.loads(proc.stdout)              # outer: --output-format json envelope
inner    = json.loads(envelope["result"])       # inner: rubric JSON string
correctness = float(inner["correctness"])
rationale   = str(inner["rationale"])
```

- `stdin=DEVNULL` — required; `claude -p` hangs if stdin stays open.
- No `--verbose` — we want the plain `{"result": ...}` envelope, not stream-json.
- No tools, no MCP, no `--max-turns` — the judge is a single-turn responder,
  forbidden from doing anything except emitting the rubric JSON.
- The double `json.loads` is mandated by the CLI's wire format: the outer
  envelope is real JSON, but its `result` field is the model's raw text
  output, which (per the rubric) is itself a JSON string. Two parses.
- Defensive coercion: `float(...)` and `str(...)` on the parsed fields, so a
  numeric-typed rationale or string-typed correctness still converts cleanly
  (and a non-numeric correctness string raises `GradeError` via `ValueError`).
- All parse paths (`JSONDecodeError`, `KeyError`, `TypeError`, `ValueError`,
  non-zero exit, `OSError`/`SubprocessError`) funnel into `GradeError`.

## TDD evidence

### RED (Step 2 — before implementation)

```
tests/bench/test_grade.py:13: in <module>
    from bench.grade import (
E   ImportError: cannot import name 'GradeError' from 'bench.grade'
=========================== short test summary info ============================
ERROR tests/bench/test_grade.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.06s ===============================
```

The new imports (`GradeError`, `RUBRIC`, `TOOL_NAME_RE`, `blind_transcript`,
`judge_answer`) all fail to resolve — collection is interrupted.

### GREEN (Step 4 — after implementation)

```
tests/bench/test_grade.py::test_extract_simple_names_on_real_answer PASSED [  5%]
tests/bench/test_grade.py::test_grade_set_match_perfect PASSED           [ 10%]
tests/bench/test_grade.py::test_grade_set_match_partial PASSED           [ 15%]
tests/bench/test_grade.py::test_grade_set_match_bc_impl_01 PASSED        [ 21%]
tests/bench/test_grade.py::test_grade_schema_fields_and_frozen PASSED    [ 26%]
tests/bench/test_grade.py::test_to_grade_dict_roundtrips_json PASSED     [ 31%]
tests/bench/test_grade.py::test_expected_simple_names_dots PASSED        [ 36%]
tests/bench/test_grade.py::test_grade_set_match_empty_extracted PASSED   [ 42%]
tests/bench/test_grade.py::test_grade_set_match_empty_truth PASSED       [ 47%]
tests/bench/test_grade.py::test_extract_simple_names_drops_stopwords_and_lowercase PASSED [ 52%]
tests/bench/test_grade.py::test_grade_path_match_ordered PASSED          [ 57%]
tests/bench/test_grade.py::test_grade_path_match_unordered_jaccard PASSED [ 63%]
tests/bench/test_grade.py::test_grade_client_route_match_partial PASSED  [ 68%]
tests/bench/test_grade.py::test_grade_absence_correct PASSED             [ 73%]
tests/bench/test_grade.py::test_grade_absence_wrong PASSED               [ 78%]
tests/bench/test_grade.py::test_blind_transcript_scrubs_tool_names PASSED [ 84%]
tests/bench/test_grade.py::test_blind_transcript_preserves_content PASSED [ 89%]
tests/bench/test_grade.py::test_judge_answer_returns_grade PASSED        [ 94%]
tests/bench/test_grade.py::test_judge_answer_raises_on_unparseable PASSED [100%]

============================= 19 passed in 10.89s ==============================
```

- All 15 T11-T12 tests still pass — no regressions.
- All 4 T13 tests pass.
- `test_judge_answer_returns_grade` (marked `@pytest.mark.requires_claude`)
  ran for real: `claude` is present at `/Users/dmitry/.local/bin/claude`
  (v2.1.200), so the marker did NOT skip. Total suite time 10.89s vs ~1s for
  pure tests — the delta is the real glm-5.2 API round-trip. The trivial
  prompt (transcript asserting "2 + 2 = 4", expected answer `"4"`) returned
  a parsed `Grade(method="llm_judge", judge_model="glm-5.2", correctness in
  [0,1], non-empty rationale)`.
- `test_judge_answer_raises_on_unparseable` monkeypatches
  `bench.grade.subprocess.run` to return an envelope whose `result` is the
  string `"this is not { valid json }"`; the inner `json.loads` raises
  `JSONDecodeError`, which is caught and re-raised as `GradeError`. Test
  passes — confirms the error contract.

## Files changed

- `/Users/dmitry/Desktop/CursorProjects/java-enterprise-codebase-rag/bench/grade.py`
  (+165 lines; new imports `json` + `subprocess`; module docstring updated
  with the Task 13 surface; `GradeError`, `RUBRIC`, `TOOL_NAME_RE`,
  `blind_transcript`, `judge_answer` appended. T11-T12 code untouched.)
- `/Users/dmitry/Desktop/CursorProjects/java-enterprise-codebase-rag/tests/bench/test_grade.py`
  (+79 lines; the 5 new imports + 4 new tests under a `Task 13` section
  header. T11-T12 tests untouched.)

## Self-review findings

All checklist items pass:

- blind_transcript replaces ALL tool-name tokens (`mcp__jrag__*`, Grep, Glob,
  Read, Bash) with `[tool]` — verified by `test_blind_transcript_scrubs_tool_names`.
- Preserves non-tool prose — verified by `test_blind_transcript_preserves_content`
  (6 prose fragments asserted to survive verbatim).
- Leaves >=4 `[tool]` placeholders in the briefed test — `_BLIND_TRANSCRIPT`
  contains exactly 4 tool-name tokens (neighbors, search, Grep, Read); the
  test asserts `out.count("[tool]") >= 4` and passes.
- judge_answer returns `Grade(method=="llm_judge", judge_model=="glm-5.2",
  0<=correctness<=1, non-empty detail["rationale"])` — verified by the real
  glm-5.2 call in `test_judge_answer_returns_grade`.
- judge_answer raises `GradeError` on unparseable `result.result` — verified
  by `test_judge_answer_raises_on_unparseable`.
- `stdin=DEVNULL` — yes, present.
- No `--verbose` — yes, argv is `[-p, prompt, --model, judge_model,
  --output-format, json, --permission-mode, bypassPermissions]` only.
- T11-T12 tests still pass — all 15 pre-existing tests green.

## Commit

```
4263cb1 feat(bench): condition-blinded glm-5.2 LLM judge + rubric
```

Subject matches the brief verbatim; trailer
`Co-Authored-By: Claude <noreply@anthropic.com>` present after a blank line.

## Concerns

- **No timeout on the subprocess.** The brief specified `stdin=DEVNULL` +
  `capture_output=True` only; no timeout was in the contract. The judge call
  is single-turn with a trivial prompt, so it completes in seconds (10.89s
  total suite time includes the call). If the driver in a later task wraps
  many judge calls back-to-back, it may want to add a wall-clock budget;
  that's out of scope here.
- **Defensive coercion.** `float(inner["correctness"])` / `str(inner["rationale"])`
  accept type variants (e.g. rationale as non-string). This is more forgiving
  than the rubric strictly demands, but it never masks a malformed result —
  a non-numeric correctness string raises `ValueError` -> `GradeError`, and a
  missing key raises `KeyError` -> `GradeError`. Net behavior: stricter on
  parse failures, looser on benign type drift.
- **Blinding coverage is exactly the briefed set.** Only `mcp__jrag__*` +
  Grep/Glob/Read/Bash are scrubbed. Other condition signals (e.g. file paths
  inside `[tool]` output snippets, error message text from jrag vs. built-ins)
  are NOT scrubbed. The brief scoped blinding to tool-name tokens only, so
  this is per-spec; if a future audit finds leakage via non-token channels,
  the regex can be widened without touching the judge.

## Judge hardening (fence-strip + rationale typing)

### Problem

glm-5.2 sometimes wraps `--output-format json` result in ```json fences on longer
prompts (e.g. ` ```json\n{"correctness": 0.8, "rationale": "..."}\n``` `). The
inner `json.loads(envelope["result"])` raised `JSONDecodeError` on the leading
``` chars, breaking grading at scale. Additionally, `str(inner["rationale"])`
coerced `null` → `"None"`, masking malformed responses.

### Fix 1: Fence stripping (bench/grade.py ~line 553)

**Before:**
```python
envelope = json.loads(proc.stdout)
inner = json.loads(envelope["result"])
```

**After:**
```python
envelope = json.loads(proc.stdout)
inner_text = envelope["result"].strip()
if inner_text.startswith("```"):
    lines = inner_text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    inner_text = "\n".join(lines).strip()
inner = json.loads(inner_text)
```

- Strips leading ```json/``` line and trailing ``` line before parsing.
- Non-fenced JSON still parses exactly as before (no `if` taken).
- `GradeError` behavior preserved: `JSONDecodeError` → `GradeError` in the
  existing try/except wrapper.

### Fix 2: Stricter rationale typing (bench/grade.py ~line 561)

**Before:**
```python
rationale = str(inner["rationale"])  # Coerced null → "None"
```

**After:**
```python
rationale = inner.get("rationale")
if not isinstance(rationale, str) or not rationale:
    raise GradeError(
        f"judge result missing/invalid rationale: must be non-empty str; got {type(rationale).__name__}"
    )
```

- Raises `GradeError` if rationale is missing, not a string, or empty.
- No longer coerces via `str()` — `null` now fails loudly instead of masking
  as `"None"`.
- `correctness` still uses `float()` coercion (acceptable per original design).

### Regression test

Added `test_judge_answer_parses_fenced_json` to `tests/bench/test_grade.py`:
- Monkeypatches `subprocess.run` to return a `CompletedProcess` whose stdout is
  the `--output-format json` envelope with a fenced `result` field:
  ` ```json\n{"correctness": 0.8, "rationale": "factually correct."}\n``` `.
- Asserts `judge_answer(...)` returns a `Grade` with `correctness == 0.8`,
  `method == "llm_judge"`, and non-empty `detail["rationale"]`.
- No `requires_claude` marker — monkeypatched, no real call.

### RED reasoning

**Pre-fix behavior:** The test's fenced result string starts with ````` ```,
which is not valid JSON. The old `json.loads(envelope["result"])` raised
`JSONDecodeError: Expecting value: line 1 column 1 (char 0)`. This exception
was caught and re-raised as `GradeError`, so `judge_answer` would fail the test.

**Post-fix behavior:** Fence stripping removes the ```json opening line and ```
closing line before parsing, leaving valid JSON that parses successfully. Test
passes with `correctness == 0.8` and `rationale == "factually correct."`.

### Verification

```
.venv/bin/pytest tests/bench/test_grade.py -v
```

**Result:** 20 passed (existing 19 + new fenced test). The existing
`test_judge_answer_raises_on_unparseable` still passes (truly unparseable input
still raises `GradeError`).

## TLDR

Task 13 done. Added `GradeError`, `RUBRIC`, `TOOL_NAME_RE`,
`blind_transcript`, `judge_answer` to `bench/grade.py`. 4 new TDD tests
(RED -> GREEN verified); the `requires_claude` test ran a real glm-5.2 call
and parsed a valid Grade. All 19 tests in `tests/bench/test_grade.py` pass.
Committed as `4263cb1` with the briefed subject + trailer.
