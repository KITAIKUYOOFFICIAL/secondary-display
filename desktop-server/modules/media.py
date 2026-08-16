"""
Media Playback & Session Watcher Module
"""
import os
import sys
import time
import json
import logging
import platform
import subprocess
from pathlib import Path
from threading import Thread, Lock

log = logging.getLogger("secdisp")

WINSDK_AVAILABLE = False
if platform.system() == "Windows":
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )
        WINSDK_AVAILABLE = True
    except ImportError:
        pass


class PowerShellMediaWatcher:
    """通过 PowerShell + Windows SMTC 实时获取当前播放媒体 (带精确进度)"""

    def __init__(self, script_path: Path):
        self._current = {}
        self._lock = Lock()
        self._proc = None
        self._script = script_path
        self._start()

    @staticmethod
    def _find_powershell() -> str:
        for exe in ("powershell", "pwsh"):
            try:
                p = subprocess.run(
                    [exe, "-NoProfile", "-NonInteractive", "-Command", "echo ready"],
                    capture_output=True, text=True, timeout=8,
                )
                if p.returncode == 0 and "ready" in p.stdout:
                    return exe
            except Exception:
                pass
        return None

    def _start(self):
        if not self._script or not self._script.exists():
            log.debug("media_watcher.ps1 未找到, 跳过 PowerShell SMTC 媒体监听")
            return
        ps = self._find_powershell()
        if not ps:
            log.debug("未找到 PowerShell, 跳过 SMTC 媒体监听")
            return
        try:
            self._proc = subprocess.Popen(
                [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(self._script)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
            )
            Thread(target=self._read_loop, daemon=True).start()
            Thread(target=self._stderr_loop, daemon=True).start()
            log.info("已启用 PowerShell SMTC 媒体检测 (精确播放进度)")
        except Exception as e:
            log.warning(f"启动 PowerShell 媒体监听失败: {e}")

    def _stderr_loop(self):
        try:
            for line in iter(self._proc.stderr.readline, ""):
                line = line.strip()
                if line:
                    log.warning(f"[SMTC] ps错误: {line[:200]}")
        except Exception:
            pass

    def _read_loop(self):
        try:
            for line in iter(self._proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    with self._lock:
                        self._current = data
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"[SMTC] 读取线程退出: {e}")

    def get_current_media(self) -> dict:
        with self._lock:
            return dict(self._current) if self._current else {}

    def stop(self):
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass


class MediaWatcher:
    """监听当前播放的音乐

    检测优先级:
      0. BetterNCM InfLinkApi 推送 (最高优先级, 毫秒级原生直推)
      1. PowerShell SMTC (Windows.Media.Control) — 精确标题/歌手/进度/播放状态
      2. winsdk SMTC — 同上, 仅在 winsdk 可用时使用
      3. 窗口标题解析 — 兜底 (网易云音乐 / QQ音乐 / 酷狗 等窗口标题)
    """

    PLAYER_PATTERNS = [
        ("网易云音乐", "网易云音乐"),
        ("QQ音乐", "QQ音乐"),
        ("酷狗音乐", "酷狗音乐"),
        ("酷我音乐", "酷我音乐"),
        ("Spotify", "Spotify"),
    ]

    PLAYER_PROCESSES = {
        "cloudmusic.exe": "网易云音乐",
        "music.ui.exe": "网易云音乐",
        "qqmusic.exe": "QQ音乐",
        "kugou.exe": "酷狗音乐",
        "kwmusic.exe": "酷我音乐",
        "spotify.exe": "Spotify",
    }

    def __init__(self, ps_script: Path = None):
        self._last_media = {}
        self._lock = Lock()
        self._manager = None
        self._pygetwindow = None
        self._win32gui = None
        self._win32process = None
        self._inflink_data = {}
        self._inflink_data_time = 0

        self._ps = PowerShellMediaWatcher(ps_script) if (platform.system() == "Windows" and ps_script) else None

        try:
            import pygetwindow
            self._pygetwindow = pygetwindow
        except ImportError:
            pass

        try:
            import win32gui
            import win32process
            self._win32gui = win32gui
            self._win32process = win32process
        except ImportError:
            pass

        if WINSDK_AVAILABLE:
            try:
                self._manager = MediaManager.request_async().get_results()
                log.info("已启用 winsdk SMTC 媒体检测")
            except Exception as e:
                log.warning(f"初始化 winsdk SMTC 失败: {e}")

    def _parse_window_title(self, title: str) -> dict:
        title = title.strip()
        if not title:
            return None

        for keyword, source in self.PLAYER_PATTERNS:
            if keyword not in title:
                continue
            prefix = title.replace(f" - {source}", "").strip()
            if not prefix or prefix == source:
                return None

            parts = [p.strip() for p in prefix.split(" - ") if p.strip()]
            if len(parts) >= 2:
                return {
                    "title": parts[0],
                    "artist": parts[1],
                    "album": "",
                    "duration": 0,
                    "position": 0,
                    "playing": True,
                    "source": source,
                    "updated_at": time.time(),
                }
            elif len(parts) == 1:
                return {
                    "title": parts[0],
                    "artist": "",
                    "album": "",
                    "duration": 0,
                    "position": 0,
                    "playing": True,
                    "source": source,
                    "updated_at": time.time(),
                }
        return None

    def _get_media_by_window(self) -> dict:
        if not self._pygetwindow:
            return None
        try:
            windows = self._pygetwindow.getAllWindows()
            for w in windows:
                if not w.title:
                    continue
                parsed = self._parse_window_title(w.title)
                if parsed:
                    return parsed
        except Exception:
            pass
        return None

    def _parse_title_generic(self, title: str, source: str) -> dict:
        t = title.strip()
        if not t:
            return None
        for suffix in (f" - {source}", f"- {source}"):
            if t.endswith(suffix):
                t = t[: -len(suffix)].strip()
                break
        parts = [p.strip() for p in t.split(" - ") if p.strip()]
        if not parts:
            return None
        return {
            "title": parts[0],
            "artist": parts[1] if len(parts) >= 2 else "",
            "album": "",
            "duration": 0,
            "position": 0,
            "playing": True,
            "source": source,
            "updated_at": time.time(),
        }

    def _get_media_by_window_proc(self) -> dict:
        if not (self._win32gui and self._win32process):
            return None
        try:
            import psutil as _psutil
        except ImportError:
            return None

        matches = []
        def _enum(hwnd, _):
            if not self._win32gui.IsWindowVisible(hwnd):
                return
            title = self._win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            try:
                _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
                pname = _psutil.Process(pid).name().lower()
            except Exception:
                return
            src = self.PLAYER_PROCESSES.get(pname)
            if src:
                matches.append((title, src))

        try:
            self._win32gui.EnumWindows(_enum, None)
        except Exception:
            return None

        for title, src in matches:
            parsed = self._parse_title_generic(title, src)
            if parsed:
                return parsed
        return None

    def _get_media_by_smtc(self) -> dict:
        if not WINSDK_AVAILABLE or not self._manager:
            return None
        try:
            session = self._manager.get_current_session()
            if not session:
                return None

            props = session.try_get_media_properties_async().get_results()
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()

            title = props.title or ""
            artist = props.artist or ""
            if not title:
                return None

            return {
                "title": title,
                "artist": artist,
                "album": props.album_title or "",
                "duration": timeline.end_time.total_seconds() if timeline.end_time else 0,
                "position": timeline.position.total_seconds() if timeline.position else 0,
                "playing": playback.playback_status.value == 4 if playback and playback.playback_status else False,
                "source": session.source_app_user_model_id or "",
                "updated_at": time.time(),
            }
        except Exception:
            return None

    def get_current_media(self) -> dict:
        """获取当前播放媒体信息 (按优先级尝试各来源)"""
        # 0. BetterNCM InfLinkApi 推送 (最高优先级, 保持歌曲信息)
        if self._inflink_data and self._inflink_data.get("title"):
            m = dict(self._inflink_data)
            # 若超过 4 秒未收到高频心跳，自动标记为暂停状态但保留曲目信息
            if (time.time() - self._inflink_data_time) > 4.0:
                m["playing"] = False
            with self._lock:
                self._last_media = m
            return m

        # 1. PowerShell SMTC (主)
        if self._ps:
            m = self._ps.get_current_media()
            if m and m.get("title"):
                with self._lock:
                    self._last_media = m
                return m

        # 2. winsdk SMTC
        m = self._get_media_by_smtc()
        if m:
            with self._lock:
                self._last_media = m
            return m

        # 3. 窗口标题 (兜底)
        m = self._get_media_by_window()
        if m:
            with self._lock:
                self._last_media = m
            return m

        # 3.5 窗口进程识别
        m = self._get_media_by_window_proc()
        if m:
            with self._lock:
                self._last_media = m
            return m

        return self._last_media

    def update_inflink(self, data: dict):
        """由 ContentManager 调用: 接收 BetterNCM 插件推送的精确进度"""
        self._inflink_data = dict(data)
        self._inflink_data_time = time.time()
