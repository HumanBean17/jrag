package com.bank.chat.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.EnumType;
import javax.persistence.Enumerated;
import javax.persistence.FetchType;
import javax.persistence.Id;
import javax.persistence.PrePersist;
import javax.persistence.PreUpdate;
import javax.persistence.JoinColumn;
import javax.persistence.ManyToOne;
import javax.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "chat_session")
public class ChatSession {

    @Id
    @Column(columnDefinition = "uuid", updatable = false, nullable = false)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "client_id", nullable = false)
    private Client client;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 40)
    private SessionStatus status = SessionStatus.INITIAL;

    @Column(name = "assigned_operator_id", length = 128)
    private String assignedOperatorId;

    @Column(name = "first_client_message_at")
    private Instant firstClientMessageAt;

    @Column(name = "first_operator_response_at")
    private Instant firstOperatorResponseAt;

    @Column(name = "sla_first_response_deadline_at")
    private Instant slaFirstResponseDeadlineAt;

    @Column(name = "last_activity_at")
    private Instant lastActivityAt;

    @Column(name = "compliance_hold", nullable = false)
    private boolean complianceHold;

    @Column(name = "after_hours_queued", nullable = false)
    private boolean afterHoursQueued;

    @Enumerated(EnumType.STRING)
    @Column(name = "closed_reason", length = 32)
    private ClosedReason closedReason;

    @Column(name = "closed_at")
    private Instant closedAt;

    @Column(name = "last_read_by_client_seq", nullable = false)
    private long lastReadByClientSeq;

    @Column(name = "last_read_by_operator_seq", nullable = false)
    private long lastReadByOperatorSeq;

    @Column(name = "message_seq", nullable = false)
    private long messageSeq;

    @Column(name = "typing_until")
    private Instant typingUntil;

    @Column(name = "escalation_level", nullable = false)
    private int escalationLevel;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt = Instant.now();

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt = Instant.now();

    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
    }

    public Client getClient() {
        return client;
    }

    public void setClient(Client client) {
        this.client = client;
    }

    public SessionStatus getStatus() {
        return status;
    }

    public void setStatus(SessionStatus status) {
        this.status = status;
    }

    public String getAssignedOperatorId() {
        return assignedOperatorId;
    }

    public void setAssignedOperatorId(String assignedOperatorId) {
        this.assignedOperatorId = assignedOperatorId;
    }

    public Instant getFirstClientMessageAt() {
        return firstClientMessageAt;
    }

    public void setFirstClientMessageAt(Instant firstClientMessageAt) {
        this.firstClientMessageAt = firstClientMessageAt;
    }

    public Instant getFirstOperatorResponseAt() {
        return firstOperatorResponseAt;
    }

    public void setFirstOperatorResponseAt(Instant firstOperatorResponseAt) {
        this.firstOperatorResponseAt = firstOperatorResponseAt;
    }

    public Instant getSlaFirstResponseDeadlineAt() {
        return slaFirstResponseDeadlineAt;
    }

    public void setSlaFirstResponseDeadlineAt(Instant slaFirstResponseDeadlineAt) {
        this.slaFirstResponseDeadlineAt = slaFirstResponseDeadlineAt;
    }

    public Instant getLastActivityAt() {
        return lastActivityAt;
    }

    public void setLastActivityAt(Instant lastActivityAt) {
        this.lastActivityAt = lastActivityAt;
    }

    public boolean isComplianceHold() {
        return complianceHold;
    }

    public void setComplianceHold(boolean complianceHold) {
        this.complianceHold = complianceHold;
    }

    public boolean isAfterHoursQueued() {
        return afterHoursQueued;
    }

    public void setAfterHoursQueued(boolean afterHoursQueued) {
        this.afterHoursQueued = afterHoursQueued;
    }

    public ClosedReason getClosedReason() {
        return closedReason;
    }

    public void setClosedReason(ClosedReason closedReason) {
        this.closedReason = closedReason;
    }

    public Instant getClosedAt() {
        return closedAt;
    }

    public void setClosedAt(Instant closedAt) {
        this.closedAt = closedAt;
    }

    public long getLastReadByClientSeq() {
        return lastReadByClientSeq;
    }

    public void setLastReadByClientSeq(long lastReadByClientSeq) {
        this.lastReadByClientSeq = lastReadByClientSeq;
    }

    public long getLastReadByOperatorSeq() {
        return lastReadByOperatorSeq;
    }

    public void setLastReadByOperatorSeq(long lastReadByOperatorSeq) {
        this.lastReadByOperatorSeq = lastReadByOperatorSeq;
    }

    public long getMessageSeq() {
        return messageSeq;
    }

    public void setMessageSeq(long messageSeq) {
        this.messageSeq = messageSeq;
    }

    public Instant getTypingUntil() {
        return typingUntil;
    }

    public void setTypingUntil(Instant typingUntil) {
        this.typingUntil = typingUntil;
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

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(Instant updatedAt) {
        this.updatedAt = updatedAt;
    }

    public long nextMessageSeq() {
        this.messageSeq += 1;
        return this.messageSeq;
    }

    @PrePersist
    void prePersist() {
        Instant now = Instant.now();
        if (id == null) {
            id = UUID.randomUUID();
        }
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = Instant.now();
    }
}
