package com.bank.chat.engine.pipeline;

public interface EventFilter<T> {
    boolean test(T input);
    String name();
}
