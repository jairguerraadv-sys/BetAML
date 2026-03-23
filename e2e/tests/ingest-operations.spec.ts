import { test, expect } from '@playwright/test';

import {
  apiLoginAsAdmin,
  authHeaders,
  createIngestJobViaApi,
  createWebhookIngestErrorViaApi,
  loginAsAdmin,
  waitForIngestJobStatus,
  API_URL,
} from './helpers';

test.describe('Ingest Operations', () => {
  test('admin can replay a quarantined ingest error from the UI', async ({ page, request }) => {
    const session = await apiLoginAsAdmin(request);
    const created = await createWebhookIngestErrorViaApi(request, session.access_token);

    await loginAsAdmin(page);
    await page.goto('/ingest-errors');

    await page.getByLabel(/source system do filtro de quarentena/i).fill('ConnectorEpsilon');
    await page.getByRole('button', { name: /atualizar quarentena de ingestão/i }).click();

    await expect(page.getByText(created.marker)).toBeVisible({ timeout: 10_000 });
    await page.getByRole('button', { name: /^replay$/i }).first().click();

    await expect(page.getByRole('heading', { name: /replay de payload corrigido/i })).toBeVisible();
    await page.getByLabel(/payload corrigido do replay de ingestão/i).fill(JSON.stringify({
      event_id: created.marker,
      external_player_id: `CPF-${Date.now()}`,
      transaction_type: 'DEPOSIT',
      amount: 1000,
      occurred_at: '2026-03-20T12:00:00Z',
      currency: 'BRL',
    }, null, 2));
    await page.getByLabel(/nota do replay de ingestão/i).fill('Replay validado pela suíte E2E');

    const replayResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/ingest/errors/') && response.url().endsWith('/replay') && response.request().method() === 'POST',
    );

    await page.getByLabel(/enviar replay de ingestão/i).click();
    const replayResponse = await replayResponsePromise;
    expect(replayResponse.status()).toBe(202);
    const replayBody = await replayResponse.json();
    expect(replayBody.status).toBe('queued');
    expect(replayBody.resolved).toBeTruthy();
  });

  test('admin can reprocess an eligible ingest job from the UI', async ({ page, request }) => {
    const session = await apiLoginAsAdmin(request);
    const created = await createIngestJobViaApi(request, session.access_token);
    const originalJob = await waitForIngestJobStatus(
      request,
      session.access_token,
      created.body.job_id,
      ['DONE', 'PARTIAL', 'FAILED'],
    );

    await loginAsAdmin(page);
    await page.goto('/ingest-jobs');

    await page.getByLabel(/source system do filtro de ingest jobs/i).fill('BackofficeAlpha');
    await page.getByRole('button', { name: /atualizar jobs de ingestão/i }).click();

    await expect(page.getByText(created.fileName)).toBeVisible({ timeout: 10_000 });
    await page.getByText(created.fileName).first().click();

    await expect(page.getByRole('heading', { name: /detalhes do job/i })).toBeVisible();
    await page.getByLabel(/reprocessar job de ingestão/i).click();
    await page.getByLabel(/motivo do reprocessamento do job/i).fill('Reprocessamento exercitado pela suíte E2E');

    const reprocessResponsePromise = page.waitForResponse((response) =>
      response.url().includes(`/ingest/jobs/${originalJob.id}`) && response.url().endsWith('/reprocess') && response.request().method() === 'POST',
    );

    await page.getByLabel(/confirmar reprocessamento do job/i).click();
    const reprocessResponse = await reprocessResponsePromise;
    expect(reprocessResponse.status()).toBe(202);
    const reprocessBody = await reprocessResponse.json();
    expect(reprocessBody.status).toBe('QUEUED');

    const newJobResponse = await request.get(`${API_URL}/ingest/jobs/${reprocessBody.job_id}`, {
      headers: authHeaders(session.access_token),
    });
    expect(newJobResponse.ok()).toBeTruthy();
    const newJob = await newJobResponse.json();
    expect(newJob.reprocessed_from).toBe(String(originalJob.id));
  });
});
