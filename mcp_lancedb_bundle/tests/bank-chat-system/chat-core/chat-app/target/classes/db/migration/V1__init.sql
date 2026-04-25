CREATE TABLE client (
    id UUID PRIMARY KEY,
    conversation_id VARCHAR(128) NOT NULL UNIQUE,
    epk_id VARCHAR(128) NOT NULL,
    first_name VARCHAR(128),
    last_name VARCHAR(128),
    client_segment VARCHAR(32) NOT NULL,
    risk_flags TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_client_epk ON client(epk_id);

CREATE TABLE chat_session (
    id UUID PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES client(id),
    status VARCHAR(40) NOT NULL,
    assigned_operator_id VARCHAR(128),
    first_client_message_at TIMESTAMPTZ,
    first_operator_response_at TIMESTAMPTZ,
    sla_first_response_deadline_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ,
    compliance_hold BOOLEAN NOT NULL,
    after_hours_queued BOOLEAN NOT NULL,
    closed_reason VARCHAR(32),
    closed_at TIMESTAMPTZ,
    last_read_by_client_seq BIGINT NOT NULL,
    last_read_by_operator_seq BIGINT NOT NULL,
    message_seq BIGINT NOT NULL,
    typing_until TIMESTAMPTZ,
    escalation_level INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX uq_chat_session_client ON chat_session(client_id);

CREATE INDEX idx_chat_session_sla ON chat_session(status, sla_first_response_deadline_at);

CREATE TABLE chat_event (
    id UUID PRIMARY KEY,
    chat_session_id UUID NOT NULL REFERENCES chat_session(id),
    client_id UUID NOT NULL REFERENCES client(id),
    event_type VARCHAR(64) NOT NULL,
    event_message TEXT,
    correlation_id VARCHAR(64),
    idempotency_key VARCHAR(128),
    source VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_chat_event_session_created ON chat_event(chat_session_id, created_at);

CREATE TABLE session_sla_checkpoint (
    id UUID PRIMARY KEY,
    chat_session_id UUID NOT NULL REFERENCES chat_session(id),
    checkpoint_type VARCHAR(32) NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    breached_at TIMESTAMPTZ,
    escalation_level INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_session_checkpoint_due UNIQUE (chat_session_id, checkpoint_type, due_at)
);

CREATE TABLE processed_event (
    idempotency_key VARCHAR(256) PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL
);
