package com.bank.chat.domain;

import org.springframework.data.jpa.repository.JpaRepository;

public interface ProcessedEventKeyRepository extends JpaRepository<ProcessedEventKey, String> {
}
