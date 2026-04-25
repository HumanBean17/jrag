package com.bank.chat.assign.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;

@Configuration
@EnableConfigurationProperties(AssignProperties.class)
public class AssignConfiguration {

    @Bean
    public RestTemplate assignRestTemplate() {
        return new RestTemplate();
    }
}
