"""转换逻辑测试（不真正调用 ODA，只校验命令拼装、结果处理、服务层契约）。

需要 ODA 才能跑通端到端，这里用 mock 验证框架自身正确性。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from dwg_converter.engines.oda_converter import (
    OdaConverter,
    ConvertResult,
    BatchResult,
)
from dwg_converter.service import convert, convert_file, get_converter, reset_converter


def _fake_ok():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")


# ---------------------------------------------------------------------- #
# CLI 命令拼装
# ---------------------------------------------------------------------- #
def test_build_cmd_order():
    conv = OdaConverter(executable=Path("/fake/oda"))
    cmd = conv._build_cmd(
        Path("/src"), Path("/dst"),
        version="ACAD2018",
        recursive=False, audit=True, file_filter="a.dwg",
    )
    # 顺序：exe src dst version type(DXF) recursive audit filter
    assert cmd[0] == "/fake/oda"
    assert cmd[1] == "/src"
    assert cmd[2] == "/dst"
    assert cmd[3] == "ACAD2018"
    assert cmd[4] == "DXF"  # 输出类型固定
    assert cmd[5] == "0"  # recursive
    assert cmd[6] == "1"  # audit
    assert cmd[7] == "a.dwg"


# ---------------------------------------------------------------------- #
# 单文件转换
# ---------------------------------------------------------------------- #
def test_convert_file_missing_source(tmp_path: Path):
    conv = OdaConverter(executable=Path("/fake/oda"))
    result = conv.convert_file(tmp_path / "nope.dwg", tmp_path / "out")
    assert not result.success
    assert "源文件不存在" in (result.error or "")


def test_convert_file_success(tmp_path: Path):
    """模拟 ODA 成功：源文件存在、subprocess 返回 0、目标文件被创建。"""
    src = tmp_path / "a.dwg"
    src.write_bytes(b"fake dwg")
    out_dir = tmp_path / "out"
    conv = OdaConverter(executable=Path("/fake/oda"))

    def make_target(cmd, timeout):
        # cmd: [exe, src_dir, dst_dir, version, type, rec, audit, filter]
        target_dir = Path(cmd[2])
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(cmd[-1]).stem
        (target_dir / f"{stem}.dxf").write_text("dxf")
        return _fake_ok()

    with mock.patch.object(conv, "_run_once", side_effect=make_target):
        result = conv.convert_file(src, out_dir)

    assert result.success
    assert result.target.suffix == ".dxf"
    assert result.returncode == 0


def test_convert_file_err_byproduct_detected(tmp_path: Path):
    """ODA 失败时 returncode 仍是 0，但生成 <name>.dxf.err —— 应被判失败并清理。"""
    src = tmp_path / "a.dwg"
    src.write_bytes(b"fake")
    out_dir = tmp_path / "out"
    conv = OdaConverter(executable=Path("/fake/oda"))

    def make_err(cmd, timeout):
        target_dir = Path(cmd[2])
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "a.dxf.err").write_text("OdError: Unexpected end of file")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with mock.patch.object(conv, "_run_once", side_effect=make_err):
        result = conv.convert_file(src, out_dir)

    assert not result.success
    assert result.error and "Unexpected end of file" in result.error
    # .err 副产物应被清理
    assert not (out_dir / "a.dxf.err").exists()


def test_convert_file_timeout_returns_failure(tmp_path: Path):
    """超时视为转换失败（返回结果），不抛异常。"""
    src = tmp_path / "a.dwg"
    src.write_bytes(b"fake")
    out_dir = tmp_path / "out"
    conv = OdaConverter(executable=Path("/fake/oda"), default_timeout=1, default_retries=0)

    def boom(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    with mock.patch.object(conv, "_run_once", side_effect=boom):
        result = conv.convert_file(src, out_dir)

    assert not result.success
    assert result.returncode == 124
    assert result.error and "超时" in result.error
    assert "1s" in result.error  # timeout 值进 error_hint


# ---------------------------------------------------------------------- #
# 目录批量转换
# ---------------------------------------------------------------------- #
def test_convert_directory_collects_from_target(tmp_path: Path):
    """目录模式：转换后按目标目录实际产物构造结果，而非假设全部成功。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "ok.dwg").write_bytes(b"x")
    (src_dir / "bad.dwg").write_bytes(b"x")
    out_dir = tmp_path / "out"

    conv = OdaConverter(executable=Path("/fake/oda"))

    def make_partial(cmd, timeout):
        target_dir = Path(cmd[2])
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "ok.dxf").write_text("dxf")          # 成功
        (target_dir / "bad.dxf.err").write_text("boom")    # 失败：只有 .err
        return _fake_ok()

    with mock.patch.object(conv, "_run_once", side_effect=make_partial):
        batch = conv.convert_directory(src_dir, out_dir)

    assert isinstance(batch, BatchResult)
    assert batch.total == 2
    assert batch.ok == 1
    assert batch.failed == 1
    assert batch.all_success is False
    by_name = {r.source.stem: r for r in batch.results}
    assert by_name["ok"].success
    assert not by_name["bad"].success
    assert "boom" in by_name["bad"].error
    # .err 应被清理
    assert not (out_dir / "bad.dxf.err").exists()


