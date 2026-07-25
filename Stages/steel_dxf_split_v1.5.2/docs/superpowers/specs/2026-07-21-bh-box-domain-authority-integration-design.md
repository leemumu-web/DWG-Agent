# BH/BOX 领域权威融合架构设计

> 已废弃：用户最终选择 BH v1.5.2 主体、Project2 BOX 子包和薄判型分发，
> 不采用本文的 `application/adapters` 结构。

日期：2026-07-21
状态：待用户书面审阅
目标框架版本：`2.0.0`

## 1. 目标

把当前工程整理为一个统一的 DXF 拆板模块，同时保留两个平级且互不改写的领域算法权威：

- BH：`steel-dxf-split v1.5.2`，tag commit
  `302dd73fa4b92f1d39486063c15dd49227e58b8a`；
- BOX：已经完成压缩融合的 Project2 `box-dxf-split v1.0.0`，tag commit
  `5a2be1a82eb7235bcff62d97a13d2937f9ad026b`。

统一模块只负责判型、来源授权、唯一领域适配器路由、跨领域结果包络和批次事务。
它不得解释、投票、拼接或覆盖 BH/BOX 的几何、角色、孔归属、ManufacturingIR、
ProofReport、writer 或保存后验证结论。

本设计参考
`D:\Documents\00_Inbox\Downloads\架构设计.txt` 中“分类与语义提取、固定自动流程、
独立校验、人工任务分流和批次完成屏障”的原则。本仓只产生机器可读处置；MySQL、
Celery、MinIO 和人工任务生命周期属于上层系统，不进入本阶段实现。

## 2. 非目标

- 不重新设计 BH v1.5.2 的领域算法、证明条件或输出几何；
- 不重新设计已经验收的 Project2 BOX 核心；
- 不保留旧 BOX 求解器、fallback、双跑、投票或结果拼接；
- 不建立共享 BH/BOX 几何 IR；
- 不把所有警告、配置错误或系统故障统一送入人工工程审核；
- 不为未来未知型材族公开插件注册表或 backend 选择器；
- 不在本阶段接入数据库、消息队列、对象存储或 CAD Agent；
- 不执行 commit、push、主干合并或发布。

## 3. 当前架构问题

当前包根同时存在旧 BH、旧通用拆板文件和统一入口，而 BOX 是一个完整子包。结果是：

- BH 看起来是“主架构”，BOX 看起来是“被放进去的项目”；
- `pipeline.py`、`cli.py` 和 `batch_cli.py` 直接知道两套领域配置与报告字段；
- 旧通用模型仍占据包根，理解一个概念需要在多组新旧文件之间跳转；
- BH 与 BOX 的路由词、路径字段、版本字段不一致，调用方被迫理解领域内部细节。

删除统一入口后，判型、授权、唯一内核选择、错误分类和工件闭包会散落到所有调用方，
因此这些职责应收拢为一个真正的深模块，而不是继续增加转发文件。

## 4. 最终模块结构

```text
src/steel_dxf_split/
├── __init__.py
├── cli.py
├── batch_cli.py
│
├── application/
│   ├── __init__.py
│   ├── contracts.py
│   ├── classification.py
│   ├── compiler.py
│   ├── batch.py
│   └── adapters.py
│
├── bh/
│   ├── __init__.py
│   ├── bh_*.py
│   ├── pipeline.py
│   ├── cli.py
│   ├── batch_cli.py
│   ├── dxf_io.py
│   ├── dxf_preview.py
│   ├── artifact_io.py
│   ├── process_control.py
│   ├── layered_*.py
│   ├── weld_allowance*.py
│   └── release_evidence/
│
└── box/
    ├── __init__.py
    ├── compiler.py
    ├── contracts.py
    ├── frontend.py
    ├── analysis.py
    ├── solve.py
    ├── manufacturing.py
    ├── validation.py
    ├── delivery.py
    └── Project2 其余领域模块
```

依赖方向固定为：

```text
CLI / batch CLI
       ↓
application
   ↙       ↘
BH adapter  BOX adapter
   ↓          ↓
bh/          box/
```

- `bh/` 与 `box/` 永不互相导入；
- 两个领域包不得反向导入 `application/`；
- `application/` 只能经固定适配器调用领域公开入口；
- 包根不保留旧 `bh_*.py` 兼容转发文件；
- 包根旧通用拆板文件在依赖与测试证明无调用后删除，不形成第三套算法。

## 5. 领域来源与冻结规则

### 5.1 BH

`bh/` 以 v1.5.2 为唯一来源。BH 原生能力保持不变，包括：

