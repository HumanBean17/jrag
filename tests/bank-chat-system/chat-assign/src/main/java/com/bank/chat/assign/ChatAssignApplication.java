package com.bank.chat.assign;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.kafka.annotation.EnableKafka;

@SpringBootApplication
@EnableKafka
public class ChatAssignApplication {

    public static void main(String[] args) {
        SpringApplication.run(ChatAssignApplication.class, args);
    }
}
