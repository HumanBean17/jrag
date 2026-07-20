> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# YAML-PATH-EXPANSION — Apply `~` and `$VAR` expansion to `embedding.model` in project YAML

**Status**: completed — shipped (`maybe_expand_embedding_model_path` in `java_codebase_rag/config.py`; tests on `master`).
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-12 (v2)

## TL;DR

- **The call**: when `embedding.model` is resolved (from CLI, env, or YAML), apply `os.path.expanduser` and `os.path.expandvars` to the picked value *only* when it looks like a local filesystem path (heuristic below) — never to HuggingFace hub ids. The expansion lives at the resolution layer (post-`_pick_str`), so all three resolution paths agree.
- **Why**: today `embedding.model` is **inconsistent** across consumers, not uniformly broken. `index_common.py` and `java_index_v1_common.py` apply `expanduser` + `expandvars` to `os.environ.get("SBERT_MODEL", ...)` at module-import time, so the cocoindex subprocess that performs indexing already works for `~/models/...` set via env, and even works for YAML `~/...` *after* `apply_to_os_environ()` propagates the literal into `SBERT_MODEL` (because the child re-imports and re-expands). But `resolve_operator_config().embedding_model` itself stays literal, so `java-codebase-rag meta` output, MCP search (`mcp_v2.py:382`, `server.py:177` which read the env directly without re-expanding), and JSON payloads (`cli.py:266`) all show `~/models/x` literally and may pass it to sentence-transformers unexpanded. The fix unifies the contract: one expansion point, one canonical value visible everywhere.
- **Separate gap, noted in §5 as out of scope**: `index_dir`-from-YAML expands `~` but not `$VAR`.
- **Scope**: surgical. Only `embedding.model`. Other YAML string fields (`role_overrides`, `route_overrides`, `microservice_roots`, `http_client_overrides`, `async_producer_overrides`, `cross_service_resolution`) are **not** filesystem paths and stay unchanged.
- **Migration**: 1 PR. No deprecations. No behaviour change for absolute paths or hub ids. Tilde/`$VAR` cases that previously broke now work.
- **Risk**: trivial. ~6-line code change in `java_codebase_rag/config.py`, ~3 new precedence tests covering `~`, `$VAR`, and a hub id (which must *not* be expanded — `$VAR` heuristic must not fire on hub ids that happen to contain `$`).

## 1. Frame: what is this thing, really?

> **Project YAML, CLI, and env are three resolution paths to the same knob; the resolved value visible to every downstream consumer must be canonical, regardless of which path fired.**

Today the *value* of `embedding.model` is canonicalised in some places (the indexer subprocess re-imports `index_common` and re-applies `expanduser`/`expandvars` to whatever `SBERT_MODEL` is in its env) and not in others (`resolve_operator_config().embedding_model`, `meta` output, MCP search, JSON payloads). That's two implementations of "is this expanded?" depending on which consumer you ask. The propose collapses them: do the expansion once, at the resolution layer, on the picked value; every consumer sees the same string. `_pick_str` keeps the precedence and `source` contract; the new helper is a normalization that runs after the pick.

## 2. Design principles

