# DXF 全类型分类、数据库标注与文件夹下载设计

## 目标

完善 Linux 生产流程的第二阶段“DXF 分类与分流”：

- PX 等已知但当前未登记的钢结构类型进入正式类型目录。
- 标题栏证据明确的新字母前缀自动识别、分类和分流，不依赖穷举类型表。
- 每张 DXF 的分类结论、规格、类型来源、证据状态和下一阶段可用性写入数据库。
- 下一阶段只通过分类模块的数据库接口读取，不扫描目录、不读取 JSON/CSV、不重复解析 DXF。
- 前端按分类文件夹展示结果，点击文件夹查看分页明细。
- 只有不能确定或不能读取的文件触发预警；自动发现的新类型不作为错误。
- 支持下载任意单类 DXF ZIP 和全部分类 DXF ZIP，两者都不包含 JSON、CSV、DWG 或其他产物。

本设计保留分类 JSON/CSV 作为后台审计产物和完整生产归档的一部分，但分类页面不显示它们，分类专用下载也不包含它们。

## 已选方案

采用“版本化分类器 + 数据库权威分类账本 + 分类组投影”。

分类器负责输出完整且版本化的逐图语义；后端把语义和正式输出文件绑定到分类账本；分类组由逐图记录实时聚合，不新增容易与逐图记录失去一致性的文件夹表。前端和下一阶段都读取数据库，不把物理目录名当作业务真相。

未采用的方案：

- 只扩展静态类型列表：无法覆盖未来新类型，也不能满足下一阶段的稳定数据库合同。
- 以输出文件夹为权威：文件夹重命名、重复执行和对象存储迁移会破坏业务关联。
- 所有未知前缀统一待确认：与“自动识别出来的新类型也要分类”的业务目标冲突。

## 分类语义

### 已登记类型

分类器维护版本化的钢结构类型目录，补充 PX，并覆盖当前已知的板材、焊接组合截面、H/I/T 型、角钢与槽钢、空心截面、管材和棒材前缀。目录的用途是提供正式类型来源和可审计的类型族信息，不作为自动分类能力的上限。

已登记类型输出：

```text
disposition=classified
type_source=catalog
part_type=<规范前缀>
next_stage_eligible=true
```

### 自动发现类型

未登记前缀只有同时满足以下条件才自动分类：

1. 证据来自合格标题栏或零件信息表的截面字段。
2. 标签和值位于同一块实例路径。
3. 相邻区域只形成一个唯一规格事实。
4. 前缀由 2 至 12 个 ASCII 字母组成。
5. 前缀后存在可解析的数值尺寸主体。
6. 规格不含路径字符、材料牌号尾字母、比例或说明语句。

自动发现类型输出：

```text
disposition=classified
type_source=auto_discovered
part_type=<自动发现的规范前缀>
next_stage_eligible=true
diagnostics=PROFILE_TYPE_AUTO_DISCOVERED
```

`PROFILE_TYPE_AUTO_DISCOVERED` 是信息性诊断，不触发前端黄色或红色预警。

单字母未知前缀不得自动建类。已登记的 `H`、`I`、`T`、`L`、`C`、`U`、`Z` 等单字母类型仍按正式目录识别。`Q355B`、`1:10`、纯数字、带路径字符的文本和说明文字不得成为类型。

### 不确定和无法读取

以下结果不允许下一阶段读取：

| 处置 | 诊断 | 下一阶段可用 |
| --- | --- | --- |
| `review_required` | `TITLE_FIELD_MISSING` | 否 |
| `review_required` | `TITLE_VALUE_MISSING` | 否 |
| `review_required` | `TITLE_VALUE_CONFLICT` | 否 |
| `unreadable` | `DXF_READ_FAILED` | 否 |

部分文件待确认时，已确定文件仍保持可用；预警记录不会混入下一阶段输入。若一个可执行阶段没有任何可用输入，服务端必须明确阻止执行，不能把空输入当作成功。

