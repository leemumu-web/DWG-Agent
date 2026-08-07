#!/bin/bash
# 核验工具：对一批图运行独立核验脚本并输出
# 用法: verify_batch.sh <图列表...>
cd /home/Creeken/Paper/CAD_research/complete_framework/Stages/BOX左右进读取
uv run python tools/verify_independent.py "$@"
