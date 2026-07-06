"""环境检查：定位 ODA File Converter 可执行文件与可选的 ezdxf。

ODA 的 AppImage / 原生二进制可能装在多处，这里按优先级在常见路径里搜索，
找不到时返回明确错误，方便上层决定是否提示用户安装。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# 候选路径（按优先级）—— 只保留 $PATH 覆盖不到的位置。
# $PATH 中的路径（/usr/bin、/usr/local/bin、~/.local/bin 等）交给 shutil.which 处理。
ODA_CANDIDATE_PATHS = [
    # 项目自带（不在 $PATH 中）
    Path(__file__).resolve().parents[2] / "tools" / "oda" / "ODAFileConverter.AppImage",
    Path(__file__).resolve().parents[2] / "tools" / "oda" / "ODAFileConverter",
    # 常见系统安装位置（/opt 一般不在 $PATH 中）
    Path("/opt/ODAFileConverter/ODAFileConverter"),
    Path("/opt/oda/ODAFileConverter"),
]


@dataclass
class EnvironmentStatus:
    """环境检查结果。"""
    oda_executable: Optional[Path]
    oda_found: bool
    ezdxf_available: bool
    messages: list[str]

    @property
    def ok(self) -> bool:
        """转换链路是否可用：只需要 ODA。ezdxf 仅解析阶段需要。"""
        return self.oda_found


def _find_oda_executable() -> tuple[Optional[Path], list[str]]:
    """在候选路径与 $PATH 中查找 ODA File Converter。

    查找顺序（优先级从高到低）：
    1. $ODA_HOME 环境变量（如 /opt/oda/ODAFileConverter.AppImage）
    2. 项目自带 tools/oda/（限于 editable 安装或从源码运行）
    3. 系统常见路径（/opt/ODAFileConverter, /opt/oda）
    4. $PATH 中的 ODAFileConverter
    """
    messages: list[str] = []

    # $ODA_HOME 环境变量 — 最高优先级，适用于 Docker 部署和自定义安装
    oda_home = os.environ.get("ODA_HOME")
    if oda_home:
        for name in ("ODAFileConverter.AppImage", "ODAFileConverter"):
            candidate = Path(oda_home) / name
            if candidate.is_file():
                messages.append(f"命中 $ODA_HOME: {candidate}")
                return candidate, messages
        messages.append(f"$ODA_HOME={oda_home} 已设置但未找到可执行文件，已跳过。")

    # 静态候选路径（项目自带 + 常见系统位置）
    for candidate in ODA_CANDIDATE_PATHS:
        if candidate.is_file():
            messages.append(f"命中候选路径: {candidate}")
            return candidate, messages

    # $PATH
    on_path = shutil.which("ODAFileConverter")
    if on_path:
        messages.append(f"命中 $PATH: {on_path}")
        return Path(on_path), messages

    messages.append("未在任何候选路径或 $PATH 中找到 ODA File Converter。")
    return None, messages


def _check_ezdxf() -> tuple[bool, list[str]]:
    """检查 ezdxf 是否可导入（仅 DXF 解析阶段需要，转换阶段不需要）。"""
    messages: list[str] = []
    try:
        import ezdxf  # noqa: F401
        messages.append(f"ezdxf 可用: {ezdxf.__version__}（解析阶段需要）")
        return True, messages
    except ImportError as e:
        messages.append(f"ezdxf 不可用: {e}（不影响转换，DXF 解析阶段才需要）")
        return False, messages


def check_environment() -> EnvironmentStatus:
    """检查转换链路所需环境，返回结构化状态。"""
    oda_path, oda_msgs = _find_oda_executable()
    ezdxf_ok, ezdxf_msgs = _check_ezdxf()

    return EnvironmentStatus(
        oda_executable=oda_path,
        oda_found=oda_path is not None,
        ezdxf_available=ezdxf_ok,
        messages=oda_msgs + ezdxf_msgs,
    )


def print_environment_report() -> EnvironmentStatus:
    """检查环境并打印报告，方便命令行自检。"""
    status = check_environment()
    print("=== DXF→DWG 环境检查 ===")
    for m in status.messages:
        print(f"  - {m}")
    print(f"  ODA:     {'OK' if status.oda_found else '缺失'} (转换必需)")
    print(f"  ezdxf:   {'OK' if status.ezdxf_available else '缺失'} (仅解析需要)")
    print(f"  总体:    {'OK' if status.ok else '不完整'}")
    return status


if __name__ == "__main__":
    print_environment_report()