## 分类器版本与输出合同

分类器升级为 `1.2.0`，同步升级 Python 包版本、CLI schema、报告 schema、后端精确依赖和数据库算法版本。

逐图报告在现有字段基础上增加：

```json
{
  "source_name": "member_001_拆板前.dxf",
  "disposition": "classified",
  "part_type": "PX",
  "profile_raw": "PX300*150*8",
  "profile_normalized": "PX300*150*8",
  "type_source": "catalog",
  "group_key": "type:PX",
  "next_stage_eligible": true,
  "diagnostics": ["TITLE_PROFILE_PROVED"],
  "candidates": [],
  "source_metadata": {},
  "output_directory": "project_PX_dxf"
}
```

保留 DXF 批处理目录、JSON 报告和 CSV 清单的现有事务语义。输出 DXF 仍是预处理后输入 DXF 的逐字节副本，分类不得修改几何或文字内容。

## 数据库设计

继续使用 `dxf_classification_runs` 表记录一次版本化执行，使用 `dxf_classification_items` 表作为每张 DXF 的权威分类记录。

`dxf_classification_items` 墺加：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `profile_raw` | nullable string | 标题栏规格原文 |
| `profile_normalized` | nullable string | 规范化后的完整规格 |
| `type_source` | nullable string | `catalog`、`auto_discovered` 或 `legacy` |
| `group_key` | non-null string | `type:PX`、`status:review_required` 等稳定分类键 |
| `next_stage_eligible` | non-null boolean | 是否允许下一阶段读取 |

继续保留并使用：

- `drawing_id`
- `source_file_id`
- `output_file_id`
- `source_name`
- `output_name`
- `output_directory`
- `disposition`
- `part_type`
- `diagnostics_json`
- `evidence_json`

数据库增加以下组合索引：

```text
(run_id, group_key)
(run_id, next_stage_eligible)
(run_id, part_type)
```

一致性规则：

- 新执行中，只有 `classified` 且正式输出文件已经登记的记录可以设为 `next_stage_eligible=true`。
- `review_required` 和 `unreadable` 必须为 `false`。
- `type_source=auto_discovered` 不降低下一阶段资格。
- `group_key` 是接口和聚合键；`output_directory` 只是可变的展示/审计信息。

### 历史数据迁移

迁移不删除或重写现有分类账本和文件：

- 已有 `classified`、`part_type` 和有效 `output_file_id` 的记录回填对应 `type:<part_type>`，并标记下一阶段可用。
- `review_required` 和 `unreadable` 分别回填稳定状态组，且不可用于下一阶段。
- 能从现有证据安全恢复的规格字段可以回填；不能恢复的字段保持空值。
- 无法证明目录来源的历史已分类记录使用 `type_source=legacy`，不得伪造为正式目录或自动发现。
- 迁移必须支持空数据库建库、现有数据库升级和重复执行保护。

## 下一阶段内部接口

分类模块公开稳定的只读接口：

```text
list_next_stage_inputs(workflow_id)
```

返回类型化记录：

```text
drawing_id
part_type
profile_normalized
type_source
source_file_id
output_file_id
classifier_version
```

接口只返回最新权威分类运行中 `next_stage_eligible=true` 且输出文件仍可用的记录。调用方不得：

- 扫描分类输出目录；
- 解析文件夹名获取类型；
- 读取分类 JSON/CSV；
- 重新运行标题栏规则；
- 只靠原始文件名维持关联。

## HTTP 接口

### 分类摘要与文件夹

保留：

```text
GET /api/v1/workflows/{workflow_id}/dxf-classification
```

响应返回运行摘要和分类组，不再要求前端下载或解析审计文件：

