-- BetAML migration v30
-- Permite registrar submissao regulatoria no timeline de casos.

ALTER TABLE case_events
  DROP CONSTRAINT IF EXISTS case_events_event_type_check;

ALTER TABLE case_events
  ADD CONSTRAINT case_events_event_type_check
  CHECK (
    event_type IN (
      'NOTE',
      'STATUS_CHANGE',
      'EVIDENCE_UPLOAD',
      'ASSIGNMENT',
      'REPORT_GENERATED',
      'REPORT_SUBMITTED'
    )
  );