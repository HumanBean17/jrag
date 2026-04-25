package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.ingest.ProcessingContext;
import com.bank.chat.engine.policy.SessionPolicyEngine;
import com.bank.chat.engine.sla.SlaService;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.time.Instant;

@Component
@Order(55)
public class SessionReopenProcessor implements EventProcessor {

    private final SessionPolicyEngine policyEngine;
    private final RejectionPublisher rejectionPublisher;
    private final SlaService slaService;

    public SessionReopenProcessor(
            SessionPolicyEngine policyEngine,
            RejectionPublisher rejectionPublisher,
            SlaService slaService
    ) {
        this.policyEngine = policyEngine;
        this.rejectionPublisher = rejectionPublisher;
        this.slaService = slaService;
    }

    @Override
    public boolean supports(EventType type) {
        return type == EventType.SESSION_REOPEN;
    }

       @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        if (session.getStatus() != SessionStatus.CLOSED) {
            rejectionPublisher.publish(ctx.getClient(), event, "session_not_closed");
            return;
        }
        if (!policyEngine.isReopenAllowed(session)) {
            rejectionPublisher.publish(ctx.getClient(), event, "reopen_window_expired");
            return;
        }

        slaService.clearFirstResponseSla(session);
        session.setStatus(SessionStatus.INITIAL);
        session.setClosedAt(null);
        session.setClosedReason(null);
        session.setAssignedOperatorId(null);
        session.setFirstClientMessageAt(null);
        session.setFirstOperatorResponseAt(null);
        session.setSlaFirstResponseDeadlineAt(null);
        session.setComplianceHold(false);
        session.setAfterHoursQueued(false);
        session.setLastActivityAt(Instant.now());
    }
}
