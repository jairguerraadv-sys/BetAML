import { test, expect } from '@playwright/test';

import { loginAsAdmin } from './helpers';

test.describe('Admin And Settings', () => {
  test('admin dashboard tabs are accessible', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/admin');

    await expect(page.getByRole('heading', { name: /administração/i })).toBeVisible();
    await expect(page.getByText(/modo manutenção/i)).toBeVisible();

    await page.getByRole('button', { name: /usuários/i }).click();
    await expect(page.getByRole('heading', { name: /usuários do tenant/i })).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /uso/i }).click();
    await expect(page.getByText(/uso do mês corrente|eventos este mês/i)).toBeVisible({ timeout: 10_000 });
  });

  test('admin can save tenant settings', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/settings');

    await expect(page.getByRole('heading', { name: /configurações/i })).toBeVisible();
    await expect(page.getByLabel(/peso — regras dsl/i)).toBeVisible();

    await page.getByRole('button', { name: /salvar configuração/i }).click();
    await expect(page.getByText(/salvo com sucesso/i)).toBeVisible({ timeout: 10_000 });
  });
});
