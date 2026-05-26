# Guia de Migração - Sistema de Tradução BetAML

**Versão:** 1.0.0  
**Data:** 26/05/2026  
**Objetivo:** Eliminar jargão técnico e padronizar traduções em todo o frontend

---

## 📚 Recursos Criados

### 1. Glossário Centralizado (`lib/glossary.ts`)
- **150+ termos** mapeados (backend → UI operacional)
- Funções de tradução para todos os enums:
  - `translateSeverity()`, `translateAlertType()`, `translateAlertStatus()`
  - `translateCaseStatus()`, `translateCaseDecision()`, `translateCOSStatus()`
  - `translateRiskBand()`, `translateGameType()`, etc.
- Correção automática **SAR → COS** (contexto brasileiro)

### 2. Hook Personalizado (`lib/use-glossary.ts`)
```typescript
import { useGlossary } from '@/lib/use-glossary';

function MyComponent() {
  const { t, translate, style } = useGlossary();
  
  return (
    <>
      <h1>{t('dashboard.title')}</h1>
      <Badge className={style.severityColor(alert.severity)}>
        {translate.severity(alert.severity)}
      </Badge>
    </>
  );
}
```

### 3. Componentes Badge Reutilizáveis (`components/badges.tsx`)
```typescript
import { 
  SeverityBadge, 
  AlertStatusBadge, 
  CaseStatusBadge,
  RiskBandBadge,
  GameTypeBadge,
  AlertTypeBadge,
  CaseDecisionBadge 
} from '@/components/badges';

// Uso:
<SeverityBadge severity="HIGH" size="md" />           // "Alta"
<AlertStatusBadge status="IN_PROGRESS" />             // "Em análise"
<CaseDecisionBadge decision="FILE_SAR" />             // "Comunicar ao Coaf"
<RiskBandBadge riskBand="HIGH" />                     // "Risco alto"
<GameTypeBadge gameType="SPORTSBOOK" />               // "Apostas esportivas"
```

### 4. Utilitários (`lib/utils.ts`)
- `cn()` - merge de classes Tailwind
- `formatCurrency()`, `formatPercent()`, `truncate()`

### 5. i18n Expandido (`lib/i18n.ts`)
- Chaves de navegação atualizadas
- Seções para Casos, Coaf/COS, Alertas, Jogadores
- Preparado para inglês (en-US) quando necessário

---

## 🔄 Como Migrar Código Existente

### ❌ ANTES (não fazer mais):

```typescript
// ❌ Traduções locais duplicadas
const SEV_PT = {
  HIGH: 'Alto',
  MEDIUM: 'Médio',
  // ...
};

// ❌ Enum cru exposto
<Badge>{alert.severity}</Badge>  // "HIGH"

// ❌ Textos hardcoded
<span>Comunicar SAR ao COAF</span>
```

### ✅ DEPOIS (novo padrão):

```typescript
// ✅ Usar hook de glossário
const { translate } = useGlossary();
<Badge>{translate.severity(alert.severity)}</Badge>  // "Alta"

// ✅ Ou usar componente badge pronto
<SeverityBadge severity={alert.severity} />

// ✅ Textos via i18n
const { t } = useGlossary();
<h1>{t('coaf.cos_full')}</h1>  // "Comunicação de Operação Suspeita"
```

---

## 🎯 Checklist de Migração por Arquivo

Ao atualizar um arquivo `.tsx`:

- [ ] **Remover traduções locais** (SEV_PT, STATUS_PT, etc)
- [ ] **Importar hook**: `import { useGlossary } from '@/lib/use-glossary'`
- [ ] **Substituir enums crus** por `translate.severity()`, `translate.alertType()`, etc
- [ ] **Usar badges prontos** em vez de `<span>` customizados
- [ ] **Substituir "SAR"** por **"COS"** em textos visíveis
- [ ] **Usar i18n** para textos fixos: `t('nav.dashboard')`, `t('btn.save')`

---

## 📖 Glossário Rápido - Termos Mais Comuns

| Backend (enum) | UI Brasileira |
|---|---|
| `severity: "HIGH"` | "Alta" |
| `status: "OPEN"` | "Aberto" |
| `status: "IN_PROGRESS"` | "Em análise" |
| `alert_type: "RULE"` | "Regra automática" |
| `alert_type: "ML_ANOMALY"` | "Inteligência artificial" |
| `decision: "FILE_SAR"` | "Comunicar ao Coaf" ⚠️ |
| `risk_band: "HIGH"` | "Risco alto" |
| `game_type: "SPORTSBOOK"` | "Apostas esportivas" |
| `game_type: "SLOT"` | "Caça-níqueis" |

