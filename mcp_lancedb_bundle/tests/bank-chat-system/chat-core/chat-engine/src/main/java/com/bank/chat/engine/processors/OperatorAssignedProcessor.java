package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.ingest.ProcessingContext;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.util.UUID;

@Component
@Order(30)
public class OperatorAssignedProcessor implements EventProcessor {

    private final FollowUpKafkaPublisher publisher;

    public OperatorAssignedProcessor(FollowUpKafkaPublisher publisher) {
        this.publisher = publisher;
    }

    @Override
    public boolean supports(EventType type) {
        return type == EventType.OPERATOR_ASSIGNED;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        if (StringUtils.hasText(event.getOperatorId())) {
            session.setAssignedOperatorId(event.getOperatorId());
        }
        if (session.getStatus() == SessionStatus.AWAITING_ASSIGNMENT) {
            session.setStatus(SessionStatus.ASSIGNED);
        }

        InternalEvent done = InternalEvent.create(
                UUID.randomUUID().toString(),
                "assigned-done-" + event.getIdempotencyKey(),
                ctx.getClient().getEpkId(),
                ctx.getClient().getConversationId(),
                EventType.ASSIGNMENT_COMPLETED,
                session.getAssignedOperatorId(),
                event.getMetadata()
        );
        publisher.publishIncoming(done);
    }
}
