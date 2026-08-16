"""
HTTP Web Dashboard & REST API Module
"""
import time
import json
import base64
import socket
import logging
import platform
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from .discovery import DiscoveryBroadcaster
from .content import ContentManager
from .monitor import SystemMonitor

log = logging.getLogger("secdisp")


class DashboardHTTPHandler(SimpleHTTPRequestHandler):
    """HTTP 服务：提供网页仪表盘和 REST API"""

    ws_port = 8765
    http_port = 8080
    content_manager = None
    system_monitor = None
    ws_server = None
    pending_inflink_command = None
    # 命令去重 (WS/HTTP 双通道 + 前端兜底双发 → 同操作只执行一次)
    _last_cmd_time = {}

    @classmethod
    def cmd_dedupe(cls, action: str) -> bool:
        """同一动作 800ms 内只允许执行一次; 返回 True 表示应执行"""
        import time as _t
        now = _t.time()
        last = cls._last_cmd_time.get(action, 0)
        if now - last < 0.8:
            return False
        cls._last_cmd_time[action] = now
        return True
    inflink_diag = None

    def __init__(self, *args, **kwargs):
        self.directory = str(Path(__file__).parent.parent / "web")
        super().__init__(*args, directory=self.directory, **kwargs)

    def _json_response(self, data: dict, code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_ota(self):
        """处理 OTA 热更新指令并向所有连接的副屏客户端广播"""
        try:
            if self.ws_server:
                self.ws_server.broadcast_sync({
                    "type": "ota_reload",
                    "data": {
                        "timestamp": int(time.time()),
                        "message": "OTA 热更新已下发"
                    }
                })
            log.info("已向所有手机副屏广播 OTA 热更新指令！")
            self._json_response({"ok": True, "message": "OTA 热更新已广播"})
        except Exception as e:
            self._json_response({"ok": False, "error": str(e)}, 500)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/inflink_diag":
            self._json_response({"data": DashboardHTTPHandler.inflink_diag})
            return

        if path == "/api/info" or path == "/discovery":
            self._json_response({
                "hostname": socket.gethostname(),
                "platform": platform.system(),
                "ws_port": self.ws_port,
                "http_port": self.http_port,
                "ip": DiscoveryBroadcaster(self.ws_port)._get_local_ip(),
                "magic": DiscoveryBroadcaster.MAGIC,
            })
            return

        if path == "/api/stats":
            stats = self.system_monitor.collect() if self.system_monitor else {}
            self._json_response({"data": stats})
            return

        if path == "/api/lyrics":
            lyric = self.content_manager.get_current_lyric() if self.content_manager else None
            self._json_response({"data": lyric})
            return

        if path == "/api/media":
            info = {}
            if self.content_manager:
                info = self.content_manager.media_watcher.get_current_media()
            self._json_response({"data": info})
            return

        if path == "/api/ota":
            self._handle_ota()
            return

        if path == "/api/wallpaper":
            wp_path = Path(self.directory) / "wallpaper.jpg"
            if wp_path.exists():
                self._json_response({"has_wallpaper": True, "url": f"/wallpaper.jpg?t={int(time.time())}"})
            else:
                self._json_response({"has_wallpaper": False, "url": ""})
            return

        if path == "/" or path == "":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))

        # 保护：单次 POST 包大小限制为 10MB
        if content_length > 10 * 1024 * 1024:
            self._json_response({"ok": False, "error": "Payload too large (max 10MB)"}, 413)
            return

        raw_body = self.rfile.read(content_length) if content_length else b""
        try:
            body = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            try:
                body = raw_body.decode("gbk")
            except UnicodeDecodeError:
                body = raw_body.decode("utf-8", errors="replace")

        if path == "/api/ota":
            self._handle_ota()
            return

        if path == "/api/wallpaper":
            try:
                data = json.loads(body) if body else {}
                img_b64 = data.get("image", "")
                if "," in img_b64:
                    img_b64 = img_b64.split(",", 1)[1]
                raw_bytes = base64.b64decode(img_b64)
                wp_path = Path(self.directory) / "wallpaper.jpg"
                with open(wp_path, "wb") as f:
                    f.write(raw_bytes)
                full_b64 = "data:image/jpeg;base64," + base64.b64encode(raw_bytes).decode("ascii")
                wp_url = f"/wallpaper.jpg?t={int(time.time())}"
                if self.ws_server:
                    self.ws_server.notify_wallpaper_updated()
                    self.ws_server.broadcast_sync({
                        "type": "wallpaper_update",
                        "data": {"url": wp_url, "image": full_b64}
                    })
                log.info(f"壁纸已更新并推送到所有手机副屏: {len(raw_bytes)} 字节")
                self._json_response({"ok": True, "url": wp_url})
            except Exception as e:
                log.error(f"壁纸上传失败: {e}")
                self._json_response({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/lyrics" and self.content_manager:
            try:
                data = json.loads(body)
                self.content_manager.set_lyrics_text(data.get("text", ""))
                if self.ws_server:
                    self.ws_server.broadcast_sync({
                        "type": "lyrics",
                        "data": self.content_manager.get_current_lyric()
                    })
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/lyric/next" and self.content_manager:
            self.content_manager.next_lyric()
            if self.ws_server:
                self.ws_server.broadcast_sync({
                    "type": "lyrics",
                    "data": self.content_manager.get_current_lyric()
                })
            self._json_response({"ok": True})
            return

        if path == "/api/lyric/prev" and self.content_manager:
            self.content_manager.prev_lyric()
            if self.ws_server:
                self.ws_server.broadcast_sync({
                    "type": "lyrics",
                    "data": self.content_manager.get_current_lyric()
                })
            self._json_response({"ok": True})
            return

        if path == "/api/notification" and self.content_manager:
            try:
                data = json.loads(body)
                self.content_manager.push_notification(
                    data.get("title", ""),
                    data.get("body", ""),
                    data.get("level", "info"),
                )
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/inflink_diag":
            try:
                data = json.loads(body) if body else {}
                DashboardHTTPHandler.inflink_diag = data
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/media_control":
            try:
                data = json.loads(body) if body else {}
                action = data.get("action", "")
                position = float(data.get("position", 0) or 0)
                if action != "seek" and not DashboardHTTPHandler.cmd_dedupe(action):
                    self._json_response({"ok": True, "deduped": True})
                    return
                # HTTP 兜底通道: 只设 pending (不 broadcast WS, 避免与 WS 入口重复执行)
                DashboardHTTPHandler.pending_inflink_command = {"action": action, "position": position}
                if self.content_manager:
                    if action == "seek":
                        self.content_manager.seek_to(position)
                    self.content_manager.tick()
                    if self.ws_server:
                        self.ws_server.broadcast_sync({
                            "type": "lyrics",
                            "data": self.content_manager.get_current_lyric()
                        })
                # 触发 Windows 系统媒体按键 (0ms 瞬时硬件级响应)
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

                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/inflink" and self.content_manager:
            try:
                data = json.loads(body) if body else {}
                self.content_manager.media_watcher.update_inflink({
                    "title": str(data.get("title", "")),
                    "artist": str(data.get("artist", "")),
                    "album": str(data.get("album", "")),
                    "cover": str(data.get("cover", "") or ""),
                    "duration": float(data.get("duration", 0) or 0),
                    "position": float(data.get("position", 0) or 0),
                    "playing": bool(data.get("playing", True)),
                    "source": "InfLinkApi",
                    "updated_at": time.time(),
                })
                cmd = DashboardHTTPHandler.pending_inflink_command
                DashboardHTTPHandler.pending_inflink_command = None
                self._json_response({"ok": True, "command": cmd})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
            return

        self._json_response({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_http_server(port: int, ws_port: int, content: ContentManager, monitor, ws_server):
    """在独立线程中启动 HTTP 服务器"""
    DashboardHTTPHandler.ws_port = ws_port
    DashboardHTTPHandler.http_port = port
    DashboardHTTPHandler.content_manager = content
    DashboardHTTPHandler.system_monitor = monitor
    DashboardHTTPHandler.ws_server = ws_server
    server = HTTPServer(("0.0.0.0", port), DashboardHTTPHandler)
    log.info(f"HTTP 仪表盘已启动 — 端口: {port}")
    server.serve_forever()
