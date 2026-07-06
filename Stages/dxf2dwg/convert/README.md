# convert —— DXF 批量转 DWG 工作区

放 DXF、出 DWG、跑脚本,一条命令完成批量转换。所有调用都在项目内
(项目自带 `uv` 虚拟环境 + `tools/oda/` 的 ODA 二进制),外部接口无需自备 Python 环境。

```
convert/
├── input_dxf/     # 放 .dxf 输入文件
├── output_dwg/    # 转换出的 .dwg 输出（脚本自动创建）
├── convert.sh     # 转换脚本（Linux / macOS / WSL）
└── convert.ps1    # 转换脚本（Windows PowerShell）
```

两份脚本功能一致,按平台二选一。**Windows 注意**:仓库自带的 `tools/oda/ODAFileConverter.AppImage`
是 Linux AppImage,Windows 上跑不了,需自行下载 Windows 版 `ODAFileConverter.exe` 放到
`tools/oda/` 或加入 `$PATH`;Windows 也不需要 `xvfb-run`(有桌面会话即可)。

## 快速开始(3 步)

**Linux / macOS / WSL:**

```bash
# 1. 把 dxf 文件丢进 input_dxf/
cp /path/to/*.dxf convert/input_dxf/

# 2. 跑脚本(项目根目录执行)
./convert/convert.sh

# 3. 拿结果
ls convert/output_dwg/    # 同名 .dwg 文件
```

**Windows PowerShell:**

```powershell
# 1. 把 dxf 文件丢进 input_dxf/
Copy-Item C:\path\to\*.dxf convert\input_dxf\

# 2. 跑脚本(项目根目录执行)
.\convert\convert.ps1

# 3. 拿结果
Get-ChildItem convert\output_dwg\    # 同名 .dwg 文件
```

`input_dxf/` 下的 **所有** `.dxf` 会被一次 ODA 调用批量转换。
产物文件名与源同名,仅后缀变 `.dwg`。

## 外部接口接入(最常用)

外部程序(后端服务、定时任务、CI)只需做三件事:

1. 把待转的 `.dxf` 复制/写入 `convert/input_dxf/`(或符号链接)。
2. 执行 `./convert/convert.sh --clean`(`--clean` 先清空旧输出,避免残留)。
3. 读 `convert/output_dwg/` 下的 `.dwg`,按同名映射回源文件。

```python
# Python 外部接口示例
import subprocess, shutil
from pathlib import Path

PROJ = Path("/home/Creeken/Paper/CAD_research/complete_framework/Stages/dxf2dwg")
inp, outp = PROJ / "convert/input_dxf", PROJ / "convert/output_dwg"

# 写入待转文件
shutil.copy("user_upload.dxf", inp / "user_upload.dxf")

# 调脚本(--clean 保证输出干净),拿退出码判断
rc = subprocess.run(
    [str(PROJ / "convert/convert.sh"), "--clean"],
    cwd=PROJ, capture_output=True, text=True,
).returncode
# rc: 0=全部成功  1=有转换失败  2=环境错误(ODA/xvfb 缺失)

# 取产物
dwg = outp / "user_upload.dwg"
if dwg.exists():
    ...
```

退出码是判断成败的唯一可靠途径(脚本不输出机器可读格式)。失败时去
`convert/output_dwg/` 找 `<name>.dwg.err` 会被自动清理,所以失败信息只进 stdout/stderr,
外部接口应捕获 `subprocess` 的 stdout 里 `[FAIL]` 行拿到失败文件名与原因。

## 命令行参数

**Linux / macOS / WSL (convert.sh):**

```bash
./convert/convert.sh                 # 转 input_dxf/ 下全部 .dxf
./convert/convert.sh a.dxf           # 只转指定文件(相对 input_dxf/ 或绝对路径)
./convert/convert.sh a.dxf b.dxf     # 转多个
./convert/convert.sh --clean         # 先清空 output_dwg/ 再转
./convert/convert.sh -v              # 详细日志(DEBUG)
./convert/convert.sh --help          # 帮助
```

参数可组合:`./convert/convert.sh --clean -v a.dxf`。

**Windows PowerShell (convert.ps1):**

```powershell
.\convert\convert.ps1                     # 转 input_dxf/ 下全部 .dxf
.\convert\convert.ps1 a.dxf               # 只转指定文件
.\convert\convert.ps1 a.dxf b.dxf         # 转多个
.\convert\convert.ps1 -Clean              # 先清空 output_dwg/ 再转
.\convert\convert.ps1 -Verbose            # 详细日志(DEBUG)
.\convert\convert.ps1 -Clean -Verbose a.dxf
```

> 注:`convert.ps1` 无 `--help`;参数名用 PowerShell 风格(`-Clean`/`-Verbose`,
> 不带 `--` 前缀)。退出码与 `convert.sh` 一致。

## 退出码

| 码 | 含义 | 外部接口处理 |
|---|---|---|
| 0 | 全部转换成功 | 读 output_dwg/ 拿产物 |
| 1 | 有转换失败 | 解析 stdout 的 `[FAIL]` 行,重试或上报失败文件 |
| 2 | 环境错误(ODA/xvfb 缺失) | 这是部署问题,不该重试,需修环境 |

## 并发与隔离

- 脚本用 `xvfb-run -a` 包裹 ODA(自动选空闲 display 号),可多实例并发跑不同 input 目录,
  但 **同一 `output_dwg/` 不要并发写**——会互相覆盖。多任务请各自用独立子目录或独立工作区。
- 单次调用是 ODA 的一次进程批量处理整个目录,**不要**为了"并行"把文件拆成多份分别调,
  那样反而慢(每次都有 ODA 启动开销)。

## 用项目 API 而非脚本(更可控)

脚本适合"丢文件→拿结果"的简单场景。若外部接口需要更细控制(单文件、超时、重试、
结构化结果),直接调项目 Python API,不要走 shell:

```python
from dxf_converter import convert, get_converter
from pathlib import Path

get_converter()  # 预热(可选),首次会探测 ODA

# 批量
batch = convert("convert/input_dxf/", "convert/output_dwg/")
print(batch.ok, batch.total, batch.all_success)
print(batch.to_dict())  # JSON 安全字典,可直接回 API 响应

# 单文件
r = convert("a.dxf", "out/")
print(r.success, r.target, r.to_dict())
```

异常约定:转换失败返回 `success=False` 结果对象(不抛);环境错误抛 `OdaConvertError`。
详见项目根 `README.md` 的"代码用法"与"FastAPI 集成"小节。

## 前置依赖

GitHub 下载后首次使用,在项目根执行一次:

```bash
make install     # uv sync,装好 .venv
make check-env   # 确认 ODA / xvfb / ezdxf 就位
```

需要系统已装 `uv` 和 `xvfb-run`(`xorg-server-xvfb` 包)。ODA 二进制已随仓库入库
(`tools/oda/ODAFileConverter.AppImage`),无需额外下载。
