# CLI-PROGRESS-OUTPUT — make `java-codebase-rag` lifecycle commands tell the user what they're doing

**Status**: draft
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-11

## TL;DR

- Today, `java-codebase-rag init` / `increment` / `reprocess` on a large Java estate sit silent for **tens of seconds to minutes** while two long subprocesses (`cocoindex update` then `build_ast_graph.py`) run. The user has no way to tell whether the tool is alive, stuck, or just slow — a real failure mode on Sberbank-scale codebases.
- **Root cause is structural, not cosmetic.** `server.py:run_refresh_pipeline` spawns both subprocesses with `stdout=PIPE, stderr=PIPE` and calls `proc.communicate()`, so every progress line the pipeline already prints (`[pass1] parsed N files in X.XXs …`, the cocoindex update output, `[write] kuzu at …`) is **buffered to the end** and only flushed after the whole pipeline finishes. The pipeline isn't quiet — its voice is being held until after it stops talking.
- **This propose ships only the minimal mode (Mode 1).** Phase 2 — TTY-pretty rendering with `rich` / progress bars / colors — is deferred to a separate later propose (`CLI-PRETTY-OUTPUT-PROPOSE.md`) and explicitly out of scope here. The split is intentional: minimal mode fixes 80% of the perceived "is it stuck?" problem at 20% of the work and zero new dependencies.
- **What ships in Mode 1**: (a) **stream** both subprocesses' stderr live to the user instead of buffering, (b) **wrap** cocoindex with one-line announcements (`[lance] running cocoindex update…` / `[lance] done in X.XXs`), (c) **heartbeat** lines every ~5 s during long passes in `build_ast_graph.py`, (d) per-pass start lines (today's pipeline only prints per-pass *end* lines — opening "now starting pass 2" lines close the silent-gap perception), (e) a one-line overall **pipeline header** and **footer** in the CLI driver.
- **Scope hard-cap: lifecycle commands only** (`init`, `increment`, `reprocess`, `erase`). `meta`, `tables`, `diagnose-ignore`, `analyze-pr` stay byte-for-byte identical in this round.
- **Backwards-compatibility invariant**: machine-readable stdout for all existing commands stays byte-for-byte identical. Only **stderr** changes. Existing `--quiet` / `quiet=True` paths must continue to suppress every new heartbeat / wrap / header line we add.
- **No new runtime dependencies.** `rich` / `tqdm` / `click` deliberately deferred to Phase 2.
- **Migration shape**: **3 PRs** — propose merge → stream + wrap (the structural fix) → heartbeats + start lines + pipeline header/footer (the cosmetic-but-real fix). Tests focus on the streaming invariant + `--quiet` parity; no progress-bar UI tests in this round.

---

## §1 — Frame: what is this propose, really?

The `java-codebase-rag` CLI is the operator's lifecycle interface to the index ([`CLI-SCENARIOS-PROPOSE.md`](completed/CLI-SCENARIOS-PROPOSE.md) §1). Its current output behaviour breaks two implicit promises of any lifecycle CLI:

1. **"I'm alive."** A long-running command must keep emitting *something* often enough that the user does not start guessing.
2. **"I tell the truth about what's happening."** When the work involves multiple phases, the user should be able to tell which phase is running right now, not only which phase finished N minutes ago.

The frame: **the lifecycle commands already produce honest progress text; the CLI just hides it until the work is done.** Once you accept that framing, two design corollaries follow:

- The first PR is **structural** — stop buffering subprocess output. Everything else is downstream of that.
- The second PR is **cosmetic-but-real** — fill in the missing "now starting pass 2" lines and add heartbeats inside passes that already exist. No new pipeline structure; we surface what's already there.

This frame rules out:

- Adding `rich`, `tqdm`, ANSI redraws, or any TTY-pretty rendering in this round. Those belong in a follow-up `CLI-PRETTY-OUTPUT-PROPOSE.md` once we have a stable event stream to render.
- Capturing and reformatting cocoindex output. We don't own cocoindex's text; we wrap it.
- Changing `meta` / `tables` / `diagnose-ignore` / `analyze-pr`. They're fast and out of scope.
- Adding a `--format=plain|pretty|json` flag in this round. The plain format **is** the format until Phase 2.
- Changing machine-readable stdout payloads. Stdout is the agent / CI contract; stderr is the human channel; this propose touches stderr only.

## §2 — Design principles

