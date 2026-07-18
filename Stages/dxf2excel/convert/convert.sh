#!/usr/bin/env bash
# =============================================================================
# convert.sh — 将 dxf_input 下的一个子目录批量转换为 .xlsx
#
# 用法:
#   ./convert.sh <子目录名>           转换 dxf_input/<子目录名> → excel_output/<子目录名>.xlsx
#   ./convert.sh --all                转换 dxf_input 下所有子目录 (等同 convert_all.sh)
#   ./convert.sh --list               列出 dxf_input 下可用的子目录
#
# 示例:
#   ./convert.sh 排版1                → excel_output/排版1.xlsx
#   ./convert.sh 排版2                → excel_output/排版2.xlsx
#   ./convert.sh --all                → 批量转换所有
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INPUT_BASE="${SCRIPT_DIR}/dxf_input"
OUTPUT_DIR="${SCRIPT_DIR}/excel_output"

# 确保输出目录存在
mkdir -p "$OUTPUT_DIR"

# ---- 帮助信息 ----
usage() {
    echo "用法: $(basename "$0") <子目录名|--all|--list>"
    echo ""
    echo "  子目录名   转换 dxf_input/<子目录名> 下的所有 .dxf → excel_output/<子目录名>.xlsx"
    echo "  --all      转换 dxf_input 下的所有子目录"
    echo "  --list     列出 dxf_input 下可用的子目录及其 .dxf 文件数量"
    echo ""
    echo "目录结构:"
    echo "  convert/"
    echo "  ├── dxf_input/"
    echo "  │   ├── 排版1/          ← 放 .dxf 文件"
    echo "  │   ├── 排版2/"
    echo "  │   └── ..."
    echo "  └── excel_output/"
    echo "      ├── 排版1.xlsx        ← 输出"
    echo "      └── ..."
    exit 0
}

# ---- 列出可用子目录 ----
list_dirs() {
    echo "dxf_input 下可用的子目录:"
    echo ""
    if [ ! -d "$INPUT_BASE" ] || [ -z "$(ls -A "$INPUT_BASE" 2>/dev/null)" ]; then
        echo "  (空 — 请将 DXF 文件夹放入 dxf_input/)"
        return
    fi
    for d in "$INPUT_BASE"/*/; do
        dirname="$(basename "$d")"
        count=$(find "$d" -maxdepth 1 -name "*.dxf" | wc -l)
        printf "  %-30s %4d 个 .dxf 文件\n" "$dirname" "$count"
    done
}

# ---- 转换单个子目录 ----
convert_one() {
    local subdir="$1"

    # 去掉可能的 dxf_input/ 前缀和尾部斜杠
    subdir="${subdir#dxf_input/}"
    subdir="${subdir%/}"

    local input_path="${INPUT_BASE}/${subdir}"
    local output_file="${OUTPUT_DIR}/${subdir}.xlsx"

    # 检查输入目录
    if [ ! -d "$input_path" ]; then
        echo "错误: 目录不存在 — $input_path"
        echo ""
        echo "可用目录:"
        list_dirs
        exit 1
    fi

    # 统计文件 (解析符号链接)
    local dxf_count
    dxf_count=$(find -L "$input_path" -maxdepth 1 -name "*.dxf" 2>/dev/null | wc -l)
    if [ "$dxf_count" -eq 0 ]; then
        echo "错误: $input_path 中没有 .dxf 文件"
        exit 1
    fi

    echo "============================================"
    echo "  转换: $subdir"
    echo "  输入: $input_path"
    echo "  输出: $output_file"
    echo "  文件: $dxf_count 个 .dxf"
    echo "============================================"
    echo ""

    # 切换到项目目录以使用 uv 环境
    cd "$PROJECT_DIR"

    # 执行提取
    uv run dxf2excel extract "$input_path" --output "$output_file"

    local exit_code=$?

    echo ""
    if [ $exit_code -eq 0 ]; then
        echo "✅ 完成 — $output_file"
        # 显示文件大小
        if [ -f "$output_file" ]; then
            local size
            size=$(du -h "$output_file" | cut -f1)
            echo "   文件大小: $size"
        fi
    else
        echo "❌ 失败 (exit code: $exit_code) — $subdir"
    fi

    return $exit_code
}

# ---- 转换所有子目录 ----
convert_all() {
    if [ ! -d "$INPUT_BASE" ] || [ -z "$(ls -A "$INPUT_BASE" 2>/dev/null)" ]; then
        echo "错误: dxf_input/ 为空，请先将 DXF 文件夹放入。"
        echo ""
        echo "示例:"
        echo "  cp -r /path/to/排版1 convert/dxf_input/"
        exit 1
    fi

    local total=0
    local success=0
    local failed=0
    local failed_dirs=""

    echo "============================================"
    echo "  批量转换: dxf_input/* → excel_output/"
    echo "============================================"
    echo ""

    for d in "$INPUT_BASE"/*/; do
        [ -d "$d" ] || continue
        local dirname
        dirname="$(basename "$d")"
        total=$((total + 1))

        if convert_one "$dirname"; then
            success=$((success + 1))
        else
            failed=$((failed + 1))
            failed_dirs="$failed_dirs  $dirname\n"
        fi
        echo ""
    done

    echo "============================================"
    echo "  汇总: $total 个目录, $success 成功, $failed 失败"
    if [ "$failed" -gt 0 ]; then
        echo "  失败目录:"
        echo -e "$failed_dirs"
    fi
    echo "============================================"
}

# ---- 入口 ----
case "${1:-}" in
    --help|-h|help)
        usage
        ;;
    --list|-l|list)
        list_dirs
        ;;
    --all|-a|all)
        convert_all
        ;;
    "")
        echo "错误: 请指定子目录名、--all 或 --list"
        echo ""
        usage
        ;;
    *)
        convert_one "$1"
        ;;
esac
