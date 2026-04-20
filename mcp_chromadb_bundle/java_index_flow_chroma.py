"""
CocoIndex flow: index Java, SQL, and YAML sources into ChromaDB (persistent or HTTP client).

Run (from repo / project root):

  cocoindex update java_index_flow_chroma.py:java_index_chroma_flow --full-reprocess

Environment:

- ``CHROMA_DB_PATH`` — on-disk directory for ``PersistentClient`` (default: ``./chromadb_data``).
- ``COCOINDEX_CHROMA_CLIENT`` — ``persistent`` (default), ``http``, or ``cloud``.
- ``COCOINDEX_CHROMA_HOST`` / ``COCOINDEX_CHROMA_PORT`` — for HTTP client.
- ``COCOINDEX_CHROMA_API_KEY`` / ``COCOINDEX_CHROMA_TENANT`` / ``COCOINDEX_CHROMA_DATABASE`` — for cloud.
- ``COCOINDEX_CODE_ROOT`` — root directory to index (default: current working directory).
- ``SBERT_MODEL`` — embedding model id (default: ``sentence-transformers/all-MiniLM-L6-v2``).

Collections match :mod:`mcp_chromadb_bundle.search_chroma` defaults:

- ``javacodeindex_java_code``
- ``sqlschemaindex_sql_schema``
- ``yamlconfigindex_yaml_config``

Chroma requires a **single** primary-key field per document; this flow adds ``doc_id`` from
``filename`` and ``location``.
"""

import json
import os
from pathlib import Path

import cocoindex
import cocoindex.targets.chromadb as coco_chromadb
from numpy.typing import NDArray
import numpy as np

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_SBERT_MODEL = os.environ.get("SBERT_MODEL", _DEFAULT_MODEL)


def _resolved_code_root() -> str:
    return str(Path(os.environ.get("COCOINDEX_CODE_ROOT", ".")).expanduser().resolve())


def _chroma_path() -> str:
    return os.environ.get("CHROMA_DB_PATH", "./chromadb_data")


def _chroma_client_type() -> coco_chromadb.ClientType:
    raw = os.environ.get("COCOINDEX_CHROMA_CLIENT", "persistent").strip().lower()
    if raw in ("http",):
        return coco_chromadb.ClientType.HTTP
    if raw in ("cloud",):
        return coco_chromadb.ClientType.CLOUD
    return coco_chromadb.ClientType.PERSISTENT


