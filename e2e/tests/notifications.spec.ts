import { test, expect } from '@playwright/test';

import { login } from './helpers';

test.describe('Notifications', () => {
  test('user can load notifications and switch filters', async ({ page }) => {
    await login(page);
    await page.goto('/notifications');

    await expect(page.getByRole('heading', { name: /notificações/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /todas/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /não lidas/i })).toBeVisible();

    await page.getByRole('button', { name: /não lidas/i }).click();
    await expect(page.getByText(/nenhuma notificação|abrir referência|marcar lida/i).first()).toBeVisible({ timeout: 10_000 });
  });
});
