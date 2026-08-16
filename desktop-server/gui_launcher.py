"""
电脑副屏服务管理器 (Secondary Display Control Panel GUI)
提供可视化服务启停、实时手机扫码连接、网易云插件管理、开机自启配置与实时日志查看。
"""

import sys
import os
import time
import socket
import threading
import subprocess
import queue
import webbrowser
import ctypes
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

# DPI 适配 (避免高分屏模糊)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 项目路径
BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_PY = BASE_DIR / "desktop-server" / "server.py"
PLUGIN_SRC = BASE_DIR / "betterncm-plugins"
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", ""))
NCM_PLUGIN_DIR = LOCAL_APP_DATA / "Netease" / "CloudMusic" / "BetterNCM" / "plugins_runtime"
STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "SecondaryDisplay.lnk"
DESKTOP_DIR = Path(os.path.expanduser("~/Desktop"))

def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class AppGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("🖥️ 电脑副屏服务管理器 - Secondary Display")
        self.geometry("780 x 640".replace(" ", ""))
        self.minsize(720, 580)
        self.configure(bg="#0f172a")

        self.server_proc = None
        self.log_queue = queue.Queue()
        self.is_running = False
        self.local_ip = get_local_ip()
        self.http_port = 8080
        self.ws_port = 8765
        self.qr_photo = None
        self.tray_icon = None

        self._setup_styles()
        self._build_ui()
        self._check_initial_status()
        self._start_log_consumer()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TFrame", background="#0f172a")
        style.configure("Card.TFrame", background="#1e293b", relief="flat")
        style.configure("TLabel", background="#0f172a", foreground="#f8fafc", font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background="#1e293b", foreground="#f8fafc", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#0f172a", foreground="#38bdf8", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Subtitle.TLabel", background="#1e293b", foreground="#94a3b8", font=("Microsoft YaHei UI", 8))
        style.configure("Status.TLabel", background="#1e293b", font=("Microsoft YaHei UI", 11, "bold"))

        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=5)
        style.configure("Primary.TButton", background="#0284c7", foreground="#ffffff", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Success.TButton", background="#10b981", foreground="#ffffff", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Danger.TButton", background="#ef4444", foreground="#ffffff", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TCheckbutton", background="#1e293b", foreground="#f8fafc", font=("Microsoft YaHei UI", 9))

    def _build_ui(self):
        # 顶栏 Header
        top_frame = ttk.Frame(self, padding=(16, 12, 16, 6))
        top_frame.pack(fill=tk.X)

        title_lbl = ttk.Label(top_frame, text="🖥️ 电脑副屏服务管理器", style="Title.TLabel")
        title_lbl.pack(side=tk.LEFT)

        author_lbl = ttk.Label(top_frame, text="Secondary Display v2.2", foreground="#64748b", font=("Segoe UI", 9))
        author_lbl.pack(side=tk.RIGHT, pady=4)

        # 主体布局：左侧连接信息与二维码，右侧控制面板
        main_box = ttk.Frame(self, padding=(16, 6, 16, 6))
        main_box.pack(fill=tk.BOTH, expand=False)

        # 左侧：连接与二维码卡片
        left_card = ttk.Frame(main_box, style="Card.TFrame", padding=14)
        left_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        status_row = ttk.Frame(left_card, style="Card.TFrame")
        status_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(status_row, text="服务状态：", style="Card.TLabel").pack(side=tk.LEFT)
        self.status_lbl = ttk.Label(status_row, text="🔴 未运行", style="Status.TLabel", foreground="#ef4444")
        self.status_lbl.pack(side=tk.LEFT)

        self.url_var = tk.StringVar(value=f"http://{self.local_ip}:{self.http_port}")
        url_box = ttk.Frame(left_card, style="Card.TFrame")
        url_box.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(url_box, text="手机访问地址：", style="Subtitle.TLabel").pack(anchor="w")

        url_entry_row = ttk.Frame(url_box, style="Card.TFrame")
        url_entry_row.pack(fill=tk.X, pady=2)
        url_ent = tk.Entry(url_entry_row, textvariable=self.url_var, bg="#0f172a", fg="#38bdf8",
                           insertbackground="#ffffff", relief="flat", font=("Segoe UI", 10, "bold"))
        url_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3, padx=(0, 6))
        copy_btn = tk.Button(url_entry_row, text="复制", bg="#334155", fg="#f8fafc", relief="flat",
                             command=self.copy_url, font=("Microsoft YaHei UI", 8), cursor="hand2")
        copy_btn.pack(side=tk.RIGHT)

        # 二维码展示区
        qr_container = ttk.Frame(left_card, style="Card.TFrame")
        qr_container.pack(pady=4)
        self.qr_label = tk.Label(qr_container, bg="#1e293b")
        self.qr_label.pack()
        ttk.Label(left_card, text="📱 手机连接同一 WiFi 扫码直接进入", style="Subtitle.TLabel").pack(pady=(2, 0))

        # 右侧：功能控制卡片
        right_card = ttk.Frame(main_box, style="Card.TFrame", padding=14)
        right_card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        ttk.Label(right_card, text="服务控制", style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

        # 启动 / 停止 / 重启 按钮
        btn_grid = ttk.Frame(right_card, style="Card.TFrame")
        btn_grid.pack(fill=tk.X, pady=(0, 10))

        self.btn_start = tk.Button(btn_grid, text="▶ 启动服务", bg="#059669", fg="#ffffff", activebackground="#10b981",
                                   activeforeground="#ffffff", relief="flat", font=("Microsoft YaHei UI", 9, "bold"),
                                   cursor="hand2", command=self.start_server, padx=12, pady=6)
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_stop = tk.Button(btn_grid, text="■ 停止服务", bg="#dc2626", fg="#ffffff", activebackground="#ef4444",
                                  activeforeground="#ffffff", relief="flat", font=("Microsoft YaHei UI", 9, "bold"),
                                  cursor="hand2", command=self.stop_server, padx=12, pady=6)
        self.btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))

        self.btn_restart = tk.Button(btn_grid, text="🔄 重启", bg="#475569", fg="#ffffff", activebackground="#64748b",
                                     activeforeground="#ffffff", relief="flat", font=("Microsoft YaHei UI", 9),
                                     cursor="hand2", command=self.restart_server, padx=8, pady=6)
        self.btn_restart.pack(side=tk.RIGHT, padx=(4, 0))

        # 快速操作
        ttk.Label(right_card, text="快捷功能", style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(6, 6))

        action_box = ttk.Frame(right_card, style="Card.TFrame")
        action_box.pack(fill=tk.X)

        open_browser_btn = tk.Button(action_box, text="🌐 PC 浏览器打开副屏", bg="#1e3a8a", fg="#bfdbfe", relief="flat",
                                     cursor="hand2", command=self.open_browser, pady=4)
        open_browser_btn.pack(fill=tk.X, pady=2)

        ota_btn = tk.Button(action_box, text="⚡ 广播 OTA 热重载 (刷新副屏)", bg="#334155", fg="#f8fafc", relief="flat",
                            cursor="hand2", command=self.broadcast_ota, pady=4)
        ota_btn.pack(fill=tk.X, pady=2)

        plugin_btn = tk.Button(action_box, text="🎵 一键修复/部署网易云插件", bg="#334155", fg="#f8fafc", relief="flat",
                               cursor="hand2", command=self.repair_plugin, pady=4)
        plugin_btn.pack(fill=tk.X, pady=2)

        # 开机自启复选框
        self.autostart_var = tk.BooleanVar(value=STARTUP_LNK.exists())
        autostart_cb = tk.Checkbutton(right_card, text="开机自动后台静默启动服务", variable=self.autostart_var,
                                      command=self.toggle_autostart, bg="#1e293b", fg="#f8fafc",
                                      selectcolor="#0f172a", activebackground="#1e293b", activeforeground="#f8fafc")
        autostart_cb.pack(anchor="w", pady=(8, 0))

        # 下方：日志输出终端
        log_frame = ttk.Frame(self, padding=(16, 6, 16, 12))
        log_frame.pack(fill=tk.BOTH, expand=True)

        log_head = ttk.Frame(log_frame)
        log_head.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(log_head, text="📜 运行实时日志", font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.LEFT)

        clear_btn = tk.Button(log_head, text="清空日志", bg="#1e293b", fg="#94a3b8", relief="flat",
                              command=self.clear_logs, font=("Microsoft YaHei UI", 8), cursor="hand2")
        clear_btn.pack(side=tk.RIGHT)

        self.log_text = tk.Text(log_frame, bg="#020617", fg="#cbd5e1", insertbackground="#38bdf8",
                                font=("Consolas", 9), relief="flat", wrap=tk.WORD, height=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 颜色标签
        self.log_text.tag_config("INFO", foreground="#38bdf8")
        self.log_text.tag_config("WARN", foreground="#facc15")
        self.log_text.tag_config("ERROR", foreground="#f87171")
        self.log_text.tag_config("SUCCESS", foreground="#4ade80")
        self.log_text.tag_config("INFLINK", foreground="#c084fc")

        self.render_qr()

    def render_qr(self):
        """生成并展示二维码"""
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=3, border=2)
            qr.add_data(self.url_var.get())
            qr.make(fit=True)
            img = qr.make_image(fill_color="#38bdf8", back_color="#1e293b")
            self.qr_photo = ImageTk.PhotoImage(img)
            self.qr_label.configure(image=self.qr_photo)
        except Exception as e:
            self.qr_label.configure(text="[二维码生成异常]")

    def log(self, text, tag="INFO"):
        self.log_queue.put((text, tag))

    def _start_log_consumer(self):
        def consume():
            try:
                while True:
                    text, tag = self.log_queue.get_nowait()
                    self.log_text.insert(tk.END, text + "\n", tag)
                    self.log_text.see(tk.END)
            except queue.Empty:
                pass
            self.after(80, self._start_log_consumer)
        self.after(80, consume)

    def clear_logs(self):
        self.log_text.delete("1.0", tk.END)

    def _check_initial_status(self):
        """检查后台是否已有运行中的 server.py"""
        running = self._is_server_process_running()
        if running:
            self.is_running = True
            self.status_lbl.configure(text="🟢 运行中", foreground="#10b981")
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.NORMAL)
            self.log("[系统] 检测到后台副屏服务已在运行中", "SUCCESS")
        else:
            self.start_server()

    def _is_server_process_running(self):
        try:
            import psutil
            for p in psutil.process_iter(["pid", "cmdline"]):
                cmd = p.info.get("cmdline") or []
                if any("server.py" in str(arg) for arg in cmd):
                    return True
        except Exception:
            pass
        return False

    def start_server(self):
        """启动服务端"""
        if self._is_server_process_running():
            self.is_running = True
            self.status_lbl.configure(text="🟢 运行中", foreground="#10b981")
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.NORMAL)
            self.log("[系统] 服务已在运行中", "INFO")
            return

        self.log("[系统] 正在启动副屏服务...", "INFO")

        def run_thread():
            try:
                self.server_proc = subprocess.Popen(
                    [sys.executable, str(SERVER_PY)],
                    cwd=str(BASE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                self.is_running = True
                self.after(0, lambda: self.status_lbl.configure(text="🟢 运行中", foreground="#10b981"))
                self.after(0, lambda: self.btn_start.configure(state=tk.DISABLED))
                self.after(0, lambda: self.btn_stop.configure(state=tk.NORMAL))
                self.log(f"[服务] 副屏已成功启动 (Web: http://{self.local_ip}:{self.http_port})", "SUCCESS")

                for line in self.server_proc.stdout:
                    line = line.strip()
                    tag = "INFO"
                    if "ERROR" in line:
                        tag = "ERROR"
                    elif "WARN" in line:
                        tag = "WARN"
                    elif "INFLINK" in line:
                        tag = "INFLINK"
                    elif "成功" in line or "OK" in line:
                        tag = "SUCCESS"
                    self.log(line, tag)

            except Exception as e:
                self.log(f"[异常] 启动失败: {e}", "ERROR")
            finally:
                self.is_running = False
                self.after(0, lambda: self.status_lbl.configure(text="🔴 已停止", foreground="#ef4444"))
                self.after(0, lambda: self.btn_start.configure(state=tk.NORMAL))
                self.after(0, lambda: self.btn_stop.configure(state=tk.DISABLED))

        threading.Thread(target=run_thread, daemon=True).start()

    def stop_server(self):
        """停止服务端"""
        self.log("[系统] 正在安全停止副屏服务...", "WARN")
        try:
            import psutil
            killed = 0
            for p in psutil.process_iter(["pid", "cmdline"]):
                cmd = p.info.get("cmdline") or []
                if any("server.py" in str(arg) for arg in cmd):
                    p.kill()
                    killed += 1
            if self.server_proc:
                self.server_proc.kill()
                self.server_proc = None
            self.is_running = False
            self.status_lbl.configure(text="🔴 已停止", foreground="#ef4444")
            self.btn_start.configure(state=tk.NORMAL)
            self.btn_stop.configure(state=tk.DISABLED)
            self.log(f"[系统] 服务已安全停止 (终止进程数: {killed})", "SUCCESS")
        except Exception as e:
            self.log(f"[异常] 停止服务出错: {e}", "ERROR")

    def restart_server(self):
        self.stop_server()
        self.after(800, self.start_server)

    def copy_url(self):
        self.clipboard_clear()
        self.clipboard_append(self.url_var.get())
        messagebox.showinfo("提示", f"已复制访问链接到剪贴板：\n{self.url_var.get()}")

    def open_browser(self):
        webbrowser.open(f"http://127.0.0.1:{self.http_port}")

    def broadcast_ota(self):
        def task():
            try:
                import urllib.request
                urllib.request.urlopen(f"http://127.0.0.1:{self.http_port}/api/ota", timeout=3)
                self.log("[OTA] 已成功向所有连接的手机副屏广播热重载！", "SUCCESS")
            except Exception as e:
                self.log(f"[OTA] 广播失败 (服务未运行?): {e}", "ERROR")
        threading.Thread(target=task, daemon=True).start()

    def repair_plugin(self):
        try:
            NCM_PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
            import shutil
            for p in PLUGIN_SRC.glob("*.plugin"):
                shutil.copy(p, NCM_PLUGIN_DIR / p.name)
            self.log(f"[插件] 已成功部署/恢复网易云 BetterNCM 插件到: {NCM_PLUGIN_DIR}", "SUCCESS")
            messagebox.showinfo("成功", "网易云音乐 BetterNCM 插件已成功部署！\n重启网易云音乐客户端即可生效。")
        except Exception as e:
            self.log(f"[插件] 部署异常: {e}", "ERROR")
            messagebox.showerror("错误", f"部署插件失败: {e}")

    def toggle_autostart(self):
        try:
            import win32com.client
            ws = win32com.client.Dispatch("WScript.Shell")
            if self.autostart_var.get():
                vbs_path = BASE_DIR / "后台静默启动(推荐).vbs"
                shortcut = ws.CreateShortcut(str(STARTUP_LNK))
                shortcut.TargetPath = "wscript.exe"
                shortcut.Arguments = f'"{vbs_path}"'
                shortcut.WorkingDirectory = str(BASE_DIR)
                shortcut.Description = "电脑副屏后台静默服务"
                shortcut.Save()
                self.log("[设置] 已开启 Windows 开机自启动", "SUCCESS")
            else:
                if STARTUP_LNK.exists():
                    STARTUP_LNK.unlink()
                self.log("[设置] 已关闭开机自启动", "WARN")
        except Exception as e:
            self.log(f"[设置] 开机自启配置失败: {e}", "ERROR")

    def on_close(self):
        # 退出 GUI 管理器时保持后台服务运行（或提示用户）
        self.destroy()


if __name__ == "__main__":
    app = AppGUI()
    app.mainloop()
