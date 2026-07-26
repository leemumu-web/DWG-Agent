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


def test_existing_display_disables_per_call_xvfb(monkeypatch):
    """Worker 已提供持久 DISPLAY 时，不再为每个 ODA 调用启动临时 Xvfb。"""
    monkeypatch.setenv("DISPLAY", ":91")
    with mock.patch(
        "dwg_converter.engines.oda_converter.shutil.which",
        return_value="/usr/bin/xvfb-run",
    ):
        conv = OdaConverter(executable=Path("/fake/oda"))
    assert conv.xvfb_run is False


def test_explicit_xvfb_setting_wins_over_display(monkeypatch):
    """调用方显式要求 xvfb-run 时，DISPLAY 不覆盖显式配置。"""
    monkeypatch.setenv("DISPLAY", ":91")
    with mock.patch(
        "dwg_converter.engines.oda_converter.shutil.which",
        return_value="/usr/bin/xvfb-run",
    ):
        conv = OdaConverter(executable=Path("/fake/oda"), xvfb_run=True)
    assert conv.xvfb_run is True


# ---------------------------------------------------------------------- #
# 单文件转换
# ---------------------------------------------------------------------- #
def test_convert_file_missing_source(tmp_path: Path):
    conv = OdaConverter(executable=Path("/fake/oda"))
    with mock.patch.object(conv, "_run_once") as run_once:
        result = conv.convert_file(tmp_path / "nope.dwg", tmp_path / "out")

    assert not result.success
    assert "源文件不存在" in (result.error or "")
    run_once.assert_not_called()


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


def test_nonzero_exit_retries_three_times_with_bounded_backoff():
    """生产配置允许 ODA 非零退出后再尝试三次，并在重试间短暂退避。"""
    conv = OdaConverter(executable=Path("/fake/oda"))
    failures = [
        subprocess.CompletedProcess(args=[], returncode=7, stdout="", stderr="busy"),
        subprocess.CompletedProcess(args=[], returncode=8, stdout="", stderr="busy"),
        subprocess.CompletedProcess(args=[], returncode=9, stdout="", stderr="busy"),
        _fake_ok(),
    ]

    with (
        mock.patch.object(conv, "_run_once", side_effect=failures) as run_once,
        mock.patch("dwg_converter.engines.oda_converter.time.sleep") as sleep,
    ):
        result = conv._run_with_retries(["oda"], timeout=10, retries=3)

    assert result.returncode == 0
    assert run_once.call_count == 4
    assert [call.args[0] for call in sleep.call_args_list] == [0.5, 1.0, 2.0]


def test_nonzero_exit_exhaustion_reports_all_attempts(tmp_path: Path):
    src = tmp_path / "a.dwg"
    src.write_bytes(b"fake")
    conv = OdaConverter(executable=Path("/fake/oda"))
    failed = subprocess.CompletedProcess(
        args=[], returncode=7, stdout="", stderr="busy",
    )

    with (
        mock.patch.object(conv, "_run_once", return_value=failed) as run_once,
        mock.patch("dwg_converter.engines.oda_converter.time.sleep"),
    ):
        result = conv.convert_file(src, tmp_path / "out", retries=3)

    assert not result.success
    assert run_once.call_count == 4
    assert "已尝试 4 次" in (result.error or "")
    assert "returncode=7" in (result.error or "")


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


# ====================================================================== #
# 对抗性边界测试：覆盖文件系统错误、ODA 运行时崩溃、异常路径
# ====================================================================== #

# ---------------------------------------------------------------------- #
# 文件系统错误 —— 不能抛异常，必须返回 success=False
# ---------------------------------------------------------------------- #
def test_convert_file_mkdir_on_existing_file_returns_failure(tmp_path: Path):
    """target_dir 是已有文件（非目录）时 mkdir 抛 FileExistsError → 返回失败，不抛。"""
    src = tmp_path / "a.dwg"
    src.write_bytes(b"fake")
    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("i am a file not a directory")

    conv = OdaConverter(executable=Path("/fake/oda"))
    result = conv.convert_file(src, not_a_dir)

    assert not result.success
    assert "非目录文件占用" in (result.error or "")


