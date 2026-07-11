# Watch mode — warm, self-refreshing `jrag` daemon

- **Status:** design (pre-implementation)
- **Date:** 2026-07-11
- **Branch:** `worktree-watch-mode`
- **Surfaces affected:** `jrag` CLI (new `watch` subcommand + warm read path); config YAML; internal/operator docs; shipped `skills/` + `agents/` artifacts.

## 1. Overview

A single long-running process per project — started by `jrag watch` — that does two jobs with one set of warm resources:

1. **Keeps the index fresh:** watches the Java source tree and runs the cheapest valid incremental reindex on change (reusing the change-detection that already exists).
2. **Keeps queries warm:** holds the embedding model, the LanceDB connection, and the LadybugDB graph open, and serves every `jrag` read command over a Unix socket — so each command returns instantly instead of paying the full per-process cold start (torch + `SentenceTransformer` load + Lance connect).

Every read command still works **without** the daemon (cold, identical to today). The daemon is a pure accelerator and freshness layer, never a dependency.

## 2. Goals

- Eliminate the per-invocation latency of `jrag search` (and the other read commands), which today reload torch + the embedding model + reconnect to Lance on every fresh process.
- Keep the index current with the editor without a manual `java-codebase-rag increment` after every change.
- Achieve both with one warm process, because the warm resources fast queries need are exactly the ones re-indexing touches.

## 3. Background (current state, from the code map)

- The `jrag` one-shot CLI reloads everything per call. The **only** warm state in the codebase lives in the MCP stdio server: `_st_model` (`mcp_v2.py:143`) and `LadybugGraph._instance` (`ladybug_queries.py:361`). The daemon lifts that caching posture into a reusable service for `jrag`.
- `run_search(model=…)` already accepts an **injected** model (`search_lancedb.py:649`); `search_v2` already uses it. The seam for a shared warm model exists.
- `lancedb.connect()` is opened fresh on every call and is **not** cached (`search_lancedb.py:700`).
- Incremental change detection already exists: `FileHashTracker.detect_changes → (added, changed, removed)` via `.graph_hashes.json` (`build_ast_graph.py:529`); vector catch-up is cocoindex-internal. The graph incremental path (`incremental_rebuild`) already falls back to full rebuild on ontology mismatch, crash marker, or >50-file dependent expansion.
- **No locking infrastructure exists.** Two indexers can run on one project with no guard; a graph incremental write does scoped delete+insert that a concurrent reader can observe mid-flight; a vector full-reprocess DROPs+recreates Lance tables. The daemon must introduce mutual exclusion and a consistent-read story.

## 4. Design

### 4.1 Architecture

`jrag watch` starts a daemon that:

- resolves the project (`discover_project_root`) and index dir (same precedence as the rest of the CLI);
- acquires an exclusive project lock (pidfile + `flock`) and derives a socket path from the index dir;
- warms two resources: `_st_model` (via `mcp_v2._get_sentence_transformer`) and the `LadybugGraph` singleton. Lance is connected per query (cheap) and each query reads one atomic committed version, so no Lance connection is cached;
- starts a file watcher over the source tree (native via `watchdog`, polling fallback) using the same `LayeredIgnore` rules as indexing;
- listens on the Unix socket and dispatches read requests to the existing backends (`search_v2`, `LadybugGraph`, `run_search` with the warm `model=`);
- on file change (debounced), runs the cheapest valid incremental reindex.

### 4.2 Components — new vs reused

| New | Reuses (unchanged) |
|---|---|
| `jrag watch` subcommand + daemon loop | `mcp_v2.search_v2` / `_get_sentence_transformer`; `ladybug_queries.LadybugGraph`; `search_lancedb.run_search(model=…)` |
| Unix-socket server + JSON request/response contract | `jrag`'s per-command handlers (rendering unchanged) |
| File watcher (native + polling) | `path_filtering.LayeredIgnore` |
| Debounced reindex dispatcher + per-type router | `pipeline.run_cocoindex_update`; `build_ast_graph.incremental_rebuild` + `FileHashTracker.detect_changes` |
| Project pidfile + `flock` | `.graph_increment_in_progress` crash marker |
| Read-command IPC client + cold fallback | every read command's existing cold path |

