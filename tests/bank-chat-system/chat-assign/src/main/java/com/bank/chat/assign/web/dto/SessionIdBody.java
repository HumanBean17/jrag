package com.bank.chat.assign.web.dto;

import javax.validation.constraints.NotNull;
import java.util.UUID;

public class SessionIdBody {

    @NotNull
    private UUID sessionId;

    public UUID getSessionId() {
        return sessionId;
    }

    public void setSessionId(UUID sessionId) {
        this.sessionId = sessionId;
    }
}
