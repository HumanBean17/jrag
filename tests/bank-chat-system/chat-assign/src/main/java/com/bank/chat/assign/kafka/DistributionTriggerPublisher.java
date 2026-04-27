package com.bank.chat.assign.kafka;

import com.bank.chat.assign.config.AssignProperties;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class DistributionTriggerPublisher {

    private final KafkaTemplate<String, String> kafkaTemplate;
    private final AssignProperties assignProperties;

    public DistributionTriggerPublisher(
            KafkaTemplate<String, String> kafkaTemplate,
            AssignProperties assignProperties
    ) {
        this.kafkaTemplate = kafkaTemplate;
        this.assignProperties = assignProperties;
    }

    public void publishTrigger() {
        String topic = assignProperties.getKafka().getDistributionTopic();
        kafkaTemplate.send(topic, "");
    }
}
