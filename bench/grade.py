"""Grade schema + set_match grader (pure) — Task 11 (Phase 3 grading).

Pure: no I/O, no subprocess. Later tasks (T12-T14) add the other graders, the
LLM judge, dispatch, κ, and the CLI.

Public surface:
    Grade                       frozen dataclass: correctness/method/detail/judge_model
    to_grade_dict               JSON-serializable dict view of a Grade
    extract_simple_names        tokenize answer text → candidate class simple-names
    expected_simple_names       oracle `expected` block → set of simple names
    grade_set_match             precision/recall/f1 over the two sets

Task 12 additions:
    extract_path                tokenize answer text → ordered hop simple-names
    grade_path_match            ordered_match + jaccard over hop sequences
    extract_client_routes       tokenize answer text → set of (client, route) pairs
    grade_client_route_match    matched/missing/spurious over the pair set
    grade_absence               verdict_match against absence-signal detection

Task 13 additions:
    GradeError                  raised when the LLM judge result cannot be parsed
    RUBRIC                      locked scoring rubric sent to the judge model
    TOOL_NAME_RE                matches tool-name tokens to scrub (mcp__jrag__* +
                                Grep/Glob/Read/Bash)
    blind_transcript            scrub tool-name tokens → `[tool]` (condition blinding)
    judge_answer                single-turn claude CLI call → Grade (llm_judge)

Task 14 additions:
    GRADE_DISPATCH              Question.grading value → grader name
    grade_cell                  top-level dispatch: cell+transcript+Question+expected → Grade
    cohen_kappa                 inter-rater agreement κ over two equal-length label lists
    grade_run                   per-run grader: reads cells.jsonl, writes graded.jsonl, summary
    main                        argparse CLI entry: --cells/--expected/--questions-glob/...
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from bench.load_questions import Question, load_all_questions


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

# --- Task 12: path / client_route / absence extraction heuristics ---

# A path hop separator: ASCII/Unicode arrows, the prose connector "then",
# newlines (numbered lists, one item per line), or semicolons. Surrounded by
# optional whitespace so re.split does not leave leading/trailing space on the
# resulting segments.
_PATH_SEPARATOR_RE = re.compile(
    r"\s*(?:->|→|\bthen\b|;|\n)\s*",
    re.IGNORECASE,
)

# Leading list-numbering / bullet to strip from each path segment after split.
_PATH_LEADING_BULLET_RE = re.compile(r"^\s*(?:\d+[.)]\s*|[-*•]\s+)")

# A single Java identifier token, for scanning a path segment's tokens.
_IDENT_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

# HTTP method alternatives, used to spot route mentions in client_route answers.
_HTTP_METHODS = (
    r"GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE|CONNECT"
)
# Path char class for an HTTP route. Includes path-template characters
# (``{``, ``}`` for ``/users/{id}``; ``:`` for ``/users/:id``), file/extension
# dots (``.`` for ``/users.json``), and the query delimiter (``?``) so routes
# with these are captured fully rather than truncated at the special char.
_ROUTE_PATH_CHARS = r"A-Za-z0-9_\-/{}:.?"
# An HTTP route: METHOD followed by whitespace and a slash-leading path. The
# method is captured case-insensitively (answers sometimes write "post /join")
# and normalized to uppercase before pairing.
_ROUTE_RE = re.compile(
    rf"\b({_HTTP_METHODS})\s+(/[{_ROUTE_PATH_CHARS}]+)",
    re.IGNORECASE,
)

# Absence-signal phrases — answer asserts that something is missing.
_ABSENCE_SIGNAL_RE = re.compile(
    r"there is no"
    r"|does not exist"
    r"|not present"
    r"|\bno\s",
    re.IGNORECASE,
)


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


# --- Task 12: path / client_route / absence graders ---


def extract_path(answer_text: str) -> list[str]:
    """Extract the ordered hop sequence (simple-names) from ``answer_text``.

    Tolerant of formatting variants:

      * ASCII/Unicode arrows: ``Controller -> Service -> Repo``,
        ``Controller → Service → Repo``.
      * Prose connector: ``Controller then Service then Repo``.
      * Numbered lists (one item per line)::

          1. Controller
          2. Service
          3. Repo

      * Semicolon-separated: ``Controller; Service; Repo``.

    After splitting on the separator regex, each segment has its leading
    bullet/numbering stripped, then the LAST Java identifier token with an
    uppercase letter (and not in the stopword set) is taken as the hop's
    simple-name. Picking the last uppercase identifier lets a segment like
    ``com.bank.Controller`` resolve to ``Controller``; for single-token
    segments (the common case) it is a no-op.

    Returns:
        Ordered list of hop simple-names. Empty if no hop-like token was
        found in any segment.
    """
    out: list[str] = []
    for segment in _PATH_SEPARATOR_RE.split(answer_text):
        segment = _PATH_LEADING_BULLET_RE.sub("", segment).strip()
        if not segment:
            continue
        picked: str | None = None
        for tok in _IDENT_TOKEN_RE.findall(segment):
            if not _HAS_UPPERCASE_RE.search(tok):
                continue
            if tok in _STOPWORDS:
                continue
            picked = tok
        if picked is not None:
            out.append(picked)
    return out


def grade_path_match(answer_text: str, expected: dict) -> Grade:
    """Grade ``answer_text`` against ``expected`` (a ``path`` oracle).

    Compares the extracted ordered hop sequence to the oracle's hop sequence
    (the simple-name of each ``hops[i]["fqn"]``).

    Metrics in ``detail``:

      * ``ordered_match``: ``got == truth`` (exact sequence equality).
      * ``jaccard``: ``|got ∩ truth| / |got ∪ truth|`` (0 if union empty),
        treating the sequences as sets.

    ``correctness`` is ``1.0`` when ``ordered_match`` is true; otherwise it
    falls back to ``jaccard`` (so the same set out of order still scores the
    set-overlap fraction — 1.0 for same-set-different-order, 2/3 for a
    2-of-3-out-of-order answer).
    """
    got = extract_path(answer_text)
    truth = [h["fqn"].rsplit(".", 1)[-1] for h in expected["hops"]]

    ordered_match = got == truth
    got_set = set(got)
    truth_set = set(truth)
    union = got_set | truth_set
    jaccard = (len(got_set & truth_set) / len(union)) if union else 0.0

    detail = {"ordered_match": ordered_match, "jaccard": jaccard}
    correctness = 1.0 if ordered_match else jaccard
    return Grade(
        correctness=correctness,
        method="path_match",
        detail=detail,
        judge_model=None,
    )


def extract_client_routes(answer_text: str) -> set[tuple[str, str]]:
    """Extract ``(client_simple_name, route)`` pairs from ``answer_text``.

    For each HTTP route mention of the form ``METHOD /path`` (e.g.
    ``POST /join``, ``GET /api/v1/users``), pair the route with the nearest
    preceding class simple-name token. The method is normalized to uppercase
    so that ``post /join`` and ``POST /join`` produce the same pair.

    This nearest-preceding-client heuristic handles the common shapes:

      * ``ChatClient calls POST /join on ChatService`` →
        ``("ChatClient", "POST /join")``.
      * ``ChatClient invokes POST /join and POST /leave`` →
        both pairs attributed to ``ChatClient``.

    Returns:
        Set of ``(client_simple_name, route)`` pairs. Routes with no
        preceding client token are skipped.
    """
    # Pre-compute the positions of all candidate class simple-names.
    candidates: list[tuple[int, str]] = []
    for m in _IDENT_TOKEN_RE.finditer(answer_text):
        tok = m.group(0)
        if not _HAS_UPPERCASE_RE.search(tok):
            continue
        if tok in _STOPWORDS:
            continue
        candidates.append((m.start(), tok))

    out: set[tuple[str, str]] = set()
    for rm in _ROUTE_RE.finditer(answer_text):
        method = rm.group(1).upper()
        path = rm.group(2)
        route = f"{method} {path}"
        # Scan candidates in reverse to find the nearest one strictly before
        # the route's start. This naturally excludes the HTTP method token
        # itself (which sits at the same offset as rm.start()).
        client: str | None = None
        for start, name in reversed(candidates):
            if start < rm.start():
                client = name
                break
        if client is None:
            continue
        out.add((client, route))
    return out


def grade_client_route_match(answer_text: str, expected: dict) -> Grade:
    """Grade ``answer_text`` against ``expected`` (a ``client_route_pairs`` oracle).

    Compares the extracted ``(client_simple_name, route)`` pairs to the
    oracle's pairs (simple-name of ``client_fqn`` paired with the literal
    ``route`` string).

    Metrics in ``detail``:

      * ``matched``: ``|got ∩ truth|``.
      * ``missing``: ``sorted(truth - got)`` (truth pairs the answer missed).
      * ``spurious``: ``sorted(got - truth)`` (extra pairs the answer added).

    ``correctness`` is ``matched / len(truth)`` (0 if truth is empty).
    """
    got = extract_client_routes(answer_text)
    truth = {
        (p["client_fqn"].rsplit(".", 1)[-1], p["route"])
        for p in expected["pairs"]
    }

    matched = len(got & truth)
    missing = sorted(truth - got)
    spurious = sorted(got - truth)

    detail = {
        "matched": matched,
        "missing": missing,
        "spurious": spurious,
    }
    correctness = (matched / len(truth)) if truth else 0.0
    return Grade(
        correctness=correctness,
        method="client_route_match",
        detail=detail,
        judge_model=None,
    )


def grade_absence(answer_text: str, expected: dict) -> Grade:
    """Grade ``answer_text`` against ``expected`` (an ``absence`` oracle).

    Detects whether the answer asserts absence via phrases such as
    ``"there is no"``, ``"does not exist"``, ``"not present"``, or
    ``"no <something>"``. Compares that detected signal to the oracle's
    expected verdict (``"not_in_project"`` ⇒ expected to assert absence).

    Metrics in ``detail``:

      * ``detected``: the answer asserts absence (bool).
      * ``expected_verdict``: oracle's verdict means absence is expected.
      * ``verdict_match``: ``detected == expected_verdict``.

    ``correctness`` is ``1.0`` when ``verdict_match`` is true, else ``0.0``.
    """
    detected = bool(_ABSENCE_SIGNAL_RE.search(answer_text))
    expected_verdict = expected.get("verdict") == "not_in_project"
    verdict_match = detected == expected_verdict

    detail = {
        "verdict_match": verdict_match,
        "detected": detected,
        "expected_verdict": expected_verdict,
    }
    correctness = 1.0 if verdict_match else 0.0
    return Grade(
        correctness=correctness,
        method="absence_check",
        detail=detail,
        judge_model=None,
    )


# --- Task 13: condition-blinded LLM judge ---


class GradeError(Exception):
    """Raised when an LLM judge result cannot be parsed into a Grade.

    The judge is invoked via the ``claude`` CLI with ``--output-format json``;
    a non-zero return code, a malformed outer envelope, a ``result`` field
    that is not valid JSON, or a missing ``correctness``/``rationale`` key
    all raise this. The benchmark driver decides whether to retry or mark
    the cell as failed.
    """


# Locked scoring rubric sent to the judge. The judge MUST reply with ONLY the
# specified JSON object — no prose, no fences — so the driver can parse the
# score deterministically. Scoring is factual correctness against the expected
# answer; style/verbosity are explicitly ignored to avoid length bias.
RUBRIC = """You are an impartial judge grading an assistant's answer to a question about a Java codebase. The assistant's transcript is shown below with all tool names blinded to a neutral `[tool]` placeholder, so you cannot be biased by which tools the assistant used.

