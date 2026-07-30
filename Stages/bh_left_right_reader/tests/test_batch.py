from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from zipfile import ZipFile

from bh_reader.model import DrawingData, DrawingResult, PlateMeasurement
from bh_reader.simple_xlsx import write_results_xlsx


class FakeAnalyzer:
    def analyze(self, drawing: DrawingData) -> DrawingResult:
        return DrawingResult(
            file_name=drawing.path.name,
            part_number=drawing.path.stem,
            specification="BH500*200*10*16",
            status="OK",
            confidence=0.95,
            measurements=[
                PlateMeasurement("腹", 10.2, 20.7, 10, 20, 0.95, "synthetic")
            ],
            warnings=[],
            diagnostics={"front_view": {"id": "FRONT"}},
        )


def test_analyze_manifest_reports_each_file_and_isolates_single_file_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    assert importlib.util.find_spec("bh_reader.batch") is not None
    batch = importlib.import_module("bh_reader.batch")
    good = tmp_path / "1.dxf"
    bad = tmp_path / "2.dxf"
    good.touch()
    bad.touch()

    def read_drawing(path: Path, _backend: str) -> DrawingData:
        if path == bad:
            raise ValueError("broken dxf")
        return DrawingData(path, [], [], "test")

    monkeypatch.setattr(batch, "_read", read_drawing)
    progress = []
    outcome = batch.analyze_manifest(
        (
            batch.BhInputEntry(good, "BH-good.dxf"),
            batch.BhInputEntry(bad, "BH-bad.dxf"),
        ),
        backend="ascii",
        on_progress=progress.append,
        analyzer=FakeAnalyzer(),
    )

    assert [item.file_name for item in outcome.items] == [
        "BH-good.dxf",
        "BH-bad.dxf",
    ]
    assert [item.status for item in outcome.items] == ["OK", "ERROR_UNHANDLED"]
    assert outcome.processed_count == 2
    assert outcome.ok_count == 1
    assert outcome.failure_count == 1
    assert [
        (item.processed, item.total, item.file_name, item.status)
        for item in progress
    ] == [
        (1, 2, "BH-good.dxf", "OK"),
        (2, 2, "BH-bad.dxf", "ERROR_UNHANDLED"),
    ]
    assert not hasattr(outcome.items[0], "drawing")
    assert not hasattr(outcome.items[0], "diagnostics")


def test_results_xlsx_consumes_result_and_diagnostic_iterables_once(
    tmp_path: Path,
) -> None:
    consumed = {"results": 0, "diagnostics": 0}

    def result_rows():
        consumed["results"] += 1
        if consumed["results"] > 1:
            raise AssertionError("result rows consumed more than once")
        yield ["BH-1.dxf", "P1腹", "BH500*200*10*16", 10, 20]

    def diagnostic_rows():
        consumed["diagnostics"] += 1
        if consumed["diagnostics"] > 1:
            raise AssertionError("diagnostic rows consumed more than once")
        yield ["BH-1.dxf", "P1", "OK"]

    output = tmp_path / "reader.xlsx"
    write_results_xlsx(output, result_rows(), diagnostic_rows())

    assert consumed == {"results": 1, "diagnostics": 1}
    with ZipFile(output) as archive:
        assert b"BH-1.dxf" in archive.read("xl/worksheets/sheet1.xml")
        assert b"BH-1.dxf" in archive.read("xl/worksheets/sheet2.xml")


def test_analyze_manifest_calls_consumer_before_compacting_each_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch = importlib.import_module("bh_reader.batch")
    source = tmp_path / "source.dxf"
    source.touch()
    drawing = DrawingData(source, [], [], "test")
    monkeypatch.setattr(batch, "_read", lambda _path, _backend: drawing)
    consumed = []

    def consume(entry, actual_drawing, result) -> None:
        assert entry.file_name == "logical-name.dxf"
        assert actual_drawing is drawing
        result.warnings.append("consumer warning")
        consumed.append(result)

    outcome = batch.analyze_manifest(
        (batch.BhInputEntry(source, "logical-name.dxf"),),
        backend="ascii",
        on_progress=lambda _progress: None,
        on_analyzed=consume,
        analyzer=FakeAnalyzer(),
    )

    assert len(consumed) == 1
    assert outcome.items[0].warnings == ("consumer warning",)


def test_analyze_manifest_keeps_5000_results_compact_and_emits_delivery_rows(
    monkeypatch,
) -> None:
    batch = importlib.import_module("bh_reader.batch")
    monkeypatch.setattr(
        batch,
        "_read",
        lambda path, _backend: DrawingData(path, [], [], "test"),
    )
    entries = (
        batch.BhInputEntry(Path(f"/virtual/{index}.dxf"), f"BH-{index}.dxf")
        for index in range(5000)
    )
    progress = []

    outcome = batch.analyze_manifest(
        entries,
        backend="ascii",
        on_progress=progress.append,
        analyzer=FakeAnalyzer(),
    )

    assert outcome.processed_count == 5000
    assert outcome.measurement_count == 5000
    assert len(progress) == 5000
    result_rows = list(outcome.iter_result_rows())
    diagnostic_rows = list(outcome.iter_diagnostic_rows())
    assert len(result_rows) == len(diagnostic_rows) == 5000
    assert result_rows[0][:9] == [
        "BH-0.dxf",
        "0腹",
        "BH500*200*10*16",
        10,
        20,
        10.2,
        20.7,
        "OK",
        0.95,
    ]
    assert diagnostic_rows[0][:3] == ["BH-0.dxf", "0", "OK"]
    assert not hasattr(outcome.items[0], "diagnostics")
