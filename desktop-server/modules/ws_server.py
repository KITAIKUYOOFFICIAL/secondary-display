"""
WebSocket Server Module for Real-Time Streaming
"""
import time
import json
import base64
import socket
import logging
import platform
import asyncio
from datetime import datetime
from pathlib import Path
import websockets
from .monitor import SystemMonitor
from .content import ContentManager

log = logging.getLogger("secdisp")


class WebSocketServer:
    """WebSocket 服务器：向手机副屏推送数据 (支持增量歌词与连接数保护)"""

    MAX_CLIENTS = 16

    def __init__(self, port: int, monitor: SystemMonitor, content: ContentManager, web_dir: Path = None):
        self.port = port
        self.monitor = monitor
        self.content = content
        self.web_dir = web_dir or (Path(__file__).parent.parent / "web")
        self.clients = set()
        self.ncm_clients = set()  # BetterNCM 探针直连客户端
        self._client_song_versions = {}  # websocket -> last_sent_song_version
        self._server = None
        self.push_interval = 0.2
        self.lyrics_interval = 0.2
        self._loop = None

        # 缓存壁纸
        self._cached_wallpaper_b64 = ""
        self._cached_wallpaper_mtime = 0
        self._load_wallpaper_cache()

    async def send_ncm_command(self, cmd: dict):
        """向 NetEase CEF 探针直发播放控制指令 (seek, play, pause, next, prev)"""
        dead = set()
        sent = 0
        for ws in list(self.ncm_clients):
            try:
                await ws.send(json.dumps({"type": "ncm_command", **cmd}, ensure_ascii=False))
                sent += 1
            except Exception:
                dead.add(ws)
        self.ncm_clients.difference_update(dead)
        log.info(f"探针指令 {cmd.get('action')}: 已发送给 {sent} 个探针 (共 {len(self.ncm_clients)} 在线)")

    def _load_wallpaper_cache(self):
        wp_path = self.web_dir / "wallpaper.jpg"
        if wp_path.exists():
            try:
                mtime = wp_path.stat().st_mtime
                if mtime != self._cached_wallpaper_mtime:
                    with open(wp_path, "rb") as f:
                        self._cached_wallpaper_b64 = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
                    self._cached_wallpaper_mtime = mtime
            except Exception:
                pass
        else:
            self._cached_wallpaper_b64 = ""

    def notify_wallpaper_updated(self):
        self._load_wallpaper_cache()

    def broadcast_sync(self, data: dict):
        """跨线程同步广播"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(data), self._loop)

    async def handler(self, websocket):
        """处理客户端连接"""
        if len(self.clients) >= self.MAX_CLIENTS:
            log.warning(f"拒绝新连接: 达到最大连接数限制 ({self.MAX_CLIENTS})")
            await websocket.close(1008, "Max connections reached")
            return

        self.clients.add(websocket)
        client_addr = websocket.remote_address if websocket.remote_address else "unknown"
        log.info(f"副屏连接: {client_addr} (当前连接: {len(self.clients)})")

        self._load_wallpaper_cache()
        wp_url = f"/wallpaper.jpg?t={int(time.time())}" if (self.web_dir / "wallpaper.jpg").exists() else ""

        try:
            # 发送初始握手
            await websocket.send(json.dumps({
                "type": "connected",
                "data": {
                    "hostname": socket.gethostname(),
                    "platform": platform.system(),
                    "platform_release": platform.release(),
                    "server_time": datetime.now().isoformat(),
                    "wallpaper_url": wp_url,
                    "wallpaper_image": self._cached_wallpaper_b64,
                    "message": "已连接到桌面副屏服务",
                }
            }, ensure_ascii=False))

            # 立即推送首帧系统状态与全量歌词
            stats_data = self.monitor.collect()
            await websocket.send(json.dumps({"type": "stats", "data": stats_data}, ensure_ascii=False))

            lyric_data = self.content.get_current_lyric()
            await websocket.send(json.dumps({"type": "lyrics", "data": lyric_data}, ensure_ascii=False))

        except Exception as e:
            log.debug(f"初始数据发送异常: {e}")

        try:
            async for message in websocket:
                await self._handle_client_message(websocket, message)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            self.ncm_clients.discard(websocket)
            log.info(f"副屏已断开: {client_addr} (剩余连接: {len(self.clients)})")

    async def _handle_client_message(self, websocket, message: str):
        try:
            msg = json.loads(message)
            msg_type = msg.get("type")

            # 1. 来自网易云音乐 BetterNCM 插件的毫秒级直推与注册
            if msg_type in ("ncm_hello", "ncm_sync"):
                is_new = websocket not in self.ncm_clients
                self.ncm_clients.add(websocket)
                if is_new:
                    log.info(f"探针注册: {websocket.remote_address} (当前 {len(self.ncm_clients)} 个探针)")
                data = msg.get("data")
                if data:
                    self.content.media_watcher.update_inflink({
                        "title": str(data.get("title", "")),
                        "artist": str(data.get("artist", "")),
                        "album": str(data.get("album", "")),
                        "cover": str(data.get("cover", "") or ""),
                        "duration": float(data.get("duration", 0) or 0),
                        "position": float(data.get("position", 0) or 0),
                        "playing": bool(data.get("playing", True)),
                        "source": "InfLinkWS",
                        "updated_at": time.time(),
                    })
                    self.content.tick()
                    asyncio.create_task(self.broadcast_lyrics())
                return

            if msg_type == "lyric_next":
                self.content.next_lyric()
                await self.broadcast_lyrics(force_full=True)
            elif msg_type == "lyric_prev":
                self.content.prev_lyric()
                await self.broadcast_lyrics(force_full=True)
            elif msg_type == "lyric_seek":
                self.content.set_lyric_index(msg.get("data", {}).get("index", 0))
                await self.broadcast_lyrics(force_full=True)
            elif msg_type == "set_lyrics":
                self.content.set_lyrics_text(msg.get("data", {}).get("text", ""))
                await self.broadcast_lyrics(force_full=True)
            elif msg_type == "media_control":
                action = msg.get("action") or msg.get("data", {}).get("action")
                pos = msg.get("position") if "position" in msg else msg.get("data", {}).get("position", 0)
                if action:
                    from modules.http_server import DashboardHTTPHandler
                    if action != "seek" and not DashboardHTTPHandler.cmd_dedupe(action):
                        log.info(f"媒体控制指令去重忽略: {action}")
                        return
                    # WS 通道: 只走 WS 直发 (不设 HTTP pending, 避免与 HTTP 入口重复执行)
                    if action == "seek" and pos is not None:
                        log.info(f"快进/跳转: {pos}s")
                        self.content.seek_to(float(pos))
                    else:
                        log.info(f"媒体控制指令: {action}")
                    await self.send_ncm_command({"action": action, "position": float(pos or 0)})
                    try:
                        import win32api
                        if action in ("play_pause", "toggle"):
                            win32api.keybd_event(0xB3, 0, 0, 0)
                            win32api.keybd_event(0xB3, 0, 2, 0)
                        elif action == "next":
                            win32api.keybd_event(0xB0, 0, 0, 0)
                            win32api.keybd_event(0xB0, 0, 2, 0)
                        elif action in ("prev", "previous"):
                            win32api.keybd_event(0xB1, 0, 0, 0)
                            win32api.keybd_event(0xB1, 0, 2, 0)
                    except Exception:
                        pass
                    
                    # 终极保底: 异步调用 PowerShell SMTC 操控 (独立线程, 不阻塞 WebSocket)
                    if action in ("play_pause", "toggle", "next", "prev", "previous"):
                        import threading, subprocess, os
                        def smtc_fallback():
                            try:
                                # 修正: 脚本实际位于 desktop-server/media_control.ps1
                                # (旧代码引用 scratch/smtc_control.ps1 不存在, 导致保底静默失效)
                                ps1_path = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'media_control.ps1'))
                                if not os.path.exists(ps1_path):
                                    log.warning(f"SMTC 控制脚本缺失: {ps1_path}")
                                    return
                                smtc_action = "play_pause" if action in ("play_pause", "toggle") else ("next" if action == "next" else "prev")
                                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", ps1_path, "-Action", smtc_action], creationflags=0x08000000, timeout=2)
                            except Exception as e:
                                log.warning(f"SMTC 保底控制失败: {e}")
                        threading.Thread(target=smtc_fallback, daemon=True).start()
                    self.content.tick()
                    await self.broadcast_lyrics(force_full=True)
            elif msg_type == "ping":
                pass
            elif msg_type == "volume":
                # 手机音量键 → PC 系统音量 (ctypes 模拟音量键, 零依赖不要求 pywin32)
                action = msg.get("action") or msg.get("data", {}).get("action", "")
                vk = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}.get(action)
                if vk:
                    try:
                        import ctypes
                        user32 = ctypes.windll.user32
                        user32.keybd_event(vk, 0, 0, 0)
                        user32.keybd_event(vk, 0, 2, 0)
                        log.info(f"音量键: {action}")
                    except Exception as e:
                        log.warning(f"音量键模拟失败: {e}")
            elif msg_type == "set_interval":
                interval = msg.get("data", {}).get("interval", 0.2)
                self.push_interval = max(0.1, min(10.0, float(interval)))
                log.info(f"推送间隔调整为: {self.push_interval}s")
        except json.JSONDecodeError:
            log.warning(f"收到无效消息: {message[:100]}")
        except Exception as e:
            log.error(f"处理客户端消息异常: {e}")

    async def broadcast(self, data: dict):
        if not self.clients:
            return
        message = json.dumps(data, ensure_ascii=False)
        dead = set()
        for client in list(self.clients):
            try:
                await client.send(message)
            except websockets.ConnectionClosed:
                dead.add(client)
            except Exception as e:
                log.debug(f"广播失败: {e}")
                dead.add(client)
        for d in dead:
            self.clients.discard(d)
            self._client_song_versions.pop(d, None)

    async def broadcast_stats(self):
        """广播硬件与系统性能数据"""
        data = self.monitor.collect()
        await self.broadcast({"type": "stats", "data": data})

    async def broadcast_lyrics(self, force_full: bool = False):
        """广播歌词进度 (统一完整包同步)"""
        if not self.clients:
            return
        data = self.content.get_current_lyric()
        await self.broadcast({"type": "lyrics", "data": data})

    async def push_loop(self):
        """高频系统状态推送循环 (200ms)"""
        while True:
            try:
                if self.clients:
                    await self.broadcast_stats()
            except Exception as e:
                log.error(f"推送循环异常: {e}")
            try:
                await asyncio.sleep(self.push_interval)
            except Exception:
                await asyncio.sleep(0.2)

    async def lyrics_loop(self):
        """歌词高频同步循环 (200ms)"""
        while True:
            try:
                if self.clients:
                    self.content.tick()
                    await self.broadcast_lyrics()
            except Exception as e:
                log.error(f"歌词循环异常: {e}")
            try:
                await asyncio.sleep(self.lyrics_interval)
            except Exception:
                await asyncio.sleep(0.2)

    def start(self):
        async def main():
            self._loop = asyncio.get_running_loop()
            self._server = await websockets.serve(
                self.handler,
                "0.0.0.0",
                self.port,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
            log.info(f"WebSocket 服务已启动在端口: {self.port}")
            await asyncio.gather(
                self.push_loop(),
                self.lyrics_loop(),
                return_exceptions=True
            )
        return main()
