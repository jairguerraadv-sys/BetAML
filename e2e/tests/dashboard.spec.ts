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

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('KPI cards are visible', async ({ page }) => {
    await expect(page.getByText(/alertas hoje/i)).toBeVisible();
    await expect(page.getByText(/críticos abertos/i)).toBeVisible();
    await expect(page.getByText(/casos em andamento/i)).toBeVisible();
    await expect(page.getByText(/sla vencido/i)).toBeVisible();
  });

  test('KPI values are numeric', async ({ page }) => {
    // Cards should show a number (possibly 0)
    const kpiValues = page.locator('.text-4xl');
    await expect(kpiValues.first()).toBeVisible();
    const text = await kpiValues.first().textContent();
    expect(Number(text)).toBeGreaterThanOrEqual(0);
  });

  test('severity chart renders', async ({ page }) => {
    await expect(page.getByText(/alertas abertos por prioridade/i)).toBeVisible();
  });

  test('navigation links work', async ({ page }) => {
    await page.getByRole('link', { name: /ver todos/i }).first().click();
    await expect(page.url()).toMatch(/\/(alerts|cases)/);
  });
});
