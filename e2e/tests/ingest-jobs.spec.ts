import { test, expect } from '@playwright/test';

import { apiLoginAsAdmin, createIngestJobViaApi, loginAsAdmin } from './helpers';

test.describe('Ingest Jobs', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/ingest-jobs');
  });

  test('can list and inspect an ingest job created via API upload', async ({ page, request }) => {
    const session = await apiLoginAsAdmin(request);
    const created = await createIngestJobViaApi(request, session.access_token);

    await page.getByLabel(/source system do filtro de ingest jobs/i).fill('BackofficeAlpha');
    await page.getByRole('button', { name: /atualizar jobs de ingestão/i }).click();

    await expect(page.getByText(created.fileName)).toBeVisible({ timeout: 10_000 });
    await page.getByText(created.fileName).click();

    await expect(page.getByRole('heading', { name: /detalhes do job/i })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(created.fileName)).toBeVisible();
    await expect(page.getByText(/caminho do arquivo/i)).toBeVisible();
    await expect(page.getByText(/bronze\//i).first()).toBeVisible();
  });
});
