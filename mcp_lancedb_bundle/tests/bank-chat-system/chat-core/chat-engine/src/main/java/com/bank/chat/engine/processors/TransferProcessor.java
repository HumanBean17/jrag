package com.bank.chat.engine.processors;

import com.bank.chat.contracts.AssignmentRequest;
import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.Client;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.assign.ChatAssignmentPort;
import com.bank.chat.engine.ingest.ProcessingContext;
import com.bank.chat.engine.policy.BusinessHoursPolicy;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.time.Instant;
import java.util.UUID;

@Component
@Order(32)
public class TransferProcessor implements EventProcessor {

    private final ChatAssignmentPort chatAssignmentPort;
    private final BusinessHoursPolicy businessHoursPolicy;

    public TransferProcessor(ChatAssignmentPort chatAssignmentPort, BusinessHoursPolicy businessHoursPolicy) {
        this.chatAssignmentPort = chatAssignmentPort;
        this.businessHoursPolicy = businessHoursPolicy;
    }

    @Override
    public boolean supports(EventType type) {
        return type == EventType.OPERATOR_TRANSFER_REQUESTED || type == EventType.OPERATOR_TRANSFER_COMPLETED;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        Client client = ctx.getClient();

        if (event.getEventType() == EventType.OPERATOR_TRANSFER_REQUESTED) {
            session.setAssignedOperatorId(null);
            session.setStatus(SessionStatus.AWAITING_ASSIGNMENT);
            boolean within = businessHoursPolicy.isWithinBusinessHours(Instant.now());
            chatAssignmentPort.requestAssignment(buildAssignment(session, client, !within, "TRANSFER"));
        } else if (StringUtils.hasText(event.getOperatorId())) {
            session.setAssignedOperatorId(event.getOperatorId());
            session.setStatus(SessionStatus.ASSIGNED);
        }
    }

    private AssignmentRequest buildAssignment(ChatSession session, Client client, boolean afterHours, String reason) {
        AssignmentRequest r = new AssignmentRequest();
        r.setCallbackCorrelationId(UUID.randomUUID().toString());
        r.setConversationId(client.getConversationId());
        r.setEpkId(client.getEpkId());
        r.setClientSegment(client.getClientSegment().name());
        r.setRiskFlagsJson(client.getRiskFlagsJson());
        r.setPriorityScore(80 + session.getEscalationLevel() * 10);
        r.setReason(reason);
        r.setAfterHoursQueued(afterHours);
        r.setRequestedAt(Instant.now());
        return r;
    }
}
