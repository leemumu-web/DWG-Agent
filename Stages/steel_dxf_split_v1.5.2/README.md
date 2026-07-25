# Steel DXF Split：BH/BOX 统一双产物拆板程序

本程序是融合后的主程序。它只保留一个公开命令，自动识别 BH 或 BOX，调用对应领域核心，并把同一次拆板结果发布为一对完整 DXF：普通版与余量伸长版。

```text
输入目录快照
→ 每张 DXF 只判型一次
→ 只调用 BH 或 BOX 中的一个拆板核心
→ 执行对称孔颜色策略
→ 形成完整拆板基础结果
   ├─ normal：不做余量伸长
   └─ weld_allowance：复制基础结果后做余量伸长
→ 两份文件成对验收
→ 整个任务目录一次性发布
```

## 运行合同

- 公开命令只有 `steel-dxf-split`。
- 命令只扫描输入目录第一层的 `.dxf` 文件，并在处理开始前冻结文件列表。
- 输入目录与输出目录不得相同或互相嵌套。
- `output` 永远不作为任何阶段的输入。
- 每张图只执行一次类型识别和一次拆板。
- BH 与 BOX 使用各自的领域处理器，不互相导入制造几何模型，也没有失败后回退旧入口的逻辑。
- 自动验收成功的每张输入严格产生两个完整 DXF；板件数量不会改变文件数量。
- 普通版与余量版必须同源、同时验收、同时发布。任意一份失败，整个任务进入 `manual_review`。

自动验收目录结构如下：

```text
output/
├─ BH拆板信息表.xlsx
├─ auto_accepted/
│  ├─ bh/
│  │  └─ <原图名称>/
│  │     ├─ <原图名称>_正常拆板.dxf
│  │     ├─ <原图名称>_余量增长.dxf
│  │     ├─ <原图名称>_report.json
│  │     ├─ <原图名称>_weld_allowance_report.json
│  │     └─ previews/
│  └─ box/
│     └─ <原图名称>/
│        └─ 同上
└─ manual_review/
   ├─ bh/<原图名称>/
   └─ box/<原图名称>/
```

每个 `auto_accepted` 任务目录中恰好有两个 `.dxf` 文件。JSON 与 PNG 是验收旁证，不计入 DXF 产出数量。

`BH拆板信息表.xlsx` 是项目级台账。统一命令每次从本次输入快照重新生成，
只记录 BH 的“零件号”“BH尺寸”“上下翼板是否相同”三列，其中最后一列
固定使用“是”或“否”。该表用于后续 Excel 第二阶段唯一定位 BH 零件并决定
是否把翼板行拆分为上翼板和下翼板；BOX 不写入此表。

## 两份 DXF 的差异

`normal` 是完成正常拆板、孔洞识别和孔洞染色后的完整结果，不执行余量伸长。

`weld_allowance` 从同一份普通拆板结果复制派生，只移动余量规则允许移动的板端几何。它不会重新扫描目录、重新识别构件或重新识别孔洞。

成对验收会重新打开两份 DXF，并检查：

- DXF 版本、毫米单位、实体数量与图层数量一致；
- 所有 `CUT_HOLE` 的原生几何和显式 ACI 颜色完全一致；
- 余量报告绑定当前两份文件，且每块板满足 `伸长后长度 = 原长度 + 规定余量`；
- 两份 DXF 均通过 ezdxf 保存后审计；
- 任务目录内不存在缺少配对文件的自动验收结果。

余量规则允许某些短板得到 `0 mm`。这表示正确执行规则，不表示漏做余量；发布报告会逐板记录原长度、规定余量和伸长后长度。真实正余量回归使用 BH `2b1-cb-29` 与 BOX `2b1-cb-56`，两者都证明至少一个板件发生了正向伸长。

## 对称孔颜色

颜色判断在每块拆出板件自己的局部 X 范围内完成：

- 仅当两个圆孔关于板件 X 中线唯一镜像、Y 坐标一致且半径一致时，才认定为一对对称孔；
- 左侧孔使用显式 ACI 1（红色）；
- 右侧孔使用显式 ACI 7（白色）；
- 中线孔、无配对孔、重复候选或歧义孔全部保持白色；
- 非圆形内孔保持白色；
- `CUT_HOLE` 图层默认颜色为 ACI 7，避免“另一侧”继承红色图层颜色。

上游算法仓库的真实 BH `2b1-cb-29` 回归样例输出有 48 个圆孔，其中 24 个左孔为红色、24 个右孔为白色，且普通版与余量版颜色完全一致。DWG-Agent 中的运行时切片不复制 DXF 样本语料。

现有 20 对 BOX 权威前后样例及 30 张项目 2 BOX 源图没有横向镜像圆孔对，因此 BOX 的该规则由制造 IR 合成回归验证；真实 BOX 样例出现后，应追加为发布级回归，不能用合成样例冒充真实证据。

## 安装与运行

要求 Python 3.12 或 3.13。开发环境使用项目自己的 `.venv` 或 `uv`，不要把依赖安装到 Conda `base`。

在 DWG-Agent 中，该包由 `backend/pyproject.toml` 的锁定路径依赖安装：

```powershell
cd backend
uv sync --locked
```

处理混合 BH/BOX 输入目录：

```powershell
uv run steel-dxf-split `
  ".\input" `
  --output-dir ".\output" `
  --authorize-tekla-bh-single-part-profile project_tekla_bh_dxf_v1 `
  --authorize-tekla-box-single-part-profile project_tekla_box_dxf_v1
```

如果目录中只有一种类型，只需提供对应授权。`--box-release-attestation` 仅用于显式指定经过验证的 BOX 发布认证；省略时使用安装包内置认证。

退出码：

- `0`：全部输入成对进入 `auto_accepted`；
- `1`：至少一个任务进入 `manual_review`；
- `2`：输入参数错误或至少一个输入发生无法形成审计任务的处理异常。

命令标准输出为一个 JSON 数组，每项包含类型、两份 DXF、报告、任务目录、最终路由及处理耗时。

## 领域核心与证据边界

- BH 领域核心基于当前 `steel-dxf-split` v1.5.2 代码与冻结回归。
- BOX 领域核心来自 `box-dxf-split` v1.0.0，commit `5a2be1a82eb7235bcff62d97a13d2937f9ad026b`，所有融合补丁由来源校验清单逐文件绑定 SHA-256。
- BOX 拆板算法的最终权威是只读目录 `D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf` 与 `D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf` 的 20 对样例。
- 人工“拆板后”参考图只用于离线验收，不能进入统一运行时的识别、求解或路由。
- 旧批处理 CLI、旧余量 CLI、独立余量发布复扫器和对应 console script 已从源码与安装入口删除。

## 验证

DWG-Agent 只跟踪运行所需的 `src/`、包内发布证据、`pyproject.toml`、`VERSION` 和说明文件，不复制上游的 DXF 样本、测试语料、开发脚本与发布报告。运行时接入验证命令：

```powershell
cd backend
uv run python -m steel_dxf_split.cli --help
uv run pytest -q tests/dxf_splitting tests/architecture/test_cad_processing_boundaries.py
```

算法级测试和 corpus 验收继续在上游 `steel-dxf-split-worker` 源仓库执行。BOX 最终验收必须覆盖只读的拆板前后黄金样本目录；这些数据是权威输入，但不是 DWG-Agent 的发布文件。
