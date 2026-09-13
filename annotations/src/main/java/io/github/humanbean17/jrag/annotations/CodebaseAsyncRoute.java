package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Repeatable;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Declares an inbound async listener (topic consumer) the framework
 * introspector cannot see. Replaces same-method built-in {@code @KafkaListener}
 * extraction for the jrag indexer.
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseAsyncRoutes.class)
public @interface CodebaseAsyncRoute {
    String topic();
}
