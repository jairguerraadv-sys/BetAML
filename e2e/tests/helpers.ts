import { expect, type APIRequestContext, type Page } from '@playwright/test';
import { createHmac } from 'crypto';
import { readFile } from 'fs/promises';
import path from 'path';

export const BASE_URL = process.env.BASE_URL ?? 'http://localhost:3000';
export const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8000';
export const USERNAME = process.env.E2E_USERNAME ?? '';
export const PASSWORD = process.env.E2E_PASSWORD ?? '';
const REPO_ROOT = process.env.REPO_ROOT ?? path.resolve(process.cwd(), '..');
const FICTIBET_DATASET_DIR = path.join(REPO_ROOT, 'datasets', 'fictibet_pld');
const SECONDARY_ADMIN_USERNAME_ENV = process.env.E2E_SECONDARY_ADMIN_USERNAME ?? process.env.E2E_TENANT_B_USERNAME;
const SECONDARY_ADMIN_PASSWORD_ENV = process.env.E2E_SECONDARY_ADMIN_PASSWORD ?? process.env.E2E_TENANT_B_PASSWORD;

function valueOrFallback(value: string | undefined, fallback = '') {
  if (!value) return fallback;
  if (value.startsWith('your_')) return fallback;
  return value;
}

export const ADMIN_USERNAME = valueOrFallback(process.env.E2E_ADMIN_USERNAME, USERNAME);
export const ADMIN_PASSWORD = valueOrFallback(process.env.E2E_ADMIN_PASSWORD, PASSWORD);
export const AUDITOR_USERNAME = valueOrFallback(process.env.E2E_AUDITOR_USERNAME);
export const AUDITOR_PASSWORD = valueOrFallback(process.env.E2E_AUDITOR_PASSWORD);
export const SUPER_ADMIN_USERNAME = valueOrFallback(
  process.env.E2E_SUPER_ADMIN_USERNAME ?? process.env.SUPER_ADMIN_USER,
  'superadmin',
);
export const SUPER_ADMIN_PASSWORD = valueOrFallback(
  process.env.E2E_SUPER_ADMIN_PASSWORD ?? process.env.SUPER_ADMIN_PASS,
  'superadmin123',
);
export const SECONDARY_ADMIN_USERNAME = valueOrFallback(SECONDARY_ADMIN_USERNAME_ENV, 'admin_b');
export const SECONDARY_ADMIN_PASSWORD = valueOrFallback(SECONDARY_ADMIN_PASSWORD_ENV, 'admin123');

type RoleCredentials = {
  username: string;
  password: string;
  email?: string;
};

let cachedAuditorCredentials: RoleCredentials | null = null;

export function assertE2ECredentials() {
  if (!USERNAME || !PASSWORD) {
    throw new Error('E2E_USERNAME e E2E_PASSWORD devem estar definidos no ambiente.');
  }
}

async function waitForHttpReady(url: string, timeoutMs = 30_000) {
  const startedAt = Date.now();
  let lastError: unknown;

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url, { redirect: 'manual' });
      if (response.ok || response.status === 307 || response.status === 308 || response.status === 401) {
        return;
      }
      lastError = new Error(`Unexpected status ${response.status} for ${url}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  throw new Error(`Timed out waiting for ${url}: ${String(lastError)}`);
}

export async function login(page: Page) {
  assertE2ECredentials();
  await loginWithCredentials(page, USERNAME, PASSWORD);
}

export async function loginAsAdmin(page: Page) {
  await loginWithCredentials(page, ADMIN_USERNAME, ADMIN_PASSWORD);
}

export async function loginAsSuperAdmin(page: Page) {
  await loginWithCredentials(page, SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD);
}

export async function loginAsAuditor(page: Page, request?: APIRequestContext) {
  const credentials = request ? await ensureAuditorCredentials(request) : { username: AUDITOR_USERNAME, password: AUDITOR_PASSWORD };
  await loginWithCredentials(page, credentials.username, credentials.password);
}

export async function loginWithCredentials(page: Page, username: string, password: string) {
  await waitForHttpReady(`${BASE_URL}/login`);
  await waitForHttpReady(`${API_URL}/health/live`);

  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.goto('/login');
    await page.getByLabel(/usuário|username/i).fill(username);
    await page.getByLabel(/senha|password/i).fill(password);
    await page.getByRole('button', { name: /entrar|login/i }).click();

    try {
      await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 8_000 });
      return;
    } catch (error) {
      lastError = error;
      await page.waitForTimeout(1000 * (attempt + 1));
    }
  }

  throw lastError ?? new Error('Login did not complete successfully.');
}

export async function apiLogin(request: APIRequestContext) {
  assertE2ECredentials();
  return apiLoginWithCredentials(request, USERNAME, PASSWORD);
}

export async function apiLoginAsAdmin(request: APIRequestContext) {
  return apiLoginWithCredentials(request, ADMIN_USERNAME, ADMIN_PASSWORD);
}

export async function apiLoginAsSuperAdmin(request: APIRequestContext) {
  return apiLoginWithCredentials(request, SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD);
}

export async function apiLoginWithCredentials(
  request: APIRequestContext,
  username: string,
  password: string,
) {
  await waitForHttpReady(`${API_URL}/health/live`);
  const response = await request.post(`${API_URL}/auth/login`, {
    data: { username, password },
  });
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  expect(body?.access_token).toBeTruthy();
  return body as { access_token: string; role?: string; tenant_id?: string };
}

export async function apiLoginAsSecondaryAdmin(request: APIRequestContext) {
  return apiLoginWithCredentials(request, SECONDARY_ADMIN_USERNAME, SECONDARY_ADMIN_PASSWORD);
}

export async function apiLoginAsAuditor(request: APIRequestContext) {
  const credentials = await ensureAuditorCredentials(request);
  await waitForHttpReady(`${API_URL}/health/live`);
  const response = await request.post(`${API_URL}/auth/login`, {
    data: { username: credentials.username, password: credentials.password },
  });
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  expect(body?.access_token).toBeTruthy();
  return body as { access_token: string; role?: string; tenant_id?: string };
}

export function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}` };
}

