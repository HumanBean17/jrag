package com.bank.chat.app.web;

import com.bank.chat.contracts.ChatMetricsReport;
import com.bank.chat.contracts.ChatSummaryReport;
import com.bank.chat.contracts.brownfield.CodebaseHttpRoute;
import com.bank.chat.contracts.brownfield.CodebaseHttpRoutes;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/reports")
public class ChatReportingController {

    @GetMapping("/metrics")
    @CodebaseHttpRoute(path = "/api/v1/reports/metrics", method = "GET")
    public ResponseEntity<ChatMetricsReport> getMetrics() {
        return ResponseEntity.ok(new ChatMetricsReport(0, 0, 0, 0.0));
    }

    @CodebaseHttpRoutes({
        @CodebaseHttpRoute(path = "/api/v1/reports/summary", method = "GET"),
        @CodebaseHttpRoute(path = "/api/v1/reports/summary/csv", method = "GET")
    })
    public ResponseEntity<ChatSummaryReport> getSummary() {
        return ResponseEntity.ok(new ChatSummaryReport("daily", java.util.List.of(), java.util.Map.of()));
    }
}
