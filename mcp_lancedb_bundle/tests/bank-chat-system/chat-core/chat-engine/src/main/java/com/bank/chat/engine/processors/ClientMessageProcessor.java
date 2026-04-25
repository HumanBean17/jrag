package com.bank.chat.engine.processors;

import com.bank.chat.contracts.AssignmentRequest;
import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.Client;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.assign.ChatAssignmentPort;
import com.bank.chat.engine.compliance.ComplianceScanner;
import com.bank.chat.engine.ingest.ProcessingContext;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import com.bank.chat.engine.policy.BusinessHoursPolicy;
import com.bank.chat.engine.policy.SessionPolicyEngine;
import com.bank.chat.engine.ratelimit.ClientMessageRateLimiter;
import com.bank.chat.engine.sla.SlaService;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Component
@Order(20)
public class ClientMessageProcessor implements EventProcessor {

    private final SessionPolicyEngine policyEngine;
    private final ClientMessageRateLimiter rateLimiter;
    private final ComplianceScanner complianceScanner;
    private final BusinessHoursPolicy businessHoursPolicy;
    private final SlaService slaService;
    private final ChatAssignmentPort chatAssignmentPort;
    private final FollowUpKafkaPublisher publisher;
    private final RejectionPublisher rejectionPublisher;

    public ClientMessageProcessor(
            SessionPolicyEngine policyEngine,
            ClientMessageRateLimiter rateLimiter,
            ComplianceScanner complianceScanner,
            BusinessHoursPolicy businessHoursPolicy,
            SlaService slaService,
            ChatAssignmentPort chatAssignmentPort,
            FollowUpKafkaPublisher publisher,
            RejectionPublisher rejectionPublisher
    ) {
        this.policyEngine = policyEngine;
        this.rateLimiter = rateLimiter;
        this.complianceScanner = complianceScanner;
        this.businessHoursPolicy = businessHoursPolicy;
        this.slaService = slaService;
        this.chatAssignmentPort = chatAssignmentPort;
        this.publisher = publisher;
        this.rejectionPublisher = rejectionPublisher;
    }

    @Override
    public boolean supports(EventType type) {
        return type == EventType.CLIENT_MESSAGE;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        Client client = ctx.getClient();
        ChatSession session = ctx.getSession();

        if (policyEngine.isTerminal(session)) {
            rejectionPublisher.publish(client, event, "session_closed");
            return;
        }

        if (!rateLimiter.allow(client.getEpkId())) {
            InternalEvent throttled = InternalEvent.create(
                    UUID.randomUUID().toString(),
                    "throttled-" + event.getIdempotencyKey(),
                    client.getEpkId(),
                    client.getConversationId(),
                    EventType.CLIENT_THROTTLED,
                    "rate_limit",
                    event.getMetadata()
            );
            publisher.publishIncoming(throttled);
            return;
        }

        ComplianceScanner.ComplianceScanResult scan = complianceScanner.scan(event.getMessage());
        if (scan.isFlagged()) {
            session.setComplianceHold(true);
            Map<String, String> md = new HashMap<>(event.getMetadata());
            md.put("reason", scan.getReasonCode());
            InternalEvent flagged = InternalEvent.create(
                    UUID.randomUUID().toString(),
                    "compliance-" + event.getIdempotencyKey(),
                    client.getEpkId(),
                    client.getConversationId(),
                    EventType.COMPLIANCE_FLAGGED,
                    scan.getReasonCode(),
                    md
            );
            publisher.publishComplianceReview(flagged);
            publisher.publishIncoming(flagged);
        }

        session.setLastActivityAt(Instant.now());
        session.nextMessageSeq();

        if (session.getFirstClientMessageAt() == null) {
            session.setFirstClientMessageAt(Instant.now());
            slaService.scheduleFirstResponseSla(session);
        }

        boolean within = businessHoursPolicy.isWithinBusinessHours(Instant.now());
        session.setAfterHoursQueued(!within);

        SessionStatus st = session.getStatus();
        if (st == SessionStatus.INITIAL) {
            session.setStatus(SessionStatus.AWAITING_ASSIGNMENT);
        } else if (st == SessionStatus.ACTIVE || st == SessionStatus.PENDING_CLIENT) {
            session.setStatus(SessionStatus.PENDING_OPERATOR);
        }

        if (!within) {
            InternalEvent deferred = InternalEvent.create(
                    UUID.randomUUID().toString(),
                    "defer-" + event.getIdempotencyKey(),
                    client.getEpkId(),
                    client.getConversationId(),
                    EventType.ASSIGNMENT_DEFERRED,
                    "after_hours",
                    event.getMetadata()
            );
            publisher.publishIncoming(deferred);
        }

        if (session.isComplianceHold()) {
            return;
        }

        if (session.getStatus() == SessionStatus.AWAITING_ASSIGNMENT) {
            chatAssignmentPort.requestAssignment(
                    buildAssignment(session, client, !within, "NEW_SESSION", event.getSplit()));
        }
    }

    private AssignmentRequest buildAssignment(
            ChatSession session, Client client, boolean afterHours, String reason, String split) {
        AssignmentRequest r = new AssignmentRequest();
        r.setCallbackCorrelationId(UUID.randomUUID().toString());
        r.setConversationId(client.getConversationId());
        r.setEpkId(client.getEpkId());
        r.setClientSegment(client.getClientSegment().name());
        r.setRiskFlagsJson(client.getRiskFlagsJson());
        r.setPriorityScore(priorityScore(session, client));
        r.setReason(reason);
        r.setSplit(split);
        r.setAfterHoursQueued(afterHours);
        r.setRequestedAt(Instant.now());
        return r;
    }

    private int priorityScore(ChatSession session, Client client) {
        int base = switch (client.getClientSegment()) {
            case VIP -> 120;
            case PRIVATE -> 70;
            case RETAIL -> 20;
        };
        return base + session.getEscalationLevel() * 25;
    }
}
