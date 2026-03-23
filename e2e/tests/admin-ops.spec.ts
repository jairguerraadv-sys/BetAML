import { test, expect } from '@playwright/test';

import { loginAsAdmin } from './helpers';

test.describe('Admin Ops', () => {
  test('admin can view operational health and alerts panels', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/admin/ops');

    await expect(page.getByRole('heading', { name: /operações/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /saúde dos serviços/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /alertas operacionais/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /modo de manutenção/i })).toBeVisible();
    await expect(page.getByText(/kafka|redis|minio|postgres/i).first()).toBeVisible({ timeout: 10_000 });
  });
});
