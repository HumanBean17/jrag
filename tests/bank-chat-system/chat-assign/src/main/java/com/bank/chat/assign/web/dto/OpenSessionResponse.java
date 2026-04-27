package com.bank.chat.assign.web.dto;

import java.util.UUID;

public class OpenSessionResponse {

    private final UUID sessionId;

    public OpenSessionResponse(UUID sessionId) {
        this.sessionId = sessionId;
    }

    public UUID getSessionId() {
        return sessionId;
    }
}
