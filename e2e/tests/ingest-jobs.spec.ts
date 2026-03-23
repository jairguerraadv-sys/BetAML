import { test, expect } from '@playwright/test';

import { apiLogin, createIngestJobViaApi, login } from './helpers';

test.describe('Ingest Jobs', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/ingest-jobs');
  });

  test('can list and inspect an ingest job created via API upload', async ({ page, request }) => {
    const session = await apiLogin(request);
    const created = await createIngestJobViaApi(request, session.access_token);

    await page.getByLabel(/source system do filtro de ingest jobs/i).fill('BackofficeAlpha');
    await page.getByRole('button', { name: /atualizar jobs de ingestão/i }).click();

    await expect(page.getByText(created.fileName)).toBeVisible({ timeout: 10_000 });
    await page.getByText(created.fileName).click();

    await expect(page.getByRole('heading', { name: /detalhes do job/i })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(created.fileName)).toBeVisible();
    await expect(page.getByText(/bronze path/i)).toBeVisible();
  });
});