### 4.3 CLI surface

- `jrag watch` — foreground (default). Ctrl+C → drain → exit.
- `jrag watch --detach` — run in background; return once the daemon reports ready.
- `jrag watch --stop` — gracefully stop the running watcher for this project (pidfile → signal → drain → remove socket). Works regardless of how it was started.
- `jrag watch --status` — up/down, pid, socket path, last reindex, files pending.
- Flags: `--debounce-ms`, `--backend {auto|watchdog|polling}`, plus shared `--source-root` / `--index-dir`, and `--quiet`.
- **Read commands** (`search`, `find`, `describe`, `callers`, `callees`, `flow`, `inspect`): if a live daemon socket exists for the resolved project, send the request over IPC and render the response exactly as today; otherwise run today's cold in-process path. `status` stays local.

All control verbs live under the single `watch` subcommand (no separate `watcher` noun-group, no service-manager posture such as restart policies or log forwarding).

### 4.4 Data flow — warm query

1. A read command resolves the project and computes the same socket path the daemon uses.
2. Socket present **and** pid alive → connect, send one JSON request, receive one JSON response, render.
3. Otherwise → fall back to the cold in-process path. **No daemon == today's behavior, exactly.**

### 4.5 Data flow — watched change

1. Watcher emits native events; the daemon debounces (~1.5s) to coalesce editor burst-saves.
2. Per-type router selects the cheapest valid op:
   - any `.java` changed → vectors catch-up **and** graph `incremental_rebuild` (full `increment` semantics);
   - only `db/migration/*.sql` / `application*.yml` changed → **vectors-only** (the graph does not index these).
3. Vectors run via `run_cocoindex_update` (cocoindex memo catch-up — changed files only); graph via `incremental_rebuild` (same full-rebuild fallbacks as `increment`).
4. On commit, any in-process read caches the daemon holds are invalidated so the next query reflects the newly committed state. (The embedding model itself is unchanged — same `SBERT_MODEL` — so `_st_model` is not reloaded.)

### 4.6 Lifecycle & locking

- A pidfile + exclusive `flock`, keyed to the resolved index dir, prevent a second watcher **or** a concurrent manual `java-codebase-rag increment` from corrupting the project. If the lock is held, `jrag watch` reports the holding pid and exits non-zero.
- `flock` is released by the OS on holder death, so a `kill -9` leaves no stuck lock; the pidfile/socket are reconciled on next start and by `--stop`/`--status`.
- Shutdown reuses the `os._exit` teardown discipline (the one-shot CLI already needs it to avoid a pyarrow/lance worker-thread crash at interpreter finalization) — applied once, at process end.

### 4.7 Concurrency model — "searches never wait, never see partial"

Verified against the installed engines during planning (see §9): LanceDB commits are atomic per version, and `ladybug` (a kùzu wrapper) has **no transaction API** — every statement autocommits and a `Database` is single-writer. The design therefore differs by store:

- **Lance (vectors):** each query connects fresh (`lancedb.connect`, cheap) and reads the latest **committed** version. cocoindex catch-up writes new versions atomically, so a query sees old-or-new, never partial, never blocked. No handle caching or `checkout` is required for the normal path.
- **Ladybug graph:** no transactions means a concurrent reader on the same file could observe a partially-applied incremental write, and the single-writer rule means the graph build must run as a **subprocess** that owns the file. To honor never-wait/never-partial, the daemon takes a **copy-on-write file snapshot** of `code_graph.lbug` around each graph reindex: before spawning the build, it copies the file to a sidecar and serves graph reads from the sidecar (old, consistent) for the duration; on success it drops the sidecar and reopens the updated original. Graph queries during a reindex thus hit the pre-write snapshot — never blocked, never partial. (Fallback if snapshotting proves unreliable on some filesystems: briefly quarantine graph reads for the duration of the write — a wait of seconds, only for graph queries, only during a graph reindex. Process crashes mid-write are still covered by the existing `.graph_increment_in_progress` marker + full-rebuild fallback.)

