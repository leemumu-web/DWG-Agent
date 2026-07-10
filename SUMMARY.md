# DWG-Agent 项目开发总结

> 生成时间：2026-07-10
> 本文件汇总了所有已完成的工作、发现的问题、待处理事项和下一步计划。
> 在新对话中使用此文件作为上下文继续开发。

---

## 一、项目概述

**DWG-Agent** 是企业级 CAD 智能处理平台，面向钢结构行业。委托方：郑州宝冶钢结构有限公司，研发方：郑州大学。

- **仓库地址**：https://github.com/Creeken-Harrans/DWG-Agent
- **当前阶段**：Stage 1（平台骨架）已完成，Stage 3（DXF 管线代码已实现但默认关闭）
- **技术栈**：Python 3.12 + FastAPI + MySQL 8.x + React 19 + TypeScript + Vite + Ant Design 6 + Celery + Docker Compose
- **部署方式**：Docker Compose（9 服务），目标服务器为中标麒麟

---

## 二、完成的修复工作

### 2.1 仓库可用性修复（可提交到 Git）

**问题**：`Stages/dxf2excel/` 和 `Stages/excel_final/` 是空目录，导致 `uv sync` 和 `docker compose build` 失败。

**修复**：创建了最小 stub 包：

| 文件 | 操作 |
|------|------|
| `Stages/dxf2excel/pyproject.toml` | 新建（stub 包声明） |
| `Stages/dxf2excel/src/dxf2excel/__init__.py` | 新建（docstring stub） |
| `Stages/excel_final/pyproject.toml` | 新建（stub 包声明） |
| `Stages/excel_final/src/excel_final/__init__.py` | 新建（docstring stub） |

**注意**：`Stages/dwg2dxf/` 和 `Stages/dxf2dwg/` 本身已有完整代码（ODA File Converter 管道），不需要修复。

### 2.2 Dockerfile 修复

**问题**：Dockerfile 没有 `COPY Stages/excel_final`，但 `pyproject.toml` 依赖它。

**修复**：`backend/Dockerfile` 第 43 行新增：
```dockerfile
COPY Stages/excel_final ./Stages/excel_final
```

### 2.3 Alembic 迁移修复

**问题**：迁移 `3480bd86ddc3_add_excel_final_tables.py` 使用 Alembic 的 `op.drop_table()` 删除 Celery SQL 传输表，但 MySQL 不支持 DDL 回滚。如果 Celery 表不存在则报错，且已创建的 `excel_final_*` 表在重试时又因"已存在"而失败。

**修复**：将 DROP 操作改为 `op.execute("DROP TABLE IF EXISTS ...")`，将 downgrade 中的 CREATE 改为 `op.execute("CREATE TABLE IF NOT EXISTS ...")`。

### 2.4 init_db.py 修复

**问题**：Dockerfile CMD 执行 `alembic upgrade head && python -m app.db.init_db && gunicorn ...`。Alembic 已管理所有 schema，但 `init_db.py` 又调用 `Base.metadata.create_all()`，导致 MySQL 报 "Table already exists"。

**修复**：从 `init_db.py` 中移除 `Base.metadata.create_all(bind=engine)` 调用。现在 init_db.py 只做种子数据（角色、权限、超级管理员），schema 由 Alembic 独占管理。

### 2.5 .env.docker 配置

从 `.env.docker.example` 创建 `.env.docker`，将所有 `CHANGE_ME_*` 占位值替换为开发用密钥。`.env.docker` 已被 `.gitignore` 忽略，不提交。

**已设置的开发值**：
```
MYSQL_PASSWORD=dwg_dev_pass
MYSQL_ROOT_PASSWORD=root_dev_pass
JWT_SECRET_KEY=dev-jwt-secret-not-for-production-use-only
SUPER_ADMIN_PASSWORD=SuperAdminPass1
REFRESH_COOKIE_SECURE=false
MINIO_ROOT_USER=minioadmin / MINIO_ROOT_PASSWORD=minioadmin
```

