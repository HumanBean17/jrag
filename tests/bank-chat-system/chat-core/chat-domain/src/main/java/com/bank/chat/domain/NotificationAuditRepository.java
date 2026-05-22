package com.bank.chat.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.UUID;

public interface NotificationAuditRepository extends JpaRepository<NotificationAudit, UUID> {

    List<NotificationAudit> findByRecipientAndChannel(String recipient, String channel);
}
