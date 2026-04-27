package com.bank.chat.assign.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "assign")
public class AssignProperties {

    private final ChatCore chatCore = new ChatCore();
    private final Kafka kafka = new Kafka();
    private final Distribution distribution = new Distribution();

    public ChatCore getChatCore() {
        return chatCore;
    }

    public Kafka getKafka() {
        return kafka;
    }

    public Distribution getDistribution() {
        return distribution;
    }

    public static class ChatCore {
        private String baseUrl = "http://localhost:8080";
        private String internalToken = "";

        public String getBaseUrl() {
            return baseUrl;
        }

        public void setBaseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
        }

        public String getInternalToken() {
            return internalToken;
        }

        public void setInternalToken(String internalToken) {
            this.internalToken = internalToken;
        }
    }

    public static class Kafka {
        private String distributionTopic = "chat.assign.distribution";
        private String consumerGroup = "chat-assign-distribution";

        public String getDistributionTopic() {
            return distributionTopic;
        }

        public void setDistributionTopic(String distributionTopic) {
            this.distributionTopic = distributionTopic;
        }

        public String getConsumerGroup() {
            return consumerGroup;
        }

        public void setConsumerGroup(String consumerGroup) {
            this.consumerGroup = consumerGroup;
        }
    }

    public static class Distribution {
        private int maxChatsPerOperator = 1;

        public int getMaxChatsPerOperator() {
            return maxChatsPerOperator;
        }

        public void setMaxChatsPerOperator(int maxChatsPerOperator) {
            this.maxChatsPerOperator = maxChatsPerOperator;
        }
    }
}
