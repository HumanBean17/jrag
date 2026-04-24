"""
CocoIndex 1.0 app: index Java, Flyway SQL, and YAML into SQLite (sqlite-vec float[N] columns).

Environment:
  SQLITE_CODE_INDEX_DB — SQLite file path for app tables (default: java_code_index.sqlite under COCOINDEX_CODE_ROOT)
  COCOINDEX_DB — CocoIndex state DB (default: cocoindex_java_sqlite.db under COCOINDEX_CODE_ROOT)
  COCOINDEX_CODE_ROOT — project root for defaults and file scanning (default: current working directory)

Dependencies:
  pip install -r requirements.txt   # includes cocoindex extras and sqlean.py (macOS-safe sqlite-vec)

Usage:
  cd /path/to/project
  cocoindex update mcp_sqlite_bundle/java_index_flow_sqlite.py:JavaCodeIndexSqlite --full-reprocess -f
"""
from __future__ import annotations

import sqlite3_ext_shim  # noqa: F401

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import cocoindex as coco
import numpy as np
import numpy.typing as npt
from cocoindex.connectors import localfs, sqlite
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.file import PatternFilePathMatcher

from index_flow_common import (
    COMMON_EXCLUDED_PATH_PATTERNS,
    JAVA_CHUNK,
    SQL_CHUNK,
    YAML_CHUNK,
    chunk_key_range,
    position_to_json,
    SBERT_MODEL,
)

PROJECT_ROOT = coco.ContextKey[Path]("java_sqlite_project_root", detect_change=False)
SQLITE_DB = coco.ContextKey[sqlite.ManagedConnection](
    "java_sqlite_managed_conn", detect_change=False
)
EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder](
    "java_sqlite_embedder", detect_change=False
)

splitter = RecursiveSplitter()


@dataclass
class JavaSqliteChunk:
    id: str
    filename: str
    language: str
    text: str
    range_start: int
    range_end: int
    start: dict[str, Any]
    end: dict[str, Any]
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]


@dataclass
class SqlSqliteChunk:
    id: str
    filename: str
    text: str
    range_start: int
    range_end: int
    start: dict[str, Any]
    end: dict[str, Any]
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]


@dataclass
class YamlSqliteChunk:
    id: str
    filename: str
    text: str
    range_start: int
    range_end: int
    start: dict[str, Any]
    end: dict[str, Any]
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]


@coco.lifespan
async def coco_lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
    root = Path(os.environ.get("COCOINDEX_CODE_ROOT", ".")).resolve()

    raw_coco = os.environ.get("COCOINDEX_DB")
    if raw_coco is None or not str(raw_coco).strip():
        builder.settings.db_path = root / "cocoindex_java_sqlite.db"
    else:
        builder.settings.db_path = Path(os.path.expanduser(raw_coco))

    builder.provide(PROJECT_ROOT, root)

    embedder = SentenceTransformerEmbedder(
        SBERT_MODEL,
        trust_remote_code=True,
    )
    builder.provide(EMBEDDER, embedder)

    raw_idx = os.environ.get("SQLITE_CODE_INDEX_DB")
    if raw_idx is None or not str(raw_idx).strip():
        db_path = str((root / "java_code_index.sqlite").resolve())
    else:
        db_path = os.path.expanduser(raw_idx)
    # Vector columns require sqlite-vec; fail fast in indexer if missing.
    builder.provide_with(
        SQLITE_DB,
        sqlite.managed_connection(db_path, load_vec=True, timeout=30.0),
    )
    yield


@coco.fn(memo=True)
async def process_java_file(
    file: localfs.File,
    table: sqlite.TableTarget[JavaSqliteChunk],
) -> None:
    embedder = coco.use_context(EMBEDDER)
    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return
    if not content.strip():
        return

    language = detect_code_language(filename=file.file_path.path.name) or "text"
    cs, mn, ov = JAVA_CHUNK
    chunks = splitter.split(
        content,
        cs,
        min_chunk_size=mn,
        chunk_overlap=ov,
        language=language,
    )
    rel = file.file_path.path.as_posix()

    for ch in chunks:
        rs, re = chunk_key_range(ch)
        emb = await embedder.embed(ch.text)
        table.declare_row(
            row=JavaSqliteChunk(
                id=str(uuid.uuid4()),
                filename=rel,
                language=language,
                text=ch.text,
                range_start=rs,
                range_end=re,
                start=position_to_json(ch.start),
                end=position_to_json(ch.end),
                embedding=emb,
            )
        )


