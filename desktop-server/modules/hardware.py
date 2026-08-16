"""
Hardware Info & Power Reading Module
"""
import time
import json
import urllib.request
from threading import Thread

# 检测显示器当前刷新率
try:
    import win32api
    import win32con
    _devmode = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
    BASE_DISPLAY_REFRESH = float(_devmode.DisplayFrequency or 60.0)
except Exception:
    BASE_DISPLAY_REFRESH = 60.0


class HardwareInfo:
    """采集 CPU/GPU 品牌型号 (WMI), 用于前端显示厂商 logo 与功耗估算"""

    def __init__(self):
        self.cpu_name = ""
        self.cpu_brand = "unknown"
        self.cpu_cores = 0
        self.cpu_threads = 0
        self.cpu_tdp = 65.0
        self.gpu_name = ""
        self.gpu_brand = "unknown"
        self.gpu_tdp = 150.0
        self._cached_gpu_usage = 0.0
        self._gpu_freq_mhz = None      # LHM GPU Core Clock (未装 LHM 则为 None)
        self._gpu_temp_c = None        # LHM GPU Temperature
        self._lhm_gpu_found = False
        self._detect()
        Thread(target=self._gpu_loop, daemon=True).start()

    def _detect(self):
        try:
            import win32com.client
        except ImportError:
            return
        try:
            c = win32com.client.GetObject("winmgmts:")
            for cpu in c.ExecQuery("SELECT * FROM Win32_Processor"):
                self.cpu_name = (cpu.Name or "").strip()
                self.cpu_cores = int(cpu.NumberOfCores or 0)
                self.cpu_threads = int(cpu.NumberOfLogicalProcessors or 0)
                break
            skip = ["virtual", "mumu", "gameviewer", "basic", "display adapter", "microsoft", "远程"]
            for gpu in c.ExecQuery("SELECT * FROM Win32_VideoController"):
                name = (gpu.Name or "").strip()
                low = name.lower()
                if any(k in low for k in skip):
                    continue
                self.gpu_name = name
                break
        except Exception:
            pass

        low_cpu = self.cpu_name.lower()
        if "amd" in low_cpu:
            self.cpu_brand = "amd"
            if "ryzen 9" in low_cpu or "r9" in low_cpu:
                self.cpu_tdp = 170
            elif "ryzen 7" in low_cpu or "r7" in low_cpu:
                self.cpu_tdp = 105
            elif "threadripper" in low_cpu:
                self.cpu_tdp = 280
            else:
                self.cpu_tdp = 65
        elif "intel" in low_cpu:
            self.cpu_brand = "intel"
            if "i9" in low_cpu:
                self.cpu_tdp = 125
            elif "i7" in low_cpu:
                self.cpu_tdp = 125
            elif "i5" in low_cpu:
                self.cpu_tdp = 125
            elif "ultra" in low_cpu:
                self.cpu_tdp = 125
            else:
                self.cpu_tdp = 65

        low_gpu = self.gpu_name.lower()
        if "nvidia" in low_gpu:
            self.gpu_brand = "nvidia"
            self.gpu_tdp = 250
        elif "radeon" in low_gpu or "amd" in low_gpu:
            self.gpu_brand = "amd"
            self.gpu_tdp = 230
        elif "intel" in low_gpu:
            self.gpu_brand = "intel"
            self.gpu_tdp = 150

    def _gpu_loop(self):
        """后台异步采样 GPU 性能计数器，避免每次广播阻塞 asyncio 循环"""
        lhm_try = 0
        while True:
            try:
                import win32com.client
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    c = win32com.client.GetObject("winmgmts:")
                    rows = c.ExecQuery("SELECT * FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine")
                    mx = 0
                    for r in rows:
                        try:
                            mx = max(mx, int(r.UtilizationPercentage))
                        except Exception:
                            pass
                    self._cached_gpu_usage = float(mx) if mx > 0 else 0.0
                finally:
                    pythoncom.CoUninitialize()
            except Exception:
                pass

            # LHM 频率/温度探测 (5s 节流; 未装 LHM 时每次探测间隔拉长, 减少无效请求)
            now = time.time()
            if now - lhm_try >= 5.0:
                lhm_try = now
                self._probe_lhm()

            time.sleep(1.0)

    def _probe_lhm(self):
        """从 LibreHardwareMonitor HTTP API 读取 GPU 核心频率与温度。
        未安装/未开启 LHM 时静默降级 (freq/temp 保持 None, 前端显示 —)。"""
        try:
            import json
            with urllib.request.urlopen("http://127.0.0.1:8085/data.json", timeout=2) as r:
                data = json.load(r)
            freq = temp = None
            for hw in data.get("Hardware", []):
                if not str(hw.get("HardwareType", "")).startswith("Gpu"):
                    continue
                for s in hw.get("Sensors", []):
                    n = s.get("Name", "")
                    v = s.get("Value")
                    if v is None:
                        continue
                    try:
                        if "GPU Core Clock" in n and freq is None:
                            freq = round(float(v), 0)
                        elif "GPU Temperature" in n and temp is None:
                            temp = round(float(v), 1)
                    except Exception:
                        continue
            self._gpu_freq_mhz = freq
            self._gpu_temp_c = temp
            self._lhm_gpu_found = freq is not None or temp is not None
        except Exception:
            # LHM 不可达: 仅当之前可用时才重复探测, 否则延长探测间隔
            self._gpu_freq_mhz = None
            self._gpu_temp_c = None

    def gpu_usage(self):
        """GPU 使用率: 极速读取后台线程缓存值"""
        return self._cached_gpu_usage

    def gpu_extra(self) -> dict:
        """GPU 扩展信息: 频率 (MHz) / 温度 (°C), 来自 LHM (未装则为 None)"""
        return {
            "freq_mhz": self._gpu_freq_mhz,
            "temp_c": self._gpu_temp_c,
            "lhm": self._lhm_gpu_found,
        }

    def info(self) -> dict:
        return {
            "cpu": {"name": self.cpu_name, "brand": self.cpu_brand,
                    "cores": self.cpu_cores, "threads": self.cpu_threads,
                    "tdp_w": self.cpu_tdp},
            "gpu": {"name": self.gpu_name, "brand": self.gpu_brand, "tdp_w": self.gpu_tdp},
        }


