# 桌面副屏 (Secondary Display)

将 WiFi 手机变为台式电脑的局域网横屏副屏，实时显示 CPU / 内存 / 网络 / 磁盘等系统状态，自动识别网易云音乐等播放器并同步滚动歌词。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                    台式电脑 (有线网络)                 │
│                                                       │
│  ┌──────────────┐    ┌──────────────────────────┐   │
│  │  psutil      │───▶│  Python Server            │   │
│  │  系统状态采集  │    │                            │   │
│  └──────────────┘    │  • WebSocket :8765 (推送)  │   │
│                      │  • HTTP      :8080 (仪表盘)│   │
│  ┌──────────────┐    │  • UDP       :8888 (发现)  │   │
│  │  歌词/通知    │───▶│                            │   │
│  └──────────────┘    └──────────┬───────────────────┘   │
│                                 │                       │
└─────────────────────────────────┼───────────────────────┘
                                  │ 局域网 (LAN)
                                  │
┌─────────────────────────────────┼───────────────────────┐
│                    手机 (WiFi)   │                       │
│                                 ▼                       │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Android APP / Web 浏览器                          │   │
│  │                                                    │   │
│  │  • WebSocket 接收实时数据                          │   │
│  │  • UDP + HTTP 双通道自动发现服务器                 │   │
│  │  • 断线持久重连 (已修复反复断开)                   │   │
│  │  • 深色仪表盘 UI                                   │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

## 目录结构

```
secondary-display/
├── desktop-server/              # Python 桌面端服务
│   ├── server.py                # 主服务 (WebSocket + HTTP + UDP)
│   ├── media_watcher.ps1         # PowerShell SMTC 媒体检测 (精确进度)
│   ├── requirements.txt         # Python 依赖
│   └── web/
│       └── index.html           # Web 仪表盘 (PWA)
│
├── android-app/                 # Android Studio 项目
│   ├── app/
│   │   ├── build.gradle         # 模块构建配置
│   │   └── src/main/
│   │       ├── AndroidManifest.xml
│   │       ├── java/com/secondarydisplay/app/
│   │       │   ├── MainActivity.kt      # WebView 容器
│   │       │   ├── AndroidBridge.kt     # JS 桥接
│   │       │   └── DiscoveryService.kt  # UDP 广播 + HTTP 局域网扫描 双通道发现
│   │       ├── assets/web/index.html    # 仪表盘 UI (与 server 共用)
│   │       └── res/                     # 资源文件
│   ├── build.gradle             # 项目级构建配置
│   ├── settings.gradle
│   ├── gradlew / gradlew.bat    # Gradle Wrapper
│   └── local.properties         # SDK 路径
│
├── secondary-display-debug.apk  # 已构建的 debug APK
└── README.md                    # 本文件
```

## 快速开始

### 1. 启动桌面服务

```bash
cd desktop-server
pip install -r requirements.txt
python server.py
```

启动后终端会显示:
```
  局域网 IP:  192.168.1.100
  WebSocket:  ws://192.168.1.100:8765
  Web仪表盘:  http://192.168.1.100:8080
```

### 2. 手机端连接 (三种方式)

**方式 A — 安装 APK (推荐)**
1. 将 `secondary-display-debug.apk` 传到手机
2. 安装 (需允许"未知来源应用")
3. 打开 APP，点击"自动发现"或手动输入电脑 IP:端口
4. 点击"连接"

**方式 B — 浏览器直接打开**
1. 手机浏览器访问 `http://<电脑IP>:8080`
2. 无需安装任何东西

**方式 C — PWA 安装**
1. 浏览器打开后，添加到主屏幕
2. 可像原生 APP 一样全屏使用

### 3. 使用

连接成功后，手机屏幕将实时显示:
- **横屏沉浸式布局**: 左右分栏，专为副屏场景优化
- CPU 使用率 (总览 + 每核心)
- **GPU 使用率 / 核心频率 / 核心温度** (占用率内置读取；频率与温度需安装 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) 并开启其 Remote Web Server，见故障排除)
- 内存 / 磁盘使用率 (环形进度)
- 网络上传/下载速率 (实时折线图)
- 系统运行时间、进程数、负载、电池、温度
- **歌词自动识别**: 检测网易云音乐 / QQ音乐 / 酷狗 / Spotify 等播放器窗口
- **歌词滚动效果**: 解析 LRC 时间轴，按播放进度高亮当前行并平滑滚动
- 手动歌词推送 (兼容纯文本和 LRC 格式)
- **手机音量键遥控 PC 音量**: 在 APP 内按手机物理音量键 +/− 直接调节电脑系统音量 (长按连续调节, 手机自身音量不变)

