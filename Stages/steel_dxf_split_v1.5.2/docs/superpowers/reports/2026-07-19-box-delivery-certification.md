# BOX 制造交接、REGION 交付与离线认证验收报告

> **历史结果，已被替代（2026-07-21）：** 本报告验证的是旧算法/REGION
> 路径，不代表当前源码级融合状态。

## 1. 结论

本次合并优化候选 1、2、3 已在
`D:\Dev\Projects\dxf agent\worktrees\box-completion` 完成实现并通过 BOX
权威样例验收：

1. 已建立唯一、不可变的 `BoxDeliveryBatch` 制造交接层；
2. 已建立 `auto_accepted` / `review_required` 强路由和 ACIS REGION 正式交付闭环；
3. 已将 20 对金样认证移出生产运行时，生产路径只读取自包含 release
   attestation。

本任务没有复制 `box-dxf-split v0.2.1` 源代码，没有改动两个金样目录，没有提交或
推送。

## 2. 实现摘要

### 2.1 规范化制造交接

新增 `box_delivery_ir.py`：

- 保留四个已证明物理角色；
- 只在板族、板厚、外轮廓和全部切孔完全等价时合并交付组；
- 交付数量与物理角色分开保存；
- 同形但孔不同的板不合并；
- 非圆孔、无效轮廓、缺 provenance 或不完整制造合同时 fail closed；
- 交接 fingerprint 对输入顺序稳定，对真实语义变化敏感。

正式 writer 不再接收 `SplitAssembly`。`SplitAssembly` 只保留为复核候选和图纸比例
辅助输出的旧适配器。

### 2.2 REGION 正式交付

新增：

- `box_region.py`
- `box_delivery_writer.py`
- `box_delivery_validator.py`

正式输出合同为：

- R2007 / `AC1021`；
- 毫米单位；
- `PLATE_CUT/REGION`；
- `CUT_HOLE/REGION`；
- `PART_LABEL/TEXT`；
- `SplitChinese` + `simsun.ttc`；
- 制造图层上无 LWPOLYLINE、POLYLINE、CIRCLE、ARC 或 LINE。

真实带孔样例曾暴露 ezdxf 默认 SAT 使用 6 位有效数字造成的大坐标舍入。根因修复
后，SAT 实数和 ACIS transform 都使用 17 位有效数字独立序列化。大坐标回归中：

- 外轮廓保存回读最大误差约 `2.8e-14 mm`；
- 14 个圆孔保存回读误差为 `0 mm`。

validator 会重新打开已保存 DXF，从 ACIS body 恢复面边界，再验证实体数量、几何、
标签、字体、单位、版本、writer 闭环和 legacy 制造曲线白名单。

### 2.3 强路由

- 只有 `production_ready=true` 且 `runtime_authorized=true` 才能写入
  `auto_accepted`。
- 未认证运行即使 `write_clean=true`，`SplitResult.clean_path` 也为 `None`。
- 未认证输出只允许进入 `review_required/<构件>`。
- `require_auto_accept=true` 未满足时抛错，且不留下本次产物。
- 生产路由只生成正式 REGION clean；复核和图纸比例文件不冒充正式交付。

### 2.4 离线认证

新增 `box_release.py`。release attestation 顶层只有：

```text
schema_version
created_at
certification
payload_digest
```

其中不包含 manifest 路径、corpus 路径或逐对 evaluation。生产
`box_pipeline.py` 不再导入 `box_supervision.py` 或
`reference_geometry.py`。

运行时会检查：

- schema 和字段合同；
- 20 / 10 / 10 数量门；
- payload digest；
- manifest 和 gate fingerprint；
- 当前生产实现 fingerprint。

该摘要用于检测误用、过期和实现漂移，不宣称抵抗具有本机写权限的恶意攻击。

## 3. 权威金样验收

只读权威目录：

```text
D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf
D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf
```

离线认证结果：

| 指标 | 结果 |
|---|---:|
| 总样例对 | 20 |
| calibration | 10 |
| acceptance | 10 |
| 逐对通过 | 20/20 |
| gate | passed |

