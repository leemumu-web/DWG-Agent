#!/usr/bin/env bash
#
# 把 convert/input_dxf/ 下的 .dxf 批量转成 .dwg 输出到 convert/output_dwg/。
#
# 所有调用都在项目内：用项目自带的 uv 虚拟环境跑 dxf_converter，
# ODA 可执行文件由 dxf_converter 自动从 tools/oda/ 或 $PATH 探测。
#
# 用法:
#   ./convert/convert.sh                # 转 input_dxf/ 下全部 .dxf
#   ./convert/convert.sh a.dxf          # 只转指定文件（相对 input_dxf/ 或绝对路径）
#   ./convert/convert.sh -v             # 详细日志
#   ./convert/convert.sh --clean        # 先清空 output_dwg/ 再转
#
# 退出码: 0 全部成功; 1 有转换失败; 2 环境错误(ODA/xvfb 缺失)。

set -euo pipefail

# ---- 定位项目根（脚本在 <root>/convert/convert.sh） ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

INPUT_DIR="${PROJECT_ROOT}/convert/input_dxf"
OUTPUT_DIR="${PROJECT_ROOT}/convert/output_dwg"

# ---- 参数解析 ----
VERBOSE=0
CLEAN=0
TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose) VERBOSE=1; shift ;;
        --clean)      CLEAN=1; shift ;;
        -h|--help)
            sed -n '2,16p' "$0"
            exit 0
            ;;
        *)            TARGETS+=("$1"); shift ;;
    esac
done

# ---- 进入项目根，确保 uv 用的是本项目的 .venv ----
cd "${PROJECT_ROOT}"

# ---- --clean: 清空输出目录（保留 .gitkeep） ----
if [[ ${CLEAN} -eq 1 ]]; then
    echo "[clean] 清空 ${OUTPUT_DIR}"
    find "${OUTPUT_DIR}" -mindepth 1 ! -name '.gitkeep' -delete
fi

mkdir -p "${OUTPUT_DIR}"

UV_ARGS=(uv run --project "${PROJECT_ROOT}")
if [[ ${VERBOSE} -eq 1 ]]; then
    UV_ARGS+=(python -m dxf_converter -v)
else
    UV_ARGS+=(python -m dxf_converter)
fi

# ---- 转换 ----
if [[ ${#TARGETS[@]} -eq 0 ]]; then
    # 无参数：转整个 input_dxf 目录
    echo "[convert] ${INPUT_DIR} -> ${OUTPUT_DIR}"
    "${UV_ARGS[@]}" "${INPUT_DIR}" -o "${OUTPUT_DIR}"
else
    # 有参数：逐个转指定文件。单个文件失败不中止后续文件，
    # 累积退出码：任一失败→1，任一环境错误(exit 2)→2 优先。
    overall=0
    for t in "${TARGETS[@]}"; do
        # 相对路径相对 input_dxf/ 解析；绝对路径直接用
        if [[ "$t" = /* ]]; then
            src="$t"
        else
            src="${INPUT_DIR}/$t"
        fi
        echo "[convert] ${src} -> ${OUTPUT_DIR}"
        # 关 set -e 跑单次：失败不退出脚本，手动取退出码
        set +e
        "${UV_ARGS[@]}" "${src}" -o "${OUTPUT_DIR}"
        rc=$?
        set -e
        if [[ $rc -ne 0 ]]; then
            if [[ $rc -eq 2 ]]; then
                overall=2
            elif [[ $overall -ne 2 ]]; then
                overall=1
            fi
        fi
    done
    exit $overall
fi
