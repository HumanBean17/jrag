package io.github.humanbean17.jrag.annotations;

/**
 * Capabilities a type can declare for the jrag indexer (additive to the role;
 * never replaces it). Mirrors the parser's {@code VALID_CAPABILITIES}
 * vocabulary. Usage documentation: jrag {@code docs/CONFIGURATION.md} section 4.3.
 */
public enum CodebaseCapabilityKind {
    MESSAGE_LISTENER,
    MESSAGE_PRODUCER,
    HTTP_CLIENT,
    SCHEDULED_TASK,
    EXCEPTION_HANDLER
}
