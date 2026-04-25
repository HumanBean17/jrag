CREATE TABLE assign_split (
    id UUID PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE
);

INSERT INTO assign_split (id, name) VALUES
    ('a0000001-0001-0001-0001-000000000001', 'general'),
    ('a0000001-0001-0001-0001-000000000002', 'investment'),
    ('a0000001-0001-0001-0001-000000000003', 'mortgage');

CREATE TABLE assign_operator_session (
    id UUID PRIMARY KEY,
    operator_id VARCHAR(128) NOT NULL,
    operator_status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_assign_operator_session_operator_id ON assign_operator_session(operator_id);
CREATE INDEX idx_assign_operator_session_status ON assign_operator_session(operator_status);

CREATE TABLE assign_operator_split (
    operator_session_id UUID NOT NULL REFERENCES assign_operator_session(id) ON DELETE CASCADE,
    split_id UUID NOT NULL REFERENCES assign_split(id) ON DELETE CASCADE,
    PRIMARY KEY (operator_session_id, split_id)
);

CREATE TABLE assign_chat (
    id UUID PRIMARY KEY,
    conversation_id VARCHAR(128) NOT NULL UNIQUE,
    operator_session_id UUID REFERENCES assign_operator_session(id) ON DELETE SET NULL,
    split_id UUID NOT NULL REFERENCES assign_split(id),
    epk_id VARCHAR(128),
    priority_score INT NOT NULL DEFAULT 0,
    reason VARCHAR(256),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_assign_chat_conversation ON assign_chat(conversation_id);
CREATE INDEX idx_assign_chat_operator_session ON assign_chat(operator_session_id);

CREATE TABLE assign_queue (
    id UUID PRIMARY KEY,
    assign_chat_id UUID NOT NULL UNIQUE REFERENCES assign_chat(id) ON DELETE CASCADE,
    enqueued_at TIMESTAMPTZ NOT NULL,
    priority_score INT NOT NULL DEFAULT 0
);

CREATE INDEX idx_assign_queue_order ON assign_queue(priority_score DESC, enqueued_at ASC);
