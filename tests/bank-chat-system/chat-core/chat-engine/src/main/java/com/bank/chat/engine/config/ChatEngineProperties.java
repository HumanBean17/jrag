package com.bank.chat.engine.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.HashMap;
import java.util.Map;

@ConfigurationProperties(prefix = "chat")
public class ChatEngineProperties {

    private String consumerGroupId = "chat-ingestion";
    private int rateLimitPerMinute = 60;
    private int reopenWindowHours = 72;
    private Map<String, Long> slaFirstResponseSeconds = defaultSla();
    private BusinessHours businessHours = new BusinessHours();
    private ChatAssign chatAssign = new ChatAssign();
    private JoinOperator joinOperator = new JoinOperator();
    private PushGateway pushGateway = new PushGateway();
    private Crm crm = new Crm();

    private static Map<String, Long> defaultSla() {
        Map<String, Long> m = new HashMap<>();
        m.put("VIP", 60L);
        m.put("PRIVATE", 120L);
        m.put("RETAIL", 300L);
        return m;
    }

    public String getConsumerGroupId() {
        return consumerGroupId;
    }

    public void setConsumerGroupId(String consumerGroupId) {
        this.consumerGroupId = consumerGroupId;
    }

    public int getRateLimitPerMinute() {
        return rateLimitPerMinute;
    }

    public void setRateLimitPerMinute(int rateLimitPerMinute) {
        this.rateLimitPerMinute = rateLimitPerMinute;
    }

    public int getReopenWindowHours() {
        return reopenWindowHours;
    }

    public void setReopenWindowHours(int reopenWindowHours) {
        this.reopenWindowHours = reopenWindowHours;
    }

    public Map<String, Long> getSlaFirstResponseSeconds() {
        return slaFirstResponseSeconds;
    }

    public void setSlaFirstResponseSeconds(Map<String, Long> slaFirstResponseSeconds) {
        this.slaFirstResponseSeconds = slaFirstResponseSeconds;
    }

    public BusinessHours getBusinessHours() {
        return businessHours;
    }

    public void setBusinessHours(BusinessHours businessHours) {
        this.businessHours = businessHours;
    }

    public ChatAssign getChatAssign() {
        return chatAssign;
    }

    public void setChatAssign(ChatAssign chatAssign) {
        this.chatAssign = chatAssign;
    }

    public JoinOperator getJoinOperator() {
        return joinOperator;
    }

    public void setJoinOperator(JoinOperator joinOperator) {
        this.joinOperator = joinOperator;
    }

    public PushGateway getPushGateway() {
        return pushGateway;
    }

    public void setPushGateway(PushGateway pushGateway) {
        this.pushGateway = pushGateway;
    }

    public Crm getCrm() {
        return crm;
    }

    public void setCrm(Crm crm) {
        this.crm = crm;
    }

    public static class BusinessHours {
        private String zoneId = "UTC";
        private String weekdayStart = "09:00";
        private String weekdayEnd = "18:00";

        public String getZoneId() {
            return zoneId;
        }

        public void setZoneId(String zoneId) {
            this.zoneId = zoneId;
        }

        public String getWeekdayStart() {
            return weekdayStart;
        }

        public void setWeekdayStart(String weekdayStart) {
            this.weekdayStart = weekdayStart;
        }

        public String getWeekdayEnd() {
            return weekdayEnd;
        }

        public void setWeekdayEnd(String weekdayEnd) {
            this.weekdayEnd = weekdayEnd;
        }
    }

    public static class ChatAssign {
        private String baseUrl = "";

        public String getBaseUrl() {
            return baseUrl;
        }

        public void setBaseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
        }
    }

    /**
     * Service-to-service callback from chat-assign ({@code POST /chat/joinOperator}).
     * When {@code internalToken} is non-blank, requests must include header {@code X-Chat-Internal-Token} with the same value.
     */
    public static class JoinOperator {
        private String internalToken = "";

        public String getInternalToken() {
            return internalToken;
        }

        public void setInternalToken(String internalToken) {
            this.internalToken = internalToken;
        }
    }

    public static class PushGateway {
        private String baseUrl = "";

        public String getBaseUrl() {
            return baseUrl;
        }

        public void setBaseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
        }
    }

    public static class Crm {
        private String baseUrl = "";

        public String getBaseUrl() {
            return baseUrl;
        }

        public void setBaseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
        }
    }
}
