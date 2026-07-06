"""FastAPI 兼容的服务层：稳定的转换入口。

- 转换失败（找不到源文件、源目录不存在、超时、.err 副产物）→ 返回 success=False
  的结果对象，不抛异常。
- 环境错误（找不到 ODA、缺 xvfb）→ 构造 OdaConverter() 时抛 OdaConvertError，
  向上传播，由 FastAPI 层捕获映射为 5xx / 启动失败。
- get_converter() 惰性单例：FastAPI worker 不会每次请求重新探测 ODA。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from .engines import OdaConverter, OdaConvertError
from .engines.oda_converter import BatchResult, ConvertResult

logger = logging.getLogger(__name__)

_converter: Optional[OdaConverter] = None


def get_converter() -> OdaConverter:
    """返回惰性单例 OdaConverter。

    首次调用时探测 ODA 可执行文件与 xvfb；任一缺失即抛 OdaConvertError
    （环境错误）。FastAPI 可在 startup 事件里调用以预热并暴露配置问题。
    """
    global _converter
    if _converter is None:
        _converter = OdaConverter()
    return _converter


def reset_converter() -> None:
    """清空单例（测试用：让下次 get_converter 重新探测）。"""
    global _converter
    _converter = None


def convert_file(
    source: Union[Path, str],
    target_dir: Union[Path, str],
    *,
    version: Optional[str] = None,
    audit: Optional[bool] = None,
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    converter: Optional[OdaConverter] = None,
) -> ConvertResult:
    """转换单个 DXF 文件。

    转换失败返回 success=False 的 ConvertResult（不抛）；
    环境错误（ODA/xvfb 缺失）由 get_converter() 抛 OdaConvertError。

    version/audit/timeout/retries 默认 None，由 OdaConverter 的 default_* 决定，
    避免在 service 层硬编码默认值与 engine 的 _resolve_defaults 失效。
    """
    conv = converter or get_converter()
    return conv.convert_file(
        source=source, target_dir=target_dir,
        version=version, audit=audit, timeout=timeout, retries=retries,
    )


def convert_directory(
    source_dir: Union[Path, str],
    target_dir: Union[Path, str],
    *,
    version: Optional[str] = None,
    audit: Optional[bool] = None,
    recursive: bool = False,
    file_filter: str = "*.dxf",
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    converter: Optional[OdaConverter] = None,
) -> BatchResult:
    """转换目录下所有 DXF。

    转换失败在 BatchResult.results 里体现（不抛，含源目录不存在的情形）；
    环境错误（ODA/xvfb 缺失）抛 OdaConvertError。
    """
    conv = converter or get_converter()
    return conv.convert_directory(
        source_dir=source_dir, target_dir=target_dir,
        version=version, audit=audit, recursive=recursive,
        file_filter=file_filter, timeout=timeout, retries=retries,
    )


def convert(
    source: Union[Path, str],
    target_dir: Union[Path, str],
    *,
    version: Optional[str] = None,
    audit: Optional[bool] = None,
    recursive: bool = False,
    file_filter: str = "*.dxf",
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    converter: Optional[OdaConverter] = None,
) -> Union[ConvertResult, BatchResult]:
    """统一入口：源是文件返回 ConvertResult，是目录返回 BatchResult。

    不存在的路径按单文件处理 → 返回 success=False（找不到源文件），不抛异常。
    源目录不存在也返回失败结果（不抛）。两者都不抛转换失败。
    timeout 由 engine 的 default_timeout 决定（单文件 120 / 目录 120）；
    如需更长超时显式传 timeout。
    """
    source = Path(source).resolve()
    target_dir = Path(target_dir).resolve()

    # 只有明确是目录才走目录分支；不存在或不是目录都按单文件处理，
    # 让 convert_file 返回 success=False（"源文件不存在"），而非误入目录分支抛异常。
    if source.is_dir():
        return convert_directory(
            source, target_dir, version=version, audit=audit, recursive=recursive,
            file_filter=file_filter, timeout=timeout, retries=retries,
            converter=converter,
        )
    return convert_file(
        source, target_dir, version=version, audit=audit,
        timeout=timeout, retries=retries, converter=converter,
    )


__all__ = [
    "get_converter",
    "reset_converter",
    "convert",
    "convert_file",
    "convert_directory",
    "ConvertResult",
    "BatchResult",
    "OdaConvertError",
]