### 4.8 IPC contract

- **Socket path:** per-user runtime directory containing `watch-<hash>.sock` and `watch-<hash>.pid`, where `<hash>` is the first 12 hex chars of SHA-256 of the absolute index-dir path. Stable per project, distinct across projects, machine-local. (Runtime dir: `$XDG_RUNTIME_DIR/jrag/` on Linux; `$TMPDIR`-/`~/Library/Caches/jrag/`-based on macOS.)
- **Transport:** newline-delimited JSON (one request per line, one response per line) over `AF_UNIX`.
- **Request:** `{"v":1,"cmd":"<read-cmd>","args":{…}}` — `cmd` ∈ {`search`,`find`,`describe`,`callers`,`callees`,`flow`,`inspect`}; `args` mirror the CLI flags of that command.
- **Response (success):** `{"v":1,"ok":true,"result":<payload>}` where `<payload>` is the same structure the cold path produces (so rendering is shared).
- **Response (error):** `{"v":1,"ok":false,"error":{"kind":"<stable-code>","message":"…"}}`.
- **Version field `v`:** a CLI/daemon whose `v` differs cold-falls-back with a one-line advisory to restart `jrag watch`.

### 4.9 Config (new `watch:` block in `.java-codebase-rag.yml`)

```yaml
watch:
  debounce_ms: 1500      # coalesce editor burst-saves before reindexing
  backend: auto          # auto | watchdog | polling
  poll_interval_ms: 2000 # polling-fallback cadence
```

- Warm query needs no knob — every read command uses the daemon if up, else cold. No new environment variables are introduced; configuration is YAML plus the `jrag watch` flags above.

## 5. Error handling

| Failure | Behavior |
|---|---|
| Daemon crash mid-reindex | Existing `.graph_increment_in_progress` marker → full-rebuild fallback next run (unchanged). |
| cocoindex/tree-sitter binary missing | Same pre-spawn `status=failed` stub the lifecycle CLI emits. |
| Stale pidfile / dead socket (`kill -9`) | Read commands, `--status`, `--stop` detect a dead pid → remove socket → cold fallback or clean exit. `flock` auto-released by the OS. |
| Lock held by another process | `jrag watch` reports the holding pid and exits non-zero; never fights. |
| Model/embedding load failure | Daemon logs and exits non-zero; CLI cold path still works. |
| CLI/daemon `v` mismatch | CLI cold-falls-back with an advisory to restart `jrag watch`. |
| Watch error on a subtree (permissions) | Log and keep watching the rest; do not crash. |

## 6. Testing

Existing ritual applies (`rm -rf tests/*/.java-codebase-rag*`, temp index, editable install).

- **Unit:** debounce/coalesce window; per-type routing (java → vectors+graph; sql/yml → vectors-only); socket-path derivation (stable per project, distinct across projects); pidfile/`flock` acquire and stale-detection; cold fallback when no socket; backend auto-selection (watchdog vs polling).
- **Integration** (gated like the existing heavy e2e tests): daemon on a temp index → touch a `.java` → assert `incremental_rebuild` ran (`FileHashTracker` hashes / counts changed) and a concurrent `jrag search` returns a consistent old-or-new result; assert a second `jrag watch` is refused by the lock; assert `--stop` drains and removes the socket.
- **Concurrency correctness (key risk test):** concurrent graph reads during an incremental write never observe a partial graph — validates the transaction/RWMutex decision.
- **Warm path:** assert the daemon serves a search without re-importing torch — i.e. `run_search` receives the injected warm `model=` (mock the cache) rather than constructing a new `SentenceTransformer`.

## 7. Documentation & shipped-artifact impact

