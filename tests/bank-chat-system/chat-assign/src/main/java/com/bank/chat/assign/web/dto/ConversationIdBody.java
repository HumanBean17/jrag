package com.bank.chat.assign.web.dto;

import javax.validation.constraints.NotBlank;

public class ConversationIdBody {

    @NotBlank
    private String conversationId;

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }
}
