package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.ingest.ProcessingContext;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

@Component
@Order(70)
public class AckProcessor implements EventProcessor {

    @Override
    public boolean supports(EventType type) {
        return type == EventType.ACK;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        // explicit no-op: audit trail already persisted
    }
}
