"""Tests for bench/grade.py: Grade schema + set_match grader (pure).

Task 11 — TDD: write failing tests first (RED), then implement (GREEN).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.grade import (
    Grade,
    GradeError,
    GRADE_DISPATCH,
    to_grade_dict,
    extract_simple_names,
    expected_simple_names,
    grade_set_match,
    extract_path,
    grade_path_match,
    extract_client_routes,
    grade_client_route_match,
    grade_absence,
    RUBRIC,
    TOOL_NAME_RE,
    blind_transcript,
    judge_answer,
    grade_cell,
    cohen_kappa,
    grade_run,
)
from bench.load_questions import Question

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
ANSWER_FIXTURE = FIXTURES / "answers" / "bc-impl-01_answer.txt"
ORACLE_FIXTURE = REPO_ROOT / "bench" / "oracle" / "expected" / "bc-impl-01.json"


# The 12 EventProcessor implementations the bc-impl-01 answer must surface.
TWELVE_PROCESSORS = {
    "AckProcessor",
    "ClientMessageProcessor",
    "CloseChatProcessor",
    "ComplianceHoldProcessor",
    "EscalationProcessor",
    "FallbackEventProcessor",
    "OperatorAssignedProcessor",
    "OperatorMessageProcessor",
    "ReadReceiptProcessor",
    "SessionReopenProcessor",
    "TransferProcessor",
    "TypingProcessor",
}


# --- Step 1: failing tests (per task brief) ---


def test_extract_simple_names_on_real_answer():
    """The real bc-impl-01 answer mentions all 12 processor simple-names."""
    text = ANSWER_FIXTURE.read_text()
    extracted = extract_simple_names(text)
    missing = TWELVE_PROCESSORS - extracted
    assert not missing, f"extract_simple_names missed: {sorted(missing)}"


def test_grade_set_match_perfect():
    """extracted == truth → f1 == 1.0, correctness == 1.0."""
    truth = {
        "kind": "symbol_set",
        "fqns": ["com.example.Foo", "com.example.Bar"],
        "ids": [],
    }
    # Construct a synthetic answer whose extraction is exactly the truth set.
    answer = "Foo Bar"
    g = grade_set_match(answer, truth)
    assert g.correctness == 1.0
    assert g.detail["f1"] == 1.0
    assert g.detail["precision"] == 1.0
    assert g.detail["recall"] == 1.0


def test_grade_set_match_partial():
    """truth has 12, answer extracts 8 correct + 0 spurious.

    recall == 8/12, precision == 1.0, f1 == harmonic mean.
    """
    truth = {
        "kind": "symbol_set",
        "fqns": [f"com.example.P{i}" for i in range(12)],
        "ids": [],
    }
    # Answer mentions exactly P0..P7 (8 of the 12 truth names, 0 spurious).
    answer = " ".join(f"P{i}" for i in range(8))
    g = grade_set_match(answer, truth)
    expected_precision = 1.0
    expected_recall = 8 / 12
    expected_f1 = (
        2 * expected_precision * expected_recall / (expected_precision + expected_recall)
    )
    assert g.detail["precision"] == pytest.approx(expected_precision)
    assert g.detail["recall"] == pytest.approx(expected_recall)
    assert g.detail["f1"] == pytest.approx(expected_f1)
    assert g.correctness == pytest.approx(expected_f1)


def test_grade_set_match_bc_impl_01():
    """Real bc-impl-01 answer grades >=0.95 against the oracle."""
    text = ANSWER_FIXTURE.read_text()
    expected = json.loads(ORACLE_FIXTURE.read_text())["expected"]
    g = grade_set_match(text, expected)
    assert g.correctness >= 0.95, (
        f"correctness={g.correctness} detail={g.detail}"
    )
    assert g.method == "set_match"
    assert g.judge_model is None
    assert g.detail["expected_n"] == 12


# --- Supplementary contract tests ---


def test_grade_schema_fields_and_frozen():
    """Grade has exactly correctness/method/detail/judge_model and is frozen."""
    g = Grade(
        correctness=0.5,
        method="set_match",
        detail={"f1": 0.5},
        judge_model=None,
    )
    assert set(g.__dataclass_fields__.keys()) == {
        "correctness",
        "method",
        "detail",
        "judge_model",
    }
    with pytest.raises(Exception):
        g.correctness = 0.9  # type: ignore[misc]


def test_to_grade_dict_roundtrips_json():
    """to_grade_dict produces a JSON-serializable dict with the 4 keys."""
    g = Grade(
        correctness=0.75,
        method="set_match",
        detail={"precision": 0.75, "recall": 1.0, "f1": 0.857},
        judge_model=None,
    )
    d = to_grade_dict(g)
    assert set(d.keys()) == {"correctness", "method", "detail", "judge_model"}
    # Round-trip JSON.
    s = json.dumps(d)
    d2 = json.loads(s)
    assert d2 == d
    assert d2["correctness"] == 0.75
    assert d2["method"] == "set_match"
    assert d2["judge_model"] is None


def test_expected_simple_names_dots():
    """expected_simple_names takes the last `.`-separated segment of each fqn."""
    expected = {
        "kind": "symbol_set",
        "fqns": [
            "com.bank.chat.engine.processors.AckProcessor",
            "com.bank.chat.engine.processors.TypingProcessor",
        ],
        "ids": [],
    }
    assert expected_simple_names(expected) == {"AckProcessor", "TypingProcessor"}


def test_grade_set_match_empty_extracted():
    """Empty extracted → all metrics 0 (precision denominator 0)."""
    g = grade_set_match(
        "no class names here at all",
        {"kind": "symbol_set", "fqns": ["com.example.Foo"], "ids": []},
    )
    assert g.detail["precision"] == 0
    assert g.detail["recall"] == 0
    assert g.detail["f1"] == 0
    assert g.correctness == 0


def test_grade_set_match_empty_truth():
    """Empty truth → all metrics 0 (recall denominator 0)."""
    g = grade_set_match(
        "Foo Bar Baz",
        {"kind": "symbol_set", "fqns": [], "ids": []},
    )
    assert g.detail["precision"] == 0
    assert g.detail["recall"] == 0
    assert g.detail["f1"] == 0
    assert g.correctness == 0


def test_extract_simple_names_drops_stopwords_and_lowercase():
    """Stopwords (The/A/Answer/Tools) and lowercase tokens are dropped."""
    text = "The Answer uses Tools from com.bank.chat to AckProcessor"
    extracted = extract_simple_names(text)
    assert extracted == {"AckProcessor"}


# --- Task 12: path / client_route / absence graders ---


def test_grade_path_match_ordered():
    """Controller -> Service -> Repo with truth [Controller, Service, Repo].

    ordered_match is True, correctness == 1.0.
    """
    expected = {
        "kind": "path",
        "hops": [
            {"fqn": "com.bank.Controller"},
            {"fqn": "com.bank.Service"},
            {"fqn": "com.bank.Repo"},
        ],
    }
    answer = "Controller -> Service -> Repo"
    g = grade_path_match(answer, expected)
    assert g.detail["ordered_match"] is True
    assert g.detail["jaccard"] == 1.0
    assert g.correctness == 1.0
    assert g.method == "path_match"


def test_grade_path_match_unordered_jaccard():
    """Answer has 2 of 3 hops, out of order.

    ordered_match is False, jaccard == 2/3, correctness == 2/3.
    """
    expected = {
        "kind": "path",
        "hops": [
            {"fqn": "com.bank.Controller"},
            {"fqn": "com.bank.Service"},
            {"fqn": "com.bank.Repo"},
        ],
    }
    # Answer omits Repo and reverses the remaining two.
    answer = "Service -> Controller"
    g = grade_path_match(answer, expected)
    assert g.detail["ordered_match"] is False
    assert g.detail["jaccard"] == pytest.approx(2 / 3)
    assert g.correctness == pytest.approx(2 / 3)
    assert g.method == "path_match"


def test_grade_client_route_match_partial():
    """Expected two pairs, answer has one → matched == 1, correctness == 0.5."""
    expected = {
        "kind": "client_route_pairs",
        "pairs": [
            {
                "client_fqn": "com.bank.ChatClient",
                "route": "POST /join",
                "target_service": "ChatService",
            },
            {
                "client_fqn": "com.bank.ChatClient",
                "route": "POST /leave",
                "target_service": "ChatService",
            },
        ],
    }
    # Answer mentions only the /join route.
    answer = "ChatClient calls POST /join on ChatService."
    g = grade_client_route_match(answer, expected)
    assert g.detail["matched"] == 1
    assert g.correctness == 0.5
    assert ("ChatClient", "POST /join") not in g.detail["missing"]
    assert ("ChatClient", "POST /leave") in g.detail["missing"]
    assert g.method == "client_route_match"


def test_grade_absence_correct():
    """Answer asserts absence; expected verdict not_in_project → match."""
    expected = {"kind": "absence", "verdict": "not_in_project", "proof": "..."}
    answer = "There is no Redis cache layer."
    g = grade_absence(answer, expected)
    assert g.detail["verdict_match"] is True
    assert g.detail["detected"] is True
    assert g.detail["expected_verdict"] is True
    assert g.correctness == 1.0
    assert g.method == "absence_check"


def test_grade_absence_wrong():
    """Answer asserts presence; expected verdict not_in_project → no match."""
    expected = {"kind": "absence", "verdict": "not_in_project", "proof": "..."}
    answer = "The Redis cache is in CacheService."
    g = grade_absence(answer, expected)
    assert g.detail["verdict_match"] is False
    assert g.detail["detected"] is False
    assert g.detail["expected_verdict"] is True
    assert g.correctness == 0.0
    assert g.method == "absence_check"


def test_extract_client_routes_template_paths():
    """``_ROUTE_RE`` captures path templates (``{id}``, ``:id``) and file/query
    suffixes (``.json``, ``?``) fully — not truncated at the special char.

    RED reasoning: pre-fix, the path char class ``[A-Za-z0-9_\\-/]+`` excluded
    ``{``, ``}``, ``:``, ``.``, ``?``, so for ``"POST /api/v1/users/{id}"`` the
    route captured was ``"POST /api/v1/users/"`` (path truncated at ``{``).
    That drops the template parameter from the route string, so any
    ``(client, route)`` pair compared against an oracle with the full
    ``"/api/v1/users/{id}"`` route would mismatch. Widening the char class to
    include ``{``, ``}``, ``:``, ``.``, ``?`` makes the capture cover the full
    template path.
    """
    pairs = extract_client_routes("ChatClient calls POST /api/v1/users/{id}")
    # Exactly one pair attributed to ChatClient with the full template route.
    assert ("ChatClient", "POST /api/v1/users/{id}") in pairs
    # Sanity: no truncated-route pair leaks through.
    assert ("ChatClient", "POST /api/v1/users/") not in pairs


# --- Task 13: condition-blinded LLM judge (blind_transcript + judge_answer) ---


# A transcript that mentions all four tool-name shapes the blinder must scrub:
# `mcp__jrag__neighbors`, `mcp__jrag__search`, `Grep`, `Read`. None of these
# literals may survive blind_transcript; each must be replaced by `[tool]`.
_BLIND_TRANSCRIPT = (
    "The assistant first called mcp__jrag__neighbors on AckProcessor, "
    "then ran mcp__jrag__search for 'typing' to find related handlers. "
    "It followed up with Grep for `@Component` annotations and used "
    "Read to inspect TypingProcessor.java. The chat domain has 12 processors."
)


def test_blind_transcript_scrubs_tool_names():
    """All four tool-name shapes are scrubbed to `[tool]` (≥4 placeholders)."""
    out = blind_transcript(_BLIND_TRANSCRIPT)
    # None of the tool-name literals survive.
    assert "mcp__jrag__neighbors" not in out
    assert "mcp__jrag__search" not in out
    assert "Grep" not in out
    assert "Read" not in out
    # The neutral placeholder appears at least once per scrubbed token (>=4).
    assert out.count("[tool]") >= 4


def test_blind_transcript_preserves_content():
    """Non-tool prose in the same transcript survives unchanged."""
    out = blind_transcript(_BLIND_TRANSCRIPT)
    # Each of these non-tool prose fragments must appear verbatim.
    assert "The assistant first called" in out
    assert "on AckProcessor" in out
    assert "for 'typing' to find related handlers" in out
    assert "annotations and used" in out
    assert "to inspect TypingProcessor.java" in out
    assert "The chat domain has 12 processors." in out


def test_blind_transcript_hides_jrag_verb_b_vs_d():
    """Plan 4: `jrag <verb>` is scrubbed so the judge can't tell B (search) from D (graph).

    Both `jrag search X` (vector-only) and `jrag callers X` (full graph) must
    fully consume the verb — reducing to the identical `[tool]` shape with no
    `jrag` and no verb leaked. A bare `jrag` in prose NOT followed by a known
    verb scrubs only `jrag`, preserving the next prose word.
    """
    out_b = blind_transcript("ran jrag search for 'typing'")
    out_d = blind_transcript("ran jrag callers on AckProcessor")
    for out in (out_b, out_d):
        assert "jrag" not in out
        assert "[tool]" in out
    # The verb itself is gone — not leaked as a B-vs-D signal.
    assert "search" not in out_b
    assert "callers" not in out_d
    # Prose "jrag <non-verb>": only `jrag` is scrubbed, the next word survives.
    prose = blind_transcript("the jrag is a tool")
    assert "jrag" not in prose
    assert "is a tool" in prose


@pytest.mark.requires_claude
def test_judge_answer_returns_grade():
    """judge_answer returns a Grade from a real glm-5.2 call.

    Uses a tiny synthetic transcript answering a trivial semantic question
    (2 + 2 = 4) correctly, so the judge call is cheap and reliably parses.
    """
    blinded = (
        "The assistant used [tool] to inspect the code and concluded: "
        "the value of 2 + 2 is 4."
    )
    question = "What is the value of 2 + 2?"
    expected = {"kind": "semantic", "answer": "4"}
    g = judge_answer(blinded, question, expected)
    assert g.method == "llm_judge"
    assert g.judge_model == "glm-5.2"
    assert 0.0 <= g.correctness <= 1.0
    assert g.detail.get("rationale")


def test_judge_answer_raises_on_unparseable(monkeypatch):
    """judge_answer raises GradeError when result.result is not valid JSON."""

    class _FakeCompleted:
        # An outer --output-format json envelope whose `result` is not JSON.
        stdout = '{"result": "this is not { valid json }"}'
        returncode = 0

    def _fake_run(argv, *args, **kwargs):
        return _FakeCompleted()

    monkeypatch.setattr("bench.grade.subprocess.run", _fake_run)
    with pytest.raises(GradeError):
        judge_answer("blinded", "q", {"kind": "semantic", "answer": "a"})


def test_judge_answer_parses_fenced_json(monkeypatch):
    """judge_answer strips ```json fences before parsing result."""

    class _FakeCompleted:
        # An outer --output-format json envelope whose `result` is a fenced JSON string.
        # After JSON parsing, \n becomes actual newline characters.
        stdout = '{"result": "```json\\n{\\"correctness\\": 0.8, \\"rationale\\": \\"factually correct.\\"}\\n```"}'
        returncode = 0

    def _fake_run(argv, *args, **kwargs):
        return _FakeCompleted()

    monkeypatch.setattr("bench.grade.subprocess.run", _fake_run)
    g = judge_answer("blinded", "q", {"kind": "semantic", "answer": "a"})
    assert g.correctness == 0.8
    assert g.method == "llm_judge"
    assert g.judge_model == "glm-5.2"
    assert g.detail["rationale"] == "factually correct."
    assert len(g.detail["rationale"]) > 0  # Non-empty


def test_judge_answer_timeout_raises_gradeerror(monkeypatch):
    """``judge_answer`` raises ``GradeError`` when the judge subprocess times out.

    RED reasoning: pre-fix, ``judge_answer`` called ``subprocess.run`` without
    a ``timeout=`` argument, so a hung judge process would block the grader
    forever (no per-cell wall-clock bound). The fix adds ``timeout=120`` and
    catches ``subprocess.TimeoutExpired`` → ``GradeError(\"judge timed out
    (>120s)\")``. With the per-cell tolerance added in ``grade_run`` (sibling
    test ``test_grade_run_continues_past_error_cell``), a hung judge becomes a
    single 0.0 cell instead of a stuck pipeline.
    """
    import subprocess as _subprocess

    def _fake_run(argv, *args, **kwargs):
        raise _subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 120))

    monkeypatch.setattr("bench.grade.subprocess.run", _fake_run)
    with pytest.raises(GradeError, match="timed out"):
        judge_answer("blinded", "q", {"kind": "semantic", "answer": "a"})


# --- Task 14: grade_cell dispatch + cohen_kappa + grade_run + CLI ---


def _make_question(qid: str, *, grading: str, question: str = "q?") -> Question:
    """Build a minimal valid Question for grade_cell dispatch tests."""
    return Question(
        id=qid,
        corpus="bank-chat",
        category="interface-impls",
        difficulty="easy",
        question=question,
        oracle_source="manual",
        claim_refs=["C1"],
        grading=grading,
    )


def test_grade_dispatch_map_covers_five_methods():
    """GRADE_DISPATCH maps all 5 grading values to their grader names."""
    assert GRADE_DISPATCH == {
        "programmatic_set_match": "set_match",
        "programmatic_path_match": "path_match",
        "programmatic_client_route_match": "client_route_match",
        "absence_check": "absence_check",
        "llm_judge": "llm_judge",
    }


def test_grade_cell_dispatch_set_match():
    """grade_cell dispatches programmatic_set_match -> grade_set_match.

    Cell with final_answer naming 2 of 2 expected symbols -> correctness 1.0,
    method == "set_match".
    """
    cell = {"final_answer": "Foo Bar"}
    question = _make_question("q1", grading="programmatic_set_match")
    expected = {
        "kind": "symbol_set",
        "fqns": ["com.example.Foo", "com.example.Bar"],
        "ids": [],
    }
    g = grade_cell(cell, "irrelevant transcript text", question, expected)
    assert g.method == "set_match"
    assert g.correctness == 1.0


def test_grade_cell_dispatch_judge(monkeypatch):
    """grade_cell dispatches llm_judge -> blind_transcript + judge_answer.

    Monkeypatches judge_answer to return a fixed Grade and captures the call:
    blinded transcript, question text, expected, judge_bin must all flow through.
    """
    question = _make_question(
        "q-judge",
        grading="llm_judge",
        question="What does X do?",
    )
    expected = {"kind": "semantic", "answer": "fact"}

    sentinel = Grade(
        correctness=0.9,
        method="llm_judge",
        detail={"rationale": "fake"},
        judge_model="glm-5.2",
    )
    captured: list[tuple] = []

    def fake_judge(blinded, q_text, exp, *, judge_bin="claude"):
        captured.append((blinded, q_text, exp, judge_bin))
        return sentinel

    monkeypatch.setattr("bench.grade.judge_answer", fake_judge)

    transcript_text = "Assistant called mcp__jrag__search then Read TypingProcessor.java"
    cell = {"final_answer": "unused for the judge path"}
    g = grade_cell(cell, transcript_text, question, expected, judge_bin="myjudge")

    assert g is sentinel
    assert len(captured) == 1
    blinded, q_text, exp, jbin = captured[0]
    # blind_transcript was applied before judge_answer
    assert "mcp__jrag__search" not in blinded
    assert "[tool]" in blinded
    assert q_text == question.question
    assert exp == expected
    assert jbin == "myjudge"


def test_grade_cell_dispatch_absence():
    """grade_cell dispatches absence_check -> grade_absence.

    Answer asserts absence + expected verdict not_in_project -> correctness 1.0,
    method == "absence_check".
    """
    question = _make_question(
        "q-abs",
        grading="absence_check",
        question="Is there a Redis cache?",
    )
    expected = {"kind": "absence", "verdict": "not_in_project", "proof": "..."}
    cell = {"final_answer": "There is no Redis cache layer."}
    g = grade_cell(cell, "transcript", question, expected)
    assert g.method == "absence_check"
    assert g.correctness == 1.0


def test_grade_cell_dispatch_none_final_answer():
    """grade_cell tolerates ``final_answer == None`` (capped runs).

    A capped cell writes JSON ``null`` for ``final_answer``; the programmatic
    graders must not TypeError on it. Normalized to "" -> set_match scores
    0.0 (no symbols matched).
    """
    question = _make_question("q-none", grading="programmatic_set_match")
    expected = {
        "kind": "symbol_set",
        "fqns": ["com.example.Foo", "com.example.Bar"],
        "ids": [],
    }
    cell = {"final_answer": None}
    g = grade_cell(cell, "transcript", question, expected)
    assert g.method == "set_match"
    assert g.correctness == 0.0


def test_grade_cell_capped_short_circuits_to_zero(monkeypatch):
    """grade_cell short-circuits exit_reason=="cap" to a deterministic 0.0.

    A capped cell produced no answer by definition. Plan 3: return
    Grade(0.0, method=<method>, detail={"reason": "cap"}) WITHOUT calling the
    grader/judge — no judge budget spent, no false-positive from the judge
    scoring the transcript's exploration. Verified on the llm_judge path (the
    one that would otherwise invoke the judge).
    """
    question = _make_question("q-cap", grading="llm_judge", question="q?")
    expected = {"kind": "semantic", "answer": "fact"}
    cell = {"exit_reason": "cap", "final_answer": "[BENCH_CAP: ...]"}

    def fake_judge(blinded, q_text, exp, *, judge_bin="claude"):
        raise AssertionError("judge_answer must not be called for a capped cell")

    monkeypatch.setattr("bench.grade.judge_answer", fake_judge)

    g = grade_cell(cell, "some transcript", question, expected)

    assert g.correctness == 0.0
    assert g.method == "llm_judge"
    assert g.detail == {"reason": "cap"}
    assert g.judge_model is None


def test_cohen_kappa_perfect():
    """judge_labels == human_labels (non-constant) -> kappa == 1.0."""
    judge = ["correct", "incorrect", "correct", "incorrect"]
    human = ["correct", "incorrect", "correct", "incorrect"]
    assert cohen_kappa(judge, human) == 1.0


def test_cohen_kappa_known_value():
    """Hand-computed kappa for a small mixed-agreement example.

    judge = ["yes", "yes", "no",  "no" ]
    human = ["yes", "yes", "yes", "no" ]
    n = 4
    agreements = positions 0, 1, 3 -> 3 -> p_o = 3/4 = 0.75
    count_judge: yes=2, no=2
    count_human: yes=3, no=1
    p_e = (2/4)*(3/4) + (2/4)*(1/4) = 6/16 + 2/16 = 8/16 = 0.5
    kappa = (p_o - p_e) / (1 - p_e) = (0.75 - 0.5) / (1 - 0.5) = 0.25/0.5 = 0.5
    """
    judge = ["yes", "yes", "no", "no"]
    human = ["yes", "yes", "yes", "no"]
    assert cohen_kappa(judge, human) == pytest.approx(0.5, abs=1e-4)


def test_grade_run_fills_grades(tmp_path):
    """grade_run reads cells.jsonl + transcripts + expected, writes grades.

    Two cells (set_match + absence_check, both correctness 1.0):
      - out_path gets 2 lines, each with non-null grade.
      - summary returns graded_n=2, by_method counts, mean_correctness=1.0,
        kappa=None (no human_labels_path).
    """
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()

    # Transcripts (cell["transcript_path"] is repo-relative; absolute also works).
    t1 = transcripts_dir / "t1.txt"
    t1.write_text("transcript one")
    t2 = transcripts_dir / "t2.txt"
    t2.write_text("transcript two")

    (expected_dir / "q-set.json").write_text(json.dumps({
        "question_id": "q-set",
        "expected": {
            "kind": "symbol_set",
            "fqns": ["com.example.Foo", "com.example.Bar"],
            "ids": [],
        },
    }))
    (expected_dir / "q-abs.json").write_text(json.dumps({
        "question_id": "q-abs",
        "expected": {
            "kind": "absence",
            "verdict": "not_in_project",
            "proof": "...",
        },
    }))

    cells = [
        {
            "run_id": "r1",
            "question_id": "q-set",
            "final_answer": "Foo Bar",
            "transcript_path": str(t1),
            "grade": None,
        },
        {
            "run_id": "r2",
            "question_id": "q-abs",
            "final_answer": "There is no Redis cache layer.",
            "transcript_path": str(t2),
            "grade": None,
        },
    ]
    cells_path = tmp_path / "cells.jsonl"
    cells_path.write_text("\n".join(json.dumps(c) for c in cells))

    questions = [
        _make_question("q-set", grading="programmatic_set_match"),
        _make_question("q-abs", grading="absence_check"),
    ]

    out_path = tmp_path / "graded.jsonl"
    summary = grade_run(
        str(cells_path),
        str(expected_dir),
        questions,
        out_path=str(out_path),
    )

    assert summary["graded_n"] == 2
    assert summary["by_method"] == {"set_match": 1, "absence_check": 1}
    assert summary["mean_correctness"] == pytest.approx(1.0)
    assert summary["kappa"] is None  # no human_labels_path

    # out_path has 2 lines, each with a non-null grade dict.
    lines = [ln for ln in out_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        d = json.loads(line)
        assert d["grade"] is not None
        assert "method" in d["grade"]
        assert "correctness" in d["grade"]


def test_grade_run_continues_past_error_cell(tmp_path, monkeypatch):
    """``grade_run`` writes-as-you-go and tolerates per-cell grade errors.

    2 cells (both set_match): cell 1 grades cleanly, cell 2's ``grade_cell``
    raises (monkeypatched). Pre-fix the buffered-write design meant cell 1's
    line sat in ``out_lines`` and was LOST when cell 2 raised — plus the run
    aborted. Post-fix the run continues, emits an ``_error`` grade line for
    cell 2 (correctness 0.0), and prior cells persist.

    RED reasoning: pre-fix, ``grade_run`` accumulated ``out_lines`` and wrote
    them once at the end. If any cell's ``grade_cell`` raised, the exception
    bypassed the final write, losing EVERY prior cell's grade. A single bad
    cell (transient judge failure, missing expected file, etc.) would
    therefore nuke the whole run's graded output. The fix opens ``out_path``
    once and writes each line as the cell is graded, wrapping each
    ``grade_cell`` in try/except so an error becomes a 0.0 ``<method>_error``
    grade line and the run continues. Summary gains an ``errors: int`` field.
    """
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()

    t1 = transcripts_dir / "t1.txt"
    t1.write_text("transcript one")
    t2 = transcripts_dir / "t2.txt"
    t2.write_text("transcript two")

    expected_record = {
        "question_id": "q-set",
        "expected": {
            "kind": "symbol_set",
            "fqns": ["com.example.Foo", "com.example.Bar"],
            "ids": [],
        },
    }
    (expected_dir / "q-set.json").write_text(json.dumps(expected_record))

    cells = [
        {
            "run_id": "r1",
            "question_id": "q-set",
            "final_answer": "Foo Bar",
            "transcript_path": str(t1),
            "grade": None,
        },
        {
            "run_id": "r2",
            "question_id": "q-set",
            "final_answer": "Foo Bar",
            "transcript_path": str(t2),
            "grade": None,
        },
    ]
    cells_path = tmp_path / "cells.jsonl"
    cells_path.write_text("\n".join(json.dumps(c) for c in cells))

    questions = [_make_question("q-set", grading="programmatic_set_match")]

    # Monkeypatch grade_cell so the SECOND call raises (cell 2 fails).
    real_grade_cell = grade_cell
    call_count = {"n": 0}

    def flaky_grade_cell(cell, transcript_text, question, expected, *, judge_bin="claude"):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated judge explosion on cell 2")
        return real_grade_cell(cell, transcript_text, question, expected, judge_bin=judge_bin)

    monkeypatch.setattr("bench.grade.grade_cell", flaky_grade_cell)

    out_path = tmp_path / "graded.jsonl"

    # Must not raise — the error is absorbed into an `_error` grade line.
    summary = grade_run(
        str(cells_path),
        str(expected_dir),
        questions,
        out_path=str(out_path),
    )

    # Both cells counted; one clean, one error.
    assert summary["graded_n"] == 2
    assert summary["errors"] == 1

    # out_path persisted BOTH lines (write-as-you-go, not buffered).
    lines = [ln for ln in out_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])
    # Cell 1 graded cleanly.
    assert first["run_id"] == "r1"
    assert first["grade"]["method"] == "set_match"
    assert first["grade"]["correctness"] == 1.0
    # Cell 2 became an error grade: method ends in `_error`, correctness 0.0,
    # detail carries the error message.
    assert second["run_id"] == "r2"
    assert second["grade"]["method"].endswith("_error")
    assert second["grade"]["correctness"] == 0.0
    assert "error" in second["grade"]["detail"]
    assert "simulated judge explosion" in second["grade"]["detail"]["error"]


def test_grade_to_judge_label_threshold():
    """_grade_to_judge_label binarizes at JUDGE_CORRECT_THRESHOLD, not ==1.0.

    Plan 3: a 0.90 answer is "correct". Boundary tests use the constant so they
    hold regardless of its exact value.
    """
    from bench.grade import _grade_to_judge_label, JUDGE_CORRECT_THRESHOLD

    def grade_with(c: float) -> Grade:
        return Grade(
            correctness=c, method="llm_judge", detail={}, judge_model="glm-5.2"
        )

    assert _grade_to_judge_label(grade_with(1.0)) == "correct"
    assert _grade_to_judge_label(grade_with(0.9)) == "correct"
    assert _grade_to_judge_label(grade_with(JUDGE_CORRECT_THRESHOLD)) == "correct"
    assert (
        _grade_to_judge_label(grade_with(JUDGE_CORRECT_THRESHOLD - 0.01))
        == "incorrect"
    )
    assert _grade_to_judge_label(grade_with(0.0)) == "incorrect"


def test_grade_run_emits_blinded_transcripts(tmp_path, monkeypatch):
    """grade_run emits <run_id>.blinded.txt for judged cells (Plan 3 kappa).

    The human kappa-gate must label the SAME blinded transcript the judge graded.
    A judged cell -> .blinded.txt equals blind_transcript(transcript); a
    programmatic cell -> no artifact.
    """
    from bench.grade import blind_transcript

    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()

    t_judge = transcripts_dir / "t_judge.txt"
    t_judge.write_text("Assistant called mcp__jrag__search then Read Foo.java")
    t_set = transcripts_dir / "t_set.txt"
    t_set.write_text("transcript set")

    (expected_dir / "q-judge.json").write_text(
        json.dumps(
            {"question_id": "q-judge", "expected": {"kind": "semantic", "answer": "x"}}
        )
    )
    (expected_dir / "q-set.json").write_text(
        json.dumps(
            {
                "question_id": "q-set",
                "expected": {"kind": "symbol_set", "fqns": ["com.example.Foo"], "ids": []},
            }
        )
    )

    cells = [
        {
            "run_id": "r-judge",
            "question_id": "q-judge",
            "final_answer": "x",
            "transcript_path": str(t_judge),
            "grade": None,
            "exit_reason": "done",
        },
        {
            "run_id": "r-set",
            "question_id": "q-set",
            "final_answer": "Foo",
            "transcript_path": str(t_set),
            "grade": None,
            "exit_reason": "done",
        },
    ]
    cells_path = tmp_path / "cells.jsonl"
    cells_path.write_text("\n".join(json.dumps(c) for c in cells))

    questions = [
        _make_question("q-judge", grading="llm_judge", question="q?"),
        _make_question("q-set", grading="programmatic_set_match"),
    ]

    def fake_judge(blinded, q_text, exp, *, judge_bin="claude"):
        return Grade(
            correctness=0.9,
            method="llm_judge",
            detail={"rationale": "fake"},
            judge_model="glm-5.2",
        )

    monkeypatch.setattr("bench.grade.judge_answer", fake_judge)

    out_path = tmp_path / "graded.jsonl"
    grade_run(str(cells_path), str(expected_dir), questions, out_path=str(out_path))

    # Judged cell: blinded artifact emitted, equals blind_transcript(transcript).
    blinded_path = tmp_path / "r-judge.blinded.txt"
    assert blinded_path.exists()
    text = blinded_path.read_text()
    assert text == blind_transcript(t_judge.read_text())
    assert "mcp__jrag__search" not in text
    assert "[tool]" in text

    # Programmatic cell: no blinded artifact.
    assert not (tmp_path / "r-set.blinded.txt").exists()
