import { test, expect } from '@playwright/test';

import { login } from './helpers';

test.describe('Mappings', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/mappings');
  });

  test('mapping studio loads templates and editor', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /mappingconfig studio/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /connectorgamma/i })).toBeVisible();
    await expect(page.getByLabel(/nome do mapping/i)).toBeVisible();
    await expect(page.getByLabel(/editor do mapping/i)).toBeVisible();
  });

  test('can create a mapping from template with preview', async ({ page }) => {
    const mappingName = `E2E Mapping ${Date.now()}`;

    await page.getByRole('button', { name: /connectorgamma/i }).click();
    await page.getByLabel(/nome do mapping/i).fill(mappingName);
    await page.getByLabel(/change notes do mapping/i).fill('Criado pela suíte E2E');

    await expect(page.getByText(/config válido e compatível com schema canônico/i)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /gerar preview/i }).click();
    await expect(page.getByLabel(/resultado do preview do mapping/i)).not.toContainText(/sem preview/i, { timeout: 10_000 });

    await page.getByRole('button', { name: /criar mapping/i }).click();
    await expect(page.getByText(mappingName)).toBeVisible({ timeout: 10_000 });
  });
});
