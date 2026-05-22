package com.bank.chat.contracts.brownfield;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface CodebaseHttpClient {

    String targetService();

    String path();

    String method() default "GET";

    String clientKind() default "rest_template";
}
