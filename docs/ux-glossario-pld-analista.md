# Glossario UX PLD para Analistas

Este glossario define como o BetAML deve traduzir termos tecnicos para linguagem operacional de PLD.

| Termo tecnico | Linguagem para analista |
| --- | --- |
| Rule / DSL | Condicao de risco |
| Compound rule | Condicao combinada |
| Feature | Indicador |
| Feature Store | Base de indicadores |
| ML model | Analisador automatico |
| Anomaly score | Intensidade do sinal |
| Composite score | Risco consolidado |
| Threshold | Sensibilidade / limite de atencao |
| Auto-case threshold | Ponto de abertura automatica de caso |
| Risk band | Classificacao de risco do cliente |
| Evidence payload | Evidencias principais |
| Kafka topic / canonical event | Evento recebido pelo sistema |

## Microcopy padrao

- "Por que estou vendo isso?" para explicar alertas.
- "O que voce concluiu?" para triagem.
- "Registrar avaliacao" em vez de "Triagem" quando a acao exige julgamento humano.
- "Salvar qualidade" em vez de "Aplicar label".
- "Comportamento fora do padrao" em vez de "ML anomaly".
- "Condicoes de risco cadastradas" em vez de "regras deterministicas".

## Regra de produto

Termos tecnicos podem aparecer em secoes recolhidas chamadas "Detalhes tecnicos", mas nao devem ser a primeira camada da experiencia do analista.
