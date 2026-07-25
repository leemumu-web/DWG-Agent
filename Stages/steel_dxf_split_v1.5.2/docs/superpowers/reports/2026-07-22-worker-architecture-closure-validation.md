# BH/BOX 拆板 Worker 架构收口验证报告

日期：2026-07-22

## 结论

候选 A、B、C 已全部落地：BOX 重复公开编排已退役，BH/BOX 统一返回顶层
`SplitResult`，批处理按单图完整产物集执行可回滚提升。BH v1.5.2 与 Project2 BOX
v1.0.0 的领域核心均保留为各自权威，没有新增 backend、fallback 或第二套路由系统。

本次结果可称为“Windows 开发态架构融合完成、跨平台结构安装态通过”。它尚不能称为
新的 Linux 正式发布：本次生产实现指纹已经变化，旧 BOX release attestation 自动失效，
仍需在带完整依赖和字体的 Linux Worker 环境重跑算法发布门并生成新证明。

## 架构结果

- 唯一公开单图入口：`steel_dxf_split.pipeline.split_dxf`
- 唯一公开命令：`steel-dxf-split`、`steel-dxf-split-batch`
- 已退役：`box/cli.py`、`box/batch_cli.py`、`box/pipeline.py`、`box.split_dxf`
- 统一处置值：`auto_accepted`、`review_required`、`rejected`
- 保留领域原始处置：`native_automation_route`
- 单图事务：全量 staging、目标备份、逐项提升、失败撤回与旧产物恢复
- 批次语义：后续图失败不回滚此前已经完成的图

## 提交

- `10b700c`：建立 BH v1.5.2 与 BOX v1.0.0 的来源可验证融合基线
- `af81a79`：退役 BOX 重复编排
- `993b6fa`：统一 BH/BOX Worker 结果契约
- `2dc628a`：实现单图产物提升失败回滚
- `f63a4d9`：锁定 BH 集成适配哈希
- `685fdb2`：保留 post-hoc 审计 CLI 契约

## Windows 验证

全量测试按测试域分片执行，pytest 共收集 1031 个节点，失败数为 0：

```text
根 Worker 契约与编排：            59 / 59
BOX 非权威语料节点：              310 / 310（含既有 Windows 条件跳过）
BOX 权威前后 DXF：                 2 / 2 测试，覆盖 20 / 20 对制造真值
BH 非表示变换节点：               500 / 500（含既有条件跳过）
BH 表示不变性：                   160 / 160
合计：                            1031 collected / 0 failed
```

补充检查：

- `python -m compileall -q -f src tests tools scripts`：通过
- BH v1.5.2 来源：48 exact + 5 hashed adaptations + 8 hashed patches
- BOX v1.0.0 来源：19 exact + 7 hashed patches + 1 adaptation + 3 retired
- 最终 wheel 隔离安装、入口枚举、内置证据读取和安装目录 compileall：通过

## 最终候选 wheel

```text
路径：D:\AppDataRelocated\Temp\steel-dxf-worker-wheel-20260722-architecture-closure-final\steel_dxf_split-1.5.2-py3-none-any.whl
大小：363493 bytes
SHA-256：e9389c3e99aa82b0daee486f81e365849455dbaa1b730cc85a2f204d53a8fa87
```

## Linux 结构安装态

使用本机已有镜像、禁止网络、禁止拉取新镜像：

```text
镜像：ghcr.io/astral-sh/uv:python3.12-bookworm-slim
Python：3.12.12
平台：Linux x86_64 / glibc 2.36
wheel 安装：通过
统一 console scripts：通过
旧 BOX 编排模块不存在：通过
BH/BOX 内置发布证据可读取：通过
安装目录 compileall：通过
```

该镜像没有 `ezdxf`、`Shapely`、`matplotlib`、`Pillow` 等运行依赖；依据环境安全约束，
本次没有自行安装依赖。因此未在最终 wheel 上执行 Linux 完整 pytest、BH/BOX 真实 DXF
编译、字体/预览门和新 BOX attestation 生成。

## 剩余风险与边界

- 旧 Linux 发布结果属于上一实现指纹，不能为本 wheel 签发。
- 新 Linux 完整发布门通过前，本 wheel 只能作为候选产物。
- 仓库根目录未跟踪的 `release/` 是既有生成物，本次没有纳入提交或清理。
- 本次只提交到当前功能分支；没有 push、没有合并主干、没有打标签。
