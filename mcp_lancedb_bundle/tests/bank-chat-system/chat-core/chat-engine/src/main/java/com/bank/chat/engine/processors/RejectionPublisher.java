package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.Client;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.stereotype.Component;

import java.util.UUID;

@Component
public class RejectionPublisher {

    private final FollowUpKafkaPublisher publisher;

    public RejectionPublisher(FollowUpKafkaPublisher publisher) {
        this.publisher = publisher;
    }

    public void publish(Client client, InternalEvent original, String reason) {
        String baseKey = original.getIdempotencyKey() != null ? original.getIdempotencyKey() : UUID.randomUUID().toString();
        InternalEvent rejection = InternalEvent.create(
                UUID.randomUUID().toString(),
                "reject-" + baseKey,
                client.getEpkId(),
                client.getConversationId(),
                EventType.SYSTEM_REJECTED_TRANSITION,
                reason,
                original.getMetadata()
        );
        publisher.publishIncoming(rejection);
    }
}
