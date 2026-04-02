import { test, expect } from '@playwright/test';

import { apiLogin, createCaseViaApi, fetchFirstPlayerId, login } from './helpers';

test.describe('Cases', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/cases');
  });

  test('cases page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /casos em investigação/i })).toBeVisible();
  });

  test('filter tabs are visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: /em andamento/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /encerrados/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /todos/i })).toBeVisible();
  });

  test('new case button is present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /novo caso/i })).toBeVisible();
  });

  test('creating a case via UI opens its detail page', async ({ page }) => {
    const title = `E2E Manual Case ${Date.now()}`;
    await page.getByRole('button', { name: /novo caso/i }).click();
    await expect(page).toHaveURL(/\/cases\/new/, { timeout: 10_000 });

    await page.getByLabel(/título do caso/i).fill(title);
    await page.getByLabel(/descrição do caso/i).fill('Caso criado pela suíte E2E para validar o fluxo manual.');
    await page.getByLabel(/severidade do caso/i).selectOption('HIGH');
    await page.getByRole('button', { name: /criar caso/i }).click();

    await expect(page).toHaveURL(/\/cases\/.+/, { timeout: 10_000 });
    await expect(page.getByRole('button', { name: /visão geral/i })).toBeVisible();
    await expect(page.locator('h1').first()).toContainText(/E2E Manual Case/i);
  });

  test('case detail supports workflow notes and report package generation', async ({ page, request }) => {
    const session = await apiLogin(request);
    const playerId = await fetchFirstPlayerId(request, session.access_token);
    const createdCase = await createCaseViaApi(request, session.access_token, {
      title: `E2E Workflow Case ${Date.now()}`,
      severity: 'HIGH',
      player_id: playerId,
    });

    await page.goto(`/cases/${createdCase.id}`);
    await expect(page).toHaveURL(new RegExp(`/cases/${createdCase.id}$`), { timeout: 10_000 });

    await expect(page.getByRole('button', { name: /visão geral/i })).toBeVisible();

    await page.getByRole('button', { name: /clique para adicionar uma anotação ao caso/i }).click();
    await page.getByLabel(/anotação do caso/i).fill('Comentário E2E para validar a timeline do caso.');
    await page.getByRole('button', { name: /anotar/i }).click();
    await expect(page.getByText(/comentário e2e para validar a timeline do caso/i)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /decisão e relatório/i }).click();
    await page.getByLabel(/decisão no report package: no_action/i).check();
    await page.getByRole('button', { name: /gerar dossiê/i }).click();

    await expect(page.getByText(/relatório gerado com sucesso/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('heading', { name: /histórico de reportpackages/i })).toBeVisible();
  });

  test('case detail can request a narrative suggestion', async ({ page, request }) => {
    const session = await apiLogin(request);
    const createdCase = await createCaseViaApi(request, session.access_token, {
      title: `E2E Narrative Suggestion ${Date.now()}`,
      severity: 'HIGH',
    });

    await page.goto(`/cases/${createdCase.id}`);
    await page.getByRole('button', { name: /decisão e relatório/i }).click();

    await page.getByRole('button', { name: /sugerir narrativa inicial/i }).click();
    await expect(page.getByLabel(/narrativa analítica do report package/i)).not.toHaveValue('', { timeout: 10_000 });
  });
});
