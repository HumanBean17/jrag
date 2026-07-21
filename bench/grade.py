"""Grade schema + set_match grader (pure) — Task 11 (Phase 3 grading).

Pure: no I/O, no subprocess. Later tasks (T12-T14) add the other graders, the
LLM judge, dispatch, κ, and the CLI.

Public surface:
    Grade                       frozen dataclass: correctness/method/detail/judge_model
    to_grade_dict               JSON-serializable dict view of a Grade
    extract_simple_names        tokenize answer text → candidate class simple-names
    expected_simple_names       oracle `expected` block → set of simple names
    grade_set_match             precision/recall/f1 over the two sets
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# A token "looks like" a Java identifier if it starts with a letter/underscore/$
# and continues with letters/digits/underscore/$.
_JAVA_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

# Class simple-name shape: contains at least one uppercase letter. Filters out
# lowercase package segments (com, bank, chat, engine, processors) and pure
# digits (1, 12, ...) which fail this rule anyway.
_HAS_UPPERCASE_RE = re.compile(r"[A-Z]")

# Tokens that pass the shape test but are common prose/structural words rather
# than class simple-names. Kept small — only high-frequency false positives
# from typical LLM answers. Case-sensitive (these are the exact shapes that
# appear at the start of sentences or as section headings).
_STOPWORDS: frozenset[str] = frozenset({
    "The", "A", "An", "Answer", "Answers", "Tools", "I", "We",
    "This", "That", "These", "Those", "It", "They", "But", "And", "Or",
    "For", "With", "Note", "Notes", "Summary", "Explanation", "Analysis",
    "Conclusion", "Class", "Classes", "Interface", "Interfaces",
    "Method", "Methods",
})


@dataclass(frozen=True)
class Grade:
    """Result of grading a single answer.

    Attributes:
        correctness: Primary correctness score in [0, 1]. For set_match this
            equals the F1 of the predicted/expected simple-name sets.
        method: Grader name (e.g. ``"set_match"``, ``"llm_judge"``).
        detail: Method-specific metrics (precision/recall/f1, judge metadata).
        judge_model: For LLM-judged grades, the model id; ``None`` for pure
            graders (set_match).
    """

    correctness: float
    method: str
    detail: dict
    judge_model: str | None


def to_grade_dict(g: Grade) -> dict:
    """Serialize a Grade to a JSON-serializable dict.

    Returns a fresh dict with exactly the four Grade fields. ``detail`` is
    copied so callers cannot mutate the underlying Grade's detail via the
    returned dict.
    """
    return {
        "correctness": g.correctness,
        "method": g.method,
        "detail": dict(g.detail),
        "judge_model": g.judge_model,
    }


def extract_simple_names(text: str) -> set[str]:
    """Extract candidate class simple-names from free-form answer text.

    Tokenize on runs of non-identifier characters (anything outside
    ``[A-Za-z0-9_$]``). Keep tokens that:

      * match a Java identifier shape ``^[A-Za-z_$][A-Za-z0-9_$]*$``,
      * contain at least one uppercase letter (class simple-name shape —
        this also drops pure-number tokens like ``"12"``),
      * are not in the small stopword set (``The``, ``A``, ``An``,
        ``Answer``, ``Tools``, …).

    Args:
        text: Free-form answer text (markdown, prose, fenced code, etc.).

    Returns:
        Set of candidate class simple-names mentioned in the answer. May
        contain false positives (e.g. ``EventProcessor`` when only its
        implementations are expected); the grader treats these as spurious
        predictions, lowering precision.
    """
    out: set[str] = set()
    # re.split on a run of non-identifier chars produces empty strings at
    # boundaries (leading/trailing/adjacent separators); skip them.
    for token in re.split(r"[^A-Za-z0-9_$]+", text):
        if not token:
            continue
        if not _JAVA_IDENTIFIER_RE.match(token):
            continue
        if not _HAS_UPPERCASE_RE.search(token):
            continue
        if token in _STOPWORDS:
            continue
        out.add(token)
    return out


def expected_simple_names(expected: dict) -> set[str]:
    """Extract simple names from an oracle ``expected`` block.

    For each fqn in ``expected["fqns"]``, take the substring after the last
    dot (e.g. ``com.bank.chat.engine.processors.AckProcessor`` →
    ``AckProcessor``). Missing ``fqns`` is treated as empty.
    """
    return {fqn.rsplit(".", 1)[-1] for fqn in expected.get("fqns", [])}


def grade_set_match(answer_text: str, expected: dict) -> Grade:
    """Grade ``answer_text`` against ``expected`` (a ``symbol_set`` oracle).

    extracted = extract_simple_names(answer_text)
    truth     = expected_simple_names(expected)
    tp        = |extracted ∩ truth|
    precision = tp / |extracted|   (0 if extracted is empty)
    recall    = tp / |truth|       (0 if truth is empty)
    f1        = 2·precision·recall / (precision + recall)   (0 if denom is 0)
    correctness = f1
    method      = "set_match"
    judge_model = None
    """
    extracted = extract_simple_names(answer_text)
    truth = expected_simple_names(expected)

    tp = len(extracted & truth)
    predicted_n = len(extracted)
    expected_n = len(truth)

    precision = tp / predicted_n if predicted_n else 0.0
    recall = tp / expected_n if expected_n else 0.0
    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom else 0.0

    detail = {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "predicted_n": predicted_n,
        "expected_n": expected_n,
    }
    return Grade(
        correctness=f1,
        method="set_match",
        detail=detail,
        judge_model=None,
    )
