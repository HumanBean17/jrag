package com.bank.chat.engine.policy;

import com.bank.chat.engine.config.ChatEngineProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class SessionPolicyEngineBean {

    @Bean
    public SessionPolicyEngine sessionPolicyEngine(ChatEngineProperties properties) {
        return new SessionPolicyEngine(properties);
    }

    @Bean
    public BusinessHoursPolicy businessHoursPolicy(ChatEngineProperties properties) {
        return new BusinessHoursPolicy(properties);
    }
}
