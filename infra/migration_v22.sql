-- migration_v22.sql — Fase 1: Integridade & Segurança
--
-- 1. CompoundRule: consolida (logic + component_rule_ids) como canônicos;
--    remove sinônimos (operator, child_rule_ids).
-- 2. ModelRegistry: consolida (is_active, artifact_uri, training_rows);
--    remove aliases (active, artifact_path, sample_count).
-- 3. Player: remove coluna full_name (PII em claro) — canônico é name_encrypted (Fernet).
-- 4. PlayerListEntry: índice de unicidade (list_id, value) para evitar duplicatas.
--
-- LGPD Art. 46: proteção de dados pessoais por design — eliminar armazenamento
-- de PII em claro (full_name) e garantir consistência de campos.
-- ============================================================

BEGIN;

-- ── 1. CompoundRule — unificar logic + component_rule_ids ─────────────────────

-- Sincronizar antes de remover aliases
UPDATE compound_rules
    SET logic = operator
    WHERE logic IS NULL AND operator IS NOT NULL;

UPDATE compound_rules
    SET component_rule_ids = child_rule_ids
    WHERE (component_rule_ids IS NULL OR component_rule_ids = '[]'::jsonb)
      AND child_rule_ids IS NOT NULL
      AND child_rule_ids != '[]'::jsonb;

-- Garantir que logic tenha valor padrão em todas as linhas
UPDATE compound_rules
    SET logic = 'AND'
    WHERE logic IS NULL;

-- Remover campos duplicados
ALTER TABLE compound_rules
    DROP COLUMN IF EXISTS operator,
    DROP COLUMN IF EXISTS child_rule_ids;

-- ── 2. ModelRegistry — unificar is_active, artifact_uri, training_rows ────────

-- Sincronizar antes de remover aliases
UPDATE model_registry
    SET is_active = active
    WHERE is_active IS DISTINCT FROM active AND active IS NOT NULL;

UPDATE model_registry
    SET artifact_uri = artifact_path
    WHERE artifact_uri IS NULL AND artifact_path IS NOT NULL;

UPDATE model_registry
    SET training_rows = sample_count
    WHERE training_rows IS NULL AND sample_count IS NOT NULL;

-- Remover campos duplicados
ALTER TABLE model_registry
    DROP COLUMN IF EXISTS active,
    DROP COLUMN IF EXISTS artifact_path,
    DROP COLUMN IF EXISTS sample_count;

-- ── 3. Player — remover full_name (PII plaintext) ─────────────────────────────
--
-- full_name era um cache desprotegido do nome em claro.
-- O campo canônico é name_encrypted (cifrado com Fernet/AES-128-CBC+HMAC).
-- A camada Python (models.py) expõe full_name como @property que decifra
-- name_encrypted em tempo de execução; nenhum dump SQL contém o nome em claro.
--
-- Proteção retroativa: se alguma linha tiver name_encrypted vazio mas
-- full_name preenchido, marcamos com sentinela para reprocessamento posterior.

UPDATE players
    SET name_encrypted = 'PENDING_MIGRATION'::bytea
    WHERE (name_encrypted IS NULL OR length(name_encrypted) = 0)
      AND full_name IS NOT NULL AND full_name <> '';

ALTER TABLE players
    DROP COLUMN IF EXISTS full_name;

-- ── 4. PlayerListEntry — índice de unicidade para (list_id, value) ────────────
--
-- Previne entradas duplicadas de valor no mesmo watchlist (ex.: mesmo CPF hash
-- adicionado duas vezes por upload de CSV).

CREATE UNIQUE INDEX IF NOT EXISTS idx_player_list_entries_unique_val
    ON player_list_entries (list_id, value)
    WHERE value IS NOT NULL;

-- ── Fim da migration v22 ──────────────────────────────────────────────────────
COMMIT;
