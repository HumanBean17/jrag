package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Repeatable-container for {@link CodebaseProducer}; written by the compiler
 * when a method declares more than one publish site.
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseProducers {
    CodebaseProducer[] value();
}
