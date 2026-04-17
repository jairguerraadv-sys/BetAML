import { test, expect } from '@playwright/test';

import {
  apiLoginAsAdmin,
  createMappingViaApi,
  createMappingVersionViaApi,
  loginAsAdmin,
  rollbackMappingVersionViaApi,
} from './helpers';

test.describe('Mappings Versioning', () => {
  test('admin can inspect versions and rollback a mapping version', async ({ page, request }) => {
    test.setTimeout(90_000);

    const session = await apiLoginAsAdmin(request);
    const uniqueName = `E2E Mapping Versioned ${Date.now()}`;
    const sourceSystem = `ConnectorGammaE2E-${Date.now()}`;

    const first = await createMappingViaApi(request, session.access_token, {
      name: uniqueName,
      source_system: sourceSystem,
      change_notes: 'Versão inicial E2E',
    });
    const second = await createMappingVersionViaApi(request, session.access_token, first.id, {
      name: uniqueName,
      change_notes: 'Versão 2 E2E',
    });

    await loginAsAdmin(page);
    await page.goto('/mappings');
    await page.waitForLoadState('networkidle');

    const currentVersionRow = page.getByRole('row', {
      name: new RegExp(`${uniqueName} ${sourceSystem} TRANSACTION ${second.version_number} SIM`, 'i'),
    });
    await expect(currentVersionRow).toBeVisible({ timeout: 30_000 });
    await currentVersionRow.click();

    await expect(page.getByText(/versões/i)).toBeVisible();
    await expect(page.getByText(`v${second.version_number} (atual)`)).toBeVisible({ timeout: 30_000 });

    await rollbackMappingVersionViaApi(request, session.access_token, second.id, first.version_number);

    await page.reload();
    await page.waitForLoadState('networkidle');
    const rolledBackCurrentRow = page.getByRole('row', {
      name: new RegExp(`${uniqueName} ${sourceSystem} TRANSACTION ${second.version_number + 1} SIM`, 'i'),
    });
    await expect(rolledBackCurrentRow).toBeVisible({ timeout: 30_000 });
    await rolledBackCurrentRow.click();

    await expect(page.getByText(`v${second.version_number + 1} (atual)`)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(`Rollback para v${first.version_number}`)).toBeVisible({ timeout: 30_000 });
  });
});
