# DXF 分类分流阶段实施计划

1. 以失败测试固定十阶段模板、执行权限/幂等、分类器 I/O 适配和数据库关系。
2. 引入最小化 `steel_dxf_classifier` 1.1.0 包源码与锁定依赖，补齐 Docker/Compose 队列部署。
3. 新增分类 run/item 模型、Alembic 迁移、API schema 和查询接口。
4. 实现分类 Worker：冻结输入下载、CLI 调用、输出核验、MinIO 写入、MySQL/Artifact/Result 登记和 Job 状态收敛。
5. 将 `dxf_classification` 接入工作流模板和通用 execution API，保留下一阶段拆板留白。
6. 新增前端 API/type/专用面板，显示启动、重试、进度、汇总、逐图结果和下载。
7. 同步 OpenAPI、架构、部署和验证文档；执行单元、API、迁移、前端契约、构建、Playwright、MySQL/MinIO 实链验证并分步提交。