def test_convert_file_copy_failure_returns_failure(tmp_path: Path):
    """复制源文件失败（如权限不足）→ 返回 success=False，不抛。"""
    src = tmp_path / "a.dwg"
    src.write_bytes(b"fake")
    out_dir = tmp_path / "out"
    conv = OdaConverter(executable=Path("/fake/oda"))

    with mock.patch("shutil.copy2", side_effect=OSError("Permission denied")):
        result = conv.convert_file(src, out_dir)

    assert not result.success
    assert "无法复制源文件" in (result.error or "")
    assert "Permission denied" in (result.error or "")


def test_convert_directory_mkdir_on_existing_file_returns_failure(tmp_path: Path):
    """目录模式下 target_dir 是文件 → 返回含失败条目的 BatchResult，不抛。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.dwg").write_bytes(b"fake")
    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("i am a file")

    conv = OdaConverter(executable=Path("/fake/oda"))
    batch = conv.convert_directory(src_dir, not_a_dir)

    assert isinstance(batch, BatchResult)
    assert batch.total == 1
    assert batch.failed == 1
    assert "非目录文件占用" in (batch.results[0].error or "")


# ---------------------------------------------------------------------- #
# ODA 运行时崩溃（二进制被删/损坏）—— 必须返回失败，不抛
# ---------------------------------------------------------------------- #
def test_convert_file_oda_execution_error_returns_failure(tmp_path: Path):
    """ODA 执行时二进制不存在（FileNotFoundError）→ 返回 success=False，不抛。"""
    src = tmp_path / "a.dwg"
    src.write_bytes(b"fake")
    out_dir = tmp_path / "out"
    conv = OdaConverter(executable=Path("/fake/oda"))

    with mock.patch.object(conv, "_run_once", side_effect=FileNotFoundError("No such file")):
        result = conv.convert_file(src, out_dir)

    assert not result.success
    assert "ODA 执行失败" in (result.error or "")


def test_convert_directory_oda_execution_error_returns_failure(tmp_path: Path):
    """目录模式下 ODA 执行崩溃 → BatchResult 中所有文件标记失败，不抛。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.dwg").write_bytes(b"x")
    (src_dir / "b.dwg").write_bytes(b"x")
    out_dir = tmp_path / "out"

    conv = OdaConverter(executable=Path("/fake/oda"))
    with mock.patch.object(conv, "_run_once", side_effect=PermissionError("Access denied")):
        batch = conv.convert_directory(src_dir, out_dir)

    assert batch.total == 2
    assert batch.failed == 2
    assert all("ODA 执行失败" in (r.error or "") for r in batch.results)


# ---------------------------------------------------------------------- #
# _collect_result 边界：returncode=0 但产物未生成（静默失败）
# ---------------------------------------------------------------------- #
def test_collect_result_silent_failure_no_target_no_err(tmp_path: Path):
    """ODA returncode=0 但既无 .dxf 也无 .dxf.err —— _collect_result 判失败。"""
    src = tmp_path / "test.dwg"
    target_dir = tmp_path / "out"
    target_dir.mkdir()

    result = OdaConverter._collect_result(
        source=src, target_dir=target_dir,
        returncode=0, duration=1.0,
    )

    assert not result.success
    assert "静默失败" in (result.error or "")
    assert result.returncode == 0


def test_collect_result_returncode_nonzero_no_err_file(tmp_path: Path):
    """ODA returncode=3 且无 .err 文件 → 判失败，错误信息包含退出码。"""
    src = tmp_path / "test.dwg"
    target_dir = tmp_path / "out"
    target_dir.mkdir()

    result = OdaConverter._collect_result(
        source=src, target_dir=target_dir,
        returncode=3, duration=1.0,
    )

    assert not result.success
    assert "returncode=3" in (result.error or "")


