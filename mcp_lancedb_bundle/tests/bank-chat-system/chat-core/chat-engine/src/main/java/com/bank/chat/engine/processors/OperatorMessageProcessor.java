package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.ingest.ProcessingContext;
import com.bank.chat.engine.sla.SlaService;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.time.Instant;

@Component
@Order(40)
public class OperatorMessageProcessor implements EventProcessor {

    private final SlaService slaService;

    public OperatorMessageProcessor(SlaService slaService) {
        this.slaService = slaService;
    }

    @Override
    public boolean supports(EventType type) {
        return type == EventType.OPERATOR_MESSAGE;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        if (session.isComplianceHold()) {
            String force = event.getMetadata() != null ? event.getMetadata().get("force") : null;
            if (!"true".equalsIgnoreCase(force)) {
                return;
            }
        }

        if (session.getFirstOperatorResponseAt() == null) {
            session.setFirstOperatorResponseAt(Instant.now());
            slaService.clearFirstResponseSla(session);
        }

        session.setLastActivityAt(Instant.now());
        session.nextMessageSeq();

        if (session.getStatus() == SessionStatus.ASSIGNED || session.getStatus() == SessionStatus.AWAITING_ASSIGNMENT) {
            session.setStatus(SessionStatus.ACTIVE);
        } else if (session.getStatus() == SessionStatus.PENDING_OPERATOR) {
            session.setStatus(SessionStatus.ACTIVE);
        }

        session.setStatus(SessionStatus.PENDING_CLIENT);
    }
}