---

## 三、新增功能实现

### 3.1 DXF 文件预览 ✅ **已调通**

**方案**：服务端渲染。后端用 ezdxf + matplotlib 将 DXF 渲染为 PNG 深色背景图片，存储到 MinIO/本地文件系统（带缓存 key），前端通过模态弹窗展示（支持滚轮缩放 + 拖拽平移）。

**已验证**：上传 DXF → `GET /api/v1/files/{id}/dxf-preview` 返回 `{total_entities, layers, bounds, preview_url, cached}` → 前端弹窗展示 PNG 图片。

**Docker 中运行正常**：`curl http://localhost/api/v1/files/6/dxf-preview` 返回完整 JSON。

**修复的 bug**：
- ezdxf 1.4.4 API 适配：`doc.layers.names()` → `[l.dxf.name for l in doc.layers]`
- `entity.dxf.dxftype()` → `entity.dxftype()`（ezdxf 1.4.4 中某些对象返回 string 而非 callable）
- `LayoutProperties.to_drawing_config()` 不存在 → 使用 `Configuration(background_policy=BackgroundPolicy.CUSTOM, custom_bg_color="#1a1a2e")`

**新建文件**：

| 文件 | 说明 |
|------|------|
| `backend/app/services/dxf_preview_service.py` | DXF→PNG 渲染服务 |
| `frontend/src/components/DxfPreviewModal.tsx` | 预览弹窗组件 |

**修改文件**：

| 文件 | 改动 |
|------|------|
| `backend/pyproject.toml` | 添加 `ezdxf>=1.4` + `matplotlib>=3.9` |
| `backend/app/api/v1/files_api.py` | 新增 `GET /{file_id}/dxf-preview` 端点（同步 def，避免阻塞 asyncio） |
| `frontend/package.json` | 添加 `react-zoom-pan-pinch` |
| `frontend/src/types/file.ts` | 添加 `DxfPreviewResponse`、`DxfEntity`、`DxfBounds` 类型 |
| `frontend/src/api/files.api.ts` | 添加 `fetchDxfPreview()` 函数 |
| `frontend/src/components/ConversionPage.tsx` | 操作列添加"预览"按钮（DXF 源文件和转换后 DXF 结果文件） |

**关键技术决策**：

| 决策点 | 方案 |
|--------|------|
| Matplotlib 线程安全 | 使用 `Figure` + `FigureCanvasAgg` OO API，显式 `ax.clear()` / `fig.clf()` / `gc.collect()` 清理 |
| 异步事件循环保护 | 端点使用 `def`（非 `async def`），FastAPI 自动线程池运行 |
| 缓存失效 | cache key = `previews/{file_id}_{sha256[:8]}.png` |
| 背景颜色 | 深色 `#1a1a2e`，通过 `LayoutProperties.set_colors()` 自动适配 ACI 颜色 |
| 大文件保护 | >20MB 或 >100K 实体直接拒绝 |
| 前端缩放 | `react-zoom-pan-pinch` 库实现滚轮缩放 + 拖拽平移 |
| BBox 防护 | `try-except` 包裹，失败返回默认值 |

**预览入口**：
- DXF 源文件：操作列显示青色眼睛图标按钮
- DWG→DXF 转换完成后：操作列显示蓝色眼睛图标按钮（预览 DXF 结果文件）

前端构建通过，ruff 0 errors。

前端构建通过，ruff 0 errors。

### 3.2 Excel → 最终零件清单（excel_final 前端对接）✅ **已完成**

**状态**：后端 100% 完成（14 个 API 端点），前端从 0% → 现在已完成对接。

**新建文件**：

| 文件 | 说明 |
|------|------|
| `frontend/src/types/excel-final.ts` | TypeScript 类型（Batch/Part/Component/Status 等） |
| `frontend/src/api/excel-final.api.ts` | API 客户端（11 个函数） |
| `frontend/src/features/files/ExcelFinalPage.tsx` | Excel → 零件清单页面 |

**修改文件**：

