# DWG-Agent 企业级 CAD 智能处理平台 · 本机开发骨架

本仓库是基于《DWG-Agent 企业平台技术规范》落地的第一版工程框架。
当前阶段明确 **不使用 Docker**，后端 Python 固定为 **3.12**，优先完成本机可运行的前后端骨架、RESTful API、数据库模型、文件上传、任务占位执行、审计与权限边界。

## 当前实现范围

已实现：

- FastAPI 后端工程骨架
- SQLAlchemy 2.x ORM 模型
- 本机 SQLite 默认开发数据库，后续可切 MySQL
- RESTful `/api/v1` 路由结构
- 登录、当前用户、用户管理、角色、权限
- 项目、项目成员
- 文件上传、文件元数据、短期 download-url 与本机下载端点
- 图纸、版本
- 任务创建、任务步骤、结果、复核、审计日志
- Agent / DXF / ZWCAD 处理边界占位
- React + TypeScript + Vite 前端骨架
- API client、路由、基础页面、权限守卫占位
- 本机启动脚本与开发文档

暂不实现：

- Agent 内部 LangGraph 调用
- DWG → DXF 转换与 ezdxf 解析
- Windows ZWCAD Worker 实际调用
- Docker Compose / Nginx / MinIO / Redis / Celery 生产编排

## 目录结构

```text
complete_framework/
├── README.md
├── .env.example
├── Makefile
├── docs/
├── backend/
├── frontend/
├── agents/
├── cad-worker/
├── infra/
└── scripts/
```

## 本机启动

后端：

```bash
cd backend
uv python install 3.12  # 如果本机尚未安装 Python 3.12
uv sync --locked
cp ../.env.example .env
uv run python -m app.db.init_db
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端：

```bash
cd frontend
npm ci
npm run dev
```

默认账号：

```text
username: admin
password: admin123456
```

## 开发原则

- 初始阶段不依赖 Docker。
- 本机 SQLite 只用于开发验证，生产使用 MySQL。
- 本地文件系统只用于开发验证，生产替换 MinIO/NAS。
- RESTful API 路径以 `/api/v1` 为准。
- Agent、DXF、CAD Worker 均先保留边界，不把算法逻辑塞进 API 层。
