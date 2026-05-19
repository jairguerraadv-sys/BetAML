/**
 * Fase 2/3 — Protocolo COAF: Registro do número de protocolo Siscoaf
 *
 * Cobre as features adicionadas na Fase 2:
 *  - PATCH /cases/{id}/report-packages/{rp_id}/protocol-number  (apenas GESTOR)
 *
 * E a exibição no frontend (Fase 3):
 *  - Formulário de registro na aba Decisão
 *  - Exibição do protocolo + sha256 nos pacotes de relatório
 */

import { test, expect } from '@playwright/test';

import {
  API_URL,
  apiLogin,
  apiLoginAsAdmin,
  authHeaders,
  changeCaseStatusViaApi,
  createCaseViaApi,
  createReportPackageViaApi,
  fetchFirstPlayerId,
  loginAsAdmin,
} from './helpers';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers locais
// ─────────────────────────────────────────────────────────────────────────────

/** Avança o case para CLOSED e cria um ReportPackage com decision=FILE_SAR. */
async function prepareFiledReportPackage(
  request: import('@playwright/test').APIRequestContext,
  analystToken: string,
) {
  const playerId = await fetchFirstPlayerId(request, analystToken);

  const caseItem = await createCaseViaApi(request, analystToken, {
    player_id: playerId,
    severity: 'HIGH',
    title: `E2E COAF Protocol ${Date.now()}`,
  });

  await changeCaseStatusViaApi(request, analystToken, caseItem.id, 'INVESTIGATING');
  await changeCaseStatusViaApi(request, analystToken, caseItem.id, 'PENDING_REVIEW');
  await changeCaseStatusViaApi(request, analystToken, caseItem.id, 'CLOSED');

  const report = await createReportPackageViaApi(request, analystToken, caseItem.id, {
    decision: 'FILE_SAR',
    analyst_narrative: 'Relatório E2E para teste de protocolo COAF.',
    occurrence_codes: [1407],
    involvement_types: [49],
    valor_premio: 8000,
    valor_apostas: 6500,
    informacoes_adicionais: 'Teste automatizado de registro de protocolo Siscoaf.',
  });

  return { caseItem, report };
}

/**
 * Submit requer role GESTOR — passa o token do adminSession.
 * O endpoint opera no ReportPackage mais recente do caso (sem rp_id).
 */
