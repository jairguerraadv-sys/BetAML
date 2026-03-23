import { test, expect } from '@playwright/test';

import { apiLogin, fetchFirstPlayerId, login } from './helpers';

test.describe('Feature Store', () => {
  test('can open a player feature store page and switch tabs', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    await login(page);
    await page.goto(`/feature-store/${playerId}`);

    await expect(page.getByRole('heading', { name: /feature store/i })).toBeVisible();
    await expect(page.getByText(playerId)).toBeVisible();
    await expect(page.getByRole('button', { name: /atuais/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /histórico/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /distribuição/i })).toBeVisible();

    await page.getByRole('button', { name: /histórico/i }).click();
    await expect(page.getByText(/histórico de snapshots|nenhum snapshot encontrado/i)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /distribuição/i }).click();
    await expect(page.getByText(/estatísticas populacionais|carregando estatísticas/i)).toBeVisible({ timeout: 10_000 });
  });
});
