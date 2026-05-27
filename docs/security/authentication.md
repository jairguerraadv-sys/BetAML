# Autenticacao

BetAML usa JWT Bearer para usuarios e API key v2 para ingestao automatizada.

## JWT

Access tokens devem conter:

- `sub`: id do usuario.
- `tenant_id`: tenant do usuario.
- `token_type=access`.
- `jti`: identificador para blacklist.
- `exp`, `iat`, `nbf`.

Tokens sem `sub`, sem `tenant_id` ou com `token_type` diferente de `access` sao rejeitados. O tenant do token precisa bater com o tenant do usuario carregado do banco.

## Refresh token

Refresh tokens usam `token_type=refresh` e `jti` persistido em `users.refresh_token_jti`. A cada refresh, um novo refresh token e emitido e o `jti` anterior deixa de ser aceito. Logout remove o refresh token persistido e coloca o access token corrente na blacklist Redis.

## Blacklist Redis

Tokens revogados sao armazenados em `betaml:revoked:jti:{jti}` ate o vencimento do token. Perda do Redis remove a blacklist em memoria, por isso rotacao de `JWT_SECRET` e o procedimento de contencao para vazamento confirmado.

## API key v2

Formato:

```text
btml_<tenant_uuid_hex32>_<secret>
```

O prefixo contem o tenant em hexadecimal e permite definir `app.current_tenant` antes da consulta em `api_keys`, mantendo RLS efetivo mesmo com `FORCE ROW LEVEL SECURITY`. API keys podem expirar, ficar inativas e carregar permissoes como `ingest`.

## API key legada

Chaves sem prefixo `btml_` ainda sao suportadas por compatibilidade, mas disparam log de deprecacao e fazem scan por tenants. Elas devem ser rotacionadas para v2 antes de producao formal.

## Rotacao

- JWT: publicar novo `JWT_SECRET`, invalidar sessoes e forcar login.
- Refresh: limpar `refresh_token_jti` dos usuarios afetados.
- API key: criar nova chave v2, atualizar conector, observar uso, revogar antiga.
- Redis: perda exige avaliacao de risco porque access tokens revogados podem voltar a ser aceitos ate expirarem.
