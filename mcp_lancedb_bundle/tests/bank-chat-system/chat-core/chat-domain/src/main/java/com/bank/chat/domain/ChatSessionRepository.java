package com.bank.chat.domain;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface ChatSessionRepository extends JpaRepository<ChatSession, UUID> {

    Optional<ChatSession> findByClient_Id(UUID clientId);

    @Query("select s from ChatSession s where s.slaFirstResponseDeadlineAt is not null "
            + "and s.firstOperatorResponseAt is null "
            + "and s.slaFirstResponseDeadlineAt < :now "
            + "and s.status in :statuses")
    List<ChatSession> findSessionsWithBreachedFirstResponseSla(
            @Param("now") Instant now,
            @Param("statuses") List<SessionStatus> statuses
    );
}
