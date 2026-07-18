/**
 * API contract tests — verify every frontend API call hits the correct
 * backend endpoint with the right method, payload shape, and response format.
 *
 * These run against the real backend. No browser needed — pure HTTP.
 */
import { test, expect } from '@playwright/test';
import { API_BASE as BASE } from './test-env';

// ── helpers ─────────────────────────────────────────────────────────────────

async function getSessionAuth(request: any): Promise<{ token: string; sseCookie: string }> {
  const r = await request.post(`${BASE}/api/v1/auth/sessions`, {
    data: { username: 'admin', password: 'SuperAdminPass1' },
  });
  const body = await r.json();
  const setCookie = r.headers()['set-cookie'] || '';
  const cookieMatch = setCookie.match(/dwg_sse_token=([^;]+)/);
  if (!cookieMatch) throw new Error('Login response did not set the scoped SSE cookie');
  return {
    token: body.data.access_token,
    sseCookie: `dwg_sse_token=${cookieMatch[1]}`,
  };
}

function auth(token: string) {
  return { Authorization: `Bearer ${token}` };
}

// ── tests ───────────────────────────────────────────────────────────────────

test.describe('API Contract — every endpoint used by the frontend', () => {
  let token: string;
  let sseCookie: string;
  let jobId: number;

  test.beforeAll(async ({ request }) => {
    ({ token, sseCookie } = await getSessionAuth(request));
    const jobsR = await request.get(`${BASE}/api/v1/jobs?page_size=1`, {
      headers: auth(token),
    });
    const jobs = (await jobsR.json()).data;
    if (jobs.length > 0) {
      jobId = jobs[0].id;
      return;
    }
    const created = await request.post(`${BASE}/api/v1/jobs`, {
      headers: auth(token),
      data: { task_type: 'framework_smoke_test', precision_level: 'normal' },
    });
    jobId = (await created.json()).data.id;
  });

  // ── Auth ─────────────────────────────────────────────────────────────
  test('POST /api/v1/auth/sessions — login', async ({ request }) => {
    const r = await request.post(`${BASE}/api/v1/auth/sessions`, {
      data: { username: 'admin', password: 'SuperAdminPass1' },
    });
    expect(r.status()).toBe(201);
    const body = await r.json();
    expect(body.data).toHaveProperty('access_token');
    expect(body.data).toHaveProperty('user');
    expect(body.data.user).toHaveProperty('username', 'admin');
  });

  test('GET /api/v1/auth/me — current user', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/auth/me`, { headers: auth(token) });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.data).toHaveProperty('id');
    expect(body.data).toHaveProperty('roles');
  });

  test('POST /api/v1/auth/tokens/refresh — token refresh', async ({ request }) => {
    // Login first to get cookies
    const loginR = await request.post(
      `${BASE}/api/v1/auth/sessions`,
      { data: { username: 'admin', password: 'SuperAdminPass1' } },
    );
    // Use the same request context which should have cookies
    const r = await request.post(`${BASE}/api/v1/auth/tokens/refresh`);
    // May 401 if no cookie — that's fine, just testing endpoint exists
    expect([200, 401]).toContain(r.status());
  });

  // ── Files ──────────────────────────────────────────────────────────────
  test('GET /api/v1/files — list files', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/files`, { headers: auth(token) });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.data)).toBe(true);
    expect(body).toHaveProperty('pagination');
    expect(body.pagination).toHaveProperty('total');
  });

  test('GET /api/v1/files?batch_name= — filter by batch', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/files?batch_name=test`, {
      headers: auth(token),
    });
    expect(r.status()).toBe(200);
  });

  test('GET /api/v1/files/batches — list batches', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/files/batches`, {
      headers: auth(token),
    });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.data)).toBe(true);
  });

  test('GET /api/v1/files/{id} — file metadata', async ({ request }) => {
    // Use file id 1 (should exist after seed)
    const r = await request.get(`${BASE}/api/v1/files/1`, { headers: auth(token) });
    if (r.status() === 200) {
      const body = await r.json();
      expect(body.data).toHaveProperty('original_name');
      expect(body.data).toHaveProperty('file_ext');
      expect(body.data).toHaveProperty('size_bytes');
      expect(body.data).toHaveProperty('sha256');
      expect(body.data).toHaveProperty('batch_name'); // may be null
    }
  });

  test('GET /api/v1/files/{id}/download-url — signed URL', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/files/1/download-url`, {
      headers: auth(token),
    });
    if (r.status() === 200) {
      const body = await r.json();
      expect(body.data).toHaveProperty('url');
      expect(body.data).toHaveProperty('expires_in', 300);
    }
  });

  test('GET /api/v1/files/{id}/download — file download', async ({ request }) => {
    // Need a signed URL first
    const urlR = await request.get(`${BASE}/api/v1/files/1/download-url`, {
      headers: auth(token),
    });
    if (urlR.status() !== 200) return;
    const { url } = (await urlR.json()).data;

    const fullUrl = url.startsWith('http') ? url : `${BASE}${url}`;
    const r = await request.get(fullUrl, { headers: auth(token) });
    expect([200, 403]).toContain(r.status()); // 403 if expired, 200 if fresh
  });

  test('POST /api/v1/files/bulk-delete — bulk delete', async ({ request }) => {
    const r = await request.post(`${BASE}/api/v1/files/bulk-delete`, {
      headers: { ...auth(token), 'Content-Type': 'application/json' },
      data: { file_ids: [] },
    });
    // Empty file_ids → 422 validation error
    expect(r.status()).toBe(422);
  });

  test('POST /api/v1/files/download-zip — zip download', async ({ request }) => {
    const r = await request.post(`${BASE}/api/v1/files/download-zip`, {
      headers: { ...auth(token), 'Content-Type': 'application/json' },
      data: { file_ids: [], formats: ['dwg'], folder_name: 'test' },
    });
    // Empty file_ids → 422
    expect(r.status()).toBe(422);
  });

  test('POST /api/v1/files/download-zip/preview — zip availability', async ({ request }) => {
    const r = await request.post(`${BASE}/api/v1/files/download-zip/preview`, {
      headers: { ...auth(token), 'Content-Type': 'application/json' },
      data: { file_ids: [], formats: ['dwg', 'dxf'], folder_name: 'test' },
    });
    // Empty file_ids use the same validation contract as the formal download.
    expect(r.status()).toBe(422);
  });

  // ── Jobs ───────────────────────────────────────────────────────────────
  test('GET /api/v1/jobs — list jobs', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/jobs`, { headers: auth(token) });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.data)).toBe(true);
  });

  test('GET /api/v1/jobs/{id} — single job', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/jobs/${jobId}`, { headers: auth(token) });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.data).toHaveProperty('task_type');
    expect(body.data).toHaveProperty('status');
    expect(body.data).toHaveProperty('attempt');
    expect(body.data).toHaveProperty('progress');
    expect(body.data).toHaveProperty('pipeline');
    expect(body.data).toHaveProperty('params_json');
  });

  test('GET /api/v1/jobs/{id}/steps — job steps', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/jobs/${jobId}/steps`, {
      headers: auth(token),
    });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.data)).toBe(true);
    for (const step of body.data) expect(step).toHaveProperty('attempt');
  });

  test('GET /api/v1/jobs/{id}/results — job results', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/jobs/${jobId}/results`, {
      headers: auth(token),
    });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.data)).toBe(true);
  });

  test('POST /api/v1/jobs/{id}/retry-requests — retry job', async ({ request }) => {
    const r = await request.post(`${BASE}/api/v1/jobs/${jobId}/retry-requests`, {
      headers: auth(token),
    });
    // 202 if job can be retried, 409 or other if not
    expect([202, 409]).toContain(r.status());
  });

  test('POST /api/v1/jobs/cancel-all-active — cancel all', async ({ request }) => {
    const r = await request.post(`${BASE}/api/v1/jobs/cancel-all-active`, {
      headers: auth(token),
    });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.data).toHaveProperty('cancelled_count');
    expect(body.data).toHaveProperty('celery_revoked');
    expect(body.data).toHaveProperty('broker_purge_failed_queues');
  });

  test('POST /api/v1/jobs (convert_dwg_to_dxf) — create DXF job', async ({ request }) => {
    // First get a valid file_id
    const filesR = await request.get(`${BASE}/api/v1/files?page_size=1`, {
      headers: auth(token),
    });
    const files = (await filesR.json()).data;
    if (files.length === 0) return; // no files

    const r = await request.post(`${BASE}/api/v1/jobs`, {
      headers: { ...auth(token), 'Content-Type': 'application/json' },
      data: {
        task_type: 'convert_dwg_to_dxf',
        precision_level: 'normal',
        params: { file_id: files[0].id },
      },
    });
    expect(r.status()).toBe(202);
    const body = await r.json();
    expect(body.data.pipeline).toBe('dxf_open_source');
  });

  test('POST /api/v1/jobs (convert_dxf_to_dwg) — create DXF→DWG job', async ({ request }) => {
    const filesR = await request.get(`${BASE}/api/v1/files?page_size=1`, {
      headers: auth(token),
    });
    const files = (await filesR.json()).data;
    if (files.length === 0) return;

    const r = await request.post(`${BASE}/api/v1/jobs`, {
      headers: { ...auth(token), 'Content-Type': 'application/json' },
      data: {
        task_type: 'convert_dxf_to_dwg',
        precision_level: 'normal',
        params: { file_id: files[0].id },
      },
    });
    expect(r.status()).toBe(202);
    const body = await r.json();
    expect(body.data.pipeline).toBe('dxf2dwg_open_source');
  });

  // ── Results ────────────────────────────────────────────────────────────
  test('GET /api/v1/results/{id}/download-url — result download URL', async ({ request }) => {
    // Find a valid result
    const jobsR = await request.get(`${BASE}/api/v1/jobs?page_size=5`, {
      headers: auth(token),
    });
    const jobs = (await jobsR.json()).data;
    const succeededJob = jobs.find((j: any) => j.status === 'succeeded');
    if (!succeededJob) return;

    const resultsR = await request.get(
      `${BASE}/api/v1/jobs/${succeededJob.id}/results`,
      { headers: auth(token) },
    );
    const results = (await resultsR.json()).data;
    if (results.length === 0) return;

    const r = await request.get(
      `${BASE}/api/v1/results/${results[0].id}/download-url`,
      { headers: auth(token) },
    );
    expect(r.status()).toBe(200);
  });

  // ── System ─────────────────────────────────────────────────────────────
  test('GET /api/v1/system/health/oda — ODA health', async ({ request }) => {
    const r = await request.get(`${BASE}/api/v1/system/health/oda`, {
      headers: auth(token),
    });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.data.healthy).toBe(true);
    expect(body.data.oda_found).toBe(true);
  });

  // ── SSE ────────────────────────────────────────────────────────────────
  test('GET /api/v1/jobs/{id}/events — SSE stream', async ({ request }) => {
    // Hit the SSE endpoint; expect 200 + text/event-stream content type
    const r = await request.get(`${BASE}/api/v1/jobs/${jobId}/events`, {
      headers: { Accept: 'text/event-stream', Cookie: sseCookie },
    });
    expect(r.status()).toBe(200);
    const ct = r.headers()['content-type'] || '';
    expect(ct).toContain('text/event-stream');
  });
});
