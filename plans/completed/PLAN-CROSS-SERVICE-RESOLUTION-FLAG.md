# Plan: `cross_service_resolution` config flag

Status: **active** — propose merged in [#26](https://github.com/HumanBean17/java-codebase-rag/pull/26).
Source: `propose/CROSS-SERVICE-RESOLUTION-FLAG-PROPOSE.md` on master.
Companion plan: `plans/PLAN-FEIGN-NOT-AN-EXPOSER.md` (orthogonal — different pass; either order works, but combined testing on `cross_service_smoke` is cleaner).

## TL;DR

Single PR adds a global config flag in `.lancedb-mcp.yml`:

```yaml
cross_service_resolution: auto | brownfield_only  # default: auto
```

When `brownfield_only`, `pass6_match_edges` suppresses
auto-extracted cross-service edges; only edges where **both sides**
are sourced from brownfield layers (`@CodebaseRoute` +
`@CodebaseClient`, or YAML equivalents) remain `cross_service`.
`intra_service`, `phantom`, `ambiguous`, and the PR-E3 invariant
guard remain active in both modes.

~150 LOC. Ontology bump 7→8 (additive `STRING` column on
`GraphMeta`). No new MCP tools, no new annotation types, no new
client/route extraction logic.

## Origin

| From | Item | Severity |
|------|------|----------|
| Mid-session architecture decision 2026-05-05 | Auto cross-service resolution has working infrastructure (PR-D3, PR-E2, PR-E3) but has not been tested on a real brownfield codebase, and just produced a wrong-direction EXPOSES bug (#25). User wants to **regain control** before real-project test by running brownfield-first, with the auto path preserved as opt-in. | medium (operability) |

## Recommended PR boundaries

Single PR, ~150 LOC. Scope is small enough that splitting hurts
reviewability more than it helps.

- **PR-G1** — config flag reader + threading on `GraphTables` +
  gate inside `pass6_match_edges` + `_is_brownfield_sourced`
  helper + `GraphMeta` schema extension + ontology bump 7→8 +
  6 tests + verbose log line + README update.

§9 [TBD] items in the propose are all v1-resolved with explicit
recommendations. The plan inherits those defaults; revisit only
if real-project test data contradicts them.

---

## PR-G1 — Config flag for cross-service resolution mode

Touches: `graph_enrich.py` (new `_load_config_cross_service_resolution`
function — mirror of `_load_config_microservice_roots`),
`build_ast_graph.py` (new field on `GraphTables`, gate in
`pass6_match_edges`, schema extension on `GraphMeta`, ontology bump),
`kuzu_queries.py` (extend `meta()` to read the new column with
`None` fallback), `tests/test_pass6_match_edges.py` (or a new
`tests/test_cross_service_resolution_flag.py`),
`tests/fixtures/cross_service_smoke/` (extend with annotated and
unannotated variants — see §Tests),
`README.md` (brownfield section).

Out of scope:
- CLI override (`--cross-service-resolution=...`) — 5-line follow-up
  if real use surfaces the need.
- Per-channel granularity (`cross_service_http_resolution` +
  `cross_service_async_resolution`) — propose §7 explicitly defers.
- Mixed mode (brownfield client + auto route, or vice versa) —
  propose [TBD-2] keeps both-sides-brownfield rule for v1.
- New `match` enum value `suppressed_by_mode` — propose [TBD-3]
  keeps `unresolved` for v1.
- Removing or refactoring the auto path itself.

### Background

The brownfield system (verified complete 2026-05-06) supports
authoritative cross-service edges via:
- Consumer side: `@CodebaseRoute(microservice, path, method)` /
  `route_overrides` YAML
- Caller side (HTTP): `@CodebaseClient(target_service, path, method)` /
  `http_client_overrides` YAML
- Caller side (async): `@CodebaseAsyncProducer(broker, topic)` /
  `async_producer_overrides` YAML

`_classify_match_for_call` (`build_ast_graph.py:1620-1648`) does
**not** filter by `route_source_layer` — so a layer-C
`@CodebaseRoute` lands in the same candidate pool as a built-in
`@GetMapping` Route. This means brownfield-sourced cross-service
edges already work today **without** the flag.

The flag's only job is to **suppress** auto-sourced cross-service
matches when the user wants brownfield-first precision.

### Failure modes the flag addresses

1. **Real-project shakedown surprises.** Auto resolution has only
   been tested on curated fixtures. Real brownfield codebases will
   surface ambiguity (multiple endpoints matching the same path),
   path-template mismatches (caller's URI built dynamically), and
   raw-HTTP-client patterns (`HttpURLConnection`, `OkHttp`) that
   the auto path can't handle. `brownfield_only` mode lets the
   user annotate the gnarly bits and trust nothing else.
2. **Silent drift.** Auto resolution can produce wrong edges (PR
   #25 was one) without obvious test-fixture symptoms. In
   `brownfield_only` mode, every cross-service edge has a hand-written
   provenance.
3. **Mental model creep.** A user who only cares about "annotate
   the legacy hot spots, get correct edges" doesn't need to learn
   the 3-strategy ladder, the matcher, the `match` labelling enum,
   or the cross-service candidate scoring.

### Resolution

Five coordinated changes:

#### Change 1: Config reader

Add to `graph_enrich.py` near `_load_config_microservice_roots`
(line 115):

```python
@cache
def _load_config_cross_service_resolution(project_root_str: str) -> str:
    """Read `cross_service_resolution` from `.lancedb-mcp.yml`.

    Returns one of `"auto"` or `"brownfield_only"`. Default `"auto"`
    if the key is absent. Unknown values warn and fall back to
    `"auto"`.
    """
    root = Path(project_root_str)
    for name in CONFIG_FILENAMES:
        p = root / name
        if not p.exists():
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return "auto"
        val = data.get("cross_service_resolution", "auto")
        if val not in {"auto", "brownfield_only"}:
            print(
                f"[lancedb-mcp] cross_service_resolution: unknown value "
                f"{val!r}, falling back to 'auto'",
                file=sys.stderr,
            )
            return "auto"
        return val
    return "auto"
```

Mirror style from `_load_config_microservice_roots` exactly —
same caching, same error handling, same warn-and-fall-back
behaviour.

#### Change 2: Thread the flag onto `GraphTables`

Add field on `GraphTables` (the existing tables container near
the top of `build_ast_graph.py`):

```python
@dataclass
class GraphTables:
    ...
    cross_service_resolution: str = "auto"
```

Set near the three existing `load_brownfield_overrides(...)`
call sites in `build_ast_graph.py` (lines 1274, 1409, 1927):

```python
overrides = load_brownfield_overrides(source_root)
tables.cross_service_resolution = _load_config_cross_service_resolution(source_root)
```

#### Change 3: `_is_brownfield_sourced` helper + gate in pass6

Add helper near `pass6_match_edges`:

```python
_BROWNFIELD_LAYERS = frozenset(
    {"layer_c_source", "layer_b_ann", "layer_b_fqn", "layer_a_meta"}
)


def _is_brownfield_sourced(
    call: HttpCallRow | AsyncCallRow,
    candidates: list[RouteRow],
) -> bool:
    """Both sides must come from brownfield layers for an edge to count
    as authoritative under `brownfield_only` mode. See propose §2.3."""
    if call.strategy not in _BROWNFIELD_LAYERS:
        return False
    return all(
        getattr(c, "source_layer", "builtin") in _BROWNFIELD_LAYERS
        for c in candidates
    )
```

Inside `pass6_match_edges`, after each call's candidate computation
(both the HTTP loop near line 1697 and the async loop near line
1737):

```python
match, candidates = _classify_match_for_call(call, all_routes, caller_microservice)

if (
    tables.cross_service_resolution == "brownfield_only"
    and match == "cross_service"
    and not _is_brownfield_sourced(call, candidates)
):
    match = "unresolved"
    candidates = []
    suppressed_count += 1

# ... existing match handling
```

`intra_service`, `phantom`, `ambiguous` paths are untouched.
PR-E3's invariant guard runs in pass3 (not pass6), so it remains
active.

#### Change 4: `GraphMeta` schema extension + ontology bump

In `build_ast_graph.py` schema definition (`_SCHEMA_GRAPH_META`
or whichever current name — verify on master), add column:

```python
"cross_service_resolution STRING"
```

Bump `ONTOLOGY_VERSION` 7 → 8 in `ast_java.py:74`. Update
`__init__` docstring to mention "Phase 6: cross-service
resolution mode flag" (or whichever wording is consistent
with the existing phase comments).

#### Change 5: Surface in `KuzuGraph.meta()`

`kuzu_queries.py` — extend `meta()` to read the new column with
`None` fallback for old graphs:

```python
try:
    raw = conn.execute("MATCH (m:GraphMeta) RETURN m.cross_service_resolution")
    row = raw.get_next() if raw.has_next() else None
    cs_resolution = row[0] if row else None
except RuntimeError:  # column not in old graphs
    cs_resolution = None
```

Or use `meta()`'s existing column-introspection pattern if
there's already one for older meta fields.

### Verbose logging

`pass6_match_edges` logs (only when `cross_service_resolution ==
"brownfield_only"`):

```
[pass6] cross_service_resolution=brownfield_only:
        N cross_service edges from brownfield layers,
        K auto-cross-service candidates suppressed -> unresolved
        (first 5: {fqn1}, {fqn2}, ...)
```

Keep the example list capped at 5 to avoid log spam on large
graphs (per propose [TBD-4]).

### Tests

Use `tests/fixtures/cross_service_smoke/` as the base. Two
variants needed:
- **Unannotated** (today's fixture): no `@CodebaseRoute` /
  `@CodebaseClient`. Auto path produces cross-service edges.
- **Annotated** (new sub-fixture or per-test patch): add
  `@CodebaseRoute` to `JoinControllerB` and `@CodebaseClient`
  to `BFeignClient`. Both-sides-brownfield.

| # | Test name | Asserts |
|---|---|---|
| 1 | `test_cross_service_resolution_auto_default` | No `.lancedb-mcp.yml` set → `tables.cross_service_resolution == "auto"`. Build unannotated `cross_service_smoke` → 1 cross-service HTTP_CALL + 1 cross-service ASYNC_CALL (today's baseline). |
| 2 | `test_brownfield_only_suppresses_auto_cross_service` | Set `cross_service_resolution: brownfield_only` in fixture YAML. Build unannotated `cross_service_smoke` → 0 cross-service edges; the would-be edges become `unresolved`. |
| 3 | `test_brownfield_only_keeps_annotated_cross_service` | Same flag + add `@CodebaseRoute` and `@CodebaseClient` to fixture → 1 cross-service HTTP_CALL produced (matches both sides brownfield-sourced). |
| 4 | `test_brownfield_only_preserves_intra_service` | Build a fixture with intra-JVM CALLS in `brownfield_only` mode → all `intra_service` edges still present (PR-E3 invariant guard holds; pass3 is unaffected). |
| 5 | `test_meta_reports_cross_service_resolution` | Built-with-flag graph → `KuzuGraph.meta()["cross_service_resolution"]` returns the literal value (`"auto"` or `"brownfield_only"`). |
| 6 | `test_meta_resolution_null_for_old_graphs` | Synthesize a graph without the `GraphMeta.cross_service_resolution` column → `KuzuGraph.meta()["cross_service_resolution"]` returns `None`. |
| 7 | `test_unknown_value_falls_back_to_auto` | YAML has `cross_service_resolution: nonsense` → reader returns `"auto"` and warns. Behaviour matches default. |
| 8 | `test_brownfield_client_with_auto_route_does_not_match` | YAML in `brownfield_only` mode + `@CodebaseClient` on Feign caller but **no** `@CodebaseRoute` on controller → edge is suppressed (matches propose [TBD-2] decision: both sides must be brownfield). |

Test count target: `pytest tests -q` baseline + 8 = **~280 passed, 4 skipped** (combined with PR-F1's +6).

### Manual evidence to capture in PR description

```bash
cd /home/user/workspace/user-rag

# Auto mode (default, unchanged)
rm -rf /tmp/check_auto && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_auto --verbose 2>&1 | grep -E 'cross_service|brownfield_only'

# Expected: today's baseline lines, no brownfield_only mention.

# Brownfield-only mode (negative case — fixture is unannotated)
echo "cross_service_resolution: brownfield_only" \
  > tests/fixtures/cross_service_smoke/.lancedb-mcp.yml
rm -rf /tmp/check_bo && \
  python build_ast_graph.py --source-root tests/fixtures/cross_service_smoke \
    --kuzu-path /tmp/check_bo --verbose 2>&1 | grep -E 'cross_service|brownfield_only'

# Expected:
#   [pass6] cross_service_resolution=brownfield_only:
#           0 brownfield, 2 auto-cross-service suppressed -> unresolved

# Verify meta
python -c "
from kuzu_queries import KuzuGraph
print(KuzuGraph('/tmp/check_bo').meta()['cross_service_resolution'])
"
# Expected: 'brownfield_only'

# Cleanup
rm tests/fixtures/cross_service_smoke/.lancedb-mcp.yml
```

### Migration

No data migration. Old graphs continue to load; the
`cross_service_resolution` field returns `None` from `meta()`.
Document "rebuild to apply the flag" in the PR description.

Ontology bump 7→8 is the only schema-level change. Existing
`GraphMeta` reads continue to work because the new column is
nullable.

### Definition of Done

- [ ] `_load_config_cross_service_resolution` reader added to `graph_enrich.py` with cache, mirroring `_load_config_microservice_roots`
- [ ] `cross_service_resolution: str = "auto"` field on `GraphTables`, populated at the three `load_brownfield_overrides` call sites
- [ ] `_is_brownfield_sourced` helper + gate in both HTTP and async loops of `pass6_match_edges`
- [ ] `GraphMeta` schema extended with `cross_service_resolution STRING`
- [ ] `ONTOLOGY_VERSION` bumped 7 → 8
- [ ] `KuzuGraph.meta()` extended to surface the field with `None` fallback
- [ ] All 8 tests above pass
- [ ] Verbose log line per propose §2.5 (count + first 5 examples)
- [ ] README brownfield section updated to document the flag
- [ ] Existing `pytest tests -q` baseline does not regress; combined with PR-F1, target is **~280 passed, 4 skipped**
- [ ] PR description includes manual evidence block
- [ ] No new MCP tools, no new annotation types

### Risk register (from propose §3)

| # | Risk | Mitigation |
|---|---|---|
| 1 | A brownfield-sourced call matches an auto-sourced route, user expects edge | Both sides must be brownfield-sourced (test #8). Document explicitly in README. If too strict in real use, [TBD-2] revisit in v2. |
| 2 | `unresolved` rate spikes silently when user flips the flag | Verbose log line + `KuzuGraph.meta()` surface the mode. Query tools can warn. |
| 3 | Default `auto` masks the flag's value | Document in README brownfield section + `KuzuGraph.meta()` docstring. |
| 4 | Test fixtures don't exercise `brownfield_only` mode | Tests 2/3/4/7/8 cover it explicitly. |
| 5 | Old graphs lack the meta field | Test 6 covers null fallback. Read in `kuzu_queries.py` wraps the column access in try/except. |
| 6 | User confuses "unresolved" with "broken" | Verbose log distinguishes mode-suppressed from genuinely unresolved. [TBD-3] keeps `unresolved` value for v1; can split to `suppressed_by_mode` later. |
| 7 | Flag interacts unexpectedly with PR-F1's Feign-not-an-exposer fix | PR-F1 runs in pass4 (route emission), this in pass6 (matching). Orthogonal. Recommend running both fixes together in CI matrix to confirm. |

---

## Followups (non-blockers, capture in PR description as TBDs)

1. **CLI override** (`--cross-service-resolution=...`) — 5-LOC
   follow-up if real use surfaces the need.
2. **Per-channel flags** — `cross_service_http_resolution` +
   `cross_service_async_resolution`, only if real-project test
   reveals async should be trusted independently of HTTP.
3. **`suppressed_by_mode` match value** — split from `unresolved`
   in the graph if downstream queries need to distinguish
   mode-suppressed from genuinely unresolved.
4. **Auto-suggest annotations** — when `brownfield_only` suppresses
   an edge that the auto path would have caught, log a hint:
   `would have matched svc-b's POST /orders; consider
   @CodebaseClient(targetService="svc-b", path="/orders",
   method="POST")`. Lowers the cost of brownfield-first mode on
   real codebases.
5. **Mixed mode** — allow brownfield client + auto route (or vice
   versa). Currently rejected by [TBD-2]; revisit if real-project
   testing shows this is the common case.
6. **Auto-path cleanup decision.** After real-project test, if
   user always sets `brownfield_only`, a v2 cleanup PR can delete
   the auto matcher entirely. If user always sets `auto`, this
   plan's flag becomes vestigial — keep it for power-user control,
   document it less prominently.

---

## References

- `propose/CROSS-SERVICE-RESOLUTION-FLAG-PROPOSE.md` — the merged
  propose (PR #26) that this plan implements
- `build_ast_graph.py:1620-1648` — `_classify_match_for_call`
  (today does NOT filter by `route_source_layer`)
- `build_ast_graph.py:1651-` — `pass6_match_edges`, the gate point
- `build_ast_graph.py:1274,1409,1927` — three call sites that
  load `BrownfieldOverrides`; sibling location for the new
  config flag read
- `graph_enrich.py:91` — `CONFIG_FILENAMES`
- `graph_enrich.py:115-166` — `_load_config_microservice_roots`,
  the pattern to mirror
- `graph_enrich.py:190-203` — `HttpClientHint` /
  `AsyncProducerHint` carrying brownfield client target info
- `graph_enrich.py:838,919,952,981,995,1033,1267,1358` —
  `route_source_layer` and `resolution_strategy` literals that
  `_is_brownfield_sourced` must check
- `ast_java.py:74` — `ONTOLOGY_VERSION = 7` (bump to 8)
- `plans/PLAN-FEIGN-NOT-AN-EXPOSER.md` — orthogonal companion
  plan; combined CI matrix recommended
- `plans/PLAN-POST-TIER1B-FOLLOWUPS.md` § PR-E3 — intra-JVM
  invariant guard, which remains active in `brownfield_only` mode
