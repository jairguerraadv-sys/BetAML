-- Migration v25: RBAC multi-role por usuário
-- Adiciona coluna `roles` (JSONB array) em users e faz backfill
-- do mapeamento legado (role string → lista de novos papéis).

BEGIN;

-- 1. Ampliar o campo legado para aceitar os novos nomes (até 50 chars)
ALTER TABLE users
  ALTER COLUMN role TYPE varchar(50);

-- 2. Adicionar coluna roles (JSONB array, nullable — vazio = usar role legado)
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS roles JSONB DEFAULT '[]'::jsonb;

-- 3. Backfill: traduzir role legado → lista de novos papéis
UPDATE users SET roles =
  CASE role
    WHEN 'AML_ANALYST' THEN '["Operador_Analista"]'::jsonb
    WHEN 'AUDITOR'     THEN '["Operador_Analista"]'::jsonb
    WHEN 'ADMIN'       THEN '["Operador_Gestor","Operador_AdminTecnico","Operador_Analista"]'::jsonb
    WHEN 'SUPER_ADMIN' THEN '["BetAML_SuperAdmin"]'::jsonb
    ELSE '[]'::jsonb
  END
WHERE roles = '[]'::jsonb OR roles IS NULL;

-- 4. Índice GIN para queries de membros por papel
CREATE INDEX IF NOT EXISTS idx_users_roles ON users USING GIN (roles);

COMMIT;
