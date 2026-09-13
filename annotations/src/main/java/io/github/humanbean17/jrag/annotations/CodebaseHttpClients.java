package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Repeatable-container for {@link CodebaseHttpClient}; written by the compiler
 * when a method performs more than one declared outbound HTTP call.
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseHttpClients {
    CodebaseHttpClient[] value();
}
