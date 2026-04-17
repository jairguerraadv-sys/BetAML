import { test, expect } from '@playwright/test';

import { loginAsSuperAdmin } from './helpers';

test.describe('Onboarding Wizard', () => {
  test('admin can complete the tenant onboarding wizard end-to-end', async ({ page }) => {
    const stamp = Date.now();
    const tenantName = `Operador E2E ${stamp}`;
    const tenantSlug = `operador-e2e-${stamp}`;
    const adminUsername = `tenant_admin_${stamp}`;
    const adminEmail = `tenant-admin-${stamp}@example.com`;
    const csv = [
      'player_id,amount,currency,transaction_type,occurred_at',
      `PLY-${stamp},1250,BRL,DEPOSIT,2026-03-20T10:00:00Z`,
    ].join('\n');

    await loginAsSuperAdmin(page);
    await page.goto('/admin/onboarding');

    await expect(page.getByRole('heading', { name: /cadastrar novo operador/i })).toBeVisible();

    await page.getByLabel(/nome do operador/i).fill(tenantName);
    await page.getByLabel(/slug \(identificador único\)/i).fill(tenantSlug);
    await page.getByLabel(/^cnpj$/i).fill('12.345.678/0001-95');
    await page.getByLabel(/email de contato/i).fill(`compliance-${stamp}@example.com`);
    await page.getByRole('button', { name: /^próximo$/i }).click();

    await expect(page.getByRole('heading', { name: /etapa 2/i })).toBeVisible();
    await page.getByLabel(/^username$/i).fill(adminUsername);
    await page.getByLabel(/^email$/i).fill(adminEmail);
    await page.getByLabel(/^senha$/i).fill('BetAML!234');
    await page.getByLabel(/confirmar senha/i).fill('BetAML!234');
    await page.getByRole('button', { name: /concluir cadastro/i }).click();

    await expect(page.getByRole('heading', { name: /etapa 3/i })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(new RegExp(tenantSlug, 'i'))).toBeVisible();
    await page.getByRole('button', { name: /pular esta etapa/i }).click();

    await expect(page.getByRole('heading', { name: /etapa 4/i })).toBeVisible({ timeout: 15_000 });
    await page.locator('input[type="file"]').setInputFiles({
      name: `onboarding-${stamp}.csv`,
      mimeType: 'text/csv',
      buffer: Buffer.from(csv, 'utf-8'),
    });
    await expect(page.getByRole('button', { name: new RegExp(`onboarding-${stamp}\\.csv`, 'i') })).toBeVisible();
    await expect(page.getByText(/prévia/i)).toBeVisible();
    await page.getByRole('button', { name: /enviar e continuar/i }).click();

    await expect(page.getByRole('heading', { name: /etapa 5/i })).toBeVisible({ timeout: 15_000 });
    await page.getByRole('button', { name: /spike de depósito/i }).click();
    await page.getByRole('button', { name: /criar regra e concluir/i }).click();

    await expect(page.getByRole('heading', { name: /operador cadastrado com sucesso/i })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(new RegExp(tenantSlug, 'i'))).toBeVisible();
    await expect(page.getByText(new RegExp(adminUsername, 'i'))).toBeVisible();
  });
});
