"""Unified operator config: index paths, embedding knobs, YAML (PR-CLI-2).

Precedence for shared knobs: CLI > env > YAML > built-in default.
Legacy env names and legacy YAML filenames are never read for behaviour;
optional one-line stderr hints may fire when deprecated names are detected.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SettingSource = Literal["cli", "env", "yaml", "default"]

YAML_CONFIG_FILENAMES = (".java-codebase-rag.yml", ".java-codebase-rag.yaml")
LEGACY_YAML_FILENAMES = (".lancedb-mcp.yml", ".lancedb-mcp.yaml")

# Pointer file written into the index dir at index time so discovery can locate
# a YAML that does not sit beside the index-dir anchor — e.g. a config living in
# a sibling ``project-context/`` dir when the agent's cwd is inside a
# microservice (a descendant of the index anchor, a sibling of the config).
# Contains one line: the absolute path of the YAML used to build the index. A
# direct YAML at the anchor always wins; the pointer only fires when the anchor
# has no YAML beside it (see ``_effective_config_dir``).
CONFIG_SOURCE_FILENAME = "config_source"
# Operator-owned files inside the index dir that ``erase`` removes. Kept separate
# from ``build_ast_graph.BUILDER_OWNED_INDEX_FILES`` (builder-owned artifacts).
OPERATOR_OWNED_INDEX_FILES = (CONFIG_SOURCE_FILENAME,)

ENV_INDEX_DIR = "JAVA_CODEBASE_RAG_INDEX_DIR"
# Public operator contract is six names: INDEX_DIR, DEBUG_CONTEXT, RUN_HEAVY, SBERT_MODEL, SBERT_DEVICE, HINTS_ENABLED.
# SOURCE_ROOT is still required for MCP / subprocess Java tree resolution (see mcp.json.example); it is not folded into the headline "5".
ENV_SOURCE_ROOT = "JAVA_CODEBASE_RAG_SOURCE_ROOT"
ENV_DEBUG_CONTEXT = "JAVA_CODEBASE_RAG_DEBUG_CONTEXT"
ENV_RUN_HEAVY = "JAVA_CODEBASE_RAG_RUN_HEAVY"

# CocoIndex inflight-component throttle. CocoIndex's default is 1024 inflight
# components (cocoindex/_internal/app.py: ``_ENV_MAX_INFLIGHT_COMPONENTS``),
# which spawns enough concurrent LanceDB merge-inserts to exhaust OS file
# descriptors under default ulimits -> "Too many open files (os error 24)".
# NOTE: this is the REAL env var. An earlier fix (#293) set the non-existent
# ``COCOINDEX_SOURCE_MAX_INFLIGHT_ROWS`` — CocoIndex never reads it, so it was a
# no-op and the EMFILE error recurred (#306).
COCOINDEX_MAX_INFLIGHT_COMPONENTS_ENV = "COCOINDEX_MAX_INFLIGHT_COMPONENTS"
COCOINDEX_DEFAULT_MAX_INFLIGHT_COMPONENTS = "256"

# Lance native DataFusion hash-join memory pool ceiling (FairSpillPool). The
# lance default is ~100 MiB, tuned for query workloads — too small for the
# single big ``merge_insert`` cocoindex emits at the end of a flow component.
# On ``--full-reprocess`` (all rows match the existing table → bulk-update
# path) the hash join builds on a large side and exhausts the pool somewhere
# around 75k-100k chunks: "Resources exhausted: Failed to allocate ... for
# HashJoinInput ... N MiB remain available for the total pool". cocoindex is a
# bare pass-through to lancedb (it never sets a Session/memory_limit), so it
# inherits this default — we raise it here. FairSpillPool is a *reservation
# ceiling*, not a pre-allocation: setting 1 GiB does not reserve 1 GiB upfront,
# it just allows the join to grow before spilling/erroring, so it is safe on
# memory-constrained hosts. An operator can still override via their own
# ``LANCE_MEM_POOL_SIZE`` (subprocess_env copies os.environ, and apply is via
# ``setdefault`` so the operator value wins). Increment is unaffected (tiny
# batch → tiny hash table); only the full-reprocess write path is at risk.
LANCE_MEM_POOL_SIZE_ENV = "LANCE_MEM_POOL_SIZE"
LANCE_DEFAULT_MEM_POOL_SIZE = "1073741824"  # 1 GiB


def cocoindex_subprocess_env_defaults() -> dict[str, str]:
    """Env defaults applied to every CocoIndex subprocess.

    Bounds CocoIndex concurrency (``COCOINDEX_MAX_INFLIGHT_COMPONENTS``; see
    :issue:`306`) and raises the Lance hash-join memory ceiling
    (``LANCE_MEM_POOL_SIZE``) so a large full-reprocess does not exhaust the
    default ~100 MiB pool mid-``merge_insert``.

    Apply with ``env.setdefault(...)`` so a caller-provided (operator) value
    always wins.
    """
    return {
        COCOINDEX_MAX_INFLIGHT_COMPONENTS_ENV: COCOINDEX_DEFAULT_MAX_INFLIGHT_COMPONENTS,
        LANCE_MEM_POOL_SIZE_ENV: LANCE_DEFAULT_MEM_POOL_SIZE,
    }

_DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Matches either $VAR or ${VAR} (POSIX shell variable syntax).
_UNRESOLVED_VAR_RE = re.compile(r"\$(\w+|\{[^}]+\})")


def maybe_expand_embedding_model_path(
    value: str,
    *,
    config_dir: Path | None = None,
    source_root: Path | None = None,
    source: SettingSource | None = None,
) -> str:
    """Expand ``~`` / ``$VAR`` for path-shaped values and resolve relatives to absolute.

    Path-shape: starts with ``/``, ``./``, ``../``, ``~``, or contains ``$``.
    Plain ``org/name`` (hub id) does not match and is passed through unchanged.

    Relative resolution mirrors :func:`_resolve_index_dir_path` so a committed
    config is portable regardless of process CWD:

    * YAML values (``source == "yaml"``) resolve against ``config_dir`` (the
      directory holding ``.java-codebase-rag.yml``).
    * CLI / env values resolve against ``source_root``.

    Only a result that still starts with ``./`` or ``../`` *after* ``~`` /
    ``$VAR`` expansion is re-based — so hub ids (``org/name``), absolute paths,
    ``~/``-expanded paths, and an env var that already yielded an absolute path
    are all left untouched.

    When no base is supplied (the runtime ``SBERT_MODEL`` read via
    :func:`resolved_sbert_model_for_process_env`), relative resolution is
    skipped: the value is returned ``expandvars`` / ``expanduser``-expanded but
    not re-based, matching the prior best-effort behavior. The main resolution
    path (:func:`resolve_operator_config`) supplies a base, so the absolute path
    it stores is what downstream loaders receive.
    """
    needs_expand = value.startswith(("/", "./", "../", "~")) or "$" in value
    if not needs_expand:
        return value
    expanded = os.path.expandvars(os.path.expanduser(value))
    if _UNRESOLVED_VAR_RE.search(expanded):
        print(
            f"java-codebase-rag: path-shaped model string contains unresolved variable: {expanded}",
            file=sys.stderr,
        )
    if expanded.startswith(("./", "../")):
        base = _embedding_model_base(
            source=source, config_dir=config_dir, source_root=source_root
        )
        if base is not None:
            return str((base / expanded).resolve())
    return expanded


def _embedding_model_base(
    *,
    source: SettingSource | None,
    config_dir: Path | None,
    source_root: Path | None,
) -> Path | None:
    """Base directory for a relative ``embedding.model``.

    Mirrors :func:`_resolve_index_dir_path`: YAML values anchor on the config
    file's directory; CLI / env values anchor on the resolved ``source_root``.
    """
    if source == "yaml":
        return config_dir
    return source_root


def resolved_sbert_model_for_process_env(import_time_default: str) -> str:
    """``SBERT_MODEL`` from the process environment, with the same expansion as YAML/CLI resolution.

    *import_time_default* is typically ``index_common.SBERT_MODEL`` (expanded at import
    when ``SBERT_MODEL`` was unset); when the env var is set or non-empty, that value wins
    and is normalized with :func:`maybe_expand_embedding_model_path`.
    """
    raw = os.environ.get("SBERT_MODEL")
    picked = import_time_default if (raw is None or not str(raw).strip()) else str(raw).strip()
    return maybe_expand_embedding_model_path(picked)


# Legacy env keys: never honored; detection-only hints name the replacement (if any).
_LEGACY_ENV_HINTS: tuple[tuple[str, str], ...] = (
    ("LANCEDB_URI", "JAVA_CODEBASE_RAG_INDEX_DIR"),
    ("LANCEDB_MCP_PROJECT_ROOT", "cwd or --source-root (no env replacement)"),
    ("LANCEDB_MCP_ALLOW_REFRESH", "(removed; use init / increment / reprocess / erase)"),
    ("LANCEDB_MCP_GRAPH_ENABLED", "(removed; graph is used when code_graph.lbug exists)"),
    ("LANCEDB_MCP_MICROSERVICE_ROOTS", "microservice_roots: in .java-codebase-rag.yml"),
    ("LANCEDB_MCP_DEBUG_CONTEXT", ENV_DEBUG_CONTEXT),
    ("LANCEDB_MCP_RUN_HEAVY", ENV_RUN_HEAVY),
    ("COCOINDEX_DB", "defaults to <JAVA_CODEBASE_RAG_INDEX_DIR>/cocoindex.db"),
)

_legacy_hint_seen: set[str] = set()
_legacy_yaml_hint_roots: set[str] = set()


def emit_legacy_env_hints_if_present() -> None:
    """One-line stderr hints when deprecated env vars are set (values are not read)."""
    for old, replacement in _LEGACY_ENV_HINTS:
        if old not in os.environ:
            continue
        key = f"env:{old}"
        if key in _legacy_hint_seen:
            continue
        _legacy_hint_seen.add(key)
        print(
            f"java-codebase-rag: {old} is set but no longer read; use {replacement}.",
            file=sys.stderr,
        )


def emit_legacy_yaml_hint_if_needed(source_root: Path) -> None:
    """If legacy YAML exists without a new config file, print a one-line stderr hint once per root."""
    root_s = str(source_root.resolve())
    if root_s in _legacy_yaml_hint_roots:
        return
    has_new = any((source_root / n).is_file() for n in YAML_CONFIG_FILENAMES)
    if has_new:
        return
    for name in LEGACY_YAML_FILENAMES:
        if (source_root / name).is_file():
            _legacy_yaml_hint_roots.add(root_s)
            print(
                "java-codebase-rag: found legacy "
                f"{name}; rename to .java-codebase-rag.yml to re-enable config.",
                file=sys.stderr,
            )
            return


def find_yaml_config_file(source_root: Path) -> Path | None:
    for name in YAML_CONFIG_FILENAMES:
        p = source_root / name
        if p.is_file():
            return p
    return None


def _has_index_dir(directory: Path) -> bool:
    """True if *directory* contains a non-empty ``.java-codebase-rag/`` index directory."""
    idx = directory / ".java-codebase-rag"
    return idx.is_dir() and any(idx.iterdir())


def discover_project_root(start: Path) -> Path | None:
    """Walk up from start to find the directory containing a config file or index.

    Looks for ``.java-codebase-rag.yml`` / ``.java-codebase-rag.yaml`` (preferred)
    or the ``.java-codebase-rag/`` index directory as a project boundary marker.

    First match wins (closest to start). Config file takes priority over index
    directory at the same level. Stops at $HOME inclusive — checks $HOME itself
    but does not walk past it. Returns None if no marker found.

    A bare ``.java-codebase-rag/`` index directory at ``$HOME`` is intentionally
    NOT treated as an anchor (issue #357): a stray home-level index (e.g. an
    accidental ``init`` run from home) would otherwise hijack resolution for any
    command run from a ``$HOME`` subdir without its own marker, silently reading
    and writing the home-level index. A config file at ``$HOME`` still anchors.
    """
    start = start.resolve()
    home = Path.home().resolve()

    current = start
    while True:
        # Config file is the primary anchor (valid at every level, including $HOME).
        if find_yaml_config_file(current) is not None:
            return current
        # Index directory is the secondary anchor (supports indexes without config),
        # but NOT at $HOME — see the docstring for the cross-project hijack rationale.
        if current != home and _has_index_dir(current):
            return current

        # Stop if we've reached home (config-file check above already handled home)
        if current == home:
            return None

        # Stop if we've reached filesystem root
        parent = current.parent
        if parent == current:
            return None

        current = parent


_stale_pointer_seen: set[str] = set()


def _config_dir_from_pointer(anchor: Path) -> Path | None:
    """Return the YAML config dir recorded in the index dir's ``config_source`` pointer.

    Reads ``<anchor>/.java-codebase-rag/config_source`` (one absolute path). If it
    names an existing ``.java-codebase-rag.yml`` / ``.yaml``, returns that file's
    parent directory; otherwise (missing/blank/stale) returns ``None``. Used only
    when the anchor has no direct YAML — see :func:`_effective_config_dir`.
    """
    pointer = anchor / ".java-codebase-rag" / CONFIG_SOURCE_FILENAME
    if not pointer.is_file():
        return None
    try:
        raw = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        # Relative to the anchor (the index-dir parent), not the pointer file.
        target = (anchor / target).resolve()
    if not target.is_file() or target.name not in YAML_CONFIG_FILENAMES:
        key = str(pointer.resolve())
        if key not in _stale_pointer_seen:
            _stale_pointer_seen.add(key)
            print(
                "java-codebase-rag: ignoring stale index pointer "
                f"{pointer} -> {raw} (target missing or not a config file).",
                file=sys.stderr,
            )
        return None
    return target.parent


def _effective_config_dir(config_dir: Path) -> Path:
    """Resolve the directory YAML config fields are relative to.

    A direct ``.java-codebase-rag.yml`` / ``.yaml`` in ``config_dir`` always wins.
    Otherwise, if ``config_dir`` hosts the ``.java-codebase-rag/`` index dir and
    that index remembers its config via a ``config_source`` pointer, follow it to
    the YAML's directory. This lets a config in a sibling dir (e.g.
    ``project-context/`` beside the Java tree) be found when discovery anchors on
    the index dir from inside a microservice — without an env var or flag, and
    with YAML-relative fields (``index_dir``, ``source_root``, ``embedding.model``)
    resolving against the YAML's home rather than the index anchor. Falls back to
    ``config_dir`` unchanged when neither applies.
    """
    if find_yaml_config_file(config_dir) is not None:
        return config_dir
    return _config_dir_from_pointer(config_dir) or config_dir


def load_yaml_mapping(source_root: Path) -> dict[str, Any]:
    path = find_yaml_config_file(source_root)
    if path is None:
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        # Best-effort loader: a missing/unreadable/malformed config must NOT abort
        # startup — return {} and proceed with defaults. Narrowing this to
        # ``yaml.YAMLError`` alone let OSError (chmod 000, stat/read TOCTOU) and
        # UnicodeDecodeError (non-UTF-8 config) propagate to the caller; the broader
        # tuple restores the graceful-degradation contract while still surfacing the
        # problem on stderr.
        print(
            f"java-codebase-rag: could not load config {path}: {exc}; ignoring config.",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class ResolvedOperatorConfig:
    source_root: Path
    index_dir: Path
    ladybug_path: Path
    cocoindex_db: Path
    embedding_model: str
    embedding_device: str | None
    hints_enabled: bool
    index_dir_source: SettingSource
    embedding_model_source: SettingSource
    embedding_device_source: SettingSource
    hints_enabled_source: SettingSource
    # Absence diagnosis config knobs (PR-ABS-0)
    absence_close_threshold: float
    absence_absent_floor: float
    absence_candidate_count: int
    absence_ngram_q: int
    absence_diag_enabled: bool
    absence_close_threshold_source: SettingSource
    absence_absent_floor_source: SettingSource
    absence_candidate_count_source: SettingSource
    absence_ngram_q_source: SettingSource
    absence_diag_enabled_source: SettingSource
    # Absolute path of the YAML actually loaded (None when built-in defaults were
    # used with no config file). Recorded into the index dir at index time so a
    # later discovery run from a sibling/cwd can relocate this config.
    yaml_config_path: Path | None = None

    def apply_to_os_environ(self) -> None:
        """Make downstream modules (server, ladybug_queries, flows) see a consistent environment.

        When ``embedding_device`` is unset, ``SBERT_DEVICE`` is not removed from ``os.environ`` so
        a long-lived host process is not mutated for unrelated callers; subprocesses still use
        :meth:`subprocess_env`, which omits ``SBERT_DEVICE`` unless explicitly resolved.
        """
        os.environ[ENV_INDEX_DIR] = str(self.index_dir.resolve())
        os.environ[ENV_SOURCE_ROOT] = str(self.source_root.resolve())
        os.environ["SBERT_MODEL"] = self.embedding_model
        if self.embedding_device is not None:
            os.environ["SBERT_DEVICE"] = self.embedding_device

    def subprocess_env(self, base: dict[str, str] | None = None) -> dict[str, str]:
        out = dict(base or os.environ)
        out[ENV_INDEX_DIR] = str(self.index_dir.resolve())
        out[ENV_SOURCE_ROOT] = str(self.source_root.resolve())
        out["SBERT_MODEL"] = self.embedding_model
        if self.embedding_device is not None:
            out["SBERT_DEVICE"] = self.embedding_device
        else:
            out.pop("SBERT_DEVICE", None)
        return out


def _pick_str(
    *,
    cli_val: str | None,
    env_key: str,
    yaml_dict: dict[str, Any],
    yaml_path: tuple[str, ...],
    default: str,
) -> tuple[str, SettingSource]:
    if cli_val is not None and str(cli_val).strip() != "":
        return str(cli_val).strip(), "cli"
    env_raw = os.environ.get(env_key, "").strip()
    if env_raw:
        return env_raw, "env"
    cur: Any = yaml_dict
    for part in yaml_path:
        if not isinstance(cur, dict) or part not in cur:
            cur = None
            break
        cur = cur.get(part)
    if isinstance(cur, str) and cur.strip():
        return cur.strip(), "yaml"
    return default, "default"


def _pick_optional_device(
    *,
    cli_val: str | None,
    env_key: str,
    yaml_dict: dict[str, Any],
) -> tuple[str | None, SettingSource]:
    if cli_val is not None and str(cli_val).strip() != "":
        return str(cli_val).strip(), "cli"
    env_raw = os.environ.get(env_key, "").strip()
    if env_raw:
        return env_raw, "env"
    emb = yaml_dict.get("embedding")
    if isinstance(emb, dict):
        d = emb.get("device")
        if isinstance(d, str) and d.strip():
            return d.strip(), "yaml"
    return None, "default"


def _pick_bool(
    *,
    env_key: str,
    yaml_dict: dict[str, Any],
    yaml_path: tuple[str, ...],
    default: bool,
) -> tuple[bool, SettingSource]:
    env_raw = os.environ.get(env_key, "").strip().lower()
    if env_raw in ("1", "true", "yes"):
        return True, "env"
    if env_raw in ("0", "false", "no"):
        return False, "env"
    cur: Any = yaml_dict
    for part in yaml_path:
        if not isinstance(cur, dict) or part not in cur:
            cur = None
            break
        cur = cur.get(part)
    if isinstance(cur, bool):
        return cur, "yaml"
    return default, "default"


def _pick_float(
    *,
    env_key: str,
    yaml_dict: dict[str, Any],
    yaml_path: tuple[str, ...],
    default: float,
) -> tuple[float, SettingSource]:
    """Pick a float setting from env (parsed via float(...)), YAML, or default.

    Precedence: CLI > env > YAML > default. Env values that fail to parse as float
    fall back to the default (matching the brief's requirement for graceful degradation).
    """
    env_raw = os.environ.get(env_key, "").strip()
    if env_raw:
        try:
            return float(env_raw), "env"
        except ValueError:
            # Invalid env value falls back to default (per brief)
            pass
    cur: Any = yaml_dict
    for part in yaml_path:
        if not isinstance(cur, dict) or part not in cur:
            cur = None
            break
        cur = cur.get(part)
    if isinstance(cur, (int, float)):
        return float(cur), "yaml"
    return default, "default"


def _pick_int(
    *,
    env_key: str,
    yaml_dict: dict[str, Any],
    yaml_path: tuple[str, ...],
    default: int,
) -> tuple[int, SettingSource]:
    """Pick an int setting from env (parsed via int(...)), YAML, or default.

    Precedence: CLI > env > YAML > default. Env values that fail to parse as int
    fall back to the default (matching the brief's requirement for graceful degradation).
    """
    env_raw = os.environ.get(env_key, "").strip()
    if env_raw:
        try:
            return int(env_raw), "env"
        except ValueError:
            # Invalid env value falls back to default (per brief)
            pass
    cur: Any = yaml_dict
    for part in yaml_path:
        if not isinstance(cur, dict) or part not in cur:
            cur = None
            break
        cur = cur.get(part)
    if isinstance(cur, int):
        return cur, "yaml"
    return default, "default"


def _resolve_index_dir_path(
    *,
    source_root: Path,
    config_dir: Path,
    cli_index_dir: str | None,
    yaml_dict: dict[str, Any],
) -> tuple[Path, SettingSource]:
    # Bases for relative paths:
    #   - YAML ``index_dir``  -> the config file's directory (``config_dir``),
    #     the SAME base used for YAML ``source_root``. Paths written in the
    #     config file are relative to the file, so both keys stay consistent.
    #   - CLI / env ``index_dir`` -> ``source_root`` (unchanged). These are not
    #     "in the config file"; preserving the existing base avoids a semantics
    #     change for operators who pass ``--index-dir`` on the command line.
    #   - Default ``./.java-codebase-rag`` -> ``source_root`` so the index sits
    #     beside the Java tree (the layout ``discover_project_root`` anchors on).
    raw_cli = cli_index_dir.strip() if isinstance(cli_index_dir, str) else None
    if raw_cli:
        p = Path(raw_cli).expanduser()
        out = p.resolve() if p.is_absolute() else (source_root / p).resolve()
        return out, "cli"

    env_raw = os.environ.get(ENV_INDEX_DIR, "").strip()
    if env_raw:
        p = Path(env_raw).expanduser()
        out = p.resolve() if p.is_absolute() else (source_root / p).resolve()
        return out, "env"

    idx = yaml_dict.get("index_dir")
    if isinstance(idx, str) and idx.strip():
        p = Path(idx.strip()).expanduser()
        out = p.resolve() if p.is_absolute() else (config_dir / p).resolve()
        return out, "yaml"

    return (source_root / ".java-codebase-rag").resolve(), "default"


def resolve_operator_config(
    *,
    source_root: Path | None,
    cli_index_dir: str | None = None,
    cli_embedding_model: str | None = None,
    cli_embedding_device: str | None = None,
) -> ResolvedOperatorConfig:
    # Phase 1: Find the config file directory
    if source_root is not None:
        # CLI flag provided: use it as both config_dir and effective source_root
        # (skip YAML source_root check - CLI wins). ``_effective_config_dir`` may
        # rebase config_dir to a YAML reached via the index-dir pointer; root is
        # untouched (explicit source_root wins).
        root = source_root.expanduser().resolve()
        config_dir = _effective_config_dir(root)
        yaml_dict = load_yaml_mapping(config_dir)
    else:
        # Check env var first
        env_raw = os.environ.get(ENV_SOURCE_ROOT, "").strip()
        if env_raw:
            root = Path(env_raw).expanduser().resolve()
            config_dir = _effective_config_dir(root)
            yaml_dict = load_yaml_mapping(config_dir)
        else:
            # Walk up to find config dir
            discovered = discover_project_root(Path.cwd())
            config_dir = discovered if discovered is not None else Path.cwd().resolve()
            # Follow an index-dir pointer to the real config dir when the anchor
            # has no YAML beside it (e.g. config in a sibling dir).
            config_dir = _effective_config_dir(config_dir)
            # Load YAML from config dir
            yaml_dict = load_yaml_mapping(config_dir)

            # Phase 2: Resolve effective source root
            # Check for YAML source_root field (resolved relative to config dir)
            yaml_source_root = yaml_dict.get("source_root")
            if isinstance(yaml_source_root, str) and yaml_source_root.strip():
                yroot = Path(yaml_source_root.strip()).expanduser()
                root = yroot.resolve() if yroot.is_absolute() else (config_dir / yroot).resolve()
            else:
                root = config_dir

    index_dir, index_src = _resolve_index_dir_path(
        source_root=root, config_dir=config_dir, cli_index_dir=cli_index_dir, yaml_dict=yaml_dict
    )
    model, model_src = _pick_str(
        cli_val=cli_embedding_model,
        env_key="SBERT_MODEL",
        yaml_dict=yaml_dict,
        yaml_path=("embedding", "model"),
        default=_DEFAULT_EMBEDDING_MODEL,
    )
    model = maybe_expand_embedding_model_path(
        model,
        config_dir=config_dir,
        source_root=root,
        source=model_src,
    )
    device, device_src = _pick_optional_device(
        cli_val=cli_embedding_device,
        env_key="SBERT_DEVICE",
        yaml_dict=yaml_dict,
    )
    hints, hints_src = _pick_bool(
        env_key="JAVA_CODEBASE_RAG_HINTS_ENABLED",
        yaml_dict=yaml_dict,
        yaml_path=("hints", "enabled"),
        default=True,
    )
    # Absence diagnosis config (PR-ABS-0)
    abs_close, abs_close_src = _pick_float(
        env_key="JAVA_CODEBASE_RAG_ABSENCE_CLOSE_THRESHOLD",
        yaml_dict=yaml_dict,
        yaml_path=("absence", "close_threshold"),
        default=0.85,
    )
    abs_floor, abs_floor_src = _pick_float(
        env_key="JAVA_CODEBASE_RAG_ABSENCE_ABSENT_FLOOR",
        yaml_dict=yaml_dict,
        yaml_path=("absence", "absent_floor"),
        default=0.40,
    )
    abs_cand, abs_cand_src = _pick_int(
        env_key="JAVA_CODEBASE_RAG_ABSENCE_CANDIDATE_COUNT",
        yaml_dict=yaml_dict,
        yaml_path=("absence", "candidate_count"),
        default=5,
    )
    abs_q, abs_q_src = _pick_int(
        env_key="JAVA_CODEBASE_RAG_ABSENCE_NGRAM_Q",
        yaml_dict=yaml_dict,
        yaml_path=("absence", "ngram_q"),
        default=3,
    )
    abs_diag, abs_diag_src = _pick_bool(
        env_key="JAVA_CODEBASE_RAG_ABSENCE_DIAG_ENABLED",
        yaml_dict=yaml_dict,
        yaml_path=("absence", "diag_enabled"),
        default=True,
    )
    ku = index_dir / "code_graph.lbug"
    coco = index_dir / "cocoindex.db"
    return ResolvedOperatorConfig(
        source_root=root,
        index_dir=index_dir,
        ladybug_path=ku,
        cocoindex_db=coco,
        embedding_model=model,
        embedding_device=device,
        hints_enabled=hints,
        index_dir_source=index_src,
        embedding_model_source=model_src,
        embedding_device_source=device_src,
        hints_enabled_source=hints_src,
        absence_close_threshold=abs_close,
        absence_absent_floor=abs_floor,
        absence_candidate_count=abs_cand,
        absence_ngram_q=abs_q,
        absence_diag_enabled=abs_diag,
        absence_close_threshold_source=abs_close_src,
        absence_absent_floor_source=abs_floor_src,
        absence_candidate_count_source=abs_cand_src,
        absence_ngram_q_source=abs_q_src,
        absence_diag_enabled_source=abs_diag_src,
        yaml_config_path=find_yaml_config_file(config_dir),
    )


def write_config_source_pointer(
    *, index_dir: Path, yaml_config_path: Path | None
) -> None:
    """Record the YAML config path inside the index dir (best-effort).

    Writes ``<index_dir>/config_source`` with the YAML's absolute path so a later
    discovery run that anchors on the index dir (but has no YAML beside it) can
    relocate the config via :func:`_effective_config_dir`. No-op when
    ``yaml_config_path`` is None (pure-default build — nothing to remember). Never
    raises: the pointer is an optimization, not a correctness requirement — a
    missing/unreadable pointer just falls back to built-in defaults.
    """
    if yaml_config_path is None:
        return
    try:
        index_dir.mkdir(parents=True, exist_ok=True)
        content = str(yaml_config_path.resolve()) + "\n"
        target = index_dir / CONFIG_SOURCE_FILENAME
        tmp = index_dir / (CONFIG_SOURCE_FILENAME + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def index_dir_has_existing_artifacts(index_dir: Path) -> tuple[bool, list[str]]:
    """True if graph dir or any Lance table already exists under index_dir."""
    paths: list[str] = []
    ku = index_dir / "code_graph.lbug"
    if ku.exists():
        paths.append(str(ku.resolve()))
    if index_dir.is_dir():
        try:
            import lancedb

            db = lancedb.connect(str(index_dir.resolve()))
            for name in db.list_tables():
                paths.append(str((index_dir / name).resolve()) + " (Lance table)")
        except Exception:
            pass
    return bool(paths), paths


def describe_path_sizes(paths: list[Path]) -> list[tuple[Path, int]]:
    """Return (path, bytes) for files/dirs that exist."""
    out: list[tuple[Path, int]] = []

    def _sz(p: Path) -> int:
        if p.is_file():
            return p.stat().st_size
        if p.is_dir():
            total = 0
            for sub in p.rglob("*"):
                if sub.is_file():
                    try:
                        total += sub.stat().st_size
                    except OSError:
                        pass
            return total
        return 0

    for p in paths:
        if p.exists():
            out.append((p, _sz(p)))
    return out
