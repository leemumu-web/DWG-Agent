# DXF 零件类型分类规则

## 1. 权威输入

分类对象是 `<项目名称>_dxf` 第一层中的 `.dxf` 文件；扩展名比较不区分大小写，但不递归进入任何子目录。分类前先把文件原地重命名为 `*_拆板前.dxf`，DXF 内容保持不变，分类目录保存逐字节复制品。

预处理具有幂等性：已经带 `_拆板前.dxf` 的文件在重复运行时保持不变。程序先建立整批改名计划，并以 Unicode 大小写折叠检查命名冲突；任何冲突都会在修改前停止。实际改名通过隐藏临时名分两阶段完成，失败时逆序回滚。

零件类型的最高权限证据是右上标题栏或零件信息表中的截面字段。当前标签词典为：

```text
截面
截面型材
规格
PROFILE
SECTION
PROFILE/SIZE
```

标签位置使用整张图文字范围的相对坐标判断，不绑定某个图框的绝对毫米坐标。值必须与标签位于同一块实例路径，并处于下方同列或右侧同行的受限相邻区域。

## 2. DXF 文字恢复

解析器保留 TEXT、MTEXT、ATTRIB、图层、句柄、文字高度、世界坐标和嵌套 INSERT 块路径。旧 Tekla 图常声明 `$DWGCODEPAGE=GB2312` 或 `ANSI_936`，两者用 GBK 兼容解码重新读取；AutoCAD `\U+xxxx` 与中文 `\M+5hhhh` MIF 控制序列在文本规范化阶段恢复。

恢复失败属于 `DXF_READ_FAILED`，文件复制到 `<项目名称>_无法读取_dxf`，不会尝试从文件名补救。

## 3. 零件类型

规格值经 NFKC、大小写、空白和乘号规范化后解析。内置类型覆盖：

| 工程族 | 保留的具体类型 |
|---|---|
| 板与扁钢 | PL, FB, FL, BL |
| 焊接组合截面 | BH, BBH, RH, BOX, XBOX, BT, PX |
| H/I/T 型 | H, HW, HM, HN, HT, HE, HEA, HEB, HEM, HL, HD, HP, I, IPE, IPN, INP, UB, UC, W, S, M, T, WT, ST, MT |
| 角钢与槽钢 | L, C, CH, PFC, MC, U, UPN, UPE, Z |
| 空心截面与管材 | RHS, SHS, CHS, HSS, PIPE |
| 棒材 | RB, SB |

标题栏唯一强证据允许自动发现安全的未注册 ASCII 前缀，例如 `TT25 → TT`。动态前缀必须由 2–12 个字母组成，后方必须是数值尺寸主体；结果标记 `type_source=auto_discovered` 和 `PROFILE_TYPE_AUTO_DISCOVERED`，保留独立 `group_key`。目录中的注册类型标记为 `type_source=catalog`。未知单字母前缀、`Q355B`、孤立的 `M20`、`1:10`、纯数字尺寸和说明语句不是型材规格。

## 4. Fail-closed 决策

自动分类要求同时满足：

1. 标题字段位于上部或右部候选区域；
2. 标签和值具有同一块路径；
3. 相邻区域只形成一个唯一规格事实；
4. 规格可得到安全、稳定的类型前缀。

否则使用以下诊断：

- `TITLE_FIELD_MISSING`：没有合格标题字段；
- `TITLE_VALUE_MISSING`：有字段但没有可解析规格；
- `TITLE_VALUE_CONFLICT`：材料表、多行数据或重复标题产生多个不同值；
- `DXF_READ_FAILED`：文件读取或遍历失败。

材料表不是标题栏。即使材料表包含 PL、BOX、BH 等已知字符串，也不能绕过唯一字段证明。

## 5. 输出事务与审计

每次运行生成 `<项目名称>_分类报告.json` 和 `<项目名称>_分类清单.csv`。JSON 的 schema 为 `STEEL-DXF-CLASSIFICATION-1.2`，是完整审计记录；CSV 是其面向技术人员的扁平投影。两者的输入计数、处置、类型、`group_key`、`type_source`、`next_stage_eligible` 和输出目录必须一致。只有证据充分且已形成安全类型分组的结果可供下一阶段读取；待确认和无法读取固定为不可接入。CLI 的 `--json` 输出仅为摘要投影，正式进程/文件流规则见 [IO_CONTRACT.md](IO_CONTRACT.md)。

默认不覆盖现有目录。`--overwrite` 先在隐藏 staging 目录生成所有副本和报告、核对文件数量，再备份并替换已有同项目输出；提升失败则恢复备份。输入 `<项目名称>_dxf` 永远不在输出替换集合中，但其中的 DXF 文件名会在分类前按预处理规则规范化。
