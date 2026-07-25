$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
uv sync --extra dev
uv run python .\scripts\run_test_matrix.py
