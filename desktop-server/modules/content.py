"""
Custom Content & Lyrics Management Module
"""
import os
import re
import time
import logging
from pathlib import Path
from threading import Thread, Lock
from .media import MediaWatcher
from .lyrics import LyricsProvider, parse_lrc

log = logging.getLogger("secdisp")


class ContentManager:
    """管理自定义内容推送 — 歌词、通知等 (支持全量与增量同步)"""

    def __init__(self, ps_script: Path = None):
        self.notification_queue = []
        self._lock = Lock()

        # 手动歌词
        self.manual_lines = []
        self.manual_parsed_lines = []
        self.manual_index = 0
        self._manual_start_time = 0

        # 自动媒体歌词
        if ps_script is None:
            server_dir = Path(__file__).parent.parent
            ps_script = server_dir / "media_watcher.ps1"

        self.media_watcher = MediaWatcher(ps_script=ps_script)
        self.lyrics_provider = LyricsProvider()
        self._media_info = {}
        self._lyrics_lines = []
        self._lyrics_source_title = ""
        self._lyrics_fetched = False
        self._last_position_time = 0
        self._position_offset = 0
        self._last_reported_pos = None
        self._playing = False
        self._position_last_moving = None
        self._song_version = 0

        # 当前激活模式
        self.auto_mode = False

    # -----------------------------------------------------------------------
    # 手动歌词 API
    # -----------------------------------------------------------------------
    def set_lyrics_text(self, text: str):
        raw_lines = [line.strip() for line in text.split("\n") if line.strip()]
        has_lrc = any(re.search(r"\[\d{1,2}:\d{2}\.\d{2,3}\]", line) for line in raw_lines)
        if has_lrc:
            self.manual_parsed_lines = parse_lrc(text)
        else:
            self.manual_parsed_lines = [{"time": i * 5.0, "text": line} for i, line in enumerate(raw_lines)]

        self.manual_lines = raw_lines
        self.manual_index = 0
        self._manual_start_time = time.time()
        self.auto_mode = False
        with self._lock:
            self._song_version += 1
        log.info(f"手动歌词已设置: {len(self.manual_parsed_lines)} 行")

    def next_lyric(self):
        if self.manual_parsed_lines:
            self.manual_index = min(self.manual_index + 1, len(self.manual_parsed_lines) - 1)
            self._manual_start_time = time.time() - self.manual_parsed_lines[self.manual_index]["time"]

    def prev_lyric(self):
        if self.manual_parsed_lines:
            self.manual_index = max(self.manual_index - 1, 0)
            self._manual_start_time = time.time() - self.manual_parsed_lines[self.manual_index]["time"]

    def set_lyric_index(self, index: int):
        if self.manual_parsed_lines:
            self.manual_index = max(0, min(index, len(self.manual_parsed_lines) - 1))
            self._manual_start_time = time.time() - self.manual_parsed_lines[self.manual_index]["time"]

    # -----------------------------------------------------------------------
    # 通知 API
    # -----------------------------------------------------------------------
    def push_notification(self, title: str, body: str, level: str = "info"):
        with self._lock:
            self.notification_queue.append({
                "title": title,
                "body": body,
                "level": level,
                "timestamp": int(time.time()),
            })
            if len(self.notification_queue) > 20:
                self.notification_queue = self.notification_queue[-20:]

    def drain_notifications(self) -> list:
        with self._lock:
            notifs = self.notification_queue[:]
            self.notification_queue.clear()
            return notifs

    def seek_to(self, position: float):
        """用户主动跳转播放时间 (秒)"""
        with self._lock:
            self._position_offset = max(0.0, float(position))
            self._last_position_time = time.time()
            self._last_reported_pos = self._position_offset
            self._song_version += 1
        log.info(f"歌词/进度主动跳转至: {position:.1f}s")

    # ------------------------------------------------------------------------
    # 媒体与歌词同步
    # ------------------------------------------------------------------------
    def tick(self):
        """每 200ms 调用一次, 更新媒体播放进度并触发歌词获取"""
        media = self.media_watcher.get_current_media()
        if not media or not media.get("title"):
            return

        title = media.get("title", "")
        artist = media.get("artist", "")

        # 检测歌曲变化
        song_key = f"{title}||{artist}"
        if song_key != self._lyrics_source_title:
            self._lyrics_source_title = song_key
            self._lyrics_fetched = False
            self._lyrics_lines = []
            self._position_offset = 0
            self._last_position_time = time.time()
            self._last_reported_pos = None
            self._position_last_moving = None
            with self._lock:
                self._song_version += 1
            log.info(f"检测到新歌曲: {title} - {artist}")

            Thread(target=self._fetch_lyrics, args=(title, artist, media.get("album", ""), media.get("duration", 0)), daemon=True).start()

        # 记录播放状态与位置 (供平滑插值)
        playing_reported = bool(media.get("playing", True))
        pos = media.get("position", 0) or 0

        # 位置运动检测: 修正插件 getPlaybackStatus 误报
        # (网易云实际在播放时 InfLinkApi 偶发返回 Paused → position 仍持续前进)
        now = time.time()
        if self._last_reported_pos is not None and pos > self._last_reported_pos + 0.2:
            self._position_last_moving = now          # 位置在推进 → 在播放
            self._playing = True
        elif self._last_reported_pos is not None and self._position_last_moving and (now - self._position_last_moving) < 1.2:
            self._playing = True                      # 刚停止推进 ≤1.2s → 仍视为播放(缓冲)
        else:
            self._playing = playing_reported          # 停止推进超时 → 用上报值

        if pos != self._last_reported_pos:
            self._last_reported_pos = pos
            self._position_offset = pos
            self._last_position_time = time.time()

        self._media_info = media
        self.auto_mode = True

    def _fetch_lyrics(self, title: str, artist: str, album: str, duration: float):
        try:
            result = self.lyrics_provider.search(title, artist, album, duration)
            synced = result.get("syncedLyrics") or ""
            plain = result.get("plainLyrics") or ""
            lrc_text = synced if synced else plain
            lines = []
            if lrc_text:
                lines = parse_lrc(lrc_text)
                if not lines and plain:
                    lines = [{"time": i * 4.0, "text": line} for i, line in enumerate(plain.strip().split("\n")) if line.strip()]
            with self._lock:
                # 竞态保护: 异步 fetch 完成时校验歌曲是否已切换,
                # 若已切到新歌则丢弃过期结果, 防止"歌名/歌词不匹配"
                current_key = f"{self._media_info.get('title','')}||{self._media_info.get('artist','')}" if self._media_info else ""
                if current_key != f"{title}||{artist}":
                    log.info(f"丢弃过期歌词结果: {title} (当前歌曲已切换为: {current_key or '无'})")
                    return
                self._lyrics_lines = lines
                self._lyrics_fetched = True
                self.auto_mode = True
                self._song_version += 1
                if lines:
                    log.info(f"歌词已获取: {len(lines)} 行 ({title})")
                else:
                    log.info(f"未找到歌词: {title} - {artist}")
        except Exception as e:
            log.warning(f"获取歌词异常: {e}")
            with self._lock:
                self._lyrics_fetched = True

    def _current_time(self) -> float:
        """当前歌曲播放位置 (秒) — 基于媒体源上报的位置做平滑插值"""
        if not self._media_info or not self._media_info.get("title"):
            return 0
        if self._playing and self._position_offset >= 0:
            elapsed = time.time() - self._last_position_time
            return max(0.0, self._position_offset + elapsed)
        return max(0.0, self._position_offset)

    def _find_lyric_index(self, current_time: float) -> int:
        lines = self._lyrics_lines
        if not lines:
            return -1
        idx = 0
        for i, line in enumerate(lines):
            if line["time"] <= current_time:
                idx = i
            else:
                break
        return idx

    def get_song_version(self) -> int:
        return self._song_version

    def get_current_lyric(self) -> dict:
        """获取全量歌词数据 (含所有 lines)"""
        # 手动模式优先
        if not self.auto_mode and self.manual_parsed_lines:
            current_time = time.time() - self._manual_start_time
            idx = self.manual_index
            for i, line in enumerate(self.manual_parsed_lines):
                if line["time"] <= current_time:
                    idx = max(idx, i)
            lines = self.manual_parsed_lines
            duration = lines[-1]["time"] + 5.0 if lines else 0
            return {
                "version": self._song_version,
                "playing": True,
                "song": {"title": "手动歌词", "artist": "", "album": "", "duration": duration},
                "lines": lines,
                "current_index": idx,
                "current_time": current_time,
                "progress": current_time / duration if duration > 0 else 0,
                "current": lines[idx]["text"] if 0 <= idx < len(lines) else "",
                "next": lines[idx + 1]["text"] if -1 <= idx + 1 < len(lines) else "",
            }

        # 自动模式 (只要有歌曲信息即可展示)
        if self._media_info and self._media_info.get("title"):
            current_time = self._current_time()
            idx = self._find_lyric_index(current_time) if self._lyrics_lines else -1
            duration = self._media_info.get("duration", 0)
            if duration <= 0 and self._lyrics_lines:
                duration = self._lyrics_lines[-1]["time"] + 5.0
            if duration > 0:
                current_time = min(current_time, duration)
            return {
                "version": self._song_version,
                "playing": self._playing,
                "song": {
                    "title": self._media_info.get("title", "未知歌曲"),
                    "artist": self._media_info.get("artist", ""),
                    "album": self._media_info.get("album", ""),
                    "cover": self._media_info.get("cover", ""),
                    "duration": duration,
                },
                "lines": self._lyrics_lines,
                "current_index": idx,
                "current_time": current_time,
                "progress": current_time / duration if duration > 0 else 0,
                "current": self._lyrics_lines[idx]["text"] if (self._lyrics_lines and 0 <= idx < len(self._lyrics_lines)) else ("暂无歌词" if self._lyrics_fetched else "正在加载歌词..."),
                "next": self._lyrics_lines[idx + 1]["text"] if (self._lyrics_lines and -1 <= idx + 1 < len(self._lyrics_lines)) else "",
            }

        return {
            "version": self._song_version,
            "playing": self._playing,
            "song": {"title": "等待音乐...", "artist": "", "album": "", "duration": 0},
            "lines": [],
            "current_index": -1,
            "current_time": 0,
            "current": "暂无歌词",
            "next": "",
        }

    def get_lyric_tick(self) -> dict:
        """获取轻量级增量歌词进度 (不包含 lines，大幅减少网络传输)"""
        full = self.get_current_lyric()
        return {
            "version": full.get("version", 0),
            "playing": self._playing,
            "current_index": full.get("current_index", -1),
            "current_time": full.get("current_time", 0),
            "progress": full.get("progress", 0),
            "duration": full.get("song", {}).get("duration", 0),
            "current": full.get("current", ""),
            "next": full.get("next", "")
        }