- `docs/JAVA-CODEBASE-RAG-CLI.md` — new `jrag watch` section (foreground / `--detach` / `--stop` / `--status`), the "run it while you code" workflow, and the cold-fallback guarantee.
- `docs/CONFIGURATION.md` — the `watch:` YAML block.
- `docs/ARCHITECTURE.md` — daemon on the read path and a new watch path; note it is the MCP server's warm-cache posture, served to the CLI.
- `docs/DESIGN.md` — watch as a first-class surface; the non-goals below.
- `skills/` / `agents/` — the `explorer-rag-cli` skill/subagent gets a "start `jrag watch` once per session for fast + fresh queries" tip. These ship verbatim; the repo is the source of truth.

## 8. Non-goals

- Not a boot/persistent service — no launchd/systemd/autostart; explicit `jrag watch` only.
- Not multi-project per process — one daemon per resolved index dir.
- Not network/remote serving — Unix socket, local only; no TCP, no auth.
- Not a replacement for the cold path — every read command still works without the daemon.
- Not new search semantics — identical backends (`search_v2`, `LadybugGraph`); only transport differs.
- Not for the MCP surface — it is already long-running and warm; watch targets the `jrag` CLI.

## 9. Verified during planning

1. **LadybugDB transactions — not available.** The `ladybug` package (kùzu wrapper) exposes no `begin`/`commit`/`rollback`; every `conn.execute` autocommits, and a `Database` is single-writer. Atomic graph writes are impossible at the DB level, so the design uses a file-level COW snapshot around each graph reindex (§4.7). Process crashes mid-write remain covered by the existing `.graph_increment_in_progress` crash marker + full-rebuild fallback.
2. **LanceDB versioned reads — available and sufficient.** Lance commits are atomic per version; a fresh `lancedb.connect` per query returns a consistent old-or-new view during a cocoindex write, so no version-pinning/`checkout` is needed for the normal path. (`table.checkout(v)` exists as an escape hatch but mutates shared table-object state, so it is not used by default.)

## 10. Known limitations & follow-ups

1. **CI skips the load-bearing tests.** `.github/workflows/test.yml` sets `JAVA_CODEBASE_RAG_RUN_HEAVY=0`, so the heavy watch tests are skipped in CI — they run only locally with `JAVA_CODEBASE_RAG_RUN_HEAVY=1`. These are the tests that prove the core guarantees: the golden IPC byte-identity suite (now 6: `search` + `find`/`inspect`/`callers`/`callees`/`flow` over the real socket vs cold), the COW snapshot characterization (3), and the warm-reuse + snapshot lifecycle tests (5) — 14 total. The rest of `tests/watch/` (lifecycle, lock, protocol, client reconstruction) is lightweight and runs in CI. **Follow-up:** add a heavy CI job (or nightly) so the byte-identity and COW guarantees are gated, not just locally verified.

2. **Client-side graph load may transiently error during a graph reindex.** The `jrag` read handlers call `_load_graph_or_error` (client-side, opens the ORIGINAL graph) before `get_payload` dispatches to the daemon. During a graph reindex the daemon serves reads from the sidecar copy, but the client-side open of the original races the subprocess writer; if kùzu refuses a read-only open mid-write, the command surfaces a transient `rc=2` for the ~seconds of the reindex. This is never partial data — the served result always comes from the daemon's sidecar, and a stale-graph error is the honest signal. V1-acceptable. **Follow-up:** when `is_daemon_alive`, skip the client-side graph load and let the daemon's `ERR_STALE_INDEX` drive the error path (the client-side load is only needed for the cold path's own pre-`get_payload` work, e.g. auto-scope defaults).

## TL;DR

`jrag watch` runs one warm process per project that watches files → runs the cheapest incremental reindex on change (reusing existing change detection), and serves every `jrag` read command over a Unix socket using a pre-loaded embedding model and held-open Lance/Ladybug connections — eliminating the per-call cold start. Reads never block and see old-or-new snapshots, never partial (Lance versioning + graph transactions, with a sub-millisecond-commit RWMutex fallback). A pidfile/`flock` adds the mutual exclusion the codebase currently lacks. With no daemon running, every command behaves exactly as it does today.
