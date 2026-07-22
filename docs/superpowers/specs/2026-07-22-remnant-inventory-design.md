# 余料库设计

**状态：** 已批准

**日期：** 2026-07-22

**适用版本：** DWG-Agent v0.1 技术预览版后续功能

## 1. 目标

在现有 Web 管理端增加全厂共享的余料库。工人可以按标准材质和厚度精确查找可用或已预占余料，在线预览对应图纸，预占后下载原始 DWG/DXF，并最终确认使用或取消预占。

所有有余料权限的工人都可以单张或批量导入 DWG/DXF。系统负责将 DWG 转换为 DXF，从图纸中自动提取材质、项目编号和多个零件编号；工人手工填写厚度，校正解析结果并确认入库。

## 2. 范围与边界

### 2.1 第一版包含

- 全厂共享余料检索，不按现有项目成员关系隔离；
- 材质和厚度精确检索，可选“包含同系列材质”；
- 默认展示可用和已预占余料，已使用/归档记录进入历史筛选；
- DWG/DXF 单选和多选上传，不支持 ZIP；
- DWG 批量转 DXF、DXF 多进程解析、逐图失败和重试；
- 图纸级厚度、材质、项目编号及多个零件编号；
- 自动解析候选、证据和警告，人工修改及批量确认；
- 在线 DXF/SVG 预览和受权限控制的原图下载；
- `available → reserved → used` 库存生命周期，以及取消预占；
- 管理员维护标准材质、同系列标识和解析别名；
- 权限、审计、幂等、重复文件检测和并发预占保护。

### 2.2 第一版不包含

- ZIP 导入；
- 余料几何面积、周长、重量、库位和实物尺寸管理；
- 自动套料、CAM 或 SinoCAM 联动；
- 自动释放预占；
- 将余料库直接加入 `linux_production` 工作流；
- RabbitMQ、Outbox 或新的分布式基础设施。

## 3. 架构

### 3.1 领域边界

新增 `backend/app/modules/remnant_inventory/` 作为唯一余料业务 owner，新增 `frontend/src/features/remnant-inventory/` 作为前端 owner。该领域通过公共接口复用现有能力：

- `files.interface`：原图、派生 DXF、SVG 预览、摘要与签名下载；
- `jobs.interface`：Job、attempt、进度、重试、取消和终态；
- `cad_processing.interface`：DWG→DXF 批量转换与 DXF 预览；
- `identity.interface`：当前用户和权限；
- `operations.audit.interface`：导入、确认、修改、预占、取消、使用和归档审计。

余料模块不得复制文件字节、Job 状态或项目模型，也不得导入其他领域的私有实现。`app.platform` 不反向依赖余料模块；模型、路由和任务通过 bootstrap registry 装配。

### 3.2 解析 Stage

新增独立 `Stages/remnant_drawing_reader/`。Stage 输入单个 DXF，输出版本化 JSON，不访问 HTTP、MySQL、MinIO、用户或权限。

解析器遍历 `TEXT`、`MTEXT`、`ATTRIB` 和嵌套 `INSERT`，处理 GBK/MIF、DXF Unicode、NFKC 和空白归一化。输出包含：

- 原始材质候选及文字、图层、块路径、坐标和句柄证据；
- 项目编号候选及证据；
- 去重后的零件编号候选及逐项证据；
- 未识别、冲突候选、编码异常和结构异常；
- 解析器版本、schema 版本和源 DXF SHA-256。

Stage 返回原始候选；标准材质和别名匹配由后端完成。厚度不由解析器决定，始终由工人填写。

### 3.3 多进程处理

API 只登记批次并立即响应，不在 FastAPI 请求进程中运行 ODA、DXF 解析或 Python 进程池。

- `remnant_convert` 队列默认并发 2；同一导入批次的全部 DWG 使用一次 ODA 目录批量转换，不同批次最多并行两个 ODA 进程；
- `remnant_parse` 队列默认并发 4；直接上传的 DXF 立即解析，转换成功的 DXF 逐图投递解析任务；
- 每个进程使用独立临时目录；
- 每个导入项独立成功、失败和重试；
- 所有 Worker 更新匹配当前 Job `status + attempt`，旧 attempt 不得覆盖新结果；
- 文件数量上限由 `REMNANT_IMPORT_MAX_FILES` 配置，不把常见的 2–10 张或偶发 20 张写成硬限制。

当前 MySQL SQL transport 足以承载有界批量导入；第一版不引入 RabbitMQ。

## 4. 数据模型

### 4.1 材质目录

`remnant_materials` 保存管理员维护的标准完整牌号，例如 `Q235B`、`Q235D`、`Q235B-Z15`。后缀属于牌号本身，不能被截断。字段包含标准代码、系列标识、启用状态和审计时间。

`remnant_material_aliases` 保存原始图纸写法到标准材质的映射。精确检索只匹配选中的标准材质；启用“包含同系列材质”时，扩展到系列标识相同的所有启用材质。

### 4.2 导入账本

`remnant_import_batches` 保存一次多文件导入的创建人、状态和总数/转换/解析/待确认/确认/失败计数。

`remnant_import_items` 保存每张图的源文件、派生 DXF、转换 Job、解析 Job、当前 attempt、处理状态、解析候选、识别证据、问题列表和工人校正值。正式确认前，所有候选和修改都只存在于导入项。

导入项状态为：

```text
uploaded → converting → parsing → pending_confirmation → confirmed
                    ↘ failed ← retry increments attempt
```

直接上传 DXF 跳过 `converting`。图纸可读取但字段未识别或冲突时仍进入 `pending_confirmation` 并展示警告；工人可手工补齐。只有文件损坏、转换失败或 DXF 无法读取才进入 `failed`。

