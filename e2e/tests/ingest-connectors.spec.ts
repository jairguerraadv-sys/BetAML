import { expect, test } from '@playwright/test';

import { API_URL, apiLoginAsAdmin, authHeaders } from './helpers';

test.describe('Ingest Connectors', () => {
  test('admin can parse ConnectorGamma XML with mixed valid/invalid records and inspect job errors', async ({ request }) => {
    const session = await apiLoginAsAdmin(request);
    const headers = authHeaders(session.access_token);

    const mappingResponse = await request.post(`${API_URL}/mappings`, {
      headers,
      data: {
        name: `Gamma strict ${Date.now()}`,
        source_system: 'ConnectorGamma',
        entity_type: 'TRANSACTION',
        format: 'json',
        config_json: {
          source_system: 'ConnectorGamma',
          entity_type: 'TRANSACTION',
          fields: [
            { target: 'external_transaction_id', source: 'event_id', transform: 'copy', required: true },
            { target: 'player_cpf', source: 'external_player_id', transform: 'copy', required: true },
            { target: 'type', source: 'transaction_type', transform: 'copy', required: true },
            { target: 'amount', source: 'amount', transform: 'coerceDecimal', required: true },
            { target: 'currency', source: 'currency', transform: 'copy', required: true },
            { target: 'occurred_at', source: 'occurred_at', transform: 'parseDate', required: true },
          ],
        },
        change_notes: 'Strict gamma mapping for e2e connector failures',
      },
    });
    expect(mappingResponse.ok()).toBeTruthy();
    const mapping = await mappingResponse.json() as { id: string };

    const xmlPayload = [
      '<Events>',
      '  <Transaction>',
      '    <EventId>gamma-ok-1</EventId>',
      '    <PlayerId>CPF-GAMMA-1</PlayerId>',
      '    <Type>DEPOSIT</Type>',
      '    <Amount currency="BRL">1500.00</Amount>',
      '    <Timestamp>2026-03-20T10:00:00Z</Timestamp>',
      '    <Instrument><Type>PIX</Type><Token>pix-gamma-ok</Token></Instrument>',
      '    <DeviceId>dev-gamma-1</DeviceId>',
      '  </Transaction>',
      '  <Transaction>',
      '    <EventId>gamma-bad-1</EventId>',
      '    <PlayerId>CPF-GAMMA-2</PlayerId>',
      '    <Type>DEPOSIT</Type>',
      '    <Amount currency="BRL">not-a-number</Amount>',
      '    <Timestamp>2026-03-20T10:05:00Z</Timestamp>',
      '    <Instrument><Type>PIX</Type><Token>pix-gamma-bad</Token></Instrument>',
      '    <DeviceId>dev-gamma-2</DeviceId>',
      '  </Transaction>',
      '</Events>',
    ].join('\n');

    const parseResponse = await request.post(`${API_URL}/ingest/connectors/gamma/parse`, {
      headers,
      multipart: {
        entity_type: 'TRANSACTION',
        mapping_config_id: mapping.id,
        file: {
          name: `gamma-${Date.now()}.xml`,
          mimeType: 'application/xml',
          buffer: Buffer.from(xmlPayload, 'utf-8'),
        },
      },
    });

    expect(parseResponse.status()).toBe(202);
    const parseBody = await parseResponse.json() as {
      job_id: string;
      source_system: string;
      summary: { accepted: number; failed: number; total: number };
    };
    expect(parseBody.source_system).toBe('ConnectorGamma');
    expect(parseBody.summary.total).toBeGreaterThanOrEqual(2);
    expect(parseBody.summary.accepted).toBeGreaterThanOrEqual(1);
    expect(parseBody.summary.failed).toBeGreaterThanOrEqual(1);

    const jobResponse = await request.get(`${API_URL}/ingest/jobs/${parseBody.job_id}`, {
      headers,
    });
    expect(jobResponse.ok()).toBeTruthy();
    const job = await jobResponse.json() as {
      source_system: string;
      failed_records: number;
      error_count: number;
      bytes_processed: number;
    };
    expect(job.source_system).toBe('ConnectorGamma');
    expect(job.failed_records).toBeGreaterThanOrEqual(1);
    expect(job.error_count).toBeGreaterThanOrEqual(1);
    expect(job.bytes_processed).toBeGreaterThan(0);

    const errorsResponse = await request.get(`${API_URL}/ingest/errors`, {
      headers,
      params: {
        job_id: parseBody.job_id,
        source_system: 'ConnectorGamma',
      },
    });
    expect(errorsResponse.ok()).toBeTruthy();
    const errors = await errorsResponse.json() as Array<{ source_system: string; error_reason: string }>;
    expect(errors.length).toBeGreaterThan(0);
    expect(errors[0]?.source_system).toBe('ConnectorGamma');
    expect((errors[0]?.error_reason ?? '').length).toBeGreaterThan(0);
  });

  test('admin can parse ConnectorDelta NDJSON and quarantine malformed lines', async ({ request }) => {
    const session = await apiLoginAsAdmin(request);
    const headers = authHeaders(session.access_token);

    const mappingResponse = await request.post(`${API_URL}/mappings`, {
      headers,
      data: {
        name: `Delta strict ${Date.now()}`,
        source_system: 'ConnectorDelta',
        entity_type: 'TRANSACTION',
        format: 'json',
        config_json: {
          source_system: 'ConnectorDelta',
          entity_type: 'TRANSACTION',
          fields: [
            { target: 'external_transaction_id', source: 'id', transform: 'copy', required: true },
            { target: 'player_cpf', source: 'uid', transform: 'copy', required: true },
            { target: 'type', source: 'evt_type', transform: 'copy', required: true },
            { target: 'amount', source: 'val', transform: 'coerceDecimal', required: true },
            { target: 'currency', source: 'ccy', transform: 'copy', required: true },
            { target: 'occurred_at', source: 'ts', transform: 'parseDate', required: true },
          ],
        },
        change_notes: 'Strict delta mapping for e2e connector failures',
      },
    });
    expect(mappingResponse.ok()).toBeTruthy();
    const mapping = await mappingResponse.json() as { id: string };

    const ndjsonPayload = [
      JSON.stringify({
        id: `delta-ok-${Date.now()}`,
        uid: 'CPF-DELTA-1',
        evt_type: 'DEPOSIT',
        ts: '2026-03-20T12:00:00Z',
        val: 99.9,
        ccy: 'BRL',
      }),
      '{"id":"delta-bad-json","uid":"CPF-DELTA-2"', // malformed JSON line
      JSON.stringify({
        id: `delta-bad-${Date.now()}`,
        uid: 'CPF-DELTA-3',
        evt_type: 'DEPOSIT',
        ts: '2026-03-20T12:05:00Z',
        val: 'not-a-number',
        ccy: 'BRL',
      }),
    ].join('\n');

    const parseResponse = await request.post(`${API_URL}/ingest/connectors/delta/parse`, {
      headers,
      multipart: {
        entity_type: 'TRANSACTION',
        mapping_config_id: mapping.id,
        file: {
          name: `delta-${Date.now()}.ndjson`,
          mimeType: 'application/x-ndjson',
          buffer: Buffer.from(ndjsonPayload, 'utf-8'),
        },
      },
    });

    expect(parseResponse.status()).toBe(202);
    const parseBody = await parseResponse.json() as {
      job_id: string;
      source_system: string;
      summary: { accepted: number; failed: number; total: number };
    };
    expect(parseBody.source_system).toBe('ConnectorDelta');
    expect(parseBody.summary.total).toBeGreaterThanOrEqual(3);
    expect(parseBody.summary.accepted).toBeGreaterThanOrEqual(1);
    expect(parseBody.summary.failed).toBeGreaterThanOrEqual(2);

    const jobResponse = await request.get(`${API_URL}/ingest/jobs/${parseBody.job_id}`, {
      headers,
    });
    expect(jobResponse.ok()).toBeTruthy();
    const job = await jobResponse.json() as {
      source_system: string;
      failed_records: number;
      error_count: number;
    };
    expect(job.source_system).toBe('ConnectorDelta');
    expect(job.failed_records).toBeGreaterThanOrEqual(2);
    expect(job.error_count).toBeGreaterThanOrEqual(2);
  });

  test('admin can parse ConnectorGamma with explicit MappingConfig version tracking', async ({ request }) => {
    const session = await apiLoginAsAdmin(request);
    const headers = authHeaders(session.access_token);

    const mappingResponse = await request.post(`${API_URL}/mappings`, {
      headers,
      data: {
        name: `Gamma explicit ${Date.now()}`,
        source_system: 'ConnectorGamma',
        entity_type: 'TRANSACTION',
        format: 'json',
        config_json: {
          source_system: 'ConnectorGamma',
          entity_type: 'TRANSACTION',
          fields: [
            { target: 'external_transaction_id', source: 'event_id', transform: 'copy' },
            { target: 'player_cpf', source: 'external_player_id', transform: 'copy' },
            { target: 'type', source: 'transaction_type', transform: 'copy' },
            { target: 'amount', source: 'amount', transform: 'coerceDecimal' },
            { target: 'occurred_at', source: 'occurred_at', transform: 'parseDate' },
          ],
        },
        change_notes: 'Explicit gamma mapping for e2e',
      },
    });
    expect(mappingResponse.ok()).toBeTruthy();
    const mapping = await mappingResponse.json() as { id: string };

    const xmlPayload = [
      '<transactions>',
      '  <transaction>',
      '    <id>gamma-map-1</id>',
      '    <player_id>CPF-GAMMA-MAP-1</player_id>',
      '    <type>DEPOSIT</type>',
      '    <amount>321.00</amount>',
      '    <currency>BRL</currency>',
      '    <timestamp>2026-03-20T10:00:00Z</timestamp>',
      '  </transaction>',
      '</transactions>',
    ].join('\n');

    const parseResponse = await request.post(`${API_URL}/ingest/connectors/gamma/parse`, {
      headers,
      multipart: {
        entity_type: 'TRANSACTION',
        mapping_config_id: mapping.id,
        file: {
          name: `gamma-explicit-${Date.now()}.xml`,
          mimeType: 'application/xml',
          buffer: Buffer.from(xmlPayload, 'utf-8'),
        },
      },
    });

    expect(parseResponse.status()).toBe(202);
    const parseBody = await parseResponse.json() as {
      job_id: string;
      mapping_config_id: string | null;
      mapping_version_id: string | null;
    };
    expect(parseBody.mapping_config_id).toBe(mapping.id);
    expect(parseBody.mapping_version_id).toBe(mapping.id);

    const jobResponse = await request.get(`${API_URL}/ingest/jobs/${parseBody.job_id}`, {
      headers,
    });
    expect(jobResponse.ok()).toBeTruthy();
    const job = await jobResponse.json() as {
      mapping_config_id: string | null;
      mapping_version_id: string | null;
    };
    expect(job.mapping_config_id).toBe(mapping.id);
    expect(job.mapping_version_id).toBe(mapping.id);
  });
});