本轮 release attestation：

```text
manifest_fingerprint       1e5b58c19af4f2c62923ba847c2538cf72faf72b9c9c09b4cbfe4e7ae468e894
gate_fingerprint           f403c5116e1c4bb7d035f3e346e802ec0da7ed839ec2b749b09b7dd525ce0cd3
implementation_fingerprint 0bd4262a714fb69795e3cb1978a943c5496bacec80878c3bca09c163ea58087a
payload_digest             4f20b22acd36bfa506630e23421299a021681bb3a328d9d701879bec292c863c
```

用该 attestation 对 20 个拆板前 DXF 逐个执行正式生产入口：

| 指标 | 结果 |
|---|---:|
| `auto_accepted` 输出 | 20/20 |
| 保存后回读通过 | 20/20 |
| 板 REGION | 63 |
| 孔 REGION | 36 |
| PART_LABEL | 63 |
| legacy manufacturing curves | 0 |
| review/sheet 混入正式路由 | 0 |

随后直接从这 20 个正式 DXF 的 `PLATE_CUT` / `CUT_HOLE` ACIS REGION 回读制造
几何，并按冻结 manifest 的人工批准阈值与对应的拆板后金样逐对比较：

| 正式成品对金样指标 | 结果 | 冻结阈值 |
|---|---:|---:|
| 逐对通过 | 20/20 | 20/20 |
| 最大板件包围盒差 | 0.877849 mm | 2 mm |
| 最大板件边界 Hausdorff 距离 | 0.877849 mm | 2 mm |
| 最大孔心差 | 0.014313 mm | 2 mm |
| 最大孔半径差 | 0.003017 mm | 0.1 mm |

这项复核比较的是实际落盘成品，而不是只比较写出前的内存板件。另行检查显示，
正式 REGION 对交付布局的最大闭环误差约为 `5.9e-12 mm`，交付布局对算法内存
板件的最大边界误差约为 `3.0e-9 mm`；正式写出没有引入可观测的制造几何漂移。

逐文件机器结果保存在：

```text
D:\AppDataRelocated\Temp\box-merge-final-ji0_4fgk\summary.json
```

正式批量输出保存在：

```text
D:\AppDataRelocated\Temp\box-merge-final-ji0_4fgk\outputs\auto_accepted
```

金样保护核验：任务末次批量认证前后，对 40 个权威 DXF 比较 SHA-256、文件长度和
纳秒级修改时间，变化数量为 `0`。

## 4. 自动化测试

### BOX

- BOX 测试：`241/241` 通过；
- 包含制造交接、release attestation、REGION 大坐标、路由、原子提升、20 对 gate
  和真实带孔保存回读。

### 完整项目

完整项目共收集 350 项。合并本轮单独执行的完整 BOX gate 后：

- `349` 项通过；
- `1` 项环境失败；
- 无本次 BOX 实现失败。

唯一失败：

```text
tests/test_bh_semantic_contract_v11.py::
test_pytest_worker_bypasses_hanging_interpreter_finalizer
```

原因是当前 `.venv` 没有安装 `pyproject.toml` 已声明的可选 dev 依赖 `pytest`。
主测试进程通过临时追加外部 pytest 路径运行，但该 BH 测试会用
`sys.executable` 启动全新子进程，子进程按设计不继承该临时路径，因此在
`scripts/pytest_worker.py` 导入 pytest 时失败。遵守环境边界，本任务未擅自安装
依赖。

## 5. 剩余边界

- 正式交付当前只接受直线 Polygon 外轮廓和可证明圆孔；任意非圆内轮廓仍 fail
  closed。
- 20/20 证明的是当前权威样例覆盖的制图分布，不是所有 CAD 来源的无限泛化保证。
- release attestation 尚未做外部私钥签名。
- 生产输出使用 ACIS REGION，需要下游 CAD/CAM 保持对 R2007 SAT REGION 的支持。
