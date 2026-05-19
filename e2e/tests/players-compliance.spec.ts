/**
 * Fase 2/3 — Compliance PLD: Auto-Exclusão, Limite de Depósito e Eventos KYC
 *
 * Cobre as features adicionadas na Fase 2 (Lei 14.790/2023 Art. 33):
 *  - POST   /players/{id}/self-exclusion   (ANALISTA ou GESTOR)
 *  - DELETE /players/{id}/self-exclusion   (apenas GESTOR)
 *  - PATCH  /players/{id}/deposit-limit    (ANALISTA ou GESTOR)
 *  - POST   /players/{id}/kyc-events       (ANALISTA ou GESTOR)
 *  - GET    /players/{id}/kyc-events       (ANALISTA ou GESTOR)
 *
 * Inclui testes de UI na aba "Compliance PLD" do detalhe do player (Fase 3).
 */

import { test, expect } from '@playwright/test';

import {
  API_URL,
  apiLogin,
  apiLoginAsAdmin,
  authHeaders,
  fetchFirstPlayerId,
  login,
  loginAsAdmin,
} from './helpers';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers locais
// ─────────────────────────────────────────────────────────────────────────────

async function setSelfExclusionViaApi(
  request: import('@playwright/test').APIRequestContext,
  token: string,
  playerId: string,
  reason?: string,
) {
  const response = await request.post(`${API_URL}/players/${playerId}/self-exclusion`, {
    headers: authHeaders(token),
    data: reason ? { reason } : {},
  });
  expect(response.ok(), `set-self-exclusion falhou: ${response.status()}`).toBeTruthy();
  return await response.json() as { player_id: string; self_exclusion_flag: boolean; status: string };
}

async function clearSelfExclusionViaApi(
  request: import('@playwright/test').APIRequestContext,
  token: string,
  playerId: string,
) {
  const response = await request.delete(`${API_URL}/players/${playerId}/self-exclusion`, {
    headers: authHeaders(token),
  });
  expect(response.ok(), `clear-self-exclusion falhou: ${response.status()}`).toBeTruthy();
  return await response.json() as { player_id: string; self_exclusion_flag: boolean; status: string };
}

async function updateDepositLimitViaApi(
  request: import('@playwright/test').APIRequestContext,
  token: string,
  playerId: string,
  depositLimitDaily: number,
) {
  const response = await request.patch(`${API_URL}/players/${playerId}/deposit-limit`, {
    headers: authHeaders(token),
    data: { deposit_limit_daily: depositLimitDaily },
  });
  expect(response.ok(), `deposit-limit falhou: ${response.status()}`).toBeTruthy();
  return await response.json() as { player_id: string; deposit_limit_daily: number };
}