class PowerReader:
    """功耗读取: 优先 LibreHardwareMonitor HTTP, 否则按使用率估算"""

    def __init__(self, lhm_url="http://127.0.0.1:8085/data.json"):
        self.lhm_url = lhm_url
        self._last_data = None
        self._last_try = 0

    def _lhm(self):
        if self._last_data is not None or (time.time() - self._last_try) < 10:
            return self._last_data
        self._last_try = time.time()
        try:
            with urllib.request.urlopen(self.lhm_url, timeout=2) as r:
                self._last_data = json.load(r)
            return self._last_data
        except Exception:
            self._last_data = None
            return None

    def _find_sensor(self, data, keys):
        for hw in data.get("Hardware", []):
            for s in hw.get("Sensors", []):
                n = s.get("Name", "")
                if any(k in n for k in keys) and s.get("Value") is not None:
                    try:
                        return round(float(s["Value"]), 1)
                    except Exception:
                        return None
        return None

    def read(self, cpu_percent, gpu_percent, hw: HardwareInfo) -> dict:
        data = self._lhm()
        if data:
            cpu_w = self._find_sensor(data, ["CPU Package Power", "CPU PPT", "CPU Cores Power"])
            gpu_w = self._find_sensor(data, ["GPU Power", "GPU Core Power"])
            if cpu_w is not None or gpu_w is not None:
                return {"cpu_w": cpu_w, "gpu_w": gpu_w, "source": "lhm"}
        return {
            "cpu_w": round((cpu_percent or 0) / 100 * hw.cpu_tdp, 1),
            "gpu_w": round((gpu_percent or 0) / 100 * hw.gpu_tdp, 1),
            "source": "est",
        }
