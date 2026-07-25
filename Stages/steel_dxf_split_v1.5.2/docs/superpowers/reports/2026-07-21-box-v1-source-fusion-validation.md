# BOX v1.0.0 源码级融合最终验证报告

## 结论

BOX 源码级融合的功能与金样验收通过。当前生产调用图只有一个 BOX 核心：

```text
steel_dxf_split.pipeline
→ steel_dxf_split.box.compiler
→ Frontend
→ Analysis
→ Solve
→ Manufacturing
→ Validation
→ Delivery
```

不存在旧 BOX solver、双后端、结果投票、自动 fallback 或 `SplitAssembly` 降级桥。
项目 2 的角色、方向、等价合并、孔归属、标签和几何重建结果不再由主项目重新解释。

仓库全量 Ruff/Mypy 的旧基线仍不为零；本次 BOX 源码、集成层、入口、测试、脚本和
离线工具的定向 Ruff/Mypy 均通过。该静态债务不影响本次运行时与金样结论，但必须
作为后续 BH v1.5.1 覆盖和框架收口的显式剩余项。

## 基线

```text
工作树：
D:\Dev\Projects\dxf agent\worktrees\box-completion

分支：
codex/feature/box-instance-reconstruction-active

本地 HEAD：
86cc4666bf4b811bae2faffa343a49300e84e8af

项目 2：
tag = v1.0.0
tag object = 1f55423a922e8ad4ba57342782ab294887c24359
commit = 5a2be1a82eb7235bcff62d97a13d2937f9ad026b
source = https://github.com/Creeken-Harrans/box-dxf-split
```

未执行 commit、push 或主干合并。

## 源码迁入与架构结果

- `src/steel_dxf_split/box/` 共有 40 个 Python 文件：
  - 初始迁入 30 个项目 2 v1.0.0 源码文件；当前其中 23 个逐字节一致，7 个属于
    `box-notch-hotfix-2026-07-21` 声明补丁；
  - 10 个主项目集成文件：
    `analysis.py`、`compiler.py`、`contracts.py`、`delivery.py`、
    `frontend.py`、`manufacturing.py`、`provenance.py`、`release.py`、
    `solve.py`、`validation.py`。
- 当前来源校验同时验证 v1.0.0 tag/commit、23 个原样文件和 7 个补丁文件的前后
  SHA-256；未声明差异数为 0。
- 已移除 23 个顶层旧 BOX 模块，以及旧监督 CLI、旧语料审计脚本、v2 适配器和
  对应旧测试。
- `pyproject.toml` 和 `uv.lock` 不含外部 `box-dxf-split` distribution；
  当前 `.venv` 中 `find_spec("box_dxf_split")` 返回 `None`。
- 生产 writer 直接消费项目 2 `ManufacturingIR`，只写原生
  `LWPOLYLINE`、`CIRCLE` 和 `TEXT`。
- 主项目外围只保留显式 source contract、release attestation、同盘 staging、
  原子提升、回滚、报告和进程隔离。
- 旧 v0.2.1/v2/REGION 设计、计划和报告均已标记为废弃历史。

## 测试与检查

### 运行测试

| 范围 | 结果 | 说明 |
|---|---:|---|
| BOX v1 完整套件 | 286 tests，0 failure，11 skipped | 275 通过；最终项目 `.venv`；667.716 秒 |
| 主项目/BH（排除 `tests/box_v1`） | 131/131 | 0 failure，0 skipped；最终精确复跑 66.698 秒 |
| 合计 | 417 tests，0 failure | 406 通过，11 条条件 skip |
| Windows 上游 pipeline 预览复验 | 3/3 | 使用主交付层相同的宋体/微软雅黑/黑体 fallback |
| 型材冲突和单核心入口复验 | 7/7 | 混合 BH/BOX 证据 fail closed |

