# Fase 2 - Item 12 (Validacao Temporal de ML)

Data: 2026-05-22
Status: Implementado

## Objetivo

Substituir a avaliacao in-sample por validacao temporal holdout no retreino automatico de ML.

## Correcoes implementadas

1. Split temporal do conjunto de treino
- Arquivo: services/ml_trainer/main.py
- Funcoes: _prepare_alert_samples, _temporal_split_samples
- Comportamento: usa os alertas mais recentes como holdout de validacao

2. Avaliacao out-of-sample
- Arquivo: services/ml_trainer/main.py
- Funcao: _evaluate_supervised_model / _evaluate_unsupervised_model
- Comportamento: precision, recall, f1 e AUC passam a ser calculados no holdout temporal

3. Persistencia de contrato de validacao
- Arquivo: services/ml_trainer/main.py
- Registry metrics: validation_precision, validation_recall, validation_f1_score, validation_auc_roc
- Metadados: validation_samples, validation_strategy, validation_window_days

4. Documentacao operacional
- Arquivo: docs/ops-guide.md
- Secao atualizada: acompanhamento champion/challenger e retreino manual

5. Cobertura de testes
- Arquivo: tests/unit/test_ml_trainer.py
- Testes: split temporal usa os alerts mais recentes; registry persiste metadados de validacao

## Validacao

1. Regressao unitaria
- Comando: pytest -q tests/unit/test_ml_trainer.py
- Resultado: 19 passed

## Criterio de aceite do item 12

- Avaliacao temporal aplicada: atendido
- Contrato de validacao persistido: atendido
