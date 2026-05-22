package com.bank.chat.engine.pipeline;

import com.bank.chat.contracts.InternalEvent;
import org.springframework.stereotype.Component;

@Component
public class EventTypeFilter implements EventFilter<InternalEvent> {

    @Override
    public boolean test(InternalEvent input) {
        return input.getEventType() != null;
    }

    @Override
    public String name() {
        return "eventTypeFilter";
    }
}
