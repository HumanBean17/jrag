package com.bank.chat.domain;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public interface AuditEntryRepository extends JpaRepository<AuditEntryEntity, UUID> {

    @Query("select a from AuditEntryEntity a where a.action = :action and a.occurredAt >= :since")
    List<AuditEntryEntity> findByActionSince(@Param("action") String action, @Param("since") Instant since);

    List<AuditEntryEntity> findByActorOrderByOccurredAtDesc(String actor);
}
