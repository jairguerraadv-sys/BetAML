import { test, expect } from '@playwright/test';

import { loginAsAdmin } from './helpers';

test.describe('UI/API contracts smoke', () => {
  test('critical flows load without fatal UI crash', async ({ page, request }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (err) => pageErrors.push(String(err.message || err)));

    await loginAsAdmin(page);

    await page.goto('/alerts');
    await expect(page.getByRole('heading', { name: /monitor de alertas/i })).toBeVisible();
    await expect(
      page.getByText(/nenhum alerta encontrado|ver detalhes|investigar/i).first(),
    ).toBeVisible({ timeout: 15_000 });

    await page.goto('/cases');
    await expect(page.getByRole('heading', { name: /casos em investigação/i })).toBeVisible();
    await expect(
      page.getByText(/nenhum caso encontrado|novo caso|caso/i).first(),
    ).toBeVisible({ timeout: 15_000 });

    const firstCaseRow = page.getByTestId('case-row').first();
    if (await firstCaseRow.count()) {
      await firstCaseRow.click();
      await expect(page).toHaveURL(/\/cases\/[^/]+/);
      await expect(page.getByText(/cadeia de custódia|status|decisão/i).first()).toBeVisible();
    }

    await page.goto('/reports');
    await expect(page.getByRole('heading', { name: /relatórios mensais/i })).toBeVisible();
    await expect(
      page.getByText(/governança de comunicação ao coaf|consultar|enfileirar/i).first(),
    ).toBeVisible({ timeout: 15_000 });

    const rpListResp = await request.get('/api-proxy/report-packages?limit=1');
    const contentType = rpListResp.headers()['content-type'] ?? '';
    if (rpListResp.ok() && contentType.includes('application/json')) {
      const payload = (await rpListResp.json()) as unknown;
      const items = Array.isArray(payload)
        ? (payload as Array<{ id: string }>)
        : Array.isArray((payload as { items?: Array<{ id: string }> }).items)
          ? ((payload as { items: Array<{ id: string }> }).items ?? [])
          : [];
      const rpId = items[0]?.id;
      if (rpId) {
        await page.goto(`/reports/${rpId}`);
        await expect(page.getByRole('heading', { name: /dossiê cos/i })).toBeVisible();
        await expect(page.getByText(/dados do dossiê|cadeia de custódia/i).first()).toBeVisible();
      }
    }

    expect(pageErrors).toEqual([]);
  });
});
