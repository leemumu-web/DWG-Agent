# 异孔折判断 — 分支集成说明

## 概述

本文档说明如何将"异孔折判断"（DXF板件图形分类）项目作为 DWG-Agent 主仓库的一个功能分支进行开发和维护，满足以下需求：

- **随时改动分支内容，不影响主仓库**
- **能并入主仓库**
- **前端后端写好对齐**
- **并入后依旧能够随时改动**
- **能使用主仓库功能**

---

## 一、仓库与分支结构

### 1.1 当前状态

| 项目 | 说明 |
|------|------|
| **工作目录** | `F:\pycharm\projectweizhi\1\异孔折判断_项目\` |
| **主仓库** | `https://github.com/Creeken-Harrans/DWG-Agent.git` |
| **远程名称** | `origin` |
| **本地 main 分支** | 跟踪 `origin/main`，包含 DWG-Agent 全部代码 |
| **本地 feature 分支** | `feature/plate-hole-bend-classifier`（当前所在分支） |
| **分支状态** | **仅在本地，未推送到远程**（等你确认后推送） |

### 1.2 分支关系图

```
origin/main (DWG-Agent 主仓库)
    │
    ├── main (本地，= origin/main)
    │
    └── feature/plate-hole-bend-classifier ★ 当前工作分支
            │
            ├── Stages/yikongzhe/              ← 异孔折判断 Stage（核心算法）
            ├── backend/app/modules/plate_classification/  ← 后端 API 模块
            └── frontend/src/features/plate-classification/ ← 前端页面
```

### 1.3 分支策略

```
main                              feature/plate-hole-bend-classifier
  │                                        │
  │  定期 rebase/merge 同步主仓库更新 ←─────│  日常开发在这里进行
  │                                        │
  │──── 审核通过后 merge feature ────────→│  MR/PR 合并到 main
  │                                        │
  ▼                                        ▼
 生产分支                                开发分支
```

---

## 二、新增/修改的文件清单

### 2.1 新增文件

#### Stage 层（独立 CLI 工具）

```
Stages/yikongzhe/
├── pyproject.toml                    # 包配置（依循主仓库 Stage 规范）
├── README.md                         # Stage 说明
├── docs/
│   ├── API.md                        # 原项目 API 文档
│   └── DATA_FLOW.md                  # 原项目数据流文档
└── src/yikongzhe/
    ├── __init__.py                   # 导出 Part, PartClassification, classify_directory
    ├── __main__.py                   # CLI 入口（uv run python -m yikongzhe）
    ├── models.py                     # 数据模型（8 种分类枚举）
    ├── dxf_reader.py                 # DXF 解析（含 REGION ACIS 支持）
    ├── geometry.py                   # 几何分析（外轮廓/矩形/孔洞检测）
    ├── bend_detector.py              # 折弯特征检测
    ├── classifier.py                 # 分类编排（三步判断 → 8 种类别）
    └── excel_writer.py               # Excel 输出
```

#### 后端模块

```
backend/app/modules/plate_classification/
├── __init__.py                       # 模块说明
├── models.py                         # DB 模型（PlateClassificationRun + Item）
├── schemas.py                        # Pydantic 请求/响应模型
├── router.py                         # FastAPI 路由（触发/查询分类）
├── tasks.py                          # Celery 异步任务
└── execution.py                      # CLI 子进程调用封装
```

#### 前端页面

```
frontend/src/features/plate-classification/
└── index.tsx                         # 分类触发 + 结果查看页面
```

### 2.2 修改的注册文件

| 文件 | 修改内容 |
|------|----------|
| `backend/app/bootstrap/router.py` | 注册 `plate_classification_router` 到 `/api/v1/plate-classification` |
| `backend/app/bootstrap/model_registry.py` | 注册 `plate_classification` 模型到 SQLAlchemy |
| `backend/app/bootstrap/task_registry.py` | 注册 `plate_classification` Celery 任务 |
| `frontend/src/app/router.tsx` | 添加 `/files/plate-classification` 路由 |

---

## 三、日常开发工作流

### 3.1 在分支上开发（不影响主仓库）

