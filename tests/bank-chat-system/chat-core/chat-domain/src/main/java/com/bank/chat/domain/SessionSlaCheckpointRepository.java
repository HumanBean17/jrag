package com.bank.chat.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface SessionSlaCheckpointRepository extends JpaRepository<SessionSlaCheckpoint, UUID> {

    Optional<SessionSlaCheckpoint> findByChatSession_IdAndCheckpointTypeAndDueAt(
            UUID sessionId,
            SlaCheckpointType type,
            java.time.Instant dueAt
    );

    void deleteByChatSession_Id(UUID sessionId);
}