1. **Stream first, beautify never (in this round).** Live unbuffered stderr is worth more than any progress bar; bars are deferred to Phase 2.
2. **stdout is the agent contract; stderr is the human channel.** Every new line we emit lands on stderr. Stdout payloads for `meta` / `tables` / `analyze-pr` remain byte-for-byte identical.
3. **No new runtime dependencies.** No `rich`, no `tqdm`, no `click`. Pure stdlib `time` / `sys` / `threading`.
4. **Honest about partial knowledge.** When a pass cannot announce a percentage (e.g. cocoindex internals are opaque to us), we say "running…" with elapsed time, not a fake bar. Mirrors the "partial fidelity is loud" principle from CLI-SCENARIOS §2.
5. **`--quiet` is sacred.** The existing `--quiet` flag (which sets `quiet=True` and drops `--verbose` from the graph builder) must continue to suppress *every* new line this propose adds, including the pipeline header / footer and heartbeats. CI consumers depend on it.
6. **Cardinal-number discipline.** This propose locks **5 user-visible improvements** (stream, cocoindex wrap, heartbeats, pass-start lines, pipeline header/footer) across **3 PRs**. Adding a 6th improvement in this round requires a propose amendment, not a drive-by. Mirrors [`propose/completed/CLI-SCENARIOS-PROPOSE.md`](completed/CLI-SCENARIOS-PROPOSE.md) §6.
7. **Heartbeat cadence is fixed at ~5 s.** Not adjustable in this round (one knob, one default; matches CLI-SCENARIOS "one source of truth per config knob" principle). A future propose may make it configurable if a real consumer needs it.
8. **No structural change to the pipeline.** We surface existing phases; we do not split, merge, reorder, or rename passes in `build_ast_graph.py`.

## §3 — The proposed surface

### 3.1 The five improvements, in implementation order

#### Improvement 1 — Stream both subprocesses' stderr live (PR-PROG-2, structural)

**File**: `server.py:run_refresh_pipeline`

**What changes**: replace `stdout=PIPE, stderr=PIPE` + `proc.communicate()` with **stdout captured to memory (for the machine-readable summary), stderr inherited or relayed line-by-line to the parent process's stderr in real time**. Both subprocesses get the same treatment.

**Why this is improvement 1**: every other improvement depends on it. Today's per-pass `[pass1] parsed N files in X.XXs` line is *already printed* — it just lands in `graph_err` instead of the terminal. Streaming makes the existing voice audible immediately.

