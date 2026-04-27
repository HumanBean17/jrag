package com.bank.chat.engine.ratelimit;

import com.bank.chat.engine.config.ChatEngineProperties;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;

@Component
public class ClientMessageRateLimiter {

    private final ChatEngineProperties properties;
    private final Cache<String, AtomicInteger> minuteBuckets;

    public ClientMessageRateLimiter(ChatEngineProperties properties) {
        this.properties = properties;
        this.minuteBuckets = Caffeine.newBuilder()
                .expireAfterWrite(Duration.ofMinutes(2))
                .build();
    }

    public boolean allow(String epkId) {
        if (epkId == null) {
            return true;
        }
        String key = epkId + ":" + (System.currentTimeMillis() / 60_000);
        AtomicInteger counter = minuteBuckets.get(key, k -> new AtomicInteger(0));
        int next = counter.incrementAndGet();
        return next <= properties.getRateLimitPerMinute();
    }
}
