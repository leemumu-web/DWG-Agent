# steel_dxf_split_xbox

独立的 Tekla XBOX（封闭箱形焊接构件）DXF 拆板 Stage。成对产物：正常拆板 DXF + 焊接余量增长 DXF。

## 自包含声明

本包**不导入** BH/BOX 的 `steel_dxf_split` 发行包。`box/`（封闭箱形几何核心）、`hole_color_policy.py`、`part_mark_layout.py`、`preview_fonts.py`、`weld_allowance_geometry.py` 与 `manufacturing_decision/` 是字节固定的 vendored 副本；原包保持零改动，BOX 认证链不受本包影响。运行时依赖仅 `ezdxf==1.4.4` 与 `shapely>=2.1,<3`。

## 源契约

`project_tekla_xbox_dxf_v1`：`tekla_structures / single_part_drawing / welded_xbox`。输入必须来自平台冻结并经 `steel_dxf_classifier` 分类确认的 XBOX 图纸（`BOX5`/`HK` 方言已归一化）。

## CLI

```bash
steel-dxf-split-xbox <input_dir> --output-dir <dir> \
    --authorize-project-tekla-xbox-dxf-v1 [--overwrite]
```

- 输入目录的纯度由平台保证（专用候选读取器只放行 XBOX）；逐图任务名取自文件名 stem（去除 `_拆板前`）。
- 批量信封：`{"schema": "steel-dxf-split-xbox-report/1", "items": [...], "success_count", "rejected_count", "exit_code"}`；致命错误输出 `{"status": "fatal", "error": {code, message_zh}}` 并返回退出码 2。
- 逐图产物（auto_accepted）：`<member>_正常拆板.dxf`、`<member>_余量增长.dxf`、`<member>_report.json`、`<member>_weld_allowance_report.json`。
- 余量档位：长度 ≤2000/+0、≤5000/+5、≤10000/+10、≤15000/+15、>15000/+20 mm；只对已证明的纵向可移动端加量。

## 发布认证

`release_evidence/xbox_release_attestation.json` 绑定三指纹：

- `manifest_sha256`：权威 20 组配对样本清单（`XBOX配对清单.json`）的文件字节 SHA-256；
- `gate_fingerprint`：`tools/acceptance_gate.json` 规范化哈希（校准 10 组 + 独立验收 10 组）；
- `implementation_fingerprint`：本包自有层 + vendored 层全部实现文件的逐文件 SHA-256。

任何实现文件改动都会触发 drift 拒绝；重新签发必须重跑完整验收：

```bash
python -m steel_dxf_split_xbox.tools.acceptance_check \
    --corpus-root "<XBOX图纸根目录>" --work-root "<scratch>"
```

20 组样本（含 `XBOX配对清单.json`）不入 Git；清单内含逐文件 SHA-256 可校验语料完整性。

## 受保护镜像

`write_xbox_protected_runtime_manifest()` 在删除 `.py` 源码前把源码态实现 payload 冻结到 `release_evidence/xbox_protected_runtime_manifest.json`；此后指纹从该 manifest 读取（与 `steel_dxf_split` 的 BOX 保护机制同型）。
