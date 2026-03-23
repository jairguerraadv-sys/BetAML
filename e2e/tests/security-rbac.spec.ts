import { test, expect } from '@playwright/test';

import {
  API_URL,
  createAlertViaApi,
  apiLogin,
  apiLoginAsAuditor,
  authHeaders,
  fetchFirstPlayerId,
  login,
  loginAsAuditor,
} from './helpers';

test.describe('Security RBAC', () => {
  test('analyst sees full CPF while auditor sees masked CPF', async ({ page, request }) => {
    const analystSession = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, analystSession.access_token);

    await login(page);
    await page.goto(`/players/${playerId}`);
    await expect(page.getByText(/^CPF$/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/mascarado/i)).toHaveCount(0);
    await expect(page.getByText(/\d{11}/)).toBeVisible({ timeout: 10_000 });

    await loginAsAuditor(page, request);
    await page.goto(`/players/${playerId}`);
    await expect(page.getByText(/mascarado/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/\*{3}\.\*{3}\.\*{3}[.-]\d{2}/)).toBeVisible({ timeout: 10_000 });
  });

  test('auditor cannot mutate alert labels through protected endpoint', async ({ request }) => {
    const analystSession = await apiLogin(request);
    const alert = await createAlertViaApi(request, analystSession.access_token, {
      title: `E2E RBAC alert ${Date.now()}`,
    });

    const auditorSession = await apiLoginAsAuditor(request);
    const response = await request.post(`${API_URL}/alerts/${alert.id}/label`, {
      headers: authHeaders(auditorSession.access_token),
      data: {
        label: 'FALSE_POSITIVE',
        note: 'Tentativa inválida pela suíte E2E',
      },
    });

    expect(response.status()).toBe(403);
  });

  test('auditor can access audit logs in read-only mode', async ({ page, request }) => {
    await loginAsAuditor(page, request);
    await page.goto('/audit-logs');

    await expect(page.getByRole('heading', { name: /logs de auditoria/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /aplicar filtros/i })).toBeVisible();
    await expect(page.getByText(/registros nesta página/i)).toBeVisible();
  });
});