export function resolveFictibetDatasetPath(fileName: string) {
  return path.join(FICTIBET_DATASET_DIR, fileName);
}

export async function readFictibetDatasetFile(fileName: string) {
  return readFile(resolveFictibetDatasetPath(fileName));
}

export function buildEpsilonWebhookHeaders(
  payload: Buffer,
  options?: {
    secret?: string;
    timestamp?: number;
  },
) {
  const timestamp = options?.timestamp ?? Math.floor(Date.now() / 1000);
  const secret = options?.secret ?? process.env.EPSILON_WEBHOOK_SECRET ?? 'dev-secret-change-me';

  const signature = createHmac('sha256', secret)
    .update(`${timestamp}.`, 'utf-8')
    .update(payload)
    .digest('hex');

  return {
    'x-epsilon-timestamp': String(timestamp),
    'x-epsilon-signature': `sha256=${signature}`,
  };
}

export async function fetchMappingTemplate(
  request: APIRequestContext,
  token: string,
  sourceSystem = 'ConnectorGamma',
) {
  const response = await request.get(`${API_URL}/mappings/templates`, {
    headers: authHeaders(token),
  });
  expect(response.ok()).toBeTruthy();
  const templates = (await response.json()) as Array<{
    source_system: string;
    template: string;
    format?: 'json' | 'yaml';
  }>;
  const template = templates.find((item) => item.source_system === sourceSystem);
  expect(template).toBeTruthy();
  return template!;
}

export async function createMappingViaApi(
  request: APIRequestContext,
  token: string,
  overrides?: Partial<{
    name: string;
    source_system: string;
    entity_type: string;
    change_notes: string;
  }>,
) {
  const template = await fetchMappingTemplate(request, token, 'ConnectorGamma');
  const retries = Number(process.env.E2E_API_RETRIES ?? '3');
  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

  let lastResponse: Awaited<ReturnType<APIRequestContext['post']>> | null = null;
  let lastResponseBodyPreview = '';

  for (let attempt = 1; attempt <= retries; attempt += 1) {
    const response = await request.post(`${API_URL}/mappings`, {
      headers: authHeaders(token),
      data: {
        name: overrides?.name ?? `E2E Mapping ${Date.now()}`,
        source_system: overrides?.source_system ?? `ConnectorGammaE2E-${Date.now()}`,
        entity_type: overrides?.entity_type ?? 'TRANSACTION',
        config_text: template.template,
        format: template.format ?? 'yaml',
        change_notes: overrides?.change_notes ?? 'Criação via helper E2E',
      },
    });

    if (response.ok()) {
      return (await response.json()) as { id: string; name: string; version_number: number; is_current: boolean };
    }

    lastResponse = response;
    const responseText = await response.text();
    lastResponseBodyPreview = responseText.slice(0, 800);
    const status = response.status();
    const isTransient = status >= 500 || status === 429 || status === 408;

    if (!isTransient || attempt === retries) {
      break;
    }

    await sleep(500 * attempt);
  }

  const statusText = lastResponse ? `${lastResponse.status()} ${lastResponse.statusText()}` : 'no-response';
  expect(
    lastResponse?.ok(),
    `createMappingViaApi failed after ${retries} attempts; status=${statusText}; body=${lastResponseBodyPreview}`,
  ).toBeTruthy();
  return (await lastResponse!.json()) as { id: string; name: string; version_number: number; is_current: boolean };
}

