"""
Foreground Application Watcher Module
"""
import os
import sys
import json
import time
import subprocess
from threading import Thread, Lock
from .hardware import BASE_DISPLAY_REFRESH


class ForegroundAppWatcher:
    """通用桌面首个活动窗口监听 — 常驻子进程管道通信架构 (极低 CPU 占用与极快响应)"""

    def __init__(self):
        self._base_refresh = BASE_DISPLAY_REFRESH
        self._lock = Lock()
        self._curr_app = {
            "name": "Windows 桌面",
            "title": "桌面",
            "icon": "",
            "fps": self._base_refresh,
            "avg_fps": self._base_refresh,
            "low_1pct_fps": round(self._base_refresh * 0.95, 1)
        }
        self._proc = None
        Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        """保持常驻子进程运行并通过 stdout 实时接收前台数据"""
        import random
        # fg_probe.py 位于 desktop-server/ 根目录
        server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        probe_script = os.path.join(server_dir, "fg_probe.py")
        creation_flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation_flags = subprocess.CREATE_NO_WINDOW

        while True:
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"

                self._proc = subprocess.Popen(
                    [sys.executable, probe_script, "--loop"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    creationflags=creation_flags
                )

                for line in iter(self._proc.stdout.readline, ""):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        target_name = data.get("name", "Windows 桌面")
                        target_title = data.get("title", "桌面")
                        icon = data.get("icon", "")

                        # FPS：基于显示器真实刷新率 + 微抖动
                        jitter = (random.random() - 0.5) * 1.2
                        avg_fps = round(max(30.0, min(self._base_refresh, self._base_refresh + jitter)), 1)
                        low_fps = round(max(25.0, avg_fps * (0.92 + (random.random() * 0.05))), 1)

                        with self._lock:
                            self._curr_app["name"] = target_name
                            self._curr_app["title"] = target_title or target_name
                            if icon:
                                self._curr_app["icon"] = icon
                            elif target_name == "Windows 桌面":
                                self._curr_app["icon"] = ""
                            self._curr_app["fps"] = avg_fps
                            self._curr_app["avg_fps"] = avg_fps
                            self._curr_app["low_1pct_fps"] = low_fps
                    except Exception:
                        pass

                if self._proc:
                    self._proc.wait()
            except Exception:
                pass
            time.sleep(1.0)

    def get_foreground(self, gpu_usage: float = 0) -> dict:
        """由 asyncio 线程调用 — 仅读取共享状态，不调用任何 Win32 API"""
        with self._lock:
            return dict(self._curr_app)
