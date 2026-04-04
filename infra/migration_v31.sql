-- BetAML migration v31
-- Alinha status aceitos de report_packages com o fluxo regulatorio atual.

ALTER TABLE report_packages
  DROP CONSTRAINT IF EXISTS report_packages_status_check;

ALTER TABLE report_packages
  ADD CONSTRAINT report_packages_status_check
  CHECK (
    status IN (
      'DRAFT',
      'FINAL',
      'PENDING_REVIEW',
      'FILED',
      'REJECTED',
      'ARCHIVED'
    )
  );