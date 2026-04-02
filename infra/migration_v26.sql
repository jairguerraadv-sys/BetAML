-- Migration v26: RLS na tabela tenants + coluna plan_tier
--
-- Objetivos:
-- 1) Adicionar plan_tier (starter/standard/professional/enterprise) a tenants.
--    Alimenta redis_rate_limit_by_plan() para quotas por plano contratual.
-- 2) Aplicar FORCE ROW LEVEL SECURITY na tabela tenants:
--    - SELECT: permite quando current_tenant_id() bate com o id da linha
--              OU quando current_tenant_id() é NULL (path de login — lookup por slug
--              antes de setar contexto). O dono da tabela (betaml superuser) e o
--              role betaml_seed/migrations sempre bypassam RLS por BYPASSRLS.
--    - INSERT/UPDATE/DELETE: restritos ao tenant corrente na sessão.
--      Sem isso, um bug de aplicação poderia escrever em outro tenant.
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Adicionar plan_tier se ainda não existir
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tenants' AND column_name = 'plan_tier'
    ) THEN
        ALTER TABLE tenants
            ADD COLUMN plan_tier VARCHAR(20) NOT NULL DEFAULT 'standard';
    END IF;
END
$$;

-- Constraint de domínio para garantir valores válidos
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'tenants' AND constraint_name = 'tenants_plan_tier_check'
    ) THEN
        ALTER TABLE tenants
            ADD CONSTRAINT tenants_plan_tier_check
            CHECK (plan_tier IN ('starter', 'standard', 'professional', 'enterprise'));
    END IF;
END
$$;

-- 2. Garantir que current_tenant_id() existe (reaproveita helper de v18)
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
$$ LANGUAGE SQL STABLE;

-- 3. RLS na tabela tenants
DO $$
BEGIN
    IF to_regclass('public.tenants') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE tenants ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE tenants FORCE ROW LEVEL SECURITY';

        -- SELECT: próprio tenant OU sem contexto (login / slug lookup)
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_select ON tenants';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_tenants_select ON tenants
                FOR SELECT
                USING (
                    id = current_tenant_id()          -- path autenticado normal
                    OR current_tenant_id() IS NULL    -- path de login (lookup por slug)
                )
        $pol$;

        -- INSERT: só quando contexto bate (tenant provisioning via SuperAdmin seta contexto correto)
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_insert ON tenants';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_tenants_insert ON tenants
                FOR INSERT
                WITH CHECK (
                    id = current_tenant_id()
                    OR current_tenant_id() IS NULL  -- admin seed / provisioning fresh
                )
        $pol$;

        -- UPDATE: apenas o próprio tenant
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_update ON tenants';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_tenants_update ON tenants
                FOR UPDATE
                USING (id = current_tenant_id())
        $pol$;

        -- DELETE: apenas o próprio tenant (SuperAdmin seta contexto antes de deletar)
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_delete ON tenants';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_tenants_delete ON tenants
                FOR DELETE
                USING (id = current_tenant_id())
        $pol$;
    END IF;
END
$$;

-- 4. betaml_app continua sem BYPASSRLS → obedece as policies acima.
--    O role betaml (superuser / dono da DB) bypassa por ser owner da tabela.
--    Nenhuma ação adicional necessária para o owner.
