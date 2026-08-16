# 📱 PC副屏/网易云音乐控制台 (Secondary Display & NCM Controller) - Agent 交接文档

你好，接管此项目的 Agent。本指南详细记录了此项目的系统架构、技术栈、核心模块原理以及最近的修复与优化进度。这个项目包含一个运行在 PC 的服务端，一个注入到网易云音乐的探测器插件，一个移动端 Web UI，以及一个用于沉浸式运行 Web UI 的安卓端 App。

## 🏗️ 系统架构设计

### 1. 核心拓扑图
```
[手机/平板客户端 (Android WebView或浏览器)] 
    │ 
    ├─ (HTTP) ──> 请求最新的 index.html UI 界面
    └─ (WS) ────> 连接到 ws://192.168.x.x:8765 (控制指令与心跳)
                     │
                     ▼
             [PC 端 Python 后端 (desktop-server)]
             (运行 `python desktop-server/server.py`)
                     │
                     ├─> [Windows SMTC 控制] -> 使用 media_watcher.ps1 / media_control.ps1 读取/强控 Windows 媒体
                     ├─> [RTSS/AIDA64/Psutil] -> 获取电脑硬件性能指标 (CPU/GPU/内存/网络)
                     │
                     ▼
[网易云音乐 PC 版 (包含 BetterNCM 注入的 secdisp-probe 插件)]
    └─ (WS) ────> 插件在 `cloudmusic.exe` 中运行，连接到 `ws://127.0.0.1:8765`
    └─ (Hook) ──> 提取最新渲染的歌词、进度、播放状态，0延迟回传给 Python 后端
```

### 2. 关键目录结构
- **`desktop-server/server.py`**: Python 后端主入口，启动异步 asyncio loop，开启 WebSockets (`ws_server.py`) 和 HTTP 服务。
- **`desktop-server/modules/ws_server.py`**: 处理多端 WebSocket 分发。负责将手机端发送的媒体指令 (`play_pause`, `next`, `seek`) 转发给网易云音乐，同时使用 SMTC 进行终极保底控制。
- **`desktop-server/web/index.html`**: 核心前端 UI（无框架单页面应用，原生 JS + CSS），包含了极具动感和现代设计的仪表盘卡片与网易云歌词控制卡片。
- **`<BetterNCM安装目录>/plugins_runtime/secdisp-probe/main.js`** (注意绝对路径, 默认 `C:/betterncm`): 注入网易云 CEF 的探针脚本。截获原生 `InfLinkApi` 的歌词数据、当前时间线数据。
- **`android-app/`**: 原生安卓套壳项目。核心是 `MainActivity.kt` 中的 WebView，它会隐式全屏加载 `file:///android_asset/web/index.html` 或是传入的局域网 IP。
- **`scratch/`**: 开发过程中沉淀的验证脚本（如打包脚本 `repack_apk.py`）与 APK 产物。媒体控制脚本已移入 `desktop-server/media_control.ps1` / `media_control_worker.ps1`。

---

## 🛠️ 最近解决的技术痛点 (历史踩坑记录)

为了避免你重走弯路，以下是最新被解决的顽固 BUG 及其深层原因：

### 1. 手机端触控“按下去没反应”的 DOM 撕裂问题
- **现象**: 用户在手机浏览器/App点击暂停/播放按钮，没有任何交互反馈。
- **根因**: 后端以高达 `5次/秒` 的频率下发状态，前端在 `handleLyrics()` 中不管状态是否改变，疯狂执行 `playBtn.innerHTML = '<svg...>'`，导致按钮节点在触摸事件 `touchstart` 之后、`touchend` 触发之前被强行销毁，手势被物理打断。
- **解决方案**: 在 `playBtn` 引入了 `data-playing` 状态比对（DOM Diff），只有当播放状态切实改变时才更新 innerHTML；此外利用 `pointer-events: none` 屏蔽 SVG/Path 的点击劫持。