```json
{
  "status": "completed_with_review",
  "classifier_version": "1.2.0",
  "input_count": 120,
  "classified_count": 118,
  "review_required_count": 2,
  "unreadable_count": 0,
  "groups": [
    {
      "group_key": "type:PX",
      "label": "PX",
      "part_type": "PX",
      "type_source": "catalog",
      "disposition": "classified",
      "count": 12,
      "warning_count": 0,
      "total_size_bytes": 123456
    }
  ]
}
```

审计报告和清单仍可保留在后端响应模型或内部 artifact 中以兼容现有能力，但分类前端不展示它们。

### 文件夹明细

新增：

```text
GET /api/v1/workflows/{workflow_id}/dxf-classification/groups/{group_key}?page=1&page_size=20
```

明细分页返回：

- DXF 文件名；
- 零件类型；
- 规格原文和规范化规格；
- 类型来源；
- 处置状态；
- 已翻译所需的稳定诊断码；
- 文件大小。

响应不暴露文件 ID、结果 ID、数据库主键、MinIO bucket/key 或证据 JSON 原文。

### DXF 专用下载

新增：

```text
GET /api/v1/workflows/{workflow_id}/dxf-classification/groups/{group_key}/download-archive
GET /api/v1/workflows/{workflow_id}/dxf-classification/download-archive
```

第一条只下载指定分类组的正式输出 DXF。第二条下载本次分类的全部正式输出 DXF，并保留类别目录结构。待确认和无法读取组也可下载，便于人工处理。

两种 ZIP 均不得包含：

- JSON；
- CSV；
- DWG；
- 分类报告；
- 分类清单；
- 其他工作流阶段 artifact。

端点复用现有 Files 权限检查、注册文件 ZIP 构建、outbound transfer、审计日志、Blob 错误和流式临时文件清理。稳定处理非法分类键、空分类组、缺失文件和项目越权。

现有完整工作流归档和通用阶段归档继续承担审计/交付用途；分类页面的下载按钮只能调用 DXF 专用端点。

## 前端设计

“DXF 分类与分流”采用与现有工作流一致的工业生产工作台风格，保留阶段状态和执行入口，把完成结果改为文件夹视图。

### 顶部摘要

显示：

- 分类状态和分类器版本；
- 输入、已分类、待确认、无法读取计数；
- “下载全部 DXF”主按钮；
- 刷新动作；
- 当前阶段允许时的开始或重试动作。

历史阶段只允许查看和下载，不允许重新执行。

### 文件夹视图

每个分类组显示为可点击文件夹卡片：

```text
PX          12 张   内置类型       下载本类
BH          67 张   内置类型       下载本类
XY           4 张   自动发现       下载本类
待确认       2 张   需要处理       下载本类
无法读取     1 张   读取失败       下载本类
```

待确认和无法读取置顶并使用警示样式；正常类型按类型名称自然排序。自动发现类型正常参与排序，显示信息性“自动发现”标签，不显示错误预警。

点击文件夹主体打开右侧明细抽屉。下载按钮与打开动作分离，并阻止事件冒泡，避免误下载或误打开。

### 明细抽屉

分页显示：

- DXF 文件名；
- 零件类型；
- 规格原文；
- 规范化规格；
- 类型来源；
- 分类状态；
- 中文诊断；
- 文件大小。

不显示内部 ID、JSON/CSV、对象存储位置或证据 JSON 原文。文件名和规格允许换行，分页状态与当前文件夹绑定。

### 预警

只有以下诊断触发预警：

- `TITLE_FIELD_MISSING`
- `TITLE_VALUE_MISSING`
- `TITLE_VALUE_CONFLICT`
- `DXF_READ_FAILED`

顶部预警显示待处理总数，并提供“查看待确认”“查看无法读取”快捷动作。逐图明细使用稳定诊断码映射中文说明。

### 下载体验