# ---------------------------------------------------------------------- #
# Unicode 文件名 / 特殊字符
# ---------------------------------------------------------------------- #
def test_convert_file_unicode_filename(tmp_path: Path):
    """包含中文/特殊字符的文件名应正常转换。"""
    src = tmp_path / "图纸-测试_01.dwg"
    src.write_bytes(b"fake dwg")
    out_dir = tmp_path / "out"
    conv = OdaConverter(executable=Path("/fake/oda"))

    def make_target(cmd, timeout):
        target_dir = Path(cmd[2])
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(cmd[-1]).stem
        (target_dir / f"{stem}.dxf").write_text("dxf")
        return _fake_ok()

    with mock.patch.object(conv, "_run_once", side_effect=make_target):
        result = conv.convert_file(src, out_dir)

    assert result.success
    assert result.target.name == "图纸-测试_01.dxf"


def test_convert_file_at_sign_filename(tmp_path: Path):
    """包含 @ 的文件名（CAD 常用命名）应正常转换。"""
    src = tmp_path / "BYSJ-001@B7-B1-A1-GGZ-1.dwg"
    src.write_bytes(b"fake")
    out_dir = tmp_path / "out"
    conv = OdaConverter(executable=Path("/fake/oda"))

    def make_target(cmd, timeout):
        target_dir = Path(cmd[2])
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(cmd[-1]).stem
        (target_dir / f"{stem}.dxf").write_text("dxf")
        return _fake_ok()

    with mock.patch.object(conv, "_run_once", side_effect=make_target):
        result = conv.convert_file(src, out_dir)

    assert result.success
    assert "@" in result.target.name
    assert result.target.suffix == ".dxf"


# ---------------------------------------------------------------------- #
# 空目录 / 零匹配
# ---------------------------------------------------------------------- #
def test_convert_empty_directory(tmp_path: Path):
    """空源目录返回空 BatchResult（total=0）。"""
    src_dir = tmp_path / "empty_src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"

    conv = OdaConverter(executable=Path("/fake/oda"))
    batch = conv.convert_directory(src_dir, out_dir)

    assert isinstance(batch, BatchResult)
    assert batch.total == 0
    assert batch.all_success is False  # 空不算成功


def test_convert_directory_wrong_extension_filter(tmp_path: Path):
    """文件扩展名不匹配 filter → 返回空 BatchResult。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.txt").write_bytes(b"not dwg")
    (src_dir / "b.bin").write_bytes(b"not dwg")
    out_dir = tmp_path / "out"

    conv = OdaConverter(executable=Path("/fake/oda"))
    batch = conv.convert_directory(src_dir, out_dir, file_filter="*.dwg")

    assert batch.total == 0


# ---------------------------------------------------------------------- #
# 错误码映射 (_failure_code)
# ---------------------------------------------------------------------- #
def test_failure_code_timeout():
    """超时错误应映射为 DXF_TIMEOUT。"""
    from dwg_converter.framework import _failure_code, ERROR_CODES
    r = ConvertResult(Path("/a.dwg"), Path("/a.dxf"), False, error="ODA 超时（120s）")
    assert _failure_code(r) == ERROR_CODES["DXF_TIMEOUT"]


def test_failure_code_source_missing():
    """源文件缺失应映射为 DXF_SOURCE_MISSING。"""
    from dwg_converter.framework import _failure_code, ERROR_CODES
    r = ConvertResult(Path("/a.dwg"), Path("/a.dxf"), False, error="源文件不存在")
    assert _failure_code(r) == ERROR_CODES["DXF_SOURCE_MISSING"]


def test_failure_code_generic():
    """其他错误应映射为 DXF_CONVERSION_FAILED。"""
    from dwg_converter.framework import _failure_code, ERROR_CODES
    r = ConvertResult(Path("/a.dwg"), Path("/a.dxf"), False, error="未知错误")
    assert _failure_code(r) == ERROR_CODES["DXF_CONVERSION_FAILED"]


# ---------------------------------------------------------------------- #
# health_check 返回结构
# ---------------------------------------------------------------------- #
def test_health_check_returns_health_status():
    """health_check() 返回 HealthStatus 且不抛异常。"""
    from dwg_converter.framework import health_check, HealthStatus
    status = health_check()
    assert isinstance(status, HealthStatus)
    assert isinstance(status.healthy, bool)
    assert isinstance(status.to_dict(), dict)
