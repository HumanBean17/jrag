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
"""

from __future__ import annotations

import json
import re
import subprocess
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
# An HTTP route: METHOD followed by whitespace and a slash-leading path. The
# method is captured case-insensitively (answers sometimes write "post /join")
# and normalized to uppercase before pairing.
_ROUTE_RE = re.compile(
    rf"\b({_HTTP_METHODS})\s+(/[A-Za-z0-9_\-/]+)",
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
        )
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
