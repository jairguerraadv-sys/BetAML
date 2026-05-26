import { test, expect } from '@playwright/test';

import { login } from './helpers';

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('KPI cards are visible', async ({ page }) => {
    await expect(page.getByRole('link', { name: /^alertas abertos\b/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /^em investigação\b/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /^próximos do sla\b/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /^jogadores alto risco\b/i })).toBeVisible();
    await expect(page.getByText(/^eventos ingeridos hoje$/i)).toBeVisible();
  });

  test('KPI values are numeric', async ({ page }) => {
    const kpiCard = page.getByRole('link', { name: /alertas abertos/i });
    await expect(kpiCard).toBeVisible();
    const text = await kpiCard.locator('p.text-4xl').textContent();
    expect(Number(String(text).replace(/[^\d.-]/g, ''))).toBeGreaterThanOrEqual(0);
  });

  test('charts and heatmap render', async ({ page }) => {
    await expect(page.getByText(/alertas por severidade, últimos 30 dias/i)).toBeVisible();
    await expect(page.getByText(/distribuição por tipo de alerta/i)).toBeVisible();
    await expect(page.getByLabel(/mapa de calor de alertas por hora e dia da semana/i)).toBeVisible();
  });

  test('navigation links work', async ({ page }) => {
    await page.getByRole('link', { name: /alertas abertos/i }).click();
    await expect(page).toHaveURL(/\/alerts/, { timeout: 10_000 });
  });
});
