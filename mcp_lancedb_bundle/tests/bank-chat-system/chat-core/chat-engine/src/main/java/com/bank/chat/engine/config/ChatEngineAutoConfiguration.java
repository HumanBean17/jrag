package com.bank.chat.engine.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.annotation.EnableKafka;

@Configuration
@EnableKafka
@ComponentScan(basePackages = "com.bank.chat.engine")
@EnableConfigurationProperties(ChatEngineProperties.class)
public class ChatEngineAutoConfiguration {
}
