import { test, expect, Page } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME ?? 'analyst_a';
const PASSWORD = process.env.E2E_PASSWORD ?? 'analyst123';

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel(/usuário|username/i).fill(USERNAME);
  await page.getByLabel(/senha|password/i).fill(PASSWORD);
  await page.getByRole('button', { name: /entrar|login/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10_000 });
}

test.describe('Alerts', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/alerts');
  });

  test('alerts page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /alertas/i })).toBeVisible();
  });

  test('severity filter buttons present', async ({ page }) => {
    for (const label of ['Todos', 'Crítico', 'Alto']) {
      await expect(page.getByRole('button', { name: new RegExp(label, 'i') })).toBeVisible();
    }
  });

  test('search input filters list', async ({ page }) => {
    const search = page.getByPlaceholder(/buscar/i);
    await expect(search).toBeVisible();
    await search.fill('test-nonexistent-xyz');
    // Either the list becomes empty or shows "nenhum"
    await expect(
      page.getByText(/nenhum alerta/i).or(page.locator('table tbody tr').filter({ hasText: 'test-nonexistent-xyz' }))
    ).toBeVisible({ timeout: 5_000 });
  });

  test('clicking an alert opens detail view', async ({ page }) => {
    const firstRow = page.locator('table tbody tr').first();
    // Only proceed if there are rows
    const count = await firstRow.count();
    if (count === 0) test.skip();
    await firstRow.click();
    await expect(page.url()).toMatch(/\/alerts\/.+/);
  });
});
