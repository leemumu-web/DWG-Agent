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


if __name__ == "__main__":
    unittest.main()
