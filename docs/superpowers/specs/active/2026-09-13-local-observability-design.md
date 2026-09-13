# Local observability: usage events, health surfacing, effectiveness measurement (opt-in)

- **Date:** 2026-09-13
- **Status:** implemented

## Motivation

The owner uses `jrag` daily, but AI agents drive it autonomously via the agent
CLI verbs while the watch daemon maintains the index. Nothing records what
happened, so three questions are unanswerable today: how is jrag being used,
was it useful, and were there problems?

Current state (verified against the code):

- **No per-invocation record.** Every `jrag <verb>` subprocess prints an
  envelope and exits; no log, no counter, nothing persists. The fail-loud
  counters (`mcp/mcp_v2.py`) die with each process; the watch state file's
  `queries_served` is a stub hardcoded to 0.
- **Silent failure modes are real, not hypothetical:**
  1. Daemon death is masked — `get_payload` (`watch/client.py`) silently
     cold-falls-back to in-process cores, so agents get slow-but-correct
     answers forever and nothing reports the death.
  2. The daemon persists `last_error`, but neither `jrag status` nor
     `jrag watch --status` renders it.
  3. A persistent vectors-phase failure freezes graph updates too
     (`watch/watcher.py` reindex early-returns before the graph phase).
  4. Subprocess stderr is captured and then discarded; only a bare
     returncode survives into the state file.
  5. No heartbeat and no subprocess timeout — a hung build is
     indistinguishable from an idle daemon.
  6. Crash markers (`.graph_increment_in_progress`) self-heal via full
     rebuild, hiding recurring crashes.
- **Effectiveness is measurable only via staged benchmarks** (`eval/`,
  `bench/`), which sample conditions rather than observe real usage.

## Goal & scope

**Goal.** A local-only, **opt-in** observability layer that records what agents
did with jrag (usage events), surfaces operational health where agents and the
owner already look, and provides continuous usefulness signals (proxy metrics,
sparse owner labels, live-index eval runs). One configuration switch enables
everything; disabled, jrag behaves exactly as today.

**In scope.** The `usage/` package (event contract, JSONL writer, pure
aggregation); taps at the agent-CLI funnel, the operator-CLI funnel, and the
watch daemon's event funnel; new agent verbs `jrag usage` and `jrag feedback`;
daemon state health fields + heartbeat; health surfacing on `status` / `prime`;
`eval --reuse-index`; configuration knobs; docs; tests.

**Out of scope (future work).** GraphMeta build-over-build diffing;
`jrag doctor` diagnostics command; MCP-server and watch-socket taps (the event
schema already carries `surface`, so these are additive later); draining
fail-loud counters into events; two-tier reflog-style rollup retention;
hint-follow-rate measurement; any network export — permanently, not just v1.

## Decisions

1. **Opt-in, single master switch (owner decision).** `usage.enabled`
   defaults to **false**. YAML `usage.enabled: true` or env
   `JAVA_CODEBASE_RAG_USAGE_ENABLED=1` enables the entire layer: event
   writing, new daemon state fields, heartbeat, `event_id` on envelopes, and
   health surfacing on `status`/`prime`. No per-feature flags. Two exceptions:
   rendering the already-recorded `last_error` in `jrag watch --status` stays
   ungated (display of existing state, not collection), and
   `eval --reuse-index` stays ungated (explicit operator invocation that
   writes only the eval harness's own report dirs).
2. **JSONL day-sharded files, not SQLite.** Concurrent one-shot CLI processes
   favor one atomic `os.write` on an `O_APPEND` fd over WAL/lock-retry
   machinery; a torn line costs one event instead of the history; rotation is
   file deletion; the only consumer is one streaming aggregator. The `llm`
   CLI's SQLite usage log is the cautionary prior art (unbounded growth is
   its known open problem). Revisit only if streaming aggregation proves
   insufficient.
3. **Durable state dir keyed by `project_key`, never the index dir.** The
   state dir reuses the existing `project_key` derivation (`watch/paths.py`).
   `jrag erase` rebuilds the index; it must not wipe the usage history that
   justifies the feature. Layout:
   `<state>/events/<project_key>/events-YYYY-MM-DD.jsonl` plus
   `feedback.jsonl` alongside.
