# BetAML - Backlog de Product Readiness

Estado atual consolidado: MVP avancado, apto para piloto controlado, ainda abaixo do padrao de produto comercial completo para PLD bancario.

## P0 - Bloqueadores de confianca operacional

1. Corrigir navegacao quebrada e contratos falsos entre frontend e backend.
   - Remover ou implementar rotas expostas em menu sem pagina funcional.
   - Alinhar RBAC real de middleware, nav-config e paginas protegidas.
   - Critério de aceite: nenhum link do menu principal leva a 404, tela vazia ou fluxo sem backend correspondente.

2. Eliminar mocks em caminhos operacionais de validacao externa.
   - Isolar provider mock apenas para dev e testes.
   - Falhar de forma explicita quando o ambiente exigir integracao real e ela nao estiver configurada.
   - Critério de aceite: ambiente operacional nao executa validacao mock silenciosa.

3. Alinhar topologia Kafka entre bootstrap, produtores e consumidores.
   - Revisar topicos inicializados no compose e topicos realmente consumidos pelos servicos.
   - Corrigir contratos raw, canonical, features, alerts e replay.
   - Critério de aceite: smoke end to end gera evento, feature e alerta sem dependencia de topico ausente.

4. Endurecer segredos, PII e logging.
   - Remover defaults inseguros fora de dev.
   - Revisar decriptacao, mascaramento e logs com payload bruto.
   - Critério de aceite: nenhum caminho produtivo depende de segredo padrao ou vaza PII em logs operacionais.

## P1 - Fechamento funcional para operacao real

5. Completar coerencia do fluxo alerta -> caso -> dossie -> report package.
   - Garantir referencias, status, atribuicao, narrativa e exportacao consistentes.
   - Critério de aceite: fluxo do analista funciona ponta a ponta sem dados sintéticos obrigatorios.

6. Endurecer camada de ML para uso controlado.
   - Separar bootstrap sintético de runtime produtivo.
   - Tornar challenger, promocao e explainability auditaveis e previsiveis.
   - Critério de aceite: inferencia online nao usa fallback sintético silencioso em ambiente operacional.

7. Limpar residuos de teste e seeds em ambientes de demonstracao e staging.
   - Evitar casos, alertas e entidades de teste convivendo com dados operacionais.
   - Critério de aceite: ambiente alvo possui dataset coerente e rastreavel por tenant.

## P2 - Go live defensavel

8. Fechar readiness operacional.
   - Backup restore validado.
   - Rollback ensaiado.
   - Alertas, dashboards e runbooks revisados com evidencias.
   - Critério de aceite: checklist de go live pode ser executado sem lacunas materiais.

9. Executar carga sustentada com metas SLO.
   - Medir p95, taxa de erro, backlog e throughput de ingestao.
   - Critério de aceite: SLO acordado aprovado em teste reproduzivel.

10. Formalizar trilha de producao.
   - Secret manager externo.
   - TLS e ingress produtivos.
   - Janela de deploy, rollback e aceite de Operacoes e Compliance.
   - Critério de aceite: parecer final muda de aprovado condicional para pronto para producao.

## Ordem sugerida de execucao por agente

1. BetAML UI API Contract Agent
2. BetAML Real Pipeline Agent
3. BetAML Security and PII Agent
4. BetAML Case and Report Agent
5. BetAML ML Hardening Agent
6. BetAML Ops Go Live Agent

## Regra de execucao

Cada agente deve:
- trabalhar com escopo estreito;
- validar com evidencias reais;
- devolver riscos residuais explicitamente;
- evitar expandir o produto alem do necessario para readiness.