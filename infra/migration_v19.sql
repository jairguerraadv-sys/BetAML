-- Migration v19: alinhar workflow enterprise de cases ao schema inicial legado
--
-- Problema:
-- - bases novas criadas a partir do init-db.sql ainda herdavam status antigos
--   (IN_REVIEW, PENDING_INFO, CLOSED_SAR, CLOSED_SAT);
-- - a API e os testes E2E usam o workflow enterprise:
--   OPEN -> INVESTIGATING -> PENDING_REVIEW -> CLOSED | REPORTED
--
-- Solução:
-- 1. normalizar quaisquer valores legados remanescentes;
-- 2. recriar o CHECK constraint de cases.status com o conjunto canônico.

UPDATE cases
SET status = CASE status
    WHEN 'IN_REVIEW' THEN 'INVESTIGATING'
    WHEN 'PENDING_INFO' THEN 'PENDING_REVIEW'
    WHEN 'CLOSED_SAR' THEN 'REPORTED'
    WHEN 'CLOSED_SAT' THEN 'CLOSED'
    ELSE status
END
WHERE status IN ('IN_REVIEW', 'PENDING_INFO', 'CLOSED_SAR', 'CLOSED_SAT');

ALTER TABLE cases
    DROP CONSTRAINT IF EXISTS cases_status_check;

ALTER TABLE cases
    ADD CONSTRAINT cases_status_check
    CHECK (status IN ('OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'CLOSED', 'REPORTED'));
