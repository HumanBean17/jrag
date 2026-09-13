package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Repeatable;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Declares an inbound HTTP route (handler method) the framework introspector
 * cannot see. Replaces same-method built-in Spring mapping rows for the jrag
 * indexer (brownfield exclusivity).
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpRoutes.class)
public @interface CodebaseHttpRoute {
    String path();

    CodebaseHttpMethod method();
}
