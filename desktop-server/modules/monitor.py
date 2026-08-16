"""
System Resource Monitoring Module
"""
import os
import time
import psutil
from .hardware import HardwareInfo, PowerReader
from .app_watcher import ForegroundAppWatcher


class SystemMonitor:
    """使用 psutil 采集系统资源状态与硬件性能"""

    def __init__(self):
        self._prev_net = psutil.net_io_counters()
        self._prev_time = time.time()
        self._cpu_freq = None
        try:
            self._cpu_freq = psutil.cpu_freq()
        except Exception:
            pass
        psutil.cpu_percent(percpu=True)
        # 硬件信息 + 功耗 + 前台应用
        self.hardware = HardwareInfo()
        self.power = PowerReader()
        self.app_watcher = ForegroundAppWatcher()
        self._perf_history = {"cpu": [], "ram": [], "gpu": []}  # 最近 60 个采样点

    def collect(self) -> dict:
        now = time.time()
        dt = max(now - self._prev_time, 0.001)

        cpu_percent = psutil.cpu_percent(interval=None, percpu=True)
        cpu_overall = sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0
        try:
            freq = psutil.cpu_freq()
        except Exception:
            freq = None

        mem = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage("/")
        except Exception:
            disk = None

        net = psutil.net_io_counters()
        sent_rate = (net.bytes_sent - self._prev_net.bytes_sent) / dt
        recv_rate = (net.bytes_recv - self._prev_net.bytes_recv) / dt
        self._prev_net = net
        self._prev_time = now

        battery = None
        try:
            bat = psutil.sensors_battery()
            if bat:
                battery = {"percent": bat.percent, "plugged": bat.power_plugged}
        except Exception:
            pass

        temps = []
        try:
            temp_data = psutil.sensors_temperatures()
            for name, entries in temp_data.items():
                for entry in entries[:3]:
                    temps.append({"label": entry.label or name, "current": entry.current})
        except Exception:
            pass

        load_avg = None
        try:
            load_avg = list(os.getloadavg())
        except Exception:
            pass

        proc_count = 0
        try:
            proc_count = len(psutil.pids())
        except Exception:
            pass

        # GPU 使用率 + 功耗 + 历史数据
        gpu_usage = self.hardware.gpu_usage()
        power = self.power.read(cpu_overall, gpu_usage, self.hardware)
        h = self._perf_history
        h["cpu"].append(round(cpu_overall, 1))
        h["ram"].append(round(mem.percent, 1))
        h["gpu"].append(gpu_usage if gpu_usage is not None else 0)
        if len(h["cpu"]) > 60:
            h["cpu"] = h["cpu"][-60:]
            h["ram"] = h["ram"][-60:]
            h["gpu"] = h["gpu"][-60:]

        return {
            "app": self.app_watcher.get_foreground(gpu_usage if gpu_usage is not None else 0),
            "hardware": self.hardware.info(),
            "power": power,
            "perf_history": h,
            "cpu": {
                "overall": round(cpu_overall, 1),
                "per_core": [round(x, 1) for x in cpu_percent],
                "core_count": len(cpu_percent),
                "freq_mhz": round(freq.current, 0) if freq else None,
                "freq_max": round(freq.max, 0) if freq else None,
            },
            "gpu": {
                "percent": round(gpu_usage, 1) if gpu_usage is not None else 0,
                "name": self.hardware.info().get("gpu", {}).get("name", ""),
                "brand": self.hardware.info().get("gpu", {}).get("brand", "intel"),
                "freq_mhz": self.hardware.gpu_extra().get("freq_mhz"),
                "temp_c": self.hardware.gpu_extra().get("temp_c"),
                "lhm": self.hardware.gpu_extra().get("lhm", False),
            },
            "memory": {
                "total_gb": round(mem.total / 1073741824, 2),
                "used_gb": round((mem.total - mem.available) / 1073741824, 2),
                "available_gb": round(mem.available / 1073741824, 2),
                "percent": round(mem.percent, 1),
            },
            "disk": {
                "total_gb": round(disk.total / 1073741824, 2) if disk else None,
                "used_gb": round(disk.used / 1073741824, 2) if disk else None,
                "free_gb": round(disk.free / 1073741824, 2) if disk else None,
                "percent": round(disk.percent, 1) if disk else None,
            },
            "network": {
                "sent_rate_kbps": round(sent_rate * 8 / 1024, 2),
                "recv_rate_kbps": round(recv_rate * 8 / 1024, 2),
                "total_sent_mb": round(net.bytes_sent / 1048576, 2),
                "total_recv_mb": round(net.bytes_recv / 1048576, 2),
            },
            "battery": battery,
            "temperature": temps[:6] if temps else [],
            "load_avg": load_avg,
            "process_count": proc_count,
            "uptime_seconds": int(time.time() - psutil.boot_time()),
        }