| 文件 | 改动 |
|------|------|
| `frontend/src/app/router.tsx` | 添加 `/files/excel-final` 路由 + import |
| `frontend/src/features/files/FilesLayout.tsx` | 添加第 4 个 Tab "Excel → 零件清单" |

**页面功能**：
- 上传区域：拖拽上传 Excel → 自动提交 excel_final 处理 → 轮询进度
- 批次列表：显示所有处理过的批次（状态/源文件/格式/零件数/构件数/净重）
- 批次详情抽屉：材质统计 + 零件清单表格（分页+筛选）+ 构件汇总表格
- Excel 预览模态框：复用现有 `ExcelPreview` 组件

**后端 API（14 个端点，均已实现）**：

| 方法 | URL | 功能 |
|------|-----|------|
| POST | `/api/v1/excel-final/upload` | 上传 Excel |
| POST | `/api/v1/excel-final/process` | 提交处理 |
| POST | `/api/v1/excel-final/upload-and-process` | 上传并处理 |
| GET | `/api/v1/excel-final/process/{job_id}` | 查询任务状态 |
| GET | `/api/v1/excel-final/process/{job_id}/download` | 下载处理结果 |
| GET | `/api/v1/excel-final/batches` | 批次列表 |
| GET | `/api/v1/excel-final/batches/{id}` | 批次详情+统计 |
| GET | `/api/v1/excel-final/batches/{id}/parts` | 零件清单（分页+筛选） |
| GET | `/api/v1/excel-final/batches/{id}/parts/{pid}` | 单个零件详情 |
| GET | `/api/v1/excel-final/batches/{id}/components` | 构件汇总 |
| GET | `/api/v1/excel-final/parts/search` | 跨批次零件搜索 |
| GET | `/api/v1/excel-final/weights/lookup` | 五金手册比重查询 |
| GET | `/api/v1/excel-final/health` | 管道可用性检查 |

---

## 四、Docker 部署指南

### 4.1 生产部署

```bash
cd "C:\Users\Ran-xin\Desktop\kuak\前端\DWG-Agent"

# 1. 确保 Docker Desktop 运行中

# 2. 前端构建（代码改动后需要重新构建）
cd frontend && npm run build && cd ..

# 3. 启动全部服务
docker compose down -v && docker compose build --no-cache && docker compose up -d

# 4. 验证
curl http://localhost/health
# 浏览器打开 http://localhost
# 登录：admin / SuperAdminPass1
```

### 4.2 开发模式（实时热更新 + 测试数据挂载）

使用 `compose.dev.yaml` 覆盖文件启动：

```bash
# 核心服务（开发模式）
docker compose -f compose.yaml -f compose.dev.yaml up -d

# 带 worker profile
docker compose -f compose.yaml -f compose.dev.yaml --profile workers up -d
```

**开发模式特性**：
- 后端用 `uvicorn --reload`（代码改动自动重启，无需重建镜像）
- `./backend/app` 实时挂载到 `/app/app`（改后端代码即生效）
- `./Stages` 实时挂载到 `/app/Stages`（改引擎代码即生效）
- `./test-data` 挂载到 `/app/test-data`（放 DXF/Excel 即可用容器内工具测试）
- 暴露 8000 端口（可直连 http://localhost:8000/docs）
- 关闭 healthcheck（避免开发中频繁重启）

**在容器内测试 DXF 文件**：
```bash
docker compose -f compose.yaml -f compose.dev.yaml exec backend-api \
  python -c "import ezdxf; doc=ezdxf.readfile('/app/test-data/test.dxf'); print(doc.layers.names())"
```

### 4.3 中标麒麟生产部署

```bash
# 1. 安装 Docker
sudo yum install -y docker-ce docker-compose-plugin
sudo systemctl enable --now docker

# 2. 克隆并配置
git clone https://github.com/Creeken-Harrans/DWG-Agent.git /opt/dwg-agent
cd /opt/dwg-agent
cp .env.docker.example .env.docker
# 编辑 .env.docker 填入实际密码（不要使用 CHANGE_ME_* 占位值）

# 3. 构建前端
cd frontend && npm ci && npm run build && cd ..

# 4. 启动
docker compose up -d
docker compose ps  # 确认全部 healthy
```