async function submitReportToFiledViaApi(
  request: import('@playwright/test').APIRequestContext,
  gestorToken: string,
  caseId: string,
) {
  const response = await request.post(
    `${API_URL}/cases/${caseId}/report-package/submit`,
    { headers: authHeaders(gestorToken) },
  );
  // Submit pode retornar 200 ou 202; aceitar ambos
  expect(
    response.ok() || response.status() === 202,
    `submit falhou: ${response.status()} — endpoint requer GESTOR token`,
  ).toBeTruthy();
  return await response.json() as {
    status: string;
    report_package_id: string;
    xml_path?: string | null;
    xml_sha256?: string | null;
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Testes de API
// ─────────────────────────────────────────────────────────────────────────────

test.describe('COAF Protocol Number — API', () => {
  test('gestor pode registrar número de protocolo em ReportPackage FILED', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const adminSession = await apiLoginAsAdmin(request);
    const { caseItem, report } = await prepareFiledReportPackage(request, analystSession.access_token);

    // Submeter para ficar FILED (requer GESTOR)
    await submitReportToFiledViaApi(request, adminSession.access_token, caseItem.id);
    const protocolNumber = `COAF-E2E-${Date.now()}`;

    const response = await request.patch(
      `${API_URL}/cases/${caseItem.id}/report-packages/${report.report_package_id}/protocol-number`,
      {
        headers: authHeaders(adminSession.access_token),
        data: { coaf_protocol_number: protocolNumber },
      },
    );
    expect(response.ok(), `register-protocol falhou: ${response.status()}`).toBeTruthy();

    const body = await response.json() as {
      report_package_id: string;
      coaf_protocol_number: string;
      registered_at: string;
    };

    expect(body.report_package_id).toBe(report.report_package_id);
    expect(body.coaf_protocol_number).toBe(protocolNumber);
    expect(body.registered_at).toBeTruthy();
  });

  test('protocolo vazio é rejeitado com 400', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const adminSession = await apiLoginAsAdmin(request);
    const { caseItem, report } = await prepareFiledReportPackage(request, analystSession.access_token);
    await submitReportToFiledViaApi(request, adminSession.access_token, caseItem.id);
    const response = await request.patch(
      `${API_URL}/cases/${caseItem.id}/report-packages/${report.report_package_id}/protocol-number`,
      {
        headers: authHeaders(adminSession.access_token),
        data: { coaf_protocol_number: '   ' },
      },
    );
    expect(response.status()).toBe(400);
  });

  test('analista não pode registrar protocolo (RBAC — apenas GESTOR)', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const adminSession = await apiLoginAsAdmin(request);
    const { caseItem, report } = await prepareFiledReportPackage(request, analystSession.access_token);
    await submitReportToFiledViaApi(request, adminSession.access_token, caseItem.id);

    const response = await request.patch(
      `${API_URL}/cases/${caseItem.id}/report-packages/${report.report_package_id}/protocol-number`,
      {
        headers: authHeaders(analystSession.access_token),
        data: { coaf_protocol_number: 'COAF-FORBIDDEN' },
      },
    );
    expect(response.status()).toBe(403);
  });

  test('protocolo só pode ser registrado em ReportPackage com status FILED', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const { caseItem, report } = await prepareFiledReportPackage(request, analystSession.access_token);
    // Não submeter — ReportPackage está em status GENERATED/DRAFT

    const adminSession = await apiLoginAsAdmin(request);
    const response = await request.patch(
      `${API_URL}/cases/${caseItem.id}/report-packages/${report.report_package_id}/protocol-number`,
      {
        headers: authHeaders(adminSession.access_token),
        data: { coaf_protocol_number: 'COAF-PREMATURE' },
      },
    );
    expect(response.status()).toBe(400);
    const body = await response.json() as { detail: string };
    expect(body.detail.toLowerCase()).toContain('filed');
  });

  test('registro de protocolo gera entrada no audit trail', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const adminSession = await apiLoginAsAdmin(request);
    const { caseItem, report } = await prepareFiledReportPackage(request, analystSession.access_token);
    await submitReportToFiledViaApi(request, adminSession.access_token, caseItem.id);
    const protocolNumber = `COAF-AUDIT-${Date.now()}`;

    await request.patch(
      `${API_URL}/cases/${caseItem.id}/report-packages/${report.report_package_id}/protocol-number`,
      {
        headers: authHeaders(adminSession.access_token),
        data: { coaf_protocol_number: protocolNumber },
      },
    );

    const auditResponse = await request.get(`${API_URL}/audit-logs`, {
      headers: authHeaders(adminSession.access_token),
      params: { limit: '100' },
    });
    expect(auditResponse.ok()).toBeTruthy();
    const logs = await auditResponse.json() as Array<{
      action: string;
      entity_type: string;
      entity_id?: string | null;
    }>;

    expect(
      logs.some(
        (l) =>
          l.action === 'REGISTER_COAF_PROTOCOL'
          && l.entity_type === 'ReportPackage'
          && l.entity_id === report.report_package_id,
      ),
    ).toBeTruthy();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Testes de UI — aba Decisão (Fase 3)
// ─────────────────────────────────────────────────────────────────────────────

test.describe('COAF Protocol Number — UI', () => {
  /**
   * Ao navegar para um caso com RP já FILED (via API), a aba "Decisão e Relatório"
   * exibe o histórico de ReportPackages com o botão "+ Registrar Protocolo".
   * O formulário principal (painel azul) só aparece quando o usuário submete via UI.
   */
  test('aba Decisão e Relatório exibe botão de registro de protocolo para RP FILED', async ({ page, request }) => {
    const analystSession = await apiLogin(request);
    const adminSession = await apiLoginAsAdmin(request);
    const { caseItem } = await prepareFiledReportPackage(request, analystSession.access_token);
    await submitReportToFiledViaApi(request, adminSession.access_token, caseItem.id);

    await loginAsAdmin(page);
    await page.goto(`/cases/${caseItem.id}`);

    // Navegar para a aba Decisão e Relatório
    await page.getByRole('button', { name: /decisão e relatório/i }).click();

    // Histórico mostra "FILED" e botão "+ Registrar Protocolo" (RP sem protocolo ainda)
    await expect(page.getByText(/FILED/i).first()).toBeVisible({ timeout: 12_000 });
    await expect(page.getByRole('button', { name: /registrar protocolo/i })).toBeVisible({ timeout: 8_000 });
  });

  test('gestor pode registrar protocolo COAF via UI e ver confirmação', async ({ page, request }) => {
    const analystSession = await apiLogin(request);
    const adminSession = await apiLoginAsAdmin(request);
    const { caseItem } = await prepareFiledReportPackage(request, analystSession.access_token);
    await submitReportToFiledViaApi(request, adminSession.access_token, caseItem.id);

    await loginAsAdmin(page);
    await page.goto(`/cases/${caseItem.id}`);

    await page.getByRole('button', { name: /decisão e relatório/i }).click();

    // Clicar "+ Registrar Protocolo" na seção de histórico para abrir o form inline
    const registerBtn = page.getByRole('button', { name: /registrar protocolo/i });
    await expect(registerBtn).toBeVisible({ timeout: 12_000 });
    await registerBtn.click();

    // Input inline aparece com placeholder "Protocolo COAF"
    const protocolInput = page.getByPlaceholder(/protocolo coaf/i);
    await expect(protocolInput).toBeVisible({ timeout: 8_000 });

    const protocolNumber = `UI-COAF-${Date.now()}`;
    await protocolInput.fill(protocolNumber);

    // Confirmar com o botão "OK" do form inline
    await page.getByRole('button', { name: /^ok$/i }).click();

    // O número de protocolo deve aparecer na listagem: "Protocolo COAF: <número>"
    await expect(page.getByText(new RegExp(protocolNumber))).toBeVisible({ timeout: 10_000 });
  });

  test('sha256 do XML é exibido após geração do relatório', async ({ page, request }) => {
    const analystSession = await apiLogin(request);
    const adminSession = await apiLoginAsAdmin(request);
    const { caseItem } = await prepareFiledReportPackage(request, analystSession.access_token);
    const submitResult = await submitReportToFiledViaApi(request, adminSession.access_token, caseItem.id);

    // Só validar exibição do sha256 se a API retornou um
    if (!submitResult.xml_sha256) {
      test.skip();
      return;
    }

    await loginAsAdmin(page);
    await page.goto(`/cases/${caseItem.id}`);

    await page.getByRole('button', { name: /decisão e relatório/i }).click();

    // Na seção de histórico, sha256 é exibido truncado: "SHA-256: {hash.slice(0,16)}…"
    // Verificamos os primeiros 8 chars que estarão presentes
    const shortHash = submitResult.xml_sha256.slice(0, 8);
    await expect(page.getByText(new RegExp(shortHash, 'i'))).toBeVisible({ timeout: 12_000 });
  });
});
