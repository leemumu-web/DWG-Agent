# Steel DXF Classifier 1.1.0

本项目是独立的 Tekla 钢结构零件 DXF 分类工具。它读取零件图右上信息表的“截面/规格”字段，把输入目录第一层的 DXF 复制到按具体零件类型划分的同级目录。程序会在分类前原地规范输入文件名，但不重写 DXF 内容；它不依赖 BH 拆板仓库，也不把文件名或材料表中的游离规格当作分类事实。证据不足时停止自动判断，交给技术人员确认。

## 安装

需要 Linux、Python 3.12–3.13 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录使用锁定环境安装：
包名、CLI 入口、Python 范围和依赖声明以本目录 `pyproject.toml` 为准，锁定解析以 `uv.lock` 为准。

```bash
uv sync --frozen
```

Python 支持 3.12–3.13，DXF 解析使用 ezdxf。

若移动、重命名或解压项目目录后曾运行过本项目，已有 `.venv` 中的入口脚本可能仍指向旧绝对路径。首次运行前执行以下命令重写锁定环境：

```bash
uv sync --reinstall --frozen
```

检查安装：

```bash
uv run steel-dxf-classify --help
```

版本检查：

```bash
uv run steel-dxf-classify --version
```

## 输入与运行

输入目录必须精确命名为 `<项目名称>_dxf`，目录第一层直接放置 DXF：

```text
/data/项目2_dxf/
├── A001.dxf
├── A002.dxf
└── archive/       # 不递归，不读取
```

执行：

```bash
uv run steel-dxf-classify /data/项目2_dxf
```

自动化调用使用单 JSON 输出流：

```bash
uv run steel-dxf-classify --json /data/项目2_dxf
```

`--json` 的 stdout 只输出一个 `STEEL-DXF-CLI-1.1` JSON 对象；错误仍只写入 stderr。字段、退出码和完整文件系统契约见 [docs/IO_CONTRACT.md](docs/IO_CONTRACT.md)。

### 文件名预处理

分类开始前，输入目录第一层的全部 DXF 会原地重命名为 `*_拆板前.dxf`：

```text
A001.dxf      → A001_拆板前.dxf
B002.DXF      → B002_拆板前.dxf
```

只修改文件名，DXF 内容保持不变。已经符合规则的文件保持原名，因此重复运行不会追加第二个 `_拆板前`。程序在改名之前检查全部目标；遇到命名冲突会整批停止，不覆盖任何文件。子目录和非 DXF 文件不参与预处理。

分类输出是预处理后输入文件的副本，不会移动输入文件。已有本项目输出时默认停止；确认替换工具生成结果后使用：

```bash
uv run steel-dxf-classify /data/项目2_dxf --overwrite
```

## 输出

输出位于输入目录同级，只建立实际需要的目录：

```text
/data/项目2_BH_dxf/             # 例如 A001_拆板前.dxf
/data/项目2_BOX_dxf/
/data/项目2_PL_dxf/
/data/项目2_RHS_dxf/
/data/项目2_待确认_dxf/
/data/项目2_无法读取_dxf/
/data/项目2_分类清单.csv
/data/项目2_分类报告.json
```

通用目录规则是 `<项目名称>_<零件类型>_dxf`。具体前缀不会被粗略合并：例如 BH、BBH、H、HW、HM、HN、HEA、BOX、XBOX、PL、L、C、RHS 分别输出。

`待确认` 表示 DXF 可读，但截面字段缺失、规格缺失或多个候选冲突；`无法读取` 表示文件损坏、不是 DXF 或无法解码。两者都不会被猜测为某个零件类型。

## 已覆盖的零件类型

内置型材前缀包括：

- 板材：PL、FB；
- 焊接与箱形：BH、BBH、BOX、XBOX、BT；
- H/I/T 型：H、HW、HM、HN、HEA、HEB、HEM、I、IPE、IPN、UB、UC、T；
- 角钢与槽钢：L、C、CH、PFC、U、Z；
- 管材：RHS、SHS、CHS、PIPE；
- 棒材：RB、SB。

标题栏出现安全、明确但未登记的英文前缀时，程序会保留该具体前缀，不会擅自合并；例如 `TT25` 输出到 `<项目名称>_TT_dxf`。材料牌号（如 Q355B）、纯数字、比例和含路径字符的文本不会成为零件类型。

## 证据边界

分类器支持 `TEXT`、`MTEXT`、`ATTRIB` 和嵌套 `INSERT`，支持 GB2312、ANSI_936/GBK、UTF-8、DXF `\U+` Unicode 和中文 MIF 转义。它用“截面 / 截面型材 / 规格 / PROFILE / SECTION”等标签及相邻单元格建立证据。

材料表可能同时包含大量 PL、BOX、TT 等规格。若没有唯一的标题栏字段配对，这些内容只会造成 `TITLE_VALUE_CONFLICT` 或 `TITLE_FIELD_MISSING`，不能触发自动分类。完整规则见 [docs/CLASSIFICATION_RULES.md](docs/CLASSIFICATION_RULES.md)。

## 退出码

- `0`：全部文件完成自动分类；
- `2`：批处理完成，但存在待确认或无法读取文件；
- `1`：输入契约、已有输出、文件系统或批处理事务失败。
- `64`：命令参数或输入目录命名契约错误；stdout 为空，原因写入 stderr。

`--json` 是流程摘要；`STEEL-DXF-CLASSIFICATION-1.1` JSON 报告保存完整候选、坐标、图层、实体类型、块路径、解码器和诊断码；CSV 用于技术人员快速筛选。输出先在 staging 中完整构造并核对数量，再提升到正式目录。

`--overwrite` 只替换与当前项目同名的分类目录和报告；提升新结果失败时会恢复旧结果。输入 DXF 的文件名会按上述规则原地修改，但其字节内容不变，输出副本应与预处理后的输入逐字节一致。

## 开发验证

```bash
uv run pytest -q
uv run python -m compileall -q src tests
```

两组真实项目的逐文件结果、分类分布和字节一致性见 [docs/VALIDATION.md](docs/VALIDATION.md)。分类规则和人工复核边界见 [docs/CLASSIFICATION_RULES.md](docs/CLASSIFICATION_RULES.md)，正式输入输出契约见 [docs/IO_CONTRACT.md](docs/IO_CONTRACT.md)，版本变更见 [CHANGELOG.md](CHANGELOG.md)。
