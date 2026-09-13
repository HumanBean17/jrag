package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Repeatable-container for {@link CodebaseCapability}; written by the compiler
 * when a type carries more than one capability.
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseCapabilities {
    CodebaseCapability[] value();
}
