package com.bank.chat.engine.schedule;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class ComplianceReportJob {

    @Scheduled(cron = "0 0 6 * * MON-FRI")
    @Transactional
    public void generateDailyComplianceReport() {
    }

    @Scheduled(fixedRateString = "30000")
    public void flushMetrics() {
    }
}
