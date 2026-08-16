"""
UDP Auto-Discovery Broadcast Module
"""
import time
import socket
import logging
from threading import Thread
import psutil

log = logging.getLogger("secdisp")


class DiscoveryBroadcaster:
    """通过 UDP 广播让手机端自动发现本服务器"""

    BROADCAST_PORT = 8888
    MAGIC = "SECDISP"

    def __init__(self, ws_port: int, hostname: str = None):
        self.ws_port = ws_port
        self.hostname = hostname or socket.gethostname()
        self.local_ip = self._get_local_ip()
        self._running = False
        self._sock = None

    def _get_local_ip(self) -> str:
        """获取本机局域网 IP — 优先返回私有 LAN 地址, 跳过 VPN/虚拟网卡"""
        import ipaddress

        skip_keywords = {"vpn", "virtual", "vmware", "virtualbox", "hyper-v",
                         "wsl", "docker", "vethernet", "tap", "tun", "loopback"}

        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            candidates = []

            for name, addr_list in addrs.items():
                name_lower = name.lower()
                if any(kw in name_lower for kw in skip_keywords):
                    continue
                if name in stats and not stats[name].isup:
                    continue
                for addr in addr_list:
                    if addr.family != socket.AF_INET:
                        continue
                    ip = addr.address
                    if ip.startswith("127."):
                        continue
                    try:
                        ip_obj = ipaddress.ip_address(ip)
                        if ip_obj.is_private:
                            candidates.append((name, ip))
                    except ValueError:
                        continue

            if candidates:
                for name, ip in candidates:
                    if ip.startswith("192.168."):
                        return ip
                for name, ip in candidates:
                    if ip.startswith("10."):
                        return ip
                return candidates[0][1]

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def start(self):
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        message = f"{self.MAGIC}:{self.local_ip}:{self.ws_port}:{self.hostname}"
        data = message.encode("utf-8")
        thread = Thread(target=self._broadcast_loop, args=(data,), daemon=True)
        thread.start()
        log.info(f"UDP 发现广播已启动 — IP:{self.local_ip} WS端口:{self.ws_port} 主机名:{self.hostname}")

    def _broadcast_loop(self, data: bytes):
        for _ in range(6):
            if not self._running:
                return
            try:
                self._sock.sendto(data, ("<broadcast>", self.BROADCAST_PORT))
            except Exception as e:
                log.debug(f"广播发送异常: {e}")
            time.sleep(0.3)

        while self._running:
            try:
                self._sock.sendto(data, ("<broadcast>", self.BROADCAST_PORT))
            except Exception as e:
                log.debug(f"广播发送异常: {e}")
                time.sleep(1.0)
                continue
            time.sleep(2.0)

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()
