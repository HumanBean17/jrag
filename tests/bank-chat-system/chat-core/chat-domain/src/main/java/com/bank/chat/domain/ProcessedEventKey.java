package com.bank.chat.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.Id;
import javax.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "processed_event")
public class ProcessedEventKey {

    @Id
    @Column(name = "idempotency_key", length = 256)
    private String idempotencyKey;

    @Column(name = "processed_at", nullable = false)
    private Instant processedAt = Instant.now();

    public String getIdempotencyKey() {
        return idempotencyKey;
    }

    public void setIdempotencyKey(String idempotencyKey) {
        this.idempotencyKey = idempotencyKey;
    }

    public Instant getProcessedAt() {
        return processedAt;
    }

    public void setProcessedAt(Instant processedAt) {
        this.processedAt = processedAt;
    }
}
