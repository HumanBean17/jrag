CREATE TABLE notification_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel VARCHAR(32) NOT NULL,
    recipient VARCHAR(256) NOT NULL,
    payload TEXT,
    status VARCHAR(32) NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_notification_audit_recipient ON notification_audit(recipient, channel);

CREATE TABLE audit_entry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action VARCHAR(128) NOT NULL,
    actor VARCHAR(256) NOT NULL,
    details_json TEXT,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_audit_entry_action ON audit_entry(action);
CREATE INDEX idx_audit_entry_actor ON audit_entry(actor);
