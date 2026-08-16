"""
Secondary Display Desktop Server
================================
桌面端推送服务 — 将系统状态与自定义内容推送到手机副屏

架构已重构模块化:
- modules.hardware: 硬件配置、GPU/CPU功耗、屏幕刷新率
- modules.app_watcher: 常驻子进程前台窗口 & 图标高刷监听
- modules.monitor: 系统性能指标聚合 (psutil / CPU / 内存 / 磁盘 / 网络 / 温度)
- modules.media: Windows SMTC / PowerShell / BetterNCM 媒体同步
- modules.lyrics: 网易云 / LRCLIB 歌词源 & LRC解析
- modules.content: 歌词进度插值与增量推送管理器
- modules.discovery: UDP 局域网广播自动发现
- modules.ws_server: 实时 WebSocket 流服务器 (支持歌词轻量级增量同步与连接保护)
- modules.http_server: Web 仪表盘、静态资源与 REST/OTA API
"""

import sys
import asyncio
import logging
import argparse
from pathlib import Path
from threading import Thread

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("secdisp")

try:
    from modules.monitor import SystemMonitor
    from modules.content import ContentManager
    from modules.discovery import DiscoveryBroadcaster
    from modules.ws_server import WebSocketServer
    from modules.http_server import start_http_server
    from modules.media import WINSDK_AVAILABLE
except ImportError as e:
    log.error(f"模块加载失败: {e}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="桌面副屏推送服务")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket 端口 (默认 8765)")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP 仪表盘端口 (默认 8080)")
    parser.add_argument("--no-discovery", action="store_true", help="禁用 UDP 自动发现广播")
    parser.add_argument("--interval", type=float, default=0.2, help="系统状态推送间隔秒数 (默认 0.2)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("  桌面副屏推送服务 (Secondary Display Server) — 模块化高刷版")
    log.info("=" * 60)

    if WINSDK_AVAILABLE:
        log.info("  已启用 Windows SMTC 精确媒体检测")
    log.info("  已启用窗口标题检测 (网易云音乐 / QQ音乐 / 酷狗等)")

    monitor = SystemMonitor()
    content = ContentManager()
    ws_server = WebSocketServer(args.ws_port, monitor, content)
    ws_server.push_interval = args.interval

    # 启动 HTTP 服务
    http_thread = Thread(
        target=start_http_server,
        args=(args.http_port, args.ws_port, content, monitor, ws_server),
        daemon=True,
    )
    http_thread.start()

    # 启动 UDP 自动发现广播
    discovery = None
    if not args.no_discovery:
        discovery = DiscoveryBroadcaster(args.ws_port)
        discovery.start()

    local_ip = discovery.local_ip if discovery else "127.0.0.1"

    log.info("-" * 60)
    log.info(f"  局域网 IP:  {local_ip}")
    log.info(f"  WebSocket:  ws://{local_ip}:{args.ws_port}")
    log.info(f"  Web仪表盘:  http://{local_ip}:{args.http_port}")
    log.info(f"  系统状态:   每 {args.interval}s")
    log.info(f"  歌词同步:   每 {ws_server.lyrics_interval}s (智能增量同步)")
    log.info("-" * 60)
    log.info("  手机端连接方式:")
    log.info(f"    1. 浏览器打开 http://{local_ip}:{args.http_port}")
    log.info(f"    2. 或在 APP 中输入 {local_ip}:{args.ws_port}")
    log.info(f"    3. 或开启自动发现, APP 会自动找到本机")
    log.info("=" * 60)
    log.info("按 Ctrl+C 停止服务")

    try:
        asyncio.run(ws_server.start())
    except KeyboardInterrupt:
        log.info("正在停止服务...")
        if discovery:
            discovery.stop()
        log.info("服务已停止")


if __name__ == "__main__":
    main()