# ---------------------------------------------------------------------- #
# 服务层：统一入口分发
# ---------------------------------------------------------------------- #
def test_convert_dispatches_file_vs_dir(tmp_path: Path):
    """统一入口 convert() 按源类型分派，返回对应类型。"""
    conv = OdaConverter(executable=Path("/fake/oda"))

    def fake_run(cmd, timeout):
        target_dir = Path(cmd[2])
        target_dir.mkdir(parents=True, exist_ok=True)
        source_dir = Path(cmd[1])
        for src in source_dir.glob("*.dwg"):
            (target_dir / f"{src.stem}.dxf").write_text("dxf")
        return _fake_ok()

    with mock.patch.object(conv, "_run_once", side_effect=fake_run):
        # 单文件
        sf = tmp_path / "s.dwg"
        sf.write_bytes(b"")
        single = convert(sf, tmp_path / "o1", converter=conv)
        assert isinstance(single, ConvertResult)
        assert single.success

        # 目录
        d = tmp_path / "src"
        d.mkdir()
        (d / "s.dwg").write_bytes(b"")
        batch = convert(d, tmp_path / "o2", converter=conv)
        assert isinstance(batch, BatchResult)
        assert batch.ok == 1


def test_service_convert_file_missing_source_returns_result(tmp_path: Path):
    """服务层缺源文件返回 success=False 结果，不抛。"""
    conv = OdaConverter(executable=Path("/fake/oda"))
    result = convert_file(
        tmp_path / "nope.dwg", tmp_path / "out", converter=conv,
    )
    assert isinstance(result, ConvertResult)
    assert not result.success
    assert "源文件不存在" in (result.error or "")


def test_convert_nonexistent_path_returns_failure_not_raises(tmp_path: Path):
    """统一入口对不存在的路径按单文件处理 → 返回 success=False，不抛。"""
    conv = OdaConverter(executable=Path("/fake/oda"))
    result = convert(tmp_path / "nope.dwg", tmp_path / "out", converter=conv)
    assert isinstance(result, ConvertResult)
    assert not result.success
    assert "源文件不存在" in (result.error or "")


def test_convert_directory_missing_returns_failure_not_raises(tmp_path: Path):
    """源目录不存在 → 返回含失败条目的 BatchResult，不抛。"""
    conv = OdaConverter(executable=Path("/fake/oda"))
    batch = conv.convert_directory(tmp_path / "nope_dir", tmp_path / "out")
    assert isinstance(batch, BatchResult)
    assert batch.total == 1
    assert batch.failed == 1
    assert "源目录不存在" in (batch.results[0].error or "")


# ---------------------------------------------------------------------- #
# 结果序列化
# ---------------------------------------------------------------------- #
def test_convert_result_to_dict_serializable(tmp_path: Path):
    """to_dict() 全是 JSON 原语，不含 Path/returncode/stdout/stderr。"""
    r = ConvertResult(
        source=tmp_path / "a.dwg",
        target=tmp_path / "a.dxf",
        success=False,
        returncode=0,
        duration=1.234,
        error="boom",
    )
    d = r.to_dict()
    assert set(d.keys()) == {"source", "target", "success", "duration", "error"}
    assert d["source"] == str(tmp_path / "a.dwg")
    assert d["success"] is False
    assert d["duration"] == 1.234
    assert d["error"] == "boom"
    # JSON 可序列化
    json.dumps(d)
    assert "returncode" not in d
    assert "stdout" not in d
    assert "stderr" not in d


def test_batch_result_to_dict():
    r1 = ConvertResult(Path("/a.dwg"), Path("/a.dxf"), True, duration=0.5)
    r2 = ConvertResult(Path("/b.dwg"), Path("/b.dxf"), False, error="x")
    batch = BatchResult([r1, r2])
    d = batch.to_dict()
    assert d["total"] == 2
    assert d["ok"] == 1
    assert d["failed"] == 1
    assert d["all_success"] is False
    assert len(d["results"]) == 2
    assert d["results"][0]["success"] is True
    assert d["results"][1]["error"] == "x"
    json.dumps(d)  # 可序列化


# ---------------------------------------------------------------------- #
# 单例
# ---------------------------------------------------------------------- #
def test_get_converter_singleton(tmp_path: Path):
    """get_converter() 两次返回同一实例。"""
    reset_converter()
    fake = OdaConverter(executable=Path("/fake/oda"))

    with mock.patch("dwg_converter.service.OdaConverter", return_value=fake) as mock_ctor:
        a = get_converter()
        b = get_converter()
        assert a is b is fake
        assert mock_ctor.call_count == 1  # 只构造一次
    reset_converter()
