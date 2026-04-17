'use client';
/**
 * OnboardingTour — tour guiado exibido na 1ª vez que um analista acessa o sistema.
 *
 * Persiste em localStorage por chave `betaml_tour_done_v1`.
 * Cada passo aponta para uma tela e descreve, em linguagem simples, o que o
 * analista encontra lá.
 */
import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import {
  AlertTriangle, FolderOpen, SlidersHorizontal, FileBarChart2,
  ChevronRight, ChevronLeft, X, Wand2, BookOpen, Sparkles,
} from 'lucide-react';
import { createPortal } from 'react-dom';

const STORAGE_KEY = 'betaml_tour_done_v1';

interface TourStep {
  id: number;
  icon: React.ElementType;
  title: string;
  description: string;
  hint: string;
  href: string;
  linkLabel: string;
  color: string;
}

const STEPS: TourStep[] = [
  {
    id: 1,
    icon: AlertTriangle,
    title: 'Monitor de Alertas',
    description:
      'É aqui que o dia começa. Você verá alertas organizados por prioridade: Crítico, Alto, Médio e Baixo. ' +
      'Cada card explica, em duas ou três linhas, por que o sistema sinalizou aquele apostador.',
    hint: 'Ação rápida: "Abrir caso" transforma um alerta em investigação formal.',
    href: '/alerts',
    linkLabel: 'Ir para Monitor de Alertas',
    color: 'text-red-600',
  },
  {
    id: 2,
    icon: FolderOpen,
    title: 'Casos em Investigação',
    description:
      'Cada caso reúne tudo num só lugar: resumo da suspeita, perfil do apostador, movimentações, ' +
      'vínculos com outras pessoas e o espaço para sua decisão final.',
    hint: 'Use a barra de anotações no rodapé para registrar observações enquanto lê o dossiê.',
    href: '/cases',
    linkLabel: 'Ver Casos em Investigação',
    color: 'text-indigo-600',
  },
  {
    id: 3,
    icon: SlidersHorizontal,
    title: 'Ajustes de Sensibilidade',
    description:
      'Se o sistema está gerando alertas demais (ou de menos), você pode calibrar aqui — ' +
      'sem precisar mexer em código. Arraste os sliders e veja a estimativa de impacto nos últimos 30 dias.',
    hint: 'A soma dos três pesos deve ser 100%. Use "Simular impacto" antes de salvar.',
    href: '/sensitivity',
    linkLabel: 'Calibrar Sensibilidade',
    color: 'text-brand',
  },
  {
    id: 4,
    icon: FileBarChart2,
    title: 'Relatórios Reguladores',
    description:
      'Gere dossiês e XMLs para envio ao COAF com um clique, diretamente de dentro do caso. ' +
      'O sistema preenche os campos Siscoaf automaticamente com base na sua narrativa.',
    hint: 'O relatório XML só pode ser gerado para casos com status "Encerrado" ou "Reportado".',
    href: '/reports',
    linkLabel: 'Ver Relatórios',
    color: 'text-emerald-600',
  },
  {
    id: 5,
    icon: Wand2,
    title: 'Construtor de Regras',
    description:
      'Crie condições de risco sem escrever código: escolha campo, operador e valor. ' +
      'Tem modelos prontos para Estruturação, PEP, Layering e mais. ' +
      'Disponível apenas para perfis sênior/administrador.',
    hint: 'Use "Simular antes de publicar" para ver quantos alertas a regra geraria nos últimos 30 dias.',
    href: '/rules/builder',
    linkLabel: 'Construtor de Regras',
    color: 'text-purple-600',
  },
  {
    id: 6,
    icon: BookOpen,
    title: 'Casos de Exemplo',
    description:
      'Acesse casos fictícios completos para entender como uma boa análise PLD fica ' +
      'documentada — ideal para novos analistas ou para treinamento.',
    hint: 'Esses casos não geram alertas reais; são apenas material educativo.',
    href: '/cases/examples',
    linkLabel: 'Ver Exemplos',
    color: 'text-amber-600',
  },
];