async function createKycEventViaApi(
  request: import('@playwright/test').APIRequestContext,
  token: string,
  playerId: string,
  body: { event_type: string; provider?: string; status?: string },
) {
  const response = await request.post(`${API_URL}/players/${playerId}/kyc-events`, {
    headers: authHeaders(token),
    data: {
      event_type: body.event_type,
      provider: body.provider ?? 'manual',
      status: body.status ?? 'PENDING',
    },
  });
  expect(response.ok(), `create-kyc-event falhou: ${response.status()}`).toBeTruthy();
  return await response.json() as {
    id: string;
    player_id: string;
    event_type: string;
    provider: string;
    status: string;
    player_status: string;
    created_at: string;
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Testes de API (headless, sem browser)
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Players Compliance — API', () => {
  test('analista pode ativar auto-exclusão de um player', async ({ request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    const result = await setSelfExclusionViaApi(request, session.access_token, playerId, 'Solicitação do próprio jogador E2E');

    expect(result.player_id).toBe(playerId);
    expect(result.self_exclusion_flag).toBe(true);
    expect(result.status).toBe('SELF_EXCLUDED');

    // Limpar estado para não afetar outros testes
    const adminSession = await apiLoginAsAdmin(request);
    await clearSelfExclusionViaApi(request, adminSession.access_token, playerId);
  });

  test('gestor pode remover auto-exclusão após revisão manual', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, analystSession.access_token);

    // Ativar como analista
    await setSelfExclusionViaApi(request, analystSession.access_token, playerId);

    // Remover como gestor/admin
    const adminSession = await apiLoginAsAdmin(request);
    const result = await clearSelfExclusionViaApi(request, adminSession.access_token, playerId);

    expect(result.self_exclusion_flag).toBe(false);
    expect(result.status).toBe('ACTIVE');
  });

  test('analista sem permissão não pode remover auto-exclusão (RBAC)', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, analystSession.access_token);

    // Ativar auto-exclusão primeiro
    await setSelfExclusionViaApi(request, analystSession.access_token, playerId);

    // Tentar remover com analista — deve retornar 403
    const clearResponse = await request.delete(`${API_URL}/players/${playerId}/self-exclusion`, {
      headers: authHeaders(analystSession.access_token),
    });
    expect(clearResponse.status()).toBe(403);

    // Limpar estado
    const adminSession = await apiLoginAsAdmin(request);
    await clearSelfExclusionViaApi(request, adminSession.access_token, playerId);
  });

  test('analista pode definir limite diário de depósito', async ({ request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);
    const newLimit = 500.0;

    const result = await updateDepositLimitViaApi(request, session.access_token, playerId, newLimit);

    expect(result.player_id).toBe(playerId);
    expect(result.deposit_limit_daily).toBe(newLimit);
  });

  test('limite de depósito negativo é rejeitado', async ({ request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    const response = await request.patch(`${API_URL}/players/${playerId}/deposit-limit`, {
      headers: authHeaders(session.access_token),
      data: { deposit_limit_daily: -100 },
    });
    expect(response.status()).toBe(400);
  });

  test('analista pode registrar evento KYC PENDING', async ({ request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    const event = await createKycEventViaApi(request, session.access_token, playerId, {
      event_type: 'DOCUMENT_CHECK',
      provider: 'Serasa',
      status: 'PENDING',
    });

    expect(event.event_type).toBe('DOCUMENT_CHECK');
    expect(event.provider).toBe('Serasa');
    expect(event.status).toBe('PENDING');
    expect(event.player_id).toBe(playerId);
  });

  test('evento KYC APPROVED muda status do player para ACTIVE se estava PENDING_KYC', async ({ request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    // Colocar player em PENDING_KYC via evento REJECTED primeiro
    await createKycEventViaApi(request, session.access_token, playerId, {
      event_type: 'FACIAL_BIOMETRY',
      status: 'REJECTED',
    });

    // Aprovar — player deve voltar para ACTIVE
    const event = await createKycEventViaApi(request, session.access_token, playerId, {
      event_type: 'MANUAL_APPROVAL',
      provider: 'compliance-team',
      status: 'APPROVED',
    });

    expect(event.player_status).toBe('ACTIVE');
  });

  test('analista pode listar histórico de eventos KYC do player', async ({ request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    // Criar evento para garantir que há pelo menos um
    await createKycEventViaApi(request, session.access_token, playerId, {
      event_type: 'PEP_CHECK',
      provider: 'Lista PEP Gov',
      status: 'APPROVED',
    });

    const response = await request.get(`${API_URL}/players/${playerId}/kyc-events`, {
      headers: authHeaders(session.access_token),
    });
    expect(response.ok()).toBeTruthy();
    const events = await response.json() as Array<{
      id: string;
      event_type: string;
      provider: string;
      status: string;
      created_at: string;
    }>;

    expect(Array.isArray(events)).toBeTruthy();
    expect(events.length).toBeGreaterThan(0);
    expect(events[0]).toHaveProperty('id');
    expect(events[0]).toHaveProperty('event_type');
    expect(events[0]).toHaveProperty('status');
    expect(events[0]).toHaveProperty('created_at');
  });

  test('auto-exclusão é refletida no GET /players/{id}', async ({ request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    await setSelfExclusionViaApi(request, session.access_token, playerId);

    const profileResponse = await request.get(`${API_URL}/players/${playerId}`, {
      headers: authHeaders(session.access_token),
    });
    expect(profileResponse.ok()).toBeTruthy();
    const profile = await profileResponse.json() as {
      id: string;
      self_exclusion_flag: boolean;
      status: string;
    };

    expect(profile.self_exclusion_flag).toBe(true);
    expect(profile.status).toBe('SELF_EXCLUDED');

    // Limpar estado
    const adminSession = await apiLoginAsAdmin(request);
    await clearSelfExclusionViaApi(request, adminSession.access_token, playerId);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Testes de UI — aba "Compliance PLD" (Fase 3)
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Players Compliance — UI (aba Compliance PLD)', () => {
  test('aba Compliance PLD está acessível na página do player', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    await login(page);
    await page.goto(`/players/${playerId}`);

    // A aba deve estar visível
    const complianceTab = page.getByRole('button', { name: /compliance pld/i });
    await expect(complianceTab).toBeVisible({ timeout: 10_000 });

    await complianceTab.click();

    // Seções esperadas na aba
    await expect(page.getByText(/auto.exclusão/i).first()).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText(/limite de depósito/i).first()).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/eventos kyc/i).first()).toBeVisible({ timeout: 5_000 });
  });

  test('analista pode ativar auto-exclusão via UI', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    // Garantir player ativo antes do teste
    const adminSession = await apiLoginAsAdmin(request);
    // Tentar limpar caso esteja excluído (pode falhar se não estiver excluído — ignorar)
    await request.delete(`${API_URL}/players/${playerId}/self-exclusion`, {
      headers: authHeaders(adminSession.access_token),
    });

    await login(page);
    await page.goto(`/players/${playerId}`);
    await page.getByRole('button', { name: /compliance pld/i }).click();

    // Aceitar window.confirm que aparece ao clicar no botão
    page.on('dialog', (dialog) => dialog.accept());

    // Clicar no botão de ativar auto-exclusão
    await page.getByRole('button', { name: /ativar auto.exclusão/i }).click();

    // Aguardar feedback visual de sucesso (badge AUTOEXCLUÍDO)
    await expect(page.getByText(/autoexcluído|self.excluded/i)).toBeVisible({ timeout: 10_000 });

    // Limpar estado via API
    await clearSelfExclusionViaApi(request, adminSession.access_token, playerId);
  });

  test('gestor pode remover auto-exclusão via UI', async ({ page, request }) => {
    const analystSession = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, analystSession.access_token);

    // Ativar auto-exclusão via API para preparar o estado
    await setSelfExclusionViaApi(request, analystSession.access_token, playerId);

    // Logar como admin (GESTOR)
    await loginAsAdmin(page);
    await page.goto(`/players/${playerId}`);
    await page.getByRole('button', { name: /compliance pld/i }).click();

    // Badge AUTOEXCLUÍDO deve estar visível
    await expect(page.getByText(/autoexcluído|self.excluded/i)).toBeVisible({ timeout: 10_000 });

    // Botão de remoção só aparece para GESTOR/ADMIN
    const removeButton = page.getByRole('button', { name: /remover auto.exclusão/i });
    await expect(removeButton).toBeVisible({ timeout: 5_000 });

    // Aceitar window.confirm que aparece ao clicar
    page.on('dialog', (dialog) => dialog.accept());
    await removeButton.click();

    // Player deve voltar para ATIVO
    await expect(page.getByText(/^ativo$/i)).toBeVisible({ timeout: 10_000 });
  });

  test('analista pode definir limite de depósito via UI', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    await login(page);
    await page.goto(`/players/${playerId}`);
    await page.getByRole('button', { name: /compliance pld/i }).click();

    // Preencher campo de limite (aria-label="Novo limite de depósito diário")
    const limitInput = page.getByLabel(/novo limite de depósito/i);
    await expect(limitInput).toBeVisible({ timeout: 8_000 });
    await limitInput.fill('750');

    await page.getByRole('button', { name: /definir limite/i }).click();

    // Feedback de sucesso
    await expect(page.getByText(/750|limite.*atualizado|sucesso/i).first()).toBeVisible({ timeout: 10_000 });
  });

  test('analista pode registrar evento KYC via UI', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    await login(page);
    await page.goto(`/players/${playerId}`);
    await page.getByRole('button', { name: /compliance pld/i }).click();

    // Preencher formulário de evento KYC (IDENTITY_CHECK está no dropdown do frontend)
    await page.getByLabel(/tipo de evento/i).selectOption('IDENTITY_CHECK');

    const providerInput = page.getByLabel(/provedor/i).first();
    await expect(providerInput).toBeVisible({ timeout: 5_000 });
    await providerInput.fill('Serasa');

    await page.getByLabel(/status/i).last().selectOption('APPROVED');
    await page.getByRole('button', { name: /registrar evento/i }).click();

    // O novo evento deve aparecer no histórico
    await expect(page.getByText(/identity.check/i).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/serasa/i).first()).toBeVisible({ timeout: 5_000 });
  });

  test('histórico de eventos KYC exibe badges de status', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    // Criar eventos via API para garantir histórico
    await createKycEventViaApi(request, session.access_token, playerId, {
      event_type: 'SANCTIONS_CHECK',
      provider: 'OFAC',
      status: 'APPROVED',
    });
    await createKycEventViaApi(request, session.access_token, playerId, {
      event_type: 'PEP_CHECK',
      provider: 'Lista PEP',
      status: 'PENDING',
    });

    await login(page);
    await page.goto(`/players/${playerId}`);
    await page.getByRole('button', { name: /compliance pld/i }).click();

    await expect(page.getByText(/sanctions.check/i).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/approved|aprovado/i).first()).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/pending|pendente/i).first()).toBeVisible({ timeout: 5_000 });
  });
});
