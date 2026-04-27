package com.bank.chat.engine.ingest;

import com.bank.chat.domain.ChatEvent;
import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.Client;

public class ProcessingContext {

    private final Client client;
    private final ChatSession session;
    private final ChatEvent persistedEvent;

    public ProcessingContext(Client client, ChatSession session, ChatEvent persistedEvent) {
        this.client = client;
        this.session = session;
        this.persistedEvent = persistedEvent;
    }

    public Client getClient() {
        return client;
    }

    public ChatSession getSession() {
        return session;
    }

    public ChatEvent getPersistedEvent() {
        return persistedEvent;
    }
}
