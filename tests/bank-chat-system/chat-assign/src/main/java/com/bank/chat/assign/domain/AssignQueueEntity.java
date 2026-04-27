package com.bank.chat.assign.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.FetchType;
import javax.persistence.Id;
import javax.persistence.JoinColumn;
import javax.persistence.OneToOne;
import javax.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "assign_queue")
public class AssignQueueEntity {

    @Id
    @Column(columnDefinition = "uuid", nullable = false)
    private UUID id;

    @OneToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "assign_chat_id", nullable = false, unique = true)
    private AssignChatEntity assignChat;

    @Column(name = "enqueued_at", nullable = false)
    private Instant enqueuedAt;

    @Column(name = "priority_score", nullable = false)
    private int priorityScore;

    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
    }

    public AssignChatEntity getAssignChat() {
        return assignChat;
    }

    public void setAssignChat(AssignChatEntity assignChat) {
        this.assignChat = assignChat;
    }

    public Instant getEnqueuedAt() {
        return enqueuedAt;
    }

    public void setEnqueuedAt(Instant enqueuedAt) {
        this.enqueuedAt = enqueuedAt;
    }

    public int getPriorityScore() {
        return priorityScore;
    }

    public void setPriorityScore(int priorityScore) {
        this.priorityScore = priorityScore;
    }
}
