#!/usr/bin/env bash
# =============================================================================
# convert_all.sh — 一键转换 dxf_input 下所有子目录
#
# 这是 convert.sh --all 的快捷方式。
# 运行前请确保已将 DXF 文件夹放入 convert/dxf_input/。
#
# 用法:
#   ./convert_all.sh
#
# 快速设置示例:
#   # 从 SKG dxfs 创建符号链接 (推荐 — 不复制文件)
#   for d in /path/to/dxfs/*/; do
#       ln -s "$d" convert/dxf_input/
#   done
#   ./convert_all.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd")"
exec bash "${SCRIPT_DIR}/convert.sh" --all
