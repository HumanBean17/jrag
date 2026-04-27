package com.bank.chat.assign.service;

import com.bank.chat.assign.domain.AssignSplitEntity;
import com.bank.chat.assign.repo.AssignSplitRepository;
import org.springframework.stereotype.Service;

@Service
public class SplitResolverService {

    private final AssignSplitRepository assignSplitRepository;

    public SplitResolverService(AssignSplitRepository assignSplitRepository) {
        this.assignSplitRepository = assignSplitRepository;
    }

    public AssignSplitEntity resolveSplitName(String raw) {
        String name = (raw == null || raw.isBlank()) ? "general" : raw.trim();
        return assignSplitRepository.findByNameIgnoreCase(name)
                .orElseGet(() -> assignSplitRepository.findByNameIgnoreCase("general")
                        .orElseThrow(() -> new IllegalStateException("assign_split 'general' is missing")));
    }
}