Score the answer's FACTUAL CORRECTNESS against the provided expected answer, on a continuous scale from 0.0 (completely wrong) to 1.0 (fully correct). Ignore style, formatting, and verbosity — score only whether the facts in the answer match the expected facts. Partial credit is appropriate for answers that get some facts right and some wrong.

Respond with ONLY a single JSON object — no surrounding prose, no markdown code fences — in exactly this shape:

{"correctness": <float between 0.0 and 1.0>, "rationale": "<one sentence>"}

The rationale must be a single sentence explaining the score. Do not include any other keys, fields, or text."""


# Tool-name tokens to scrub from the transcript before the judge sees it.
# Matches any ``mcp__jrag__<name>`` token (the MCP server's tool-name shape)
# and the four Claude Code built-in tool literals. Case-sensitive: the
# lowercase ``read``/``grep`` verbs in prose must survive. ``\b`` word
# boundaries prevent partial matches (e.g. ``Read`` inside ``Reader``).
TOOL_NAME_RE = re.compile(r"\b(?:mcp__jrag__\w+|Grep|Glob|Read|Bash)\b")


def blind_transcript(transcript_text: str) -> str:
    """Replace every tool-name token with the neutral placeholder ``[tool]``.

    The judge must not see which condition (A/B/C/D) produced the answer —
    the ``mcp__jrag__*`` tokens appear only under the jrag conditions, and
    ``Grep``/``Glob``/``Read``/``Bash`` appear only under the no-MCP
    conditions. Scrubbing all of them to ``[tool]`` removes the condition
    signal while preserving the structure of the transcript (number of tool
    calls, their position in the reasoning, the surrounding prose).

    Args:
        transcript_text: The raw assistant transcript (any text).

    Returns:
        The transcript with every ``TOOL_NAME_RE`` match replaced by
        ``[tool]``. Non-tool prose is unchanged.
    """
    return TOOL_NAME_RE.sub("[tool]", transcript_text)


def judge_answer(
    blinded_transcript: str,
    question_text: str,
    expected: dict,
    *,
    judge_model: str = "glm-5.2",
    judge_bin: str = "claude",
) -> Grade:
    """Grade a blinded transcript with a single-turn LLM judge call.

    Builds one prompt = ``RUBRIC`` + the question + the expected-answer
    summary + the blinded transcript, and invokes::

        judge_bin -p "<prompt>"
            --model judge_model
            --output-format json
            --permission-mode bypassPermissions

    with ``stdin=DEVNULL``. The judge is a fresh single-turn session: no
    tools, no MCP, no ``--verbose`` (plain JSON, not stream-json), no turn
    cap — it only emits the rubric JSON.

    The outer ``--output-format json`` envelope is ``{"result": "..."}``
    where ``result`` is the judge's raw output as a string. That string is
    itself the rubric JSON ``{"correctness": ..., "rationale": ...}``, so
    the parse is ``json.loads(json.loads(stdout)["result"])``.

    Args:
        blinded_transcript: Tool-name-scrubbed transcript (``blind_transcript``
            output). The caller is responsible for blinding.
        question_text: The question the transcript answered.
        expected: The oracle expected-answer block (any ``kind``). Serialized
            to JSON for the judge to compare against.
        judge_model: Model id for the judge (default ``"glm-5.2"``).
        judge_bin: Path/name of the ``claude`` CLI binary.

    Returns:
        ``Grade(correctness, method="llm_judge", detail={"rationale": ...},
        judge_model=judge_model)``.

    Raises:
        GradeError: If the outer envelope is not valid JSON, the ``result``
            field is missing, ``result`` is not valid JSON, or the parsed
            object lacks ``correctness``/``rationale``.
    """
    prompt = (
        f"{RUBRIC}\n\n"
        f"Question:\n{question_text}\n\n"
        f"Expected answer:\n{json.dumps(expected, indent=2)}\n\n"
        f"Answer transcript (tool names blinded to [tool]):\n{blinded_transcript}"
    )

    try:
        proc = subprocess.run(
            [
                judge_bin,
                "-p", prompt,
                "--model", judge_model,
                "--output-format", "json",
                "--permission-mode", "bypassPermissions",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        # Per-cell wall-clock bound on the judge. Without this a hung judge
        # process blocks the grader indefinitely. Caught before the broader
        # SubprocessError handler below so the message names the timeout.
        raise GradeError("judge timed out (>120s)") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise GradeError(f"judge subprocess failed: {exc!r}") from exc

    if proc.returncode != 0:
        raise GradeError(
            f"judge {judge_bin} exited {proc.returncode}: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )

    try:
        envelope = json.loads(proc.stdout)
        inner_text = envelope["result"].strip()
        if inner_text.startswith("```"):
            # Drop first line (```json or ```) and trailing ```
            lines = inner_text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            inner_text = "\n".join(lines).strip()
        inner = json.loads(inner_text)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GradeError(
            f"could not parse judge result: {exc!r}; stdout={proc.stdout!r}"
        ) from exc

    try:
        correctness = float(inner["correctness"])
        rationale = inner.get("rationale")
        if not isinstance(rationale, str) or not rationale:
            raise GradeError(
                f"judge result missing/invalid rationale: must be non-empty str; got {type(rationale).__name__}"
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise GradeError(
            f"judge result missing/malformed fields: {exc!r}; inner={inner!r}"
        ) from exc

    return Grade(
        correctness=correctness,
        method="llm_judge",
        detail={"rationale": rationale},
        judge_model=judge_model,
    )


# --- Task 14: grade_cell dispatch + cohen_kappa + grade_run + CLI ---


# Maps a ``Question.grading`` value to the grader name (the ``method`` string
# the corresponding grader produces). ``llm_judge`` is special-cased in
# ``grade_cell`` (it needs the transcript + blinding, not ``final_answer``);
# the other four dispatch to one of the pure programmatic graders below.
GRADE_DISPATCH: dict[str, str] = {
    "programmatic_set_match": "set_match",
    "programmatic_path_match": "path_match",
    "programmatic_client_route_match": "client_route_match",
    "absence_check": "absence_check",
    "llm_judge": "llm_judge",
}

# Method name → pure programmatic grader. ``llm_judge`` is excluded: it goes
# through ``judge_answer`` with the blinded transcript, not ``final_answer``.
_PROGRAMMATIC_GRADERS: dict[str, callable] = {
    "set_match": grade_set_match,
    "path_match": grade_path_match,
    "client_route_match": grade_client_route_match,
    "absence_check": grade_absence,
}


def grade_cell(
    cell: dict,
    transcript_text: str,
    question: Question,
    expected: dict,
    *,
    judge_bin: str = "claude",
) -> Grade:
    """Dispatch-grade one cell.

    Capped cells (``cell["exit_reason"] == "cap"``) short-circuit to a
    deterministic ``Grade(0.0, method=<method>, detail={"reason": "cap"})`` with
    no grader/judge call — a capped cell produced no answer by definition.

    Otherwise routes by ``question.grading``:

      * ``llm_judge`` → ``judge_answer(blind_transcript(transcript_text),
        question.question, expected, judge_bin=judge_bin)``. The judge sees the
        blinded transcript (tool names scrubbed), not the raw ``final_answer``.
      * anything else → the matching programmatic grader (looked up by
        ``GRADE_DISPATCH[question.grading]`` in ``_PROGRAMMATIC_GRADERS``),
        called with ``cell["final_answer"]`` and ``expected``.

    Args:
        cell: The cell dict. ``final_answer`` is optional on the programmatic
            path: it may be ``None`` or absent (capped runs write JSON ``null``)
            and is normalized to ``""`` — an empty answer naturally scores 0.0.
            Ignored on the ``llm_judge`` path.
        transcript_text: The cell's already-loaded transcript text. Used only
            on the ``llm_judge`` path; the programmatic graders ignore it.
        question: The ``Question`` this cell answers. ``question.grading``
            selects the grader; ``question.question`` (the prompt text) is
            passed to the LLM judge.
        expected: The oracle's inner ``expected`` block (e.g.
            ``{"kind": "symbol_set", "fqns": [...]}``).
        judge_bin: ``claude`` CLI binary name/path; forwarded to
            ``judge_answer`` for the ``llm_judge`` path only.

    Returns:
        The ``Grade`` returned by the dispatched grader.

    Raises:
        KeyError: if ``question.grading`` is not in ``GRADE_DISPATCH``, or the
            dispatch target is not in ``_PROGRAMMATIC_GRADERS`` (programming
            error — the two dicts must stay in sync).
    """
    method_name = GRADE_DISPATCH[question.grading]
    # A capped cell produced no answer by definition — the agent exhausted its
    # turn budget without finishing. Score it a deterministic 0.0 and skip the
    # grader/judge entirely: no judge budget spent, and no false-positive from
    # the judge scoring transcript exploration. (Plan 3 kappa methodology.)
    if cell.get("exit_reason") == "cap":
        return Grade(
            correctness=0.0,
            method=method_name,
            detail={"reason": "cap"},
            judge_model=None,
        )
    if method_name == "llm_judge":
        blinded = blind_transcript(transcript_text)
        return judge_answer(
            blinded,
            question.question,
            expected,
            judge_bin=judge_bin,
        )
    grader = _PROGRAMMATIC_GRADERS[method_name]
    # Normalize ``None`` (capped runs write JSON null for final_answer) to ""
    # so the regex-based graders don't TypeError; an empty answer naturally
    # scores 0.0 (no matched symbols / routes).
    answer_text = cell.get("final_answer") or ""
    return grader(answer_text, expected)


def cohen_kappa(judge_labels: list, human_labels: list) -> float:
    """Standard Cohen's κ over two equal-length label lists.

    Agreement fraction ``p_o`` and chance agreement ``p_e``::

        n      = len(judge_labels)  (== len(human_labels))
        p_o    = (# positions where judge[i] == human[i]) / n
        labels = set(judge_labels) | set(human_labels)
        p_e    = Σ_k (count_judge(k)/n) * (count_human(k)/n)
        κ      = (p_o - p_e) / (1 - p_e)

    Edge cases:
      * ``p_e == 1.0`` (both lists constant on the same label — perfect but
        uninformative agreement) → κ = 0.0 by convention.
      * Otherwise perfect agreement (``p_o == 1.0`` with non-constant lists)
        falls out of the formula as κ = 1.0; no special case needed.

    Args:
        judge_labels: First rater's labels (any hashable type).
        human_labels: Second rater's labels. Must be the same length as
            ``judge_labels``.

    Returns:
        Cohen's κ in [-1, 1].

    Raises:
        ValueError: if the lists are empty or have different lengths.
    """
    if len(judge_labels) != len(human_labels):
        raise ValueError(
            f"cohen_kappa: lists must be equal length; got "
            f"{len(judge_labels)} and {len(human_labels)}"
        )
    n = len(judge_labels)
    if n == 0:
        raise ValueError("cohen_kappa: lists must be non-empty")

    agree = sum(1 for j, h in zip(judge_labels, human_labels) if j == h)
    p_o = agree / n

    labels = set(judge_labels) | set(human_labels)
    p_e = 0.0
    for k in labels:
        c_j = judge_labels.count(k)
        c_h = human_labels.count(k)
        p_e += (c_j / n) * (c_h / n)

    if p_e == 1.0:
        return 0.0
    return (p_o - p_e) / (1.0 - p_e)


# Plan 3: binarize the judge's continuous [0,1] correctness for κ vs. binary
# human labels. 0.5 aligns with the rubric's pass/fail notion — a 0.90 answer is
# "correct"; the prior ``== 1.0`` made any sub-perfect score "incorrect", which
# manufactured κ disagreement between a lenient judge and the human gate.
JUDGE_CORRECT_THRESHOLD = 0.5


def _grade_to_judge_label(grade: Grade) -> str:
    """Reduce a Grade to a binary pass/fail label for κ vs. human labels.

    Threshold: ``correctness >= JUDGE_CORRECT_THRESHOLD`` → ``"correct"``;
    anything below → ``"incorrect"``. Matches the human-labeling convention
    (a rater marks each cell's answer as correct or not) without the brittle
    ``== 1.0`` float equality.
    """
    return (
        "correct"
        if grade.correctness >= JUDGE_CORRECT_THRESHOLD
        else "incorrect"
    )


def grade_run(
    cells_path: str,
    expected_dir: str,
    questions: list[Question],
    *,
    human_labels_path: str | None = None,
    judge_bin: str = "claude",
    out_path: str,
) -> dict:
    """Grade every cell in a run; write ``out_path``; return a summary.

    Write-as-you-go: ``out_path`` is opened once and each cell's line is
    written as soon as that cell is graded (with ``flush()``), so a crash on
    cell N does not discard the lines already written for cells 1..N-1.

    Per-cell tolerance: each cell's ``grade_cell`` (plus the surrounding
    transcript/expected reads) is wrapped in ``try/except Exception``. On any
    failure the run continues and the cell gets a grade line with
    ``correctness = 0.0``, ``method = "<original_method>_error"``, and
    ``detail = {"error": str(exc)}``. The happy path (every cell grades
    cleanly) is unchanged. The count of error cells is returned as
    ``summary["errors"]``.

    Per cell:
      1. Read the transcript at ``cell["transcript_path"]`` (relative to the
         process cwd — the cell stores a repo-relative path).
      2. Look up the cell's ``Question`` by ``cell["question_id"]``.
      3. Read the oracle expected at ``<expected_dir>/<question_id>.json`` and
         take its inner ``expected`` block.
      4. ``grade = grade_cell(cell, transcript_text, question, expected,
         judge_bin=judge_bin)``.
      5. Write one JSON line to ``out_path`` = the cell dict with ``grade``
         set to ``to_grade_dict(grade)``.

    After all cells: if ``human_labels_path`` is given, load it (a JSON map
    ``{run_id: label}``), collect ``(judge_label, human_label)`` pairs for
    every judged cell whose ``run_id`` is in the map, and compute
    ``cohen_kappa`` over them.

    Args:
        cells_path: Path to the run's ``cells.jsonl`` (one JSON cell per line).
        expected_dir: Directory of oracle ``<question_id>.json`` files.
        questions: The full question list (only the cell's question is used
            per cell, but the full list is needed for the id → Question map).
        human_labels_path: Optional path to a JSON file mapping
            ``{run_id: human_label}``. When provided, ``kappa`` is computed;
            otherwise ``kappa`` is ``None``.
        judge_bin: ``claude`` CLI binary; forwarded to ``grade_cell`` for
            ``llm_judge`` questions.
        out_path: Where to write the graded JSONL (one cell dict with
            ``grade`` per line).

    Returns:
        Summary dict::

            {
                "graded_n": int,
                "by_method": {method_name: count, ...},
                "mean_correctness": float,   # 0.0 if no cells
                "kappa": float | None,       # None unless human_labels given
                "errors": int,               # cells whose grade_cell raised
            }

    Raises:
        KeyError: if a cell's ``question_id`` has no Question. (Per-cell
            transcript/expected read failures and grade_cell exceptions are
            tolerated — see "write-as-you-go" note above.)
    """
    question_by_id = {q.id: q for q in questions}

    graded_n = 0
    by_method: dict[str, int] = {}
    correctness_sum = 0.0
    errors = 0
    # Track (run_id, Grade) so we can pair judge labels with human labels after.
    judged_cells: list[tuple[str, Grade]] = []

    # Write-as-you-go: open ``out_path`` once and write each cell's line as it
    # is graded, so a crash on cell N no longer discards the lines already
    # collected for cells 1..N-1. Per-cell ``grade_cell`` failures are caught
    # and turned into a 0.0 ``<original_method>_error`` grade line so the run
    # continues (a single bad cell — transient judge failure, missing oracle
    # file, etc. — must not nuke the whole run's graded output).
    cell_lines = Path(cells_path).read_text(encoding="utf-8").splitlines()
    # Sibling dir for blinded-transcript artifacts (Plan 3 kappa: the human gate
    # labels the same blinded transcript the judge graded).
    run_dir = os.path.dirname(out_path)
    with open(out_path, "w", encoding="utf-8") as out_f:
        for line in cell_lines:
            line = line.strip()
            if not line:
                continue
            cell = json.loads(line)
            qid = cell["question_id"]
            question = question_by_id[qid]
            original_method = GRADE_DISPATCH.get(question.grading, "unknown")

            try:
                transcript_text = Path(cell["transcript_path"]).read_text(encoding="utf-8")
                expected_record = json.loads(
                    Path(expected_dir, f"{qid}.json").read_text(encoding="utf-8")
                )
                expected = expected_record["expected"]

                grade = grade_cell(
                    cell,
                    transcript_text,
                    question,
                    expected,
                    judge_bin=judge_bin,
                )
                # Plan 3 kappa: emit the blinded transcript the judge graded so
                # the human gate labels the SAME input (recomputed here; pure,
                # idempotent). Only judged cells — programmatic graders read
                # final_answer, not the blinded transcript.
                if question.grading == "llm_judge":
                    blinded_path = os.path.join(
                        run_dir, f"{cell['run_id']}.blinded.txt"
                    )
                    Path(blinded_path).write_text(
                        blind_transcript(transcript_text), encoding="utf-8"
                    )
            except Exception as exc:
                # Absorb the failure into a 0.0 error-grade line and continue.
                # ``GradeError`` and any other Exception land here; the run
                # does not abort and prior cells are already persisted on disk.
                grade = Grade(
                    correctness=0.0,
                    method=f"{original_method}_error",
                    detail={"error": str(exc)},
                    judge_model=None,
                )
                errors += 1

            graded_n += 1
            by_method[grade.method] = by_method.get(grade.method, 0) + 1
            correctness_sum += grade.correctness
            judged_cells.append((cell["run_id"], grade))

            cell_out = dict(cell)
            cell_out["grade"] = to_grade_dict(grade)
            out_f.write(json.dumps(cell_out) + "\n")
            out_f.flush()

    kappa: float | None = None
    if human_labels_path is not None:
        human_labels = json.loads(
            Path(human_labels_path).read_text(encoding="utf-8")
        )
        judge_labels: list = []
        paired_human: list = []
        for run_id, grade in judged_cells:
            if run_id in human_labels:
                judge_labels.append(_grade_to_judge_label(grade))
                paired_human.append(human_labels[run_id])
        if judge_labels:
            kappa = cohen_kappa(judge_labels, paired_human)

    mean_correctness = (correctness_sum / graded_n) if graded_n else 0.0
    return {
        "graded_n": graded_n,
        "by_method": by_method,
        "mean_correctness": mean_correctness,
        "kappa": kappa,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    """Argparse CLI entry for grading a run's ``cells.jsonl``.

    Flags:
        --cells          Path to the run's ``cells.jsonl`` (required).
        --expected       Dir of oracle ``<question_id>.json`` files
                         (default ``bench/oracle/expected``).
        --questions-glob Glob of question JSONL files
                         (default ``bench/questions/*.jsonl``).
        --human-labels   Optional JSON map ``{run_id: label}`` for κ.
        --judge-bin      ``claude`` CLI binary (default ``claude``).
        --out            Output graded JSONL path
                         (default ``<cells dir>/graded.jsonl``).

    Prints the summary dict as JSON to stdout and returns 0.
    """
    parser = argparse.ArgumentParser(
        prog="grade",
        description="Grade a benchmark run's cells.jsonl against the oracle.",
    )
    parser.add_argument("--cells", required=True,
                        help="Path to the run's cells.jsonl.")
    parser.add_argument("--expected", default="bench/oracle/expected",
                        help="Dir of oracle <question_id>.json files.")
    parser.add_argument("--questions-glob", default="bench/questions/*.jsonl",
                        help="Glob of question JSONL files.")
    parser.add_argument("--human-labels", default=None,
                        help="Optional JSON map {run_id: label} for κ.")
    parser.add_argument("--judge-bin", default="claude",
                        help="claude CLI binary (default: claude).")
    parser.add_argument("--out", default=None,
                        help="Output graded JSONL path "
                             "(default: <cells dir>/graded.jsonl).")
    args = parser.parse_args(argv)

    out_path = args.out
    if out_path is None:
        cells_dir = os.path.dirname(os.path.abspath(args.cells))
        out_path = os.path.join(cells_dir, "graded.jsonl")

    questions = load_all_questions(args.questions_glob)

    summary = grade_run(
        args.cells,
        args.expected,
        questions,
        human_labels_path=args.human_labels,
        judge_bin=args.judge_bin,
        out_path=out_path,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