- 八阶段编译链；
- 来源契约、SourceIR、DrawingGraph、候选搜索和完整性证明；
- `BH-MANUFACTURING-IR-1.1`；
- writer、保存后 DXF 校验和三路物理处置；
- `BH-COMPILATION-REPORT-1.4`；
- `BH-BATCH-MANIFEST-1.4`；
- PNG 预览、分层检查、焊接余量和 release evidence；
- 当前可信 20 图的 `18 auto_accepted / 2 review_required / 0 rejected`。

迁入子包时只允许三类已知包路径适配：

1. `bh/batch_cli.py` 的子进程模块路径；
2. `bh/bh_release_evidence.py` 的包资源定位；
3. `bh/layered_cli.py` 的 worker 模块路径。

这些差异必须逐项登记原始 SHA-256、迁移后 SHA-256、修改原因和测试证据。除已登记的
包路径适配外，BH 领域文件应与 v1.5.2 来源逐字节相同。不得借迁移调整阈值、角色、
证明条件、处置或制造输出。

### 5.2 BOX

`box/` 保持当前已经验收的压缩融合状态：

- Project2 v1.0.0 是唯一 BOX 算法内核；
- 来源基线固定为 Project2 v1.0.0 commit；23 个文件逐字节一致，7 个 BOX 领域热修
  文件必须通过 `box-notch-hotfix-2026-07-21` 双向 SHA-256 清单；
- 旧主项目只以已经吸收的输入资源预算等最小能力参与；
- 不恢复旧 `box_solver.py`、`box_reconstruction.py`、`box_writer.py` 等顶层实现；
- 不引入外部 distribution、backend 开关、legacy fallback、投票或结果拼接；
- 后续如修改 Project2 领域文件，必须扩展补丁清单、增加反向边界测试并重跑权威
  前后 DXF；不得用未登记差异或旧内核 fallback 改变结果。

## 6. 公共接口

包根只公开两个主要行为：

```python
authority = SplitAuthority.tekla_single_part(
    bh_profile="project_tekla_bh_dxf_v1",
    box_profile="project_tekla_box_dxf_v1",
    box_release_attestation=box_attestation,
)

result = split_dxf(
    input_path,
    output_dir,
    authority=authority,
)

batch = split_batch(
    input_paths,
    output_dir,
    authority=authority,
)
```

`SplitAuthority` 只是调用方来源声明的集合：

- BH 授权转换为原生 `BHSourceContract`；
- BOX 授权转换为原生 `BoxSourceContract`；
- 未提供的族不获得授权；
- 图纸内容不能补造或推断来源授权。

不公开以下能力：

- 强制绕过判型的 `family` 参数；
- 动态 adapter/backend 注册；
- “先 BOX、失败再 BH”的策略；
- 直接修改领域证明或统一阈值；
- 将一个领域结果转换为另一个领域模型。

BH 原生模块入口仍保留在 `steel_dxf_split.bh` 内，供 v1.5.2 回归、发布验证、分层检查
和焊接余量使用。它不是统一生产入口的 fallback。

## 7. 自动判型

`application.classification` 是独立、只读、无制造输出的入口判型模块。

固定顺序为：

1. 校验输入是稳定的普通 `.dxf` 文件并记录 SHA-256；
2. 使用 ezdxf 读取模型空间；
3. 递归展开嵌套 `INSERT`；
4. 提取 `TEXT` 与 `MTEXT`；
5. 解码 MIF、DXF Unicode、`%%c` 和已验证旧 cp936 直径符号；
6. 规范化空白、大小写和乘号；
7. 只接受完整截面规格；
8. 汇总全图唯一族并保存证据。

BH 规格兼容 v1.5.2 已支持的 `BH/WH/HW/HM/HN/H` 及变高度形式；BOX 只接受
完整 `BOX h*b*tw*tf` 规格。单独出现“BH”或“BOX”、文件名、图层名和固定 handle
均不是判型证据。

每项证据记录：

- 原始文字与规范化文字；
- 实体类型、handle、坐标和嵌套块路径；
- 规则 ID 与解析尺寸；
- 输入 SHA-256 和判型器版本。

路由规则：

- 只有 BH 证据：选择 BH；
- 只有 BOX 证据：选择 BOX；
- 同族重复证据：允许，不升级为复核；
- BH 与 BOX 同时存在：`manual_split_required/family_conflict`；
- 没有完整证据：`manual_split_required/family_unknown`。

坐标和材料表位置进入诊断与验收，但在权威语料证明稳定规则前，不新增武断的固定右上
区域裁剪。判型完成后释放文档；领域内核从源路径独立读取，避免共享可变 ezdxf 对象。

