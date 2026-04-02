import { test, expect } from '@playwright/test';

import { apiLogin, fetchFirstPlayerId, login } from './helpers';

test.describe('Players LGPD', () => {
  test('player detail supports data export and LGPD guarded actions', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);

    await login(page);
    await page.goto(`/players/${playerId}`);

    await expect(page.getByRole('heading', { name: /lgpd e governança de dados/i })).toBeVisible();

    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /baixar data export/i }).click();
    const download = await downloadPromise;

    expect(download.suggestedFilename()).toContain(`player-data-export-${playerId}-`);

    await page.evaluate(() => {
      const win = window as Window & { __confirmCalls?: string[] };
      win.__confirmCalls = [];
      window.confirm = (message?: string) => {
        win.__confirmCalls?.push(String(message ?? ''));
        return false;
      };
    });

    await page.getByRole('button', { name: /anonimizar \(erase\)/i }).click();
    await page.getByRole('button', { name: /right to erasure \(alias\)/i }).click();

    const confirmCalls = await page.evaluate(() => {
      const win = window as Window & { __confirmCalls?: string[] };
      return win.__confirmCalls ?? [];
    });

    expect(confirmCalls.some((msg) => /anonimização irreversível/i.test(msg))).toBeTruthy();
    expect(confirmCalls.some((msg) => /right-to-erasure/i.test(msg))).toBeTruthy();
  });
});
