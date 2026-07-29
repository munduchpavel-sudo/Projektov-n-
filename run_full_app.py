from __future__ import annotations

import atexit
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _start_process(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(args, cwd=ROOT)


def main() -> int:
    api_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app_api:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]

    web_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app_web_3d.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8501",
        "--server.headless",
        "true",
    ]

    processes: list[subprocess.Popen] = []

    def shutdown(*_args: object) -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()

    atexit.register(shutdown)
    signal.signal(signal.SIGINT, lambda *_: shutdown())
    signal.signal(signal.SIGTERM, lambda *_: shutdown())

    print("Spoustim API na http://127.0.0.1:8000")
    print("Spoustim web na http://127.0.0.1:8501")

    processes.append(_start_process(api_cmd))
    processes.append(_start_process(web_cmd))

    exit_code = 0
    try:
        while True:
            alive = False
            for process in processes:
                code = process.poll()
                if code is None:
                    alive = True
                elif code != 0:
                    exit_code = code
                    shutdown()
                    return exit_code
            if not alive:
                return exit_code
            time.sleep(0.2)
    except KeyboardInterrupt:
        shutdown()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())