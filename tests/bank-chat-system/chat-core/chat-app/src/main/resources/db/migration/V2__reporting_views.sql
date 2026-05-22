CREATE VIEW v_active_session_summary AS
SELECT
    cs.id AS session_id,
    c.conversation_id,
    c.epk_id,
    c.client_segment,
    cs.status,
    cs.assigned_operator_id,
    cs.first_client_message_at,
    cs.sla_first_response_deadline_at,
    cs.escalation_level,
    EXTRACT(EPOCH FROM (NOW() - cs.first_client_message_at)) AS seconds_since_first_message,
    (SELECT COUNT(*) FROM chat_event ce WHERE ce.chat_session_id = cs.id) AS event_count
FROM chat_session cs
JOIN client c ON c.id = cs.client_id
WHERE cs.status NOT IN ('CLOSED');

CREATE VIEW v_sla_breach_summary AS
SELECT
    c.client_segment,
    COUNT(*) AS total_breaches,
    AVG(EXTRACT(EPOCH FROM (ssc.breached_at - ssc.due_at))) AS avg_breach_seconds
FROM session_sla_checkpoint ssc
JOIN chat_session cs ON cs.id = ssc.chat_session_id
JOIN client c ON c.id = cs.client_id
WHERE ssc.breached_at IS NOT NULL
GROUP BY c.client_segment;

CREATE OR REPLACE FUNCTION fn_get_operator_workload(p_operator_id VARCHAR)
RETURNS TABLE (
    conversation_id VARCHAR,
    session_status VARCHAR,
    priority_score INT,
    enqueued_minutes INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ac.conversation_id,
        'QUEUED' AS session_status,
        ac.priority_score,
        EXTRACT(EPOCH FROM (NOW() - aq.enqueued_at))::INT / 60 AS enqueued_minutes
    FROM assign_chat ac
    JOIN assign_queue aq ON aq.assign_chat_id = ac.id
    JOIN assign_operator_session aos ON aos.id = ac.operator_session_id
    WHERE aos.operator_id = p_operator_id
    ORDER BY ac.priority_score DESC;
END;
$$ LANGUAGE plpgsql;
