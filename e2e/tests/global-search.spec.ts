import { test, expect } from '@playwright/test';

import {
  apiLoginAsAdmin,
  createCaseViaApi,
  loginAsAdmin,
} from './helpers';

test.describe('Global Search', () => {
  test('admin can search a case and navigate with keyboard', async ({ page, request }) => {
    const { access_token } = await apiLoginAsAdmin(request);
    const query = `Search Case ${Date.now()}`;
    const createdCase = await createCaseViaApi(request, access_token, {
      title: query,
      description: 'Caso criado para validar a busca global via E2E.',
    });

    await loginAsAdmin(page);
    await page.goto('/dashboard', { waitUntil: 'networkidle' });

    await page.getByRole('button', { name: /abrir busca global/i }).click();
    await expect(page.getByLabel(/buscar cpf, nome, caso ou alerta/i)).toBeVisible();

    const input = page.getByLabel(/buscar cpf, nome, caso ou alerta/i);
    await input.fill(query);

    const result = page.getByRole('option', { name: new RegExp(query, 'i') });
    await expect(result).toBeVisible({ timeout: 10_000 });

    // Use ArrowDown to focus the first result, then Enter to navigate
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await page.waitForURL(`**/cases/${createdCase.id}`, { timeout: 10_000 });
    await expect(page.getByRole('heading', { name: new RegExp(query, 'i') })).toBeVisible();
  });
});
