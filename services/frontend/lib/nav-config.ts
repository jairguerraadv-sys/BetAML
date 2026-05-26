/**
 * lib/nav-config.ts — Configuração central de navegação por papel (RBAC)
 *
 * Esta é a fonte da verdade para:
 *  - Quais seções cada papel enxerga no menu lateral
 *  - Quais rotas exigem qual papel (usado pelo useRouteGuard hook)
 *
 * IMPORTANTE: o backend é a barreira de segurança real.
 * O frontend esconde menus e redireciona — nunca concede acesso de fato.
 */

// ── Papéis do sistema ─────────────────────────────────────────────────────────
export type AppRole =
  | 'Operador_Analista'
  | 'Operador_Gestor'
  | 'Operador_AdminTecnico'
  | 'BetAML_SuperAdmin';

export const ALL_OPERATOR_ROLES: AppRole[] = [
  'Operador_Analista',
  'Operador_Gestor',
  'Operador_AdminTecnico',
];

// ── Estrutura de item de navegação ────────────────────────────────────────────
export interface NavItem {
  path: string;
  label: string;
  icon?: string;  // nome do ícone Lucide (resolvido no Sidebar)
  tooltip?: string;
  children?: NavItem[];
}

export interface NavSection {
  label: string;
  items: NavItem[];
}

// ── Seções de navegação por papel ─────────────────────────────────────────────

/** Seção PLD — visível para Analista e Gestor */
const SECTION_PLD: NavSection = {
  label: 'Meu Trabalho',
  items: [
    { path: '/dashboard',      label: 'Painel Diário',      icon: 'LayoutDashboard', tooltip: 'Resumo do dia: alertas, casos pendentes e KPIs' },
    { path: '/alerts',         label: 'Alertas',            icon: 'AlertTriangle',   tooltip: 'Fila de alertas para triagem por prioridade' },
    { path: '/cases',          label: 'Casos',              icon: 'FolderOpen',      tooltip: 'Investigações em andamento e histórico' },
    { path: '/cases/examples', label: 'Casos Exemplares',   icon: 'BookOpen',        tooltip: 'Casos fictícios para treinamento e referência' },
    { path: '/reports',        label: 'Comunicação ao Coaf',icon: 'FileBarChart2',   tooltip: 'Dossiês COS e relatórios regulatórios' },
    { path: '/notifications',  label: 'Notificações',       icon: 'Bell',            tooltip: 'Alertas e avisos enviados para você' },
  ],
};

/** Seção Gestão — exclusiva para Gestor */
const SECTION_GESTAO: NavSection = {
  label: 'Ajustes e Configurações',
  items: [
    { path: '/sensitivity',      label: 'Ajustes de Sensibilidade', icon: 'SlidersHorizontal', tooltip: 'Calibre o volume e precisão dos alertas' },
    { path: '/players',          label: 'Perfis de Apostadores',    icon: 'Users',             tooltip: 'Apostadores monitorados do operador' },
    { path: '/rules',            label: 'Condições de Risco',       icon: 'BookOpen',           tooltip: 'Regras de detecção no tenant' },
    { path: '/rules/builder',    label: 'Construtor Visual',        icon: 'Wand2',              tooltip: 'Editor guiado de condições de risco' },
    { path: '/rules/compound',   label: 'Condições Combinadas',     icon: 'GitBranch',          tooltip: 'Combinações de condições de risco' },
    { path: '/player-lists',     label: 'Listas de Monitoramento',  icon: 'List',               tooltip: 'Listas PEP, sanções e monitoramento especial' },
    { path: '/feature-store',    label: 'Base de Indicadores',      icon: 'Database',           tooltip: 'Qualidade e histórico dos indicadores usados pelo sistema' },
  ],
};

/** Seção Integração — exclusiva para AdminTecnico */
const SECTION_INTEGRACAO: NavSection = {
  label: 'Integração & Dados',
  items: [
    { path: '/mappings',      label: 'Integração de Dados',    icon: 'Plug',         tooltip: 'Configure mapeamentos de campos dos conectores' },
    { path: '/ingest-jobs',   label: 'Jobs de Ingestão',       icon: 'Activity',     tooltip: 'Histórico de envios de dados ao BetAML' },
    { path: '/ingest-errors', label: 'Quarentena de Erros',    icon: 'AlertOctagon', tooltip: 'Registros com falha de validação ou mapeamento' },
    { path: '/admin',         label: 'Usuários do Operador',   icon: 'Shield',       tooltip: 'Gerencie usuários, chaves e flags do seu tenant' },
    { path: '/settings',      label: 'Parâmetros de Sistema',  icon: 'Settings',     tooltip: 'Configurações técnicas do tenant' },
    { path: '/audit-logs',    label: 'Log de Auditoria',       icon: 'ScrollText',   tooltip: 'Registro de ações da plataforma' },
  ],
};

/** Seção Plataforma — exclusiva para BetAML_SuperAdmin */
const SECTION_PLATAFORMA: NavSection = {
  label: 'Plataforma BetAML',
  items: [
    { path: '/admin',            label: 'Operadores (Tenants)',    icon: 'Building2',      tooltip: 'Gerencie operadores e recursos administrativos da plataforma' },
    { path: '/admin/onboarding', label: 'Onboarding de Operadores', icon: 'LayoutTemplate', tooltip: 'Fluxo guiado de criação e bootstrap de novos tenants' },
    { path: '/model-registry',   label: 'Analisadores Automáticos', icon: 'BrainCircuit',   tooltip: 'Versionamento dos analisadores automáticos de risco' },
    { path: '/admin/ops',        label: 'Métricas da Plataforma',  icon: 'BarChart3',      tooltip: 'Saúde operacional e indicadores consolidados' },
    { path: '/audit-logs',       label: 'Auditoria da Plataforma', icon: 'ShieldCheck',    tooltip: 'Log de ações administrativas e trilha de auditoria' },
  ],
};

