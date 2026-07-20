<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Lossless-permissive `NodeFilter` contract (tactical fix for silent-drop bug class)

## Status

Completed — implemented in PR-N1 (`enforce lossless NodeFilter (forbid extras + kind applicability)`). Tracks upstream discussion: [HumanBean17/java-codebase-rag#122](https://github.com/HumanBean17/java-codebase-rag/issues/122) (tactical subset of the strategic frame question in #117).

## Problem Statement

MCP tools that accept a `filter` object use the shared `NodeFilter` Pydantic model in `mcp_v2.py`. Today that surface has two honesty gaps:

1. **Unknown keys are ignored.** Pydantic’s default extra handling means typos or misplaced fields (for example nesting a `kind` selector inside `filter` instead of at the tool boundary) are **silently dropped**. The tool runs with a weaker filter than the agent believed it sent.

2. **Cross-kind fields are ignored at match time.** `_node_matches_filter` only reads fields that apply to the row’s kind (`symbol` / `route` / `client`). Fields that belong to another kind are never consulted — so an agent can pass symbol-only constraints (for example `fqn_prefix`) while calling `find(kind="client")` and still receive **every** client, with no signal that part of the filter was inert.

Both behaviors are **lossy**: input the agent treated as meaningful is discarded without an error. That matches the “silent-drop bug class” called out in #117; this proposal fixes that class **without** choosing the long-term strict vs permissive vs hybrid frame (#117).

**Bright line (from #122):** lossless input normalization (same intent, different encoding — for example JSON string vs dict) remains acceptable; **lossy** silent drops are not.

## Proposed Solution

1. **`NodeFilter` uses explicit extra forbiddance.** Set `model_config = ConfigDict(extra="forbid")` on `NodeFilter` so unknown top-level keys raise `pydantic.ValidationError` at parse time. That catches the “wrong key / nested kind” class cheaply.

2. **Per-kind applicability validation** after `NodeFilter` is constructed and **before** graph push-down / neighbor expansion: the set of filter fields that are populated (non-`None`, and for lists non-empty if that is the chosen rule) must be a subset of the fields defined as applicable for the **effective** kind:
   - `find_v2`: the `kind` argument.
   - `search_v2`: post-filter rows are always symbol-shaped hits from Lance; applicability is the **symbol** field set (today’s `_node_matches_filter("symbol", …)` branch).
   - `neighbors_v2`: the **neighbor node’s** kind after resolution (`symbol` / `route` / `client`) — the filter applies to `other` nodes loaded in the loop, so validation should mirror that (see Open Questions).

   When a populated field is not applicable, return the same structured failure style as other tool errors (`success=False`, `message=…`) listing the offending field(s) and the **applicable field names for that kind** so the message acts as a teaching surface. Composition note: if [#120](https://github.com/HumanBean17/java-codebase-rag/issues/120) (hints field) lands, these messages are natural candidates for machine-readable `hints`.

3. **Keep `_coerce_filter` behavior.** JSON-decoding a string into a dict before validation is a **lossless** multi-form input path (same object, different wire encoding) and stays as-is per #122.

4. **Do not change `search.query` semantics.** Fuzzy query ranking is unrelated; only the `filter` contract is tightened.

**Repo-specific correction vs #122 text:** `describe_v2` in this codebase does **not** accept `filter`; only `find_v2`, `search_v2`, and `neighbors_v2` do. No change is required on `describe_v2` for filter honesty unless a future proposal adds filtering there.

## Scope

- `mcp_v2.py`: `NodeFilter` model config; a small applicability helper (or inline checks) keyed by kind; wiring at `find_v2`, `search_v2`, and `neighbors_v2` entry points after coerce + validate.
- Tests: unknown filter keys; cross-kind populated fields; empty filter still succeeds; JSON-string filter still accepted.
- README / tool instruction strings if the public contract text should mention loud-fail filter semantics (minimal delta).

## Schema / Ontology / Re-index impact

- **Ontology bump:** not required (no graph or indexer semantics change).
- **Re-index required:** no.
- **Config / tool surface changes:** behavior change only on invalid filter shapes — agents that relied on ignored extra keys or inert cross-kind fields will start seeing errors instead of silent success.

## Tests / Validation

- Unknown top-level key in `filter` → validation failure (exact surface: `ValidationError` vs wrapped `success=False` — see Open Questions).
- Populated symbol-only field with `find(kind="client")` → `success=False` with message listing applicable client fields.
- Populated client-only field with `find(kind="symbol")` → `success=False`.
- `fqn_prefix` + `find(kind="symbol")` → still honored (regression).
- Empty `{}` filter (and `None` where allowed) → unchanged success path.
- Filter passed as JSON string → still decodes then validates (lossless multi-form).

Run: `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` (plus any new focused tests under `tests/` naming the scenarios above).

## Open Questions ([TBD])

1. **`ValidationError` vs uniform `success=False` for unknown keys.** `find_v2` / `search_v2` already wrap generic exceptions into `success=False`; `neighbors_v2` currently re-raises `ValidationError` for at least some validation paths. Should unknown-key failures be **wrapped** into `success=False` for all three tools for a uniform MCP experience, or is raising `ValidationError` acceptable for “contract violations”?
   - **Recommended:** catch filter `ValidationError` at the same boundary as applicability errors and return `success=False` with a parsed, agent-readable message (consistent across tools).

2. **`neighbors_v2` applicability kind.** Neighbors can be mixed when multiple origin ids are passed; each edge’s `other` has its own kind. Is the contract “filter must be applicable to **every** neighbor kind that could appear,” “union of fields that applies to any neighbor,” or “validate per neighbor row” (strictest, matches runtime)?
   - **Recommended:** validate **per neighbor** inside the loop is unnecessary if we define the rule as: populated fields must be subset of **union** of applicable fields across `symbol`, `route`, `client`, **or** require the filter to only use the common subset (`microservice`, `module`). The issue text implies per-request kind like `find`; neighbors are trickier — default to **reject if any populated field is not applicable to the neighbor kind currently being tested** (same as proposed post-validate before filtering each `other`), which may mean no single upfront check — implementors can still factor a helper `_filter_applies_to_kind(kind, nf) -> str | None` returning an error message.

3. **Empty lists vs `None`.** Should `exclude_roles: []` count as “populated” for applicability?
   - **Recommended:** treat empty list as absent (same as `None`) for applicability and for Cypher push-down, matching intuitive “no-op constraint” behavior.

## Out of scope

- Choosing or implementing the full #117 frame (strict / permissive / hybrid vocabulary).
- New tools (`resolve`, etc.), field renames, or cross-kind field aliasing.
- Changing `search.query` fuzzy matching or ranking.
- Changing `EdgeType` literals, `find.kind` literals, or graph schema.
- `describe_v2` (no filter today).

## Sequencing / Follow-ups

- **Independent of** graph builder / Kuzu schema work; can land in **one small PR** after any urgent Kuzu query fixes upstream maintainers prioritize (issue #119 in the same tracker — independent).
- **Composes with** hints-field work (#120): teaching-style messages become structured hints later.
- **Strategic** #117 remains open to lock the long-term contract after observing real agent traffic under loud-fail behavior.

## References (this repo)

- `NodeFilter` — `mcp_v2.py` (approx. lines 59–76).
- `_coerce_filter` — `mcp_v2.py` (approx. lines 79–98).
- `_node_matches_filter` — `mcp_v2.py` (approx. lines 351–398).
- `search_v2` / `find_v2` / `neighbors_v2` — `mcp_v2.py` (approx. lines 401+).
