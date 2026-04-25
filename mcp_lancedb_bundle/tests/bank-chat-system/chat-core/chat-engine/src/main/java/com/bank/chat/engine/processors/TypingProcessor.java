package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.engine.ingest.ProcessingContext;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.temporal.ChronoUnit;

@Component
@Order(60)
public class TypingProcessor implements EventProcessor {

    @Override
    public boolean supports(EventType type) {
        return type == EventType.OPERATOR_TYPING;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        session.setTypingUntil(Instant.now().plus(5, ChronoUnit.SECONDS));
    }
}
