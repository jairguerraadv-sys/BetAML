import { test, expect } from '@playwright/test';

import {
  API_URL,
  apiLogin,
  apiLoginAsAuditor,
  authHeaders,
  changeCaseStatusViaApi,
  createCaseViaApi,
  createReportPackageViaApi,
  fetchFirstPlayerId,
} from './helpers';

test.describe('Report Audit Trail', () => {
  test('analyst can export COAF XML and auditor can verify audit entries for report exports', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, analystSession.access_token);

    const caseItem = await createCaseViaApi(request, analystSession.access_token, {
      player_id: playerId,
      severity: 'HIGH',
      title: `E2E Audit Export ${Date.now()}`,
    });

    await changeCaseStatusViaApi(request, analystSession.access_token, caseItem.id, 'INVESTIGATING');
    await changeCaseStatusViaApi(request, analystSession.access_token, caseItem.id, 'PENDING_REVIEW');
    await changeCaseStatusViaApi(request, analystSession.access_token, caseItem.id, 'CLOSED');

    const report = await createReportPackageViaApi(request, analystSession.access_token, caseItem.id, {
      decision: 'FILE_SAR',
      analyst_narrative: 'Narrativa regulatória gerada pela suíte E2E para validar exportações.',
      occurrence_codes: [1407],
      involvement_types: [49],
      valor_premio: 12500,
      valor_apostas: 9800,
      informacoes_adicionais: 'Comunicação regulatória E2E para validar exportações e trilha de auditoria.',
    });

    const jsonResponse = await request.get(
      `${API_URL}/cases/${caseItem.id}/report-package/json?rp_id=${report.report_package_id}`,
      { headers: authHeaders(analystSession.access_token) },
    );
    expect(jsonResponse.ok()).toBeTruthy();

    const pdfResponse = await request.get(
      `${API_URL}/cases/${caseItem.id}/report-package/pdf?rp_id=${report.report_package_id}`,
      { headers: authHeaders(analystSession.access_token) },
    );
    expect(pdfResponse.ok()).toBeTruthy();

    const xmlResponse = await request.get(
      `${API_URL}/cases/${caseItem.id}/report-package/coaf-xml`,
      { headers: authHeaders(analystSession.access_token) },
    );
    expect(xmlResponse.ok()).toBeTruthy();
    expect(xmlResponse.headers()['content-type'] ?? '').toContain('application/xml');
    const xmlBody = await xmlResponse.text();
    expect(xmlBody).toContain('<ComunicacaoOperacoesSuspeitas');
    expect(xmlBody).toContain('<NarrativaAnalista>');
    expect(xmlBody).toContain('<InformacoesAdicionais>');

    const auditorSession = await apiLoginAsAuditor(request);
    const auditResponse = await request.get(`${API_URL}/audit-logs`, {
      headers: authHeaders(auditorSession.access_token),
      params: { limit: '100' },
    });
    expect(auditResponse.ok()).toBeTruthy();
    const auditLogs = await auditResponse.json() as Array<{
      action: string;
      entity_type: string;
      entity_id?: string | null;
    }>;

    expect(auditLogs.some((item) =>
      item.action === 'EXPORT_REPORT_JSON'
      && item.entity_type === 'ReportPackage'
      && item.entity_id === report.report_package_id,
    )).toBeTruthy();
    expect(auditLogs.some((item) =>
      item.action === 'EXPORT_REPORT_PDF'
      && item.entity_type === 'ReportPackage'
      && item.entity_id === report.report_package_id,
    )).toBeTruthy();
    expect(auditLogs.some((item) =>
      item.action === 'DOWNLOAD_COAF_XML'
      && item.entity_type === 'Case'
      && item.entity_id === caseItem.id,
    )).toBeTruthy();
  });
});
