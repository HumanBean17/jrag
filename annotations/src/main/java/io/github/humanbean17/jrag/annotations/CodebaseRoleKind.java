package io.github.humanbean17.jrag.annotations;

/**
 * Roles a type can declare for the jrag indexer. Mirrors the parser's
 * {@code VALID_ROLES} vocabulary. Usage documentation: jrag
 * {@code docs/CONFIGURATION.md} section 4.3.
 */
public enum CodebaseRoleKind {
    CONTROLLER,
    SERVICE,
    REPOSITORY,
    COMPONENT,
    CONFIG,
    ENTITY,
    CLIENT,
    MAPPER,
    DTO
}
