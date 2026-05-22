package com.bank.chat.assign.web.dto;

public record TransferResult(
    boolean success,
    String conversationId,
    String newOperatorId
) {}
