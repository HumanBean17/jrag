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
)

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
