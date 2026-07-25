from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import time


def _child(port_path: Path) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port_path.write_text(str(listener.getsockname()[1]), encoding="ascii")
        while True:
            connection, _ = listener.accept()
            connection.close()


def _parent(port_path: Path) -> int:
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child", str(port_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not port_path.exists():
        if child.poll() is not None:
            raise RuntimeError(f"listener child exited with {child.returncode}")
        time.sleep(0.01)
    if not port_path.exists():
        raise RuntimeError("listener child did not publish its port")
    print(f"listener-child-pid={child.pid}", flush=True)
    while True:
        time.sleep(1.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("port_path", type=Path)
    args = parser.parse_args()
    return _child(args.port_path) if args.child else _parent(args.port_path)


if __name__ == "__main__":
    raise SystemExit(main())
