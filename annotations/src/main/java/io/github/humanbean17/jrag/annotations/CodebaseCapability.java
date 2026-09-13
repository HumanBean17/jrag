package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Repeatable;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Declares one capability of a type for the jrag indexer. Repeatable: a type
 * may carry several capabilities; they augment the role, never replace it.
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseCapabilities.class)
public @interface CodebaseCapability {
    CodebaseCapabilityKind value();
}
