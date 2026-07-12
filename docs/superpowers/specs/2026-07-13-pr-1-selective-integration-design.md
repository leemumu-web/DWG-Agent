# PR #1 选择性重写吸收设计

## 背景与目标

远程 PR #1 `feature/excel-final-frontend` 从提交 `0687ace` 分叉，包含 DXF 在线预览、Excel Final 前端增强、DXF→Excel 到 Excel Final 的流程桥接、字段扩容和开发 Compose。当前 `main` 已在该分叉点之后完成数据控制台、MinIO/MySQL 一致性、文件传输流水、事务补偿和服务端分页，因此不能通过普通合并或逐提交 cherry-pick 吸收 PR。

本次工作的目标是以当前 `main` 为权威基线，保留 PR 的产品思路，重写不符合现有事务、权限、迁移和前端契约的实现，并确保所有新增对象和下载均进入 MySQL/MinIO 登记与文件传输流水。

## 审查结论

### 可保留的产品能力

1. DXF 文件在线预览、缩放、平移、图层和实体统计。
2. Excel Final 健康状态、跨批次搜索、比重查询、零件详情和构件汇总。
3. DXF→Excel 结果直接提交到 Excel Final。
4. Excel Final 真实业务标识字段扩容。
5. 开发 Compose 覆盖文件的使用思路。

### 必须拒绝或重写的实现

1. 不回改历史迁移，不执行 PR 中删除 Celery/Kombu 表的 DDL。
2. 不接入从 `3480bd86ddc3` 分叉的迁移；新迁移只能接在当前唯一 head 后。
3. 不使用错误指向源 DXF 的 `preview_url`，不依赖只对本地存储生效的 `local_path()` 缓存判断。
4. 不让 `<img>` 直接访问要求 Bearer Token 的签名下载 URL；预览内容由前端以认证 Blob 请求加载。
5. 不使用双遍、约 27 秒的 Matplotlib PNG 渲染；采用约 1.4 秒的 ezdxf SVG 记录器输出。
6. 不采用仅统计 modelspace 且永远无法触发的实体上限。
7. 不采用每五秒逐批次查询状态的 N+1 前端轮询。
8. 不采用只统计第一页却标记为全局总数的统计卡。
9. 不合入过期的根目录 `SUMMARY.md`、不一致的依赖锁和旧端口开发配置。

## 总体架构

```text
DXF source StoredFile
  -> permission + size guard
  -> bounded storage read
  -> ezdxf parse + document entity guard
  -> SVG recorder (external images disabled)
  -> source-row lock + cache recheck
  -> save_bytes_as_file saga
  -> preview StoredFile + generated transfer ledger

Browser
  -> GET metadata with Bearer token
  -> GET preview content as authenticated Blob
  -> outbound preview transfer ledger settles by streamed bytes
  -> object URL rendered inside zoom/pan viewer
```

Excel Final 保留现有任务状态、重试、服务端分页和下载实现，在其上增加权限过滤的全局概览、工具区、详情能力和 DXF→Excel 流程入口。

## DXF 预览设计

### 解析与渲染

- 后端直接声明 `ezdxf` 和 `Pillow` 依赖，不依赖 `dxf2excel` 的传递依赖。
- 使用 `SVGBackend` 生成 `image/svg+xml`，文本由 ezdxf 转换为绘图路径。
- 禁用 DXF 外部图片，拒绝生成结果中的 `script`、`foreignObject`、`href`、DOCTYPE 和 ENTITY。
- 文件大小在读取对象前检查，流式读取时再次检查真实字节数。
- 复杂度使用文档中存活实体总量判断；同时返回 modelspace 类型统计供界面展示。
- SVG 最终字节数另设上限，防止小输入生成异常大的预览。

### 缓存、并发与事务

- 预览文件使用系统批次标记 `dxf-preview:{source_id}:{sha_prefix}`，对象 key 使用 UUID，避免缺失对象阻塞同一唯一键重建。
- 缓存命中通过 MySQL `StoredFile` 查询加存储 `stat_object()` 验证，因此本地和 MinIO 行为一致。
- 初次缓存未命中时先完成 CPU 渲染，再锁定源文件行并二次检查缓存。并发请求可以重复计算，但只有第一个请求写入对象和元数据。
- 写入统一调用 `save_bytes_as_file()`，继承对象写入、MySQL 登记、提交前补偿和 `FileTransfer` 流水。
- 缓存对象缺失时将旧登记标记为删除并创建新对象，不复用已失真的对象 key。

### 权限与输出

- 元数据端点和内容端点都重新验证源 DXF 的读取权限及删除状态。
- 内容端点只接受与源文件 ID、源 SHA 对应的预览文件 ID，不能借其读取任意 StoredFile。
- 内容采用认证流式响应；前端用 Axios 获取 Blob 并创建临时 object URL。
- 每次内容输出建立 `direction=outbound, operation=preview` 的流水，以实际流式字节结算。
- 生成和查看分别写入审计日志。

## Excel Final 设计

### 后端

- 新增权限过滤的概览接口，返回可访问批次数、零件数、构件数、净重和毛重。
- 构件列表改为服务端分页，并保持响应 `data` 字段兼容。
- 现有批次、零件、搜索、比重、健康和任务接口继续作为权威契约。
- 标识字段扩容通过当前 Alembic head 后的新迁移完成，模型与迁移同步。

### 前端

- `ExcelFinalPage` 继续负责上传、任务、批次和页面级编排。
- 概览/健康、跨批次搜索/比重工具、批次详情分别拆为聚焦组件，避免继续扩大单文件职责。
- 搜索使用草稿与已提交条件分离；清除条件立即隐藏并清空旧结果。
- 批次、零件、构件和跨批次搜索全部使用服务端分页。
- 结果文件继续通过现有认证下载路径获取；Excel 结果可复用 `ExcelPreview` 预览。
- 错误不静默吞掉，轮询、工具和详情都有可见错误与重试动作。

## DXF→Excel 到 Excel Final 桥接

- 只在 DXF→Excel 成功且存在结果文件时显示提交操作。
- 前端静态导入 Excel Final API，使用单批次 loading 集合防止双击重复提交。
- 提交前显示确认信息；成功后导航至带 `job_id` 查询参数的 Excel Final 页面。
- Excel Final 页面读取该参数并立即显示、轮询相应任务。

## 开发配置与文档

- `compose.dev.yaml` 以当前 8010 端口、当前 worker 名称和内部网络为基线重写。
- 开发后端仅绑定 `127.0.0.1` 的可配置宿主端口，不关闭关键健康检查。
- 文档只更新 `docs/*.md` 中文版本和生成的 `docs/api.md`；不增加根目录状态快照文档。
- 文档明确区分代码存在、默认 flag、外部依赖和本次验证证据。

## 验收标准

1. Alembic 只有一个 head，空库和现有库迁移都通过。
2. 本地存储与 MinIO 均能生成、命中和读取 DXF 预览缓存。
3. 预览生成和输出分别产生成功的内部/出站流水；失败写入可补偿或可见失败状态。
4. 无权用户不能生成或读取预览；共享源文件权限保持现有语义。
5. 真实仓库 DXF 样例可以在前端缩放、平移和查看统计。
6. Excel Final 概览数值来自权限过滤后的全量数据，不是当前页近似值。
7. 搜索清除不显示旧结果，构件和零件分页不全量拉取。
8. DXF→Excel 成功结果可一次提交并跳转跟踪 Excel Final 任务。
9. 后端全集、前端构建、Playwright、迁移、Compose、基础设施和文档门禁全部通过。
