import { test, expect } from '@playwright/test';

import { apiLogin, createAlertViaApi, createCaseViaApi, login } from './helpers';

test.describe('Alerts', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/alerts');
  });

  test('alerts page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /monitor de alertas/i })).toBeVisible();
  });

  test('severity and status filters are visible', async ({ page }) => {
    await expect(page.getByRole('combobox', { name: /filtrar status dos alertas/i })).toBeVisible();
    for (const label of ['Todos', 'Crítico', 'Alto', 'Médio', 'Baixo']) {
      await expect(page.getByRole('button', { name: new RegExp(label, 'i') })).toBeVisible();
    }
  });

  test('alert detail supports triage to false positive', async ({ page, request }) => {
    const session = await apiLogin(request);
    const alert = await createAlertViaApi(request, session.access_token, {
      title: `E2E triage alert ${Date.now()}`,
    });

    await page.goto(`/alerts/${alert.id}`);
    await expect(page.getByRole('heading', { name: new RegExp(alert.title ?? 'alerta', 'i') })).toBeVisible();

    await page.getByRole('button', { name: /triagem/i }).click();
    await page.getByLabel(/disposição da triagem/i).selectOption('FALSE_POSITIVE');
    await page.getByLabel(/observação da triagem/i).fill('Triagem automatizada via Playwright');
    await page.getByRole('button', { name: /confirmar triagem/i }).click();

    await expect(page.locator('span', { hasText: 'FALSE_POSITIVE' }).first()).toBeVisible({ timeout: 10_000 });
  });

  test('alert detail can be linked to a newly created case', async ({ page, request }) => {
    const session = await apiLogin(request);
    const alert = await createAlertViaApi(request, session.access_token, {
      title: `E2E link alert ${Date.now()}`,
    });

    const createdCase = await createCaseViaApi(request, session.access_token, {
      title: `Case for alert link ${Date.now()}`,
      severity: 'MEDIUM',
    });

    await page.goto(`/alerts/${alert.id}`);
    await page.getByRole('button', { name: /vincular a caso/i }).click();
    await page.getByLabel(/selecionar caso para vincular/i).selectOption(createdCase.id);
    await page.getByRole('button', { name: /vincular alerta a caso/i }).click();

    await expect(page.getByRole('link', { name: /ver caso/i })).toBeVisible({ timeout: 10_000 });
  });

  test('alert detail supports label feedback and close action', async ({ page, request }) => {
    const session = await apiLogin(request);
    const alert = await createAlertViaApi(request, session.access_token, {
      title: `E2E close and label alert ${Date.now()}`,
    });

    await page.goto(`/alerts/${alert.id}`);
    await expect(page.getByRole('heading', { name: new RegExp(alert.title ?? 'alerta', 'i') })).toBeVisible();

    await page.locator('select').first().selectOption('FALSE_POSITIVE');
    await page.getByPlaceholder(/nota opcional para feedback do modelo/i).fill('Feedback de qualidade via Playwright');
    await page.getByRole('button', { name: /aplicar label/i }).click();

    await expect(page.getByText(/label atualizado com sucesso/i)).toBeVisible({ timeout: 10_000 });

    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: /fechar alerta/i }).click();

    await expect(page.getByText('CLOSED')).toBeVisible({ timeout: 10_000 });
  });
});
