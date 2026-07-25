from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "samples/box_pairs/BOX_拆板前_dxf"
REFERENCES = ROOT / "samples/box_pairs/BOX_拆板后_dxf"
DEV_DATA_ROOT = Path(os.environ.get("DXF_TEST_DEVDATA_ROOT", r"D:\DevData"))
PROJECT_1_INPUTS = DEV_DATA_ROOT / "项目1_BOX_dxf"
PROJECT_2_INPUTS = DEV_DATA_ROOT / "项目2_BOX_dxf"
