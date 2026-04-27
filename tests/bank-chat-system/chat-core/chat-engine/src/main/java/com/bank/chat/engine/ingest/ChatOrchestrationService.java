package com.bank.chat.engine.ingest;

import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.ChatEvent;
import com.bank.chat.domain.ChatEventRepository;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.ChatSessionRepository;
import com.bank.chat.domain.Client;
import com.bank.chat.domain.ClientRepository;
import com.bank.chat.domain.ClientSegment;
import com.bank.chat.domain.ProcessedEventKey;
import com.bank.chat.domain.ProcessedEventKeyRepository;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.processors.EventProcessorRegistry;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Service
public class ChatOrchestrationService {

    private final ClientRepository clientRepository;
    private final ChatSessionRepository chatSessionRepository;
    private final ProcessedEventKeyRepository processedEventKeyRepository;
    private final ChatEventRepository chatEventRepository;
    private final EventProcessorRegistry processorRegistry;

    public ChatOrchestrationService(
            ClientRepository clientRepository,
            ChatSessionRepository chatSessionRepository,
            ProcessedEventKeyRepository processedEventKeyRepository,
            ChatEventRepository chatEventRepository,
            EventProcessorRegistry processorRegistry
    ) {
        this.clientRepository = clientRepository;
        this.chatSessionRepository = chatSessionRepository;
        this.processedEventKeyRepository = processedEventKeyRepository;
        this.chatEventRepository = chatEventRepository;
        this.processorRegistry = processorRegistry;
    }

    @Transactional
    public void handle(InternalEvent event) {
        String key = event.getIdempotencyKey();
        if (key != null && processedEventKeyRepository.existsById(key)) {
            return;
        }

        Client client = findOrCreateClient(event);
        ChatSession session = findOrCreateSession(client);
        ChatEvent row = persistChatEvent(client, session, event);

        processorRegistry.find(event.getEventType())
                .ifPresent(p -> p.process(new ProcessingContext(client, session, row), event));

        if (key != null) {
            ProcessedEventKey pe = new ProcessedEventKey();
            pe.setIdempotencyKey(key);
            pe.setProcessedAt(Instant.now());
            processedEventKeyRepository.save(pe);
        }
    }

    private Client findOrCreateClient(InternalEvent event) {
        return clientRepository.findByConversationId(event.getConversationId())
                .map(existing -> refreshProfileHints(existing, event))
                .orElseGet(() -> clientRepository.save(newClientFrom(event)));
    }

    private Client refreshProfileHints(Client existing, InternalEvent event) {
        Map<String, String> md = event.getMetadata();
        if (md != null) {
            if (md.containsKey("first_name")) {
                existing.setFirstName(md.get("first_name"));
            }
            if (md.containsKey("last_name")) {
                existing.setLastName(md.get("last_name"));
            }
            if (md.containsKey("segment")) {
                existing.setClientSegment(parseSegment(md.get("segment")));
            }
            if (md.containsKey("risk_flags")) {
                existing.setRiskFlagsJson(md.get("risk_flags"));
            }
        }
        return clientRepository.save(existing);
    }

    private Client newClientFrom(InternalEvent event) {
        Client c = new Client();
        c.setConversationId(event.getConversationId());
        c.setEpkId(event.getEpkId());
        Map<String, String> md = event.getMetadata();
        if (md != null) {
            c.setFirstName(md.get("first_name"));
            c.setLastName(md.get("last_name"));
            if (md.containsKey("segment")) {
                c.setClientSegment(parseSegment(md.get("segment")));
            } else {
                c.setClientSegment(ClientSegment.RETAIL);
            }
            if (md.containsKey("risk_flags")) {
                c.setRiskFlagsJson(md.get("risk_flags"));
            }
        } else {
            c.setClientSegment(ClientSegment.RETAIL);
        }
        return c;
    }

    private ClientSegment parseSegment(String raw) {
        if (raw == null) {
            return ClientSegment.RETAIL;
        }
        try {
            return ClientSegment.valueOf(raw.trim().toUpperCase());
        } catch (IllegalArgumentException ex) {
            return ClientSegment.RETAIL;
        }
    }

    private ChatSession findOrCreateSession(Client client) {
        return chatSessionRepository.findByClient_Id(client.getId())
                .orElseGet(() -> {
                    ChatSession s = new ChatSession();
                    s.setClient(client);
                    s.setStatus(SessionStatus.INITIAL);
                    s.setLastActivityAt(Instant.now());
                    return chatSessionRepository.save(s);
                });
    }

    private ChatEvent persistChatEvent(Client client, ChatSession session, InternalEvent event) {
        ChatEvent row = new ChatEvent();
        row.setClient(client);
        row.setChatSession(session);
        row.setEventType(event.getEventType());
        row.setEventMessage(event.getMessage());
        row.setCorrelationId(event.getCorrelationId());
        row.setIdempotencyKey(event.getIdempotencyKey());
        row.setSource("kafka");
        return chatEventRepository.save(row);
    }
}
