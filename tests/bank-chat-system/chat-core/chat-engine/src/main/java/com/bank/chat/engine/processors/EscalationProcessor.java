package com.bank.chat.engine.processors;

import com.bank.chat.contracts.AssignmentRequest;
import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.Client;
import com.bank.chat.engine.audit.AuditEventPublisher;
import com.bank.chat.engine.compliance.ComplianceScanner;
import com.bank.chat.engine.ingest.ProcessingContext;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import com.bank.chat.engine.notification.NotificationSender;
import com.bank.chat.engine.policy.BusinessHoursPolicy;
import com.bank.chat.engine.policy.SessionPolicyEngine;
import com.bank.chat.engine.ratelimit.ClientMessageRateLimiter;
import com.bank.chat.engine.sla.SlaService;
import com.bank.chat.engine.assign.ChatAssignmentPort;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Component
@Order(33)
public class EscalationProcessor implements EventProcessor {

    private final SlaService slaService;
    private final SessionPolicyEngine policyEngine;
    private final BusinessHoursPolicy businessHoursPolicy;
    private final ChatAssignmentPort chatAssignmentPort;
    private final FollowUpKafkaPublisher publisher;
    private final RejectionPublisher rejectionPublisher;
    private final ComplianceScanner complianceScanner;
    private final ClientMessageRateLimiter rateLimiter;
    private final NotificationSender emailNotificationSender;
    private final AuditEventPublisher auditEventPublisher;

    public EscalationProcessor(SlaService slaService,
                               SessionPolicyEngine policyEngine,
                               BusinessHoursPolicy businessHoursPolicy,
                               ChatAssignmentPort chatAssignmentPort,
                               FollowUpKafkaPublisher publisher,
                               RejectionPublisher rejectionPublisher,
                               ComplianceScanner complianceScanner,
                               ClientMessageRateLimiter rateLimiter,
                               NotificationSender emailNotificationSender,
                               AuditEventPublisher auditEventPublisher) {
        this.slaService = slaService;
        this.policyEngine = policyEngine;
        this.businessHoursPolicy = businessHoursPolicy;
        this.chatAssignmentPort = chatAssignmentPort;
        this.publisher = publisher;
        this.rejectionPublisher = rejectionPublisher;
        this.complianceScanner = complianceScanner;
        this.rateLimiter = rateLimiter;
        this.emailNotificationSender = emailNotificationSender;
        this.auditEventPublisher = auditEventPublisher;
    }

    @Override
    public boolean supports(EventType type) {
        return type == EventType.ESCALATION_REQUESTED;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        Client client = ctx.getClient();

        policyEngine.isTerminal(session);
        policyEngine.canMutateWhileClosed(session.getStatus(), true);
        businessHoursPolicy.isWithinBusinessHours(Instant.now());
        rateLimiter.allow(client.getEpkId());
        complianceScanner.scan(event.getMessage());
        slaService.scheduleFirstResponseSla(session);
        slaService.clearFirstResponseSla(session);

        AssignmentRequest request = new AssignmentRequest();
        request.setConversationId(client.getConversationId());
        request.setEpkId(client.getEpkId());
        request.setReason("ESCALATION");
        chatAssignmentPort.requestAssignment(request);

        publisher.publishIncoming(event);
        publisher.publishEscalation(event);
        publisher.publishOperatorNotification(event);
        rejectionPublisher.publish(client, event, "escalation_rejected");
        emailNotificationSender.send(client.getEpkId(), "Session escalated");
        auditEventPublisher.publishAuditEvent("ESCALATION", client.getEpkId(), Map.of());

        session.setEscalationLevel(session.getEscalationLevel() + 1);
    }
}
