package com.example.rag;

/**
 * HTTP verbs supported as the `method` field on
 * {@code @CodebaseHttpRoute} and {@code @CodebaseHttpClient}.
 *
 * Closed value set — see propose/HTTP-ROUTE-METHOD-ENUM-PROPOSE.md.
 * Adding a value is a breaking-change amendment to the enum file
 * plus a re-extract of every annotated codebase.
 *
 * Used identically by inbound (route) and outbound (client) HTTP
 * annotations; the value set is the same on both sides because
 * the wire protocol is the same.
 */
public enum CodebaseHttpMethod {
    GET,
    POST,
    PUT,
    PATCH,
    DELETE,
    HEAD,
    OPTIONS
}
