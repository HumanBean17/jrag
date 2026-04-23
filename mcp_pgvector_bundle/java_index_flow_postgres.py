"""
CocoIndex flow: index Java, SQL, and YAML sources into PostgreSQL (pgvector + GIN FTS).

Run (from repo / project root, with Postgres reachable via ``COCOINDEX_DATABASE_URL``):

  cocoindex update java_index_flow_postgres.py:java_index_postgres_flow --full-reprocess

Environment:

- ``COCOINDEX_DATABASE_URL`` — PostgreSQL connection string (pgvector extension required).
- ``COCOINDEX_CODE_ROOT`` — root directory to index (default: current working directory).
- ``SBERT_MODEL`` — embedding model id (default: ``sentence-transformers/all-MiniLM-L6-v2``).

Tables created match :mod:`mcp_pgvector_bundle.search_postgres` defaults:

- ``javacodeindex_java_code``
- ``sqlschemaindex_sql_schema``
- ``yamlconfigindex_yaml_config``
"""

import os
from pathlib import Path

import cocoindex
from numpy.typing import NDArray
import numpy as np

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_SBERT_MODEL = os.environ.get("SBERT_MODEL", _DEFAULT_MODEL)


def _resolved_code_root() -> str:
    return str(Path(os.environ.get("COCOINDEX_CODE_ROOT", ".")).expanduser().resolve())


_COMMON_EXCLUDED = [
    "**/.*",
    "**/node_modules/**",
    "**/target/**",
    "**/build/**",
    "**/.git/**",
]


def _gin_fts_setup(table: str) -> cocoindex.targets.PostgresSqlCommand:
    return cocoindex.targets.PostgresSqlCommand(
        name=f"{table}_text_fts_gin",
        setup_sql=(
            f"CREATE INDEX IF NOT EXISTS {table}_text_fts "
            f"ON {table} USING GIN (to_tsvector('english', text));"
        ),
        teardown_sql=f"DROP INDEX IF EXISTS {table}_text_fts;",
    )


@cocoindex.transform_flow()
def code_to_embedding(
    text: cocoindex.DataSlice[str],
) -> cocoindex.DataSlice[NDArray[np.float32]]:
    return text.transform(
        cocoindex.functions.SentenceTransformerEmbed(
            model=_SBERT_MODEL,
        )
    )


@cocoindex.flow_def(name="JavaEnterpriseCodeIndexPg")
def java_index_postgres_flow(
    flow_builder: cocoindex.FlowBuilder,
    data_scope: cocoindex.DataScope,
) -> None:
    root = _resolved_code_root()

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
            java_embeddings.collect(
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
            sql_embeddings.collect(
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
            yaml_embeddings.collect(
                filename=file["filename"],
                location=chunk["location"],
                text=chunk["text"],
                embedding=chunk["embedding"],
                start=chunk["start"],
                end=chunk["end"],
            )

    java_table = "javacodeindex_java_code"
    sql_table = "sqlschemaindex_sql_schema"
    yaml_table = "yamlconfigindex_yaml_config"

    java_embeddings.export(
        java_table,
        cocoindex.targets.Postgres(table_name=java_table),
        primary_key_fields=["filename", "location"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            ),
        ],
        attachments=[_gin_fts_setup(java_table)],
    )
    sql_embeddings.export(
        sql_table,
        cocoindex.targets.Postgres(table_name=sql_table),
        primary_key_fields=["filename", "location"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            ),
        ],
        attachments=[_gin_fts_setup(sql_table)],
    )
    yaml_embeddings.export(
        yaml_table,
        cocoindex.targets.Postgres(table_name=yaml_table),
        primary_key_fields=["filename", "location"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            ),
        ],
        attachments=[_gin_fts_setup(yaml_table)],
    )