## 8. 唯一领域适配器接缝

`application.adapters` 内部定义一个不公开的领域编译接缝。它只有两个固定适配器：

- `BHV152Adapter`；
- `BoxProject2V100Adapter`。

适配器只负责：

- 接收已判型且已授权的输入；
- 构造领域原生调用参数；
- 调用对应领域入口一次；
- 校验领域报告的族、schema、版本、处置和声明工件；
- 将领域处置映射为统一结果；
- 引用领域报告，不转换领域 IR 或证明。

一个输入最多调用一个适配器一次。任何领域异常都不得触发另一个适配器。固定映射属于
实现细节，不向调用方公开注册表。

## 9. 三道制造安全门

“深模块”不等于“层层否决”。只有以下三类条件能够阻止正式生产输出：

1. 型材族必须唯一且对应来源授权有效；
2. 被选领域内核必须形成其原生证明允许的唯一制造结果；
3. 保存后 DXF 必须重新打开，并与领域 ManufacturingIR 及声明工件闭合。

每个证明事实只有一个所有者：

- 判型唯一性由 `application.classification` 所有；
- BH/BOX 几何与制造证明由各自领域内核所有；
- 保存后几何闭包由领域 validator 所有；
- 统一层只核验报告与工件包络，不重复推理几何。

普通警告、同族重复证据、诊断信息和非阻塞观察不得升级为人工复核。不得采用“任意
warning 即 review”或多个模块对同一证据重复否决。

## 10. 统一处置与错误分类

统一状态为：

| 状态 | 含义 | 上层动作 |
|---|---|---|
| `auto_accepted` | 领域证明、发布授权和保存后闭包全部通过 | 接受正式 DXF |
| `review_required` | 已有唯一制造候选，但存在真实工程证据缺口 | 创建工程复核任务 |
| `manual_split_required` | 未知族或族冲突，未调用领域内核 | 创建人工拆板任务 |
| `unprocessable` | 领域证明确认冲突或不可制造 | 创建人工拆板任务并保留拒绝诊断 |
| `configuration_blocked` | 来源授权或 release 配置缺失/不匹配 | 修复配置，不进入工程审核 |
| `system_failed` | I/O、进程、程序或工件提升故障 | 按错误类别重试或修复 |
| `timeout` | 子进程超过监督预算 | 重试或诊断性能 |

重要映射：

- BH `production` → `auto_accepted`；
- BH `review_required` → `review_required`；
- BH `rejected` → `unprocessable`；
- BOX `auto_accepted` → `auto_accepted`；
- BOX 真实证明证据不足 → `review_required`；
- BOX 证明已是 `auto_accept`、但 release attestation 缺失或失效 →
  `configuration_blocked`，不能污染人工审核队列；
- source contract 缺失或不匹配 → `configuration_blocked`。

编程错误不能降级成普通工程复核。单图调用抛出结构化系统异常；批次为保证全批可见性，
把该项记录为 `system_failed` 或 `timeout`，并丢弃其 staging。

## 11. 统一结果包络

统一结果只包含跨领域事实：

- 输入路径与 SHA-256；
- 判型结果和完整判型证据；
- 统一状态与稳定诊断码；
- framework version；
- 领域名称、领域版本和来源 commit；
- 正式、复核、源副本、预览及报告等工件引用；
- 领域报告 schema、路径与 SHA-256；
- 处理时间与系统失败分类。

BH/BOX 原始报告保持各自 schema、证明和诊断。批次从 staging 提升到正式目录时，只允许
等价改写报告中声明的 staging 绝对路径；不得修改 proof、ManufacturingIR、处置或版本。
统一包络引用提升后的最终领域报告哈希。

建议统一路由报告 schema 从 `DXF-SPLIT-ROUTING-1.0` 开始，批次 manifest 从
`DXF-SPLIT-BATCH-MANIFEST-1.0` 开始。

## 12. 批次事务

混合 BH/BOX 批次按每个输入独立处理：

1. 父进程冻结输入集合、唯一文件身份和源哈希；
2. 每个 DXF 启动一个新的统一 worker；
3. worker 在输出目录同盘 staging 中完成判型、授权和唯一领域编译；
4. 父进程校验版本、统一状态、领域报告和“声明工件集合等于实际文件集合”；
5. 重算源哈希，输入在处理期间变化则拒绝提升；
6. 校验通过后原子提升；
7. 每项完成后原子 checkpoint 统一 manifest；
8. 系统失败或超时只清理该项 staging，不覆盖其他结果。

批次默认处理完整输入集合，以便形成完整人工任务与系统故障清单。严格
`require_auto_accept` 只影响最终退出策略，不改变各项真实处置。

