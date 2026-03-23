import { test, expect } from '@playwright/test';

import { API_URL, apiLoginAsAdmin, authHeaders } from './helpers';

test.describe('Maintenance Mode', () => {
  test('admin can enable maintenance, observe 503 on protected routes, and disable it safely', async ({ request }) => {
    const session = await apiLoginAsAdmin(request);
    const headers = authHeaders(session.access_token);

    const enableResponse = await request.put(`${API_URL}/admin/maintenance-mode?enabled=true`, {
      headers,
    });
    expect(enableResponse.ok()).toBeTruthy();

    try {
      const protectedResponse = await request.get(`${API_URL}/players`, {
        headers,
      });
      expect(protectedResponse.status()).toBe(503);

      const healthResponse = await request.get(`${API_URL}/health/live`);
      expect(healthResponse.ok()).toBeTruthy();
      const healthBody = await healthResponse.json();
      expect(healthBody.status).toBe('live');
    } finally {
      const disableResponse = await request.put(`${API_URL}/admin/maintenance-mode?enabled=false`, {
        headers,
      });
      expect(disableResponse.ok()).toBeTruthy();
    }

    const recoveredResponse = await request.get(`${API_URL}/players`, {
      headers,
    });
    expect(recoveredResponse.ok()).toBeTruthy();
  });
});
