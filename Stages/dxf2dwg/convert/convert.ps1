<#
.SYNOPSIS
    把 convert/input_dxf/ 下的 .dxf 批量转成 .dwg 输出到 convert/output_dwg/。

.DESCRIPTION
    所有调用都在项目内：用项目自带的 uv 虚拟环境跑 dxf_converter，
    ODA 可执行文件由 dxf_converter 自动从 tools/oda/ 或 $PATH 探测。

    用法:
        .\convert.ps1                  # 转 input_dxf/ 下全部 .dxf
        .\convert.ps1 a.dxf            # 只转指定文件（相对 input_dxf/ 或绝对路径）
        .\convert.ps1 a.dxf b.dxf      # 转多个
        .\convert.ps1 -Clean           # 先清空 output_dwg/ 再转
        .\convert.ps1 -Verbose         # 详细日志
        .\convert.ps1 -Clean -Verbose a.dxf

    退出码（$LASTEXITCODE）：0 全部成功；1 有转换失败；2 环境错误（uv/ODA/xvfb 缺失）。

    Windows 部署注意：仓库自带的 tools/oda/ODAFileConverter.AppImage 是 Linux AppImage，
    Windows 上无法直接运行。Windows 用户需自行下载 Windows 版 ODAFileConverter.exe 放到
    tools/oda/ 或加入 $PATH。xvfb-run 在 Windows 上不需要（有桌面会话即可）。
#>

param(
    # 待转文件名（可选，不指定则转全部）
    [Parameter(Position=0, ValueFromRemainingArguments)]
    [string[]]$Targets,

    # 先清空 output_dwg/（保留 .gitkeep）
    [switch]$Clean,

    # 详细日志（DEBUG）
    [switch]$Verbose
)

# ---- 定位项目根（脚本在 <root>/convert/convert.ps1） ----
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir

$InputDir = Join-Path $ProjectRoot "convert" "input_dxf"
$OutputDir = Join-Path $ProjectRoot "convert" "output_dwg"

# ---- 进入项目根（让 uv 找到 .venv 和 pyproject.toml） ----
Set-Location $ProjectRoot

# ---- 确认 uv 可用（环境错误 → 退出码 2） ----
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    Write-Error "未找到 uv。请先安装 uv（https://docs.astral.sh/uv/）。"
    exit 2
}

# ---- -Clean: 清空输出目录（保留 .gitkeep） ----
if ($Clean) {
    Write-Host "[clean] 清空 $OutputDir"
    Get-ChildItem -Path $OutputDir -Force | Where-Object { $_.Name -ne ".gitkeep" } | Remove-Item -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ---- 拼装 uv 命令 ----
$UvArgs = @("run", "--project", $ProjectRoot, "python", "-m", "dxf_converter")
if ($Verbose) {
    $UvArgs += "-v"
}

# ---- 转换 ----
if ($Targets.Count -eq 0) {
    # 无参数：转整个 input_dxf 目录
    Write-Host "[convert] $InputDir -> $OutputDir"
    & "uv" @UvArgs $InputDir "-o" $OutputDir
    # uv 进程退出码：0 成功 / 1 转换失败 / 2 环境错误
    if ($LASTEXITCODE -eq $null) { exit 2 }   # uv 异常未设退出码 → 视为环境错误
    exit $LASTEXITCODE
}

# 有参数：逐个转指定文件。单文件失败不中止后续；env 错误(exit 2)优先于转换失败(1)。
$failed = 0
$envError = $false
foreach ($t in $Targets) {
    if ([System.IO.Path]::IsPathRooted($t)) {
        $src = $t
    } else {
        $src = Join-Path $InputDir $t
    }
    Write-Host "[convert] $src -> $OutputDir"
    & "uv" @UvArgs $src "-o" $OutputDir
    if ($LASTEXITCODE -ne 0) { $failed = 1 }
    if ($LASTEXITCODE -eq 2) { $envError = $true }
}
# 环境错误（exit 2）优先于转换失败（exit 1）
if ($envError) { exit 2 }
exit $failed