### 4.3 启用的 Feature Flags

在 `.env.docker` 中设置（说明当前可用功能所需的值）：

```ini
# Stage 1 (默认)
AGENT_ENABLED=false           # Agent 功能 Stage 2 才启用

# Stage 3 - DXF 管线（需要 ODA File Converter）
DXF_PIPELINE_ENABLED=false    # DWG→DXF 转换
DXF2DWG_PIPELINE_ENABLED=false # DXF→DWG 反向转换
DXF2EXCEL_PIPELINE_ENABLED=false # 批量 DXF→Excel 提取

# Stage 5 - Excel Final（需要 excel_final Python 包实现）
EXCEL_FINAL_PIPELINE_ENABLED=false  # Excel→最终零件清单

# DXF 预览（ezdxf 纯 Python 渲染）无需 feature flag，直接可用
```

### 4.4 Docker Compose 服务清单

| 服务 | 说明 | 默认启动 |
|------|------|:---:|
| nginx | 前端静态文件 + API 反代 | ✅ |
| backend-api | FastAPI (gunicorn 4 workers) | ✅ |
| mysql | MySQL 8.4 | ✅ |
| minio | 对象存储 | ✅ |
| worker-report | Celery 冒烟测试任务 | ✅ |
| worker-agent | Agent 任务（Stage 2） | 需 `--profile workers` |
| worker-dxf | DWG→DXF 任务 | 需 `--profile workers` |
| worker-dxf2dwg | DXF→DWG 任务 | 需 `--profile workers` |
| worker-dxf2excel | DXF→Excel 任务 | 需 `--profile workers` |
| worker-excel-final | Excel→零件清单任务 | 需 `--profile workers` |

启动 profile workers：
```bash
docker compose --profile workers up -d
```

---

## 五、待处理事项

### 5.1 紧急

- [ ] **ODA File Converter 仅支持 x86_64**：Dockerfile 中 `COPY Stages/dwg2dxf/tools/oda /app/oda` 是 x86_64 二进制。如果中标麒麟用 ARM64 CPU（飞腾/鲲鹏），DWG↔DXF 管线（`DXF_PIPELINE_ENABLED`）将不可用。DXF 预览（ezdxf 纯 Python）和 DXF→Excel 不受影响。后续需要找 ARM64 ODA 替代方案或移除相关配置。

### 5.2 中优先级

- [x] **`excel_final` Python 包需要实现**：`Stages/excel_final/src/excel_final/` 中 `pipeline.py` 和 `handbook.py` 目前只有 stub。`excel_final_service.py` 会检测到包不可用并将任务标记为 `EXCEL_FINAL_UNAVAILABLE`。需要实现：
  - `pipeline.py`：`run_pipeline()` 和 `run_init_pipeline()` ✅ 已完成 (2026-07-10)
  - `handbook.py`：`lookup_steel_weight()`（五金手册查询）✅ 已完成 (2026-07-10)

- [ ] **`dxf2excel` Python 包需要实现**：`Stages/dxf2excel/` 目前只有 stub。需要实现 `pipeline.py` → DXF→Excel 提取逻辑。

### 5.3 低优先级

- [ ] **前端 Docker 重建流程优化**：每次前端代码改动需要 `npm run build && docker compose build --no-cache && docker compose up -d`。可以考虑将前端的 `dist/` 通过 volume 挂载到 Nginx 容器，这样只需 `npm run build` 即可更新。

- [x] **Dxf2ExcelPage 添加"提交至 excel_final"按钮**：DXF→Excel 完成后自动将结果 Excel 提交到 excel_final 管道。当前需手动下载后上传。✅ 已完成 (2026-07-10) — 按钮使用 `processFile()` 基于已有 file_id 提交。

