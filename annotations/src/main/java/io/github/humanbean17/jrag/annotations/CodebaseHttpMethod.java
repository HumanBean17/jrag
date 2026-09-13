package io.github.humanbean17.jrag.annotations;

/**
 * HTTP methods accepted by {@link CodebaseHttpRoute} (inbound) and
 * {@link CodebaseHttpClient} (outbound). Shared by both directions.
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
