from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import ezdxf

from bh_reader.cli import _expand_inputs, main


class CliIoTests(unittest.TestCase):
    @staticmethod
    def _valid_dxf(path: Path) -> None:
        document = ezdxf.new("R2000")
        document.header["$INSUNITS"] = 4
        space = document.modelspace()
        for start, end in (
            ((0, 0), (1000, 0)),
            ((0, 20), (1000, 20)),
            ((0, 0), (0, 20)),
            ((1000, 0), (1000, 20)),
            ((0, 580), (1000, 580)),
            ((0, 600), (1000, 600)),
            ((0, 580), (0, 600)),
            ((1000, 580), (1000, 600)),
            ((0, 20), (0, 580)),
            ((1000, 20), (1000, 580)),
        ):
            space.add_line(start, end, dxfattribs={"layer": "Part"})
        space.add_text(
            "BH600*250*12*20", dxfattribs={"layer": "OtherObjectType", "height": 10}
        ).set_placement((0, -100))
        space.add_text(
            "1000", dxfattribs={"layer": "OtherObjectType", "height": 10}
        ).set_placement((50, -100))
        document.saveas(path)

    def test_expand_inputs_accepts_only_dxf_and_deduplicates_resolved_paths(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            upper = root / "part2.DXF"
            lower = root / "part10.dxf"
            text = root / "notes.txt"
            upper.touch()
            lower.touch()
            text.touch()
            paths = _expand_inputs([str(root), str(upper), str(text)])
        self.assertEqual([path.name for path in paths], ["part2.DXF", "part10.dxf"])

    def test_cli_refuses_to_overwrite_an_input_dxf(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.dxf"
            self._valid_dxf(path)
            original = path.read_bytes()
            with self.assertRaises(SystemExit):
                main([str(path), "--output", str(path), "--no-visuals"])
            self.assertEqual(path.read_bytes(), original)

    def test_cli_refuses_output_collisions_and_visuals_inside_input_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            input_dir.mkdir()
            source = input_dir / "source.dxf"
            self._valid_dxf(source)
            same = root / "delivery" / "same.xlsx"
            with self.assertRaises(SystemExit):
                main([
                    str(source), "--output", str(same), "--json", str(same), "--no-visuals"
                ])
            self.assertFalse(same.exists())
            with self.assertRaises(SystemExit):
                main([
                    str(source), "--output", str(root / "delivery" / "result.xlsx"),
                    "--visual-dir", str(input_dir),
                ])

    def test_cli_keeps_all_generated_files_out_of_input_directories(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            input_dir.mkdir()
            source = input_dir / "source.dxf"
            self._valid_dxf(source)
            for output, json_path in (
                (input_dir / "result.xlsx", root / "delivery" / "result.json"),
                (root / "delivery" / "result.xlsx", input_dir / "result.json"),
            ):
                with self.assertRaises(SystemExit):
                    main([
                        str(source), "--output", str(output), "--json", str(json_path),
                        "--no-visuals",
                    ])
                self.assertFalse(output.exists())
                self.assertFalse(json_path.exists())

    def test_cli_creates_separate_nested_output_directories(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input" / "source.dxf"
            source.parent.mkdir()
            self._valid_dxf(source)
            xlsx = root / "delivery" / "tables" / "result.xlsx"
            json_path = root / "delivery" / "metadata" / "result.json"
            code = main([
                str(source), "--backend", "ascii", "--output", str(xlsx),
                "--json", str(json_path), "--no-visuals",
            ])
            self.assertEqual(code, 0)
            self.assertTrue(xlsx.is_file())
            self.assertTrue(json_path.is_file())

    def test_cli_default_output_uses_a_dedicated_subdirectory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.dxf"
            self._valid_dxf(source)
            previous = Path.cwd()
            try:
                os.chdir(root)
                code = main([str(source), "--backend", "ascii", "--no-visuals"])
            finally:
                os.chdir(previous)
            self.assertEqual(code, 0)
            self.assertTrue((root / "outputs" / "bh_left_right_results.xlsx").is_file())
            self.assertTrue((root / "outputs" / "bh_left_right_results.json").is_file())

    def test_cli_xlsx_records_the_generated_validation_image(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input" / "source.dxf"
            source.parent.mkdir()
            self._valid_dxf(source)
            output = root / "delivery" / "result.xlsx"
            visual_dir = root / "delivery" / "visuals"
            code = main([
                str(source), "--backend", "ascii", "--output", str(output),
                "--visual-dir", str(visual_dir),
            ])
            self.assertEqual(code, 0)
            image = visual_dir / "individual" / "source_左右进校验.png"
            self.assertTrue(image.is_file())
            with ZipFile(output) as archive:
                result_sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                diagnostic_sheet = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
            self.assertIn("校验图路径", result_sheet)
            self.assertIn(str(image), result_sheet)
            self.assertIn(str(image), diagnostic_sheet)

    def test_cli_returns_failure_when_requested_visualization_fails(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input" / "source.dxf"
            source.parent.mkdir()
            self._valid_dxf(source)
            output = root / "delivery" / "result.xlsx"
            with patch(
                "bh_reader.cli.render_three_step_sample",
                side_effect=RuntimeError("render failed"),
            ):
                code = main([
                    str(source), "--backend", "ascii", "--output", str(output),
                    "--visual-dir", str(root / "delivery" / "visuals"),
                ])
            self.assertEqual(code, 2)
            with ZipFile(output) as archive:
                result_sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("可视化生成失败", result_sheet)

    def test_cli_refuses_visual_directory_nested_below_an_artifact_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input" / "source.dxf"
            source.parent.mkdir()
            self._valid_dxf(source)
            json_path = root / "delivery" / "result.json"
            with self.assertRaises(SystemExit):
                main([
                    str(source), "--output", str(root / "delivery" / "result.xlsx"),
                    "--json", str(json_path),
                    "--visual-dir", str(json_path / "images"),
                ])
            self.assertFalse(json_path.exists())

    def test_cli_refuses_existing_output_hardlinked_to_an_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input" / "source.dxf"
            source.parent.mkdir()
            self._valid_dxf(source)
            output = root / "delivery" / "result.xlsx"
            output.parent.mkdir()
            output.hardlink_to(source)
            original = source.read_bytes()
            with self.assertRaises(SystemExit):
                main([str(source), "--output", str(output), "--no-visuals"])
            self.assertEqual(source.read_bytes(), original)

    def test_cli_refuses_duplicate_basenames_from_different_directories(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "input-a" / "same.dxf"
            second = root / "input-b" / "same.DXF"
            first.parent.mkdir()
            second.parent.mkdir()
            self._valid_dxf(first)
            self._valid_dxf(second)
            with self.assertRaises(SystemExit):
                main([
                    str(first), str(second),
                    "--output", str(root / "delivery" / "result.xlsx"),
                    "--no-visuals",
                ])

    def test_cli_refuses_output_hardlinked_to_configuration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input" / "source.dxf"
            source.parent.mkdir()
            self._valid_dxf(source)
            config = root / "settings.toml"
            config.write_text("[geometry]\n", encoding="utf-8")
            output = root / "delivery" / "result.xlsx"
            output.parent.mkdir()
            output.hardlink_to(config)
            original = config.read_bytes()
            with self.assertRaises(SystemExit):
                main([
                    str(source), "--config", str(config),
                    "--output", str(output), "--no-visuals",
                ])
            self.assertEqual(config.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