- 顶部“下载全部 DXF”调用全部 DXF 专用 ZIP。
- 每个文件夹“下载本类”调用分类组 ZIP。
- 请求期间按钮显示加载状态并防止重复提交。
- Blob 错误通过统一错误解析展示后端中文原因、错误码和 request ID。
- 页面不提供 JSON/CSV 下载入口。

## 状态和错误处理

- 未执行：说明冻结 DXF 已就绪，并在当前阶段开放开始操作。
- 执行中：显示权威 Job 进度并轮询摘要。
- 完成：显示文件夹、统计和下载。
- 部分待确认：显示预警；已确定记录仍可供下一阶段读取。
- 全部不能确定：显示完成但需处理；下一阶段获取不到输入时由服务端明确拒绝。
- 失败：显示稳定错误码、原因和当前阶段重试动作。
- 历史查看：允许浏览和下载，禁止执行或覆盖。

下载和查询不改变工作流阶段。分类执行按 Job attempt 隔离，旧 attempt 不得覆盖新结果。

## 测试策略

所有行为修改遵循测试先行。

### 分类器

- PX 作为正式目录类型。
- 类型目录中的每一种前缀均有参数化样本。
- `XY250*120*8` 等安全新前缀自动分类。
- 自动发现字段、诊断和下一阶段资格完整。
- `Q355B`、比例、纯数字、路径字符、说明文字和未知单字母前缀不能自动建类。
- 标题字段缺失、规格冲突和读取失败保持 fail-closed。
- 输入输出 DXF SHA-256 一致。
- 现有真实验证项目的分类数量和类型分布无无法解释的回归。

### 数据库与后端

- 空库迁移和现有库升级均通过。
- 历史回填遵守 `legacy` 和资格规则。
- 分类 Job 真实持久化全部新字段。
- 分类组统计与逐图记录一致。
- 下一阶段接口只返回合格记录。
- 明细分页不暴露内部 ID 或审计文件。
- 单类 ZIP 只包含该组 DXF。
- 全部 ZIP 包含所有正式输出 DXF，但不含 JSON、CSV、DWG。
- 实际检查 ZIP 成员路径、数量、扩展名、DXF 头和 SHA-256。
- 权限、非法组、空组和缺失文件返回稳定错误。
- 下载生成 outbound transfer 和审计记录。

### 前端

- 完成结果默认显示文件夹而不是长表。
- 点击文件夹打开分页明细。
- 待确认和无法读取触发预警。
- 自动发现类型不触发错误预警。
- 页面不存在 JSON/CSV 下载入口。
- 单类和全部下载调用各自 DXF 专用端点。
- 历史阶段不能执行或重试。
- Blob 错误显示后端原因和请求 ID。
- 键盘可以聚焦、打开文件夹和下载。

## 发布与验证

发布同步更新：

- 分类器代码、版本、schema、锁文件和发行文档；
- 后端依赖、迁移、模型、接口、任务持久化和测试；
- 前端类型、API、页面、样式和浏览器测试；
- OpenAPI、模块 README、当前验证文档和根能力说明。

最终完成门：

1. 分类器测试、静态编译和真实 DXF 样本通过。
2. 空数据库和现有数据库迁移路径通过。
3. 后端分类、数据库、权限、ZIP 和完整相关回归通过。
4. 前端类型检查、构建和浏览器交互通过。
5. 实际启动服务完成一次真实分类。
6. 数据库逐图核对类型、来源、规格和下一阶段资格。
7. 浏览器实际打开多个分类文件夹和预警组。
8. 实际下载 PX 单类 ZIP 和全部 DXF ZIP，确认不含 JSON/CSV。
9. 文档、版本、代码、产物和 Git 状态一致。

当前工作区已有两个阶段导航设计/计划提交、用户未提交的阶段 ZIP 测试修改，以及未跟踪的 `Stages/excel_final/data/` 和 `output/`。实施必须保留这些内容；与本设计冲突的旧分类 ZIP 测试按新的 DXF 专用下载合同协调，其他用户内容不回退、不覆盖。
