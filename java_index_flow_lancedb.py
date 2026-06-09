"""
CocoIndex 1.0 app: index Java, Flyway SQL, and YAML into LanceDB.

LanceDB requires a single primary key per table; each chunk gets a UUID `id`.

Environment:
  JAVA_CODEBASE_RAG_INDEX_DIR — Lance tables + LadybugDB + cocoindex state (default: ./.java-codebase-rag)
  JAVA_CODEBASE_RAG_SOURCE_ROOT — Java repo root for indexing (optional; else cocoindex cwd)
  SBERT_MODEL / SBERT_DEVICE — embedding (optional; YAML also supported via java-codebase-rag CLI)

Dependencies:
  pip install "cocoindex[lancedb]" sentence-transformers

Usage:
  cocoindex update java_index_flow_lancedb.py:JavaCodeIndexLance --full-reprocess
"""
from __future__ import annotations

import inspect
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
import pyarrow as pa
from cocoindex.connectors import lancedb, localfs
from cocoindex.connectors.lancedb import LanceType
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.file import PatternFilePathMatcher

from java_codebase_rag.config import resolved_sbert_model_for_process_env
from java_index_v1_common import (
    JAVA_CHUNK,
    SBERT_MODEL,
    SQL_CHUNK,
    YAML_CHUNK,
    chunk_key_range,
    position_to_json,
)
from path_filtering import LayeredIgnore
from ast_java import ONTOLOGY_VERSION, parse_java
from graph_enrich import enrich_chunk

# Older cocoindex (e.g. 1.0.0a43) uses ``tracked=False``; newer releases renamed
# the flag to ``detect_change`` (default False) and reject ``tracked``.
_ck_params = inspect.signature(coco.ContextKey.__init__).parameters
if "detect_change" in _ck_params:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root")
    LANCE_DB = coco.ContextKey("java_lance_async_conn")
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("java_lance_embedder")
elif "tracked" in _ck_params:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root", tracked=False)
    LANCE_DB = coco.ContextKey("java_lance_async_conn", tracked=False)
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder](
        "java_lance_embedder", tracked=False
    )
else:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root")
    LANCE_DB = coco.ContextKey("java_lance_async_conn")
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("java_lance_embedder")

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
    package: str
    module: str
    microservice: str
    primary_type_fqn: str
    primary_type_kind: str
    role: str
    # Native PyArrow lists: without the LanceType override CocoIndex would JSON-encode
    # `list[str]` into a STRING column, which caller code then iterates character-by-character.
    capabilities: Annotated[list[str], LanceType(pa.list_(pa.string()))]
    annotations_on_type: Annotated[list[str], LanceType(pa.list_(pa.string()))]
    symbols: Annotated[list[str], LanceType(pa.list_(pa.string()))]
    ontology_version: int


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
    idx_raw = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if idx_raw and not idx_raw.startswith(("s3://", "gs://", "az://")):
        index_dir = Path(idx_raw).expanduser().resolve()
    else:
        index_dir = (Path(".").resolve() / ".java-codebase-rag").resolve()
    index_dir.mkdir(parents=True, exist_ok=True)
    builder.settings.db_path = index_dir / "cocoindex.db"

    env_root = os.environ.get("JAVA_CODEBASE_RAG_SOURCE_ROOT", "").strip()
    if env_root:
        root = Path(env_root).expanduser().resolve()
    else:
        root = Path(".").resolve()
    builder.provide(PROJECT_ROOT, root)

    embedder = SentenceTransformerEmbedder(
        resolved_sbert_model_for_process_env(SBERT_MODEL),
        device=os.environ.get("SBERT_DEVICE") or None,
        trust_remote_code=True,
    )
    builder.provide(EMBEDDER, embedder)

    uri = str(index_dir)

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
    project_root = coco.use_context(PROJECT_ROOT)
    if LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve())[0]:
        return
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
    content_bytes = content.encode("utf-8", errors="replace")
    ast = parse_java(content_bytes)

    for ch in chunks:
        rs, re = chunk_key_range(ch)
        enrich = enrich_chunk(
            ast,
            chunk_start_byte=ch.start.byte_offset,
            chunk_end_byte=ch.end.byte_offset,
            file_path=rel,
            project_root=project_root,
        )
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
                package=enrich.package,
                module=enrich.module,
                microservice=enrich.microservice,
                primary_type_fqn=enrich.primary_type_fqn,
                primary_type_kind=enrich.primary_type_kind,
                role=enrich.role,
                capabilities=list(enrich.capabilities),
                annotations_on_type=enrich.annotations_on_type,
                symbols=enrich.symbols,
                ontology_version=ONTOLOGY_VERSION,
            )
        )


@coco.fn(memo=True)
async def process_sql_file(
    file: localfs.File,
    table: lancedb.TableTarget[SqlLanceChunk],
) -> None:
    embedder = coco.use_context(EMBEDDER)
    project_root = coco.use_context(PROJECT_ROOT)
    if LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve())[0]:
        return
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
    project_root = coco.use_context(PROJECT_ROOT)
    if LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve())[0]:
        return
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

    project_root = coco.use_context(PROJECT_ROOT)
    _ignore = LayeredIgnore(project_root)
    _walk_excludes = _ignore.cocoindex_excluded_patterns()
    java_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=["**/*.java"],
            excluded_patterns=_walk_excludes,
        ),
    )
    sql_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=["**/src/main/resources/db/migration/*.sql"],
            excluded_patterns=_walk_excludes,
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
            excluded_patterns=_walk_excludes,
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