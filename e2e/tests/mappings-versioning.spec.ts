import { test, expect } from '@playwright/test';

import {
  apiLoginAsAdmin,
  createMappingViaApi,
  createMappingVersionViaApi,
  loginAsAdmin,
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

    const mappingCell = page.getByRole('gridcell', { name: uniqueName }).first();
    await expect(mappingCell).toBeVisible({ timeout: 30_000 });
    await mappingCell.click();

    await expect(page.getByText(/versões/i)).toBeVisible();
    await expect(page.getByText(`v${second.version_number} (atual)`)).toBeVisible({ timeout: 30_000 });

    await page.getByLabel(`Rollback versão ${first.version_number} do mapping`).click();
    await expect(page.getByText(`v${first.version_number} (atual)`)).toBeVisible({ timeout: 30_000 });
  });
});
