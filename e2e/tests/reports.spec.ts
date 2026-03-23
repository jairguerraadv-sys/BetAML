import { test, expect } from '@playwright/test';

import { loginAsAdmin } from './helpers';

test.describe('Reports', () => {
  test('admin can consult and enqueue monthly reports', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/reports');

    await expect(page.getByRole('heading', { name: /relatórios mensais/i })).toBeVisible();
    await expect(page.getByLabel(/data inicial do relatório mensal/i)).toBeVisible();

    await page.getByRole('button', { name: /consultar/i }).click();
    await expect(page.getByText(/período:|nenhum dado encontrado/i)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /enfileirar/i }).click();
    await expect(page.getByText(/relatório enfileirado|erro ao enfileirar geração/i)).toBeVisible({ timeout: 10_000 });
  });
});