### 4.3 正式余料

`remnants` 只在确认时创建，保存：

- 原图 `source_file_id`、解析用 `dxf_file_id`；
- 原图 SHA-256 唯一键；
- `thickness_mm`；
- 标准 `material_id`；
- 图纸级 `project_no`；
- `status`；
- 导入人、确认人和确认时间；
- 当前预占人及预占时间；
- 使用人、使用时间、归档人和归档时间；
- 乐观并发版本和时间戳。

`remnant_parts` 保存一张余料下的多个零件编号，`(remnant_id, part_no)` 唯一。

正式状态为：

```text
available → reserved → used
available ← reserved
available/reserved → archived
```

预占不自动释放。预占通过 `UPDATE ... WHERE status='available'` 原子执行；并发请求只有一个成功。预占后字段锁定，需先取消预占才能修改；`used` 永久只读。所有历史动作写入现有审计日志。

## 5. API 与权限

### 5.1 API 资源

- `/api/v1/remnant-materials`：查询标准材质；管理员新增、修改、停用材质和维护别名；
- `/api/v1/remnant-import-batches`：创建批次、查看进度、读取/修改导入项、批量填写厚度、重试、取消和批量确认；
- `/api/v1/remnants`：分页检索、详情、预览、下载、编辑、预占、取消预占、确认使用和归档。

成功和错误继续使用平台统一 envelope、request ID、稳定错误码和 SQL 权限过滤。静态动作路由必须先于 `/{remnant_id}` 参数路由。

### 5.2 权限

- 余料工人：查询、预览、创建导入、编辑并确认自己的导入、预占可用余料；
- 导入人和管理员：修改或归档仍为 `available` 的正式余料；
- 预占人和管理员：下载已预占原图、取消预占、确认使用；
- 管理员：维护材质/别名并管理所有余料和批次。

他人预占的余料仍可进入详情和在线预览，并显示占用人及预占时间，但不能下载或再次预占。项目编号是全厂共享余料的业务字段，不连接现有项目访问控制。

## 6. 检索与界面

检索必须提供标准材质和厚度；两者默认精确匹配。可选“包含同系列材质”。默认只返回 `available` 和 `reserved`，可用项在前，同状态按最新入库时间倒序。已使用和归档项只在历史筛选中出现。

前端沿用现有 React、Ant Design 和 feature 分区风格：

- “余料检索”：材质、厚度、同系列开关、状态和结果表；
- “批量导入”：多选 DWG/DXF、整批及逐图进度、选中项批量填写厚度、批量确认；
- “解析确认”：左侧在线预览，右侧编辑厚度、材质、项目编号和多个零件编号；
- 页面刷新后根据批次 ID 恢复进度和待确认数据；
- 在线预览使用派生 DXF/SVG；原图下载复用现有短期签名机制。

## 7. 校验、错误和幂等

- 只接受真实 DWG/DXF；扩展名、大小、DWG 头和 DXF 结构均须验证；
- 同批次或正式库 SHA-256 相同的原图阻止重复，并返回已有记录；
- ODA 批量转换后逐图核对产物，部分失败不阻塞其他项；
- 只有厚度、启用的标准材质、项目编号和至少一个去重零件编号完整时才能确认；
- 同一导入项重复确认返回原正式余料，不复制库存；
- 批量确认只提交校验通过的选中项；
- 材质候选无法匹配标准目录时，工人必须选择已有标准材质，或等待管理员建立材质；
- 未确认批次可取消，批次独占的派生对象使用现有存储补偿机制清理；
- 客户端错误不暴露 traceback、child stderr、主机路径、DSN 或对象存储签名。

## 8. 测试与验收

### 8.1 自动测试

- Stage：中文编码、普通文字、MTEXT、块属性、嵌套块、多零件号、牌号后缀、冲突候选和损坏 DXF；
- 后端：混合 DWG/DXF 批次、部分失败、attempt 重试、幂等确认、重复 SHA、权限负例、状态锁定和并发预占；
- 数据库：空 MySQL migration、唯一约束、材质系列查询和原子预占；
- 前端：多文件选择、进度恢复、批量厚度、逐图校正、批量确认、精确/系列检索、占用人展示和下载限制；
- 架构：模块 owner、公共接口、任务注册、路由、前端 feature、分区 README 和 runtime contract。

### 8.2 真实样本

以 `C:\Users\Ran-xin\Desktop\kuak\余料库\手动拆分清单` 中 144 个 AutoCAD 2018 DWG 为外部验收 corpus。原始业务图纸不提交仓库。

系统先生成候选报告，由业务方校对材质、项目编号和零件编号；校对结论用于改进规则，并将脱敏的最小 DXF 固化为自动回归夹具。典型验收批次为 2–10 张混合 DWG/DXF，并补充超过 20 张的背压验证。

### 8.3 上线门禁

新增 `REMNANT_INVENTORY_ENABLED=false`。只有完成以下验证后才启用：

1. 管理员已配置正式材质、系列和别名；
2. 真实样本候选已完成首轮业务校对；
3. DWG 批量转换、四进程解析、失败重试和刷新恢复通过；
4. 两名用户并发预占只允许一人成功；
5. 他人预占时只能预览、不能下载；
6. `make verify-quick`、受影响后端/Stage/前端测试、文档和架构检查通过。

## 9. 仓库集成要求

实施必须同步更新模型和任务 registry、API router、Celery 路由、Compose Worker、Alembic migration、配置示例、模块目录、runtime contract、前端 feature 检查、生成 API 文档和新增分区 README。

余料库作为独立产品能力交付。未来生产工作流需要查询或消耗余料时，只能通过 `remnant_inventory.interface` 接入，不直接导入内部模型或服务。
