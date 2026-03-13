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

test.describe('Cases', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/cases');
  });

  test('cases page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /casos em investigação/i })).toBeVisible();
  });

  test('filter tabs are visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: /em andamento/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /encerrados/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /todos/i })).toBeVisible();
  });

  test('new case button is present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /novo caso/i })).toBeVisible();
  });

  test('clicking a case row opens detail', async ({ page }) => {
    const firstCase = page.locator('[class*="cursor-pointer"]').first();
    const count = await firstCase.count();
    if (count === 0) test.skip();
    await firstCase.click();
    await expect(page.url()).toMatch(/\/cases\/.+/);
    await expect(page.getByRole('tab', { name: /visão geral/i })).toBeVisible();
  });

  test('cases detail has expected tabs', async ({ page }) => {
    const firstCase = page.locator('[class*="cursor-pointer"]').first();
    if (await firstCase.count() === 0) test.skip();
    await firstCase.click();
    await expect(page.getByRole('tab', { name: /visão geral/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /perfil/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /movimentações/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /decisão/i })).toBeVisible();
  });
});