@coco.fn(memo=True)
async def process_sql_file(
    file: localfs.File,
    table: sqlite.TableTarget[SqlSqliteChunk],
) -> None:
    embedder = coco.use_context(EMBEDDER)
    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return
    if not content.strip():
        return

    language = "sql"
    cs, mn, ov = SQL_CHUNK
    chunks = splitter.split(
        content,
        cs,
        min_chunk_size=mn,
        chunk_overlap=ov,
        language=language,
    )
    rel = file.file_path.path.as_posix()

    for ch in chunks:
        rs, re = chunk_key_range(ch)
        emb = await embedder.embed(ch.text)
        table.declare_row(
            row=SqlSqliteChunk(
                id=str(uuid.uuid4()),
                filename=rel,
                text=ch.text,
                range_start=rs,
                range_end=re,
                start=position_to_json(ch.start),
                end=position_to_json(ch.end),
                embedding=emb,
            )
        )


@coco.fn(memo=True)
async def process_yaml_file(
    file: localfs.File,
    table: sqlite.TableTarget[YamlSqliteChunk],
) -> None:
    embedder = coco.use_context(EMBEDDER)
    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return
    if not content.strip():
        return

    ext = file.file_path.path.suffix.lower()
    language = "yaml" if ext in (".yml", ".yaml") else "text"
    cs, mn, ov = YAML_CHUNK
    chunks = splitter.split(
        content,
        cs,
        min_chunk_size=mn,
        chunk_overlap=ov,
        language=language,
    )
    rel = file.file_path.path.as_posix()

    for ch in chunks:
        rs, re = chunk_key_range(ch)
        emb = await embedder.embed(ch.text)
        table.declare_row(
            row=YamlSqliteChunk(
                id=str(uuid.uuid4()),
                filename=rel,
                text=ch.text,
                range_start=rs,
                range_end=re,
                start=position_to_json(ch.start),
                end=position_to_json(ch.end),
                embedding=emb,
            )
        )


@coco.fn
async def app_main() -> None:
    java_schema = await sqlite.TableSchema.from_class(
        JavaSqliteChunk,
        primary_key=["id"],
    )
    java_table = await sqlite.mount_table_target(
        SQLITE_DB,
        "javacodeindex_java_code",
        java_schema,
    )

    sql_schema = await sqlite.TableSchema.from_class(
        SqlSqliteChunk,
        primary_key=["id"],
    )
    sql_table = await sqlite.mount_table_target(
        SQLITE_DB,
        "sqlschemaindex_sql_schema",
        sql_schema,
    )

    yaml_schema = await sqlite.TableSchema.from_class(
        YamlSqliteChunk,
        primary_key=["id"],
    )
    yaml_table = await sqlite.mount_table_target(
        SQLITE_DB,
        "yamlconfigindex_yaml_config",
        yaml_schema,
    )

    java_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=["**/*.java"],
            excluded_patterns=COMMON_EXCLUDED_PATH_PATTERNS,
        ),
    )
    sql_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=["**/src/main/resources/db/migration/*.sql"],
            excluded_patterns=COMMON_EXCLUDED_PATH_PATTERNS,
        ),
    )
    yaml_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=[
                "**/src/main/resources/application*.yml",
                "**/src/main/resources/application*.yaml",
            ],
            excluded_patterns=COMMON_EXCLUDED_PATH_PATTERNS,
        ),
    )

    await coco.mount_each(
        coco.component_subpath(coco.Symbol("java_files")),
        process_java_file,
        java_files.items(),
        java_table,
    )
    await coco.mount_each(
        coco.component_subpath(coco.Symbol("sql_files")),
        process_sql_file,
        sql_files.items(),
        sql_table,
    )
    await coco.mount_each(
        coco.component_subpath(coco.Symbol("yaml_files")),
        process_yaml_file,
        yaml_files.items(),
        yaml_table,
    )


app = coco.App(
    coco.AppConfig(name="JavaCodeIndexSqlite"),
    app_main,
)
