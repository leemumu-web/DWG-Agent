# BOX 模块隔离重构计划

> 依据：`docs/superpowers/specs/2026-07-18-box-independent-multiview-reconstruction-design.md`
>
> 目标：只调整模块归属，不改变 BOX 几何、求解、命名、去重或输出语义。

## 成功标准

1. `pipeline.py` 只负责公共参数、结果类型、型材识别和延迟分派。
2. BOX 运行流程、输出写入和输出校验分别位于 `box_pipeline.py`、`box_writer.py`、`box_validator.py`。
3. BOX 生产路径不导入 `bh_*` 或旧 `extractor.py`；BH 路径不导入 `box_*`。
4. 重构前后的清洁 DXF 通过相同的规范化几何、孔、标签、图层和污染实体回读合同；不比较包含生成时间等文档元数据的原始文件 SHA-256。
5. 修正后的 20 对 proof、BOX 测试和 BH 回归测试全部通过。

## 任务 1：用架构测试锁定边界

**文件：**

- 修改：`tests/test_box_architecture_v2.py`
- 修改：`tests/test_box_atomic_pipeline_v1.py`

**步骤：**

1. 增加断言：`box_pipeline.py`、`box_writer.py`、`box_validator.py` 必须存在。
2. 增加断言：`pipeline.py` 不得定义 `_split_box_dxf`，不得在模块加载时导入 BOX/BH 领域实现。
3. 增加断言：旧的 `writer.py`、`validator.py` 不得继续充当 BOX 权威入口。
4. 把原子写入测试的 monkeypatch 目标切换到 `box_pipeline`。
5. 先运行架构测试并确认迁移前失败：

```powershell
python -m pytest tests/test_box_architecture_v2.py tests/test_box_atomic_pipeline_v1.py -q
```

## 任务 2：迁移 BOX 主路径与输出模块

**文件：**

- 新增：`src/steel_dxf_split/box_pipeline.py`
- 移动：`src/steel_dxf_split/writer.py` → `src/steel_dxf_split/box_writer.py`
- 移动：`src/steel_dxf_split/validator.py` → `src/steel_dxf_split/box_validator.py`
- 修改：`src/steel_dxf_split/pipeline.py`
- 修改：`src/steel_dxf_split/box_supervision.py`
- 修改：`tests/test_box_supervision_v1.py`
- 修改：`tests/test_box_validator_v2.py`

**步骤：**

1. 将 `_promote_staged_files()` 与 BOX 拆板流程原样迁入 `box_pipeline.py`，公开入口命名为 `split_box_dxf()`。
2. 将公共入口压缩为 DXF 型材识别及对 `bh_pipeline`、`box_pipeline` 的延迟分派。
3. 更新 BOX 监督路径和测试的 writer/validator 导入。
4. 不改任何算法常数、报告字段、文件名或输出实体。
5. 运行针对性测试：

```powershell
python -m pytest tests/test_box_architecture_v2.py tests/test_box_atomic_pipeline_v1.py tests/test_box_validator_v2.py tests/test_box_supervision_v1.py -q
```

## 任务 3：验证行为等价和领域隔离

**文件：**

- 可能修改：`README.md`

**步骤：**

1. 运行全部 BOX 测试。
2. 运行全部 BH 测试。
3. 重新验证修正后的 20 对 proof。
4. 生成代表性清洁 DXF，并与重构前 `output/box-verified` 使用同一规范化回读合同比较几何、孔、标签、图层和污染实体。
5. 确认完整 20 对 proof 覆盖全部样本的同类规范化比较。
6. 检查 `git diff --check`、工作树状态和架构导入扫描。

## 任务 4：提交但不合并

**步骤：**

1. 审查仅包含本次模块归位和对应测试/文档。
2. 提交：

```powershell
git add src tests docs README.md
git commit -m "refactor(box): isolate pipeline and output modules"
```

3. 保持当前 worktree 和分支，不执行 merge 或 push。
