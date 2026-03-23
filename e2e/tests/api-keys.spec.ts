import { test, expect } from '@playwright/test';

import { loginAsAdmin } from './helpers';

test.describe('API Keys', () => {
  test('admin can create, inspect and revoke an ingest api key', async ({ page }) => {
    const keyName = `e2e-key-${Date.now()}`;

    await loginAsAdmin(page);
    await page.goto('/admin');
    await page.getByRole('button', { name: /chaves de api/i }).click();

    await page.getByLabel(/^nome \*$/i).fill(keyName);
    await page.getByLabel(/source system/i).fill('connector_delta');
    await page.getByRole('button', { name: /gerar chave/i }).click();

    await expect(page.getByText(/copie agora/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(keyName)).toBeVisible({ timeout: 10_000 });

    const row = page.locator('tr', { hasText: keyName });
    await row.getByRole('button', { name: new RegExp(`ver uso da chave ${keyName}`, 'i') }).click();
    await expect(page.getByRole('heading', { name: new RegExp(keyName, 'i') })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('heading', { name: /escopo e permissões/i })).toBeVisible();
    await page.getByRole('button', { name: /fechar/i }).click();

    await row.getByRole('button', { name: new RegExp(`revogar chave ${keyName}`, 'i') }).click();
    await expect(row.getByText(/revogada/i)).toBeVisible({ timeout: 10_000 });
  });
});