```bash
# 1. 确保在 feature 分支上
git branch
# 应显示: * feature/plate-hole-bend-classifier

# 2. 如果误切换到 main，切回来
git checkout feature/plate-hole-bend-classifier

# 3. 日常修改代码
# 编辑 Stages/yikongzhe/src/yikongzhe/ 下的文件
# 编辑 backend/app/modules/plate_classification/ 下的文件
# 编辑 frontend/src/features/plate-classification/ 下的文件

# 4. 提交修改
git add <修改的文件>
git commit -m "描述你的修改"

# 5. 这些提交只在 feature 分支上，main 分支完全不受影响
```

### 3.2 独立测试 Stage

```bash
cd Stages/yikongzhe
uv sync
uv run python -m yikongzhe <你的DXF目录> -o 测试输出.xlsx -v
```

### 3.3 从主仓库同步更新

当 DWG-Agent 主仓库有更新时，同步到本地：

```bash
# 1. 确保当前分支的修改已提交
git status

# 2. 切换到 main，拉取远程更新
git checkout main
git pull origin main

# 3. 切回 feature 分支，合并 main 的更新
git checkout feature/plate-hole-bend-classifier
git merge main

# 4. 解决可能的冲突后提交
```

### 3.4 切换回主仓库代码

```bash
# 切换到 main 分支，查看/使用 DWG-Agent 完整功能
git checkout main

# 切回来继续开发
git checkout feature/plate-hole-bend-classifier
```

---

## 四、前后端对齐说明

### 4.1 架构层次对应

```
用户操作（前端）
    │ POST /api/v1/plate-classification/runs
    ▼
FastAPI 路由（router.py）
    │ 创建 Job + PlateClassificationRun
    │ 入队 Celery 任务
    ▼
Celery Worker（tasks.py）
    │ 调用 execution.py
    ▼
子进程 CLI（execution.py）
    │ uv run python -m yikongzhe <input_dir> -o <output.xlsx>
    ▼
Stage CLI（Stages/yikongzhe/）
    │ dxf_reader → geometry → bend_detector → classifier → excel_writer
    ▼
结构化结果（DB 记录 + Excel 文件）
    │ GET /api/v1/plate-classification/runs/{id}
    ▼
前端展示（PlateClassificationPage.tsx）
```

### 4.2 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/plate-classification/runs` | 触发板件分类任务 |
| `GET` | `/api/v1/plate-classification/runs` | 列出分类运行历史（支持分页） |
| `GET` | `/api/v1/plate-classification/runs/{run_id}` | 获取单次分类详情（含逐板结果） |

### 4.3 前端页面

- **路由**: `/files/plate-classification`
- **功能**:
  1. 表单：输入 DXF 目录路径 + 项目名称 → 触发分类
  2. 运行列表：查看历史分类记录及状态
  3. 详情面板：查看某次运行的逐板分类结果（8 种类别彩色 Tag）

### 4.4 数据库表

`plate_classification_runs` — 分类运行记录
| 字段 | 类型 | 说明 |
|------|------|------|
| id | PK | 主键 |
| workflow_run_id | FK | 关联工作流 |
| project_id | FK | 关联项目 |
| job_id | FK | 关联任务 |
| job_attempt | int | 任务尝试次数（支持重试） |
| status | str | pending/running/completed/failed |
| input_directory | str | DXF 输入目录 |
| input_count | int | DXF 文件数 |
| classified_count | int | 分类板件总数 |
| category_counts_json | JSON | 8 种类别统计 |

`plate_classification_items` — 逐板分类结果
| 字段 | 类型 | 说明 |
|------|------|------|
| id | PK | 主键 |
| run_id | FK | 关联运行记录 |
| part_name | str | 板件名称 |
| dxf_file | str | 来源 DXF |
| category | str | 最终类别（方/异/方孔/...） |
| shape | str | 外形（方/异） |
| hole | str | 孔洞（有孔/无孔） |
| bend | str | 折弯（有折/无折） |

---

## 五、并入主仓库流程

### 5.1 前置条件