export async function createMappingVersionViaApi(
  request: APIRequestContext,
  token: string,
  mappingId: string,
  overrides?: Partial<{
    name: string;
    change_notes: string;
  }>,
) {
  const response = await request.put(`${API_URL}/mappings/${mappingId}`, {
    headers: authHeaders(token),
    data: {
      name: overrides?.name,
      change_notes: overrides?.change_notes ?? `Nova versão E2E ${Date.now()}`,
      format: 'yaml',
    },
  });
  expect(response.ok()).toBeTruthy();
  return await response.json() as { id: string; name: string; version_number: number; is_current: boolean };
}

export async function rollbackMappingVersionViaApi(
  request: APIRequestContext,
  token: string,
  mappingId: string,
  versionNumber: number,
) {
  const response = await request.post(`${API_URL}/mappings/${mappingId}/rollback`, {
    headers: authHeaders(token),
    params: { version_number: String(versionNumber) },
  });
  expect(response.ok()).toBeTruthy();
  return await response.json() as {
    id: string;
    version_number: number;
    rollback_source_version_number: number;
    rollback_source_mapping_id: string;
  };
}

export async function createCaseViaApi(
  request: APIRequestContext,
  token: string,
  overrides?: Partial<{
    title: string;
    description: string;
    player_id: string;
    severity: string;
  }>,
) {
  const playerId = overrides?.player_id ?? await fetchFirstPlayerId(request, token);
  const response = await request.post(`${API_URL}/cases`, {
    headers: authHeaders(token),
    data: {
      title: overrides?.title ?? `E2E Case ${Date.now()}`,
      description: overrides?.description ?? 'Caso criado automaticamente pela suíte E2E',
      player_id: playerId,
      severity: overrides?.severity ?? 'HIGH',
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as { id: string; title: string; status: string };
}

export async function ensureAuditorCredentials(request: APIRequestContext): Promise<RoleCredentials> {
  if (AUDITOR_USERNAME && AUDITOR_PASSWORD) {
    return { username: AUDITOR_USERNAME, password: AUDITOR_PASSWORD };
  }

  if (cachedAuditorCredentials) {
    return cachedAuditorCredentials;
  }

  const adminSession = await apiLoginAsAdmin(request);
  const stamp = Date.now();
  const credentials: RoleCredentials = {
    username: `e2e_auditor_${stamp}`,
    email: `e2e-auditor-${stamp}@example.com`,
    password: 'BetAML!234',
  };

  const response = await request.post(`${API_URL}/admin/users`, {
    headers: authHeaders(adminSession.access_token),
    data: {
      username: credentials.username,
      email: credentials.email,
      password: credentials.password,
      role: 'AUDITOR',
    },
  });
  expect(response.ok()).toBeTruthy();
  cachedAuditorCredentials = credentials;
  return credentials;
}

export async function createReportPackageViaApi(
  request: APIRequestContext,
  token: string,
  caseId: string,
  overrides?: Partial<{
    decision: string;
    analyst_narrative: string;
    occurrence_codes: number[];
    involvement_types: number[];
    valor_premio: number;
    valor_apostas: number;
    informacoes_adicionais: string;
  }>,
) {
  const response = await request.post(`${API_URL}/cases/${caseId}/report-package`, {
    headers: authHeaders(token),
    data: {
      decision: overrides?.decision ?? 'NO_ACTION',
      analyst_narrative: overrides?.analyst_narrative ?? 'Relatório gerado automaticamente pela suíte E2E.',
      occurrence_codes: overrides?.occurrence_codes,
      involvement_types: overrides?.involvement_types,
      valor_premio: overrides?.valor_premio,
      valor_apostas: overrides?.valor_apostas,
      informacoes_adicionais: overrides?.informacoes_adicionais,
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as {
    report_package_id: string;
    status: string;
    decision: string;
    pdf_path: string | null;
    payload: Record<string, unknown>;
  };
}

export async function changeCaseStatusViaApi(
  request: APIRequestContext,
  token: string,
  caseId: string,
  newStatus: string,
) {
  const response = await request.post(`${API_URL}/cases/${caseId}/events`, {
    headers: authHeaders(token),
    data: {
      event_type: 'STATUS_CHANGE',
      content: { new_status: newStatus },
    },
  });
  expect(response.ok()).toBeTruthy();
  return await response.json() as { id: string; event_type: string; created_at: string };
}

export async function findOpenAlertWithoutCase(request: APIRequestContext, token: string) {
  const response = await request.get(`${API_URL}/alerts`, {
    headers: authHeaders(token),
    params: { status: 'OPEN', per_page: '50' },
  });
  expect(response.ok()).toBeTruthy();
  const body = (await response.json()) as {
    items?: Array<{ id: string; case_id?: string | null; title?: string; status?: string }>;
  };
  return (body.items ?? []).find((alert) => !alert.case_id) ?? null;
}

export async function createAlertViaApi(
  request: APIRequestContext,
  token: string,
  overrides?: Partial<{
    player_id: string;
    title: string;
    description: string;
    severity: string;
    status: string;
    alert_type: string;
    evidence: Record<string, unknown>;
  }>,
) {
  const playerId = overrides?.player_id ?? await fetchFirstPlayerId(request, token);
  const response = await request.post(`${API_URL}/internal/e2e/alerts`, {
    headers: authHeaders(token),
    data: {
      player_id: playerId,
      title: overrides?.title ?? `E2E Alert ${Date.now()}`,
      description: overrides?.description ?? 'Alerta criado automaticamente pela suíte E2E',
      severity: overrides?.severity ?? 'HIGH',
      status: overrides?.status ?? 'OPEN',
      alert_type: overrides?.alert_type ?? 'RULE',
      evidence: overrides?.evidence ?? { marker: `e2e-${Date.now()}` },
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as {
    id: string;
    player_id: string | null;
    title: string;
    severity: string;
    status: string;
    case_id?: string | null;
  };
}

export async function createIngestJobViaApi(request: APIRequestContext, token: string, fileName?: string) {
  const csvName = fileName ?? `e2e-ingest-${Date.now()}.csv`;
  const csv = [
    'player_id,amount,currency,transaction_type,occurred_at,method,status',
    `PLY-E2E-${Date.now()},1500,BRL,DEPOSIT,2026-03-20T10:00:00Z,PIX,SETTLED`,
  ].join('\n');

  const response = await request.post(`${API_URL}/ingest/file`, {
    headers: authHeaders(token),
    multipart: {
      file: {
        name: csvName,
        mimeType: 'text/csv',
        buffer: Buffer.from(csv, 'utf-8'),
      },
      source_system: 'BackofficeAlpha',
    },
  });
  expect(response.ok()).toBeTruthy();
  return { fileName: csvName, body: await response.json() as { job_id: string; status: string } };
}

export async function waitForIngestJobStatus(
  request: APIRequestContext,
  token: string,
  jobId: string,
  allowedStatuses: string[],
  timeoutMs = 20_000,
) {
  const startedAt = Date.now();
  let lastBody: Record<string, unknown> | null = null;

  while (Date.now() - startedAt < timeoutMs) {
    const response = await request.get(`${API_URL}/ingest/jobs/${jobId}`, {
      headers: authHeaders(token),
    });
    expect(response.ok()).toBeTruthy();
    const body = await response.json() as Record<string, unknown>;
    lastBody = body;
    if (allowedStatuses.includes(String(body.status))) {
      return body;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  throw new Error(`Job ${jobId} não atingiu status esperado. Último payload: ${JSON.stringify(lastBody)}`);
}

export async function createWebhookIngestErrorViaApi(request: APIRequestContext, token: string) {
  const marker = `e2e-epsilon-error-${Date.now()}`;
  const payload = JSON.stringify({
    event_id: marker,
    external_player_id: `CPF-${Date.now()}`,
    transaction_type: 'DEPOSIT',
    amount: 'invalid-number',
    occurred_at: '2026-03-20T12:00:00Z',
  });

  const response = await request.post(`${API_URL}/ingest/webhook/epsilon`, {
    headers: {
      ...authHeaders(token),
      'content-type': 'application/json',
      'x-epsilon-signature': 'sha256=invalid',
      'x-epsilon-timestamp': `${Math.floor(Date.now() / 1000)}`,
    },
    data: payload,
  });
  expect(response.status()).toBe(400);
  return { marker };
}

export async function fetchFirstPlayerId(request: APIRequestContext, token: string) {
  const response = await request.get(`${API_URL}/players`, {
    headers: authHeaders(token),
    params: { limit: 10 },
  });
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  const first = Array.isArray(body) ? body[0] : null;
  expect(first?.id).toBeTruthy();
  return String(first.id);
}
