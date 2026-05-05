package com.example.rag;

import java.lang.annotation.ElementType;
import java.lang.annotation.Repeatable;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseClients.class)
public @interface CodebaseClient {
    String clientKind();
    String targetService() default "";
    String path() default "";
    String method() default "";
}