## 通信协议

### 自动发现 (UDP 广播 + HTTP 局域网扫描 双通道)

自动发现采用**双通道**，任一通道命中即视为发现服务器，对小米/MIUI 等 UDP 广播受限的设备更可靠:

**通道 1 — UDP 广播监听 (被动):**
服务器启动时瞬间多发 6 包，之后每 2 秒广播:
```
SECDISP:<ip>:<ws_port>:<hostname>
```
手机端原生监听 UDP 8888 端口，收到广播后自动填充连接信息。监听器已做健壮化 (异常后重建 socket 而非退出)。

**通道 2 — HTTP 局域网扫描 (主动, 主通道):**
手机端读取自身 WiFi 网段，向该网段各主机的 `:8080/api/info` (端口回退 8000/80) 发送 HTTP 请求，命中返回 `ws_port` 即视为发现。该方式不依赖 Android 的 UDP 广播能力，是小米等设备的可靠发现路径。

> 服务端 `/api/info` (及等价的 `/discovery`) 返回:
> `{"hostname","platform","ws_port","http_port","ip","magic":"SECDISP"}`

### WebSocket 数据格式

**服务器 → 手机:**

```jsonc
// 连接成功
{ "type": "connected", "data": { "hostname": "...", "platform": "..." } }

// 系统状态 (每秒推送)
{ "type": "stats", "data": {
    "cpu": { "overall": 45.2, "per_core": [...], "core_count": 12, "freq_mhz": 3200 },
    "memory": { "total_gb": 48, "used_gb": 19, "percent": 39.5 },
    "disk": { "total_gb": 952, "used_gb": 679, "percent": 71.3 },
    "network": { "sent_rate_kbps": 430, "recv_rate_kbps": 120 },
    // ... battery, temperature, load_avg, uptime 等
}}

// 歌词 (200ms 高频推送)
{
  "type": "lyrics",
  "data": {
    "song": { "title": "夜空中最亮的星", "artist": "逃跑计划", "album": "", "duration": 252 },
    "lines": [
      { "time": 0.0, "text": "夜空中最亮的星 能否听清" },
      { "time": 5.0, "text": "那仰望的人 心底的孤独和叹息" }
    ],
    "current_index": 0,
    "current_time": 2.35,
    "progress": 0.09,
    "current": "夜空中最亮的星 能否听清",
    "next": "那仰望的人 心底的孤独和叹息"
  }
}

// 通知
{ "type": "notifications", "data": [{ "title": "...", "body": "..." }] }
```

**手机 → 服务器 (控制指令):**

```jsonc
{ "type": "lyric_next" }              // 下一句歌词
{ "type": "lyric_prev" }              // 上一句歌词
{ "type": "lyric_seek", "data": { "index": 5 } }  // 跳转歌词
{ "type": "set_lyrics", "data": { "text": "歌词文本" } }  // 设置歌词
{ "type": "set_interval", "data": { "interval": 0.5 } }    // 调整推送频率
{ "type": "ping" }                    // 心跳
```

### HTTP REST API

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /` | GET | Web 仪表盘页面 |
| `GET /api/info` | GET | 服务器信息 (含 ws_port / http_port / ip / hostname)，供 HTTP 自动发现使用 |
| `GET /discovery` | GET | 同 `/api/info`，HTTP 自动发现专用别名 |
| `GET /api/media` | GET | 获取当前检测到的媒体信息 |
| `GET /api/lyrics` | GET | 获取当前歌词 |
| `POST /api/lyrics` | POST | 设置歌词 (body: `{"text":"..."}`，支持 LRC) |
| `POST /api/lyric/next` | POST | 下一句歌词 |
| `POST /api/lyric/prev` | POST | 上一句歌词 |
| `POST /api/notification` | POST | 推送通知 (body: `{"title":"...","body":"..."}`) |

## 自定义配置

### 服务器参数

```bash
python server.py --ws-port 9000 --http-port 8000 --interval 0.5 --no-discovery
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--ws-port` | 8765 | WebSocket 端口 |
| `--http-port` | 8080 | HTTP 仪表盘端口 |
| `--interval` | 1.0 | 推送间隔 (秒) |
| `--no-discovery` | - | 禁用 UDP 自动发现 |

### 推送自定义歌词

支持纯文本和 **LRC 格式**（带时间轴，可实现滚动效果）。

**通过 API:**
```bash
# 纯文本
curl -X POST http://localhost:8080/api/lyrics \
  -H "Content-Type: application/json" \
  -d '{"text": "第一行歌词\n第二行歌词\n第三行歌词"}'