**Quiet-mode behaviour**: when `quiet=True`, stderr is captured (today's behaviour) and not relayed. CI / agent consumers see no change.

**Out of scope**: parsing or rewriting subprocess output. We pass it through verbatim.

#### Improvement 2 — Cocoindex wrap-around announcements (PR-PROG-2, in the same PR)

**File**: `server.py:run_refresh_pipeline`, immediately around the cocoindex `create_subprocess_exec` call.

**What changes**: emit two CLI-driver-owned lines on stderr:

- Before cocoindex starts: `[lance] running cocoindex update (project_root=<root>)`
- After cocoindex exits (success or failure): `[lance] cocoindex update finished in <X.XX>s (exit=<code>)`

**Why**: cocoindex's own output is opaque (and shape varies across releases). We don't pretend to know its progress; we honestly bracket "we entered cocoindex" and "we left cocoindex" with elapsed time. The user sees the bracket even on a fresh-install run where cocoindex itself prints little.

**Quiet-mode behaviour**: suppressed.

**Out of scope**: parsing cocoindex's output; reformatting it; injecting a progress bar over it.

#### Improvement 3 — Heartbeats inside long passes (PR-PROG-3, cosmetic-real)

**File**: `build_ast_graph.py`, inside the four passes (`pass1` parse, `pass2` emit, `pass3` enrich, `pass4` cross-service) and the `write` block.

**What changes**: each pass runs a tiny background thread (or `asyncio.create_task`, whichever matches the call site) that prints `[passN] running … <elapsed>s elapsed` every ~5 seconds while the pass is in progress. Thread is cancelled when the pass completes. Heartbeat carries **no percentage** — pass-internal granularity (file counts, edge counts) is delegated to Phase 2.

**Cadence**: fixed 5 s, locked in §7 decision #6.

**Quiet-mode behaviour**: suppressed (`--verbose` path only).

**Out of scope**: per-file or per-row progress; ETA estimation; ANSI redraw-in-place.

#### Improvement 4 — Pass-start announcement lines (PR-PROG-3)

**File**: same — `build_ast_graph.py`.

**What changes**: every pass that currently prints a `[passN] done in X.XXs …` line at the end gains a paired `[passN] starting …` line at the beginning, with a one-phrase description of what the pass does.

Today's behaviour: silent for the full duration of the pass, then one summary line at the end.
Proposed: one line at the start, heartbeats every 5 s, summary line at the end.

**Quiet-mode behaviour**: suppressed (`--verbose` path only).

**Out of scope**: changing the existing summary line wording (we only add the start line; the summary line stays exactly as it is for grep parity).

#### Improvement 5 — Pipeline header / footer in the CLI driver (PR-PROG-3)

**File**: `java_codebase_rag/cli.py`, in the `init` / `increment` / `reprocess` command handlers.

**What changes**: the CLI driver itself emits a single-line header before the first subprocess and a single-line footer after the last subprocess:

- Header: `java-codebase-rag <subcommand> · source=<root> · index=<index-dir>`
- Footer: `java-codebase-rag <subcommand> · finished in <X.XX>s (exit=<code>)`

These belong in the CLI driver (not in `server.py`), so they bracket the *whole* command including any pre/post work the subprocesses don't see.

**Quiet-mode behaviour**: suppressed.

**Out of scope**: any colored / ANSI / box-drawing visuals; bar charts; counts.

### 3.2 What the user sees, before vs after

**Before (today, `java-codebase-rag init` on a fresh repo, ~3 min wall time):**

```
$ java-codebase-rag init
                                                  ← silent for ~3 minutes ←
[pass1] parsed 4523 files in 47.12s: …
[pass2] emitted 18432 EXTENDS, … in 21.84s
…
[write] kuzu at .java-codebase-rag/kuzu
```

(All five lines arrive in a single burst at the very end. Cocoindex's own output, if any, is also held until then.)

**After (post-PR-PROG-2 and PR-PROG-3, same command):**

```
$ java-codebase-rag init
java-codebase-rag init · source=/home/dmitry/sberbank-estate · index=.java-codebase-rag
[lance] running cocoindex update (project_root=/home/dmitry/sberbank-estate)
…cocoindex's own output, streamed live…
[lance] cocoindex update finished in 87.43s (exit=0)
[pass1] starting · parsing Java files under source root
[pass1] running … 5s elapsed
[pass1] running … 10s elapsed
…
[pass1] parsed 4523 files in 47.12s: …
[pass2] starting · emitting EXTENDS / IMPLEMENTS / DECLARES rows
[pass2] running … 5s elapsed
…
[pass4] cross-service edges emitted in 12.41s
[write] kuzu at .java-codebase-rag/kuzu
java-codebase-rag init · finished in 187.43s (exit=0)
```

The user now sees something happening at most ~5 s apart for the entire duration. The existing summary lines are preserved verbatim (grep parity).

### 3.3 What `--quiet` looks like

`java-codebase-rag init --quiet` produces **no stderr output** except errors (today's behaviour, preserved). stdout (the machine-readable summary the CLI driver prints at exit) is byte-for-byte identical to today's output. CI logs and agent-sandbox runs see no change in line count or content.

### 3.4 Stdout invariant (locked)

For each of `init` / `increment` / `reprocess` / `erase`, the **stdout payload** at command exit is byte-for-byte identical to today's payload. This is the agent / CI contract and is the strongest invariant in this propose. PR-PROG-3 ships a small test (`tests/test_cli_progress_stdout_invariant.py`) that runs each command against a tiny fixture and diffs the captured stdout against a recorded baseline.

## §4 — Use-case re-walk

Walking 16 realistic invocations through the proposed surface. Each row records the **mode** (interactive vs CI / agent), the **observable change** post-PR-PROG-3, and whether the **stdout invariant** holds.

| # | Invocation | Mode | Observable change | Stdout invariant |
|---|---|---|---|---|
| UC1 | `java-codebase-rag init` on 4500-file estate | Interactive | 5 s max silence; pipeline header + cocoindex wrap + per-pass start/heartbeat/summary + footer | Identical |
| UC2 | `java-codebase-rag init` on a 50-file toy repo | Interactive | Same lines, but most heartbeats never fire (passes finish in <5 s). Header / footer / start / summary still print. | Identical |
| UC3 | `java-codebase-rag init --quiet` | Interactive | No stderr output (today's behaviour) | Identical |
| UC4 | `java-codebase-rag reprocess` in CI (non-TTY, output redirected) | CI / agent | Stderr lines now appear in the CI log in real time instead of one final burst — fine for line-oriented CI consumers; no ANSI escapes | Identical |
| UC5 | `java-codebase-rag increment` (small Lance delta, full graph rebuild) | Interactive | Cocoindex wrap shows quick exit (e.g. 2 s); graph rebuild still gets heartbeats. User can tell which side is slow. | Identical |
| UC6 | `java-codebase-rag erase --yes` | Interactive | Pipeline header + footer; no cocoindex / pass lines (no subprocess work) | Identical |
| UC7 | Cursor agent runs `init` in a sandbox shell | CI / agent | Sees lines streamed instead of a single burst; agent can detect progress / hang on its own | Identical |
| UC8 | User pipes output to a file: `java-codebase-rag init 2> log.txt` | CI / agent | Log file fills as the command runs (was: log file appears empty until exit, then fills) | Identical |
| UC9 | User runs `init` in a screen / tmux pane and detaches | Interactive | Heartbeats keep landing every 5 s; pass-start / summary lines bracket each phase | Identical |
| UC10 | `init` fails because cocoindex hits an error mid-run | Interactive | Cocoindex's own error text streams live instead of being held; wrap-around `[lance] … finished in 12.4s (exit=1)` makes the failure stage clear | Identical (today's failure stdout also identical) |
| UC11 | `init` fails inside `build_ast_graph.py` pass3 | Interactive | `[pass3] starting …` then `[pass3] running … 5s elapsed` × N then traceback — user can see which pass crashed without reading the trailing summary section | Identical |
| UC12 | Agent calls `init` via subprocess.run and reads stderr post hoc | CI / agent | Stderr now contains pipeline header / footer and pass-start lines too. Existing consumers that grep for `[passN]` continue to match (summary lines unchanged). | Identical |
| UC13 | `java-codebase-rag reprocess` on a totally fresh repo (no cocoindex state) | Interactive | Same as UC1; cocoindex's first-run output (which can be quiet for ~30 s on cold cache) is bracketed by the wrap and softened by the graph-side heartbeats kicking in once it exits | Identical |
| UC14 | `init` from a CI shell that strips ANSI / colors aggressively | CI / agent | No ANSI escapes are ever emitted (Mode 1 is plain-text only). Nothing to strip. | Identical |
| UC15 | User runs `reprocess` with `JAVA_CODEBASE_RAG_INDEX_DIR=...` to a network-mounted disk; one pass is unusually slow | Interactive | The 5 s heartbeat cadence makes the slow pass obvious: `[pass2] running … 35s elapsed` is visibly different from a normal `[pass2] running … 5s elapsed` then summary. | Identical |
| UC16 | User reads `--help` and discovers the new behaviour | Interactive | `--help` text gains one sentence noting that progress is streamed to stderr and `--quiet` suppresses it. (Doc-only change, in PR-PROG-3.) | Identical |

**Result of the re-walk:**

- 16 of 16 invocations: stdout invariant holds.
- 16 of 16: observable stderr change is improvement, never regression.
- 0 of 16: requires a 6th improvement, an ANSI / pretty rendering, or a percentage-bar.
- UC4 / UC8 / UC12 explicitly exercise the **non-TTY / agent / log-file** consumer to validate that no ANSI / redraw / TTY-only construct sneaks in.

No surface revisions triggered.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
| ------------------ | -------------- |
| Add `rich` / `tqdm` / `click` | Deferred to Phase 2 (`CLI-PRETTY-OUTPUT-PROPOSE.md`). Mode 1 is dep-free by frame. |
| Add ANSI colors / redraw-in-place bars | Phase 2. Mode 1 is plain-text. |
| Add a `--format=plain\|pretty\|json` flag | Phase 2. Mode 1 has one format. |
| Parse / reformat cocoindex output | Out of scope by §2 principle 4. We wrap; we don't reformat. |
| Per-file or per-row progress inside a pass | Out of scope. Requires threading a callback through `build_ast_graph.py`, which is Phase 2 territory. |
| ETA estimation | Out of scope. Requires per-row progress. Phase 2. |
| Beautify `meta` / `tables` / `analyze-pr` output | Out of scope (lifecycle commands only). Future propose if a real consumer needs it. |
| Change cardinal numbers in CLI-SCENARIOS | Out of scope. `init` / `increment` / `reprocess` / `erase` are unchanged in count, semantics, and exit codes. |
| Add a configurable heartbeat cadence (`--heartbeat=10s`) | Out of scope. One knob, one default (5 s) by §2 principle 7. Future propose if a real consumer needs configurability. |
| Translate stderr text to Russian | Out of scope. CLI is English-only by existing convention. |
| Restructure passes in `build_ast_graph.py` | Out of scope by §2 principle 8. We surface; we do not restructure. |

## §6 — Migration plan — 3 PRs

### PR-PROG-1 — propose merge

**Title**: `propose: CLI progress output (Phase 1 — stream + heartbeats)`
**Purpose**: this document. Lock the 5 improvements and the deferral of Phase 2.
**Tests**: none (doc-only).

### PR-PROG-2 — stream subprocess stderr + cocoindex wrap

**Title**: `feat(cli): stream subprocess stderr live; wrap cocoindex with announcements`
**Purpose**: structural fix. `server.py:run_refresh_pipeline` no longer buffers subprocess stderr until completion. Cocoindex gets the wrap-around `[lance] running…` / `[lance] finished in …` lines.
**Tests**:
- Unit test for the streaming relay (asyncio task reads from a fake pipe and writes to a captured sink in real time, not after `.wait()`).
- `--quiet` parity test: stderr is empty when `quiet=True`, identical to today.
- Stdout invariant test: `init` against the fixture repo produces a stdout byte-string identical to a recorded baseline.

### PR-PROG-3 — heartbeats + pass-start lines + pipeline header/footer

**Title**: `feat(cli): pass-start lines, 5s heartbeats, pipeline header/footer`
**Purpose**: cosmetic-but-real fix. Adds the four remaining improvements in one PR.
**Tests**:
- Heartbeat fires at least once when a fixture pass is artificially slowed to >5 s; does not fire on a fast pass.
- Pass-start line is emitted before any pass-internal output.
- Pipeline header / footer wrap the whole command.
- `--quiet` parity: every new line type is suppressed.
- Stdout invariant test (regression on the PR-PROG-2 test).
- Docs: README + AGENT-GUIDE.md gain a one-sentence note that lifecycle commands stream progress on stderr.

Total: 3 PRs.

## §7 — Decisions taken (no longer open)

1. **Two-phase split is locked.** This propose ships only Mode 1 (plain stderr, no deps). Mode 2 (pretty / `rich` / bars / colors) is a separate later propose. No flag now; no opt-in for pretty rendering in this round.
2. **Scope is lifecycle commands only.** `init`, `increment`, `reprocess`, `erase`. `meta`, `tables`, `diagnose-ignore`, `analyze-pr` stay byte-for-byte identical.
3. **Cocoindex handling is wrap-only.** We do not parse, capture, or reformat cocoindex output. The wrap is two CLI-driver-owned stderr lines around the subprocess.
4. **Stdout is the agent contract; this propose touches only stderr.** Stdout payloads for all commands are byte-for-byte identical post-implementation. Tested.
5. **No new runtime dependencies.** Pure stdlib. `rich` / `tqdm` / `click` deferred to Phase 2.
6. **Heartbeat cadence locked at 5 s.** Not configurable in this round.
7. **`--quiet` suppresses every new line.** No new line type bypasses the quiet path. CI / agent consumers see no behavioural change in `--quiet` mode.
8. **Five improvements, three PRs, locked.** Adding a 6th improvement in this round requires an amendment to this propose.
9. **Per-pass start lines are net-new; summary lines preserved verbatim.** Grep parity invariant: any consumer that today greps for `[passN] parsed` / `[passN] emitted` / etc. continues to match.
10. **No ANSI escapes, no TTY detection, no redraw-in-place.** Mode 1 is plain-text in all environments. TTY-aware rendering is Phase 2.
11. **No structural change to the pipeline.** Passes are surfaced, not restructured.
12. **English-only.** No i18n.

## §8 — Risks and how we mitigate

| Risk | Mitigation |
| ---- | ---------- |
| Streaming subprocess stderr changes the asyncio control flow in `run_refresh_pipeline` and breaks the MCP `refresh` path (still used by tests / fallbacks) | PR-PROG-2 keeps `--quiet` / `quiet=True` semantically identical: captured stderr, not relayed. MCP callers (which pass `quiet=True`) see byte-for-byte identical behaviour. New streaming code path is exercised only when `quiet=False`. |
| Heartbeat thread leaks if a pass crashes mid-execution | Heartbeat helper is a context manager (`with heartbeat("pass1"): …`) that cancels its background thread / task in `__exit__`, including on exception. Unit test covers the exception path. |
| Heartbeat thread interleaves with the main thread's prints, corrupting line atomicity | All heartbeat writes use `print(..., file=sys.stderr, flush=True)` with a module-level `threading.Lock` shared between heartbeat and pass-end writers. Lock scope is the single `print` call. |
| 5 s cadence wrong for some environments (too noisy / too quiet) | Cadence is locked for this round (§7 decision #6). If a real consumer reports a problem, a future propose can introduce a configurable cadence. Two-PR cost to defer is small. |
| User confused by new lines breaking their muscle memory for the old summary-only output | Existing summary lines are preserved verbatim (§7 decision #9). Anyone grepping `[passN] parsed` keeps working. README + AGENT-GUIDE.md get a one-sentence note in PR-PROG-3. |
| `--quiet` parity bug: one of the new line types slips through quiet mode | Dedicated unit test in PR-PROG-3 (`test_cli_quiet_parity.py`) runs every lifecycle command with `--quiet` against a fixture and asserts captured stderr is empty (or matches today's empty baseline). |
| Stdout invariant test breaks because timing / wall-clock leaks into stdout | Baseline test redirects only stderr for capture; stdout is asserted against a string baseline that includes no timestamps. If a timestamp accidentally lands on stdout, the test fails — by design. |
| Non-TTY environments (CI / agent sandboxes) get noisy because we strip nothing | The new lines are line-oriented, no ANSI, no redraws. Line-oriented CI logs are the *target* shape, not an accident. Verified by UC4 / UC8 / UC12. |
| cocoindex prints a huge amount of output and floods the user terminal | Out of scope — we pass cocoindex output through verbatim by §2 principle 4. If this becomes a real problem, a future propose may add a `--lance-quiet` flag. |
| Phase 2 lands and the event format changes, breaking consumers who started parsing the new stderr | Stderr is the **human** channel by §2 principle 2. Consumers that parse it do so at their own risk; the agent / CI contract is stdout, which is invariant. PR-PROG-3 README note states this explicitly. |
| `build_ast_graph.py` has 4 passes today but a future propose adds pass5 | Heartbeat / start-line / summary-line scaffolding is per-pass and additive. New passes use the same helper. No structural coupling. |

## Appendix A — Output spec (verbatim, for the implementation PR)

The shipped lines, exactly. Anchored here so PR-PROG-2 / PR-PROG-3 can be reviewed against this single source.

```
java-codebase-rag <subcommand> · source=<source-root> · index=<index-dir>
[lance] running cocoindex update (project_root=<source-root>)
…cocoindex's own output, unmodified, streamed live…
[lance] cocoindex update finished in <X.XX>s (exit=<code>)
[passN] starting · <one-phrase description>
[passN] running … <elapsed>s elapsed       (every ~5 s)
[passN] <today's summary line verbatim>      (unchanged from current `build_ast_graph.py` output)
[write] kuzu at <kuzu-path>
java-codebase-rag <subcommand> · finished in <X.XX>s (exit=<code>)
```

Rules:
- Every line goes to **stderr**.
- Every line is suppressed when `--quiet`.
- `<elapsed>` is integer seconds (no decimals on heartbeats); pipeline header / footer use `<X.XX>s` (two decimals).
- The pipeline-header / footer lines use the U+00B7 middle dot (`·`) as a separator. No other special characters.
- No ANSI escapes anywhere.
- No trailing whitespace, no leading whitespace.

## Appendix B — Per-pass start-line wording (proposed)

For grep stability, these strings are committed here:

| Pass | Start line |
| ---- | ---------- |
| `pass1` | `[pass1] starting · parsing Java files under source root` |
| `pass2` | `[pass2] starting · emitting EXTENDS / IMPLEMENTS / DECLARES rows` |
| `pass3` | `[pass3] starting · enrichment (role / capability / framework annotations)` |
| `pass4` | `[pass4] starting · cross-service edges (HTTP_CALLS / ASYNC_CALLS)` |
| `write` | `[write] starting · writing Kuzu graph to disk` |

If a pass's actual work materially changes in a future PR, the wording is updated in lockstep with that PR (mirrors the AGENT-GUIDE.md maintenance invariant).
