"""Configuration loading for SunFire.

Loads YAML config with sensible defaults for Chinese steel fabrication.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import ColumnMapping


@dataclass
class SunFireConfig:
    """Global configuration for all SunFire processing functions."""

    # Column name mapping for the 12 standard keywords
    column_mapping: ColumnMapping = field(default_factory=ColumnMapping)

    # Keywords for detecting attachments in part type column
    attachment_keywords: list[str] = field(
        default_factory=lambda: ["连接板", "附件", "散件"]
    )

    # Keyword for detecting main material in part type column
    main_material_keyword: str = "主"

    # Profile type detection strings
    profile_patterns: dict[str, list[str]] = field(
        default_factory=lambda: {
            "bh": ["BH", "bh", "HA", "ha"],
            "bt": ["BT", "bt"],
            "plate": ["PL", "pl", "-"],
        }
    )

    # TXT import settings
    txt_encoding: str = "gbk"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SunFireConfig":
        """Load configuration from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = cls()

        if "column_mapping" in data:
            cm = data["column_mapping"]
            config.column_mapping = ColumnMapping(
                drawing_no=cm.get("drawing_no", "图号"),
                component_no=cm.get("component_no", "构件号"),
                component_qty=cm.get("component_qty", "构件数量"),
                part_no=cm.get("part_no", "零件号"),
                spec=cm.get("spec", "规格"),
                width=cm.get("width", "宽度"),
                length=cm.get("length", "长度"),
                material=cm.get("material", "材质"),
                total_parts=cm.get("total_parts", "零件总数"),
                total_weight=cm.get("total_weight", "总重"),
                part_type=cm.get("part_type", "零件类型"),
                manufacturer=cm.get("manufacturer", "制作单位"),
            )

        if "attachment_keywords" in data:
            config.attachment_keywords = data["attachment_keywords"]
        if "main_material_keyword" in data:
            config.main_material_keyword = data["main_material_keyword"]
        if "profile_patterns" in data:
            config.profile_patterns = data["profile_patterns"]
        if "txt_encoding" in data:
            config.txt_encoding = data["txt_encoding"]

        return config
