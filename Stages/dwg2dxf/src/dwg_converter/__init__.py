"""DWG → DXF 转换后端库。

链路：DWG → ODA File Converter (subprocess) → DXF。
本包只负责转换链路本身，不做表格/文字解析（那是拿到 DXF 之后的事）。

对外入口在 service 层：convert / convert_file / convert_directory / get_converter。
结果对象 ConvertResult / BatchResult 带 to_dict()，可直接作为 API 响应体。

框架集成适配层在 framework 模块：提供健康检查、错误码映射、API 字典格式化，
与 complete_framework 的 FastAPI 异常体系对齐。
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

# 框架集成适配层
from .framework import (
    HealthStatus,
    health_check,
    to_api_dict,
    to_batch_api_dict,
    convert_with_health_check,
    ERROR_CODES,
)

__all__ = [
    # 转换入口
    "convert",
    "convert_file",
    "convert_directory",
    "get_converter",
    "reset_converter",
    # 结果类型
    "ConvertResult",
    "BatchResult",
    "OdaConvertError",
    # 环境检查
    "check_environment",
    # 框架集成
    "HealthStatus",
    "health_check",
    "to_api_dict",
    "to_batch_api_dict",
    "convert_with_health_check",
    "ERROR_CODES",
]
__version__ = "0.1.0"
