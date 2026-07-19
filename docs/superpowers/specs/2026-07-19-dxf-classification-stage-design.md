# DXF 分类分流阶段设计

## 范围

在已冻结的生产输入之后新增 `dxf_classification` 自动阶段，调用 `steel_dxf_classifier` 1.1.0 的正式 CLI/文件系统契约。该阶段只完成 DXF 文件名预处理、标题栏分类和分流结果持久化；自动拆板、人工拆板和校验继续保留在下一阶段 `drawing_processing`，本次不实现。

## 流程与边界

1. `source_intake` 冻结后进入 `dxf_classification`。
2. 操作员在生产流程详情点击“开始 DXF 分类分流”。后端幂等创建 `classify_steel_dxf` Job 并绑定当前阶段。
3. Worker 只读取冻结清单中的服务器派生 DXF，重新校验 MinIO 对象大小和 SHA-256，在临时目录按 `<项目代码>-workflow-<id>_dxf` 组织输入。
4. 通过 `python -m steel_dxf_classifier.cli --json <输入目录>` 调用 1.1.0。输入副本按契约改名为 `*_拆板前.dxf`；源 MinIO 对象不改名、不覆盖。
5. 输出目录严格保留 `<项目名>_<零件类型>_dxf`、`<项目名>_待确认_dxf`、`<项目名>_无法读取_dxf`，并生成同项目 JSON 报告和 CSV 清单。
6. 每个输出 DXF、JSON、CSV 均独立写入 MinIO 并建立 `files` 登记；对象键使用 `workflows/<workflow_id>/dxf-classification/attempt-<attempt>/<契约相对路径>`。
7. `dxf_classification_runs/items` 保存 Job attempt、输入清单哈希、分类器/schema 版本、来源 DXF、Drawing、分流 DXF、处置、零件类型、诊断和输出目录。`workflow_artifacts` 保存分类 DXF、报告和清单引用。
8. CLI 退出 0 或 2 都表示批处理完成。退出 2 保留 `review_required`/`unreadable` 处置和诊断，前端明确提示；其他退出码使 Job/阶段失败并可通过同一接口重试新 attempt。
9. 分类成功后进入 `drawing_processing` 并停止自动推进。后续拆板能力仍返回既有 501 留白契约。

## 防错与幂等

- 仅允许当前 `dxf_classification` 阶段执行，且输入批次必须冻结。
- Job request key 固定为 workflow + stage；重复提交复用活动/成功 Job，失败重试递增 attempt。
- Worker 以 job_id + attempt 条件领取，旧 attempt 不能覆盖新结果。
- 每个 attempt 使用独立 MinIO 前缀和数据库 run，保留审计历史。
- 入库前核对报告逐文件数量、输出文件存在性、输出 DXF 与来源字节 SHA-256 一致。
- 前端不允许人工选择任意 DXF 批次，避免把其他项目文件送入当前流程。

## 前端

当前阶段为 `dxf_classification` 时展示专用面板：输入数量/冻结清单、开始或重试按钮、Job 状态与进度、分类汇总、类型分布、逐图处置/诊断、报告与清单下载。运行中轮询；完成后仍可在阶段产物和分类详情中追溯全部文件。
