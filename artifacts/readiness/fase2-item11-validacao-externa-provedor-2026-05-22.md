# Fase 2 - Item 11 (Validacao Externa com Provider Real)

Data: 2026-05-22
Status: Implementado

## Objetivo

Formalizar o contrato operacional da validacao externa e explicitar quando o ambiente esta configurado para provider real ou mock.

## Correcoes implementadas

1. Contrato operacional do provider
- Arquivo: services/api/routers/external_validation.py
- Endpoint: GET /external-validation/provider-contract
- Saida: provider ativo, URL configurada, token configurado, ambiente e timeout

2. Endurecimento de operacao
- Provider mock permanece bloqueado fora de development/test
- Provider solicitado deve ser compativel com o provider configurado

3. Documentacao operacional
- Arquivo: services/api/README.md
- Variaveis documentadas: EXTERNAL_VALIDATION_PROVIDER, EXTERNAL_VALIDATION_PROVIDER_URL, EXTERNAL_VALIDATION_PROVIDER_TOKEN, EXTERNAL_VALIDATION_PROVIDER_TIMEOUT_SECONDS

4. Cobertura de testes
- Arquivo: tests/unit/test_external_validation.py
- Testes: contrato do provider e bloqueio de provider nao permitido com configuracao mock

## Validacao

1. Regressao unitaria
- Comando: pytest -q tests/unit/test_external_validation.py
- Resultado: 7 passed

## Criterio de aceite do item 11

- Contrato operacional visivel: atendido
- Mock bloqueado fora de dev/test: atendido
