package com.bank.chat.engine.sla;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.ClientSegment;
import com.bank.chat.domain.SessionSlaCheckpoint;
import com.bank.chat.domain.SessionSlaCheckpointRepository;
import com.bank.chat.domain.SlaCheckpointType;
import com.bank.chat.engine.config.ChatEngineProperties;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Service
public class SlaService {

    private final SessionSlaCheckpointRepository checkpointRepository;
    private final ChatEngineProperties properties;
    private final FollowUpKafkaPublisher publisher;

    public SlaService(
            SessionSlaCheckpointRepository checkpointRepository,
            ChatEngineProperties properties,
            FollowUpKafkaPublisher publisher
    ) {
        this.checkpointRepository = checkpointRepository;
        this.properties = properties;
        this.publisher = publisher;
    }

    public void scheduleFirstResponseSla(ChatSession session) {
        if (session.getFirstOperatorResponseAt() != null) {
            return;
        }
        ClientSegment segment = session.getClient().getClientSegment();
        long seconds = resolveSlaSeconds(segment);
        Instant due = Instant.now().plusSeconds(seconds);
        session.setSlaFirstResponseDeadlineAt(due);

        SessionSlaCheckpoint checkpoint = new SessionSlaCheckpoint();
        checkpoint.setChatSession(session);
        checkpoint.setCheckpointType(SlaCheckpointType.FIRST_RESPONSE);
        checkpoint.setDueAt(due);
        checkpoint.setEscalationLevel(session.getEscalationLevel());
        checkpointRepository.save(checkpoint);
    }

    private long resolveSlaSeconds(ClientSegment segment) {
        Map<String, Long> map = properties.getSlaFirstResponseSeconds();
        Long sec = map.get(segment.name());
        return sec != null ? sec : map.getOrDefault("RETAIL", 300L);
    }

    @Transactional
    public void clearFirstResponseSla(ChatSession session) {
        session.setSlaFirstResponseDeadlineAt(null);
        checkpointRepository.deleteByChatSession_Id(session.getId());
    }

    @Transactional
    public void markBreachedAndEscalate(ChatSession session) {
        Instant due = session.getSlaFirstResponseDeadlineAt();
        if (due == null) {
            return;
        }
        SessionSlaCheckpoint checkpoint = checkpointRepository
                .findByChatSession_IdAndCheckpointTypeAndDueAt(
                        session.getId(), SlaCheckpointType.FIRST_RESPONSE, due
                )
                .orElse(null);
        if (checkpoint == null) {
            return;
        }
        if (checkpoint.getBreachedAt() != null) {
            return;
        }
        checkpoint.setBreachedAt(Instant.now());
        checkpointRepository.save(checkpoint);
        session.setEscalationLevel(session.getEscalationLevel() + 1);
        session.setSlaFirstResponseDeadlineAt(null);

        InternalEvent breached = InternalEvent.create(
                UUID.randomUUID().toString(),
                "sla-breach-" + session.getId() + "-" + due,
                session.getClient().getEpkId(),
                session.getClient().getConversationId(),
                EventType.SLA_BREACHED,
                "first_response",
                null
        );
        publisher.publishIncoming(breached);

        InternalEvent escalation = InternalEvent.create(
                UUID.randomUUID().toString(),
                "escalation-" + session.getId() + "-" + due,
                session.getClient().getEpkId(),
                session.getClient().getConversationId(),
                EventType.ESCALATION_REQUESTED,
                "level=" + session.getEscalationLevel(),
                null
        );
        publisher.publishEscalation(escalation);
    }
}
