"""
CocoIndex 1.0 app: index Java, Flyway SQL, and YAML into LanceDB.

LanceDB requires a single primary key per table; each chunk gets a UUID `id`.

Environment:
  LANCEDB_URI — database directory or URI (default: ./lancedb_data)
  COCOINDEX_DB — CocoIndex state DB path (optional)

Dependencies:
  pip install "cocoindex[lancedb]" sentence-transformers

Usage:
  cocoindex update java_index_flow_lancedb.py:JavaCodeIndexLance --full-reprocess
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import cocoindex as coco
import numpy as np
import numpy.typing as npt
from cocoindex.connectors import lancedb, localfs
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.file import PatternFilePathMatcher

from java_index_v1_common import (
    COMMON_EXCLUDED_PATH_PATTERNS,
    JAVA_CHUNK,
    SBERT_MODEL,
    SQL_CHUNK,
    YAML_CHUNK,
    chunk_key_range,
    position_to_json,
)

PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root", tracked=False)
LANCE_DB = coco.ContextKey("java_lance_async_conn", tracked=False)
EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder](
    "java_lance_embedder", tracked=False
)

splitter = RecursiveSplitter()


@dataclass
class JavaLanceChunk:
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
class SqlLanceChunk:
    id: str
    filename: str
    text: str
    range_start: int
    range_end: int
    start: dict[str, Any]
    end: dict[str, Any]
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]


@dataclass
class YamlLanceChunk:
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
    builder.settings.db_path = Path(
        os.environ.get("COCOINDEX_DB", "./cocoindex_java_lance.db")
    )
    root = Path(".").resolve()
    builder.provide(PROJECT_ROOT, root)

    embedder = SentenceTransformerEmbedder(
        SBERT_MODEL,
        trust_remote_code=True,
    )
    builder.provide(EMBEDDER, embedder)

    uri = os.environ.get("LANCEDB_URI", "./lancedb_data")

    @asynccontextmanager
    async def _lance_cm() -> AsyncIterator[Any]:
        conn = await lancedb.connect_async(uri)
        try:
            yield conn
        finally:
            conn.close()

    await builder.provide_async_with(LANCE_DB, _lance_cm())
    yield


@coco.fn(memo=True)
async def process_java_file(
    file: localfs.File,
    table: lancedb.TableTarget[JavaLanceChunk],
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
            row=JavaLanceChunk(
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
    table: lancedb.TableTarget[SqlLanceChunk],
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
            row=SqlLanceChunk(
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
    table: lancedb.TableTarget[YamlLanceChunk],
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
            row=YamlLanceChunk(
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
    java_schema = await lancedb.TableSchema.from_class(
        JavaLanceChunk,
        primary_key=["id"],
    )
    java_table = await lancedb.mount_table_target(
        LANCE_DB,
        "javacodeindex_java_code",
        java_schema,
    )

    sql_schema = await lancedb.TableSchema.from_class(
        SqlLanceChunk,
        primary_key=["id"],
    )
    sql_table = await lancedb.mount_table_target(
        LANCE_DB,
        "sqlschemaindex_sql_schema",
        sql_schema,
    )

    yaml_schema = await lancedb.TableSchema.from_class(
        YamlLanceChunk,
        primary_key=["id"],
    )
    yaml_table = await lancedb.mount_table_target(
        LANCE_DB,
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
    coco.AppConfig(name="JavaCodeIndexLance"),
    app_main,
)