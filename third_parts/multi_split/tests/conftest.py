"""Test fixtures for multi_split tests."""

import pandas as pd
import pytest


@pytest.fixture
def sample_parts_df() -> pd.DataFrame:
    """A representative steel parts list with BH, BT, PL, and named profiles."""
    return pd.DataFrame({
        "图号": ["D001", "D001", "D001", "D001", "D001", "D002", "D002"],
        "构件号": ["GJ-1", "GJ-1", "GJ-1", "GJ-1", "GJ-1", "GJ-2", "GJ-2"],
        "构件数量": [2, 2, 2, 2, 2, 1, 1],
        "零件号": ["P1", "P2", "P3", "P4", "P5", "P6", "P7"],
        "规格": ["PL10", "PL20", 6, 8, "L50*5", "PL30", "PL15"],
        "宽度": [200, 150, 284, 200, "N/A", 300, 250],
        "长度": [3000, 3000, 3000, 3000, 3000, 5000, 5000],
        "材质": ["Q235B", "Q235B", "Q235B", "Q235B", "Q235B", "Q345B", "Q345B"],
        "零件总数": [2, 4, 2, 4, 2, 2, 1],
        "总重": [94.2, 94.2, 43.0, 50.0, 15.0, 150.0, 55.0],
        "零件类型": ["主(翼缘)", "主(腹板)", "连接板", "主(连接)", "附件", "主", "主"],
        "制作单位": ["厂A", "厂A", "厂A", "厂A", "厂A", "厂B", "厂B"],
    })


@pytest.fixture
def simple_df() -> pd.DataFrame:
    """Simple DataFrame for basic tests."""
    return pd.DataFrame({
        "Name": ["Alice", "Bob", "Alice", "Charlie", "Bob"],
        "Score": [85, 92, 78, 95, 88],
        "Count": [1, 2, 3, 1, 1],
    })


@pytest.fixture
def bh_rows_df() -> pd.DataFrame:
    """DataFrame with BH-type specification strings."""
    return pd.DataFrame({
        "名称": ["H型钢1", "H型钢2", "T型钢1", "板材1", "板材2"],
        "规格": ["BH300*200*6*8", "HA250*150*5*6", "BT200*150*6*8", "PL10*2000", "-15*3000"],
        "宽度": [300, 250, 200, 10, 15],
        "数量": [1, 1, 2, 5, 3],
        "备注": ["标准", "重型", "T型", "底板", "面板"],
    })
