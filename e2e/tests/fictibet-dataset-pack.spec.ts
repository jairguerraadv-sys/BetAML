import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiLoginAsAdmin,
  authHeaders,
  buildEpsilonWebhookHeaders,
  readFictibetDatasetFile,
  waitForIngestJobStatus,
} from './helpers';

test.describe('FictiBet Dataset Pack', () => {
  test.setTimeout(90_000);

  test('admin can ingest the full realistic pack across all ingestion channels', async ({ request }) => {
    const session = await apiLoginAsAdmin(request);
    const headers = authHeaders(session.access_token);

    const canonicalPayload = await readFictibetDatasetFile('01-fictibet-canonical-events.ndjson');
    const canonicalResponse = await request.post(`${API_URL}/ingest/file`, {
      headers,
      multipart: {
        source_system: 'BackofficeAlpha',
        file: {
          name: `fictibet-canonical-${Date.now()}.ndjson`,
          mimeType: 'application/x-ndjson',
          buffer: canonicalPayload,
        },
      },
    });
    expect(canonicalResponse.status()).toBe(202);
    const canonicalBody = await canonicalResponse.json() as { job_id: string; status: string };
    expect(canonicalBody.status).toBe('QUEUED');

    const canonicalJob = await waitForIngestJobStatus(
      request,
      session.access_token,
      canonicalBody.job_id,
      ['DONE', 'PARTIAL'],
      45_000,
    );
    expect(Number(canonicalJob.total_records ?? 0)).toBeGreaterThanOrEqual(40);
    expect(Number(canonicalJob.processed_records ?? 0)).toBeGreaterThan(0);

    const gammaPayload = await readFictibetDatasetFile('02-fictibet-connector-gamma.xml');
    const gammaResponse = await request.post(`${API_URL}/ingest/connectors/gamma/parse`, {
      headers,
      multipart: {
        entity_type: 'TRANSACTION',
        file: {
          name: `fictibet-gamma-${Date.now()}.xml`,
          mimeType: 'application/xml',
          buffer: gammaPayload,
        },
      },
    });
    expect(gammaResponse.status()).toBe(202);
    const gammaBody = await gammaResponse.json() as {
      job_id: string;
      source_system: string;
      status: string;
      summary: { total: number; accepted: number; failed: number };
    };
    expect(gammaBody.source_system).toBe('ConnectorGamma');
    expect(gammaBody.status).toBe('PARTIAL');
    expect(gammaBody.summary).toEqual(expect.objectContaining({
      total: 5,
      accepted: 3,
      failed: 2,
    }));

    const deltaPayload = await readFictibetDatasetFile('03-fictibet-connector-delta.ndjson');
    const deltaResponse = await request.post(`${API_URL}/ingest/connectors/delta/parse`, {
      headers,
      multipart: {
        entity_type: 'TRANSACTION',
        file: {
          name: `fictibet-delta-${Date.now()}.ndjson`,
          mimeType: 'application/x-ndjson',
          buffer: deltaPayload,
        },
      },
    });
    expect(deltaResponse.status()).toBe(202);
    const deltaBody = await deltaResponse.json() as {
      job_id: string;
      source_system: string;
      status: string;
      summary: { total: number; accepted: number; failed: number };
    };
    expect(deltaBody.source_system).toBe('ConnectorDelta');
    expect(deltaBody.status).toBe('PARTIAL');
    expect(deltaBody.summary).toEqual(expect.objectContaining({
      total: 8,
      accepted: 5,
      failed: 3,
    }));

    const epsilonPayload = await readFictibetDatasetFile('04-fictibet-connector-epsilon-webhook.json');
    const epsilonResponse = await request.post(`${API_URL}/ingest/webhook/epsilon`, {
      headers: {
        ...headers,
        ...buildEpsilonWebhookHeaders(epsilonPayload),
        'content-type': 'application/json',
      },
      data: epsilonPayload,
    });
    expect(epsilonResponse.status()).toBe(202);
    const epsilonBody = await epsilonResponse.json() as { status: string; count: number; job_id: string };
    expect(epsilonBody.status).toBe('accepted');
    expect(epsilonBody.count).toBe(6);

    const gammaErrorsResponse = await request.get(`${API_URL}/ingest/errors`, {
      headers,
      params: { job_id: gammaBody.job_id, source_system: 'ConnectorGamma' },
    });
    expect(gammaErrorsResponse.ok()).toBeTruthy();
    const gammaErrors = await gammaErrorsResponse.json() as Array<{ id: string }>;
    expect(gammaErrors.length).toBeGreaterThanOrEqual(2);

    const deltaErrorsResponse = await request.get(`${API_URL}/ingest/errors`, {
      headers,
      params: { job_id: deltaBody.job_id, source_system: 'ConnectorDelta' },
    });
    expect(deltaErrorsResponse.ok()).toBeTruthy();
    const deltaErrors = await deltaErrorsResponse.json() as Array<{ id: string }>;
    expect(deltaErrors.length).toBeGreaterThanOrEqual(3);

    const reprocessResponse = await request.post(`${API_URL}/ingest/jobs/${gammaBody.job_id}/reprocess`, {
      headers,
      data: {
        reason: `dataset-pack-reprocess-${Date.now()}`,
      },
    });
    expect(reprocessResponse.status()).toBe(202);
    const reprocessBody = await reprocessResponse.json() as { job_id: string; status: string };
    expect(reprocessBody.status).toBe('QUEUED');

    const reprocessedJob = await waitForIngestJobStatus(
      request,
      session.access_token,
      reprocessBody.job_id,
      ['DONE', 'PARTIAL', 'FAILED'],
      45_000,
    );
    expect(String(reprocessedJob.reprocessed_from)).toBe(gammaBody.job_id);
    expect(Number(reprocessedJob.total_records ?? 0)).toBe(5);
    expect(Number(reprocessedJob.failed_records ?? 0)).toBeGreaterThanOrEqual(2);
  });
});
