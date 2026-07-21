#!/usr/bin/env bash
# DWG-Agent — 统一质量门禁
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
MODE="quick"
ALLOW_BLOCKED=false
PASS_COUNT=0
FAIL_COUNT=0
BLOCKED_COUNT=0

for arg in "$@"; do
    case "$arg" in
        quick|full) MODE="$arg" ;;
        --allow-blocked) ALLOW_BLOCKED=true ;;
        -h|--help)
            echo "用法: bash scripts/verify.sh [quick|full] [--allow-blocked]"
            exit 0
            ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

cd "$PROJECT_ROOT"

run_gate() {
    local label="$1"
    shift
    echo ""
    echo "── $label ──"
    "$@"
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "PASS: $label"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo "FAIL: $label (exit=$rc)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    return 0
}

run_optional_gate() {
    local label="$1"
    shift
    echo ""
    echo "── $label ──"
    "$@"
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "PASS: $label"
        PASS_COUNT=$((PASS_COUNT + 1))
    elif $ALLOW_BLOCKED; then
        echo "BLOCKED: $label (外部服务、sudo 或 Windows 依赖不可用，exit=$rc)"
        BLOCKED_COUNT=$((BLOCKED_COUNT + 1))
    else
        echo "FAIL: $label (exit=$rc；可用 --allow-blocked 标记外部依赖阻塞)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    return 0
}

run_gate "Shell 语法" bash -c 'find scripts -name "*.sh" -print0 | xargs -0 -n1 bash -n' \
    -- "$PROJECT_ROOT"
run_gate "Python 静态检查" bash -c \
    'cd backend && uv run ruff check app tests ../tests/run_full_verify.py' \
    -- "$PROJECT_ROOT"
run_gate "架构契约与模块归属" bash -c \
    'cd backend && uv run python ../scripts/architecture/snapshot_contracts.py --check && uv run python ../scripts/architecture/check_module_catalog.py && uv run python ../scripts/architecture/check_partition_docs.py' \
    -- "$PROJECT_ROOT"
run_gate "聚焦后端与脚本回归" bash -c \
    'cd backend && uv run pytest -q tests/infrastructure/test_scripts.py tests/infrastructure/test_forward_to_win11_script.py tests/infrastructure/test_compose.py tests/files/test_file_service.py tests/files/test_file_transfer_service.py tests/files/test_adversarial_files.py tests/contracts/test_frontend_contract.py' \
    -- "$PROJECT_ROOT"
run_gate "API/文档一致性" bash -c 'make docs-check' -- "$PROJECT_ROOT"
run_gate "前端生产构建" bash -c 'cd frontend && npm run build' -- "$PROJECT_ROOT"

if [ "$MODE" = "full" ]; then
    run_gate "完整后端 pytest" bash -c 'cd backend && uv run pytest -q' -- "$PROJECT_ROOT"
    run_gate "Alembic 模型检查" bash -c 'cd backend && uv run alembic check' -- "$PROJECT_ROOT"
    run_gate "基础设施验证" bash infra/verification/verify.sh
    run_gate "Compose 配置" docker compose config --quiet
    run_optional_gate "隔离 MySQL 迁移" bash scripts/db.sh migration-test
    run_optional_gate "DWG→DXF Stage" bash -c 'cd Stages/dwg2dxf && uv run pytest -q' -- "$PROJECT_ROOT"
    run_optional_gate "DXF→DWG Stage" bash -c 'cd Stages/dxf2dwg && uv run pytest -q' -- "$PROJECT_ROOT"
    run_gate "DXF→Excel Stage" bash -c 'cd Stages/dxf2excel && uv run pytest -q' -- "$PROJECT_ROOT"
    run_gate "Steel DXF Classifier Stage" bash -c \
        'cd Stages/steel_dxf_classifier_v1.1.0 && uv run pytest -q' -- "$PROJECT_ROOT"
    run_optional_gate "Excel Final Stage" bash -c 'cd Stages/excel_final && uv run pytest -q multi_split/tests' -- "$PROJECT_ROOT"
    run_gate "前端浏览器回归" bash -c 'cd frontend && npx playwright test' -- "$PROJECT_ROOT"
fi

echo ""
echo "验证汇总: PASS=$PASS_COUNT FAIL=$FAIL_COUNT BLOCKED=$BLOCKED_COUNT MODE=$MODE"
[ "$FAIL_COUNT" -eq 0 ]
