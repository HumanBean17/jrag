"""Unified operator config: index paths, embedding knobs, YAML (PR-CLI-2).

Precedence for shared knobs: CLI > env > YAML > built-in default.
Legacy env names and legacy YAML filenames are never read for behaviour;
optional one-line stderr hints may fire when deprecated names are detected.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SettingSource = Literal["cli", "env", "yaml", "default"]

YAML_CONFIG_FILENAMES = (".java-codebase-rag.yml", ".java-codebase-rag.yaml")
LEGACY_YAML_FILENAMES = (".lancedb-mcp.yml", ".lancedb-mcp.yaml")

ENV_INDEX_DIR = "JAVA_CODEBASE_RAG_INDEX_DIR"
# Public operator contract is five names: INDEX_DIR, DEBUG_CONTEXT, RUN_HEAVY, SBERT_MODEL, SBERT_DEVICE.
# SOURCE_ROOT is still required for MCP / subprocess Java tree resolution (see mcp.json.example); it is not folded into the headline "5".
ENV_SOURCE_ROOT = "JAVA_CODEBASE_RAG_SOURCE_ROOT"
ENV_DEBUG_CONTEXT = "JAVA_CODEBASE_RAG_DEBUG_CONTEXT"
ENV_RUN_HEAVY = "JAVA_CODEBASE_RAG_RUN_HEAVY"

_DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Legacy env keys: never honored; detection-only hints name the replacement (if any).
_LEGACY_ENV_HINTS: tuple[tuple[str, str], ...] = (
    ("LANCEDB_URI", "JAVA_CODEBASE_RAG_INDEX_DIR"),
    ("KUZU_DB_PATH", "JAVA_CODEBASE_RAG_INDEX_DIR (Kuzu lives at <index_dir>/code_graph.kuzu)"),
    ("LANCEDB_MCP_PROJECT_ROOT", "cwd or --source-root (no env replacement)"),
    ("LANCEDB_MCP_ALLOW_REFRESH", "(removed; use init / increment / reprocess / erase)"),
    ("LANCEDB_MCP_GRAPH_ENABLED", "(removed; graph is used when code_graph.kuzu exists)"),
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
    kuzu_path: Path
    cocoindex_db: Path
    embedding_model: str
    embedding_device: str | None
    index_dir_source: SettingSource
    embedding_model_source: SettingSource
    embedding_device_source: SettingSource

    def apply_to_os_environ(self) -> None:
        """Make downstream modules (server, kuzu_queries, flows) see a consistent environment.

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


def _resolve_index_dir_path(
    *,
    source_root: Path,
    cli_index_dir: str | None,
    yaml_dict: dict[str, Any],
) -> tuple[Path, SettingSource]:
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
        out = p.resolve() if p.is_absolute() else (source_root / p).resolve()
        return out, "yaml"

    return (source_root / ".java-codebase-rag").resolve(), "default"


def resolve_operator_config(
    *,
    source_root: Path | None,
    cli_index_dir: str | None = None,
    cli_embedding_model: str | None = None,
    cli_embedding_device: str | None = None,
) -> ResolvedOperatorConfig:
    root = (source_root or Path.cwd()).expanduser().resolve()
    yaml_dict = load_yaml_mapping(root)
    index_dir, index_src = _resolve_index_dir_path(
        source_root=root, cli_index_dir=cli_index_dir, yaml_dict=yaml_dict
    )
    model, model_src = _pick_str(
        cli_val=cli_embedding_model,
        env_key="SBERT_MODEL",
        yaml_dict=yaml_dict,
        yaml_path=("embedding", "model"),
        default=_DEFAULT_EMBEDDING_MODEL,
    )
    device, device_src = _pick_optional_device(
        cli_val=cli_embedding_device,
        env_key="SBERT_DEVICE",
        yaml_dict=yaml_dict,
    )
    ku = index_dir / "code_graph.kuzu"
    coco = index_dir / "cocoindex.db"
    return ResolvedOperatorConfig(
        source_root=root,
        index_dir=index_dir,
        kuzu_path=ku,
        cocoindex_db=coco,
        embedding_model=model,
        embedding_device=device,
        index_dir_source=index_src,
        embedding_model_source=model_src,
        embedding_device_source=device_src,
    )


def index_dir_has_existing_artifacts(index_dir: Path) -> tuple[bool, list[str]]:
    """True if Kuzu graph dir or any Lance table already exists under index_dir."""
    paths: list[str] = []
    ku = index_dir / "code_graph.kuzu"
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
