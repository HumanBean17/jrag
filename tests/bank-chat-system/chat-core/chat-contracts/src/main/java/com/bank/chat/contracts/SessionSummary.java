package com.bank.chat.contracts;

import java.time.Instant;

public record SessionSummary(
    String conversationId,
    String status,
    String assignedOperator,
    Instant createdAt
) {}