---

## ⚠️ ATENÇÃO: SAR vs COS

**Contexto brasileiro oficial:**
- ❌ SAR (Suspicious Activity Report) = termo americano
- ❌ STR (Suspicious Transaction Report) = termo FATF
- ✅ **COS (Comunicação de Operação Suspeita) = termo brasileiro (Coaf)**

**No código:**
- Backend mantém `FILE_SAR` (compatibilidade técnica)
- **UI SEMPRE exibe "COS"** ou "Comunicar ao Coaf"
- Função `translateCaseDecision()` faz conversão automática

**Fundamentação legal:**
- Lei 9.613/98 (Lei de Lavagem de Dinheiro)
- Circulares do Coaf
- Glossário oficial da SPA (Secretaria de Prêmios e Apostas)

---

## 🚀 Exemplos Práticos

### Exemplo 1: Card de Alerta

```typescript
// ANTES
<div>
  <span className="bg-red-100 text-red-700">
    {alert.severity === 'HIGH' ? 'Alto' : 'Médio'}
  </span>
  <p>{alert.alert_type}</p>  {/* "RULE" */}
</div>

// DEPOIS
import { SeverityBadge, AlertTypeBadge } from '@/components/badges';

<div>
  <SeverityBadge severity={alert.severity} />
  <AlertTypeBadge alertType={alert.alert_type} />  {/* "Regra automática" */}
</div>
```

### Exemplo 2: Decisão de Caso

```typescript
// ANTES
<select>
  <option value="FILE_SAR">Comunicar SAR ao COAF</option>
  <option value="NO_ACTION">Sem ação</option>
</select>

// DEPOIS
import { CASE_DECISION_LABELS } from '@/lib/glossary';

<select>
  {Object.entries(CASE_DECISION_LABELS).map(([value, label]) => (
    <option key={value} value={value}>{label}</option>
    // "FILE_SAR" exibe "Comunicar ao Coaf"
  ))}
</select>
```

### Exemplo 3: Timeline de Caso

```typescript
// ANTES
<div>Status mudou para IN_PROGRESS</div>

// DEPOIS
const { translate } = useGlossary();

<div>
  Status mudou para {translate.caseStatus('IN_PROGRESS')}
  {/* "Em investigação" */}
</div>
```

---

## 🧪 Como Testar

1. **Rodar frontend:**
   ```bash
   cd services/frontend
   npm run dev
   ```

2. **Verificar páginas:**
   - `/dashboard` - Dashboard
   - `/alerts` - Lista de alertas
   - `/cases` - Lista de casos
   - `/cases/[id]` - Detalhe de caso

3. **Checklist visual:**
   - [ ] Zero texto em inglês (HIGH, OPEN, RULE, etc)
   - [ ] Badges coloridos funcionando
   - [ ] "COS" em vez de "SAR" em decisões de caso
   - [ ] Tooltips e mensagens em português claro

---

## 📝 Regras de Ouro

1. **Zero duplicação:** Uma fonte de verdade (`glossary.ts`)
2. **Zero jargão técnico:** Backend enums NUNCA expostos na UI
3. **Contexto brasileiro:** COS, não SAR
4. **Componentes reutilizáveis:** Badges prontos, não `<span>` repetidos
5. **i18n preparado:** Fácil adicionar inglês depois

---

## 🛠️ Troubleshooting

**Problema:** `Cannot find module '@/lib/use-glossary'`  
**Solução:** Verificar tsconfig paths: `"@/*": ["./"]`

**Problema:** Badge não traduzindo  
**Solução:** Verificar se enum está em UPPERCASE no glossário

**Problema:** "SAR" ainda aparece na UI  
**Solução:** Buscar `grep -r "SAR" app/ components/` e substituir por COS

---

## 📚 Referências

- Glossário completo: `/lib/glossary.ts`
- Exemplos de uso: `/components/badges.tsx`
- Traduções i18n: `/lib/i18n.ts`
- Hook principal: `/lib/use-glossary.ts`

---

**Dúvidas?** Este sistema foi projetado para ser intuitivo. Se algo não está claro, é bug de UX — reporte!
