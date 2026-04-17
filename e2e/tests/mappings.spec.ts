import { test, expect } from '@playwright/test';

import { API_URL, apiLoginAsAdmin, authHeaders, createMappingViaApi, loginAsAdmin } from './helpers';

test.describe('Mappings', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/mappings');
  });

  test('mapping studio loads templates and editor', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /configuração de integrações|mappingconfig studio/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /connectorgamma/i })).toBeVisible();
    await expect(page.getByLabel(/nome do mapping/i)).toBeVisible();
    await expect(page.getByLabel(/editor do mapping/i)).toBeVisible();
  });

  test('can create a mapping from template with preview', async ({ page, request }) => {
    const mappingName = `E2E Mapping ${Date.now()}`;
    const session = await apiLoginAsAdmin(request);

    await page.getByRole('button', { name: /connectorgamma/i }).click();
    await page.getByLabel(/nome do mapping/i).fill(mappingName);
    await page.getByLabel(/notas da alteração do mapping|change notes do mapping/i).fill('Criado pela suíte E2E');

    await expect(page.getByText(/config válido e compatível com schema canônico/i)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /gerar preview/i }).click();
    await expect(page.getByLabel(/resultado do preview do mapping/i)).not.toContainText(/sem preview/i, { timeout: 10_000 });

    const createResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST' && response.url().includes('/mappings'),
    );
    await page.getByRole('button', { name: /criar integração|criar mapping/i }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.ok()).toBeTruthy();

    await expect.poll(async () => {
      const response = await request.get(`${API_URL}/mappings`, {
        headers: authHeaders(session.access_token),
      });
      if (!response.ok()) {
        return false;
      }
      const mappings = (await response.json()) as Array<{ name?: string }>;
      return mappings.some((mapping) => mapping.name === mappingName);
    }, { timeout: 10_000 }).toBeTruthy();
  });

  test('can run saved mapping test from selected mapping', async ({ page, request }) => {
    const session = await apiLoginAsAdmin(request);
    const mapping = await createMappingViaApi(request, session.access_token, {
      name: `E2E Mapping Saved Test ${Date.now()}`,
      source_system: `ConnectorGammaSavedTest-${Date.now()}`,
    });

    await loginAsAdmin(page);
    await page.goto('/mappings');

    await page.getByRole('gridcell', { name: mapping.name }).first().click();
    const testResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST' && response.url().includes(`/mappings/${mapping.id}/test`),
    );
    await page.getByRole('button', { name: /testar mapping salvo/i }).click();
    const testResponse = await testResponsePromise;
    expect(testResponse.ok()).toBeTruthy();
    const body = await testResponse.json() as { status?: string };
    expect(['ok', 'error']).toContain(body.status);
  });
});
