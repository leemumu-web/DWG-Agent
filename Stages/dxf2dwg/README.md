# dxf2dwg

把 AutoCAD DXF 文件批量转换成 DWG 的后端封装，底层调用 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) 的命令行模式。

```
DXF 文件/目录 → ODA File Converter (subprocess) → DWG
```

本模块只做**转换**这一件事：定位 ODA、按目录调用 CLI、超时/重试/隔离、批量转换、
扫描产物判断成败。不做表格/文字/线段解析（那是拿到 DWG 之后的下一步）。

---

## 功能

- **单文件 / 目录批量**：`convert()` 统一入口，按源是文件还是目录自动分派。
- **目录隔离**：单文件转换会把文件复制进临时源目录再调 ODA，避免误转同目录其他文件。
- **失败检测**：ODA 转换失败时进程退出码仍是 0，只在目标目录写 `<name>.dwg.err`。
  引擎会扫描该副产物，把它作为失败原因采集并自动清理，不依赖退出码判断成败。
- **超时 / 重试**：`subprocess.run(timeout=...)` 单次超时；`retries` 控制失败重试次数。
- **无头运行**：ODA 是 Qt GUI 程序，CLI 模式也要初始化 Qt 平台插件（仅有 xcb），
  无 X 服务器会崩溃。引擎默认自动探测并用 `xvfb-run -a` 包裹调用提供无头 X。
- **结构化结果**：`ConvertResult`（单文件）/ `BatchResult`（批量，含 `ok/total/failed/all_success`），
  均带 `to_dict()` 输出 JSON 安全的纯字典，可直接作为 API 响应体。
- **服务层 API 契约**：转换失败返回 `success=False` 结果对象（不抛）；环境错误
  （找不到 ODA、缺 xvfb）抛 `OdaConvertError`。`get_converter()` 惰性单例复用，
  避免每次请求重新探测。
- **环境自检**：`--check-env` 定位 ODA 可执行文件并报告 ezdxf 是否可用。

## 目录结构

```
dxf2dwg/
├── convert/                 # 批量转换工作区（input_dxf/ → output_dwg/ + convert.sh）
├── samples/                 # 示例（gitignore，不入库）
├── tools/oda/               # ODAFileConverter.AppImage（随仓库入库，开箱即用）
├── src/dxf_converter/
│   ├── __init__.py
│   ├── __main__.py          # CLI 入口
│   ├── check_env.py         # 环境探测（ODA 候选路径 + ezdxf 可导入性）
│   ├── service.py           # 稳定 API 入口：convert / convert_file / convert_directory / get_converter
│   ├── framework.py         # FastAPI 适配层：HealthStatus / health_check / to_api_dict / ERROR_CODES
│   └── engines/
│       ├── __init__.py
│       └── oda_converter.py # OdaConverter：subprocess 封装 + 产物采集 + 结果对象
├── tests/
│   └── test_converter.py
├── Makefile                 # 一键重建与常用操作
├── .python-version          # uv 据此自动选 Python 3.12
└── pyproject.toml
```

## 从 GitHub 下载后重建

GitHub clone 后,只需系统装了 `uv` 和 `xvfb-run`,一条命令重建:

```bash
git clone <repo> dxf2dwg && cd dxf2dwg
make install      # uv sync：按 uv.lock + .python-version 重建 .venv，依赖版本与开发一致
make check-env    # 确认 ODA / xvfb / ezdxf 就位
make test         # 跑单测
make convert      # 转 convert/input_dxf/ → convert/output_dwg/
```

ODA 二进制已随仓库入库(`tools/oda/ODAFileConverter.AppImage`,~82MB),无需额外下载。
`uv.lock` 也入库,保证所有人重建出的依赖版本完全一致。

## 安装

