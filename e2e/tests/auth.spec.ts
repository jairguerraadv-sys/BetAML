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

test.describe('Authentication', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveTitle(/betaml|login/i);
    await expect(page.getByRole('button', { name: /entrar|login/i })).toBeVisible();
  });

  test('login with valid credentials redirects to dashboard', async ({ page }) => {
    await login(page);
    await expect(page.url()).toContain('/dashboard');
  });

  test('login with invalid credentials shows error', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel(/usuário|username/i).fill('wrong_user');
    await page.getByLabel(/senha|password/i).fill('wrongpass');
    await page.getByRole('button', { name: /entrar|login/i }).click();
    await expect(page.getByText(/inválid|incorret|falha|erro/i)).toBeVisible({ timeout: 5_000 });
  });

  test('unauthenticated access to protected route redirects to login', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForURL('**/login', { timeout: 5_000 });
  });
});
