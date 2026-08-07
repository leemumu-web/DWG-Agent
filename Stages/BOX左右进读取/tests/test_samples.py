"""真实 BOX 样图回归测试。

用 BOX_READER_PRE_DXF_DIRECTORY 环境变量指向 BOX 拆板前 DXF 目录；
未设置时相关真实样图测试跳过。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from box_reader.analyzer import BoxAnalyzer
from box_reader.batch import BoxInputEntry, analyze_manifest
from box_reader.dxf_ezdxf import read_ezdxf

PRE_DXF_DIRECTORY = Path(
    os.environ.get(
        "BOX_READER_PRE_DXF_DIRECTORY",
        "/home/Creeken/Paper/CAD_research/complete_framework/BOX拆板前分类/BOX拆板前_BOX_dxf",
    )
)


def _sample(name: str) -> Path:
    return PRE_DXF_DIRECTORY / name


class BoxSampleTestBase(unittest.TestCase):
    maxDiff = None

    def _analyze(self, name: str):
        path = _sample(name)
        if not path.is_file():
            self.skipTest(f"sample missing: {name}")
        analyzer = BoxAnalyzer()
        drawing = read_ezdxf(path)
        return analyzer.analyze(drawing)

    def assert_setbacks(self, result, expected):
        """expected: dict[role -> (left_safe, right_safe)]"""
        actual = {m.role: (m.left_safe, m.right_safe) for m in result.measurements}
        self.assertEqual(actual, expected)


class BoxStandardRegression(BoxSampleTestBase):
    """标准齐平 BOX：四块板左右进均为 0。"""

    def test_2b1_cb_56_flat(self):
        result = self._analyze("BYSJ@零件图@2b1-cb-56_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"翼": (0, 0), "腹": (0, 0)},
        )

    def test_w4e_cb_8_flat(self):
        result = self._analyze("BYSJ@零件图@w4e-cb-8_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"翼": (0, 0), "腹": (0, 0)},
        )

    def test_5t1_cb_52_flat(self):
        result = self._analyze("BYSJ@板零件图@5t1-cb-52_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"翼": (0, 0), "腹": (0, 0)},
        )


class BoxSetbackRegression(BoxSampleTestBase):
    """非零左右进 BOX：上翼/下翼端部短于主视图。"""

    def test_2b1_cb_86_upper_left(self):
        result = self._analyze("BYSJ@零件图@2b1-cb-86_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"上翼": (166, 0), "下翼": (0, 0), "上腹": (477, 0), "下腹": (332, 0)},
        )

    def test_2t1_cb_95_upper_right(self):
        result = self._analyze("BYSJ@零件图@2t1-cb-95_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"上翼": (0, 474), "下翼": (0, 0), "上腹": (0, 0), "下腹": (0, 831)},
        )

    def test_2b1_cb_92_tapered_lower_left(self):
        """变截面/斜切箱梁：下翼左端短于上翼（尺寸标注自证 925mm），腹板左进 23。"""
        result = self._analyze("BYSJ@零件图@2b1-cb-92_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"上翼": (0, 0), "下翼": (901, 0), "腹": (23, 0)},
        )

    def test_h9_cb_72_arc_flange(self):
        """上翼左端圆弧倒角：边界不含倒角圆弧尖端（左右进=505）。"""
        result = self._analyze("BYSJ@零件图@h-9-cb-72_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"上翼": (505, 505), "下翼": (0, 0), "腹": (0, 0)},
        )

    def test_h9_cb_73(self):
        """右端腹板尖突出：上翼右进 493、下翼右进 243。"""
        result = self._analyze("BYSJ@零件图@h-9-cb-73_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"上翼": (0, 493), "下翼": (0, 243), "上腹": (0, 953), "下腹": (0, 0)},
        )

    def test_h4_cb_37_large_coords(self):
        """h-4 大坐标多视图块：上翼左进 416、下翼右进 201、腹板右进 7。"""
        result = self._analyze("BYSJ@零件图@h-4-cb-37_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assert_setbacks(
            result,
            {"上翼": (416, 0), "下翼": (0, 201), "腹": (0, 7)},
        )


class BoxMultiViewRegression(BoxSampleTestBase):
    """一块多视图的 BOX 图（h-4 系列）。"""

    def test_h4_cb_37(self):
        result = self._analyze("BYSJ@零件图@h-4-cb-37_拆板前.dxf")
        self.assertEqual(result.status, "OK")
        self.assertEqual(len(result.measurements), 3)


class BoxFullCorpus(unittest.TestCase):
    """全部 BOX 样图必须 OK 且板件齐全。"""

    def test_all_samples_ok(self):
        if not PRE_DXF_DIRECTORY.is_dir():
            self.skipTest("BOX corpus directory not mounted")
        files = sorted(PRE_DXF_DIRECTORY.glob("*.dxf"))
        self.assertGreater(len(files), 0)
        entries = [
            BoxInputEntry(path=path, file_name=path.name) for path in files
        ]
        outcome = analyze_manifest(entries, on_progress=None)
        self.assertEqual(outcome.processed_count, len(files))
        self.assertEqual(outcome.failure_count, 0)
        self.assertEqual(outcome.ok_count, len(files))
        self.assertGreaterEqual(outcome.measurement_count, 2 * len(files))
        self.assertLessEqual(outcome.measurement_count, 4 * len(files))
        roles = {m.role for item in outcome.items for m in item.measurements}
        self.assertTrue(
            roles <= {"上翼", "下翼", "翼", "腹", "上腹", "下腹", "腹板"},
            f"未预期的板件角色: {roles - {'上翼', '下翼', '翼', '腹', '上腹', '下腹', '腹板'}}",
        )


if __name__ == "__main__":
    unittest.main()
