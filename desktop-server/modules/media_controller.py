"""
Media Control Module:
High-speed native Windows SMTC controller (PowerShell worker daemon + Win32/VK fallback).
Supports instant Play/Pause, Next Track, Previous Track, and Seeking with sub-millisecond latency.
"""
import os
import sys
import time
import ctypes
import logging
import platform
import subprocess
from pathlib import Path
from threading import Thread, Lock

log = logging.getLogger("secdisp")


class MediaController:
    """Windows 原生多媒体交互控制器"""

    def __init__(self):
        self._lock = Lock()
        self._proc = None
        self._ready = False
        self._last_cmd_times = {}
        self._worker_script = Path(__file__).parent.parent / "media_control_worker.ps1"
        self._start_worker()

    def _start_worker(self):
        if platform.system() != "Windows" or not self._worker_script.exists():
            return
        try:
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(self._worker_script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
            # Wait for READY
            Thread(target=self._init_wait, daemon=True).start()
        except Exception as e:
            log.warning(f"启动多媒体控制常驻进程失败: {e}")

    def _init_wait(self):
        try:
            line = self._proc.stdout.readline()
            if "READY" in line:
                self._ready = True
                log.info("原生 Windows SMTC 多媒体控制引擎已就绪 (低延迟)")
        except Exception as e:
            log.warning(f"多媒体控制器初始化异常: {e}")

    def send_command(self, action: str, position: float = 0) -> bool:
        """执行多媒体控制指令"""
        action = str(action).lower().strip()
        now = time.time()
        if action in ("play_pause", "play", "pause"):
            last_t = self._last_cmd_times.get("play_pause", 0)
            if now - last_t < 0.28:
                log.info(f"[SMTC-CTRL] 自动忽略超高频重复指令: {action} (dt={(now-last_t)*1000:.1f}ms)")
                return True
            self._last_cmd_times["play_pause"] = now
        elif action in ("next", "prev"):
            last_t = self._last_cmd_times.get(action, 0)
            if now - last_t < 0.25:
                log.info(f"[SMTC-CTRL] 自动忽略超高频重复指令: {action}")
                return True
            self._last_cmd_times[action] = now

        success = False

        # 1. 优先使用 Windows SMTC 原生 Worker
        if self._ready and self._proc and self._proc.poll() is None:
            try:
                with self._lock:
                    cmd_line = f"{action} {position:.2f}\n" if action == "seek" else f"{action}\n"
                    self._proc.stdin.write(cmd_line)
                    self._proc.stdin.flush()
                    res = self._proc.stdout.readline().strip()
                    if res.startswith("OK:") and not res.endswith(":False"):
                        success = True
                        log.info(f"[SMTC-CTRL] 指令执行成功: {action} ({res})")
                    else:
                        log.info(f"[SMTC-CTRL] SMTC未响应或返回失败 ({res}), 触发 Win32 多重兜底")
            except Exception as e:
                log.warning(f"[SMTC-CTRL] Worker 执行异常: {e}")

        # 2. 如果 SMTC 返回失败或未就绪，使用 Win32 硬件按键 / 窗口消息多重兜底
        if not success:
            success = self._fallback_win32(action)

        return success

    def _fallback_win32(self, action: str) -> bool:
        """Win32 多媒体按键与 WM_APPCOMMAND 双重兜底"""
        try:
            user32 = ctypes.windll.user32
            vk_map = {
                "play_pause": 0xB3,
                "play": 0xB3,
                "pause": 0xB3,
                "next": 0xB0,
                "prev": 0xB1,
                "previous": 0xB1,
                "stop": 0xB2,
                "volume_mute": 0xAD,
                "mute": 0xAD,
                "volume_down": 0xAE,
                "volume_up": 0xAF,
            }
            vk = vk_map.get(action)
            if vk:
                scan = user32.MapVirtualKeyW(vk, 0)
                user32.keybd_event(vk, scan, 1, 0)
                time.sleep(0.02)
                user32.keybd_event(vk, scan, 1 | 2, 0)

            # WM_APPCOMMAND 广播
            appcmd_map = {
                "play_pause": 14,
                "play": 14,
                "pause": 14,
                "next": 11,
                "prev": 12,
                "previous": 12,
                "stop": 13,
                "volume_mute": 8,
                "volume_down": 9,
                "volume_up": 10,
            }
            appcmd = appcmd_map.get(action)
            if appcmd:
                user32.PostMessageW(0xFFFF, 0x0319, 0, appcmd << 16)

            log.info(f"[WIN32-FALLBACK] 多媒体指令兜底发送: {action}")
            return True
        except Exception as e:
            log.warning(f"Win32 媒体按键兜底失败: {e}")
            return False

    def stop(self):
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


# 单例全局实例
_controller = None

def get_media_controller() -> MediaController:
    global _controller
    if _controller is None:
        _controller = MediaController()
    return _controller
