"""ODA File Converter 引擎：用 subprocess 调用外部 ODA CLI 完成 DWG→DXF。

不依赖 ezdxf.odafc，因为生产后端需要：超时、stderr/stdout 捕获、任务目录隔离、
批量转换、失败重试、转换前后校验。subprocess 比 ezdxf.odafc 更可控。

ODA CLI 参数顺序（不同版本可能有差异，首次部署需用 --help 或测试命令确认）：
    ODAFileConverter <source_dir> <target_dir> <version> <output_type> \
                     <recursive> <audit> <file_filter>

注意：ODA 按目录工作，不支持单文件参数 —— 源目录里所有匹配 filter 的文件都会被转换。
因此单文件转换也要放到一个隔离的临时源目录里执行。

ODA 失败时的静默行为：returncode 仍是 0，只在目标目录写 <name>.dxf.err 副产物。
本引擎会扫描目标目录，把 .err 当作失败原因采集并清理。

异常约定：
- 环境错误（找不到 ODA、缺 xvfb）→ 构造 OdaConverter() 时抛 OdaConvertError。
- 转换失败（找不到源、超时、.err 副产物）→ 返回 success=False 的结果对象，不抛。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..check_env import check_environment

logger = logging.getLogger(__name__)

# 本模块只做 DWG→DXF，输出类型固定。
_OUTPUT_TYPE = "DXF"
_OUTPUT_EXT = "dxf"
# _run_with_retries 超时时构造的合成 CompletedProcess 用的退出码（约定）。
_TIMEOUT_RETCODE = 124
_RETRY_BASE_DELAY_SECONDS = 0.5
_RETRY_MAX_DELAY_SECONDS = 2.0


def _failure_hint(returncode: int, timeout: int, attempts: int) -> Optional[str]:
    if returncode == _TIMEOUT_RETCODE:
        return f"ODA 单次超时限制为 {timeout}s，已尝试 {attempts} 次"
    if returncode != 0:
        return (
            f"ODA 已尝试 {attempts} 次，最后仍返回非零退出码"
            f"（returncode={returncode}）"
        )
    return None


class OdaConvertError(RuntimeError):
    """ODA 环境错误（找不到可执行文件 / 缺 xvfb）。转换失败不抛此异常，只返回结果。"""


@dataclass
class ConvertResult:
    """单次转换的结果。

    stdout/stderr 不暴露（只进日志）；returncode 保留供调试，但 to_dict() 不输出，
    避免泄漏 subprocess 内部细节。
    """
    source: Path
    target: Path
    success: bool
    returncode: int = -1
    duration: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """JSON 安全的纯字典（无 Path 对象、无 subprocess 内部字段）。"""
        return {
            "source": str(self.source),
            "target": str(self.target),
            "success": self.success,
            "duration": round(self.duration, 3),
            "error": self.error,
        }


@dataclass
class BatchResult:
    """批量转换的汇总结果。"""
    results: list[ConvertResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        return self.total - self.ok

    @property
    def all_success(self) -> bool:
        return self.total > 0 and self.ok == self.total

    @property
    def duration(self) -> float:
        return max((r.duration for r in self.results), default=0.0)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "ok": self.ok,
            "failed": self.failed,
            "all_success": self.all_success,
            "duration": round(self.duration, 3),
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class OdaConverter:
    """封装 ODA File Converter CLI 的调用。

    Attributes:
        executable: ODA 可执行文件路径。None 时自动从环境探测。
        default_version: 默认输出版本（如 ACAD2018）。
        default_audit: 是否在转换时做 audit 修复。
        default_timeout: 单次调用超时秒数。
        default_retries: 失败重试次数（不含首次）。
        xvfb_run: 是否用 xvfb-run 提供无头 X。None=自动探测。
    """

    executable: Optional[Path] = None
    default_version: str = "ACAD2018"
    default_audit: bool = True
    default_timeout: int = 120
    default_retries: int = 0
    xvfb_run: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.executable is None:
            status = check_environment()
            if status.oda_executable is None:
                raise OdaConvertError(
                    "未找到 ODA File Converter。请安装后放入 tools/oda/ 或加入 $PATH。\n"
                    + "\n".join(f"  - {m}" for m in status.messages)
                )
            self.executable = status.oda_executable
        self.executable = Path(self.executable)
        logger.info("ODA 可执行文件: %s", self.executable)
        self._resolve_xvfb()

    def _resolve_xvfb(self) -> None:
        if self.xvfb_run is None:
            self.xvfb_run = not bool(os.environ.get("DISPLAY"))
        if self.xvfb_run and shutil.which("xvfb-run") is None:
            raise OdaConvertError(
                "xvfb_run=True 但未找到 xvfb-run。请安装 xorg-server-xvfb，"
                "或显式传 xvfb_run=False（需自备可用 DISPLAY）。"
            )
        logger.info("xvfb-run 包裹: %s", self.xvfb_run)

    def _resolve_defaults(
        self,
        *,
        version: Optional[str],
        audit: Optional[bool],
        timeout: Optional[int],
        retries: Optional[int],
    ) -> tuple[str, bool, int, int]:
        """把可选入参解析成实际值，统一 convert_file / convert_directory 的样板。

        全部用 is None 判缺省，避免 timeout=0/version='' 等 falsy 合法值被吞。
        """
        return (
            version if version is not None else self.default_version,
            self.default_audit if audit is None else audit,
            timeout if timeout is not None else self.default_timeout,
            self.default_retries if retries is None else retries,
        )

    # ------------------------------------------------------------------ #
    # CLI 拼装 / 执行
    # ------------------------------------------------------------------ #
    def _build_cmd(
        self,
        source_dir: Path,
        target_dir: Path,
        version: str,
        recursive: bool,
        audit: bool,
        file_filter: str,
    ) -> list[str]:
        # 输出类型固定 DXF（本模块只做 DWG→DXF）。
        return [
            str(self.executable),
            str(source_dir),
            str(target_dir),
            version,
            _OUTPUT_TYPE,
            "1" if recursive else "0",
            "1" if audit else "0",
            file_filter,
        ]

    def _run_once(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        """执行一次 ODA CLI。需要时用 xvfb-run 提供无头 X。"""
        if self.xvfb_run:
            # -a 自动选空闲 display 号，避免并发冲突
            full_cmd = ["xvfb-run", "-a", "--"] + cmd
        else:
            full_cmd = cmd
        logger.debug("执行命令: %s", " ".join(full_cmd))
        # AppImage 的 extract-and-run 模式会使用 TMPDIR 下由镜像哈希决定的
        # 固定目录。并发调用若共享 TMPDIR，会在退出清理时互删文件，即使产物
        # 已生成也可能返回 127。每次调用必须有独立的可执行临时目录；生产容器
        # 把父目录放在 /app/var，避开 noexec 的 /tmp。
        runtime_parent = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="oda_appimage_",
            dir=runtime_parent,
        ) as call_tmp:
            xdg_runtime = Path(call_tmp) / "runtime"
            xdg_runtime.mkdir(mode=0o700)
            process_environment = os.environ.copy()
            process_environment["TMPDIR"] = call_tmp
            process_environment["XDG_RUNTIME_DIR"] = str(xdg_runtime)
            result = subprocess.run(
                full_cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                env=process_environment,
            )
        if result.stdout:
            logger.debug("ODA stdout: %s", result.stdout[:500])
        if result.stderr:
            logger.debug("ODA stderr: %s", result.stderr[:500])
        return result

    def _run_with_retries(
        self, cmd: list[str], timeout: int, retries: int
    ) -> subprocess.CompletedProcess:
        """带重试的执行。最后一次失败才返回（调用方按产物判定成败）。"""
        last: Optional[subprocess.CompletedProcess] = None
        for attempt in range(retries + 1):
            try:
                result = self._run_once(cmd, timeout)
            except subprocess.TimeoutExpired as e:
                # 超时视为转换失败，不抛——构造一个非零退出码的 CompletedProcess。
                last = subprocess.CompletedProcess(
                    args=cmd, returncode=_TIMEOUT_RETCODE,
                    stdout="", stderr=f"timeout after {timeout}s",
                )
                if attempt < retries:
                    delay = min(
                        _RETRY_BASE_DELAY_SECONDS * (2 ** attempt),
                        _RETRY_MAX_DELAY_SECONDS,
                    )
                    logger.warning(
                        "ODA 超时，%.1f 秒后重试 %d/%d",
                        delay, attempt + 1, retries,
                    )
                    time.sleep(delay)
                    continue
                logger.error("ODA 超时（%ss）: %s", timeout, e)
                return last

            last = result
            if result.returncode == 0:
                return result
            if attempt < retries:
                delay = min(
                    _RETRY_BASE_DELAY_SECONDS * (2 ** attempt),
                    _RETRY_MAX_DELAY_SECONDS,
                )
                logger.warning(
                    "ODA 失败 returncode=%d，%.1f 秒后重试 %d/%d",
                    result.returncode, delay, attempt + 1, retries,
                )
                time.sleep(delay)
                continue
        assert last is not None
        return last

    # ------------------------------------------------------------------ #
    # 产物采集：转换后扫描目标目录，把实际产物映射回结果
    # ------------------------------------------------------------------ #
    @staticmethod
    def _collect_result(
        source: Path,
        target_dir: Path,
        returncode: int,
        duration: float,
        error_hint: Optional[str] = None,
    ) -> ConvertResult:
        """扫描目标目录，对单个源文件构造结果。

        ODA 成功时写 <stem>.dxf；失败时 returncode 仍是 0 但写 <stem>.dxf.err。
        两者都按实际产物是否存在来判断成功，并清理 .err。

        error_hint: 调用方已知的失败原因（如超时），优先于通用兜底文案。
        returncode==124 约定为 _run_with_retries 的超时码。
        """
        target = target_dir / f"{source.stem}.{_OUTPUT_EXT}"
        err_file = target_dir / f"{source.stem}.{_OUTPUT_EXT}.err"

        err_msg = None
        if err_file.is_file():
            err_msg = err_file.read_text(errors="replace").strip() or "ODA 写出 .err 副产物但内容为空"
            err_file.unlink(missing_ok=True)

        success = returncode == 0 and target.is_file() and not err_msg
        if err_msg:
            error = err_msg
        elif not success:
            if returncode == _TIMEOUT_RETCODE:
                error = error_hint or f"ODA 超时（returncode={returncode}）"
            elif returncode != 0:
                error = error_hint or f"ODA 返回非零退出码（returncode={returncode}）"
            else:
                error = error_hint or "ODA 退出码为 0 但目标文件未生成（静默失败）"
        else:
            error = None

        return ConvertResult(
            source=source,
            target=target,
            success=success,
            returncode=returncode,
            duration=duration,
            error=error,
        )

    # ------------------------------------------------------------------ #
    # 单文件转换（隔离临时源目录）
    # ------------------------------------------------------------------ #
    def convert_file(
        self,
        source: Path,
        target_dir: Path,
        version: Optional[str] = None,
        audit: Optional[bool] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> ConvertResult:
        """转换单个 DWG 文件。

        ODA 按目录工作，因此把单文件复制进隔离临时源目录后转换，
        避免误转同目录其他文件。转换失败返回 success=False，不抛异常。
        """
        source = Path(source).resolve()
        target_dir = Path(target_dir).resolve()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            return ConvertResult(
                source, target_dir / f"{source.stem}.{_OUTPUT_EXT}",
                False, error=f"输出路径被非目录文件占用: {target_dir}",
            )

        if not source.is_file():
            return ConvertResult(
                source, target_dir / f"{source.stem}.{_OUTPUT_EXT}",
                False, error="源文件不存在",
            )

        version, audit, timeout, retries = self._resolve_defaults(
            version=version, audit=audit, timeout=timeout, retries=retries,
        )

        # 隔离临时源目录：复制单文件进去
        with tempfile.TemporaryDirectory(prefix="oda_src_") as tmp_src:
            tmp_src_path = Path(tmp_src)
            try:
                shutil.copy2(source, tmp_src_path / source.name)
            except OSError as e:
                return ConvertResult(
                    source, target_dir / f"{source.stem}.{_OUTPUT_EXT}",
                    False, error=f"无法复制源文件: {e}",
                )

            cmd = self._build_cmd(
                tmp_src_path, target_dir, version,
                recursive=False, audit=audit,
                file_filter=source.name,
            )

            start = time.monotonic()
            try:
                result = self._run_with_retries(cmd, timeout, retries)
            except (FileNotFoundError, PermissionError, OSError) as e:
                return ConvertResult(
                    source, target_dir / f"{source.stem}.{_OUTPUT_EXT}",
                    False, error=f"ODA 执行失败: {e}",
                )
            duration = time.monotonic() - start

            error_hint = _failure_hint(result.returncode, timeout, retries + 1)
            return self._collect_result(
                source, target_dir,
                returncode=result.returncode, duration=duration,
                error_hint=error_hint,
            )

    # ------------------------------------------------------------------ #
    # 目录批量转换
    # ------------------------------------------------------------------ #
    def convert_directory(
        self,
        source_dir: Path,
        target_dir: Path,
        version: Optional[str] = None,
        audit: Optional[bool] = None,
        recursive: bool = False,
        file_filter: str = "*.dwg",
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> BatchResult:
        """批量转换整个目录。ODA 一次调用处理目录内所有匹配文件。

        转换后扫描目标目录的实际产物构造结果，而非假设全部成功。
        源目录不存在返回含失败条目的 BatchResult（不抛）。
        """
        source_dir = Path(source_dir).resolve()
        target_dir = Path(target_dir).resolve()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            return BatchResult([ConvertResult(
                source=source_dir,
                target=target_dir / f"{source_dir.name}.{_OUTPUT_EXT}",
                success=False,
                error=f"输出路径被非目录文件占用: {target_dir}",
            )])

        if not source_dir.is_dir():
            # 源目录不存在：返回失败结果（不抛异常），让上层统一走结果处理。
            # raise OdaConvertError(f"源目录不存在: {source_dir}")
            return BatchResult([ConvertResult(
                source=source_dir,
                target=target_dir / f"{source_dir.name}.{_OUTPUT_EXT}",
                success=False,
                error=f"源目录不存在: {source_dir}",
            )])

        sources = sorted(source_dir.glob(file_filter))
        if not sources:
            logger.warning("源目录无匹配文件 (%s): %s", file_filter, source_dir)
            return BatchResult()

        version, audit, timeout, retries = self._resolve_defaults(
            version=version, audit=audit, timeout=timeout, retries=retries,
        )

        cmd = self._build_cmd(
            source_dir, target_dir, version,
            recursive=recursive, audit=audit, file_filter=file_filter,
        )

        start = time.monotonic()
        try:
            result = self._run_with_retries(cmd, timeout, retries)
        except (FileNotFoundError, PermissionError, OSError) as e:
            duration = time.monotonic() - start
            return BatchResult([
                ConvertResult(
                    source=s, target=target_dir / f"{s.stem}.{_OUTPUT_EXT}",
                    success=False, duration=duration,
                    error=f"ODA 执行失败: {e}",
                )
                for s in sources
            ])
        duration = time.monotonic() - start

        error_hint = _failure_hint(result.returncode, timeout, retries + 1)
        results = [
            self._collect_result(
                s, target_dir,
                returncode=result.returncode, duration=duration,
                error_hint=error_hint,
            )
            for s in sources
        ]
        return BatchResult(results)
