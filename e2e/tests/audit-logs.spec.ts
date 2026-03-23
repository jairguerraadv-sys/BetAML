import { test, expect } from '@playwright/test';

import { loginAsAdmin } from './helpers';

test.describe('Audit Logs', () => {
  test('admin can load and filter audit logs', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/audit-logs');

    await expect(page.getByRole('heading', { name: /logs de auditoria/i })).toBeVisible();
    await expect(page.getByText(/registros nesta página/i)).toBeVisible();
    await expect(page.getByLabel(/ação exata/i)).toBeVisible();

    await page.getByLabel(/ação exata/i).fill('PROMOTE_MODEL');
    await page.getByRole('button', { name: /aplicar filtros/i }).click();

    await expect(page.getByText(/ação|entidade|ator|pii|diff|data/i).first()).toBeVisible();
  });
});
