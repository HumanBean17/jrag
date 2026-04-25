package com.bank.chat.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.EnumType;
import javax.persistence.Enumerated;
import javax.persistence.FetchType;
import javax.persistence.Id;
import javax.persistence.PrePersist;
import javax.persistence.JoinColumn;
import javax.persistence.ManyToOne;
import javax.persistence.Table;
import javax.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(
        name = "session_sla_checkpoint",
        uniqueConstraints = @UniqueConstraint(
                name = "uq_session_checkpoint_due",
                columnNames = {"chat_session_id", "checkpoint_type", "due_at"}
        )
)
public class SessionSlaCheckpoint {

    @Id
    @Column(columnDefinition = "uuid", updatable = false, nullable = false)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "chat_session_id", nullable = false)
    private ChatSession chatSession;

    @Enumerated(EnumType.STRING)
    @Column(name = "checkpoint_type", nullable = false, length = 32)
    private SlaCheckpointType checkpointType;

    @Column(name = "due_at", nullable = false)
    private Instant dueAt;

    @Column(name = "breached_at")
    private Instant breachedAt;

    @Column(name = "escalation_level", nullable = false)
    private int escalationLevel;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt = Instant.now();

    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
    }

    public ChatSession getChatSession() {
        return chatSession;
    }

    public void setChatSession(ChatSession chatSession) {
        this.chatSession = chatSession;
    }

    public SlaCheckpointType getCheckpointType() {
        return checkpointType;
    }

    public void setCheckpointType(SlaCheckpointType checkpointType) {
        this.checkpointType = checkpointType;
    }

    public Instant getDueAt() {
        return dueAt;
    }

    public void setDueAt(Instant dueAt) {
        this.dueAt = dueAt;
    }

    public Instant getBreachedAt() {
        return breachedAt;
    }

    public void setBreachedAt(Instant breachedAt) {
        this.breachedAt = breachedAt;
    }

    public int getEscalationLevel() {
        return escalationLevel;
    }

    public void setEscalationLevel(int escalationLevel) {
        this.escalationLevel = escalationLevel;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    @PrePersist
    void prePersist() {
        if (id == null) {
            id = UUID.randomUUID();
        }
        if (createdAt == null) {
            createdAt = Instant.now();
        }
    }
}
