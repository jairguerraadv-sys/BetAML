import { test, expect } from '@playwright/test';

import { loginAsAdmin } from './helpers';

test.describe('Player Lists', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/player-lists');
  });

  test('can create a player list and add manual entries', async ({ page }) => {
    const listName = `E2E List ${Date.now()}`;

    await page.getByRole('button', { name: /nova lista/i }).click();
    await page.getByLabel(/nome da player list/i).fill(listName);
    await page.getByLabel(/descrição da player list/i).fill('Lista criada pela suíte E2E');
    await page.getByLabel(/tipo da player list/i).selectOption('WATCH_LIST');
    await page.getByLabel(/origem da player list/i).selectOption('MANUAL');
    await page.getByRole('button', { name: /^salvar player list$/i }).click();

    await expect(page.getByText(listName)).toBeVisible({ timeout: 10_000 });
    await page.getByRole('cell', { name: listName }).click();

    await page.getByLabel(/tipo do valor da entrada/i).selectOption('CPF');
    await page.getByLabel(/entradas manuais da player list/i).fill('12345678900\n99999999999');
    await page.getByRole('button', { name: /adicionar entradas à player list/i }).click();

    await expect(page.getByText('12345678900')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('99999999999')).toBeVisible({ timeout: 10_000 });
  });
});
