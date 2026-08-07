# 前端下载功能完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完善前端下载：DXF 分类明细支持单文件下载，大体积下载支持取消，并修复"无进度像卡死 / 重复下载 / 关闭后仍下载"三类体验问题。

**Architecture:** 后端仅新增一个分类单文件下载端点（生产 workflow 文件被 `WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED` 限制无法走 `downloadFile`，必须走归档路径）；前端扩展共享下载基建（`downloadBlob` 支持 AbortSignal + `preparing` 状态、新增 `useDownload` hook），再逐组件接入取消/中断/并发约束。

**Tech Stack:** FastAPI + SQLAlchemy（后端）；React 19 + antd 6 + TanStack Query + axios（前端）；pytest（后端测试）；Playwright（前端 e2e）。

## Global Constraints

- 后端 `MAX_SOURCE_LINES` 不适用，但前端 `scripts/check-architecture.mjs` 强制 `MAX_SOURCE_LINES = 600`，且 `shared` 不得依赖 feature，跨 feature 导入必须经 `index.ts`。
- 新增前端 hook 必须放 `src/shared/api/useDownload.ts`（`src/hooks` 是已废弃的 legacy 目录）。
- `downloadBlob` 取消时**不得**把 axios `CanceledError` 包装成普通 `Error`——必须原样抛出让组件用 `isDownloadCancelled` 识别。
- 生产 workflow 文件只走归档端点；`require_file_read_access` 是基础读权限（不含 `WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED`），单文件端点用它做项目成员权威校验。
- 不改动现有下载/流转/审计逻辑；不改 API 路径语义（仅新增端点）。
- 后端测试用 `tests.support.workflow_api` 的 `client()`/`admin_headers()`；存储用 `LocalFileStorage` + `monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", ...)`。

---

### Task 1: 后端分类单文件下载端点（TDD）

**Files:**
- Test: `backend/tests/workflows/test_workflow_classification_file_download.py`（新建）
- Modify: `backend/app/modules/workflows/routes/classification.py`

**Interfaces:**
- Consumes: `latest_classification_run` / `sync_workflow_from_jobs` / `load_workflow_detail` / `require_project_member` / `require_file_read_access`（均已存在于 classification.py 或其依赖中）
- Produces: 新端点 `GET /{workflow_id}/dxf-classification/groups/{group_key}/files/{output_name}/download`，返回单文件流式响应；Task 5 的前端 API 函数调用它。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/workflows/test_workflow_classification_file_download.py`：

```python
"""HTTP integration tests for single-file DXF classification download."""

from __future__ import annotations

import hashlib
import zipfile  # noqa: F401  (kept for parity with sibling download tests)
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from app.modules.dxf_classification.interface import (
    DxfClassificationItem,
    DxfClassificationRun,
)
from app.modules.files.interface import FileTransfer, StoredFile
from app.modules.jobs.interface import Job
from app.modules.workflows.models import WorkflowRun
from app.platform.storage.local import LocalFileStorage
from tests.support import workflow_api as workflow_test_api
from tests.support.database import open_test_session


def _register_object(db, storage, *, owner_id, name, payload):
    row = StoredFile(
        bucket="classification-file-test",
        storage_key=f"objects/{uuid4().hex}/{name}",
        original_name=name,
        file_ext=Path(name).suffix.lower(),
        content_type="application/octet-stream",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        uploaded_by=owner_id,
        status="available",
    )
    db.add(row)
    db.flush()
    storage.put_fileobj(
        row.bucket, row.storage_key, BytesIO(payload), length=len(payload),
        content_type=row.content_type,
    )
    return row


def _seed_run(db, storage, *, workflow, group_key="type:BH",
              output_name="A_拆板前.dxf", output_ext=".dxf", output_payload=b"dxf bytes"):
    output = _register_object(
        db, storage, owner_id=workflow.created_by,
        name=f"{output_name if output_ext == '.dxf' else 'A_拆板前'}{output_ext}",
        payload=output_payload,
    )
    job = Job(
        project_id=workflow.project_id, created_by=workflow.created_by,
        task_type="classify_steel_dxf", pipeline="steel_dxf_classifier",
        status="succeeded", attempt=1, progress=100,
        precision_level="normal", params_json={},
    )
    db.add(job)
    db.flush()
    run = DxfClassificationRun(
        workflow_run_id=workflow.id, project_id=workflow.project_id,
        job_id=job.id, job_attempt=1, status="completed",
        classifier_version="1.2.0", report_schema="STEEL-DXF-CLASSIFICATION-1.2",
        cli_schema="STEEL-DXF-CLI-1.2", project_name="fixture-project",
        input_manifest_sha256="f" * 64, input_count=1, classified_count=1,
        review_required_count=0, unreadable_count=0, type_counts_json={"BH": 1},
    )
    db.add(run)
    db.flush()
    db.add(DxfClassificationItem(
        run=run, source_file_id=output.id, output_file_id=output.id,
        source_name=output.original_name, output_name=output.original_name,
        output_directory="fixture_BH_dxf", disposition="classified",
        part_type="BH", profile_raw="BH500*300*12*20",
        profile_normalized="BH500*300*12*20", type_source="catalog",
        group_key=group_key, next_stage_eligible=True,
        diagnostics_json=[], evidence_json={},
    ))
    db.flush()
    return run, output


