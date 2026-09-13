package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Declares the primary role of a type for the jrag indexer when built-in
 * stereotypes do not apply (brownfield role override).
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseRole {
    CodebaseRoleKind value();
}
