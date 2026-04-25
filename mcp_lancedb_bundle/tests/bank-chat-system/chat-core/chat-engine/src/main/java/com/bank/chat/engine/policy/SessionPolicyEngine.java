package com.bank.chat.engine.policy;

import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.config.ChatEngineProperties;

import java.time.Instant;
import java.time.temporal.ChronoUnit;

public class SessionPolicyEngine {

    private final ChatEngineProperties properties;

    public SessionPolicyEngine(ChatEngineProperties properties) {
        this.properties = properties;
    }

    public boolean isTerminal(ChatSession session) {
        return session.getStatus() == SessionStatus.CLOSED;
    }

    public boolean isReopenAllowed(ChatSession session) {
        if (session.getStatus() != SessionStatus.CLOSED) {
            return false;
        }
        Instant closedAt = session.getClosedAt();
        if (closedAt == null) {
            return false;
        }
        Instant limit = closedAt.plus(properties.getReopenWindowHours(), ChronoUnit.HOURS);
        return !Instant.now().isAfter(limit);
    }

    public boolean canMutateWhileClosed(SessionStatus status, boolean reopenFlow) {
        if (status != SessionStatus.CLOSED) {
            return true;
        }
        return reopenFlow;
    }
}
