package com.bank.chat.assign.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.FetchType;
import javax.persistence.Id;
import javax.persistence.JoinColumn;
import javax.persistence.ManyToOne;
import javax.persistence.PrePersist;
import javax.persistence.PreUpdate;
import javax.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "assign_chat")
public class AssignChatEntity {

    @Id
    @Column(columnDefinition = "uuid", nullable = false)
    private UUID id;

    @Column(name = "conversation_id", nullable = false, unique = true, length = 128)
    private String conversationId;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "operator_session_id")
    private AssignOperatorSessionEntity operatorSession;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "split_id", nullable = false)
    private AssignSplitEntity split;

    @Column(name = "epk_id", length = 128)
    private String epkId;

    @Column(name = "priority_score", nullable = false)
    private int priorityScore;

    @Column(length = 256)
    private String reason;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist
    void prePersist() {
        Instant now = Instant.now();
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = Instant.now();
    }

    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
    }

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }

    public AssignOperatorSessionEntity getOperatorSession() {
        return operatorSession;
    }

    public void setOperatorSession(AssignOperatorSessionEntity operatorSession) {
        this.operatorSession = operatorSession;
    }

    public AssignSplitEntity getSplit() {
        return split;
    }

    public void setSplit(AssignSplitEntity split) {
        this.split = split;
    }

    public String getEpkId() {
        return epkId;
    }

    public void setEpkId(String epkId) {
        this.epkId = epkId;
    }

    public int getPriorityScore() {
        return priorityScore;
    }

    public void setPriorityScore(int priorityScore) {
        this.priorityScore = priorityScore;
    }

    public String getReason() {
        return reason;
    }

    public void setReason(String reason) {
        this.reason = reason;
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
}
