# Local Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Opt-in local observability for jrag: usage/reindex JSONL events, a `jrag usage` summary, `jrag feedback` labels, gated health surfacing, and `eval --reuse-index`.

**Architecture:** A new stdlib-only `usage/` package (paths, events contract, JSONL writer with swallow-guard, pure aggregation) is tapped at three funnels — agent CLI `main()`, operator CLI `main()`, watch daemon `_record()`. One config switch (`usage.enabled`, default false) gates everything except rendering the already-recorded watch `last_error` and the eval reuse mode. Events live in a durable per-project state dir (never the index dir).

**Tech Stack:** Python stdlib only for the `usage/` package (`json`, `os`, `time`, `hashlib`, `pathlib`, `datetime`, `collections`, `statistics`). No new runtime dependencies.

**Spec:** `docs/superpowers/specs/active/2026-09-13-local-observability-design.md`

## Global Constraints

- Python: `.venv/bin/python` / `.venv/bin/pip` only — never system python/pip (AGENTS.md).
- Before test runs: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.yml tests/*/.java-codebase-rag.hosts` (stale indexes hijack discovery; AGENTS.md).
- All operator-facing strings via `tr()` with keys in BOTH `i18n_messages_en.py` and `i18n_messages_ru.py`.
- Env vars keep the `JAVA_CODEBASE_RAG_*` prefix (backward-compat naming is intentional).
- `usage/` imports stdlib non-network modules only — enforced by an import-lint test (Task 13).
- Telemetry never changes stdout/stderr/exit codes when disabled OR on internal failure (byte-identical behavior when off).
- Constants (not knobs): retention 30 days, day-file cap 5 MiB, line cap 512 bytes, query cap 200 chars, stderr excerpt 2 KB, heartbeat 30 s, session cutoff 30 min.
- Commit style: conventional commits (`feat(usage): …`, `test(usage): …`, `docs: …`).
- Full test suite runs once at the end (Task 14); per-task runs use the relevant subset.

---

### Task 1: `usage/paths.py` — state dir and event file derivation

**Files:**
- Create: `src/java_codebase_rag/usage/__init__.py` (empty package marker)
- Create: `src/java_codebase_rag/usage/paths.py`
- Test: `tests/usage/test_paths.py`

**Interfaces:**
- Consumes: `java_codebase_rag.watch.paths.project_key(index_dir: Path) -> str` (reuse by import; do not duplicate the hash).
- Produces:
  - `state_dir(override: str | None = None) -> Path` — resolution order: `override` (if non-empty) > `$XDG_STATE_HOME/jrag` > platform default: `~/.local/state/jrag` on Linux, `~/Library/Application Support/jrag` on macOS. Created with `parents=True, exist_ok=True` before returning. Pure-path + mkdir only; no other I/O.
  - `project_events_dir(project_key: str, override: str | None = None) -> Path` — `state_dir(override) / "events" / project_key` (created on call).
  - `day_file(project_key: str, day: datetime.date, override: str | None = None) -> Path` — `project_events_dir(...) / f"events-{day:%Y-%m-%d}.jsonl"`.
  - `feedback_file(project_key: str, override: str | None = None) -> Path` — `project_events_dir(...) / "feedback.jsonl"`.
  - `drops_file(project_key: str, day: datetime.date, override: str | None = None) -> Path` — `project_events_dir(...) / f"events-{day:%Y-%m-%d}.drops"` (holds one integer: events dropped past the size cap that day).

- [ ] **Step 1: Write failing tests**

`tests/usage/test_paths.py` (create `tests/usage/__init__.py` if the suite needs it; mirror the layout of `tests/eval/`): five tests using `monkeypatch`/`tmp_path`:
1. `test_state_dir_override_wins` — `state_dir("/tmp/x")` returns `Path("/tmp/x")` (created).
2. `test_state_dir_xdg` — with `XDG_STATE_HOME=/fake/xdg` (monkeypatch env, tmp_path), returns `/fake/xdg/jrag`.
3. `test_state_dir_macos_default` — with `XDG_STATE_HOME` removed, `sys.platform` monkeypatched to `"darwin"`, `HOME` set to tmp_path → `HOME/Library/Application Support/jrag`.
4. `test_state_dir_linux_default` — same but platform `"linux"` → `HOME/.local/state/jrag`.
5. `test_event_layout` — `project_events_dir("abc123def456")` ends with `events/abc123def456`; `day_file(..., date(2026, 9, 13))` ends with `events-2026-09-13.jsonl`; `feedback_file` ends with `feedback.jsonl`; `drops_file` ends with `events-2026-09-13.drops`.

- [ ] **Step 2: Run tests — expect FAIL** (ModuleNotFoundError: `java_codebase_rag.usage`)

Run: `.venv/bin/python -m pytest tests/usage/test_paths.py -q`

- [ ] **Step 3: Implement `usage/paths.py`**

Follow the style of `watch/paths.py` (module docstring stating pure-path role, resolution-order docstring). Implement the five functions per the Produces contract. `project_key` is imported from `watch.paths` by CALLERS, not by this module (paths stays key-agnostic).

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/usage/test_paths.py -q`

- [ ] **Step 5: Commit**

`git add src/java_codebase_rag/usage/ tests/usage/` → `git commit -m "feat(usage): state dir + event file path derivation"`

---

### Task 2: `usage/events.py` — event record contract

**Files:**
- Create: `src/java_codebase_rag/usage/events.py`
- Test: `tests/usage/test_events.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure data layer).
- Produces:
  - Constants: `SURFACES = ("cli", "watch")`, `EVENT_KINDS = ("command", "reindex", "daemon")` (open enums — `"mcp"` joins SURFACES later without format change).
  - `cap_query(q: str | None) -> str | None` — None passes through; otherwise cut at the first `"\n"`, then truncate to 200 chars.
  - `derive_event_id(*stable: object) -> str` — first 10 hex chars of SHA256 over `repr(stable)`. Deterministic, collision-safe for this scale.
  - `build_command_event(verb, query, flags: dict, duration_ms, rc, envelope_facts: dict, index_age_s, served_by, ppid, cwd, project_key) -> dict` — returns the full record: common core `{v: 1, ts: <RFC3339 UTC with ms>, surface: "cli", event: "command", project_key, pid: os.getpid()}` plus payload `{verb, query: cap_query(query), flags, duration_ms, rc, envelope_facts, index_age_s, served_by, ppid, cwd}`. `envelope_facts` keys: `status`, `result_count`, `truncated`, `candidates_count`, `absence_verdict`, `absence_cause`, `warnings_count` (each None-safe). All None-able optional fields stay explicit `None` (not omitted) so lines are uniform.
  - `build_reindex_event(kind, detail: dict, project_key) -> dict` — common core with `surface: "watch"`, `event: "reindex"`, payload `{kind, detail}` where error-phase details carry a `stderr_tail` already trimmed to 2 KB by the caller (watcher).
  - `build_daemon_event(lifecycle: str, detail: dict, project_key) -> dict` — `event: "daemon"`, payload `{lifecycle: "start"|"stop", detail}`.
  - `rfc3339_now() -> str` — UTC timestamp with milliseconds, e.g. `2026-09-13T12:34:56.789Z`.

- [ ] **Step 1: Write failing tests**

`tests/usage/test_events.py`:
1. `test_common_core` — `build_command_event(...)` has `v == 1`, `surface == "cli"`, `event == "command"`, string `pid`, `ts` parseable by `datetime.datetime.fromisoformat` after stripping the `Z` suffix.
2. `test_cap_query` — None→None; `"a\nb"`→`"a"`; 300-char string→200 chars.
3. `test_event_id_deterministic` — same args → same id, length 10; different args → different id.
4. `test_reindex_and_daemon_shapes` — kinds/surfaces/event values correct; detail passed through unchanged.

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/usage/test_events.py -q`

- [ ] **Step 3: Implement**

Per Produces contract. No I/O — this module builds dicts only.

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/usage/test_events.py -q`

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): event record contract (v1 schema, capping, event ids)"`

---

### Task 3: Config knobs — `usage.enabled` / `usage.dir`

**Files:**
- Modify: `src/java_codebase_rag/config.py` (dataclass fields + resolution in `resolve_operator_config`)
- Test: `tests/usage/test_config_knobs.py`

**Interfaces:**
- Consumes: existing `_pick_bool(env_key, yaml_dict, yaml_path, default) -> tuple[bool, SettingSource]` and `_pick_str` (config.py:444/487).
- Produces (on `ResolvedOperatorConfig`, following the `watch_*` field style at the end of the dataclass):
  - `usage_enabled: bool = False`, `usage_enabled_source: SettingSource = "default"`
  - `usage_dir: str | None = None`, `usage_dir_source: SettingSource = "default"`
  - Resolution: `usage_enabled` via `_pick_bool(env_key="JAVA_CODEBASE_RAG_USAGE_ENABLED", yaml_dict=yaml_dict, yaml_path=("usage", "enabled"), default=False)`; `usage_dir` via `_pick_str(env_key="JAVA_CODEBASE_RAG_USAGE_DIR", yaml_path=("usage", "dir"), default=None)`. **Default false — opt-in is the spec's Decision 1.**

- [ ] **Step 1: Write failing tests**

Using the same construction pattern as existing config tests (find them with `grep -rl "resolve_operator_config" tests/ | head`):
1. `test_usage_disabled_by_default` — no env, no YAML → `usage_enabled is False`, source `"default"`.
2. `test_usage_enabled_via_env` — env `JAVA_CODEBASE_RAG_USAGE_ENABLED=1` → True, source `"env"`.
3. `test_usage_enabled_via_yaml` — YAML `usage: {enabled: true}` → True, source `"yaml"`; env wins over YAML when both set.
4. `test_usage_dir_resolution` — env `JAVA_CODEBASE_RAG_USAGE_DIR=/tmp/u` → `"/tmp/u"`, source `"env"`; default None.

- [ ] **Step 2: Run tests — expect FAIL** (AttributeError: no `usage_enabled`)

Run: `.venv/bin/python -m pytest tests/usage/test_config_knobs.py -q`

- [ ] **Step 3: Implement**

Add the four fields to `ResolvedOperatorConfig` (after the `language*` fields, with a comment: "Local observability (opt-in); see docs/superpowers/specs/active/2026-09-13-local-observability-design.md"). Add the two `_pick_*` calls in `resolve_operator_config` beside the hints/language picks. Do NOT add these to `apply_to_os_environ` (the tap reads the resolved value directly).

- [ ] **Step 4: Run tests — expect PASS** (also run `tests/package/test_config*.py` if present — `ls tests/package | grep config`)

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): opt-in config knobs usage.enabled/usage.dir (default off)"`

---

### Task 4: `usage/writer.py` — append discipline

**Files:**
- Create: `src/java_codebase_rag/usage/writer.py`
- Test: `tests/usage/test_writer.py`

**Interfaces:**
- Consumes: Task 1 paths; Task 2 event dicts.
- Produces:
  - `record_event(event: dict, *, enabled: bool, state_dir_override: str | None = None) -> bool` — the single write entry point. Returns True when a line was appended. **The entire body runs inside `try/except Exception: pass`** (a debug stderr line `jrag: usage event dropped: <err>` is emitted only when env `JAVA_CODEBASE_RAG_DEBUG_CONTEXT` is truthy). Behavior inside the guard:
    1. If not `enabled` → return False immediately (no dirs created, no files touched).
    2. Serialize `json.dumps(event, separators=(",", ":"), default=str)`; if the line exceeds **512 bytes**, drop `event["query"]`→re-serialize; if still over, drop the event (counted via drops file) and return False.
    3. Day file = `day_file(event["project_key"], today, override)`; if the file already exists and its size ≥ **5 MiB (5_242_880)**, increment the drops counter file and return False.
    4. Append: `os.open(path, os.O_WRONLY|os.O_APPEND|os.O_CREAT, 0o644)` + one `os.write` of line+`"\n"` (encode utf-8) + `os.close`.
    5. Retention prune: delete `events-*.jsonl` and matching `.drops` files in the events dir whose date is older than **30 days** from today (filename parse; unparseable names untouched).
  - `record_feedback(label: dict, *, enabled: bool, state_dir_override: str | None = None) -> bool` — same guard + single-write append to `feedback_file(project_key, ...)`; no size cap, no prune (labels are tiny).
  - `bump_drops(path: Path) -> None` — best-effort read-int/write-int+1 of the drops file (used by `record_event`; tolerant of missing/garbage content → restart at 1).
- Error cases: every failure mode (permissions, disk full, serialize error) is swallowed by the guard — this function never raises.

- [ ] **Step 1: Write failing tests**

`tests/usage/test_writer.py` (point `state_dir_override` at `tmp_path`):
1. `test_disabled_writes_nothing` — `record_event(ev, enabled=False, ...)` returns False; no `events/` tree created under the override.
2. `test_appends_one_line` — two records → day file exists with exactly 2 lines, each `json.loads`-able, keys intact.
3. `test_oversize_line_drops_query_then_event` — event whose serialized form > 512 bytes with a long query → query dropped, line written ≤ 512; an event still > 512 without query → nothing written, drops file contains `1`.
4. `test_size_cap_drop` — pre-create the day file at exactly 5 MiB (write 5 MiB of padding) → record skipped, drops `1`.
5. `test_retention_prune` — create `events-2020-01-01.jsonl` in the events dir → after a successful record it is deleted; `events-<today>.jsonl` remains; a non-date file `notes.txt` remains.
6. `test_never_raises` — monkeypatch `os.open` to raise OSError → returns False, no exception (with `JAVA_CODEBASE_RAG_DEBUG_CONTEXT` unset).
7. `test_record_feedback_appends` — label dict → one line in `feedback.jsonl`.

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/usage/test_writer.py -q`

- [ ] **Step 3: Implement**

Per Produces contract; module docstring states the discipline (single os.write, synchronous, swallow-guard, why: `os._exit()` past buffered flushes in both `main()` paths).

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): JSONL writer — atomic append, caps, retention, swallow-guard"`

---

### Task 5: Agent CLI tap — `_emit` stash + `main()` write

**Files:**
- Modify: `src/java_codebase_rag/jrag.py` (`_emit` at ~:198, `main()` at ~:4523-4573)
- Modify: `src/java_codebase_rag/watch/client.py` (expose served-by)
- Test: `tests/usage/test_cli_tap.py`

**Interfaces:**
- Consumes: Tasks 1-4 (`build_command_event`, `record_event`, `derive_event_id`, `state_dir`); `count_results(envelope, shape)` from `jrag_render.py:824`; `watch.paths.state_path/index-dir`, `watch.paths.project_key`.
- Produces:
  - `watch/client.py`: module-level `LAST_SERVED_BY: str | None = None` — set to `"daemon"` on daemon success inside `get_payload`, `"cold"` on the fallback branch, `None` for commands that don't route through `get_payload`.
  - `jrag.py`: module-level `_telemetry_stash: dict` — populated in `_emit` with `{status, result_count, truncated, candidates_count, absence_verdict, absence_cause, warnings_count, shape, event_id}` where `event_id = derive_event_id(ts-now, pid, verb, query)` and `envelope.event_id` is set BEFORE `render(...)` is called (so text/JSON output carries it); cleared at the start of `_emit`.
  - `main()` tap: time from just before `handler(args)` (`time.perf_counter`) to after; resolve cfg once via the same config helper the verbs use (find with `grep -n "resolve_operator_config" src/java_codebase_rag/jrag.py | head`); on completion OR exception, call a new private helper `_record_telemetry(verb, args, rc, duration_ms, error_type)` that:
    - returns immediately when `cfg.usage_enabled` is False,
    - reads `index_age_s` from the watch state file (`json.load(state_path(cfg.index_dir))["last_reindex_at"]` → `time.time() - value`; None on any failure),
    - composes via `build_command_event(...)` with `served_by=watch.client.LAST_SERVED_BY`, `ppid=os.getppid()`, `cwd=os.getcwd()`, flags subset `{limit, format, detail, count, exists, service}` (getattr with None),
    - `record_event(..., enabled=cfg.usage_enabled, state_dir_override=cfg.usage_dir)`.
  - Error paths record too: the argparse-error except-path and the handler-exception except-path each call `_record_telemetry` with `rc=2` and `error_type` set (`"usage_error"` / exception class name); `envelope_facts.status="error"`.
- Gating rule: when disabled, `_emit` does NOT set `envelope.event_id` and no state file read happens (check the flag before the state-file read — resolve cfg cheaply or stash `usage_enabled` on args during parsing; acceptable: `_record_telemetry` resolves cfg and `_emit` checks a cached flag set by the parser setup only when telemetry is on — simplest correct form: `_emit` always computes the id but only sets `envelope.event_id` if `_usage_enabled_cached()`, a module-level bool refreshed once per process from resolved config with its own swallow-guard).

- [ ] **Step 1: Write failing tests**

`tests/usage/test_cli_tap.py` — invoke `jrag.main([...])` in-process against a small fixture index (copy the setup pattern from an existing fast verb test: `grep -rl "main(\[" tests/jrag | head`), with env `JAVA_CODEBASE_RAG_USAGE_ENABLED=1` and `JAVA_CODEBASE_RAG_USAGE_DIR=<tmp>`:
1. `test_ok_invocation_recorded` — run `search <known-symbol>` → today's day file has 1 event: verb `search`, `rc == 0`, `envelope_facts.status == "ok"`, `result_count >= 1`, `pid`/`ppid` ints, `cwd` str, `served_by` in `{"daemon", "cold"}` (cold in tests).
2. `test_disabled_writes_nothing` — same invocation without the env → no events dir created; envelope JSON contains no `event_id`.
3. `test_error_invocation_recorded` — a verb raising internally (monkeypatch a handler to raise RuntimeError) → event with `rc == 2`, `envelope_facts.status == "error"`, `error_type == "RuntimeError"`.
4. `test_event_id_on_envelope` — with telemetry on, JSON output (`--format json`) contains a 10-char `event_id` equal to the id in the recorded event line.
5. `test_usage_error_recorded` — `main(["jrag", "search"])` missing positional → event recorded with `error_type == "usage_error"` (argparse path).

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement**

Per Produces contract. Keep `_emit`'s public behavior byte-identical when telemetry is off (the only additions are stash mutation + optional `envelope.event_id`). LAST_SERVED_BY is reset to None at the start of each `get_payload` call.

- [ ] **Step 4: Run tests — expect PASS** (plus the existing jrag golden subset: `.venv/bin/python -m pytest tests/jrag -q` — must stay green)

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): agent-CLI tap — per-invocation events, event_id, served_by, index age"`

---

### Task 6: Operator CLI mirror

**Files:**
- Modify: `src/java_codebase_rag/cli.py` (`main()` at :1276)
- Test: `tests/usage/test_operator_tap.py`

**Interfaces:**
- Consumes: Task 4 `record_event`, Task 2 `build_command_event`.
- Produces: `cli.py` `main()` wraps dispatch (find the handler invocation — same structure as jrag: `args.handler(args)`) with the same timing + `_record_operator_telemetry(verb, rc, duration_ms, error_type)` helper: verb = `args.command` (operator verbs expose it; fallback to argv token), `envelope_facts` minimal `{status: "ok" if rc == 0 else "error"}`, query None, flags `{}`. Gated identically via resolved config (operator CLI already resolves config — reuse that object).

- [ ] **Step 1: Write failing tests**

Model on an existing fast operator test (`grep -rl "_cmd_tables\|def test.*meta" tests | head`); run `main(["jrag", "meta", ...])` or `tables` against a fixture index with telemetry env on:
1. `test_operator_invocation_recorded` — 1 event, `surface == "cli"`, verb `meta`/`tables`, rc 0.
2. `test_operator_disabled` — no env → nothing written.

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement** per contract (≤25 lines; reuse jrag-side helper by importing `usage.writer`/`usage.events` directly, not jrag internals).

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): operator-CLI event mirror"`

---

### Task 7: Watch daemon tap — events, state fields, heartbeat, queries_served

**Files:**
- Modify: `src/java_codebase_rag/watch/daemon.py` (`_state` init ~:110-132, `_record` ~:250-273, serve loop)
- Modify: `src/java_codebase_rag/watch/watcher.py` (reindex error detail: add `stderr_tail`; ~:275-345)
- Modify: `src/java_codebase_rag/watch/server.py` (query counter hook in dispatch; ~:196-233)
- Test: `tests/usage/test_watch_tap.py`

**Interfaces:**
- Consumes: Tasks 2/4 (`build_reindex_event`, `build_daemon_event`, `record_event`); daemon's existing `cfg`.
- Produces:
  - `_state` gains: `consecutive_errors: int = 0`, `last_vectors_ok_at: None`, `last_graph_ok_at: None` (queries_served already exists as 0).
  - `_record(kind, detail)` additionally: on `indexing_done` → reset `consecutive_errors=0`; on `error` → `consecutive_errors += 1`, and set `last_vectors_ok_at`/`last_graph_ok_at` on successful respective phases (the `vectors`/`graph` kinds already flow through here — check `watcher.py:28-30` kind list); ALWAYS (when `cfg.usage_enabled`) append a `build_reindex_event(kind, detail, project_key)` via `record_event` — the swallow-guard makes this safe on the debounce thread.
  - Lifecycle: daemon start appends `build_daemon_event("start", {mode, pid})`, `_shutdown` appends `("stop", {reason})` — both gated.
  - Heartbeat: the serve loop (which already ticks; find `_serve_until_stopped`) rewrites the state file when `time.time() - self._last_state_write >= 30` even with no events — via a `force=True` parameter on `_maybe_write_state_locked`.
  - `watcher.py` reindex: on non-zero return from `run_cocoindex_update` / `run_incremental_graph`, include `"stderr_tail": (captured stderr)[-2048:]` in the error detail dict passed to `on_event` (the captured text is already in the subprocess result object — `pipeline.py` quiet path captures it; surface what's there, empty string if None).
  - `server.py` dispatch: count every successfully served request — constructor gains optional `on_query: Callable[[], None] | None = None`; `WatchDaemon` wires it to `self._state["queries_served"] += 1` under `_state_lock` (replacing the stub comment).
- Gating: all new fields/writes/events/heartbeat only when `cfg.usage_enabled`; disabled → `_record` behavior byte-identical to today, no new state keys (state file schema when disabled must remain the old one).

- [ ] **Step 1: Write failing tests**

`tests/usage/test_watch_tap.py` (drive `WatchDaemon._record` directly on a partially-built daemon object, pattern from existing `tests/watch/` tests):
1. `test_error_then_success_counters` — `indexing_started` → `error` (vectors phase) → `indexing_done`: `consecutive_errors` ends 0, incremented to 1 after error; `last_error` recorded as today.
2. `test_error_event_appended_with_stderr_tail` — with telemetry on, `error` detail `{"phase": "vectors", "returncode": 1, "stderr_tail": "boom"}` → day file has a `surface=="watch"`, `event=="reindex"` line with `detail.stderr_tail=="boom"`.
3. `test_disabled_no_events_no_fields` — telemetry off: `_record` writes no events; state file JSON lacks `consecutive_errors`.
4. `test_queries_served_increments` — construct `WatchServer` (or call its dispatch with a stub socket/request per existing server tests) with an `on_query` callback → callback invoked per request; daemon wiring bumps the state dict.
5. `test_heartbeat_rewrite` — simulate: `_last_state_write = time.time() - 60`, call the heartbeat path → state file mtime refreshed, content still valid JSON.

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement** per contract. Keep the `queries_served` stub comment replaced by the wiring note.

- [ ] **Step 4: Run tests — expect PASS** (plus `.venv/bin/python -m pytest tests/watch -q`)

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): watch daemon tap — reindex events, failure counters, heartbeat, queries_served"`

---

### Task 8: `usage/summarize.py` — pure aggregation

**Files:**
- Create: `src/java_codebase_rag/usage/summarize.py`
- Test: `tests/usage/test_summarize.py`

**Interfaces:**
- Consumes: event dict schema (Task 2); `feedback.jsonl` label schema `{ts, event_id, rating: "good"|"bad", note}`.
- Produces (all pure — take data, return data; no file I/O except the two `load_*` readers):
  - `load_events(files: list[Path], since_days: int) -> tuple[list[dict], int]` — stream lines, `json.loads` each in try/except (torn → increment the returned counter), filter `ts >= now - since_days*86400`.
  - `load_labels(file: Path) -> list[dict]` — same tolerance.
  - `percentile(values: list[float], pct: float) -> float | None` — nearest-rank; None for empty.
  - `sessions(events: list[dict]) -> list[list[dict]]` — group by `(ppid, cwd)` sorted by ts; split on gaps > 1800 s; a group whose ppid appears again in a LATER session of another group stays separate (adjacency rule: consecutive events with same key merge only — pid-reuse guard).
  - `per_verb(events) -> list[dict]` — one row per verb: `calls`, `ok/not_found/ambiguous/error` counts, `empty_ok` (ok with `result_count == 0`), `median_result_count`, `truncated_count`, `p50_ms`/`p95_ms` overall and split `p50_ms_cold`/`p50_ms_daemon` (None when no samples).
  - `staleness_bins(events) -> list[dict]` — buckets `<1h, 1-6h, 6-24h, >24h, unknown` by `index_age_s`: per bucket `calls` and `miss_count` (`not_found` + `empty_ok`).
  - `struggle(sessions_result) -> dict` — `top_repeats` (top 5 `(verb, query)` by count>1 across window), `reformulation_chains` (count of not_found/ambiguous followed by same-verb different-query from same session within 300 s), `abandoned` (terminal session event is not_found/ambiguous or empty_ok with no later event in that session).
  - `absence_top(events, limit=10) -> list[dict]` — aggregate `not_found`/`empty_ok` events by lowercased query term, carrying `absence_verdict`/`absence_cause` counts per term.
  - `feedback_join(events, labels) -> dict` — `{labels_total, good, bad, matched_event_ids, orphan_ids}` (orphan = label whose event_id not in events — aged out).
  - `storage_status(files: list[Path], drops: list[Path]) -> dict` — `{files, total_bytes, oldest_day, drops_total}`.
  - `watch_health(events, state: dict | None) -> dict` — reindex success rate over window (`indexing_done` vs `error`), `consecutive_errors`/`last_error`/`last_vectors_ok_at`/`last_graph_ok_at`/`queries_served` passthrough from state, last 3 failure events with `stderr_tail`, reindex duration p50/max.

- [ ] **Step 1: Write failing tests**

`tests/usage/test_summarize.py` — synthetic event lists built inline (no fixtures):
1. `test_load_events_skips_torn` — 3 lines, middle invalid JSON → 2 events, torn 1.
2. `test_sessions_gap_split_and_pid_guard` — 4 events: A(ppid=1) t0, A t0+100, A t0+4000 (>1800s → new session), B(ppid=2) t0+4100 → 3 sessions.
3. `test_per_verb_metrics` — hand-built events → exact counts/percentiles asserted.
4. `test_staleness_bins` — events with `index_age_s` 500/7_200/10⁵/None → bucket assignment + miss counting.
5. `test_struggle_signals` — a session with search miss → same verb different query (reformulation), a repeat pair, and a terminal miss (abandoned) → all three counted.
6. `test_absence_top_and_feedback_join` — missed terms grouped; one label matched, one orphan.
7. `test_watch_health` — 3 done + 1 error + state passthrough → 75% success, failures list carries stderr_tail.

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement** — pure functions; `statistics.median` for medians; no imports beyond stdlib non-network.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): pure aggregation — verbs, staleness, sessions, struggle, absence, feedback, health"`

---

### Task 9: `jrag usage` verb

**Files:**
- Modify: `src/java_codebase_rag/jrag.py` (parser registration + `_cmd_usage` handler)
- Modify: `src/java_codebase_rag/cli_dispatch.py` (`AGENT_VERBS` += `usage`)
- Modify: `src/java_codebase_rag/i18n_messages_en.py`, `i18n_messages_ru.py`
- Test: `tests/usage/test_cmd_usage.py`, plus update the drift-guard test (find via `grep -rn "AGENT_VERBS" tests/`)

**Interfaces:**
- Consumes: Tasks 1-8.
- Produces:
  - Parser: subcommand `usage` with `--days INT` (default 7, clamp 1-30), registered through the same common-parser pattern as `status` (inherits `--format`/`--detail`); handler `_cmd_usage(args) -> int`.
  - Handler flow: resolve cfg → if not `cfg.usage_enabled`: return an `ok` envelope with a single message node instructing how to enable (`tr()` key `MSG_USAGE_DISABLED`, text names both `JAVA_CODEBASE_RAG_USAGE_ENABLED=1` and YAML `usage.enabled: true`), rc 0. Else: glob the project's event files (`state_dir(cfg.usage_dir)/events/<project_key>/events-*.jsonl` + `.drops` + `feedback.jsonl`), read watch state file, call the Task 8 functions, and build an Envelope rollup modeled on `_cmd_status` — one node per section: `usage` (per-verb rows), `staleness`, `sessions` (`count`, `median_queries`, `terminal_outcomes`), `struggle`, `absence`, `watch_health`, `feedback`, `storage`. Missing dir → zero-state ok envelope (all sections empty, `tr()` zero-state message), rc 0.
  - Render: via `_emit`-adjacent standard path (the `status` node pattern uses `_emit` with shape `"status"`-like rollup; mirror it — inspect how `_cmd_status` renders and copy the shape).
  - i18n keys (both en + ru): section titles, `MSG_USAGE_DISABLED`, `MSG_USAGE_EMPTY`.
- Zero-state and disabled states MUST NOT be `error` status — rc 0.

- [ ] **Step 1: Write failing tests**

1. `test_disabled_zero_state` — no env → rc 0, envelope ok, message mentions the env var; no file access (monkeypatch `usage.summarize.load_events` to raise if called).
2. `test_empty_zero_state` — telemetry on, empty state dir → rc 0, ok, sections present-empty.
3. `test_populated_rollup` — write synthetic events into the tmp state dir (reuse builders from Task 8 tests), run `main(["jrag", "usage", "--format", "json"])` → parsed JSON contains per-verb counts matching inputs; `--days 1` filters older events out.
4. `test_drift_guard` — `AGENT_VERBS` contains `usage` (the existing drift test file updated).

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement** per contract; add all i18n keys to both message files.

- [ ] **Step 4: Run tests — expect PASS** (plus `.venv/bin/python -m pytest tests/jrag -q`)

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): jrag usage summary verb (rollup, zero-states, i18n en+ru)"`

---

### Task 10: `event_id` envelope field + `jrag feedback` verb

**Files:**
- Modify: `src/java_codebase_rag/jrag_envelope.py` (field + serialization)
- Modify: `src/java_codebase_rag/jrag.py` (parser + `_cmd_feedback`)
- Modify: `src/java_codebase_rag/cli_dispatch.py` (`AGENT_VERBS` += `feedback`)
- Modify: `src/java_codebase_rag/i18n_messages_en.py`, `i18n_messages_ru.py`
- Test: `tests/usage/test_feedback.py`; golden envelope tests if they pin field sets (`grep -rn "is_external_entrypoint" tests/jrag/golden | head` — update pinned shapes if needed)

**Interfaces:**
- Consumes: Task 4 `record_feedback`, Task 5 `derive_event_id` wiring.
- Produces:
  - `Envelope.event_id: str | None = None` — included by `to_dict()`/`to_json()` only when non-None (the omitted-when-empty rule; mirror `is_external_entrypoint` handling).
  - `jrag feedback <event_id> --good | --bad [--note TEXT]` (exactly one of --good/--bad, mutually exclusive group): resolves cfg; disabled → same zero-state pattern as Task 9 (`MSG_USAGE_DISABLED`), rc 0. Enabled: verify the event_id exists in the project's event files (stream-search all retained day files); found → append `{ts, event_id, rating, note: note[:500]}` via `record_feedback`, ok envelope `feedback: recorded`, rc 0; not found → `not_found` envelope (rc 0 per resolve-miss convention), message explaining retention window.
- The search-for-id reader lives in `usage/summarize.py` as `event_exists(files: list[Path], event_id: str) -> bool` (streaming, tolerant of torn lines).

- [ ] **Step 1: Write failing tests**

1. `test_envelope_omits_event_id_when_none` — `Envelope(status="ok").to_dict()` has no `event_id` key.
2. `test_feedback_roundtrip` — synthetic event file containing id X; `main(["jrag","feedback","X","--good","--note","helpful"])` → rc 0, feedback.jsonl line `{event_id: X, rating: "good", note: "helpful"}`.
3. `test_feedback_unknown_id_not_found` — rc 0, envelope `not_found`.
4. `test_feedback_disabled_zero_state` — rc 0, disabled message.
5. `test_note_capped_500` — 600-char note → stored note is 500 chars.

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement** per contract + i18n keys both languages.

- [ ] **Step 4: Run tests — expect PASS** (plus golden subset if pinned shapes changed)

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): envelope event_id + jrag feedback labels"`

---

### Task 11: Health surfacing — `watch --status`, `status`, `prime`

**Files:**
- Modify: `src/java_codebase_rag/jrag.py` (`_cmd_watch_status` ~:1337-1370, `_cmd_status` ~:1575-1626, `_cmd_prime` ~:1700)
- Test: `tests/usage/test_health_surfacing.py`

**Interfaces:**
- Consumes: watch state file (now with health fields when enabled), `WatchDaemon` pid liveness probe pattern (`watch.lock.ProjectLock.read_holder` / `watch.client.is_daemon_alive` — use whichever the status code already imports).
- Produces:
  - `_cmd_watch_status` (UNGATED — display of already-recorded state): render `last_error` when present: phase, humanized age, detail trimmed to one line (~200 chars). Uses `_humanize_age` (jrag.py:1645).
  - `_cmd_status` (gated on `usage_enabled`): new node `daemon_health` with `{running, pid, heartbeat_age_s (state file mtime), consecutive_errors, last_reindex_age_s}` and, when unhealthy (not running OR consecutive_errors ≥ 3 OR heartbeat_age_s > 120), append a `tr()` warning string to `Envelope.warnings`.
  - `_cmd_prime` (gated): `daemon_state` string gains the failing detail: `"running"` / `"not running"` today → `"running, reindex failing since <age>"` when `consecutive_errors > 0`, else unchanged.
- All renders degrade silently on missing/garbage state file (existing `_read_state_file` already returns None — build on it).

- [ ] **Step 1: Write failing tests**

Write state files into a tmp runtime dir and drive the three commands (patterns from existing watch-status tests):
1. `test_watch_status_shows_last_error_ungated` — state with `last_error` → output contains phase + message; works with telemetry env unset.
2. `test_status_daemon_health_gated` — telemetry on + healthy state → `daemon_health` node, no warnings; dead-pid state → warning present. Telemetry off → node absent, no warnings.
3. `test_prime_enrichment_gated` — telemetry on + `consecutive_errors: 2` + fresh `last_reindex_at` → output contains "reindex failing since"; off → binary phrasing only.

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement** per contract + i18n keys both languages.

- [ ] **Step 4: Run tests — expect PASS** (plus `tests/jrag -q`)

- [ ] **Step 5: Commit**

`git commit -m "feat(usage): health surfacing — watch/status/prime renders (gated) + last_error (ungated)"`

---

### Task 12: `eval --reuse-index`

**Files:**
- Modify: `src/java_codebase_rag/eval/runner.py` (`EvalConfig`, `run_eval` ~:395-425)
- Test: `tests/eval/test_reuse_index.py`

**Interfaces:**
- Consumes: existing `_build_index_subprocess` (runner.py), `graph.meta()` reader (`ladybug_queries.LadybugGraph` already opened downstream).
- Produces:
  - `EvalConfig.reuse_index: bool = False`.
  - `run_eval` behavior when `reuse_index=True`: REQUIRE non-empty `cfg.index_dir` (else raise `ValueError("reuse_index requires index_dir")`); SKIP `_build_index_subprocess` and the env-relocation dance stays; after the graph is opened, serialize `graph.meta()` (the plain dict — counts_json may hold JSON strings, pass through) into the report.
  - `EvalReport` gains `graph_meta: dict | None = None` (dataclass field with default; serialized into `report.json` by the existing report writer — extend its dict construction).
  - CLI/entry wiring: find the runner's entrypoint (`grep -n "__main__\|argparse" src/java_codebase_rag/eval/runner.py`); add `--reuse-index` flag mapping to the field. Ungated by usage.enabled (spec Decision 1).

- [ ] **Step 1: Write failing tests**

1. `test_reuse_requires_index_dir` — `EvalConfig(reuse_index=True, index_dir="")` → `run_eval` raises ValueError.
2. `test_reuse_skips_build` — monkeypatch `_build_index_subprocess` to raise AssertionError; `run_eval` with a prebuilt fixture index (existing eval tests build small indexes — reuse their fixture helper) completes without calling it.
3. `test_report_carries_graph_meta` — result report `graph_meta` contains `built_at` key; report.json round-trips it.

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement** per contract.

- [ ] **Step 4: Run tests — expect PASS** (plus `tests/eval -q`)

- [ ] **Step 5: Commit**

`git commit -m "feat(eval): --reuse-index mode + graph_meta in report"`

---

### Task 13: Import-lint test + documentation

**Files:**
- Create: `tests/usage/test_import_lint.py`
- Modify: `docs/CONFIGURATION.md`, `docs/JRAG-CLI.md`, `docs/DESIGN.md`, `docs/ARCHITECTURE.md`
- Test: the import-lint test itself

**Interfaces:**
- Consumes: the finished `usage/` package.
- Produces:
  - `tests/usage/test_import_lint.py`: parse every `src/java_codebase_rag/usage/*.py` with `ast.walk` collecting `Import`/`ImportFrom` root modules; assert every root ∈ {json, os, sys, time, hashlib, pathlib, datetime, collections, statistics, typing, dataclasses, __future__} (extend the set only if a task above introduced a legit need — never add urllib/socket/http/requests/subprocess).
  - `docs/CONFIGURATION.md`: `usage.*` knobs section (env + YAML + defaults + provenance) and the "What jrag records locally" table — one row per event kind (command/reindex/daemon/feedback) × fields recorded, with the caps (200-char query, 512B line, 2KB stderr tail, 5MiB/day, 30d retention) and the explicit "no file contents, no network, ever" statement.
  - `docs/JRAG-CLI.md`: `jrag usage` / `jrag feedback` entries in the operator playbook (workflow, exit codes: always 0 except feedback's internal errors), `watch --status` `last_error` line, and the nightly eval cron one-liner with `--reuse-index`.
  - `docs/DESIGN.md`: observability paragraph under the surfaces/non-goals discussion (local-only, opt-in, identifiers-not-content).
  - `docs/ARCHITECTURE.md`: `usage/` module-map entry + write/read paths.

- [ ] **Step 1: Write the failing import-lint test** — run against the current package; it should PASS once written (the package is already clean by construction); its job is regression. Verify it FAILS by temporarily asserting a forbidden module in a scratch check if you want the red step (optional — a guard test may start green; note that in the commit).

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/usage/test_import_lint.py -q` — PASS.

- [ ] **Step 3: Write the four docs** per contract.

- [ ] **Step 4: Commit**

`git commit -m "docs+test(usage): what-jrag-records table, CLI playbook, module map, import lint"`

---

### Task 14: Full suite + final verification

**Files:** none new.

- [ ] **Step 1:** `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.yml tests/*/.java-codebase-rag.hosts`
- [ ] **Step 2:** Run the full suite: `.venv/bin/python -m pytest -q` (slow — full run per AGENTS.md; use a 600 s timeout and background if needed).
- [ ] **Step 3:** Fix any fallout; re-run affected subsets until green.
- [ ] **Step 4:** Smoke the feature end-to-end in a temp project: enable `JAVA_CODEBASE_RAG_USAGE_ENABLED=1` + `JAVA_CODEBASE_RAG_USAGE_DIR=<tmp>`, run two verbs + a failing one, run `jrag usage --format json`, run `jrag feedback <id> --good`, confirm files + output.
- [ ] **Step 5:** Commit any fixes; update spec Status → `implemented` and commit (`spec: local observability — implemented`).

---

## Self-Review notes (resolved during planning)

- Spec coverage: decisions 1-9 → Tasks 3/4 (switch, knobs), 1-2 (JSONL/dir/discipline), 5 (schema v1), 6 (identifiers + lint), 7 (index_age_s/served_by), 8 (naming), 9 (constants). Sections: taps → Tasks 5-7; `jrag usage` → Tasks 8-9; feedback → Task 10; health → Task 11; eval → Task 12; config/privacy/docs → Tasks 3/13; failure modes → Task 4 tests + zero-state tests; tests section → distributed per task + Task 14.
- Type consistency: `record_event(event, *, enabled, state_dir_override)` used by Tasks 5-7; `build_command_event`/`build_reindex_event`/`build_daemon_event` consistent between Tasks 2, 5, 7; summarize functions consumed by Task 9 with the names defined in Task 8.
- No code in plan; interfaces carry signatures and data shapes only.