需要系统已装 [uv](https://docs.astral.sh/uv/) 与 `xvfb-run`(`xorg-server-xvfb` 包)。
Python 由 uv 按 `.python-version`(3.12)自动管理,无需系统预装。

```bash
uv sync                   # 转换链路零硬依赖
uv sync --extra parse     # 额外装 ezdxf，给 DXF 源文件校验用
```

运行时还需要的外部二进制:

1. **ODA File Converter** —— 已随仓库入库于 `tools/oda/ODAFileConverter.AppImage`。
   也可放同名无后缀二进制或装进 `$PATH`,`check_env.py` 的 `ODA_CANDIDATE_PATHS` 列出全部搜索位置。
2. **xvfb-run**(`xorg-server-xvfb` 包)—— 无头 X 服务器,让 ODA 在无显示器环境跑。

## 环境检查

```bash
uv run python -m dxf_converter --check-env
```

输出示例（已验证环境）：

```
=== DXF→DWG 环境检查 ===
  - 命中候选路径: .../tools/oda/ODAFileConverter.AppImage
  - ezdxf 可用: 1.4.4（源文件校验需要）
  ODA:     OK (转换必需)
  ezdxf:   OK (仅校验需要)
  总体:    OK
```

`ok` 只看 ODA 是否就位；ezdxf 缺失不影响转换，只影响源 DXF 校验。

## 命令行用法

```bash
uv run python -m dxf_converter <source> [选项]
```

| 参数 | 说明 |
| --- | --- |
| `source` | 单个 `.dxf` 文件，或包含 `.dxf` 的目录（`--check-env` 时可省略） |
| `-o, --output` | 输出目录，默认 `samples/output` |
| `-r, --recursive` | 目录模式下递归子目录 |
| `--version` | DWG 版本，默认 `ACAD2018` |
| `--no-audit` | 关闭转换时的 audit 修复（默认开启） |
| `--timeout` | 单次调用超时秒，CLI 默认 600 |
| `--check-env` | 只做环境检查后退出 |
| `-v, --verbose` | 详细日志（DEBUG） |

退出码：0 全部成功；1 有转换失败；2 环境错误（ODA/xvfb 缺失）。

```bash
# 单文件
uv run python -m dxf_converter path/to/a.dxf -o path/to/out

# 批量（默认输出到 samples/output）
uv run python -m dxf_converter samples/input -r
```

批量输出末行会打印汇总，例如：

```
  批量转换: 46/46 成功, 0 失败, 耗时 11.19s
  [OK] example-001.dxf -> example-001.dwg (11.19s)
  ...
```

## 代码用法

```python
from dxf_converter import convert, get_converter

# 统一入口：源是文件返回 ConvertResult，是目录返回 BatchResult
single = convert("a.dxf", "out/")
print(single.target, single.success, single.duration)   # ConvertResult

batch = convert("input_dir/", "out/")
print(batch.ok, batch.total, batch.failed, batch.all_success)  # BatchResult
for r in batch.results:
    if not r.success:
        print(r.source, r.error)   # 失败原因（来自 .dwg.err）

# JSON 安全字典（含 Path 转字符串、不含 returncode/stdout/stderr）
print(single.to_dict())
print(batch.to_dict())
```

**异常约定**：

- **转换失败**（找不到源文件、源目录不存在、超时、`.err` 副产物）→ 返回 `success=False`
  的结果对象，**不抛异常**。调用方读 `result.success` / `result.error` 自行处理。
- **环境错误**（找不到 ODA、缺 xvfb）→ 抛 `OdaConvertError`，向上传播。
  这类错误应在进程启动时暴露，而非混在单次转换结果里。

`get_converter()` 返回惰性单例 `OdaConverter`，避免每次转换重新探测可执行文件；
想自定义参数（如关闭 xvfb）可自己 `OdaConverter(xvfb_run=False)` 后传给 `convert(... converter=conv)`。

主要参数（`convert` / `convert_file` / `convert_directory` 通用，关键字入参）：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `version` | `ACAD2018` | ODA 输出版本 |
| `audit` | `True` | 转换时是否 audit 修复 |
| `timeout` | 单文件 120 / 批量 600 | 单次调用超时秒 |
| `retries` | `0` | 失败重试次数（不含首次） |
| `recursive` | `False` | 目录模式是否递归 |
| `file_filter` | `*.dxf` | 目录模式匹配文件名 |
| `converter` | `None`(用单例) | 传入自定义 `OdaConverter` |

`OdaConverter` 自身的字段：`executable`、`default_version`、`default_audit`、
`default_timeout`、`default_retries`、`xvfb_run`（`None`=自动探测，`False` 需自备 DISPLAY）。

## FastAPI 集成

本库不依赖 FastAPI / pydantic（转换链路零硬依赖），但服务层的契约是为 API 后端设计的：
结果对象 `to_dict()` 输出纯 JSON 字典，转换失败不抛异常，环境错误抛 `OdaConvertError`。
后续 FastAPI 后端按下面骨架接入即可：

```python
from fastapi import FastAPI, HTTPException, UploadFile
from pathlib import Path
import uuid
from dxf_converter import convert_file, get_converter, OdaConvertError

app = FastAPI()

@app.on_event("startup")
def warm():
    # 预热：构造期就会因缺 ODA/xvfb 抛 OdaConvertError —— FastAPI 启动直接失败，
    # 在部署时就暴露环境问题，而不是等第一个请求才发现。
    get_converter()

@app.post("/convert")
async def convert_dxf(file: UploadFile):
    job = Path(f"/tmp/dxf-jobs/{uuid.uuid4().hex}")
    (job / "output").mkdir(parents=True)
    inp = job / "input.dxf"
    inp.write_bytes(await file.read())

    result = convert_file(inp, job / "output")   # 转换失败不抛
    if not result.success:
        raise HTTPException(422, detail=result.to_dict())
    return result.to_dict()
```

API 层职责：上传落盘 → 调 `convert_file` → 查 `result.success` 映射 HTTP 状态
（成功 200、转换失败 422、环境错误 500）。本库只负责到 `result.to_dict()` 为止。

更完整的框架集成可用 `framework` 适配层，它提供健康检查、错误码映射和 API 字典格式化，
与 `complete_framework` 的 FastAPI 异常体系对齐：

```python
from dxf_converter.framework import (
    HealthStatus, health_check, to_api_dict, to_batch_api_dict,
    convert_with_health_check, ERROR_CODES,
)

# 健康检查（FastAPI startup / GET /health/oda）
status = health_check()
if not status.healthy:
    raise service_unavailable(status.error_code, status.message)

# 带健康预检的转换
result, health = convert_with_health_check("a.dxf", "out/")
if health is not None:
    raise service_unavailable(health.error_code, health.message)

# 结果转 API 字典
body = to_api_dict(result)
# → {"success": true, "data": {...}} 或 {"success": false, "error": {"code": "DWG_...", ...}}
```

## ODA CLI 参数顺序

引擎按以下顺序拼装（见 `oda_converter._build_cmd`）：

```
ODAFileConverter <source_dir> <target_dir> <version> <output_type> \
                 <recursive> <audit> <file_filter>
```

- ⚠️ 不同版本 ODA 参数顺序可能有差异，首次部署需用 `ODAFileConverter --help` 或
  测试命令确认。
- ⚠️ ODA 按目录工作，不支持单文件参数。单文件转换复制进隔离临时源目录执行。
- ⚠️ ODA 转换失败时退出码仍为 0，只写 `<name>.dwg.err` 副产物。引擎扫描目标目录
  实际产物判断成败，把 `.err` 内容作为失败原因并清理。

## 设计取舍

- **不依赖 `ezdxf.odafc`**：生产后端需要超时、stderr/stdout 捕获、任务目录隔离、
  重试、批量、失败校验，subprocess 比 `ezdxf.odafc` 更可控；ezdxf 整个移出硬依赖。
- **按产物判定成败**：不信任 ODA 退出码，扫描目标目录的 `.dwg` / `.dwg.err` 实际存在。
  对 returncode=0 但未生成产物的"静默失败"有专门检测和错误信息。
- **单文件隔离**：复制进临时源目录，避免 ODA 误转同目录其他文件。
- **防御性错误处理**：`convert_file` / `convert_directory` 对外**绝不抛异常**。
  所有文件系统错误（`OSError`/`PermissionError`/`FileExistsError`）和 ODA 运行时崩溃
  （`FileNotFoundError`）都会被捕获并转为 `success=False` 结果。只有环境错误抛
  `OdaConvertError`——严格区分"转换失败"与"部署问题"。
- **xvfb 自动探测**：`shutil.which("xvfb-run")` 命中即启用，桌面环境可传 `xvfb_run=False`。
- **服务层异常契约**：转换失败返回结果对象（不抛），环境错误抛异常 —— 让 API 层
  能把"转换失败"映射成 422 结构化响应、把"环境错误"映射成 5xx，互不混淆。
- **零硬依赖**：`to_dict()` 用纯 dict 而非 pydantic，转换库本身不绑 FastAPI；
  API 层自行用 pydantic 包装响应模型。

## 已验证

- 46 个真实图纸 DXF → ACAD2018 DWG，**46/46 成功**，约 11s，31MB 总输出。
- 输出格式确认：`file` 命令检测为 `DWG AutoDesk AutoCAD 2018/2019/2020`。
- 文件名含 `@` / 中文字符不影响 filter 匹配（用 `*.dxf`）。
- 单文件 / 批量 / `--check-env` / `convert.sh` 四条路径均通过。
- **28 个单测**覆盖以下维度（含 14 项对抗性边界测试）：

| 类别 | 测试项 |
|------|--------|
| 命令拼装 | DWG 输出类型、参数顺序 |
| 单文件转换 | 成功/缺源/`.dwg.err` 失败/超时/复制失败/ODA 崩溃 |
| 目录批量 | 产物采集/部分失败/源目录不存在/空目录/扩展名不匹配 |
| 文件系统异常 | `mkdir` 冲突/`shutil.copy2` 权限错误/ODA 运行时 FileNotFoundError |
| 特殊文件名 | 中文 Unicode / `@` 符号 |
| 结果序列化 | `to_dict()` JSON 安全/不含 subprocess 内部字段 |
| 错误码映射 | `DWG_TIMEOUT` / `DWG_SOURCE_MISSING` / `DWG_CONVERSION_FAILED` |
| 框架适配 | `HealthStatus` / `health_check()` / `get_converter()` 单例 |

## 健壮性保证

本引擎对以下异常场景**全部保证返回 `success=False` 结果对象，不抛异常**：

- 源文件不存在 / 不可读
- 输出路径被非目录文件占用
- 磁盘满或权限不足导致源文件复制失败
- ODA 二进制在构造后被删除/损坏（`FileNotFoundError`）
- ODA 执行权限被撤销（`PermissionError`）
- ODA 静默失败（退出码 0 但未生成产物）
- ODA 写 `.dwg.err` 错误副产物
- ODA 调用超时（`subprocess.TimeoutExpired`）

只有**环境错误**（构造期找不到 ODA 可执行文件 / 显式要求 xvfb 但未安装）才抛
`OdaConvertError`。这些应在进程启动时暴露，而非混入单次转换结果。

## 与 dwg2dxf 的关系

本模块是 `dwg2dxf` 的镜像工程，代码结构完全相同、方向相反：

| | dwg2dxf | dxf2dwg |
|---|---|---|
| 输入 | `.dwg` | `.dxf` |
| 输出 | `.dxf` | `.dwg` |
| ODA output_type | `DXF` | `DWG` |
| 错误副产物 | `.dxf.err` | `.dwg.err` |
| 包名 | `dwg_converter` | `dxf_converter` |
| 错误码前缀 | `DXF_*` | `DWG_*` |

两端接口签名一致（`convert` / `convert_file` / `convert_directory` / `get_converter`），
可在同一 FastAPI 后端并排使用，覆盖 DWG ↔ DXF 双向转换。
