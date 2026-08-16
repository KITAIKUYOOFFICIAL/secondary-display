"""前台窗口探测器 — 独立进程运行 (强制 UTF-8 编码输出，杜绝中文乱码)
支持两种模式:
1. 常驻守护模式 (`--loop`): 作为长连接常驻子进程，以 10Hz 频率实时输出 UTF-8 JSON 行到 stdout
2. 单次探测模式 (默认/无参数): 探测一次输出 UTF-8 JSON 后退出
"""
import sys
import os
import io
import time
import json
import base64
import ctypes

# ★ 强制标准流为 UTF-8 编码，彻底解决 Windows 管道中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.windll.user32

try:
    import win32con
except Exception:
    win32con = None

import win32gui
import win32process
import psutil

SYSTEM_EXCLUDE = {
    "textinputhost", "applicationframehost", "systemsettings",
    "searchhost", "startmenuexperiencehost", "lockapp",
    "shellexperiencehost", "explorer", "dwm", "csrss", "svchost",
    "gamepp", "gameviewerserver", "gameviewerservice",
    "gameinputredistservice", "gameviewerhealthd"
}

ICON_CACHE = {}


def attach_desktop():
    """挂载到当前活动的输入桌面"""
    try:
        flags = (win32con.DESKTOP_SWITCHDESKTOP | 0x10000000) if win32con else 0x10000100
        hdesk = user32.OpenInputDesktop(0, False, flags)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
            return True
    except Exception:
        pass
    return False


def get_window_title_w(hwnd) -> str:
    """使用 Windows 原生 Unicode (UTF-16) API 获取窗口完整标题"""
    try:
        buf = ctypes.create_unicode_buffer(512)
        length = user32.GetWindowTextW(hwnd, buf, 512)
        if length > 0:
            return buf.value.strip()
    except Exception:
        pass
    return ""


def get_window_class_w(hwnd) -> str:
    """使用 Windows 原生 Unicode (UTF-16) API 获取窗口类名"""
    try:
        buf = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, buf, 256)
        if length > 0:
            return buf.value.strip()
    except Exception:
        pass
    return ""


def extract_icon(exe_path: str) -> str:
    """提取 EXE 图标并转为 base64 PNG"""
    if not exe_path or not os.path.exists(exe_path):
        return ""
    if exe_path in ICON_CACHE:
        return ICON_CACHE[exe_path]

    try:
        from ctypes import wintypes

        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [
                ("hIcon", wintypes.HICON),
                ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260),
                ("szTypeName", wintypes.WCHAR * 80)
            ]

        shfileinfo = SHFILEINFOW()
        res = ctypes.windll.shell32.SHGetFileInfoW(
            exe_path, 0, ctypes.byref(shfileinfo), ctypes.sizeof(shfileinfo),
            0x000000100  # SHGFI_ICON | SHGFI_LARGEICON
        )
        if res and shfileinfo.hIcon:
            import win32ui
            from PIL import Image
            hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
            hbmp = win32ui.CreateBitmap()
            hbmp.CreateCompatibleBitmap(hdc, 32, 32)
            hdc_mem = hdc.CreateCompatibleDC()
            hdc_mem.SelectObject(hbmp)
            win32gui.DrawIconEx(hdc_mem.GetHandleOutput(), 0, 0, shfileinfo.hIcon, 32, 32, 0, None, 3)
            bmpinfo = hbmp.GetInfo()
            bmpstr = hbmp.GetBitmapBits(True)
            img = Image.frombuffer("RGBA", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRA", 0, 1)
            win32gui.DestroyIcon(shfileinfo.hIcon)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            ICON_CACHE[exe_path] = b64
            return b64
    except Exception:
        pass
    return ""


def detect_once(with_icon: bool = True) -> dict:
    """执行一次前台窗口探测 (纯 Unicode 保证中文无乱码)"""
    attach_desktop()

    result = {"name": "Windows 桌面", "title": "桌面", "pid": 0, "exe": "", "icon": ""}
    target_pid = 0
    target_name = ""
    target_title = ""
    target_exe = ""

    # 1. 优先 GetForegroundWindow (带 Unicode 原生解码)
    hwnd = user32.GetForegroundWindow()
    if hwnd and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
        t = get_window_title_w(hwnd)
        c = get_window_class_w(hwnd)
        if c not in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Button") and t != "Program Manager":
            try:
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                p = pid.value
                if p > 0:
                    proc = psutil.Process(p)
                    pname = proc.name().lower().replace(".exe", "")
                    if pname not in SYSTEM_EXCLUDE:
                        target_pid = p
                        target_name = proc.name().replace(".exe", "")
                        target_title = t or target_name
                        target_exe = proc.exe()
            except Exception:
                pass

    # 2. Z-Order 顶层遍历兜底
    if target_pid <= 0:
        candidates = []
        def cb(hwnd_cur, _):
            if user32.IsWindowVisible(hwnd_cur) and not user32.IsIconic(hwnd_cur):
                t_cur = get_window_title_w(hwnd_cur)
                c_cur = get_window_class_w(hwnd_cur)
                if c_cur not in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Button") and t_cur and t_cur != "Program Manager":
                    rect = win32gui.GetWindowRect(hwnd_cur)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if w > 200 and h > 200:
                        try:
                            pid_cur = ctypes.c_ulong()
                            user32.GetWindowThreadProcessId(hwnd_cur, ctypes.byref(pid_cur))
                            p_cur = pid_cur.value
                            if p_cur > 0:
                                proc_cur = psutil.Process(p_cur)
                                pname_cur = proc_cur.name().lower().replace(".exe", "")
                                if pname_cur not in SYSTEM_EXCLUDE:
                                    candidates.append({
                                        "pid": p_cur,
                                        "name": proc_cur.name().replace(".exe", ""),
                                        "title": t_cur,
                                        "exe": proc_cur.exe()
                                    })
                        except Exception:
                            pass
            return True

        try:
            win32gui.EnumWindows(cb, None)
            if candidates:
                top = candidates[0]
                target_pid = top["pid"]
                target_name = top["name"]
                target_title = top["title"]
                target_exe = top["exe"]
        except Exception:
            pass

    if target_name:
        result["pid"] = target_pid
        result["name"] = target_name
        result["title"] = target_title or target_name
        result["exe"] = target_exe
        if with_icon and target_exe:
            result["icon"] = extract_icon(target_exe)

    return result


def main():
    if "--loop" in sys.argv:
        last_pid = -1
        cached_icon = ""
        while True:
            try:
                data = detect_once(with_icon=False)
                current_pid = data.get("pid", 0)
                current_exe = data.get("exe", "")

                if current_pid != last_pid:
                    cached_icon = extract_icon(current_exe) if current_exe else ""
                    last_pid = current_pid

                data["icon"] = cached_icon
                sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except Exception:
                pass
            time.sleep(0.1)  # 10Hz
    else:
        with_icon = "--icon" in sys.argv or "-i" in sys.argv
        data = detect_once(with_icon=with_icon)
        sys.stdout.write(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
