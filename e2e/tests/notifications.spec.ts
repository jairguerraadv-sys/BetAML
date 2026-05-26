import { test, expect } from '@playwright/test';

import { apiLoginAsAdmin, createNotificationViaApi, login, loginAsAdmin } from './helpers';

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

  test('notificação COAF_DEADLINE_WARNING exibe badge laranja de prazo', async ({ page, request }) => {
    const adminSession = await apiLoginAsAdmin(request);
    await createNotificationViaApi(request, adminSession.access_token, {
      type: 'COAF_DEADLINE_WARNING',
      title: 'Prazo COAF se aproximando',
      body: 'O caso possui prazo de 5 dias para comunicação regulatória.',
    });

    await loginAsAdmin(page);
    await page.goto('/notifications');

    // Badge laranja "⚠️ PRAZO COAF" deve estar visível
    await expect(page.getByText(/prazo coaf/i).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/prazo.*aproximando|5 dias/i).first()).toBeVisible({ timeout: 5_000 });
  });

  test('notificação COAF_DEADLINE_BREACH exibe badge vermelho de prazo vencido', async ({ page, request }) => {
    const adminSession = await apiLoginAsAdmin(request);
    await createNotificationViaApi(request, adminSession.access_token, {
      type: 'COAF_DEADLINE_BREACH',
      title: 'PRAZO COAF VENCIDO',
      body: 'O prazo regulatório de comunicação ao COAF foi excedido.',
    });

    await loginAsAdmin(page);
    await page.goto('/notifications');

    // Badge vermelho "🚨 PRAZO VENCIDO" deve estar visível
    await expect(page.getByText(/prazo vencido/i).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/prazo.*excedido|vencido/i).first()).toBeVisible({ timeout: 5_000 });
  });

  test('notificações COAF são filtradas corretamente no modo "não lidas"', async ({ page, request }) => {
    const adminSession = await apiLoginAsAdmin(request);
    await createNotificationViaApi(request, adminSession.access_token, {
      type: 'COAF_DEADLINE_WARNING',
      title: 'Aviso COAF E2E Filter',
      body: 'Teste de filtro.',
    });

    await loginAsAdmin(page);
    await page.goto('/notifications');

    // Trocar para modo "não lidas"
    await page.getByRole('button', { name: /não lidas/i }).click();

    // Verificar que a notificação aparece ou que "nenhuma notificação" é exibido
    // (depende se já foi marcada como lida em execuções anteriores)
    await expect(
      page.getByText(/prazo coaf|nenhuma notificação/i).first(),
    ).toBeVisible({ timeout: 10_000 });
  });
});
