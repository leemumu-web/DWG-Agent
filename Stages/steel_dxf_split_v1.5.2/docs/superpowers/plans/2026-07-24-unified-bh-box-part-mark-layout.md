# BH/BOX 统一零件标记布局实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 用一套共享的材料感知布局规则生成 BH/BOX 零件标记，保持既有字号，
修复 300 mm 宽 BOX 的 30 mm 文字误判，并完成全程序发布验证。

**架构：** 新增只接收 Shapely 材料几何的共享布局模块；BH 和 BOX writer
分别完成领域几何适配，validator 复用同一包络合同。BOX 现有比例公式和 BH
翼宽公式只决定首选字号，最终合法性统一由“实际文字 + 每边 5 mm”决定。

**技术栈：** Python 3.12、Shapely 2.1、ezdxf 1.4.4、pytest、Ruff、uv。

## 全局约束

- 最小标记字号固定为 `30.0 mm`。
- 安全距离固定为上下左右各 `5.0 mm`。
- 不修改制造几何、孔洞、焊接余量或编译证明逻辑。
- BOX 前后 20 对黄金目录只读，不移动、不改名、不写入。
- 吸收原仓库现有 BH 紧凑命名变更，不覆盖原仓库工作区。
- 不提交、不推送；所有变更留在隔离分支供用户检查。

---

### 任务 1：固定跨平台文本字节

**文件：**
- 修改：`.gitattributes`
- 修改：`src/steel_dxf_split/release_evidence/box_build_contract.json`

**产出：** 所有文本在工作树固定为 LF，DXF 保持二进制，构建合同记录规范化
`pyproject.toml` 与 `uv.lock` 的真实 SHA-256。

- [x] 添加 `* text=auto eol=lf` 和 `*.dxf binary`。
- [x] 机械规范化隔离工作树文本并验证 BH 发布证据哈希。
- [x] 运行 BH 发布、源码完整性和 codegen 测试。
- [x] 最终实现确定后重新生成 BOX release attestation。

### 任务 2：建立共享布局模块

**文件：**
- 新建：`src/steel_dxf_split/part_mark_layout.py`
- 新建：`tests/test_part_mark_layout.py`

**接口：**
- 输入：`PartMarkTarget(target_id, label, outer_geometry, material_geometry, hole_count)`
- 输出：同字号的 `tuple[PartMarkPlacement, ...]`

- [x] 先写文字宽度、固定 5 mm 包络、避孔、首选字号不升级和失败诊断测试。
- [x] 运行 `pytest tests/test_part_mark_layout.py -q`，确认因模块不存在而失败。
- [x] 实现最小共享模块。
- [x] 重跑测试并确认通过。

### 任务 3：接入 BOX writer 与 validator

**文件：**
- 修改：`src/steel_dxf_split/box/writer.py`
- 修改：`src/steel_dxf_split/box/validator.py`
- 修改：`tests/box_v1/test_writer.py`

**接口：**
- BOX 保留 `_layout_label_capacity` 作为首选字号估计。
- `plate_material_geometry` 向共享定位器提供去孔材料几何。

- [x] 增加 `a1-3-cb-356`、300×280/300 mm 合成回归测试，期望 30 mm 成功。
- [x] 运行该测试，确认旧代码抛出最小 30 mm 包络错误。
- [x] 用共享布局替换 BOX 私有包络和定位代码。
- [x] 验证既有 90/120/30 mm 测试保持不变，保存后 validator 通过。

### 任务 4：接入 BH writer、validator 与紧凑命名

**文件：**
- 修改：`src/steel_dxf_split/bh_text.py`
- 修改：`src/steel_dxf_split/bh_writer.py`
- 修改：`src/steel_dxf_split/bh_validator.py`
- 修改：`tests/bh_v152/test_bh_codegen_authority.py`
- 修改：`tests/bh_v152/test_bh_engineering_regressions.py`

**接口：**
- `BHLayout` 增加 `label_heights`。
- `layout_bh_manufacturing_ir(..., preferred_text_height=None)` 返回已验证位置和字号。
- 保存后 validator 检查文字、位置、字号、样式、实际包络和 5 mm 安全包络。

- [x] 先加入紧凑文字、布局高度、材料包络和篡改位置失败测试。
- [x] 运行目标测试并确认旧实现失败。
- [x] 接入共享定位器并保留 BH 首选字号上限。
- [x] 重跑 BH writer/validator 目标测试。

### 任务 5：更新源码和发布完整性合同

**文件：**
- 修改：`src/steel_dxf_split/box/release.py`
- 修改：`tests/box_v1/test_release.py`
- 修改：`tools/verify_bh_v152_source.py`
- 修改：`tests/test_bh_v152_package.py`
- 修改：`src/steel_dxf_split/release_evidence/box_release_attestation.json`

- [x] 先测试 BOX 生产实现 payload 必须包含 `part_mark_layout.py`。
- [x] 将共享模块加入 BOX 实现指纹和 BH 集成文件清单。
- [x] 计算最终 BH patch 文件 SHA-256 并更新显式补丁合同。
- [x] 对只读 BOX 20 对语料运行官方 verifier，仅由生成器重签 attestation。

### 任务 6：真实数据与全程序验证

**文件：**
- 只读：`D:\DevData\final dxf\BOX\BYSJ@零件图@a1-3-cb-356.dxf`
- 只读：两组 BOX 黄金目录
- 生成：临时验证目录和最终 wheel

- [x] 编译真实北邮 BOX，写入临时 DXF，运行保存后 validator。
- [x] 对 BH 20 对逐个写入临时 DXF并验证标记材料包络。
- [x] 运行 BOX 官方 20 对黄金比较及发布门。
- [x] 并行运行 BH、BOX、统一入口三组 pytest，合计覆盖全部收集用例。
- [x] 运行 `compileall`、`git diff --check` 和 clean wheel 构建；当前环境未安装 Ruff。
- [x] 在临时虚拟环境安装 wheel，执行真实 BOX 冒烟验证。
- [x] 复核工作树只包含本任务文件和吸收的用户命名变更。
