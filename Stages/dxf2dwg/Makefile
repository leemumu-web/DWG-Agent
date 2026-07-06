# dxf2dwg Makefile —— GitHub 下载后一键重建与常用操作。
# 依赖：uv（https://docs.astral.sh/uv/）、xvfb-run（xorg-server-xvfb）。
# ODA 已随仓库入库（tools/oda/ODAFileConverter.AppImage），无需额外下载。

.PHONY: install check-env test convert clean help

## 安装/重建依赖（读 uv.lock + .python-version，保证与开发环境一致）
install:
	uv sync

## 环境检查：确认 ODA / xvfb / ezdxf 就位
check-env:
	uv run python -m dxf_converter --check-env

## 跑单测（不调真实 ODA）
test:
	uv run pytest -q

## 转换 convert/input_dxf/ 下的 .dxf → convert/output_dwg/
convert:
	./convert/convert.sh

## 清空转换输出
clean:
	find convert/output_dwg -mindepth 1 ! -name '.gitkeep' -delete

## 清空输出并重转
reconvert: clean convert

help:
	@echo "用法: make <target>"
	@echo "  install    重建依赖（uv sync，GitHub 下载后第一步）"
	@echo "  check-env  环境检查（ODA / xvfb / ezdxf）"
	@echo "  test       跑单测"
	@echo "  convert    转 convert/input_dxf/ → convert/output_dwg/"
	@echo "  clean      清空 convert/output_dwg/"
	@echo "  reconvert  clean + convert"
