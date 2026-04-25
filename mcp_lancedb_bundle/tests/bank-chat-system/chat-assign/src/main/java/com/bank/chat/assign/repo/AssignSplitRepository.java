package com.bank.chat.assign.repo;

import com.bank.chat.assign.domain.AssignSplitEntity;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface AssignSplitRepository extends JpaRepository<AssignSplitEntity, UUID> {

    Optional<AssignSplitEntity> findByNameIgnoreCase(String name);
}
