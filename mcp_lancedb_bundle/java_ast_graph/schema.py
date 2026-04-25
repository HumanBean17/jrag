"""Kuzu property-graph schema for DKB (structural Java)."""

from __future__ import annotations

# Node PK columns use STRING; rel tables carry optional metadata.

SCHEMA_DDL: list[str] = [
    (
        "CREATE NODE TABLE File("
        "file_key STRING, rel_path STRING, module_root STRING, module_label STRING, "
        "PRIMARY KEY (file_key))"
    ),
    "CREATE NODE TABLE Package(pid STRING, PRIMARY KEY (pid))",
    (
        "CREATE NODE TABLE Type("
        "fqn STRING, kind STRING, simple_name STRING, package_id STRING, file_key STRING, "
        "PRIMARY KEY (fqn))"
    ),
    (
        "CREATE NODE TABLE Method("
        "mid STRING, name STRING, type_fqn STRING, file_key STRING, start_line INT64, "
        "is_constructor BOOL, "
        "PRIMARY KEY (mid))"
    ),
    "CREATE REL TABLE F_DECLARED_IN (FROM Type TO File)",
    "CREATE REL TABLE T_IN_PACKAGE (FROM Type TO Package)",
    "CREATE REL TABLE T_EXTENDS (FROM Type TO Type, resolved BOOL)",
    "CREATE REL TABLE T_IMPLEMENTS (FROM Type TO Type, resolved BOOL)",
    "CREATE REL TABLE T_INJECTS (FROM Type TO Type, resolved BOOL)",
    "CREATE REL TABLE M_DECLARED (FROM Type TO Method, kind STRING)",
]