# LRC 格式
curl -X POST http://localhost:8080/api/lyrics \
  -H "Content-Type: application/json" \
  -d '{"text": "[00:00.00]第一行歌词\n[00:05.00]第二行歌词\n[00:10.00]第三行歌词"}'
```

**通过 WebSocket:**
```json
{ "type": "set_lyrics", "data": { "text": "[00:00.00]歌词行1\n[00:05.00]歌词行2" } }
```

### 自动识别播放器歌词 (网易云音乐等)

桌面服务实时检测当前播放的歌曲，并在手机副屏自动显示**带精确进度的滚动歌词**。检测链路如下:

1. **Windows SMTC (首选, 精确进度)** — 通过 `media_watcher.ps1` (PowerShell + Windows.Media.Control) 读取系统媒体会话，获得歌曲名 / 歌手 / 专辑 / **播放进度 / 播放状态**。网易云音乐、QQ音乐、酷狗、Spotify 等只要注册到系统媒体会话即可识别。**无需安装任何 Python 包，Python 3.13 等任意版本均可运行** (不依赖 `winsdk`)。
2. **窗口标题 (兜底)** — 若 SMTC 不可用，则解析播放器窗口标题 (如 `歌曲 - 歌手 - 网易云音乐`)。

歌词获取优先级:
- **网易云音乐官方接口** (最匹配中文歌曲，尤其是网易云正在播放的歌) — 自动遍历搜索候选，优先返回带"同步歌词"(LRC 时间轴) 的版本。
- **LRCLIB** 免费歌词库 (兜底)。

只需在电脑端用网易云音乐等播放器播放歌曲，手机副屏即自动显示并随播放进度滚动歌词 (暂停时自动冻结当前行)。

> 说明: 旧版依赖 `winsdk` (Python 3.13 无对应预编译包) 才能实现精确进度，现已改为 PowerShell SMTC 方案，**默认即具备精确滚动进度，不再需要 `pip install winsdk`**。

### 推送通知

```bash
curl -X POST http://localhost:8080/api/notification \
  -H "Content-Type: application/json" \
  -d '{"title": "提醒", "body": "CPU 使用率超过 90%", "level": "warning"}'
