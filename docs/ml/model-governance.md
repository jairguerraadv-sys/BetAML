# Model Governance - Synthetic Promotion Policy

## Definicoes

- Modelo sintetico: modelo treinado com bootstrap/dados artificiais.
- Bootstrap sintetico: treinamento inicial sem base real suficiente.
- Champion: modelo ativo de producao para scoring principal.
- Challenger: modelo candidato comparado ao champion antes de promocao.

## Regra de promocao

Modelos sinteticos nao podem ser promovidos para active/champion em staging ou production.

## Matriz por ambiente

- development/test/local:
  - permitido treinar e promover modelo sintetico para facilitar onboarding local.
- staging/production:
  - bloqueado promover modelo sintetico para active/champion;
  - permitido registrar artefato sintetico apenas como nao ativo (quando aplicavel).

## Como o sistema detecta modelo sintetico

A deteccao considera duas fontes:

1. Campo explicito em model_registry:
   - trained_on_synthetic
2. Compatibilidade legada em metrics JSONB:
   - metrics.synthetic_bootstrap
   - metrics.synthetic

A avaliacao e centralizada em libs/ml_governance.py.

## Fluxo permitido

- registrar artefato sintetico nao ativo para analise tecnica, quando necessario;
- usar bootstrap sintetico em development/test/local;
- promover apenas modelos nao sinteticos em staging/production.

## Fluxo bloqueado

- registro ativo/champion de modelo sintetico em staging/production;
- promocao manual de challenger sintetico para champion fora de dev/test;
- auto-promocao agendada de challenger sintetico fora de dev/test.

## Evidencias

- migration com campo explicito e backfill legado:
  - services/api/alembic/versions/20260527_000004_model_registry_trained_on_synthetic.py
- gate central de registro no ml_service:
  - services/ml_service/main.py (register_model_db)
- gate de promocao manual:
  - services/api/routers/ml.py (POST /model-registry/{model_id}/promote)
- gate de auto-promocao:
  - services/api/jobs.py (auto_promote_challenger_models)
- testes de governanca:
  - tests/ml/test_model_registry_governance.py

## Relacao com PR-07

Este PR fecha o risco de promocao sintetica silenciosa.

PR-07 continua responsavel por governanca avancada de champion/challenger,
com thresholds formais de precision/FPR, feedback loop e aprovacao operacional.
