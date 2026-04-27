package com.bank.chat.engine.compliance;

public interface ComplianceScanner {

    ComplianceScanResult scan(String message);

    final class ComplianceScanResult {
        private final boolean flagged;
        private final String reasonCode;

        private ComplianceScanResult(boolean flagged, String reasonCode) {
            this.flagged = flagged;
            this.reasonCode = reasonCode;
        }

        public static ComplianceScanResult ok() {
            return new ComplianceScanResult(false, null);
        }

        public static ComplianceScanResult flagged(String reasonCode) {
            return new ComplianceScanResult(true, reasonCode);
        }

        public boolean isFlagged() {
            return flagged;
        }

        public String getReasonCode() {
            return reasonCode;
        }
    }
}