```

## 从源码构建 APK

> ⚠️ **重要**: 手机 APP 的界面 (`index.html`) 是打包进 APK `assets/web/` 的，**不随电脑端 `server.py` 自动更新**。凡改动 `index.html` (如重连逻辑、自动发现 UI)，都必须**重新构建并安装 APK** 才能生效。仅改动 `server.py` 时，重启电脑端服务即可，无需动 APK。

### 前提条件

- **JDK 17+** (Gradle 构建必须；本机 `local.properties` 已指向 `C:\sdks\android-sdk`，且 SDK 许可已接受)
- Android SDK (compileSdk 34, build-tools 34)
- Android Studio (推荐) 或 Gradle 命令行

### 使用 Android Studio

1. 打开 Android Studio
2. File → Open → 选择 `android-app/` 目录
3. 等待 Gradle 同步完成
4. Build → Build Bundle(s) / APK(s) → Build APK(s)
5. APK 输出在 `app/build/outputs/apk/debug/app-debug.apk`

### 使用命令行

```bash
cd android-app
./gradlew assembleDebug     # Debug APK
./gradlew assembleRelease   # Release APK (需签名配置)
```

### 安装到手机

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

或在手机上直接打开生成的 APK 文件，允许"未知来源应用"后安装。安装后**先卸载旧版**再装可避免签名冲突 (debug 签名一致时 `-r` 可直接覆盖)。

## 技术栈

| 组件 | 技术 |
|------|------|
| 桌面服务 | Python 3.10+, psutil, websockets, pygetwindow, pywin32 (可选) |
| 媒体检测 | PowerShell SMTC (`media_watcher.ps1`, 任意 Python 版本, 精确进度) + 窗口标题兜底 |
| 歌词源 | 网易云音乐官方接口 (优先) + LRCLIB (兜底), 均返回同步 LRC |
| 通信协议 | WebSocket (数据推送), UDP (发现), HTTP (REST) |
| Android | Kotlin, WebView, AndroidX, Material Components |
| 前端 UI | 原生 HTML/CSS/JS, Canvas (图表), 无框架依赖 |
| 构建工具 | Gradle 8.2, AGP 8.1.2, Kotlin 1.9.10 |

## 断线重连机制

- **修复了"反复断开/重连失败"的根因**: 旧版在 `connect()` 顶替旧 socket 时，旧 socket 的 `onclose` 会异步触发并调度一次"重连定时器"，从而把刚刚建好的连接又关掉，造成反复断开直到 20 次后彻底放弃 (显示"重连失败")。新版引入 `connectionToken`，只有持有"当前连接" token 的 socket 才允许触发重连，彻底消除抖动。
- 重连**持久化**: 不再有硬性 20 次上限；指数退避 1s → 1.5s → ... → 最大 30s，连接恢复后自动归零。
- **手动重连按钮常驻**: 底部"重连"按钮随时可立即重试 (重置退避)。
- **网络恢复即重试**: 监听 `online` 事件与页面可见事件，WiFi 恢复/切回前台时立即重连，无需干等退避计时。
- WebSocket 心跳保活间隔由 30ms (旧版误设，约 33 次/秒，会拖垮连接) 修正为 **15s**。

## 权限说明

| 权限 | 用途 |
|------|------|
| INTERNET | WebSocket / HTTP 网络通信 |
| ACCESS_NETWORK_STATE | 检查网络状态 |
| ACCESS_WIFI_STATE | 获取 WiFi 信息 |
| CHANGE_WIFI_MULTICAST_STATE | UDP 多播/广播接收 |
| WAKE_LOCK | 保持屏幕常亮 (副屏场景) |
| FOREGROUND_SERVICE | 后台保活 (可选) |

## 故障排除

**Q: 手机无法连接服务器?**
- 确认电脑和手机在同一局域网
- 检查电脑防火墙是否放行 8765/8080/8888 端口
- 尝试关闭防火墙临时测试

**Q: 自动发现找不到服务器?**
- 自动发现现在是 **UDP 广播 + HTTP 局域网扫描双通道**，绝大多数路由器/小米手机都能命中 HTTP 扫描。
- 确认手机与电脑在**同一 WiFi / 同一网段** (不同 SSID 或"访客网络"会隔离)。
- 确认电脑端 `server.py` 正在运行 (终端会显示 `WebSocket: ws://<IP>:8765`)。
- 若仍找不到，直接手动输入电脑 IP:端口 (终端显示的那个) 连接即可。

**Q: APK 安装被阻止?**
- 在手机设置中允许"安装未知来源应用"

**Q: 数据刷新太慢/太快?**
- 服务器启动时使用 `--interval 0.5` 调整
- 或通过 WebSocket 发送 `{"type":"set_interval","data":{"interval":0.5}}`

**Q: 歌词不自动识别?**
- 确认电脑端播放器窗口标题包含歌曲名（如 `歌曲名 - 歌手 - 网易云音乐`）
- 网易云音乐等请使用官方 PC 客户端，网页版通常无法检测
- 部分歌曲在 LRCLIB 无歌词，可手动通过 API 推送

**Q: GPU 频率/温度不显示?**
- GPU 使用率无需任何额外软件 (Windows 性能计数器)。
- 频率与温度依赖 LibreHardwareMonitor: 安装并打开 LHM → 菜单 Edit → Settings → 勾选 "Remote Web Server" (默认端口 8085，需与代码一致) → 重启 `server.py`。未装时对应项显示 "—"，不影响其他功能。

**Q: 安装后不是横屏?**
- APP 已强制 `landscape`，部分手机需在系统设置中关闭"方向锁定"
- 首次启动时若仍为竖屏，请横置手机或重新启动 APP