11 条 skip 来自项目 2 原测试的条件边界，包括 POSIX 专用目录 fsync、当前机器缺失
的可选项目 1 语料、特定字体可用性和明确限定的曲折构件参数；没有隐藏失败。

### 源码、锁和静态检查

| 检查 | 结果 |
|---|---|
| `verify_box_v1_source.py` | 23 exact + 7 declared patches，0 missing，0 changed，0 unexpected |
| 本次 BOX/入口/脚本/工具 Ruff | 通过 |
| 18 个本次集成源文件定向 Mypy | 通过 |
| `git diff --check` | 通过 |
| `uv lock --offline --check` | 通过 |
| 全仓 `ruff check src tests scripts tools` | 未通过：8 个既有未使用 import，均在未修改的 BH/旧发布脚本 |
| 全仓 `mypy --ignore-missing-imports src` | 未通过：21 个文件共 117 条既有类型债务，主要位于 BH/共享旧代码及冻结上游的平台类型注解 |

本报告初次生成时 30 个文件均与上游一致；后续 BOX 缺口热修只修改清单登记的 7 个
文件，并由双向 SHA-256 锁定。未修改 BH 实现。

## 权威 20 对金样

最终独立命令重新读取：

```text
D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf
D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf
```

结果：

```text
schema = BOX-V1-FUSION-ACCEPTANCE-1.0
samples = 20
passed = 20
failed = 0
all_passed = true
proof disposition = 20/20 auto_accept
inputs unchanged = true
references unchanged = true
ground_truth_used_for_decision = false
```

保存后 DXF 实体合计：

```text
LWPOLYLINE = 63
CIRCLE = 36
TEXT = 63
REGION = 0
LINE = 0
POLYLINE = 0
ARC = 0
XLINE = 0
RAY = 0
```

标签精确匹配 59 条，保留 4 条诊断性侧名互换；这些差异不改变板件集合、数量、
几何、孔、member/family 或项目 2 MIR 结论。

机器报告：

```text
docs/superpowers/reports/2026-07-21-box-v1-fusion-acceptance.json
SHA-256 = 1410F37374D0D70EFEE2AE024614328F871C349470399CAF52EFCA94D1DBAD57
```

## Release attestation

- 当前 schema 为 `BOX-RELEASE-ATTESTATION-2.0`。
- attestation 绑定项目 2 v1.0.0 provenance、当前生产实现指纹、20 对数量合同和
  payload digest。
- 篡改、旧 v0.2.1 schema、实现漂移和不足的 10/10 分区计数测试均通过。
- 没有代替项目所有者签发持久化生产 attestation；正式批处理仍要求调用方提供
  对当前最终实现有效的认证文件。

## 验证环境

最终测试使用工作树现有 `.venv`：

```text
Python 3.12.13
pytest 9.1.1
ezdxf 1.4.4
numpy 2.5.1
shapely 2.1.2
```

探测环境时执行的离线 `uv run --extra dev` 对该现有 `.venv` 做过一次锁定依赖同步
（安装 14 个包、卸载 1 个包）；未修改系统环境、未联网安装、未改源码。之后所有
验证均直接调用该 `.venv`，没有再次安装依赖。

## 剩余风险与下一步

1. 项目 2 v1.0.0 仓库没有 `LICENSE` 文件；技术验证通过不等于已取得对外分发或
   商业使用授权。
2. `2b1-cb-86`、`h-9-cb-133` 合计 4 条等价板上/下侧名与人工参考互换；已保留为
   诊断，不能宣称人工侧名 100% 一致。
3. 全仓 Ruff/Mypy 的旧 BH/共享代码基线仍需在 BH v1.5.1 覆盖与框架优化阶段处理。
4. 当前真实生产 release attestation 尚未签发。
5. 下一阶段应以 `steel-dxf-split v1.5.1` 覆盖当前旧 BH 部分，再验证 BH 与本次
   BOX 单核心共同进入统一主干。
