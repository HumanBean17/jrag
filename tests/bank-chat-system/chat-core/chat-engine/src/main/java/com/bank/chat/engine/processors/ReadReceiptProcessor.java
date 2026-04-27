package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.engine.ingest.ProcessingContext;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Component
@Order(35)
public class ReadReceiptProcessor implements EventProcessor {

    private final FollowUpKafkaPublisher publisher;

    public ReadReceiptProcessor(FollowUpKafkaPublisher publisher) {
        this.publisher = publisher;
    }

    @Override
    public boolean supports(EventType type) {
        return type == EventType.CLIENT_READ_MESSAGE || type == EventType.OPERATOR_READ_MESSAGE;
    }

    @Override
    public void process(ProcessingContext ctx, InternalEvent event) {
        ChatSession session = ctx.getSession();
        long seq = parseSeq(event);
        if (event.getEventType() == EventType.CLIENT_READ_MESSAGE) {
            session.setLastReadByClientSeq(Math.max(session.getLastReadByClientSeq(), seq));
        } else {
            session.setLastReadByOperatorSeq(Math.max(session.getLastReadByOperatorSeq(), seq));
        }

        Map<String, String> md = new HashMap<>(event.getMetadata());
        md.put("seq", Long.toString(seq));
        md.put("reader", event.getEventType().name());

        InternalEvent receipt = InternalEvent.create(
                UUID.randomUUID().toString(),
                "read-" + event.getIdempotencyKey(),
                ctx.getClient().getEpkId(),
                ctx.getClient().getConversationId(),
                EventType.READ_RECEIPT,
                event.getMessage(),
                md
        );
        publisher.publishOperatorNotification(receipt);
    }

    private long parseSeq(InternalEvent event) {
        String raw = event.getMetadata() != null ? event.getMetadata().get("seq") : null;
        if (raw == null || raw.isBlank()) {
            return 0L;
        }
        try {
            return Long.parseLong(raw.trim());
        } catch (NumberFormatException ex) {
            return 0L;
        }
    }
}
