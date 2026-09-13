package io.github.humanbean17.jrag.annotations;

/**
 * Outbound HTTP client mechanisms recognized by the jrag indexer. Mirrors the
 * parser's {@code VALID_CLIENT_KINDS} vocabulary.
 */
public enum CodebaseClientKind {
    feign_method,
    rest_template,
    web_client
}
