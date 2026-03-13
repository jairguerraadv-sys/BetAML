import { test, expect, Page, APIRequestContext } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME ?? '';
const PASSWORD = process.env.E2E_PASSWORD ?? '';
const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8000';

test.beforeAll(() => {
  if (!USERNAME || !PASSWORD) {
    throw new Error('E2E_USERNAME e E2E_PASSWORD devem estar definidos no ambiente.');
  }
});

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel(/usuário|username/i).fill(USERNAME);
  await page.getByLabel(/senha|password/i).fill(PASSWORD);
  await page.getByRole('button', { name: /entrar|login/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10_000 });
}

async function ensureAtLeastOneActiveCase(request: APIRequestContext) {
  const loginRes = await request.post(`${API_URL}/auth/login`, {
    data: { username: USERNAME, password: PASSWORD },
  });
  expect(loginRes.ok()).toBeTruthy();

  const loginData = await loginRes.json();
  const token = loginData?.access_token as string | undefined;
  expect(token).toBeTruthy();

  const headers = { Authorization: `Bearer ${token}` };
  const listRes = await request.get(`${API_URL}/cases?limit=20`, { headers });
  expect(listRes.ok()).toBeTruthy();

  const cases = (await listRes.json()) as Array<{ status?: string }>;
  const hasActive = cases.some((c) => ['OPEN', 'IN_REVIEW', 'UNDER_REVIEW'].includes(c.status ?? ''));

  if (!hasActive) {
    const createRes = await request.post(`${API_URL}/cases`, {
      headers,
      data: {
        title: `E2E Case ${Date.now()}`,
        description: 'Seed automatizado para testes E2E de casos',
        severity: 'HIGH',
      },
    });
    expect(createRes.ok()).toBeTruthy();
  }
}

test.describe('Cases', () => {
  test.beforeEach(async ({ page, request }) => {
    await ensureAtLeastOneActiveCase(request);
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
    const firstCase = page.getByTestId('case-row').first();
    await expect(firstCase).toBeVisible();
    await firstCase.click();
    await expect(page).toHaveURL(/\/cases\/.+/, { timeout: 10_000 });
    await expect(page.getByRole('button', { name: /visão geral/i })).toBeVisible();
  });

  test('cases detail has expected tabs', async ({ page }) => {
    const firstCase = page.getByTestId('case-row').first();
    await expect(firstCase).toBeVisible();
    await firstCase.click();
    await expect(page).toHaveURL(/\/cases\/.+/, { timeout: 10_000 });
    await expect(page.getByRole('button', { name: /visão geral/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /perfil do cliente/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /movimentações/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /decisão e relatório/i })).toBeVisible();
  });
});
