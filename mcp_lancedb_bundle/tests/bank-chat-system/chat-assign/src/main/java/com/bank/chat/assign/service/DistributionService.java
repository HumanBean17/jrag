package com.bank.chat.assign.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class DistributionService {

    private static final Logger log = LoggerFactory.getLogger(DistributionService.class);

    private final DistributionChunkService distributionChunkService;

    public DistributionService(DistributionChunkService distributionChunkService) {
        this.distributionChunkService = distributionChunkService;
    }

    public void runDistribution() {
        int guard = 0;
        while (distributionChunkService.tryAssignNextFromQueue()) {
            guard++;
            if (guard > 10_000) {
                log.warn("distribution guard limit reached");
                break;
            }
        }
    }
}
