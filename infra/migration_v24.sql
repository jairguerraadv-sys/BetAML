-- ============================================================
-- migration_v24: Alinhamento com Lei 14.790/2023 e Portaria SPA/MF 1.143/2024
-- Apostas de Quota Fixa — atualização de enums e colunas de compliance PLD
-- ============================================================

-- 1. Novos valores permitidos em financial_transactions.type
--    REVERSAL (era CHARGEBACK), FREE_BET e CASHOUT são tipos específicos
--    do contexto de apostas reguladas.
--    CHARGEBACK é mantido como alias transitório para compatibilidade de ingestão.

ALTER TABLE financial_transactions
    DROP CONSTRAINT IF EXISTS chk_transaction_type;

ALTER TABLE financial_transactions
    ADD CONSTRAINT chk_transaction_type
    CHECK (type IN (
        'DEPOSIT', 'WITHDRAWAL', 'REVERSAL',
        'BONUS', 'FREE_BET', 'CASHOUT', 'ADJUSTMENT',
        'CHARGEBACK'  -- alias transitório — remover após migração completa de fontes
    ));

-- 2. Novos valores permitidos em financial_transactions.payment_method
--    CARD_CREDIT é registrado mas NUNCA aceito como método de depósito
--    (art. 5º Portaria 1.143/2024 — proibição de cartão de crédito para depósitos).
--    CARD genérico e DEBIT são mantidos como aliases transitórios.

ALTER TABLE financial_transactions
    DROP CONSTRAINT IF EXISTS chk_payment_method;

ALTER TABLE financial_transactions
    ADD CONSTRAINT chk_payment_method
    CHECK (payment_method IN (
        'PIX', 'TED', 'DEBIT', 'CARD_DEBIT', 'CARD_CREDIT',
        'WALLET', 'OTHER',
        'CARD'  -- alias transitório → mapeado para CARD_DEBIT no ingest
    ));

-- 3. Coluna de flag: transações tentadas com CARD_CREDIT são sinalizadas
--    (ingest.py rejeita antes de persistir; coluna existe para auditoria de tentativas).
ALTER TABLE financial_transactions
    ADD COLUMN IF NOT EXISTS payment_method_flagged BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE financial_transactions
    SET payment_method_flagged = TRUE
    WHERE payment_method = 'CARD_CREDIT';

-- 4. Campos de compliance de jogo responsável em players
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS self_exclusion_flag  BOOLEAN      NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS deposit_limit_daily  NUMERIC(15,2);

COMMENT ON COLUMN players.self_exclusion_flag IS
    'Apostador com autoexclusão ativa no SIGAP — apostas devem ser bloqueadas (Portaria 1.231/2024)';
COMMENT ON COLUMN players.deposit_limit_daily IS
    'Limite de depósito diário declarado voluntariamente pelo apostador durante KYC';

-- 5. Renomear coluna multi_currency_flag → inconsistent_currency_flag
--    Em apostas domésticas (BRL-only) não existe "multi-moeda" legítimo;
--    qualquer transação não-BRL é anomalia de dado ou tentativa de evasão.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'feature_snapshots'
          AND column_name = 'multi_currency_flag'
    ) THEN
        ALTER TABLE feature_snapshots
            RENAME COLUMN multi_currency_flag TO inconsistent_currency_flag;
    END IF;
END;
$$;
