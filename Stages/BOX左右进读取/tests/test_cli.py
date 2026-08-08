"""CLI 入口测试。"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from box_reader.cli import _collect_files

PRE = Path(
    os.environ.get(
        "BOX_READER_PRE_DXF_DIRECTORY",
        "/home/Creeken/Paper/CAD_research/complete_framework/BOX拆板前分类/BOX拆板前_BOX_dxf",
    )
)


class CollectFilesTest(unittest.TestCase):
    def test_directory_scan(self):
        if not PRE.is_dir():
            self.skipTest("corpus not mounted")
        files = _collect_files([str(PRE)])
        self.assertGreater(len(files), 0)
        self.assertTrue(all(path.suffix.lower() == ".dxf" for path in files))

    def test_explicit_file(self):
        if not PRE.is_dir():
            self.skipTest("corpus not mounted")
        sample = next(PRE.glob("*.dxf"))
        files = _collect_files([str(sample)])
        self.assertEqual(len(files), 1)

    def test_deduplicate(self):
        if not PRE.is_dir():
            self.skipTest("corpus not mounted")
        sample = next(PRE.glob("*.dxf"))
        files = _collect_files([str(sample), str(sample)])
        self.assertEqual(len(files), 1)


class CliSmokeTest(unittest.TestCase):
    def test_cli_runs(self):
        if not PRE.is_dir():
            self.skipTest("corpus not mounted")
        from box_reader.cli import main

        sample = next(PRE.glob("*.dxf"))
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.xlsx"
            code = main([
                str(sample),
                "--output", str(out),
                "--no-visuals",
            ])
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())


class ModelspaceTextReadingTest(unittest.TestCase):
    """回归：Tekla 板零件图把 BOX 规格直接放在 modelspace（非块内），
    读取器必须把这些直接 TEXT 纳入文本集合，否则 ERROR_BOX_SPEC_NOT_FOUND。
    """

    def test_reads_modelspace_direct_text(self):
        import tempfile

        import ezdxf
        from box_reader.dxf_ezdxf import read_ezdxf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "msp-text.dxf"
            doc = ezdxf.new("R2007")
            msp = doc.modelspace()
            msp.add_text(
                "BOX400*250*25*25",
                dxfattribs={"layer": "OtherObjectType", "insert": (0, 0)},
            )
            msp.add_text(
                "a1-1fd-cb-464",
                dxfattribs={"layer": "PartMark", "insert": (0, 10)},
            )
            doc.blocks.new(name="*A1")
            doc.saveas(path)

            drawing = read_ezdxf(path)
            texts = {item.text.strip() for item in drawing.texts}
            self.assertIn("BOX400*250*25*25", texts)
            self.assertIn("a1-1fd-cb-464", texts)

    def test_spec_extracted_from_modelspace_text(self):
        import tempfile

        import ezdxf
        from box_reader.analyzer import BoxAnalyzer
        from box_reader.dxf_ezdxf import read_ezdxf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "msp-spec.dxf"
            doc = ezdxf.new("R2007")
            msp = doc.modelspace()
            msp.add_text(
                "BOX400*250*25*25",
                dxfattribs={"layer": "OtherObjectType", "insert": (0, 0)},
            )
            doc.blocks.new(name="*A1")
            doc.saveas(path)

            drawing = read_ezdxf(path)
            spec = BoxAnalyzer._extract_spec(drawing.texts)
            self.assertIsNotNone(spec)
            self.assertEqual(spec.depth, 400.0)
            self.assertEqual(spec.width, 250.0)


if __name__ == "__main__":
    unittest.main()