interface OnboardingTourProps {
  /** Se true, exibe o tour mesmo que já tenha sido concluído (para forçar re-exibição). */
  forceOpen?: boolean;
  onClose?: () => void;
}

export default function OnboardingTour({ forceOpen = false, onClose }: OnboardingTourProps) {
  const [visible, setVisible]   = useState(false);
  const [step, setStep]         = useState(0);
  const [mounted, setMounted]   = useState(false);

  useEffect(() => {
    setMounted(true);
    if (forceOpen) {
      setVisible(true);
      return;
    }

    // Evita bloquear execucoes automatizadas (Playwright/webdriver) com um modal global.
    if (typeof navigator !== 'undefined' && navigator.webdriver) {
      localStorage.setItem(STORAGE_KEY, '1');
      setVisible(false);
      return;
    }

    const done = localStorage.getItem(STORAGE_KEY);
    if (!done) setVisible(true);
  }, [forceOpen]);

  const dismiss = useCallback(() => {
    localStorage.setItem(STORAGE_KEY, '1');
    setVisible(false);
    onClose?.();
  }, [onClose]);

  const next = () => setStep((s) => Math.min(s + 1, STEPS.length - 1));
  const prev = () => setStep((s) => Math.max(s - 1, 0));

  if (!mounted || !visible) return null;

  const current = STEPS[step];
  const Icon    = current.icon;
  const isLast  = step === STEPS.length - 1;

  const modal = (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Tour de onboarding BetAML"
      onClick={(e) => { if (e.target === e.currentTarget) dismiss(); }}
    >
      <div className="relative mx-4 w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl">
        {/* Barra de progresso */}
        <div className="flex h-1.5 w-full bg-gray-100">
          {STEPS.map((s, i) => (
            <div
              key={s.id}
              className={`h-full flex-1 transition-colors ${i <= step ? 'bg-brand' : 'bg-gray-100'}`}
              style={{ borderRight: i < STEPS.length - 1 ? '2px solid white' : undefined }}
            />
          ))}
        </div>

        {/* Conteúdo */}
        <div className="p-7">
          {/* Fechar */}
          <button
            onClick={dismiss}
            aria-label="Fechar tour"
            className="absolute right-4 top-4 rounded-full p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
          >
            <X size={16} />
          </button>

          {/* Badge passo */}
          <div className="mb-4 flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand/10 text-xs font-bold text-brand">
              {step + 1}
            </span>
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
              de {STEPS.length}
            </span>
            {step === 0 && (
              <span className="ml-2 flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700">
                <Sparkles size={10} /> Novidade
              </span>
            )}
          </div>

          {/* Ícone + título */}
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gray-50">
              <Icon size={22} className={current.color} />
            </div>
            <h2 className="text-xl font-bold text-gray-900">{current.title}</h2>
          </div>

          {/* Descrição */}
          <p className="text-sm leading-relaxed text-gray-600">{current.description}</p>

          {/* Dica */}
          <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3">
            <p className="text-xs text-blue-800">
              <span className="mr-1 font-bold">💡 Dica:</span>
              {current.hint}
            </p>
          </div>

          {/* Rodapé com navegação */}
          <div className="mt-6 flex items-center justify-between">
            <Link
              href={current.href}
              onClick={dismiss}
              className="flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline"
            >
              {current.linkLabel}
              <ChevronRight size={14} />
            </Link>

            <div className="flex items-center gap-2">
              {step > 0 && (
                <button
                  onClick={prev}
                  className="flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50"
                >
                  <ChevronLeft size={13} /> Anterior
                </button>
              )}
              {isLast ? (
                <button
                  onClick={dismiss}
                  className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-brand/90"
                >
                  Começar a usar <Sparkles size={14} />
                </button>
              ) : (
                <button
                  onClick={next}
                  className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-brand/90"
                >
                  Próximo <ChevronRight size={14} />
                </button>
              )}
            </div>
          </div>

          {/* Skip */}
          <button
            onClick={dismiss}
            className="mt-4 w-full text-center text-xs text-gray-400 hover:text-gray-600 transition-colors"
          >
            Pular tour e começar diretamente
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
