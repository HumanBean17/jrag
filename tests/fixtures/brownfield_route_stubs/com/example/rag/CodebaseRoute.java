package com.example.rag;

import java.lang.annotation.Repeatable;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Target({ElementType.METHOD})
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseRoutes.class)
public @interface CodebaseRoute {
    CodebaseRouteFrameworkKind framework();

    CodebaseRouteKind kind();

    String path() default "";

    String method() default "";

    String topic() default "";

    String broker() default "";
}
