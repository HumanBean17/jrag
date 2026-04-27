package com.bank.chat.assign.kafka;

import com.bank.chat.assign.service.DistributionService;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class DistributionTriggerListener {

    private final DistributionService distributionService;

    public DistributionTriggerListener(DistributionService distributionService) {
        this.distributionService = distributionService;
    }

    @KafkaListener(
            topics = "${assign.kafka.distribution-topic}",
            groupId = "${assign.kafka.consumer-group}",
            concurrency = "1"
    )
    public void onDistributionTrigger(String payload) {
        distributionService.runDistribution();
    }
}