def _chroma_target_kwargs() -> dict:
    ct = _chroma_client_type()
    out: dict = {"client_type": ct}
    if ct == coco_chromadb.ClientType.PERSISTENT:
        out["path"] = _chroma_path()
    elif ct == coco_chromadb.ClientType.HTTP:
        out["host"] = os.environ.get("COCOINDEX_CHROMA_HOST", "localhost")
        out["port"] = int(os.environ.get("COCOINDEX_CHROMA_PORT", "8000"))
        out["ssl"] = os.environ.get("COCOINDEX_CHROMA_SSL", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
    else:
        key = os.environ.get("COCOINDEX_CHROMA_API_KEY", "").strip()
        if key:
            out["api_key"] = key
        out["tenant"] = os.environ.get("COCOINDEX_CHROMA_TENANT", "default_tenant")
        out["database"] = os.environ.get("COCOINDEX_CHROMA_DATABASE", "default_database")
    return out


_COMMON_EXCLUDED = [
    "**/.*",
    "**/node_modules/**",
    "**/target/**",
    "**/build/**",
    "**/.git/**",
]


@cocoindex.op.function()
def make_doc_id(filename: str, location: object) -> str:
    if isinstance(location, str):
        loc = location
    else:
        loc = json.dumps(location, sort_keys=True)
    return f"{filename}\x1e{loc}"


@cocoindex.transform_flow()
def code_to_embedding(
    text: cocoindex.DataSlice[str],
) -> cocoindex.DataSlice[NDArray[np.float32]]:
    return text.transform(
        cocoindex.functions.SentenceTransformerEmbed(
            model=_SBERT_MODEL,
        )
    )


@cocoindex.flow_def(name="JavaEnterpriseCodeIndexChroma")
def java_index_chroma_flow(
    flow_builder: cocoindex.FlowBuilder,
    data_scope: cocoindex.DataScope,
) -> None:
    root = _resolved_code_root()
    ch_kw = _chroma_target_kwargs()

    data_scope["java_files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=root,
            included_patterns=["**/*.java"],
            excluded_patterns=_COMMON_EXCLUDED,
        )
    )
    data_scope["sql_files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=root,
            included_patterns=["**/*.sql"],
            excluded_patterns=_COMMON_EXCLUDED,
        )
    )
    data_scope["yaml_files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=root,
            included_patterns=["**/*.yml", "**/*.yaml"],
            excluded_patterns=_COMMON_EXCLUDED,
        )
    )

    java_embeddings = data_scope.add_collector()
    sql_embeddings = data_scope.add_collector()
    yaml_embeddings = data_scope.add_collector()

    with data_scope["java_files"].row() as file:
        file["language"] = file["filename"].transform(
            cocoindex.functions.DetectProgrammingLanguage()
        )
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language="java",
            chunk_size=1000,
            min_chunk_size=300,
            chunk_overlap=300,
        )
        with file["chunks"].row() as chunk:
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            chunk["doc_id"] = file["filename"].transform(
                make_doc_id,
                chunk["location"],
            )
            java_embeddings.collect(
                doc_id=chunk["doc_id"],
                filename=file["filename"],
                location=chunk["location"],
                text=chunk["text"],
                embedding=chunk["embedding"],
                start=chunk["start"],
                end=chunk["end"],
                language=file["language"],
            )

    with data_scope["sql_files"].row() as file:
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language="sql",
            chunk_size=2000,
            min_chunk_size=100,
            chunk_overlap=200,
        )
        with file["chunks"].row() as chunk:
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            chunk["doc_id"] = file["filename"].transform(
                make_doc_id,
                chunk["location"],
            )
            sql_embeddings.collect(
                doc_id=chunk["doc_id"],
                filename=file["filename"],
                location=chunk["location"],
                text=chunk["text"],
                embedding=chunk["embedding"],
                start=chunk["start"],
                end=chunk["end"],
            )

    with data_scope["yaml_files"].row() as file:
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language="yaml",
            chunk_size=2000,
            min_chunk_size=100,
            chunk_overlap=200,
        )
        with file["chunks"].row() as chunk:
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            chunk["doc_id"] = file["filename"].transform(
                make_doc_id,
                chunk["location"],
            )
            yaml_embeddings.collect(
                doc_id=chunk["doc_id"],
                filename=file["filename"],
                location=chunk["location"],
                text=chunk["text"],
                embedding=chunk["embedding"],
                start=chunk["start"],
                end=chunk["end"],
            )

    java_collection = "javacodeindex_java_code"
    sql_collection = "sqlschemaindex_sql_schema"
    yaml_collection = "yamlconfigindex_yaml_config"

    java_embeddings.export(
        java_collection,
        coco_chromadb.ChromaDB(
            collection_name=java_collection,
            document_field="text",
            **ch_kw,
        ),
        primary_key_fields=["doc_id"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            ),
        ],
    )
    sql_embeddings.export(
        sql_collection,
        coco_chromadb.ChromaDB(
            collection_name=sql_collection,
            document_field="text",
            **ch_kw,
        ),
        primary_key_fields=["doc_id"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            ),
        ],
    )
    yaml_embeddings.export(
        yaml_collection,
        coco_chromadb.ChromaDB(
            collection_name=yaml_collection,
            document_field="text",
            **ch_kw,
        ),
        primary_key_fields=["doc_id"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            ),
        ],
    )