并入主仓库前需要完成的检查和修改（建议你的审核清单）：

- [ ] Stage 算法正确性验证（BH 42/42 + BOX 63/63 全部 OK）
- [ ] Stage pyproject.toml 版本号确认
- [ ] 后端 API 本地测试通过
- [ ] 前端页面本地可访问
- [ ] 数据库迁移脚本（如需要新增表到生产环境）
- [ ] 代码风格与主仓库一致（Ruff 检查通过）
- [ ] 测试用例覆盖核心逻辑

### 5.2 推送分支

确认无误后，推送到远程仓库：

```bash
# 推送 feature 分支到远程
git push origin feature/plate-hole-bend-classifier
```

### 5.3 创建 Pull Request / Merge Request

在 GitHub 上创建 PR：`feature/plate-hole-bend-classifier` → `main`

### 5.4 合并后继续开发

合并到 main 后，可以：
```bash
# 1. 更新本地 main
git checkout main
git pull origin main

# 2. 切回 feature 分支（合并后仍然可以继续改动）
git checkout feature/plate-hole-bend-classifier

# 3. 继续开发，再次提交
git commit -m "新的修改"
git push origin feature/plate-hole-bend-classifier
```

---

## 六、关键文件快速索引

| 你想做什么 | 改哪个文件 |
|------------|-----------|
| 修改分类算法逻辑 | `Stages/yikongzhe/src/yikongzhe/classifier.py` |
| 修改几何分析（方/异判断） | `Stages/yikongzhe/src/yikongzhe/geometry.py` |
| 修改折弯检测算法 | `Stages/yikongzhe/src/yikongzhe/bend_detector.py` |
| 修改 DXF 解析（实体分配等） | `Stages/yikongzhe/src/yikongzhe/dxf_reader.py` |
| 修改 Excel 输出格式 | `Stages/yikongzhe/src/yikongzhe/excel_writer.py` |
| 修改数据模型 | `Stages/yikongzhe/src/yikongzhe/models.py` |
| 修改 CLI 入口参数 | `Stages/yikongzhe/src/yikongzhe/__main__.py` |
| 修改后端 API 接口 | `backend/app/modules/plate_classification/router.py` |
| 修改后端任务执行逻辑 | `backend/app/modules/plate_classification/tasks.py` 和 `execution.py` |
| 修改数据库表结构 | `backend/app/modules/plate_classification/models.py` |
| 修改前端页面 | `frontend/src/features/plate-classification/index.tsx` |
| 调整依赖版本 | `Stages/yikongzhe/pyproject.toml` |

---

## 七、常见问题

### Q: 我怎么能确认改动只在分支上，不影响 main？
```bash
# 查看 main 和 feature 的差异
git diff main..feature/plate-hole-bend-classifier --stat

# 切换到 main 确认没有任何我们的代码
git checkout main
ls Stages/yikongzhe/    # 应该不存在
```

### Q: 如果我改乱了怎么办？
```bash
# 放弃所有未提交的本地修改
git checkout -- .
# 或回到上一次提交的状态
git reset --hard HEAD
```

### Q: 主仓库更新后我如何同步？
```bash
git checkout main
git pull origin main
git checkout feature/plate-hole-bend-classifier
git merge main
# 解决冲突（如果有）后 git commit
```

---

## 八、附录：原项目核心信息

### 分类规则（8 种）

| 类别 | 外轮廓 | 孔洞 | 折弯 |
|------|--------|------|------|
| 方 | 矩形 | 无 | 无 |
| 异 | 不规则 | 无 | 无 |
| 方孔 | 矩形 | 有 | 无 |
| 异孔 | 不规则 | 有 | 无 |
| 方折 | 矩形 | 无 | 有 |
| 异折 | 不规则 | 无 | 有 |
| 方孔折 | 矩形 | 有 | 有 |
| 异孔折 | 不规则 | 有 | 有 |

### 测试验证结果

```
BH:  42/42 OK, 0 mismatches
BOX: 63/63 OK, 0 mismatches
```

### 原项目 changelog

详见 `Stages/yikongzhe/CHANGELOG.md`
