package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.ingest.ProcessingContext;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

@Component
@Order(45)
public class ComplianceHoldProcessor implements EventProcessor {

    @Override
    public boolean supports(EventType type) {
        return type == EventType.COMPLIANCE_HOLD || type == EventType.COMPLIANCE_RELEASE;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        if (event.getEventType() == EventType.COMPLIANCE_HOLD) {
            session.setComplianceHold(true);
            if (session.getStatus() == SessionStatus.ACTIVE) {
                session.setStatus(SessionStatus.ON_HOLD);
            }
        } else {
            session.setComplianceHold(false);
            if (session.getStatus() == SessionStatus.ON_HOLD) {
                session.setStatus(SessionStatus.ACTIVE);
            }
        }
    }
}
