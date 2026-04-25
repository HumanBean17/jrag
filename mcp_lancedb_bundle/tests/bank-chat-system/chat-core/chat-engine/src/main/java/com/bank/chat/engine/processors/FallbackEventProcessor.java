package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.ingest.ProcessingContext;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

@Component
@Order(Ordered.LOWEST_PRECEDENCE)
public class FallbackEventProcessor implements EventProcessor {

    @Override
    public boolean supports(EventType type) {
        return true;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        // default: rely on persisted chat_event row only
    }
}
