"""Regression: JAX-RS-style Feign-like interface + @CodebaseHttpClient (user AssignEndpoint shape)."""

from __future__ import annotations

import shutil
from pathlib import Path

import ladybug

from java_codebase_rag.ast.ast_java import ONTOLOGY_VERSION
from java_codebase_rag.graph.graph_enrich import _load_brownfield_overrides, collect_annotation_meta_chain

STUB_ROOT = Path(__file__).resolve().parent / "fixtures" / "brownfield_client_stubs"


def _copy_stubs(dest: Path) -> None:
    shutil.copytree(STUB_ROOT, dest, dirs_exist_ok=True)


def _build(tmp: Path, yml: str | None, extra_files: dict[str, str]) -> Path:
    if yml is not None:
        (tmp / ".java-codebase-rag.yml").write_text(yml, encoding="utf-8")
    _copy_stubs(tmp)
    for rel, body in extra_files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    from _builders import build_ladybug_full_into

    db_path = tmp / "g.lbug"
    build_ladybug_full_into(tmp, db_path)
    return db_path


def _rows(db_path: Path, query: str) -> list[tuple]:
    db = ladybug.Database(str(db_path), read_only=True)
    conn = ladybug.Connection(db)
    r = conn.execute(query)
    out: list[tuple] = []
    while r.has_next():
        out.append(tuple(r.get_next()))
    return out


_JAX_RS_AND_ROLE_STUBS: dict[str, str] = {
    "com/example/rag/CodebaseRoleKind.java": """package com.example.rag;
public enum CodebaseRoleKind { CLIENT }
""",
    "com/example/rag/CodebaseRole.java": """package com.example.rag;
import java.lang.annotation.*;
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
public @interface CodebaseRole {
    CodebaseRoleKind value();
}
""",
    "com/example/rag/CodebaseCapabilityKind.java": """package com.example.rag;
public enum CodebaseCapabilityKind { HTTP_CLIENT }
""",
    "com/example/rag/CodebaseCapability.java": """package com.example.rag;
import java.lang.annotation.*;
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
public @interface CodebaseCapability {
    CodebaseCapabilityKind value();
}
""",
    "javax/ws/rs/POST.java": """package javax.ws.rs;
import java.lang.annotation.*;
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface POST {}
""",
    # User paste used @PATH (uppercase) — not standard JAX-RS @Path; stub both shapes.
    "javax/ws/rs/PATH.java": """package javax.ws.rs;
import java.lang.annotation.*;
@Target({ElementType.TYPE, ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
public @interface PATH {
    String value() default "";
}
""",
    "javax/ws/rs/core/MediaType.java": """package javax.ws.rs.core;
public final class MediaType {
    public static final String APPLICATION_JSON = "application/json";
}
""",
    "javax/ws/rs/Consumes.java": """package javax.ws.rs;
import java.lang.annotation.*;
@Target({ElementType.TYPE, ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
public @interface Consumes {
    String[] value();
}
""",
    "javax/ws/rs/Produces.java": """package javax.ws.rs;
import java.lang.annotation.*;
@Target({ElementType.TYPE, ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
public @interface Produces {
    String[] value();
}
""",
    "javax/ws/rs/HttpMethod.java": """package javax.ws.rs;
public final class HttpMethod {
    public static final String POST = "POST";
}
""",
}


def test_client_method_field_access_codebase_http_method_post_stores_post(
    tmp_path: Path,
) -> None:
    """``method = CodebaseHttpMethod.POST`` stores ``POST`` on the Client row."""
    extra = {
        "javax/ws/rs/HttpMethod.java": """package javax.ws.rs;
public final class HttpMethod {
    public static final String POST = "POST";
}
""",
        "p/E.java": """package p;

import com.example.rag.*;
import static com.example.rag.CodebaseClientKind.feign_method;

public interface E {
    @CodebaseHttpClient(clientKind = feign_method, path = "/x", method = CodebaseHttpMethod.POST)
    void m();
}
""",
    }
    db = _build(tmp_path, None, extra)
    rows = _rows(db, "MATCH (c:Client) RETURN c.method, c.path, c.client_kind")
    assert rows
    assert any(row[0] == "POST" and row[1] == "/x" and row[2] == "feign_method" for row in rows)


def test_assign_endpoint_style_interface_emits_client_row(tmp_path: Path) -> None:
    """Mirror user AssignEndpoint: role/capability + JAX-RS-ish + @CodebaseHttpClient on no-body method.

    Uses unqualified ``feign_method`` (static import) and ``method = POST`` (javax.ws.rs.HttpMethod.POST).
    """
    extra = dict(_JAX_RS_AND_ROLE_STUBS)
    extra["p/AssignEndpoint.java"] = """package p;

import com.example.rag.*;
import static com.example.rag.CodebaseClientKind.feign_method;
import static javax.ws.rs.HttpMethod.POST;

import javax.ws.rs.Consumes;
import javax.ws.rs.POST;
import javax.ws.rs.PATH;
import javax.ws.rs.Produces;
import javax.ws.rs.core.MediaType;

@CodebaseRole(CodebaseRoleKind.CLIENT)
@CodebaseCapability(CodebaseCapabilityKind.HTTP_CLIENT)
@Consumes(MediaType.APPLICATION_JSON)
@Produces(MediaType.APPLICATION_JSON)
public interface AssignEndpoint {

    @POST
    @PATH("/operator/session/open")
    @CodebaseHttpClient(
        clientKind = feign_method,
        path = "/operator/session/open",
        method = POST
    )
    Object open(String request);
}
"""
    db = _build(tmp_path, None, extra)
    rows = _rows(
        db,
        "MATCH (c:Client) RETURN c.client_kind, c.path, c.method, c.source_layer, c.member_fqn",
    )
    assert rows, "expected at least one Client row for AssignEndpoint-style interface"
    assert any(
        row[0] == "feign_method"
        and row[1] == "/operator/session/open"
        and row[2] == "POST"
        and row[3] == "layer_c_source"
        and str(row[4]).endswith("#open(String)")
        for row in rows
    ), f"unexpected Client rows: {rows}"

    meta = _rows(
        db,
        "MATCH (m:GraphMeta) RETURN m.ontology_version, m.clients_total",
    )
    assert int(meta[0][0] or 0) == ONTOLOGY_VERSION
    assert int(meta[0][1] or 0) >= 1


def teardown_module() -> None:
    _load_brownfield_overrides.cache_clear()
    collect_annotation_meta_chain.cache_clear()
