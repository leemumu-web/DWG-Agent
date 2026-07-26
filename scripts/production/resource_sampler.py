#!/usr/bin/env python3
"""Sample host, Docker, MySQL and Job pressure without mutating production."""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b)\s*$", re.I)
_SIZE_FACTORS = {
    "b": 1,
    "kb": 1_000,
    "mb": 1_000_000,
    "gb": 1_000_000_000,
    "tb": 1_000_000_000_000,
    "kib": 1_024,
    "mib": 1_048_576,
    "gib": 1_073_741_824,
    "tib": 1_099_511_627_776,
}


@dataclass(frozen=True, slots=True)
class DockerStat:
    name: str
    cpu_percent: float
    memory_used_bytes: int
    memory_limit_bytes: int
    memory_percent: float
    network_rx_bytes: int
    network_tx_bytes: int
    block_read_bytes: int
    block_write_bytes: int
    pids: int


def utc_now() -> datetime:
    """Return an aware UTC timestamp on Python 3.10 and newer."""
    return datetime.now(timezone.utc)  # noqa: UP017 - server host uses Python 3.10


def parse_size_bytes(value: str) -> int:
    match = _SIZE_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"无法识别的容量：{value}")
    number, unit = match.groups()
    return round(float(number) * _SIZE_FACTORS[unit.casefold()])


def _parse_percent(value: str) -> float:
    normalized = value.strip()
    if not normalized.endswith("%"):
        raise ValueError(f"无法识别的百分比：{value}")
    return float(normalized[:-1])


def _parse_io_pair(value: str) -> tuple[int, int]:
    parts = value.split("/")
    if len(parts) != 2:
        raise ValueError(f"无法识别的双向 I/O：{value}")
    return parse_size_bytes(parts[0]), parse_size_bytes(parts[1])


def parse_docker_stat(row: Mapping[str, str]) -> DockerStat:
    memory_used, memory_limit = _parse_io_pair(row["MemUsage"])
    network_rx, network_tx = _parse_io_pair(row["NetIO"])
    block_read, block_write = _parse_io_pair(row["BlockIO"])
    return DockerStat(
        name=row["Name"],
        cpu_percent=_parse_percent(row["CPUPerc"]),
        memory_used_bytes=memory_used,
        memory_limit_bytes=memory_limit,
        memory_percent=_parse_percent(row["MemPerc"]),
        network_rx_bytes=network_rx,
        network_tx_bytes=network_tx,
        block_read_bytes=block_read,
        block_write_bytes=block_write,
        pids=int(row["PIDs"]),
    )


def clamp_sampling_interval(value: float) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError("采样间隔必须是数字")
    return min(max(float(value), 0.25), 60.0)


