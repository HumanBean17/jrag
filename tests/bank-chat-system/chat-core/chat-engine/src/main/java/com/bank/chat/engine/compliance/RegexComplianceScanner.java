package com.bank.chat.engine.compliance;

import org.springframework.stereotype.Component;

import java.util.regex.Pattern;

@Component
public class RegexComplianceScanner implements ComplianceScanner {

    private static final Pattern CARD_LIKE = Pattern.compile("\\b(?:\\d[ -]*?){13,19}\\b");
    private static final Pattern ACCOUNT_LIKE = Pattern.compile("(?i)\\b(account|iban|sort\\s*code)\\b[^\\n]{0,40}\\b\\d[\\d\\s-]{6,}\\b");

    @Override
    public ComplianceScanResult scan(String message) {
        if (message == null || message.isBlank()) {
            return ComplianceScanResult.ok();
        }
        if (CARD_LIKE.matcher(message).find()) {
            return ComplianceScanResult.flagged("CARD_LIKE_SEQUENCE");
        }
        if (ACCOUNT_LIKE.matcher(message).find()) {
            return ComplianceScanResult.flagged("ACCOUNT_LIKE_MENTION");
        }
        return ComplianceScanResult.ok();
    }
}