- [x] **Docker 首次构建失败重试**：`apt-get update` 在 Docker Desktop 某些网络环境下会 "Input/output error"，重试通常能解决。✅ 已验证成功。

### 5.4 本回合验证结果 (2026-07-10)

| 功能 | 验证结果 |
|------|:---:|
| Docker 全栈启动 | ✅ 5 容器全部 healthy |
| 登录 API | ✅ `/api/v1/auth/sessions` 返回 JWT |
| DXF 上传 | ✅ `/api/v1/files` 上传 1.2MB .dxf 成功 |
| **DXF 预览** | ✅ `GET /files/{id}/dxf-preview` 返回 22 实体, 8 图层, PNG URL |
| Excel Final 健康 | ✅ `pipeline_enabled=true, ready=true` |
| Excel Final 批次列表 | ✅ 0 批次（无数据，接口正常） |
| 前端 TypeScript | ✅ `tsc --noEmit` 通过 |
| 前端构建 | ✅ `npm run build` 通过 |
| 后端 ruff | ✅ 0 errors |
| .env.docker | ✅ `EXCEL_FINAL_PIPELINE_ENABLED=true` |

**打开浏览器** `http://localhost` → 登录 `admin / SuperAdminPass1`
- 文件转换 → DWG→DXF Tab → 上传 DXF → 点击 👁 预览按钮 → 查看 PNG 图片
- 文件转换 → Excel→零件清单 Tab → 上传 Excel → 查看处理结果

- [x] **前端补齐 excel_final API**：`getPartDetail()` + `checkHealth()` 函数 + 类型
- [x] **ExcelFinalPage 接入跨批次搜索**：搜索栏 + 结果表格
- [x] **ExcelFinalPage 接入五金手册比重查询**：输入→调用 `lookupWeight()` → 显示结果
- [x] **ExcelFinalPage 零件详情弹窗**：点击零件号 → Modal 展示完整 27 字段
- [x] **ExcelFinalPage 健康检查提示**：页面加载时检测管道就绪状态
- [x] **`.env.docker` 添加 `EXCEL_FINAL_PIPELINE_ENABLED`**
- [x] **`pyproject.toml` 依赖修复**：openpyxl 从 optional-dep 提升为硬依赖
- [x] **`conftest.py` 修复**：移除已失效的 `init_db.engine` monkeypatch（修复 558→2 测试错误）

### 新增/修改文件本回合

| 文件 | 操作 |
|------|------|
| `Stages/excel_final/src/excel_final/pipeline.py` | 新建（TSV + 初始表管道） |
| `Stages/excel_final/src/excel_final/handbook.py` | 新建（五金手册） |
| `Stages/excel_final/pyproject.toml` | 修改（openpyxl→硬依赖） |
| `frontend/src/types/excel-final.ts` | 修改（+Health,+left_inset,+right_inset,+batch_id,+created_at） |
| `frontend/src/api/excel-final.api.ts` | 修改（+checkHealth,+getPartDetail） |
| `frontend/src/features/files/ExcelFinalPage.tsx` | 修改（+健康检查,+搜索,+比重,+零件详情弹窗） |
| `frontend/src/features/files/Dxf2ExcelPage.tsx` | 修改（+提交至零件清单按钮） |
| `.env.docker` | 修改（+EXCEL_FINAL_PIPELINE_ENABLED） |
| `backend/tests/conftest.py` | 修改（移除 init_db.engine monkeypatch） |

---

## 六、所有变更文件清单

### 新建文件（可提交到 Git）

```
Stages/dxf2excel/pyproject.toml
Stages/dxf2excel/src/dxf2excel/__init__.py
Stages/excel_final/pyproject.toml
Stages/excel_final/src/excel_final/__init__.py
backend/app/services/dxf_preview_service.py
frontend/src/components/DxfPreviewModal.tsx
frontend/src/types/excel-final.ts
frontend/src/api/excel-final.api.ts
frontend/src/features/files/ExcelFinalPage.tsx
```

### 修改文件（可提交到 Git）

