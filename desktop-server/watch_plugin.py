"""
watch_plugin.py — Secondary Display 守护进程
============================================
功能:
1. 监视 secdisp-probe 插件文件 (plugins_runtime/main.js + plugins/*.plugin)
   一旦检测到修改时间变化, 自动重启网易云音乐 (BetterNCM 只在启动时加载插件)
   → 防止"插件已更新但网易云未重启导致功能不生效"
2. 服务器保活: 若 server.py 端口 (8080/8765) 无监听, 自动重新拉起
3. 日志写入 desktop-server/watch_plugin.log

启动: pythonw watch_plugin.py   (建议加入开机自启)
"""
import os
import sys
import time
import logging
import subprocess
import socket
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent

# 以下路径可通过环境变量覆盖 (适配不同安装位置), 默认值为本机开发环境:
#   SECDISP_NCM_EXE   → 网易云音乐可执行文件路径
#   SECDISP_PYTHON    → 用于运行 server.py 的 Python 解释器
#   SECDISP_BETTERNCM → BetterNCM 安装目录 (默认 C:/betterncm)
NCM_EXE = os.environ.get("SECDISP_NCM_EXE", r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe")
SERVER_PY = BASE_DIR / "desktop-server" / "server.py"
PYTHON_EXE = os.environ.get("SECDISP_PYTHON", r"C:\Python\python.exe")
BETTERNCM_DIR = Path(os.environ.get("SECDISP_BETTERNCM", r"C:\betterncm"))

# 监视的插件文件 (修改时间变化即触发)
WATCH_FILES = [
    BETTERNCM_DIR / "plugins_runtime" / "secdisp-probe" / "main.js",
    BETTERNCM_DIR / "plugins" / "secdisp-probe.plugin",
]

LOG_FILE = BASE_DIR / "desktop-server" / "watch_plugin.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("watch")


def is_port_listening(port: int) -> bool:
    """检查本地端口是否有监听"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def is_ncm_running() -> bool:
    """检查网易云是否在运行"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cloudmusic.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return "cloudmusic.exe" in out
    except Exception:
        return True  # 无法判断时按运行处理, 避免误杀


def restart_ncm(reason: str):
    """重启网易云音乐"""
    log.info(f"重启网易云音乐: {reason}")
    try:
        subprocess.run(["taskkill", "/F", "/T", "/IM", "cloudmusic.exe"],
                       capture_output=True, timeout=8)
    except Exception as e:
        log.warning(f"taskkill 失败: {e}")
    time.sleep(2)
    try:
        subprocess.Popen([NCM_EXE])
        log.info("网易云已重新启动")
    except Exception as e:
        log.error(f"启动网易云失败: {e}")


def start_server(reason: str):
    """重启桌面副屏服务器"""
    log.info(f"重启服务器: {reason}")
    try:
        subprocess.Popen(
            [PYTHON_EXE, str(SERVER_PY)],
            cwd=str(SERVER_PY.parent),
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        log.info("服务器启动指令已发出")
    except Exception as e:
        log.error(f"启动服务器失败: {e}")


def main():
    log.info("=" * 60)
    log.info("守护进程启动: 插件监视 + 服务器保活")
    log.info("=" * 60)

    # 记录当前插件状态
    plugin_snapshots = {}
    for f in WATCH_FILES:
        try:
            plugin_snapshots[str(f)] = f.stat().st_mtime
            log.info(f"监视插件: {f} (mtime={datetime.fromtimestamp(f.stat().st_mtime):%H:%M:%S})")
        except Exception:
            plugin_snapshots[str(f)] = None
            log.info(f"监视插件: {f} (不存在, 出现时将触发重启)")

    last_restart = 0
    server_last_ok = is_port_listening(8080)

    while True:
        try:
            # === 1. 插件文件监视 ===
            for f in WATCH_FILES:
                key = str(f)
                try:
                    mtime = f.stat().st_mtime
                except Exception:
                    mtime = None

                if plugin_snapshots.get(key) != mtime and mtime is not None:
                    old = plugin_snapshots.get(key)
                    plugin_snapshots[key] = mtime
                    log.info(f"检测到插件变化: {f} (old={old}, new={mtime})")
                    if is_ncm_running():
                        now = time.time()
                        if now - last_restart > 15:  # 防抖 15s
                            last_restart = now
                            restart_ncm(f"插件 {f.name} 已更新")
                        else:
                            log.info("15 秒内已重启过, 跳过")
                    else:
                        log.info("网易云未运行, 无需重启")

            # === 2. 服务器保活 ===
            server_ok = is_port_listening(8080)
            if server_ok != server_last_ok:
                log.info(f"服务器状态变化: {'在线' if server_ok else '离线'}")
                server_last_ok = server_ok
            if not server_ok:
                now = time.time()
                if now - last_restart > 15:
                    last_restart = now
                    start_server("服务器离线检测")
                    time.sleep(2)
        except Exception as e:
            log.error(f"守护循环异常: {e}")

        time.sleep(2)


if __name__ == "__main__":
    main()
