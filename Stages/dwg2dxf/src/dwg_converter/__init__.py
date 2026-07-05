"""DWG → DXF 转换后端库。

链路：DWG → ODA File Converter (subprocess) → DXF。
本包只负责转换链路本身，不做表格/文字解析（那是拿到 DXF 之后的事）。

对外入口在 service 层：convert / convert_file / convert_directory / get_converter。
结果对象 ConvertResult / BatchResult 带 to_dict()，可直接作为 API 响应体。
"""

from .service import (
    convert,
    convert_file,
    convert_directory,
    get_converter,
    reset_converter,
)
from .engines import ConvertResult, BatchResult, OdaConvertError
from .check_env import check_environment

__all__ = [
    "convert",
    "convert_file",
    "convert_directory",
    "get_converter",
    "reset_converter",
    "ConvertResult",
    "BatchResult",
    "OdaConvertError",
    "check_environment",
]
__version__ = "0.1.0"