## 13. 版本

融合后的包不冒充 BH v1.5.2 或 BOX v1.0.0，统一框架采用新的主版本：

```text
framework_version = 2.0.0
BH engine_version = 1.5.2
BH source_commit  = 302dd73fa4b92f1d39486063c15dd49227e58b8a
BOX engine_version = 1.0.0
BOX source_commit  = 5a2be1a82eb7235bcff62d97a13d2937f9ad026b
```

领域报告继续使用领域版本；统一路由报告与 manifest 同时记录 framework 和 engine
身份。版本校验不能只看根包 `__version__`。

## 14. 旧代码退场

旧包根通用拆板文件、旧 BH 版本和已删除的顶层 BOX 算法只有在以下条件全部满足后才能
从最终包中移除：

- 生产调用图无 import；
- 上游 BH v1.5.2 测试已迁移并通过；
- BOX 单内核契约测试通过；
- 统一入口测试覆盖原公共行为；
- `rg`、构建 wheel 内容和运行时 import 检查均证明没有调用；
- 删除后 BH/BOX 权威语料处置不变。

不创建根级兼容转发模块。公开的 `steel_dxf_split.split_dxf` 与 CLI 能力保留；导入
`steel_dxf_split.bh_compiler` 等内部路径属于 2.0.0 的明确破坏性迁移。

## 15. 测试先行与验收

实现前先建立失败测试，至少覆盖：

### 15.1 判型

- BH、BOX 完整规格；
- MIF、Unicode、旧 cp936 和 MTEXT；
- 嵌套 INSERT；
- 同族重复证据不送审；
- BH/BOX 顺序无关冲突；
- 未知族；
- 文件名和图层名不能授权判型；
- 判型不产生制造 DXF。

### 15.2 唯一调用

- BH 输入只调用 BH adapter 一次；
- BOX 输入只调用 BOX adapter 一次；
- unknown/conflict 不调用任何 adapter；
- 任一领域失败不调用另一领域；
- 无 backend/fallback/legacy 配置入口；
- 不发生领域 IR 转换。

### 15.3 处置

- 真实工程证据缺口才进入 `review_required`；
- release/source 配置缺失进入 `configuration_blocked`；
- unknown/conflict 进入 `manual_split_required`；
- 系统错误和超时不进入人工审核；
- 普通 warning 不改变自动处置；
- 统一包络与原始领域报告一致。

### 15.4 BH

- v1.5.2 完整 pytest 与 warning-as-error；
- v1.5.2 repository health；
- 20 图保持 `18/2/0`，不新增 review；
- 两个既有 review reason 保持为证据缺口；
- writer 字节确定性、保存后验证、预览、分层检查和焊接余量回归；
- 来源哈希与三处包路径适配清单核验；
- BH 原生模块入口与统一 BH 路由均通过。

### 15.5 BOX

- 当前完整 BOX 测试至少保留已验证的
  `321 collected / 310 passed / 11 conditional skipped / 0 failed` 基线，
  新增测试只增加收集数；
- 权威 20 对 DXF：`20/20`，前后目录哈希不变；
- `D:\DevData\项目2_BOX_dxf`：`30/30 auto_accept`，输入哈希不变；
- 30 个 Project2 冻结核心文件来源核验；
- ManufacturingIR、孔归属、writer 和 saved-DXF validator 回归；
- 无旧 solver、fallback、投票或结果拼接。

### 15.6 统一批次与质量

- BH、BOX、unknown 和 conflict 混合批次；
- 每图独立进程、超时清理、同盘 staging、路径防逃逸和原子提升；
- manifest checkpoint 与版本漂移检测；
- 输入处理前后 SHA-256 不变；
- wheel 包内容和 console entry point 检查；
- Ruff、Mypy、强制 compileall、pytest、`git diff --check`；
- 最终人工审核数量不得因框架迁移无证据增加。

## 16. 完成条件

只有同时满足以下条件才能声明融合完成：

1. `application/` 是唯一统一生产入口；
2. `bh/` 与 `box/` 平级、互不导入；
3. BH v1.5.2 原生能力与可信处置基线不变；
4. 当前融合后的 BOX 仍是 Project2 单内核；
5. 自动判型保留结构化证据且一个输入只路由一个内核；
6. 只有三道制造安全门能够阻止正式生产输出；
7. 配置、人工拆板、工程复核和系统错误不会混入同一队列；
8. 两套权威语料和项目 2 独立语料全部通过且保持只读；
9. 旧主项目算法与根级兼容转发文件不在最终运行包；
10. 设计、实施计划、验证报告和实际代码相互一致。