def _nested_number(
    sample: Mapping[str, Any],
    section: str,
    key: str,
) -> float:
    group = sample.get(section)
    if not isinstance(group, Mapping):
        return 0.0
    value = group.get(key, 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def summarize_samples(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not samples:
        raise ValueError("资源采样不能为空")
    timestamps: list[float] = []
    for sample in samples:
        stamp = sample.get("monotonic_seconds")
        if not isinstance(stamp, (int, float)):
            raise ValueError("资源采样缺少单调时间戳")
        timestamps.append(float(stamp))
    if any(
        current <= previous for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ):
        raise ValueError("资源采样时间戳必须严格单调递增")

    peaks = {
        "host_cpu_percent": max(
            _nested_number(sample, "host", "cpu_percent") for sample in samples
        ),
        "host_memory_used_bytes": int(
            max(_nested_number(sample, "host", "memory_used_bytes") for sample in samples)
        ),
        "host_swap_used_bytes": int(
            max(_nested_number(sample, "host", "swap_used_bytes") for sample in samples)
        ),
        "mysql_threads_connected": int(
            max(_nested_number(sample, "mysql", "threads_connected") for sample in samples)
        ),
        "jobs_running": int(max(_nested_number(sample, "jobs", "running") for sample in samples)),
        "jobs_queued": int(max(_nested_number(sample, "jobs", "queued") for sample in samples)),
    }
    names: set[str] = set()
    for sample in samples:
        containers = sample.get("containers")
        if isinstance(containers, Mapping):
            names.update(str(name) for name in containers)
    container_summary: dict[str, dict[str, Any]] = {}
    for name in sorted(names):
        rows = [
            sample["containers"][name]
            for sample in samples
            if isinstance(sample.get("containers"), Mapping)
            and isinstance(sample["containers"].get(name), Mapping)
        ]
        container_summary[name] = {
            "peak_cpu_percent": max(float(row.get("cpu_percent", 0)) for row in rows),
            "peak_memory_used_bytes": int(
                max(float(row.get("memory_used_bytes", 0)) for row in rows)
            ),
            "max_restart_count": int(max(float(row.get("restart_count", 0)) for row in rows)),
            "oom_killed": any(bool(row.get("oom_killed")) for row in rows),
        }
    return {
        "sample_count": len(samples),
        "duration_seconds": round(timestamps[-1] - timestamps[0], 3),
        "peaks": peaks,
        "containers": container_summary,
    }


def _run(
    command: Sequence[str],
    *,
    timeout: float = 10.0,
) -> str:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"{command[0]} 返回 {completed.returncode}：{message[:500]}")
    return completed.stdout


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        multiplier = 1024 if len(parts) > 1 and parts[1] == "kB" else 1
        values[key] = int(parts[0]) * multiplier
    return values


def _cpu_counters() -> tuple[int, int]:
    first = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
    values = [int(value) for value in first.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _disk_counters() -> dict[str, int]:
    read_sectors = 0
    write_sectors = 0
    inflight = 0
    io_milliseconds = 0
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        if name.startswith(("loop", "ram", "fd", "sr", "dm-")):
            continue
        # Count whole disks only; partitions repeat the same underlying I/O.
        if re.search(r"(?:\d|p\d+)$", name):
            continue
        read_sectors += int(parts[5])
        write_sectors += int(parts[9])
        inflight += int(parts[11])
        io_milliseconds += int(parts[12])
    return {
        "read_bytes": read_sectors * 512,
        "write_bytes": write_sectors * 512,
        "inflight": inflight,
        "io_milliseconds": io_milliseconds,
    }


class ResourceSampler:
    def __init__(self, *, docker_bin: str = "docker") -> None:
        self.docker_bin = docker_bin
        self._last_cpu: tuple[int, int] | None = None

    def _host(self) -> dict[str, Any]:
        memory = _meminfo()
        cpu = _cpu_counters()
        cpu_percent = 0.0
        if self._last_cpu is not None:
            total_delta = cpu[0] - self._last_cpu[0]
            idle_delta = cpu[1] - self._last_cpu[1]
            if total_delta > 0:
                cpu_percent = 100 * (1 - idle_delta / total_delta)
        self._last_cpu = cpu
        loads = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        memory_total = memory.get("MemTotal", 0)
        memory_available = memory.get("MemAvailable", 0)
        swap_total = memory.get("SwapTotal", 0)
        swap_free = memory.get("SwapFree", 0)
        return {
            "cpu_percent": round(cpu_percent, 3),
            "load_1m": float(loads[0]),
            "load_5m": float(loads[1]),
            "load_15m": float(loads[2]),
            "memory_total_bytes": memory_total,
            "memory_available_bytes": memory_available,
            "memory_used_bytes": max(memory_total - memory_available, 0),
            "swap_total_bytes": swap_total,
            "swap_used_bytes": max(swap_total - swap_free, 0),
            "disk": _disk_counters(),
        }

    def _docker(self) -> dict[str, dict[str, Any]]:
        stats_by_name: dict[str, dict[str, Any]] = {}
        raw_stats = _run(
            [
                self.docker_bin,
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
            ],
            timeout=30,
        )
        for line in raw_stats.splitlines():
            if not line.strip():
                continue
            stat = parse_docker_stat(json.loads(line))
            stats_by_name[stat.name] = asdict(stat)

        container_ids = [
            item
            for item in _run(
                [self.docker_bin, "ps", "-q"],
            ).splitlines()
            if item
        ]
        if not container_ids:
            return stats_by_name
        inspected = json.loads(
            _run(
                [self.docker_bin, "inspect", *container_ids],
                timeout=30,
            )
        )
        for item in inspected:
            name = str(item.get("Name") or "").lstrip("/")
            if not name:
                continue
            state = item.get("State") if isinstance(item.get("State"), dict) else {}
            health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
            row = stats_by_name.setdefault(name, {"name": name})
            row.update(
                {
                    "status": state.get("Status"),
                    "health": health.get("Status"),
                    "restart_count": int(item.get("RestartCount") or 0),
                    "oom_killed": bool(state.get("OOMKilled")),
                }
            )
        return stats_by_name

    def _mysql_container(self) -> str | None:
        lines = _run(
            [
                self.docker_bin,
                "ps",
                "--filter",
                "label=com.docker.compose.service=mysql",
                "--format",
                "{{.ID}}",
            ]
        ).splitlines()
        return lines[0] if lines else None

    def _database_pressure(self) -> tuple[dict[str, int], dict[str, int]]:
        container_id = self._mysql_container()
        if container_id is None:
            raise RuntimeError("没有找到 Compose mysql 容器")
        query = (
            "SELECT CONCAT('job.', status), COUNT(*) FROM jobs "
            "GROUP BY status;"
            "SELECT CONCAT('mysql.', VARIABLE_NAME), VARIABLE_VALUE "
            "FROM performance_schema.global_status "
            "WHERE VARIABLE_NAME IN "
            "('Threads_connected','Threads_running','Max_used_connections');"
        )
        output = _run(
            [
                self.docker_bin,
                "exec",
                container_id,
                "sh",
                "-lc",
                ('exec mysql -N -B -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "$1"'),
                "resource-sampler",
                query,
            ],
            timeout=15,
        )
        jobs = {
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
        }
        mysql: dict[str, int] = {}
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            key, raw_value = parts
            try:
                value = int(raw_value)
            except ValueError:
                continue
            if key.startswith("job."):
                jobs[key.removeprefix("job.")] = value
            elif key.startswith("mysql."):
                mysql[key.removeprefix("mysql.").casefold()] = value
        return mysql, jobs

    def sample(self) -> dict[str, Any]:
        sample: dict[str, Any] = {
            "timestamp": utc_now().isoformat(),
            "monotonic_seconds": time.monotonic(),
            "errors": [],
        }
        collectors = {
            "host": self._host,
            "containers": self._docker,
        }
        for key, collector in collectors.items():
            try:
                sample[key] = collector()
            except Exception as exc:
                sample[key] = {}
                sample["errors"].append({"component": key, "message": str(exc)[:500]})
        try:
            mysql, jobs = self._database_pressure()
            sample["mysql"] = mysql
            sample["jobs"] = jobs
        except Exception as exc:
            sample["mysql"] = {}
            sample["jobs"] = {}
            sample["errors"].append({"component": "mysql_jobs", "message": str(exc)[:500]})
        return sample


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读采样服务器资源、容器、MySQL 连接和任务压力。",
    )
    parser.add_argument("--output", type=Path, required=True, help="JSONL 输出路径")
    parser.add_argument("--summary", type=Path, help="峰值摘要 JSON 路径")
    parser.add_argument("--interval", type=float, default=1.0, help="采样秒数")
    parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="总采样秒数；0 表示运行到收到终止信号",
    )
    parser.add_argument("--docker-bin", default="docker")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.duration < 0:
        print("总采样秒数不能为负数", file=sys.stderr)
        return 2
    interval = clamp_sampling_interval(args.interval)
    stop = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary_path = args.summary or args.output.with_suffix(".summary.json")
    samples: list[dict[str, Any]] = []
    sampler = ResourceSampler(docker_bin=args.docker_bin)
    started = time.monotonic()
    next_sample = started
    with args.output.open("w", encoding="utf-8") as stream:
        while not stop:
            now = time.monotonic()
            if args.duration and now - started >= args.duration:
                break
            if now < next_sample:
                time.sleep(min(next_sample - now, 0.25))
                continue
            sample = sampler.sample()
            samples.append(sample)
            stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
            stream.flush()
            next_sample = max(next_sample + interval, time.monotonic())
    if not samples:
        print("采样期间没有生成任何数据", file=sys.stderr)
        return 1
    summary = summarize_samples(samples)
    summary["samples_with_errors"] = sum(bool(item["errors"]) for item in samples)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
