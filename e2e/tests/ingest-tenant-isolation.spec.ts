import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiLoginAsAdmin,
  apiLoginAsSecondaryAdmin,
  authHeaders,
} from './helpers';

test.describe('Ingest Tenant Isolation', () => {
  test.setTimeout(90_000);

  test('tenant B cannot read or mutate tenant A ingest jobs/errors', async ({ request }) => {
    const tenantA = await apiLoginAsAdmin(request);
    const tenantB = await apiLoginAsSecondaryAdmin(request);

    const gammaPayload = [
      '<Events>',
      '  <Transaction>',
      '    <EventId>iso-gamma-ok-1</EventId>',
      '    <PlayerId>CPF-ISO-1</PlayerId>',
      '    <Type>DEPOSIT</Type>',
      '    <Amount currency="BRL">1000.00</Amount>',
      '    <Timestamp>2026-03-20T10:00:00Z</Timestamp>',
      '    <Instrument><Type>PIX</Type><Token>pix-iso-ok</Token></Instrument>',
      '    <DeviceId>dev-iso-1</DeviceId>',
      '  </Transaction>',
      '  <Transaction>',
      '    <EventId>iso-gamma-bad-1</EventId>',
      '    <PlayerId>CPF-ISO-2</PlayerId>',
      '    <Type>DEPOSIT</Type>',
      '    <Amount currency="BRL">-10.00</Amount>',
      '    <Timestamp>2026-03-20T10:05:00Z</Timestamp>',
      '    <Instrument><Type>PIX</Type><Token>pix-iso-bad</Token></Instrument>',
      '    <DeviceId>dev-iso-2</DeviceId>',
      '  </Transaction>',
      '</Events>',
    ].join('\n');

    const parseResponse = await request.post(`${API_URL}/ingest/connectors/gamma/parse`, {
      headers: authHeaders(tenantA.access_token),
      multipart: {
        entity_type: 'TRANSACTION',
        file: {
          name: `ingest-tenant-isolation-${Date.now()}.xml`,
          mimeType: 'application/xml',
          buffer: Buffer.from(gammaPayload, 'utf-8'),
        },
      },
    });
    expect(parseResponse.status()).toBe(202);
    const parseBody = await parseResponse.json() as {
      job_id: string;
      summary: { accepted: number; failed: number; total: number };
    };
    expect(parseBody.summary.total).toBeGreaterThanOrEqual(2);
    expect(parseBody.summary.failed).toBeGreaterThanOrEqual(1);

    const tenantAErrorsResponse = await request.get(`${API_URL}/ingest/errors`, {
      headers: authHeaders(tenantA.access_token),
      params: { job_id: parseBody.job_id },
    });
    expect(tenantAErrorsResponse.ok()).toBeTruthy();
    const tenantAErrors = await tenantAErrorsResponse.json() as Array<{ id: string }>;
    expect(tenantAErrors.length).toBeGreaterThan(0);
    const tenantAErrorId = tenantAErrors[0].id;

    const tenantBGetJob = await request.get(`${API_URL}/ingest/jobs/${parseBody.job_id}`, {
      headers: authHeaders(tenantB.access_token),
    });
    expect(tenantBGetJob.status()).toBe(404);

    const tenantBListErrors = await request.get(`${API_URL}/ingest/errors`, {
      headers: authHeaders(tenantB.access_token),
      params: { job_id: parseBody.job_id },
    });
    expect(tenantBListErrors.ok()).toBeTruthy();
    const tenantBErrors = await tenantBListErrors.json() as Array<{ id: string }>;
    expect(tenantBErrors.length).toBe(0);

    const tenantBReprocess = await request.post(`${API_URL}/ingest/jobs/${parseBody.job_id}/reprocess`, {
      headers: authHeaders(tenantB.access_token),
      data: { reason: `tenant-isolation-attempt-${Date.now()}` },
    });
    expect(tenantBReprocess.status()).toBe(404);

    const tenantBReplay = await request.post(`${API_URL}/ingest/errors/${tenantAErrorId}/replay`, {
      headers: authHeaders(tenantB.access_token),
      data: {
        corrected_payload: {
          external_player_id: 'CPF-ISO-REPLAY',
          transaction_type: 'DEPOSIT',
          amount: 999,
          occurred_at: '2026-03-20T10:00:00Z',
          currency: 'BRL',
        },
      },
    });
    expect(tenantBReplay.status()).toBe(404);

    const tenantAReprocess = await request.post(`${API_URL}/ingest/jobs/${parseBody.job_id}/reprocess`, {
      headers: authHeaders(tenantA.access_token),
      data: { reason: `tenant-a-control-reprocess-${Date.now()}` },
    });
    expect(tenantAReprocess.status()).toBe(202);
  });
});
