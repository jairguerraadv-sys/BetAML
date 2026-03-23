import { expect, test } from '@playwright/test';

import { API_URL, apiLoginAsAdmin, authHeaders } from './helpers';

test.describe('Ingest Rate Limit', () => {
  test('admin-configured tenant ingest limit is enforced for connector parse endpoint', async ({ request }) => {
    const session = await apiLoginAsAdmin(request);
    const headers = authHeaders(session.access_token);
    const flagUrl = `${API_URL}/admin/flags/ingest_rate_limit_per_min`;

    const enableTightLimit = await request.put(flagUrl, {
      headers,
      data: { value: '1' },
    });
    expect(enableTightLimit.ok()).toBeTruthy();

    const ndjsonPayload = JSON.stringify({
      id: `delta-rate-limit-${Date.now()}`,
      uid: 'CPF-RATE-LIMIT',
      evt_type: 'DEPOSIT',
      ts: '2026-03-20T18:00:00Z',
      val: 10,
      ccy: 'BRL',
    });

    const parseOnce = () => request.post(`${API_URL}/ingest/connectors/delta/parse`, {
      headers,
      multipart: {
        entity_type: 'TRANSACTION',
        file: {
          name: `delta-rate-limit-${Date.now()}.ndjson`,
          mimeType: 'application/x-ndjson',
          buffer: Buffer.from(ndjsonPayload, 'utf-8'),
        },
      },
    });

    try {
      const statuses: number[] = [];
      let last429Detail = '';
      let last429RetryAfter = '';

      for (let attempt = 0; attempt < 6; attempt += 1) {
        const response = await parseOnce();
        const status = response.status();
        statuses.push(status);
        if (status === 429) {
          const body = await response.json() as { detail?: string };
          last429Detail = body.detail ?? '';
          last429RetryAfter = response.headers()['retry-after'] ?? '';
        }
      }

      expect(statuses.filter((status) => status === 429).length).toBeGreaterThan(0);
      expect(last429Detail).toContain('Rate limit excedido');
      expect(last429RetryAfter).toBeTruthy();
    } finally {
      const resetLimit = await request.put(flagUrl, {
        headers,
        data: { value: '300' },
      });
      expect(resetLimit.ok()).toBeTruthy();

      const postResetResponse = await parseOnce();
      expect(postResetResponse.status()).toBe(202);
    }
  });
});