### 2. 网易云 PC 端控制指令无响应问题
- **现象**: 手机端 UI 已经触发事件，且后端收到 `play_pause`，但 PC 端网易云不暂停。
- **根因**: 网易云后台运行或无焦点时，不响应 `win32api` 发送的全局媒体键虚拟键码；BetterNCM 的内部 API 调用也因版本变更而存在兼容问题。
- **解决方案**: 在 `ws_server.py` 中加入了 **Windows SMTC (System Media Transport Controls) 强控保底**（调用 `desktop-server/media_control.ps1`）。现在，只要 Windows 识别到了媒体进程，就能无视焦点 100% 强制接管播放状态。

### 3. 歌曲进度条拖拽困难
- **现象**: 手机屏幕上难以点中纤细的进度条。
- **解决方案**: CSS 中利用了一个 `32px` 高度的透明外包容器 (`.lyrics-progress-wrap`) 作为真实的手势捕获层 (Hitbox)，并在里面画一条极细的轨道线 (`.lyrics-progress-track`)。

### 4. 播放控制"失灵" — 探针只上报不接收指令 (2026-08-17 修复)
- **现象**: 手机端点暂停/播放/切歌无效 (时灵时不灵, 后台播放时完全无效)。
- **根因**: `secdisp-probe` 探针的 `main.js` 是**纯上报脚本**——只有 `ws.send(ncm_sync)` 推送数据,**没有任何 `ws.onmessage` 处理**。server 的 `send_ncm_command` 把控制指令发给探针后石沉大海。真正的控制通道只剩 win32api 媒体键 (网易云后台/无焦点时无效) 和 SMTC 保底 (受会话限制)。
- **解决方案**: 探针 `main.js` 新增 `ws.onmessage` 处理 `ncm_command` 消息, 直接用 `InfLinkApi` 原生控制:
  - `play_pause/toggle` → 按 `getPlaybackStatus()` 调 `api.play()/pause()`
  - `play/pause/next/prev/previous/seek` → `api.play()/pause()/next()/previous()/seekTo(ms)`
  - **这是最可靠的控制通道**: 指令在网易云进程内部执行, 不依赖前台焦点/媒体键/SMTC。
- **三处副本必须同步** (否则下次网易云重启会从旧插件解包覆盖 runtime 导致修复丢失):
  1. `betterncm-plugins/secdisp-probe.plugin` (项目源头, 含源码副本 `secdisp-probe-main.js`)
  2. `<BetterNCM安装目录>/plugins/secdisp-probe.plugin` (BetterNCM 插件安装目录, 默认 `C:/betterncm`)
  3. `<BetterNCM安装目录>/plugins_runtime/secdisp-probe/main.js` (运行目录, watch_plugin 监视此文件)
- **验证**: 连续 3 次 play_pause 状态正确切换 (True→False→True→False) ✅

---

## 🚀 未来的开发方向与 Next Steps

如果你即将接管此项目，可以参考以下改进方向：
1. **安卓端热更新 (OTA)**: 
   目前安卓端原生套壳内嵌的 `index.html`（存放于 `assets/web/index.html`）只能通过重打包 APK 更新。如果后续用户想在 App 内自动加载最新界面，需修改 `MainActivity.kt` 优先请求局域网的 URL（即 `http://192.168.x.x:8080/`），而不是本地 asset。
2. **硬件性能监控模块扩展**: 
   如果你需要优化 PC 性能统计卡片，请参考 `desktop-server/modules/monitor.py`（psutil 指标聚合）与 `desktop-server/modules/hardware.py`（GPU/功耗/刷新率等硬件信息）。
3. **网易云封面提取**:
   目前 `secdisp-probe` 只提取歌词。提取高清封面可能需要拦截网络请求或者在原生 DOM (如 `.j-img`) 中寻找 `src`。

祝你好运！这是一个性能极高、UI设计极度现代化的全栈控制台项目，请在修改 UI 时，严格遵循现有的原生动态、毛玻璃和流畅过渡的设计美学体系！
