package com.bank.chat.domain;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.UUID;

public interface ChatEventRepository extends JpaRepository<ChatEvent, UUID> {
}