def _setup(client, storage, *, seed=True):
    admin_headers = workflow_test_api.admin_headers(client)
    response = client.post(
        "/api/v1/workflows/production-projects",
        headers=admin_headers,
        json={"code": f"CLS-{uuid4().hex[:6]}", "name": "单文件下载测试项目"},
    )
    assert response.status_code == 201, response.text
    workflow_id = response.json()["data"]["workflow"]["id"]
    output_id = None
    if seed:
        with open_test_session() as db:
            workflow = db.get(WorkflowRun, workflow_id)
            assert workflow is not None
            _, output = _seed_run(db, storage, workflow=workflow)
            output_id = output.id
    return admin_headers, workflow_id, output_id


def _single_file_url(workflow_id, group_key="type:BH", output_name="A_拆板前.dxf"):
    return (
        f"/api/v1/workflows/{workflow_id}/dxf-classification/groups/"
        f"{quote(group_key, safe='')}/files/{quote(output_name, safe='')}/download"
    )


def test_single_file_download_streams_exact_bytes_with_headers(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    with workflow_test_api.client() as client:
        headers, workflow_id, output_id = _setup(client, storage)
        response = client.get(_single_file_url(workflow_id), headers=headers)
        assert response.status_code == 200, response.text
        assert response.content == b"dxf bytes"
        assert "attachment" in response.headers["content-disposition"]
        assert "A_%E6%8B%86%E6%9D%BF%E5%89%8D.dxf" in response.headers["content-disposition"]
        with open_test_session() as db:
            transfer = db.scalar(
                select(FileTransfer).where(
                    FileTransfer.operation == "dxf_class_single_file",
                    FileTransfer.file_id == output_id,
                )
            )
            assert transfer is not None and transfer.status == "succeeded"


def test_single_file_download_404_when_run_missing(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    with workflow_test_api.client() as client:
        headers, workflow_id, _ = _setup(client, storage, seed=False)
        response = client.get(_single_file_url(workflow_id), headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CLASSIFICATION_RUN_NOT_FOUND"


def test_single_file_download_404_when_item_not_matching(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    with workflow_test_api.client() as client:
        headers, workflow_id, _ = _setup(client, storage)
        response = client.get(
            _single_file_url(workflow_id, output_name="UNKNOWN.dxf"), headers=headers
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CLASSIFICATION_FILE_NOT_FOUND"


def test_single_file_download_409_when_output_is_not_dxf(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    with workflow_test_api.client() as client:
        headers, workflow_id, _ = _setup(client, storage)
        with open_test_session() as db:
            workflow = db.get(WorkflowRun, workflow_id)
            _seed_run(db, storage, workflow=workflow, output_name="A_拆板前.dwg", output_ext=".dwg")
        response = client.get(
            _single_file_url(workflow_id, output_name="A_拆板前.dwg"), headers=headers
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CLASSIFICATION_OUTPUT_MISSING"


def test_single_file_download_403_for_non_member(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    with workflow_test_api.client() as client:
        admin_headers, workflow_id, _ = _setup(client, storage)
        _, engineer_headers = workflow_test_api.create_engineer_user(client, admin_headers)
        response = client.get(_single_file_url(workflow_id), headers=engineer_headers)
        assert response.status_code == 403
```

注意 `select` 需要从 `sqlalchemy` 导入——把 `from sqlalchemy import select` 加进测试文件顶部。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/workflows/test_workflow_classification_file_download.py -v`
Expected: 全部 FAIL，路由不存在返回 404（FastAPI 默认）/ 或 import 失败。

- [ ] **Step 3: 实现端点**

在 `backend/app/modules/workflows/routes/classification.py` 追加 import 与端点。新增 import：

```python
from urllib.parse import quote

from app.modules.files.exports import download_headers
from app.modules.files.storage_transactions import (
    TransferSpec,
    prepare_transfer_in_transaction,
    session_factory_for,
    settle_stream,
)
from app.modules.operations.audit.interface import write_audit_log
from app.platform.storage import factory as storage_factory
from app.platform.storage.base import StorageError, StorageObjectNotFound
```

在文件末尾（`download_all_dxf_classification_archive` 之后）追加：

```python
@router.get(
    "/{workflow_id}/dxf-classification/groups/{group_key}/files/{output_name}/download",
    summary="下载分类组内单个 DXF 文件",
    response_class=StreamingResponse,
    description=(
        "按分类文件夹和输出文件名下载单个正式 DXF；不经过归档 ZIP，"
        "也不暴露内部文件标识。生产 workflow 文件必须经此类归档路径下载。"
    ),
)
def download_dxf_classification_single_file(
    workflow_id: int,
    group_key: str,
    output_name: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    sync_workflow_from_jobs(db, workflow)
    run = latest_classification_run(db, workflow.id)
    db.commit()
    if run is None:
        raise AppHTTPException(
            404,
            "CLASSIFICATION_RUN_NOT_FOUND",
            "No DXF classification run exists for this workflow.",
        )
    item = next(
        (row for row in run.items
         if row.group_key == group_key and row.output_name == output_name),
        None,
    )
    if item is None:
        raise AppHTTPException(
            404,
            "CLASSIFICATION_FILE_NOT_FOUND",
            "The DXF classification file was not found.",
            {"group_key": group_key},
        )
    stored = db.get(StoredFile, item.output_file_id)
    if (
        stored is None
        or stored.status == "deleted"
        or stored.file_ext.lower() != ".dxf"
    ):
        raise AppHTTPException(
            409,
            "CLASSIFICATION_OUTPUT_MISSING",
            "A classified DXF output is unavailable.",
            {"group_key": group_key},
        )
    require_file_read_access(db, current_user, stored)

    storage = storage_factory.get_storage_backend()
    try:
        object_info = storage.stat_object(stored.bucket, stored.storage_key)
    except StorageObjectNotFound:
        raise not_found("StoredFileObject") from None
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "STORAGE_READ_FAILED",
            "Failed to read stored file object.",
        ) from exc

    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="outbound",
            operation="dxf_class_single_file",
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            idempotency_key=request.state.request_id,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            expected_bytes=object_info.size_bytes,
        ),
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="dxf_classification_files.download",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={
            "group_key": group_key,
            "output_name": output_name,
            "file_id": stored.id,
        },
        request=request,
    )
    db.commit()
    factory = session_factory_for(db)
    encoded_filename = quote(stored.original_name)
    return StreamingResponse(
        settle_stream(
            factory,
            transfer.transfer_uid,
            storage.iter_file(stored.bucket, stored.storage_key),
        ),
        media_type=stored.content_type or "application/octet-stream",
        headers={
            **download_headers(stored.original_name),
            "Content-Length": str(object_info.size_bytes),
        },
    )
```

同时确认 `classification.py` 已导入 `not_found`（`from app.platform.http.exceptions import AppHTTPException, not_found`）。若测试运行发现 `sync_workflow_from_jobs` 后 `run.items` 未加载，在 route 里加 `db.refresh(run)` 后再访问 `run.items`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/workflows/test_workflow_classification_file_download.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 运行相关回归并提交**

Run: `cd backend && python -m pytest tests/workflows/test_workflow_production.py tests/workflows/test_workflow_dxf_contracts.py -q`
Expected: 通过（确认新端点不破坏既有 workflow 契约）。
Commit:
```bash
git add backend/tests/workflows/test_workflow_classification_file_download.py backend/app/modules/workflows/routes/classification.py
git commit -m "feat(classification): 支持分类组内单 DXF 文件流式下载"
```

---

### Task 2: 共享基建 —— `downloadBlob` 支持 signal + `preparing` 状态

**Files:**
- Modify: `frontend/src/shared/api/transfer.ts`
- Modify: `frontend/src/shared/api/index.ts`

**Interfaces:**
- Consumes: 现有 `TransferProgress`/`initialTransferProgress`/`transferProgressFromAxios`/`completedTransferProgress`
- Produces: `downloadBlob` 新增可选参数 `signal?: AbortSignal`；`TransferProgress` 新增可选字段 `preparing?: boolean`；新增导出 `isDownloadCancelled(error: unknown): boolean`（纯函数，无 react 依赖）

- [ ] **Step 1: 修改 `transfer.ts`**

`TransferProgress` 接口增加 `preparing?: boolean;`（注释：服务器尚未返回首字节时为 true，仅下载用）。

新增纯函数（放在 `completedTransferProgress` 之后）：

```ts
export function isDownloadCancelled(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as {
    code?: string;
    name?: string;
    __CANCEL__?: boolean;
  };
  return candidate.code === 'ERR_CANCELED'
    || candidate.name === 'CanceledError'
    || candidate.__CANCEL__ === true;
}
```

`downloadBlob` 增加 `signal` 参数并透传；初始回调带 `preparing: true`；取消时不包装：

```ts
export async function downloadBlob({
  url,
  fallbackName,
  errorMessage,
  method = 'GET',
  data,
  expectedBytes,
  onProgress,
  timeout = 300_000,
  signal,
}: {
  url: string;
  fallbackName: string;
  errorMessage: string;
  method?: Method;
  data?: unknown;
  expectedBytes?: number;
  onProgress?: TransferProgressHandler;
  timeout?: number;
  signal?: AbortSignal;
}): Promise<void> {
  onProgress?.({ ...initialTransferProgress(expectedBytes), preparing: true });
  try {
    const response = await apiClient.request<Blob>({
      url,
      method,
      data,
      responseType: 'blob',
      timeout,
      signal,
      onDownloadProgress: (event) => {
        onProgress?.(transferProgressFromAxios(event, expectedBytes));
      },
    });
    const filename = responseFilename(response.headers, fallbackName);
    onProgress?.(completedTransferProgress(response.data.size, response.data.size));
    triggerBlobDownload(response.data, filename);
  } catch (error) {
    if (isDownloadCancelled(error)) throw error;
    throw new Error(await describeApiErrorAsync(error, errorMessage));
  }
}
```

- [ ] **Step 2: 更新 `shared/api/index.ts` 导出**

在 `shared/api/index.ts` 的 transfer 导出块中追加 `isDownloadCancelled`：

```ts
export {
  isDownloadCancelled,
} from './transfer';
```

（按该文件现有导出风格合并到同一块。）

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx tsc -b --pretty false`
Expected: 无错误。

- [ ] **Step 4: 提交**

Commit:
```bash
git add frontend/src/shared/api/transfer.ts frontend/src/shared/api/index.ts
git commit -m "feat(transfer): downloadBlob 支持取消信号与 preparing 状态"
```

---

### Task 3: 共享基建 —— `TransferProgressBar` 渲染 preparing 状态

**Files:**
- Modify: `frontend/src/shared/components/TransferProgressBar.tsx`

**Interfaces:**
- Consumes: `TransferProgress` 的 `preparing?: boolean`
- Produces: `preparing === true` 时渲染 Spin + "服务器正在生成，请稍候…"（不显示 0% 进度条）

- [ ] **Step 1: 修改组件**

```tsx
import { Progress, Space, Spin, Typography } from 'antd';
import { LoadingOutlined } from '@ant-design/icons';

import type { TransferProgress } from '../api/transfer';
import { fmtSize } from './ui';

export function TransferProgressBar({
  label,
  progress,
}: {
  label: string;
  progress: TransferProgress;
}) {
  if (progress.preparing) {
    return (
      <Space style={{ width: '100%', minWidth: 220 }}>
        <Spin indicator={<LoadingOutlined spin />} size="small" />
        <Typography.Text strong>{label}</Typography.Text>
        <Typography.Text type="secondary">服务器正在生成，请稍候…</Typography.Text>
      </Space>
    );
  }
  const total = progress.totalBytes;
  const detail = total
    ? `${fmtSize(progress.loadedBytes)} / ${progress.totalIsEstimated ? '约 ' : ''}${fmtSize(total)}`
    : `已传输 ${fmtSize(progress.loadedBytes)}`;
  return (
    <Space orientation="vertical" size={4} style={{ width: '100%', minWidth: 220 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Typography.Text strong>{label}</Typography.Text>
        <Typography.Text type="secondary">{detail}</Typography.Text>
      </Space>
      <Progress
        aria-label={`${label}进度`}
        percent={progress.percent ?? 0}
        status={progress.completed ? 'success' : 'active'}
        size="small"
        format={(percent) => progress.percent === undefined ? '传输中' : `${percent}%`}
      />
    </Space>
  );
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc -b --pretty false`
Expected: 无错误。

- [ ] **Step 3: 提交**

Commit:
```bash
git add frontend/src/shared/components/TransferProgressBar.tsx
git commit -m "feat(components): TransferProgressBar 展示服务器生成中状态"
```

---

### Task 4: 共享基建 —— `useDownload` hook

**Files:**
- Create: `frontend/src/shared/api/useDownload.ts`
- Modify: `frontend/src/shared/api/index.ts`

**Interfaces:**
- Consumes: `isDownloadCancelled`（来自 `./transfer`）
- Produces: `useDownload()` 返回 `{ active, start, finish, cancel, signal }`；`signal` 为 `AbortSignal | undefined`

```ts
import { useCallback, useEffect, useRef, useState } from 'react';

export interface DownloadControl {
  active: boolean;
  start: () => AbortSignal | undefined;
  finish: () => void;
  cancel: () => void;
  signal: () => AbortSignal | undefined;
}

export function useDownload(): DownloadControl {
  const controllerRef = useRef<AbortController | null>(null);
  const [active, setActive] = useState(false);

  const start = useCallback(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setActive(true);
    return controller.signal;
  }, []);

  const finish = useCallback(() => {
    controllerRef.current = null;
    setActive(false);
  }, []);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setActive(false);
  }, []);

  const signal = useCallback(() => controllerRef.current?.signal, []);

  useEffect(() => () => {
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  return { active, start, finish, cancel, signal };
}
```

- [ ] **Step 1: 创建 `useDownload.ts`**（内容如上）

- [ ] **Step 2: 更新 `shared/api/index.ts` 导出**

追加：
```ts
export { isDownloadCancelled, useDownload } from './transfer';
```
若 `useDownload` 从独立文件导出，则：
```ts
export { useDownload, type DownloadControl } from './useDownload';
export { isDownloadCancelled } from './transfer';
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx tsc -b --pretty false`
Expected: 无错误。

- [ ] **Step 4: 提交**

Commit:
```bash
git add frontend/src/shared/api/useDownload.ts frontend/src/shared/api/index.ts
git commit -m "feat(api): 新增 useDownload 下载取消 hook"
```

---

### Task 5: workflows.api —— 新增单文件下载 + 下载函数透传 signal

**Files:**
- Modify: `frontend/src/features/workflows/workflows.api.ts`

**Interfaces:**
- Consumes: `downloadBlob`（来自 shared/api）、Task 1 的新端点
- Produces: `downloadDxfClassificationFile(workflowId, groupKey, outputName, onProgress?, signal?)`；`downloadArchive` 增加 `signal?` 参数

- [ ] **Step 1: 修改 `downloadArchive`**

```ts
async function downloadArchive(
  url: string,
  fallbackName: string,
  errorMessage: string,
  onProgress?: TransferProgressHandler,
  expectedBytes?: number,
  signal?: AbortSignal,
) {
  return downloadBlob({
    url,
    fallbackName,
    errorMessage,
    onProgress,
    expectedBytes,
    signal,
  });
}
```

- [ ] **Step 2: 给全部既有下载函数追加 `signal?: AbortSignal` 参数并透传**

对 `downloadWorkflowArchive`、`downloadWorkflowStageArchive`、`downloadWorkflowExcelStageResult`、`downloadWorkflowExcelStage2Result`、`downloadWorkflowExcelStage2ReaderResult`、`downloadDxfClassificationGroupArchive`、`downloadAllDxfClassificationArchive`、`downloadDrawingSelectiveExport`、`downloadWorkflowBatchExport`、`downloadWorkflowRetentionExport`，在签名末尾加 `signal?: AbortSignal` 并传给 `downloadArchive(...)` 的最后一个参数。例如：

```ts
export async function downloadAllDxfClassificationArchive(
  workflowId: number,
  onProgress?: TransferProgressHandler,
  expectedBytes?: number,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/dxf-classification/download-archive`,
    `workflow-${workflowId}-all-classified-dxf.zip`,
    '全部 DXF 下载失败',
    onProgress,
    expectedBytes,
    signal,
  );
}
```

`downloadDrawingSelectiveExport`、`downloadWorkflowBatchExport`、`downloadWorkflowRetentionExport` 接受 `prepared`/`exportRow` 对象，signal 也追加在参数末尾。

- [ ] **Step 3: 新增单文件下载函数**

```ts
export async function downloadDxfClassificationFile(
  workflowId: number,
  groupKey: string,
  outputName: string,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/dxf-classification/groups/${encodeURIComponent(groupKey)}/files/${encodeURIComponent(outputName)}/download`,
    outputName,
    '分类 DXF 下载失败',
    onProgress,
    undefined,
    signal,
  );
}
```

- [ ] **Step 4: 类型检查**

Run: `cd frontend && npx tsc -b --pretty false`
Expected: 无错误。

- [ ] **Step 5: 提交**

Commit:
```bash
git add frontend/src/features/workflows/workflows.api.ts
git commit -m "feat(workflows.api): 下载函数支持 signal，新增分类单文件下载"
```

---

### Task 6: DxfClassificationPanel —— 单文件下载 + 取消 + 并发约束

**Files:**
- Modify: `frontend/src/features/workflows/DxfClassificationPanel.tsx`

**Interfaces:**
- Consumes: `useDownload`、`isDownloadCancelled`、`downloadDxfClassificationFile`
- Produces: 明细抽屉表格新增"下载"操作列；全部/整组/单文件下载共享一个 `useDownload`（同一时间仅一个下载）；下载进行中显示"取消下载"；unmount 自动中断

- [ ] **Step 1: import 与 hook**

```ts
import { downloadAllDxfClassificationArchive, downloadDxfClassificationFile, downloadDxfClassificationGroupArchive } from './workflows.api';
import { describeApiError, isDownloadCancelled, operatorErrorMessage, type TransferProgress } from '../../shared/api';
import { useDownload } from '../../shared/api/useDownload';
```

组件内加：
```ts
const downloadCtrl = useDownload();
```

- [ ] **Step 2: 改造三个下载 mutation**

`allDownload`：
```ts
const allDownload = useMutation({
  mutationFn: () => downloadAllDxfClassificationArchive(
    workflowId,
    (progress) => setDownloadProgress({ label: '全部分类图纸下载', progress }),
    run?.groups.reduce((total, group) => total + group.total_size_bytes, 0),
    downloadCtrl.start(),
  ),
  onSuccess: () => downloadCtrl.finish(),
  onError: (error) => {
    downloadCtrl.finish();
    if (isDownloadCancelled(error)) {
      message.info('下载已取消');
    } else {
      message.error(describeApiError(error, '全部 DXF 下载失败'));
    }
  },
});
```

`groupDownload` 同理（`downloadCtrl.start()` 传给 `downloadDxfClassificationGroupArchive` 的 signal 参数，`onSuccess` 调 `downloadCtrl.finish()`，`onError` 用 `isDownloadCancelled` 分支）。

新增单文件下载 mutation：
```ts
const singleFileDownload = useMutation({
  mutationFn: ({ groupKey, outputName }: { groupKey: string; outputName: string }) => (
    downloadDxfClassificationFile(
      workflowId,
      groupKey,
      outputName,
      (progress) => setDownloadProgress({ label: `下载 ${outputName}`, progress }),
      downloadCtrl.start(),
    )
  ),
  onSuccess: (_data, vars) => message.success(`已下载 ${vars.outputName}`),
  onError: (error) => {
    if (isDownloadCancelled(error)) {
      message.info('下载已取消');
    } else {
      message.error(describeApiError(error, '分类 DXF 下载失败'));
    }
  },
  onSettled: () => downloadCtrl.finish(),
});
```

- [ ] **Step 3: 下载按钮加并发约束**

`全部 DXF` 按钮：`disabled={downloadCtrl.active && !allDownload.isPending}`。
`下载本类` 按钮（主面板 + 抽屉 extra）：`disabled={downloadCtrl.active && !(groupDownload.isPending && groupDownload.variables?.group_key === group.group_key)}`。

- [ ] **Step 4: 明细表新增"下载"列**

在 `detailColumns` 数组末尾追加：
```ts
{
  title: '操作',
  key: 'actions',
  width: 110,
  render: (_: unknown, item: DxfClassificationGroupItem) => (
    <Button
      type="text"
      size="small"
      icon={<DownloadOutlined />}
      aria-label={`下载 ${item.output_name}`}
      loading={
        singleFileDownload.isPending
        && singleFileDownload.variables?.output_name === item.output_name
      }
      disabled={downloadCtrl.active
        && !(singleFileDownload.isPending
          && singleFileDownload.variables?.output_name === item.output_name)}
      onClick={() => singleFileDownload.mutate({
        groupKey: selectedGroupKey!,
        outputName: item.output_name,
      })}
    >
      下载
    </Button>
  ),
}
```

`selectedGroupKey` 已存在于组件（`selectedGroupKey!` 安全，因为表只在抽屉打开时渲染）。

- [ ] **Step 5: 显示取消按钮**

在 `downloadProgress` 渲染处（`{downloadProgress && (<TransferProgressBar ... />)}`）改成：
```tsx
{downloadProgress && (
  <Space wrap>
    <TransferProgressBar label={downloadProgress.label} progress={downloadProgress.progress} />
    {downloadCtrl.active && (
      <Button
        size="small"
        icon={<StopOutlined />}
        onClick={() => { downloadCtrl.cancel(); setDownloadProgress(null); }}
      >
        取消下载
      </Button>
    )}
  </Space>
)}
```

需要 `import { StopOutlined } from '@ant-design/icons';`。

- [ ] **Step 6: 类型检查 + 架构检查**

Run: `cd frontend && npx tsc -b --pretty false && node scripts/check-architecture.mjs`
Expected: 无错误；文件行数 < 600。

- [ ] **Step 7: 提交**

Commit:
```bash
git add frontend/src/features/workflows/DxfClassificationPanel.tsx
git commit -m "feat(classification): 明细单文件下载 + 下载取消与并发约束"
```

---

### Task 7: WorkflowRetentionControl —— 取消 + 关闭即中断

**Files:**
- Modify: `frontend/src/features/workflows/WorkflowRetentionControl.tsx`

**Interfaces:**
- Consumes: `useDownload`、`isDownloadCancelled`
- Produces: 完整备份下载进行中显示"取消下载"；Modal 可关闭并自动中断下载

- [ ] **Step 1: import 与 hook**

```ts
import { isDownloadCancelled, useDownload } from '../../shared/api';
```

组件内：
```ts
const downloadCtrl = useDownload();
```

- [ ] **Step 2: 改造 `downloadM`**

```ts
const downloadM = useMutation({
  mutationFn: (row: WorkflowRetentionExport) => (
    downloadWorkflowRetentionExport(row, setDownloadProgress, downloadCtrl.start())
  ),
  onSuccess: () => {
    downloadCtrl.finish();
    message.success('完整备份已下载到浏览器');
    setTimeout(() => { void statusQ.refetch(); }, 300);
  },
  onError: (error) => {
    downloadCtrl.finish();
    if (isDownloadCancelled(error)) {
      message.info('下载已取消，服务器文件仍保留');
    } else {
      message.error(describeApiError(error, '完整备份下载未能完成'));
    }
    void statusQ.refetch();
  },
});
```

- [ ] **Step 3: Modal 可关闭 + 关闭即中断**

`Modal` 的 `closable={!purgeM.isPending && !downloadM.isPending}` → `closable={!purgeM.isPending}`。
`close` 函数：
```ts
const close = () => {
  if (purgeM.isPending) return;
  if (downloadM.isPending) downloadCtrl.cancel();
  setOpen(false);
  setBackupChecked(false);
  setConfirmation('');
};
```
footer 的"关闭"按钮 `disabled={purgeM.isPending || downloadM.isPending}` → `disabled={purgeM.isPending}`。

- [ ] **Step 4: 下载进行中显示取消按钮**

在下载进度条附近（`{downloadProgress && (<TransferProgressBar ... />)}`）改为：
```tsx
{downloadProgress && (
  <Space wrap>
    <TransferProgressBar label="完整备份下载" progress={downloadProgress} />
    {downloadCtrl.active && (
      <Button
        size="small"
        icon={<StopOutlined />}
        onClick={() => { downloadCtrl.cancel(); setDownloadProgress(null); }}
      >
        取消下载
      </Button>
    )}
  </Space>
)}
```

需 `import { StopOutlined } from '@ant-design/icons';`。

- [ ] **Step 5: 类型检查 + 架构检查**

Run: `cd frontend && npx tsc -b --pretty false && node scripts/check-architecture.mjs`
Expected: 无错误；行数 < 600。

- [ ] **Step 6: 提交**

Commit:
```bash
git add frontend/src/features/workflows/WorkflowRetentionControl.tsx
git commit -m "feat(retention): 完整备份下载支持取消与关闭中断"
```

---

### Task 8: WorkflowBatchExportControl —— 取消 + 关闭即中断

**Files:**
- Modify: `frontend/src/features/workflows/WorkflowBatchExportControl.tsx`

**Interfaces:**
- Consumes: `useDownload`、`isDownloadCancelled`
- Produces: 分批导出下载进行中显示"取消下载"；Modal 可关闭并自动中断下载

- [ ] **Step 1: import 与 hook**

```ts
import { isDownloadCancelled, useDownload } from '../../shared/api';
```

组件内：
```ts
const downloadCtrl = useDownload();
```

- [ ] **Step 2: 改造 `downloadM`**

```ts
const downloadM = useMutation({
  mutationFn: (row: WorkflowBatchExport) => (
    downloadWorkflowBatchExport(row, setDownloadProgress, downloadCtrl.start())
  ),
  onSuccess: () => {
    downloadCtrl.finish();
    message.success('分批导出 ZIP 已下载到浏览器');
    setTimeout(() => { void statusQ.refetch(); }, 300);
  },
  onError: (error) => {
    downloadCtrl.finish();
    if (isDownloadCancelled(error)) {
      message.info('下载已取消，服务器文件仍保留，可重新下载');
    } else {
      message.error(describeApiError(error, '分批导出下载失败'));
    }
    void statusQ.refetch();
  },
});
```

- [ ] **Step 3: Modal 可关闭 + 关闭即中断**

`Modal` 的 `closable={!purgeM.isPending && !downloadM.isPending}` → `closable={!purgeM.isPending}`。
`closeAndRetain`：
```ts
const closeAndRetain = () => {
  if (purgeM.isPending) return;
  if (downloadM.isPending) downloadCtrl.cancel();
  setOpen(false);
  setCreatedExport(null);
  setSelectionInitialized(false);
  setSelected([]);
  setDownloadProgress(null);
};
```
footer 的"暂不删除"按钮 `disabled={purgeM.isPending || downloadM.isPending}` → `disabled={purgeM.isPending}`。

- [ ] **Step 4: 下载进行中显示取消按钮**

`{downloadProgress && (<TransferProgressBar label="分批图纸下载" progress={downloadProgress} />)}` 改为：
```tsx
{downloadProgress && (
  <Space wrap>
    <TransferProgressBar label="分批图纸下载" progress={downloadProgress} />
    {downloadCtrl.active && (
      <Button
        size="small"
        icon={<StopOutlined />}
        onClick={() => { downloadCtrl.cancel(); setDownloadProgress(null); }}
      >
        取消下载
      </Button>
    )}
  </Space>
)}
```

需 `import { StopOutlined } from '@ant-design/icons';`。

- [ ] **Step 5: 类型检查 + 架构检查**

Run: `cd frontend && npx tsc -b --pretty false && node scripts/check-architecture.mjs`
Expected: 无错误；行数 < 600。

- [ ] **Step 6: 提交**

Commit:
```bash
git add frontend/src/features/workflows/WorkflowBatchExportControl.tsx
git commit -m "feat(batch-export): 分批导出下载支持取消与关闭中断"
```

---

### Task 9: DrawingProcessingPanel —— 拆板 ZIP 下载取消

**Files:**
- Modify: `frontend/src/features/workflows/DrawingProcessingPanel.tsx`

**Interfaces:**
- Consumes: `useDownload`、`isDownloadCancelled`
- Produces: `useNativeWorkflowDownload` 增加 `cancel`/`active`；拆板结果与整批原图下载可取消

- [ ] **Step 1: `useNativeWorkflowDownload` 接入取消**

在 hook 内加：
```ts
const downloadCtrl = useDownload();
```
`launch` 改为：
```ts
const launch = async (next: WorkflowBatchExport) => {
  setDownloading(true);
  setProgress(null);
  try {
    await downloadWorkflowBatchExport(next, setProgress, downloadCtrl.start());
    setLaunchFailed(false);
    setTimeout(() => { void statusQ.refetch(); }, 300);
  } catch (error) {
    setLaunchFailed(true);
    if (isDownloadCancelled(error)) {
      message.info('下载已取消，服务器文件仍保留，可重新下载');
    } else {
      message.error(describeApiError(error, errorText));
    }
  } finally {
    setDownloading(false);
    downloadCtrl.finish();
  }
};
```
返回值追加：
```ts
return {
  start,
  loading: createM.isPending || downloading || (!launchFailed && ACTIVE_EXPORT_STATUSES.has(row?.status ?? '')),
  failed: launchFailed || row?.status === 'download_failed',
  progress,
  cancel: downloadCtrl.cancel,
  active: downloadCtrl.active,
};
```

需 import：`import { isDownloadCancelled, useDownload } from '../../shared/api';`

- [ ] **Step 2: 下载进行中显示取消按钮**

在两个 `TransferProgressBar`（`拆板结果下载`、`本批原图下载`）处，各加取消按钮（用对应 hook 返回的 `cancel`/`active`）：
```tsx
{splitResultsDownload.progress && (
  <Space wrap>
    <TransferProgressBar label="拆板结果下载" progress={splitResultsDownload.progress} />
    {splitResultsDownload.active && (
      <Button size="small" icon={<StopOutlined />} onClick={splitResultsDownload.cancel}>
        取消下载
      </Button>
    )}
  </Space>
)}
```
`allDrawingsDownload` 同理。需 `import { StopOutlined } from '@ant-design/icons';`。

- [ ] **Step 3: 类型检查 + 架构检查**

Run: `cd frontend && npx tsc -b --pretty false && node scripts/check-architecture.mjs`
Expected: 无错误；行数 < 600。

- [ ] **Step 4: 提交**

Commit:
```bash
git add frontend/src/features/workflows/DrawingProcessingPanel.tsx
git commit -m "feat(split): 拆板 ZIP 下载支持取消"
```

---

### Task 10: DrawingSelectiveExportControl —— 取消 + 关闭即中断

**Files:**
- Modify: `frontend/src/features/workflows/DrawingSelectiveExportControl.tsx`

**Interfaces:**
- Consumes: `useDownload`、`isDownloadCancelled`
- Produces: 选择导出下载进行中显示"取消下载"；Modal 可关闭并自动中断下载

- [ ] **Step 1: import 与 hook**

```ts
import { isDownloadCancelled, useDownload } from '../../shared/api';
```
组件内：
```ts
const downloadCtrl = useDownload();
```

- [ ] **Step 2: 改造 `downloadM`**

```ts
const downloadM = useMutation({
  mutationFn: (next: DrawingSelectiveExport) => (
    downloadDrawingSelectiveExport(next, setDownloadProgress, downloadCtrl.start())
  ),
  onSuccess: (_data, next) => {
    downloadCtrl.finish();
    message.success(`已下载 ${next.file_count} 个 DXF`);
  },
  onError: (error) => {
    downloadCtrl.finish();
    if (isDownloadCancelled(error)) {
      message.info('下载已取消');
    } else {
      message.error(describeApiError(error, '选择导出下载失败'));
    }
  },
});
```

- [ ] **Step 3: Modal 可关闭 + 关闭即中断**

`Modal` 的 `closable={!createM.isPending && !downloadM.isPending}` → `closable={!createM.isPending}`。
`close`：
```ts
const close = () => {
  if (createM.isPending) return;
  if (downloadM.isPending) downloadCtrl.cancel();
  setOpen(false);
  setPrepared(null);
  setSelectionInitialized(false);
  setSelected([]);
};
```
footer "关闭"按钮 `disabled={downloadM.isPending}` 保留（无 purge 场景，关闭即取消）。

- [ ] **Step 4: 下载进行中显示取消按钮**

`{downloadProgress && (<TransferProgressBar label="分类图纸下载" progress={downloadProgress} />)}` 改为带 `Space wrap` + 取消按钮的版本（同 Task 7/8 模式，用 `downloadCtrl.cancel`/`downloadCtrl.active`）。需 `import { StopOutlined } from '@ant-design/icons';`。

- [ ] **Step 5: 类型检查 + 架构检查**

Run: `cd frontend && npx tsc -b --pretty false && node scripts/check-architecture.mjs`
Expected: 无错误；行数 < 600。

- [ ] **Step 6: 提交**

Commit:
```bash
git add frontend/src/features/workflows/DrawingSelectiveExportControl.tsx
git commit -m "feat(selective-export): 选择导出下载支持取消与关闭中断"
```

---

### Task 11: 前端 e2e 测试（单文件下载 + 取消）

**Files:**
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`

**Interfaces:**
- Consumes: Task 6 的 UI（明细行"下载"按钮、下载进行中"取消下载"按钮）
- Produces: 覆盖分类单文件下载与取消交互的 Playwright 断言

- [ ] **Step 1: 在 `production route inspects stages safely...` 测试中补充单文件下载 mock 与断言**

在该测试已有的 `pxDetails` mock 与 `dxf-classification` mock 基础上，追加单文件端点 route：

```ts
let singleFileRequests = 0;
await page.route(
  /\/api\/v1\/workflows\/42\/dxf-classification\/groups\/type(?:%3A|:)PX\/files\/px-1_%E6%8B%86%E6%9D%BF%E5%89%8D.dxf\/download/,
  async (route) => {
    singleFileRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      headers: {
        'content-disposition': "attachment; filename*=UTF-8''px-1_%E6%8B%86%E6%9D%BF%E5%89%8D.dxf",
        'access-control-expose-headers': 'content-disposition',
      },
      body: 'PX-SINGLE-DXF',
    });
  },
);
```

在抽屉打开后、关闭前，追加断言（放在"下载 PX 类 DXF"之后）：
```ts
const singleDownloadPromise = page.waitForEvent('download');
await page
  .getByRole('dialog', { name: 'PX · 3 张 DXF' })
  .getByRole('button', { name: '下载 px-1_拆板前.dxf' })
  .click();
const singleDownload = await singleDownloadPromise;
await expect.poll(() => singleFileRequests).toBe(1);
expect(singleDownload.suggestedFilename()).toBe('px-1_拆板前.dxf');
await singleDownload.delete();
```

- [ ] **Step 2: 新增独立取消交互测试**

在同文件追加一个测试 `test('large download exposes cancel and aborts on click', async ({ page })`，复用该文件的 workflow/template/classification mock 与登录 setup（可抽取或复制最小 mock 集）。核心：

```ts
let aborted = false;
await page.route(
  /\/api\/v1\/workflows\/42\/dxf-classification\/groups\/type(?:%3A|:)PX\/download-archive/,
  async (route) => {
    route.request().on('abort', () => { aborted = true; });
    // 保持响应悬挂，模拟大文件下载进行中
    await new Promise(() => {});
  },
);
```

然后：打开分类面板 → 点击 `下载 PX 类 DXF`（或 `下载全部 DXF`）→ 断言 `取消下载` 按钮可见 → 点击 → `await expect.poll(() => aborted).toBe(true)` → 断言 `取消下载` 按钮消失、且没有触发浏览器 `download` 事件（用 `let downloadFired = false; page.on('download', () => { downloadFired = true; });` 兜底）。

- [ ] **Step 3: 运行 e2e**

Run: `cd frontend && npx playwright test tests/e2e/workflows/workflow-detail.spec.ts`
Expected: 新增与既有断言全部通过。（如环境无后端，确认 `PLAYWRIGHT_*` 环境变量按 support/test-env.ts 配置。）

- [ ] **Step 4: 提交**

Commit:
```bash
git add frontend/tests/e2e/workflows/workflow-detail.spec.ts
git commit -m "test(e2e): 分类单文件下载与取消交互"
```

---

### Task 12: 全量验证 + 子 agent 复查

**Files:** 无新文件。

- [ ] **Step 1: 后端全量测试**

Run: `cd backend && python -m pytest tests/workflows -q`
Expected: 全部通过。

- [ ] **Step 2: 前端静态检查**

Run: `cd frontend && npx tsc -b --pretty false && node scripts/check-architecture.mjs && npm run lint`
Expected: 全部通过。

- [ ] **Step 3: 前端 e2e 下载相关回归**

Run: `cd frontend && npx playwright test tests/e2e/workflows tests/e2e/files`
Expected: 全部通过。

- [ ] **Step 4: 提交最终状态并复查**

确认工作区无未提交改动（除既有未跟踪目录）。Commit 剩余改动（若有）。

- [ ] **Step 5: 派子 agent 复查**

按用户授权，派独立 sub-agent 对 `git diff origin/main..HEAD` 做代码审查：检查取消/中断路径的 axios 错误识别、Modal 关闭中断的竞态、`preparing` 状态对既有下载/上传 UI 的回归影响、并发约束是否遗漏下载入口、以及后端端点鉴权与流转一致性。修复发现的问题后复跑 Step 1–3。
