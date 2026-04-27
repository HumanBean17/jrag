package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.ClosedReason;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.ingest.ProcessingContext;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.time.Instant;

@Component
@Order(50)
public class CloseChatProcessor implements EventProcessor {

    @Override
    public boolean supports(EventType type) {
        return type == EventType.CLOSE_CHAT || type == EventType.CLOSE_CHAT_FINALIZE;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        if (event.getEventType() == EventType.CLOSE_CHAT_FINALIZE) {
            session.setStatus(SessionStatus.CLOSED);
            if (session.getClosedAt() == null) {
                session.setClosedAt(Instant.now());
            }
            return;
        }

        session.setStatus(SessionStatus.CLOSED);
        session.setClosedAt(Instant.now());
        session.setClosedReason(parseCloser(event.getCloserRole()));
    }

    private ClosedReason parseCloser(String role) {
        if (!StringUtils.hasText(role)) {
            return ClosedReason.SYSTEM;
        }
        try {
            return ClosedReason.valueOf(role.trim().toUpperCase());
        } catch (IllegalArgumentException ex) {
            return ClosedReason.SYSTEM;
        }
    }
}
