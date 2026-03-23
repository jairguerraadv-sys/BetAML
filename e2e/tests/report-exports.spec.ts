import { test, expect } from '@playwright/test';

import {
  API_URL,
  apiLogin,
  apiLoginAsAuditor,
  authHeaders,
  createCaseViaApi,
  createReportPackageViaApi,
  fetchFirstPlayerId,
} from './helpers';

test.describe('Report Exports', () => {
  test('analyst can export report package as JSON and PDF', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, analystSession.access_token);
    const caseItem = await createCaseViaApi(request, analystSession.access_token, {
      player_id: playerId,
      severity: 'HIGH',
      title: `E2E Report Export ${Date.now()}`,
    });
    const report = await createReportPackageViaApi(request, analystSession.access_token, caseItem.id, {
      decision: 'NO_ACTION',
      analyst_narrative: 'Exportação E2E de JSON e PDF.',
    });

    const jsonResponse = await request.get(
      `${API_URL}/cases/${caseItem.id}/report-package/json?rp_id=${report.report_package_id}`,
      { headers: authHeaders(analystSession.access_token) },
    );
    expect(jsonResponse.ok()).toBeTruthy();
    const jsonBody = await jsonResponse.json();
    expect(String(jsonBody.caseNumber ?? '')).not.toEqual('');
    expect(String(jsonBody.decision ?? '')).toEqual('CLOSE');

    const pdfResponse = await request.get(
      `${API_URL}/cases/${caseItem.id}/report-package/pdf?rp_id=${report.report_package_id}`,
      { headers: authHeaders(analystSession.access_token) },
    );
    expect(pdfResponse.ok()).toBeTruthy();
    expect(pdfResponse.headers()['content-type'] ?? '').toContain('application/pdf');
    const pdfBuffer = await pdfResponse.body();
    expect(pdfBuffer.length).toBeGreaterThan(1000);
  });

  test('auditor can export report JSON but is forbidden from PDF export', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, analystSession.access_token);
    const caseItem = await createCaseViaApi(request, analystSession.access_token, {
      player_id: playerId,
      severity: 'MEDIUM',
      title: `E2E Auditor Export ${Date.now()}`,
    });
    const report = await createReportPackageViaApi(request, analystSession.access_token, caseItem.id, {
      decision: 'NO_ACTION',
      analyst_narrative: 'Validação de permissão de exportação para auditor.',
    });

    const auditorSession = await apiLoginAsAuditor(request);

    const jsonResponse = await request.get(
      `${API_URL}/cases/${caseItem.id}/report-package/json?rp_id=${report.report_package_id}`,
      { headers: authHeaders(auditorSession.access_token) },
    );
    expect(jsonResponse.ok()).toBeTruthy();

    const pdfResponse = await request.get(
      `${API_URL}/cases/${caseItem.id}/report-package/pdf?rp_id=${report.report_package_id}`,
      { headers: authHeaders(auditorSession.access_token) },
    );
    expect(pdfResponse.status()).toBe(403);
  });
});
