package com.bank.chat.engine.kafka;

import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.config.ChatEngineProperties;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.springframework.boot.autoconfigure.kafka.KafkaProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.DefaultKafkaConsumerFactory;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.core.ProducerFactory;
import org.springframework.kafka.support.serializer.JsonDeserializer;
import org.springframework.kafka.support.serializer.JsonSerializer;

import java.util.HashMap;
import java.util.Map;

@Configuration
public class ChatKafkaConfiguration {

    @Bean
    public ProducerFactory<String, InternalEvent> chatProducerFactory(KafkaProperties kafkaProperties) {
        Map<String, Object> props = new HashMap<>(kafkaProperties.buildProducerProperties());
        props.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        props.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, JsonSerializer.class);
        props.put(JsonSerializer.ADD_TYPE_INFO_HEADERS, false);
        return new DefaultKafkaProducerFactory<>(props);
    }

    @Bean
    @Primary
    public KafkaTemplate<String, InternalEvent> chatKafkaTemplate(ProducerFactory<String, InternalEvent> chatProducerFactory) {
        return new KafkaTemplate<>(chatProducerFactory);
    }

    @Bean
    public ConsumerFactory<String, InternalEvent> chatConsumerFactory(
            KafkaProperties kafkaProperties,
            ChatEngineProperties chatEngineProperties
    ) {
        Map<String, Object> props = new HashMap<>(kafkaProperties.buildConsumerProperties());
        props.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        props.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, JsonDeserializer.class);
        props.put(JsonDeserializer.TRUSTED_PACKAGES, "com.bank.chat.contracts");
        props.put(JsonDeserializer.VALUE_DEFAULT_TYPE, InternalEvent.class.getName());
        props.put(ConsumerConfig.GROUP_ID_CONFIG, chatEngineProperties.getConsumerGroupId());
        return new DefaultKafkaConsumerFactory<>(props);
    }

    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, InternalEvent> chatKafkaListenerContainerFactory(
            ConsumerFactory<String, InternalEvent> chatConsumerFactory
    ) {
        ConcurrentKafkaListenerContainerFactory<String, InternalEvent> factory =
                new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(chatConsumerFactory);
        return factory;
    }
}