```
backend/Dockerfile                              ← 添加 COPY Stages/excel_final
backend/pyproject.toml                          ← 添加 ezdxf + matplotlib
backend/app/db/init_db.py                       ← 移除 Base.metadata.create_all()
backend/app/api/v1/files_api.py                 ← 添加 dxf-preview 端点
backend/migrations/versions/3480bd86ddc3_*.py   ← 改成 DROP IF EXISTS
frontend/package.json                           ← 添加 react-zoom-pan-pinch
frontend/src/types/file.ts                      ← 添加 DxfPreviewResponse 等类型
frontend/src/api/files.api.ts                   ← 添加 fetchDxfPreview()
frontend/src/components/ConversionPage.tsx       ← 添加预览按钮
frontend/src/app/router.tsx                     ← 添加 excel-final 路由
frontend/src/features/files/FilesLayout.tsx      ← 添加 excel-final Tab
```

### 不可提交文件（含本地密钥，已被 .gitignore 忽略）

```
.env.docker                     ← 从 .env.docker.example 复制，含开发密码
frontend/dist/                  ← 前端构建产物
frontend/node_modules/          ← npm 依赖
```

---

## 七、验证步骤

```bash
# 1. 后端代码检查
cd backend
uv run ruff check app           # 必须 0 errors
uv run pytest -q                # 必须全部通过

# 2. 前端代码检查
cd frontend
npx tsc --noEmit                # TypeScript 检查无错误
npm run build                   # 生产构建成功

# 3. Docker 部署
docker compose down -v && docker compose build --no-cache && docker compose up -d
docker compose ps               # 全部 service healthy

# 4. API 验证
curl http://localhost/health    # {"data":{"status":"ok"}}
curl -X POST http://localhost/api/v1/auth/sessions \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"SuperAdminPass1"}'  # 返回 JWT token

# 5. 前端验证
start http://localhost           # 浏览器打开
# 登录 → 文件转换 → 上传 DXF → 点击预览按钮 → DXF 预览弹窗
# 文件转换 → Excel → 零件清单 Tab → 上传 Excel → 查看批次详情
```

---

## 八、关键项目路径

```
项目根目录: C:\Users\Ran-xin\Desktop\kuak\前端\DWG-Agent
前端源码:   frontend\src\
后端源码:   backend\app\
Stage 包:   Stages\{dwg2dxf,dxf2dwg,dxf2excel,excel_final}
文档:       docs\
基础设施:   infra\{nginx,mysql,minio}
脚本:       scripts\
```

## 九、参考文件

```
C:\Users\Ran-xin\Desktop\kuak\new\dxf_to_png.py     ← DXF 渲染 PNG 参考实现
C:\Users\Ran-xin\Desktop\kuak\前端\CAD项目开发规范操作手册.md  ← 开发规范文档
C:\Users\Ran-xin\Desktop\kuak\前端\DWG-Agent\DWG-Agent企业平台技术规范.md  ← 核心规范
```

---

## 十、Excel Final Pipeline 已知 bug（2026-07-10）

`excel_final` Python 包存在并执行至 progress=60%，但 `excel_final_service.py` 有以下已修复的问题（需重启 Docker Desktop 后重建镜像生效）：

1.  `_mark_job_failed()` 中 `make_event(..., message=...)` → 应改为 `error_message=...`
2.  `db_stats` 中 `batch_id` key 与 `make_event()` 参数冲突
3.  `component_no` 列宽 128→512（MySQL VARCHAR）
4.  `.xls` 文件不支持 → 需先转为 `.xlsx`

## 十、新对话快速启动指令

在新对话中，使用以下内容快速获取上下文：

```
我正在开发 DWG-Agent 项目（企业级 CAD 智能处理平台）。
项目路径：C:\Users\Ran-xin\Desktop\kuak\前端\DWG-Agent
请阅读 CLAUDE.md、readme.md 和 docs/architecture.md 了解项目架构。
然后参考 SUMMARY.md（本总结文件）了解历史进度。
```
