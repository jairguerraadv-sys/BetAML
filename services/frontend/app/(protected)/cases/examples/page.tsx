'use client';
import { useState } from 'react';
import {
  BookOpen, ChevronRight, ChevronDown, AlertTriangle,
  CheckCircle, XCircle, User, ArrowRight, Tag,
} from 'lucide-react';

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface ExampleCase {
  id: string;
  title: string;
  pattern: string;
  tags: string[];
  priority: 'HIGH' | 'MEDIUM' | 'LOW';
  profile: {
    name: string;
    occupation: string;
    age: number;
    monthlyIncome: string;
  };
  flagReason: string;
  analysisSteps: string[];
  redFlags: string[];
  mitigatingFactors: string[];
  decision: 'REPORTED' | 'CLOSED' | 'INVESTIGATING';
  decisionRationale: string;
  lesson: string;
}

// ─── Dados fictícios ──────────────────────────────────────────────────────────

const EXAMPLES: ExampleCase[] = [
  {
    id: 'ex-001',
    title: 'Fracionamento via Pix (Smurfing Digital)',
    pattern: 'Estruturação de depósitos via Pix',
    tags: ['Fracionamento', 'Pix', 'Risco Alto'],
    priority: 'HIGH',
    profile: {
      name: 'Carlos Mendonça da Silva (fictício)',
      occupation: 'Vendedor autônomo',
      age: 42,
      monthlyIncome: 'R$ 3.200',
    },
    flagReason:
      'O sistema detectou 14 depósitos Pix de R$\u00a0950 cada em 48 horas, provenientes de 8 chaves Pix distintas (CPF e e-mail), totalizando R$\u00a013.300. Todos abaixo do limiar de R$\u00a01.000 que historicamente atrai atenção dos controles internos do operador.',
    analysisSteps: [
      'Verificar as chaves Pix originárias: pertencem a terceiros sem relação comprovada com o apostador.',
      'Consultar histórico: apostador realizava em média 1 depósito/mês de R$\u00a0200 nos 12 meses anteriores.',
      'Analisar horários: depósitos distribuídos entre 23h e 6h — janela que evita equipes de monitoramento.',
      'Verificar se os CPFs das chaves Pix de origem aparecem em outras investigações da plataforma.',
    ],
    redFlags: [
      'Valor unitário consistentemente abaixo de R$\u00a01.000 — padrão clássico de estruturação (art. 11, Lei 9.613/1998).',
      'Origem em múltiplas chaves Pix de terceiros sem relação comprovada com o apostador.',
      'Horário concentrado na madrugada, fora do padrão comportamental anterior.',
    ],
    mitigatingFactors: [
      'Apostador possui cadastro há 3 anos sem ocorrências PLD anteriores.',
      'Renda declarada poderia justificar parte do volume em contexto diferente.',
    ],
    decision: 'REPORTED',
    decisionRationale:
      'A combinação de fracionamento abaixo do limiar de atenção, origem em múltiplas chaves de terceiros e horário atípico configura estruturação (smurfing digital via Pix). Comunicado ao COAF nos termos da Portaria SPA/MF 1.143/2024.',
    lesson:
      'No ambiente de apostas online, o equivalente ao "smurfing" bancário é o fracionamento via múltiplas chaves Pix de terceiros. O indicador-chave é o valor sistematicamente abaixo de um limiar perceptível combinado com origens diversas.',
  },
  {
    id: 'ex-002',
    title: 'Movimentação Atípica em Conta Empresarial',
    pattern: 'Entrada e saída rápida',
    tags: ['PEP', 'Empresa', 'Passagem de Valores'],
    priority: 'HIGH',
    profile: {
      name: 'Tech Solutions Consultoria Ltda. (fictícia)',
      occupation: 'Consultoria de TI - ME',
      age: 34, // anos de existência da empresa
      monthlyIncome: 'Faturamento médio R$ 18.000/mês',
    },
    flagReason:
      'A conta recebeu R$ 250.000 via TED de empresa não relacionada e transferiu 98% do valor para 5 contas PF em menos de 6 horas.',
    analysisSteps: [
      'Identificar o remetente do TED: empresa de fachada sem site ou registros fiscais ativos.',
      'Verificar os 5 destinatários PF: CPFs aparecem em outras investigações de lavagem.',
      'Checar objeto social da ME: consultoria de TI não justifica movimentação financeira dessa magnitude.',
      'Solicitar extrato completo dos últimos 6 meses para verificar padrão.',
    ],
    redFlags: [
      'Empresa com menos de 1 ano de abertura e nenhum funcionário registrado.',
      'Sócios com vínculos a outras empresas investigadas.',
      '99% do valor saiu em < 6 horas — conta usada como passagem.',
    ],
    mitigatingFactors: [
      'Sócio-administrador não consta em listagens de PEP ou sanções internacionais.',
    ],
    decision: 'REPORTED',
    decisionRationale:
      'Estrutura de "conta mula" clássica. Objeto social incompatível com o volume, ausência de tempo de maturação dos recursos e dispersão rápida para PFs configuram tipologia de interposição de terceiros.',
    lesson:
      'Empresas jovens com objeto social genérico e movimentação desproporcional ao porte são veículos clássicos de lavagem. Analise sempre a velocidade de saída dos recursos após a entrada.',
  },
  {
    id: 'ex-003',
    title: 'Saques Pix para Contas Suspeitas',
    pattern: 'Saques em rápida sucessão para contas não relacionadas',
    tags: ['Layering', 'Pix', 'Contas Mulá'],
    priority: 'MEDIUM',
    profile: {
      name: 'Fernanda Queiroz Leal (fictícia)',
      occupation: 'Funcionária pública estadual',
      age: 37,
      monthlyIncome: 'R$ 8.500',
    },
    flagReason:
      'A apostadora realizou 8 saques via Pix para 5 chaves CPF/CNPJ diferentes em menos de 6 horas, logo após depositar R$\u00a018.000. Nenhuma das contas de destino possui relacionamento anterior com ela.',
    analysisSteps: [
      'Identificar as chaves Pix de destino: 4 CPFs cadastrados há menos de 30 dias no Bacen.',
      'Verificar se os destinatários aparecem em outras investigações: 2 CPFs com ligação a casos de lavagem.',
      'Checar padrão após o saque: conta da apostadora ficou com saldo residual < R$\u00a050 — conta usada como passagem.',
      'Solicitar extrato completo dos últimos 6 meses para verificar recorrência.',
    ],
    redFlags: [
      'Contas de destino com data de abertura muito recente e sem movimentação prévia.',
      'Volume total retirado em 6 horas representou 96% do depósito — conta de passagem.',
      'Ausência de qualquer relação entre apostadora e beneficiários identificada no CRM.',
    ],
    mitigatingFactors: [
      'Apostadora sem histórico de inadimplência ou ocorrências graves.',
      'Parte do volume pode ser compatível com transferência familiar não declarada.',
    ],
    decision: 'INVESTIGATING',
    decisionRationale:
      'Caso não é conclusivo. Solicitou-se à equipe de KYC contato com a apostadora para esclarecimentos antes da decisão final sobre comunicação ao COAF. O prazo de investigação é de 15 dias úteis.',
    lesson:
      'Saques Pix em rápida sucessão para múltiplas contas recém-abertas são o equivalente eletrônico da \"conta mula\". A fase de investigação existe para coletar contexto adicional antes de comunicar.',
  },
  {
    id: 'ex-004',
    title: 'Rejeição Correta: Aposentado com Herança',
    pattern: 'Movimentação atípica justificada',
    tags: ['Herança', 'Rejeição', 'Boa Prática'],
    priority: 'LOW',
    profile: {
      name: 'João Aparecido Ramos (fictício)',
      occupation: 'Aposentado',
      age: 71,
      monthlyIncome: 'R$ 2.400 (benefício)',
    },
    flagReason:
      'Depósito único de R$ 380.000 em conta com histórico médio de R$ 2.000/mês gerou alerta de alta variação.',
    analysisSteps: [
      'Contato com apostador via equipe de KYC/Compliance: informou recebimento de herança.',
      'Solicitação de documentação: escritura de inventário e certidão de formal de partilha apresentados.',
      'Verificação do inventariante: advogado regularmente inscrito na OAB.',
      'Origem dos recursos: conta do espólio em outro banco, com comprovante de TED.',
    ],
    redFlags: [
      'Variação brusca no volume para o perfil cadastral do apostador.',
    ],
    mitigatingFactors: [
      'Documentação completa e consistente apresentada voluntariamente.',
      'Apostador com 15 anos de relacionamento sem ocorrências.',
      'Origem dos recursos rastreável e compatível com herança declarada.',
    ],
    decision: 'CLOSED',
    decisionRationale:
      'Após análise documental, a movimentação é consistente com herança legítima devidamente comprovada. Alerta encerrado sem comunicação ao COAF. Cadastro atualizado com nova faixa de renda esperada.',
    lesson:
      'Alertas de variação de padrão não são necessariamente ilícitos. Documente bem a justificativa antes de encerrar o caso — o registro é tão importante quanto a decisão.',
  },
];

