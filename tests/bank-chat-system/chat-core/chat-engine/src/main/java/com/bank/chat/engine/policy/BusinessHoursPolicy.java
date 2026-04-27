package com.bank.chat.engine.policy;

import com.bank.chat.engine.config.ChatEngineProperties;

import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

public class BusinessHoursPolicy {

    private final ChatEngineProperties properties;

    public BusinessHoursPolicy(ChatEngineProperties properties) {
        this.properties = properties;
    }

    public boolean isWithinBusinessHours(Instant now) {
        ChatEngineProperties.BusinessHours bh = properties.getBusinessHours();
        ZoneId zone = ZoneId.of(bh.getZoneId());
        ZonedDateTime zdt = now.atZone(zone);
        DayOfWeek dow = zdt.getDayOfWeek();
        if (dow == DayOfWeek.SATURDAY || dow == DayOfWeek.SUNDAY) {
            return false;
        }
        LocalTime t = zdt.toLocalTime();
        LocalTime start = LocalTime.parse(bh.getWeekdayStart());
        LocalTime end = LocalTime.parse(bh.getWeekdayEnd());
        return !t.isBefore(start) && t.isBefore(end);
    }
}
