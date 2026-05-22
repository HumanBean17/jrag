CREATE MATERIALIZED VIEW mv_operator_load AS
SELECT
    aos.operator_id,
    aos.operator_status,
    COUNT(ac.id) AS active_chats,
    COALESCE(SUM(ac.priority_score), 0) AS total_priority
FROM assign_operator_session aos
LEFT JOIN assign_chat ac ON ac.operator_session_id = aos.id
GROUP BY aos.operator_id, aos.operator_status;

CREATE OR REPLACE FUNCTION trg_recalc_queue_priority()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE assign_queue
    SET priority_score = (SELECT priority_score FROM assign_chat WHERE id = NEW.assign_chat_id)
    WHERE assign_chat_id = NEW.assign_chat_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER t_assign_queue_priority
AFTER INSERT OR UPDATE ON assign_queue
FOR EACH ROW EXECUTE FUNCTION trg_recalc_queue_priority();
