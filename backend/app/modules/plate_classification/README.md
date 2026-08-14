# plate_classification — 板件图形分类（异孔折判断）

对拆板后的 DXF 目录执行批量图形分类，判断每块板的外轮廓形状、内部孔洞与折弯特征，归类为方/异、有孔/无孔、有折/无折的 8 种类别。后端登记 MySQL 运行记录与条目，并通过 Celery 任务调用 yikongzhe Stage 计算，前端提供触发与结果查看面板。

## 边界

- 本模块只负责运行记录的持久化、任务入队与结果查询；真正的分类算法由 `Stages/yikongzhe` Stage 包实现，本模块不能内联几何/分类逻辑。
- HTTP 路由、Celery 任务与 MySQL 表由本模块拥有，未实现跨模块直接读写他模块的数据库表。

## 源文件

- `models.py` — `PlateClassificationRun` 与 `PlateClassificationItem` 数据表模型
- `schemas.py` — API 读写模型（运行/条目/触发请求/分页）
- `router.py` — 触发、列表、详情 HTTP 路由
- `tasks.py` — Celery 任务 `classify_plates`，编排 yikongzhe 执行并回写结果
- `execution.py` — 调用 yikongzhe Stage 的执行适配层
