package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.ingest.ProcessingContext;

public interface EventProcessor {

    boolean supports(EventType type);

    void process(ProcessingContext ctx, InternalEvent event);
}