4. **Synchronous single-write appends, swallow-everything guard.** The whole
   line (≤ 1 KiB) in one `os.write`; writes happen at emit time because
   both `_console_script_main` and the daemon `os._exit()` past buffered
   flushes. The entire emit path (mkdir, serialize, write, prune) sits inside
   one `except Exception: pass`; telemetry never changes stdout, stderr, or
   exit codes. A debug line is gated behind the existing
   `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`.
5. **Surface-neutral versioned schema.** `surface` is an open enum
   (`cli` today; `watch`; `mcp` later), so later taps emit the same record
   without a format migration. Schema version `v` starts at 1; readers ignore
   unknown fields.
6. **Identifiers, not content.** Events record verbs, queries (capped 200
   chars, cut at first newline), FQNs and outcomes — never file contents or
   snippets. Local-only is enforced, not promised: an import-lint test asserts
   the `usage` package imports nothing network-capable, and
   `docs/CONFIGURATION.md` carries a "What jrag records locally" table.
7. **Cheap confounder fields ride from day one.** `index_age_s` (from the
   watch state file's `last_reindex_at` — a plain JSON read, no graph open)
   and `served_by: daemon|cold` (from the existing hot/cold branch in
   `get_payload`) are recorded on every command event; without them,
   empty-rate analysis confuses stale index with bad query, and latency
   percentiles mix millisecond hot reads with multi-second cold model loads.
8. **Summary verb named `jrag usage`**, deliberately distinct from
   `jrag status` (one-letter collision is an agent-typing hazard).
9. **Two knobs, everything else constants.** `usage.enabled` and `usage.dir`
   (env `JAVA_CODEBASE_RAG_USAGE_DIR`) follow the `_pick_*` +
   `SettingSource` provenance pattern in `config.py`.
   Constants: 30-day retention, 5 MiB/day-file cap (drop overflow beyond the
   cap, record the drop count), 1024-byte line cap, 200-char query cap, 2 KB
   stderr excerpt, 30-second heartbeat, 30-minute session inactivity cutoff.

## Architecture

New stdlib-only package `src/java_codebase_rag/usage/`:

| Unit | Responsibility |
|---|---|
| `paths.py` | State dir resolution: `usage.dir` override > `XDG_STATE_HOME` > `~/.local/state/jrag` (Linux) / `~/Library/Application Support/jrag` (macOS), following the `watch/paths.py` precedent for per-user state. Per-project event dir via `project_key`. |
| `events.py` | The event record contract: frozen field vocabulary, common core, per-surface payload shapes, `event_id` derivation (short deterministic hash of the recorded event). |
| `writer.py` | Append discipline: guard, serialize, single write, day-file selection, retention pruning, overflow accounting. |
| `summarize.py` | Pure functions over event streams: percentiles, session grouping, struggle signals, absence-term classification, feedback joins. Pure like `eval/metrics.py` — testable without a filesystem. |

Data flow: taps → `writer` appends day-sharded JSONL → `jrag usage` streams
files and aggregates via `summarize` → rendered through the standard Envelope
path (`--format text|json`).

## Event schema

Common core on every line:

```json
{"v": 1, "ts": "RFC3339 UTC with ms", "surface": "cli|watch",
 "event": "command|reindex|daemon", "project_key": "12-hex", "pid": 123}
```

`command` payload (surfaces `cli`): verb, query (capped), flags subset
(limit/format/detail/count/exists/service), `duration_ms`, `rc`, envelope
facts (`status`, `result_count` via the existing `count_results`,
`truncated`, `candidates_count`, absence verdict/cause, `warnings_count`),
`index_age_s`, `served_by`, `ppid`, `cwd`.

`reindex` payload (surface `watch`): the existing event kinds
(`indexing_started`/`vectors`/`graph`/`indexing_done`/`error`), phase,
returncode, duration, and on failure a stderr excerpt — the diagnostic
text currently captured and discarded. The state file's ``last_error`` keeps a
2 KB copy; the journaled event carries a byte-budgeted tail (≤400 bytes) so
``json.dumps`` escaping of non-ASCII stderr can never push the line past the
1 KiB cap and drop the event.

`daemon` payload (surface `watch`): start/stop/crash lifecycle.

## Taps

- **Agent CLI**: `_emit` (`jrag.py`) stashes the envelope-derived fields for
  the current invocation; `main()` composes and writes the single event —
  that funnel sees duration, rc, and the exception paths `_emit` misses.
  Error paths (argparse failures, unhandled exceptions) record their own
  events.
- **Operator CLI**: one-event mirror at `cli.py` `main()` capturing
  init/increment/reprocess outcomes and durations (`surface: "cli"`).
- **Watch daemon**: `_record` (`watch/daemon.py`) additionally appends
  events; the watcher passes subprocess stderr into the event detail. The
  state file gains `consecutive_errors` (reset on `indexing_done`),
  `last_vectors_ok_at`, `last_graph_ok_at`, and a live `queries_served`
  (incremented in `WatchServer` dispatch, replacing the stub). The serve loop
  rewrites the state file every ~30 s so its mtime is a heartbeat.

## `jrag usage`

New agent verb (registered in `jrag.build_parser()` and `AGENT_VERBS` in
`cli_dispatch.py` — the drift-guard tests pin both together). Envelope rollup
shaped like `jrag status`, `--days N` (default 7), `--format text|json`:

- **Usage**: per verb — calls, status mix (ok/ambiguous/not_found/error),
  median result count, truncated %, p50/p95 latency split by `served_by`.
- **Staleness**: empty/not_found rate binned by `index_age_s`.
- **Sessions**: grouped by `ppid`+`cwd` with the 30-minute inactivity cutoff
  (adjacency required, guarding against pid reuse) — count, queries per
  session, terminal-outcome distribution.
- **Struggle signals**: top repeated identical (verb, query) pairs;
  not_found→reformulation chain count; abandoned queries (miss with no
  follow-up jrag call in-session).
- **Absence mining**: top missed query terms classified through the absence
  engine's existing closeness data — typo/close-miss (retrieval problem) vs
  genuinely-absent (product gap) vs external (noise).
- **Watch health**: reindex success rate, consecutive failures, last N
  failures with stderr excerpts, duration trend.
- **Feedback**: label counts and agreement with the proxy metrics.
- **Storage**: file count, bytes, oldest retained day, dropped-overflow count.

Missing or garbage event files render a zero-state envelope (rc 0, with
enable instructions when telemetry is off) — the `jrag prime` "never nag,
never crash the hook" contract. Torn lines are counted and skipped.

## Feedback labels

When telemetry is enabled, every envelope carries an `event_id` (short
deterministic hash of the recorded event; omitted-when-empty keeps text
output clean, following the `is_external_entrypoint` field-plumbing
precedent). New agent verb `jrag feedback <event_id> --good|--bad [--note …]`
appends to `feedback.jsonl`. `jrag usage` joins labels to events; labels
whose events aged out of retention report as unlabeled-orphans rather than
disappearing silently.

## Health surfacing (gated by `usage.enabled`)

Rendering-only changes at surfaces agents already read:

- `jrag watch --status` renders `last_error` (ungated — display of existing
  state).
- `jrag status` gains a daemon-health section (pid liveness, heartbeat age,
  consecutive failures) and emits `Envelope.warnings` when unhealthy.
- `jrag prime`'s daemon state goes from binary running/not-running to
  "running, reindex failing since X".

Health warnings live on `status`/`prime` only — routine verb envelopes stay
clean; per-call staleness is captured in events instead.

## eval `--reuse-index`

`EvalConfig` gains `reuse_index`, skipping the unconditional index rebuild so recall@k / MRR run against the
live index (the LadybugGraph singleton rebind stays — it is what binds the
process to the reused index). An unreadable GraphMeta is recorded as
``{"error": …}`` rather than swallowed; the report carries a ``reuse_index``
mode marker. The run
additionally dumps `graph.meta()` quality fields (`parse_errors`, resolution
percentages) into `report.json`, making index health and recall jointly
analyzable over time. Tier-A ground truth regenerates from current symbols;
Tier-B frozen queries stay fixed for trend stability. `docs/JRAG-CLI.md`
gets a nightly cron one-liner. Ungated (see Decision 1).

## Configuration & privacy

- Knobs: `usage.enabled` (bool, default **false**; env
  `JAVA_CODEBASE_RAG_USAGE_ENABLED`, YAML `usage.enabled`) and `usage.dir`
  (env `JAVA_CODEBASE_RAG_USAGE_DIR`), each with `SettingSource` provenance
  per the existing pattern.
- Disabled behavior: no event files, no new daemon state fields, no
  heartbeat, no `event_id`, no health warnings — byte-for-byte today's
  behavior. `jrag usage` / `jrag feedback` render the zero-state with enable
  instructions.
- `docs/CONFIGURATION.md`: knob documentation plus the "What jrag records
  locally" table (event kinds × fields).
- Import-lint test: the `usage` package imports only stdlib non-network
  modules.
- All operator-facing strings through `tr()` with en + ru messages.

## Failure modes & recovery

- **Telemetry write failure** (disk full, permissions): swallowed; optionally
  one debug line under `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`. Host command
  unaffected.
- **Torn/partial line** (concurrent append oddity, crash mid-write):
  `json.loads` failure → counted and skipped by the reader.
- **Day-file overflow**: events beyond the 5 MiB cap are dropped for that
  day; the drop count is surfaced in `jrag usage` storage status.
- **Retention pruning failure**: swallowed; next successful emit retries.
- **Events outlive the watch daemon / vice versa**: all readers treat missing
  counterpart data as zero-state, never error.
- **`feedback.jsonl` orphan labels** (event pruned after 30 days): reported
  as orphans in the feedback section.

## Tests

- Writer: append correctness, day-file selection, 1 KiB line cap, 200-char
  query cap, retention pruning, overflow drop accounting, swallow-guard
  (simulated OSError does not propagate).
- `summarize`: purity tests over synthetic event streams — percentiles,
  session grouping incl. pid-reuse adjacency guard, struggle signals,
  absence classification join, feedback join incl. orphans.
- Envelope: `event_id` presence/omission; golden payload for `jrag usage`
  (zero-state and populated); `jrag feedback` round-trip.
- Watch: `_record` event emission with stderr excerpts; new state fields;
  heartbeat rewrite; `queries_served` increment.
- Health renders: `watch --status` `last_error` (ungated), `status` warnings
  and `prime` enrichment (gated on/off).
- Gating: with `usage.enabled=false`, no files created, no state fields, no
  `event_id`, byte-identical CLI behavior.
- eval: `--reuse-index` skips rebuild; `report.json` carries graph meta.
- Config: knob resolution and provenance for `usage.enabled` / `usage.dir`.
- Import-lint: no network-capable imports in `usage/`.
- i18n: en/ru message completeness for all new strings.
- Dispatch drift tests extended for `usage` and `feedback` verbs.

## Files touched (design-level)

New: `src/java_codebase_rag/usage/{__init__,paths,events,writer,summarize}.py`.

Modified: `jrag.py` (`_emit` stash, `main()` event write, `usage`/`feedback`
verbs, `watch --status` render), `jrag_envelope.py` (`event_id`),
`jrag_render.py` (reuse `count_results`), `watch/daemon.py` (`_record`
extension, state fields, heartbeat), `watch/watcher.py` (stderr excerpts into
events), `watch/server.py` (`queries_served`), `cli.py` (operator-CLI event
mirror), `cli_dispatch.py` (`AGENT_VERBS`), `config.py` (knobs + provenance),
`eval/runner.py` (`reuse_index`, meta dump), `i18n_messages_en.py` /
`i18n_messages_ru.py`, `docs/CONFIGURATION.md`, `docs/JRAG-CLI.md`,
`docs/DESIGN.md`, `docs/ARCHITECTURE.md`, tests.

## TL;DR

Opt-in local observability for jrag: a stdlib `usage/` package writes capped,
day-sharded JSONL usage/reindex events (single atomic appends, swallow-guard,
durable per-project state dir) from the agent-CLI, operator-CLI, and
watch-daemon funnels; `jrag usage` summarizes usage, staleness, sessions,
struggle signals, absence mining, watch health, and storage; `jrag feedback`
attaches sparse owner labels via envelope `event_id`; daemon health surfaces
on `status`/`prime`; `eval --reuse-index` makes nightly live-index quality
runs cron-cheap. One switch — `usage.enabled`, default off — gates everything
except rendering the already-recorded `last_error` and the eval mode. Strictly
local: identifiers not content, network-free by import-lint test.
