import { test, expect } from '@playwright/test';

import { API_URL, apiLoginAsAdmin, authHeaders, loginAsAdmin } from './helpers';

type ModelRegistryItem = {
  id: string;
  version: string;
  model_type: string;
  status: string;
  is_challenger?: boolean;
};

test.describe('Model Registry', () => {
  test('loads model registry summary and cards', async ({ page, request }) => {
    const session = await apiLoginAsAdmin(request);
    const response = await request.get(`${API_URL}/model-registry`, {
      headers: authHeaders(session.access_token),
    });
    expect(response.ok()).toBeTruthy();
    const models = (await response.json()) as ModelRegistryItem[];

    await loginAsAdmin(page);
    await page.goto('/model-registry');

    await expect(page.getByRole('heading', { name: /modelos analíticos/i })).toBeVisible();

    if (models.length === 0) {
      await expect(page.getByText(/nenhum modelo registrado ainda/i)).toBeVisible();
      return;
    }

    await expect(page.getByText(/precisão estimada/i)).toBeVisible();
    await expect(page.getByText(/falso alarme|falso positivo/i)).toBeVisible();
    await expect(page.getByText(/alertas avaliados|alertas rotulados/i)).toBeVisible();
    await expect(page.getByText(/tráfego no teste a\/b|tráfego challenger/i)).toBeVisible();
    await expect(page.getByText(new RegExp(`v${models[0]?.version}`))).toBeVisible();
  });

  test('admin can use an available model action', async ({ page, request }) => {
    const session = await apiLoginAsAdmin(request);
    const response = await request.get(`${API_URL}/model-registry`, {
      headers: authHeaders(session.access_token),
    });
    expect(response.ok()).toBeTruthy();
    const models = (await response.json()) as ModelRegistryItem[];

    await loginAsAdmin(page);
    await page.goto('/model-registry');

    const stagingCandidate = models.find((model) => model.status === 'STAGING' && !model.is_challenger);
    const challengerCandidate = models.find((model) => model.status === 'challenger' || model.is_challenger);

    if (stagingCandidate) {
      const action = page.getByLabel(`Designar challenger para modelo versão ${stagingCandidate.version}`).first();
      await expect(action).toBeVisible({ timeout: 10_000 });
      await action.click();
      await expect(page.getByText(/teste a\/b|a\/b challenger/i)).toBeVisible({ timeout: 10_000 });
      return;
    }

    if (challengerCandidate) {
      const action = page.getByLabel(`Promover modelo versão ${challengerCandidate.version} para produção`).first();
      await expect(action).toBeVisible({ timeout: 10_000 });
      await action.click();
      await expect(page.getByText(/em produção/i)).toBeVisible({ timeout: 10_000 });
      return;
    }

    if (models.length === 0) {
      await expect(page.getByText(/nenhum modelo registrado ainda/i)).toBeVisible();
      return;
    }

    await expect(page.getByText(/pronto para teste|em produção|teste a\/b|a\/b challenger/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /designar challenger|promover para produção/i })).toHaveCount(0);
  });
});
