package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Repeatable-container for {@link CodebaseHttpRoute}; written by the compiler
 * when a method declares more than one route.
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseHttpRoutes {
    CodebaseHttpRoute[] value();
}
