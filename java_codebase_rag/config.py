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


def cocoindex_subprocess_env_defaults() -> dict[str, str]:
    """Env defaults applied to every CocoIndex subprocess to bound concurrency.

    Apply with ``env.setdefault(...)`` so a caller-provided (operator) value
    always wins. See :issue:`306`.
    """
    return {
        COCOINDEX_MAX_INFLIGHT_COMPONENTS_ENV: COCOINDEX_DEFAULT_MAX_INFLIGHT_COMPONENTS
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
    """
    start = start.resolve()
    home = Path.home().resolve()

    current = start
    while True:
        # Config file is the primary anchor
        if find_yaml_config_file(current) is not None:
            return current
        # Index directory is the secondary anchor (supports indexes without config)
        if _has_index_dir(current):
            return current

        # Stop if we've reached home (check home itself, but don't walk past it)
        if current == home:
            return None

        # Stop if we've reached filesystem root
        parent = current.parent
        if parent == current:
            return None

        current = parent


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
    except Exception:
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
        # (skip YAML source_root check - CLI wins)
        root = source_root.expanduser().resolve()
        config_dir = root
        yaml_dict = load_yaml_mapping(config_dir)
    else:
        # Check env var first
        env_raw = os.environ.get(ENV_SOURCE_ROOT, "").strip()
        if env_raw:
            root = Path(env_raw).expanduser().resolve()
            config_dir = root
            yaml_dict = load_yaml_mapping(config_dir)
        else:
            # Walk up to find config dir
            discovered = discover_project_root(Path.cwd())
            config_dir = discovered if discovered is not None else Path.cwd().resolve()
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
    )


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
            for name in db.table_names():
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