// ─── Configurações de estilo ───────────────────────────────────────────────────

const PRIORITY_CFG = {
  HIGH:   { label: 'Alta',  cls: 'bg-red-100 text-red-700' },
  MEDIUM: { label: 'Média', cls: 'bg-orange-100 text-orange-700' },
  LOW:    { label: 'Baixa', cls: 'bg-green-100 text-green-700' },
};

const DECISION_CFG = {
  REPORTED:     { label: 'Comunicado ao COAF', cls: 'text-red-700', icon: AlertTriangle },
  CLOSED:       { label: 'Encerrado sem comunicação', cls: 'text-green-700', icon: CheckCircle },
  INVESTIGATING:{ label: 'Em investigação', cls: 'text-indigo-700', icon: ArrowRight },
};

// ─── Componente de cartão ─────────────────────────────────────────────────────

function ExampleCard({ ex }: { ex: ExampleCase }) {
  const [open, setOpen] = useState(false);
  const pCfg = PRIORITY_CFG[ex.priority];
  const dCfg = DECISION_CFG[ex.decision];
  const DecisionIcon = dCfg.icon;

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-shadow hover:shadow-md">
      {/* Cabeçalho */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-4 p-5 text-left"
        aria-expanded={open}
      >
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
          <BookOpen size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">{ex.title}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${pCfg.cls}`}>
              {pCfg.label}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-gray-500">{ex.pattern}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {ex.tags.map((t) => (
              <span key={t} className="flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-600">
                <Tag size={9} />{t}
              </span>
            ))}
          </div>
        </div>
        <div className="shrink-0 mt-1">
          {open ? <ChevronDown size={16} className="text-gray-400" /> : <ChevronRight size={16} className="text-gray-400" />}
        </div>
      </button>

      {/* Conteúdo expandido */}
      {open && (
        <div className="border-t border-gray-100 px-5 pb-6 pt-4 space-y-5 text-sm">
          {/* Perfil fictício */}
          <section>
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">
              <User size={12} /> Perfil do apostador (fictício)
            </h3>
            <div className="mt-2 grid grid-cols-2 gap-2 rounded-lg border border-dashed border-gray-200 bg-gray-50 p-3 text-xs text-gray-700 md:grid-cols-4">
              <div><span className="block text-gray-400">Nome</span>{ex.profile.name}</div>
              <div><span className="block text-gray-400">Atividade</span>{ex.profile.occupation}</div>
              <div><span className="block text-gray-400">Idade</span>{ex.profile.age} anos</div>
              <div><span className="block text-gray-400">Renda / Faturamento</span>{ex.profile.monthlyIncome}</div>
            </div>
          </section>

          {/* Por que o sistema alertou */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Por que o sistema alertou
            </h3>
            <p className="mt-1.5 text-sm leading-relaxed text-gray-700">{ex.flagReason}</p>
          </section>

          {/* Passos de análise */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Como analisar este caso
            </h3>
            <ol className="mt-2 space-y-1.5">
              {ex.analysisSteps.map((step, i) => (
                <li key={i} className="flex gap-2 text-gray-700">
                  <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[10px] font-bold text-indigo-700">
                    {i + 1}
                  </span>
                  {step}
                </li>
              ))}
            </ol>
          </section>

          {/* Red flags e fatores atenuantes */}
          <div className="grid gap-4 md:grid-cols-2">
            <section>
              <h3 className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-red-700">
                <AlertTriangle size={11} /> Sinais de alerta
              </h3>
              <ul className="mt-2 space-y-1">
                {ex.redFlags.map((f, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-xs text-gray-700">
                    <XCircle size={12} className="mt-0.5 shrink-0 text-red-500" />
                    {f}
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <h3 className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-green-700">
                <CheckCircle size={11} /> Fatores atenuantes
              </h3>
              <ul className="mt-2 space-y-1">
                {ex.mitigatingFactors.map((f, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-xs text-gray-700">
                    <CheckCircle size={12} className="mt-0.5 shrink-0 text-green-500" />
                    {f}
                  </li>
                ))}
              </ul>
            </section>
          </div>

          {/* Decisão */}
          <section className="rounded-lg border border-gray-200 bg-gray-50 p-4">
            <div className={`flex items-center gap-1.5 text-xs font-semibold ${dCfg.cls}`}>
              <DecisionIcon size={13} />
              Decisão: {dCfg.label}
            </div>
            <p className="mt-2 text-sm leading-relaxed text-gray-700">{ex.decisionRationale}</p>
          </section>

          {/* Lição */}
          <section className="rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3">
            <p className="text-xs font-semibold text-indigo-700">💡 Lição do caso</p>
            <p className="mt-1 text-sm leading-relaxed text-indigo-900">{ex.lesson}</p>
          </section>
        </div>
      )}
    </div>
  );
}

// ─── Página principal ─────────────────────────────────────────────────────────

export default function CaseExamplesPage() {
  const [filter, setFilter] = useState<'ALL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'REPORTED' | 'CLOSED' | 'INVESTIGATING'>('ALL');

  const filtered = EXAMPLES.filter((e) => {
    if (filter === 'ALL') return true;
    if (filter === 'HIGH' || filter === 'MEDIUM' || filter === 'LOW') return e.priority === filter;
    return e.decision === filter;
  });

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      {/* Cabeçalho */}
      <div>
        <div className="flex items-center gap-2">
          <BookOpen size={20} className="text-indigo-600" />
          <h1 className="text-xl font-semibold text-gray-900">Biblioteca de Casos Exemplares</h1>
        </div>
        <p className="mt-1 text-sm text-gray-500">
          Casos fictícios elaborados para treinamento e referência de boas práticas em análise PLD.
          Nenhum dado real é utilizado.
        </p>
        <div className="mt-2 inline-flex items-center gap-1 rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-700 border border-amber-200">
          <AlertTriangle size={11} /> Todos os nomes, valores e situações são fictícios
        </div>
      </div>

      {/* Filtros */}
      <div className="flex flex-wrap gap-2">
        {[
          { v: 'ALL',          l: 'Todos' },
          { v: 'HIGH',         l: 'Risco Alto' },
          { v: 'MEDIUM',       l: 'Risco Médio' },
          { v: 'REPORTED',     l: 'Comunicados ao COAF' },
          { v: 'CLOSED',       l: 'Encerrados' },
          { v: 'INVESTIGATING',l: 'Em investigação' },
        ].map(({ v, l }) => (
          <button
            key={v}
            onClick={() => setFilter(v as typeof filter)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              filter === v
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {l}
          </button>
        ))}
      </div>

      {/* Cartões */}
      <div className="space-y-4">
        {filtered.length === 0 ? (
          <p className="py-8 text-center text-sm text-gray-400">Nenhum caso corresponde ao filtro selecionado.</p>
        ) : (
          filtered.map((ex) => <ExampleCard key={ex.id} ex={ex} />)
        )}
      </div>

      {/* Rodapé */}
      <p className="text-center text-xs text-gray-400">
        Estes exemplos seguem as tipologias publicadas pelo COAF e pelo BACEN. Consulte sempre
        a equipe de Compliance para casos com dúvidas reais.
      </p>
    </div>
  );
}