// ── mapa principal: papel → lista de seções ───────────────────────────────────
export const NAV_BY_ROLE: Record<AppRole, NavSection[]> = {
  Operador_Analista:    [SECTION_PLD],
  Operador_Gestor:      [SECTION_PLD, SECTION_GESTAO],
  Operador_AdminTecnico:[SECTION_INTEGRACAO],
  BetAML_SuperAdmin:    [SECTION_PLATAFORMA],
};

// Deployment mode vem de variável de ambiente build-time (NEXT_PUBLIC_DEPLOYMENT_MODE)
export type DeploymentMode = 'saas' | 'onprem';
export const DEPLOYMENT_MODE: DeploymentMode =
  (process.env.NEXT_PUBLIC_DEPLOYMENT_MODE as DeploymentMode) ?? 'saas';

// ── guarda de rota: caminho → papéis permitidos ───────────────────────────────
export const ROUTE_ROLES: Array<{ pattern: RegExp; roles: AppRole[] }> = [
  // PLD — Analista e Gestor
  { pattern: /^\/dashboard/,     roles: ['Operador_Analista', 'Operador_Gestor'] },
  { pattern: /^\/alerts/,        roles: ['Operador_Analista', 'Operador_Gestor'] },
  { pattern: /^\/cases/,         roles: ['Operador_Analista', 'Operador_Gestor'] },
  { pattern: /^\/players/,       roles: ['Operador_Analista', 'Operador_Gestor'] },
  { pattern: /^\/reports/,       roles: ['Operador_Analista', 'Operador_Gestor'] },
  { pattern: /^\/notifications/, roles: ['Operador_Analista', 'Operador_Gestor'] },
  { pattern: /^\/investigate/,   roles: ['Operador_Analista', 'Operador_Gestor'] },
  // Gestão — apenas Gestor
  { pattern: /^\/sensitivity/,   roles: ['Operador_Gestor'] },
  { pattern: /^\/rules/,         roles: ['Operador_Gestor'] },
  { pattern: /^\/player-lists/,  roles: ['Operador_Gestor'] },
  // Integração — apenas AdminTecnico
  { pattern: /^\/mappings/,      roles: ['Operador_AdminTecnico'] },
  { pattern: /^\/ingest/,        roles: ['Operador_AdminTecnico'] },
  { pattern: /^\/admin\/onboarding/, roles: ['BetAML_SuperAdmin'] },
  { pattern: /^\/admin\/ops/,   roles: ['Operador_Gestor', 'BetAML_SuperAdmin'] },
  { pattern: /^\/admin(?:\/)?$/, roles: ['Operador_AdminTecnico', 'BetAML_SuperAdmin'] },
  { pattern: /^\/settings/,      roles: ['Operador_AdminTecnico'] },
  { pattern: /^\/audit-logs/,    roles: ['Operador_AdminTecnico', 'Operador_Gestor', 'Operador_Analista', 'BetAML_SuperAdmin'] },
  // Plataforma — apenas SuperAdmin
  { pattern: /^\/platform/,      roles: ['BetAML_SuperAdmin'] },
  { pattern: /^\/model-registry/,roles: ['Operador_Gestor', 'Operador_AdminTecnico', 'BetAML_SuperAdmin'] },
  { pattern: /^\/feature-store/, roles: ['Operador_Gestor', 'BetAML_SuperAdmin'] },
];

export function getAllowedRolesForPath(path: string): AppRole[] | null {
  const match = ROUTE_ROLES.find(({ pattern }) => pattern.test(path));
  return match?.roles ?? null;
}

/**
 * Retorna as seções de navegação para o conjunto de papéis do usuário.
 * Usuários com múltiplos papéis (ex: Analista + AdminTecnico) veem a união.
 * Em modo on-prem, a seção "Plataforma BetAML" é suprimida pois o console
 * multi-tenant não existe em instalações single-tenant.
 */
export function getNavSections(
  userRoles: string[],
  deploymentMode: DeploymentMode = DEPLOYMENT_MODE,
): NavSection[] {
  const seen = new Set<string>();
  const sections: NavSection[] = [];

  for (const role of userRoles as AppRole[]) {
    const roleSections = NAV_BY_ROLE[role] ?? [];
    for (const section of roleSections) {
      // Suprimir seção de plataforma em on-prem
      if (deploymentMode === 'onprem' && section === SECTION_PLATAFORMA) {
        continue;
      }
      if (!seen.has(section.label)) {
        seen.add(section.label);
        sections.push(section);
      }
    }
  }
  return sections;
}

/**
 * Retorna true se o usuário tem acesso à rota especificada.
 */
export function canAccessRoute(path: string, userRoles: string[]): boolean {
  const allowedRoles = getAllowedRolesForPath(path);
  if (!allowedRoles) return true;  // rotas sem guard são públicas dentro do layout protegido
  return allowedRoles.some((r) => userRoles.includes(r));
}

/**
 * Retorna true se o usuário tem o papel especificado.
 */
export function hasRole(userRoles: string[], role: AppRole): boolean {
  return userRoles.includes(role);
}

/**
 * Retorna true se o usuário tem ao menos um dos papéis especificados.
 */
export function hasAnyRole(userRoles: string[], roles: AppRole[]): boolean {
  return roles.some((r) => userRoles.includes(r));
}
