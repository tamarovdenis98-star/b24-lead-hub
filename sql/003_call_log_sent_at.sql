-- 003: record the moment the request left n8n (after throttling), so the 2 rps limit is measurable.
ALTER TABLE b24_call_log ADD COLUMN sent_at timestamptz;
CREATE INDEX b24_call_log_sent_idx ON b24_call_log (sent_at);
