import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-20T00:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'infra-e2e', timestamp: now } });
const user = { id: 1, username: 'admin', real_name: '系统管理员', status: 'active', roles: [{ id: 1, code: 'super_admin', name: '系统管理员', is_system: true }], created_at: now, updated_at: now };

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

async function mockConsole(page: Page) {
  const storage = {
    status: 'ok', checked_at: now,
    database: { status: 'ok', engine: 'mysql', database: 'dwg_agent', latency_ms: 2, table_count: 45, pool: { size: 2, max_overflow: 2, recycle_seconds: 3600 } },
    storage: { status: 'ok', backend: 'minio', latency_ms: 3, buckets: [{ name: 'dwg-original', tracked_files: 3, object_count: 3 }, { name: 'dxf-derived', tracked_files: 2, object_count: 2 }] },
    catalog: { available_files: 5, tracked_bytes: 1024, extensions: { '.dwg': 3 } }, capacity: { status: 'unknown', disk_total_bytes: null, disk_used_bytes: null, disk_free_bytes: null }, recovery: { consistency_rule: 'MySQL metadata and object storage must be backed up and restored as one recovery set.', automated_backup: false },
  };
  const dataOverview = { status: 'ok', environment: { app_env: 'production', database_engine: 'mysql', database: 'dwg_agent', storage_backend: 'minio' }, catalog: { available_files: 5, deleted_files: 0, tracked_bytes: 1024 }, transfers_today: { inbound_succeeded: 2, outbound_succeeded: 1, attention_required: 0 }, latest_scan: null };
  const control = { checked_at: now, broker: { kind: 'mysql_sqlalchemy', url_scheme: 'sqla+mysql', ready_count_source: 'kombu_message.visible', limitations: ['ready counts exclude reserved or in-flight tasks'] }, queues: [{ name: 'maintenance', business_jobs: { queued: 0, running: 0, failed: 0 }, broker_ready_messages: 0, mode: 'contract_only' }], workers: [{ id: 1, worker_name: 'maintenance@host', hostname: 'host', process_id: 10, queues: ['maintenance'], concurrency: 1, status: 'online', started_at: now, last_seen_at: now, stopped_at: null }], summary: { registered_workers: 1, online_workers: 1, stale_workers: 0, unread_messages: 0 }, implementation: { rabbitmq: 'pending', celery_beat: 'pending', durable_outbox: 'pending', windows_node_agent: 'pending' } };
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'infra-token', user }, 201));
  await page.route('**/api/v1/data-admin/overview', (route) => json(route, dataOverview));
  await page.route('**/api/v1/system/infrastructure', (route) => json(route, storage));
  await page.route('**/api/v1/control-plane/overview', (route) => json(route, control));
  await page.route('**/api/v1/control-plane/events?**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...envelope([]), pagination: { page: 1, page_size: 20, total: 0 } }) }));
  await page.route('**/api/v1/control-plane/messages?**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...envelope([]), pagination: { page: 1, page_size: 20, total: 0 } }) }));
  await page.route('**/api/v1/control-plane/contracts/windows-node-agent', (route) => json(route, { version: 'v1-draft', status: 'pending', transport: 'HTTPS draft', endpoints: [], not_available: ['lease fencing'] }));
  await page.route('**/api/v1/control-plane/maintenance/reconcile-stale-jobs', (route) => json(route, { operation: 'reconcile_stale_jobs', queue: 'maintenance', task_id: 'maintenance-task-001' }, 202));
}

test('infrastructure console shows actual storage state and queues bounded maintenance recovery', async ({ page }) => {
  await mockConsole(page);
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => { sessionStorage.setItem('dwg_access_token', token); sessionStorage.setItem('dwg_user', JSON.stringify(savedUser)); }, { token: 'infra-token', savedUser: user });
  await page.goto('/admin/infrastructure');
  await expect(page.getByText('MinIO（生产对象存储）')).toBeVisible();
  await expect(page.getByText('Bucket 对账：当前可见对象数与 MySQL 可用登记一致。')).toBeVisible();
  await expect(page.getByText('dwg-original')).toBeVisible();
  await page.getByRole('tab', { name: '运行与通信' }).click();
  await expect(page.getByText('当前运行边界：MySQL SQLAlchemy 队列')).toBeVisible();
  await expect(page.getByText('Windows Node Agent 合同（待实现）')).toBeVisible();
  await page.getByRole('button', { name: '恢复超时运行任务' }).click();
  await expect(page.getByText(/已提交维护任务/)).toBeVisible();
});