1. **Uniform shape across resolution paths.** A string that's valid as `--embedding-model` or `SBERT_MODEL` must be valid in YAML, *and vice versa*. The helper runs once on the value picked by `_pick_str`, so CLI / env / YAML all converge.
2. **One canonical value, visible to every consumer.** `meta` output, MCP search, JSON payloads, child subprocesses, and the indexer all see the same expanded string. No consumer re-implements "is this expanded?".
3. **Expansion is only for path-shaped strings.** Hub ids (`sentence-transformers/all-MiniLM-L6-v2`) must round-trip unchanged. The expansion code path must not touch them.
4. **No silent rewriting of existing values.** Absolute paths, hub ids, and relative paths that don't contain `~` or `$` must produce *byte-identical* `embedding_model` strings before and after this change.
5. **The `source` field stays truthful.** `embedding_model_source = "yaml"` is the right answer whether the YAML value was `~/models/x` or `/abs/models/x` — expansion is post-pick, not a separate source.
6. **Fail loud on `$VAR` that can't be resolved.** If `expandvars` returns a literal `$VAR` or `${VAR}` (i.e. didn't find the variable), emit a one-line stderr hint *and* keep the literal — same shell semantics as bash without `set -u`.
7. **No new YAML keys.** Don't add `embedding.model_path` or a `paths:` section. The fix is in resolution, not in surface.
8. **Hub-id detection is conservative.** When in doubt, do not expand. False negatives (a path that wasn't expanded) produce an obvious error from sentence-transformers; false positives (a hub id that got `~`-mangled) produce confusing failures.

## 3. The proposed surface

### 3.1 Code change (illustrative — exact form belongs in a plan)

In `java_codebase_rag/config.py`, after `_pick_str` returns the picked value for `embedding.model`, apply a focused helper before that value flows into `ResolvedOperatorConfig`. The helper runs on **every** resolution path (CLI / env / YAML) so the canonical-value principle holds.

```python
# pseudocode — actual placement decided in plan
# Matches either $VAR or ${VAR} forms (POSIX shell variable syntax).
_UNRESOLVED_VAR_RE = re.compile(r"\$(\w+|\{[^}]+\})")

def _maybe_expand_model_path(value: str) -> str:
    """Expand `~` and `$VAR` iff value is path-shaped.

    Path-shape heuristic: value starts with '/', './', '../', '~', or contains '$'.
    Plain 'org/name' (hub-id shape) does NOT match and is passed through.
    """
    needs_expand = value.startswith(("/", "./", "../", "~")) or "$" in value
    if not needs_expand:
        return value
    expanded = os.path.expandvars(os.path.expanduser(value))
    # one-line stderr hint if expandvars left any unresolved $VAR or ${VAR}
    if _UNRESOLVED_VAR_RE.search(expanded):
        print(
            f"java-codebase-rag: embedding.model contains unresolved variable: {expanded}",
            file=sys.stderr,
        )
    return expanded
```

Apply *only* in the `embedding.model` resolution path. Do not refactor `_pick_str` to be generic — that's a different (and unneeded) propose. The path-shape heuristic is intentionally *simpler than* the v1 draft (no `os.sep` branch, no `_looks_like_hub_id` predicate): the four leading-prefix checks plus `$` presence are sufficient, and the §3.2 table is the locked contract.

### 3.2 Heuristic boundary

| Input | Treated as | Expanded? |
|---|---|---|
| `sentence-transformers/all-MiniLM-L6-v2` | hub id | no |
| `BAAI/bge-small-en-v1.5` | hub id | no |
| `/abs/models/all-MiniLM-L6-v2` | abs path | no-op (no `~`/`$`) |
| `./models/all-MiniLM-L6-v2` | rel path | no-op |
| `../models/all-MiniLM-L6-v2` | rel path | no-op |
| `~/models/all-MiniLM-L6-v2` | abs-after-expand | **yes** |
| `$HOME/models/all-MiniLM-L6-v2` | abs-after-expand | **yes** |
| `~/models/$MODEL_NAME` | combined | **yes** (both passes) |
| `models/all-MiniLM-L6-v2` (no leading `./`) | ambiguous | no-op (don't guess — operator can prepend `./` to disambiguate from hub id) |

The boundary is deliberately strict on the "ambiguous" case (`models/all-MiniLM-L6-v2`): it shape-matches `org/name` and is rare in practice. If an operator hits this they get a clear sentence-transformers error pointing at the path; the propose for that ambiguity (if anyone ever hits it) is a separate question.

### 3.3 Provenance

The `embedding_model_source` field stays exactly as today — `"yaml"` when the value came from YAML, regardless of whether expansion fired. Add no new `embedding_model_expanded` flag. The expansion is a normalization step, not a separate source.

## 4. Use-case re-walk

16 cases covering both the happy path and every boundary the heuristic touches.

| # | Use case | Pre-expand YAML value | Post-expand result | Source | Notes |
|---|---|---|---|---|---|
| UC1 | Operator uses default | (no YAML) | `sentence-transformers/all-MiniLM-L6-v2` | `default` | unchanged |
| UC2 | Operator sets hub id in YAML | `BAAI/bge-small-en-v1.5` | `BAAI/bge-small-en-v1.5` | `yaml` | not expanded (correct) |
| UC3 | Operator sets absolute path in YAML | `/opt/models/minilm` | `/opt/models/minilm` | `yaml` | no-op (no `~`/`$`) |
| UC4 | Operator sets `~/...` in YAML (today: literal in `meta` + MCP search; works in indexer subprocess only) | `~/models/minilm` | `/Users/dmitry/models/minilm` | `yaml` | **canonicalised by this propose** |
| UC5 | Operator sets `$HOME/...` in YAML (today: same inconsistency as UC4) | `$HOME/models/minilm` | `/Users/dmitry/models/minilm` | `yaml` | **canonicalised by this propose** |
| UC6 | Operator sets `~/$MODEL_DIR` in YAML | `~/$MODEL_DIR` | `/Users/dmitry/minilm-v1` (if `MODEL_DIR=minilm-v1`) | `yaml` | both passes apply |
| UC7 | Operator sets relative path with `./` | `./models/minilm` | `./models/minilm` | `yaml` | no-op; resolution to absolute is sentence-transformers' job |
| UC8 | Operator sets relative path WITHOUT `./` | `models/minilm` | `models/minilm` | `yaml` | no-op (heuristic falls through; ambiguous with hub-id shape) |
| UC9 | Operator sets `$UNDEFINED_FOO/x` in YAML | `$UNDEFINED_FOO/x` | `$UNDEFINED_FOO/x` (literal kept) | `yaml` | stderr hint emitted; sentence-transformers will fail with a clear error |
| UC10 | CLI override beats YAML `~/...` | YAML: `~/yaml-model`, CLI: `--embedding-model /abs/cli` | `/abs/cli` | `cli` | CLI wins; helper runs on `/abs/cli` (no-op, no `~`/`$`) |
| UC10b | CLI passes a quoted `~` (shell didn't expand) | CLI: `--embedding-model "~/models/x"` | `/Users/dmitry/models/x` | `cli` | helper runs on the picked CLI value; canonicalised same as YAML |
| UC11 | Env override beats YAML `~/...` | YAML: `~/yaml-model`, env: `SBERT_MODEL=~/env-model` | `/Users/dmitry/env-model` | `env` | env wins; helper runs on the env value at resolution time — same canonical string everywhere |
| UC12 | YAML `~/...` then `apply_to_os_environ()` | YAML: `~/models/x` | `os.environ["SBERT_MODEL"] = "/Users/dmitry/models/x"` | `yaml` | downstream subprocesses see the expanded value (not `~/...`) — important for child processes that may not run through Python's `expanduser` |
| UC13 | YAML `~/...` then `subprocess_env()` | YAML: `~/models/x` | child env `SBERT_MODEL=/Users/dmitry/models/x` | `yaml` | same as UC12 for `build_ast_graph.py` subprocess |
| UC14 | YAML with leading `/` but `$VAR` inside | `/opt/$MODEL_VERSION/minilm` | `/opt/v3/minilm` (if `MODEL_VERSION=v3`) | `yaml` | abs path + `$VAR` |
| UC15 | YAML with Windows-style backslash path | `~\models\minilm` | depends on Python's `expanduser` on that platform | `yaml` | not officially supported (project is POSIX-first); operator should use forward slashes |

**Gaps found in walk**:

- UC8 ("relative path without `./`") is ambiguous with hub-id shape — explicitly leave unexpanded and document the workaround (prepend `./`).
- UC10b surfaced the CLI-parity question. Decision: the helper runs on the value picked by `_pick_str` regardless of which path won, so quoted CLI (which bypasses shell expansion) gets canonicalised exactly like YAML. Principle 1 ("uniform shape") would be violated if only the YAML branch were patched.
- UC11's revised behaviour: when env wins, the helper runs on the env-picked value. Before this propose, `_pick_str`'s env-picked value flowed into `ResolvedOperatorConfig` literal, even though `index_common`'s module-level `SBERT_MODEL` (a *different* variable in a *different* import) was expanded. The propose collapses those two into one canonical value.
- UC12 / UC13: `apply_to_os_environ()` and `subprocess_env()` already use `self.embedding_model`, so the **already-expanded** value naturally flows to subprocesses. No additional plumbing needed.

## 5. What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Apply expansion to other YAML keys (`role_overrides.fqn` keys, etc.) | None of them are filesystem paths. FQNs, role names, framework names, URL paths, topic names, capability names — all are namespace strings, not paths. |
| Apply expansion to `microservice_roots` entries | They're directory names *relative to source_root*, not arbitrary paths. Allowing `~/foo` here would let one project's YAML reach outside its tree — a footgun, not a feature. |
| Apply expansion to `route_overrides.*.path` / `*.topic` | These are URL paths and Kafka topic names. `~` and `$` are legal characters in both — expansion would corrupt them. |
| Refactor `_pick_str` to be generic over expansion | Adds optional-arg complexity for one caller. YAGNI. |
| Add a new `embedding.model_path` key | Two ways to say the same thing. Reject. |
| Add an `embedding.expand_path: bool` toggle | Inverts the principle. If the value is path-shaped, expand; otherwise, don't. No toggle needed. |
| Tighten the heuristic to detect hub-id vs path with a regex | Heuristic is already conservative. Operator can disambiguate by adding `./`. A regex adds maintenance burden for no observed need. |
| Update `embedding.device` to also expand | Devices are `cpu` / `cuda` / `mps` / `cuda:0`. No paths. Not in scope. |
| Apply to `index_dir` YAML value | Already `expanduser`'d (see `config.py:203`). Adding `expandvars` here would be a small, separate consistency fix — deliberately not folded in so this propose stays surgical and reviewable. Track as a follow-up if anyone hits it. |
| Resolve relative paths to absolute (call `.resolve()`) | sentence-transformers does this itself. Keeping the original string preserves provenance in logs. |

## 6. Migration plan — 1 PR

### PR-YAML-EXPAND-1: canonical `embedding.model` across CLI / env / YAML

- **Purpose**: apply `expanduser` + `expandvars` (with the path-shape heuristic) to the value picked by `_pick_str`, so every consumer of `ResolvedOperatorConfig.embedding_model` sees the same expanded string.
- **Test summary**: 4 new tests in `tests/test_java_codebase_rag_cli.py`:
  - `test_embedding_model_yaml_expands_tilde` — YAML value `~/foo` resolves with `HOME=/h` to `/h/foo`.
  - `test_embedding_model_yaml_expands_envvar` — YAML value `$MY_MODEL/x` with `MY_MODEL=/abs` resolves to `/abs/x`.
  - `test_embedding_model_yaml_hub_id_not_expanded` — YAML value `sentence-transformers/all-MiniLM-L6-v2` round-trips unchanged.
  - `test_embedding_model_cli_quoted_tilde_expanded` — CLI `--embedding-model "~/cli/x"` (shell-quoted, no shell expansion) resolves to `<HOME>/cli/x`; covers UC10b and locks CLI parity.
- Existing 4 precedence tests stay green; no test modifications expected.
- README §2 is updated **in the same PR** so `master` never carries a README that promises behaviour the code doesn't deliver.

## 7. Decisions taken (no longer open)

1. **Scope**: expansion applies only to `embedding.model`. No other YAML key.
2. **Both `expanduser` and `expandvars` apply**, in that order (matches existing module-import-time semantics in `index_common.py` and `java_index_v1_common.py`).
3. **Resolution-layer fix, not branch-specific.** The helper runs on the value picked by `_pick_str` (after CLI / env / YAML / default precedence), so all three non-default sources get the same canonicalisation. **Not** "apply only to the YAML branch."
4. **CLI parity**: `--embedding-model` is covered. Shell-expanded forms (`--embedding-model ~/x`) and quoted forms (`--embedding-model "~/x"`) both produce the same canonical value; the helper is idempotent on already-expanded input.
5. **Hub-id detection**: conservative — only expand if value starts with `/`, `./`, `../`, `~`, or contains `$`. Plain `org/name` is treated as a hub id. The v1 draft's `os.sep`-based branch and `_looks_like_hub_id` predicate are dropped (the leading-prefix + `$` check is sufficient; §3.2 table is the contract).
6. **Unresolved variable hint**: matches both `$VAR` and `${VAR}` forms (regex `\$(\w+|\{[^}]+\})`). Keep literal, emit one-line stderr hint. Do not raise.
7. **`embedding_model_source` value**: unchanged — `"yaml"` / `"env"` / `"cli"` / `"default"` regardless of whether expansion fired.
8. **Provenance flag**: no new `*_expanded: bool` field. Expansion is a normalization, not a source.
9. **`embedding.device`**: out of scope. Device strings are not paths.
10. **`microservice_roots`**: out of scope. Directory names are relative to `source_root` by contract.
11. **Other YAML override blocks** (`role_overrides`, `route_overrides`, `http_client_overrides`, `async_producer_overrides`, `cross_service_resolution`): out of scope. None contain filesystem paths.
12. **Refactoring**: do not generalise `_pick_str`. Add a focused helper called from `resolve_operator_config` on the `embedding.model` knob only.
13. **Windows**: best-effort. Use forward slashes in YAML; backslash behaviour follows Python's stdlib.
14. **No new YAML keys.** No `embedding.model_path`, no `paths:` section, no `expand_path` toggle.
15. **README synced with code in one PR.** README §2 currently describes expansion as if it's live; that text only becomes truthful when the impl ships. Both land in PR-YAML-EXPAND-1, no "planned" callout left in `master`.

## 8. Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Heuristic mis-classifies a hub id as a path and corrupts it | Conservative shape check: only expand on leading `/`, `./`, `../`, `~`, or presence of `$`. Plain `org/name` is left alone. Test UC2 + UC8 explicitly cover this. |
| Operator with a literal `$` in their hub-id path (unlikely but possible) gets unwanted expansion | Acceptable. HuggingFace ids cannot contain `$`. If a local dir contains `$` in its name, operator can use absolute path without `$` or escape the `$` in YAML quoted strings (`embedding.model: '/opt/foo$bar'` — `$bar` will get `expandvars`'d). Documented in README §2.1. |
| `expandvars` leaves an unresolved literal and sentence-transformers gives a confusing error | One-line stderr hint at YAML-load time names the unresolved variable; the operator gets a clear pointer before the model load even starts. |
| `apply_to_os_environ()` / `subprocess_env()` already-set `SBERT_MODEL` propagates the now-expanded value to subprocesses that previously saw the un-expanded YAML value | Intended. Subprocesses (e.g. `build_ast_graph.py`) become more reliable, not less. Documented in PR description. |
| The fix changes nothing for the 95%+ of operators who don't use `~` in YAML | That's the design. Principle #3: byte-identical for non-expanding inputs. |

## Appendix A — Test cases (verbatim, for the implementer)

```python
def test_embedding_model_yaml_expands_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: ~/models/minilm\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert cfg.embedding_model == str(tmp_path / "home" / "models" / "minilm")
    assert cfg.embedding_model_source == "yaml"


def test_embedding_model_yaml_expands_envvar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    monkeypatch.setenv("MY_MODEL_DIR", "/abs/models")
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: $MY_MODEL_DIR/minilm\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert cfg.embedding_model == "/abs/models/minilm"
    assert cfg.embedding_model_source == "yaml"


def test_embedding_model_yaml_hub_id_not_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    (tmp_path / ".java-codebase-rag.yml").write_text(
        "embedding:\n  model: BAAI/bge-small-en-v1.5\n",
        encoding="utf-8",
    )
    cfg = resolve_operator_config(source_root=tmp_path)
    assert cfg.embedding_model == "BAAI/bge-small-en-v1.5"
    assert cfg.embedding_model_source == "yaml"


def test_embedding_model_cli_quoted_tilde_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UC10b: quoted CLI argument bypasses shell expansion; helper canonicalises."""
    monkeypatch.delenv("SBERT_MODEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = resolve_operator_config(
        source_root=tmp_path,
        cli_embedding_model="~/cli/x",  # quoted in shell → arrives literal
    )
    assert cfg.embedding_model == str(tmp_path / "home" / "cli" / "x")
    assert cfg.embedding_model_source == "cli"
```

## Appendix B — What changed (traceability)

### v2 (after PR-87 review)

**Changed in response to reviewer feedback:**

1. **Framing** — TL;DR + §1 Frame rewritten from "YAML silently fails on `~/`" to "`embedding.model` is **inconsistent** across consumers." The indexer subprocess actually does expand (via `index_common`/`java_index_v1_common` import-time `expanduser`/`expandvars`). The real bug is that `meta` output, MCP search, and JSON payloads see literal while the indexer sees expanded. The fix is a single canonical value visible everywhere, not a YAML-only patch.
2. **Scope of fix expanded from YAML branch to resolution layer.** Decision #3 added: helper runs on the value picked by `_pick_str` regardless of source. Closes the CLI-parity question (decision #4) without inventing a new code path.
3. **Pseudocode in §3.1 simplified.** Dropped the v1 draft's `os.sep` branch and `_looks_like_hub_id` predicate; the §3.2 table is the locked contract and the leading-prefix + `$` check is sufficient. Decision #5 documents the drop.
4. **Unresolved-variable regex widened.** Now `\$(\w+|\{[^}]+\})` to cover `${VAR}` form, not just `$VAR`. Decision #6 + principle 6 updated.
5. **README sync locked.** Decision #15 added: README §2 and code ship in the same PR so `master` never carries a README that promises behaviour the code doesn't deliver.
6. **Use cases**: UC4 / UC5 notes rewritten from "today: broken" to "today: literal in `meta` + MCP search; works in indexer subprocess only." UC10b added for shell-quoted CLI input. UC11 rewritten with revised semantics.
7. **Tests**: 4 enumerated (was 3). Added `test_embedding_model_cli_quoted_tilde_expanded`.

**Unchanged:**

- Value set (path-shape heuristic table in §3.2: leading `/`, `./`, `../`, `~`, or `$` triggers expansion).
- `embedding_model_source` provenance contract.
- All §5 "NOT do" items still locked.
- Migration shape: 1 PR.

### v1

First draft.
