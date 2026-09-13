package io.github.humanbean17.jrag.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Repeatable;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Declares an outbound HTTP call site the built-in extraction cannot see
 * (custom RestTemplate/WebClient wrappers, Feign-likes). Brownfield outgoing
 * calls replace same-method built-in outgoing calls to avoid double-counting.
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpClients.class)
public @interface CodebaseHttpClient {
    CodebaseClientKind clientKind();

    String targetService() default "";

    String path() default "";

    CodebaseHttpMethod method();
}
