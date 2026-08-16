# 桌面副屏系统 — 新 Agent 开发指南

> 面向接手的 AI Agent / 开发者。本文是唯一权威的开发入口，请先完整读完再动手。

---

## 1. 项目是什么

**电脑端网易云音乐 → 手机副屏** 实时同步系统：

- 电脑端：Python 服务（`server.py`）采集系统状态 + 抓取网易云播放信息/歌词
- 手机端：Android WebView App，横屏显示歌词 + 系统监控仪表盘
- 通信：WS(8765 实时推送) + HTTP(8080 REST) + UDP(8888 自动发现)

**数据流**：
```
网易云音乐 (PC)
   ├─ 窗口标题 → "歌名 - 歌手"（兜底通道）
   ├─ BetterNCM 插件 secdisp-probe.plugin → window.InfLinkApi → POST /api/inflink（精确进度通道, 最高优先级）
   └─ SMTC 媒体会话（一直未注册, 勿依赖）
        ↓
   server.py (ContentManager/MediaWatcher)
        ↓ WS push
   手机 App (WebView 加载 assets/web/index.html)
```

---

## 2. 目录结构

```
C:\Users\lin\WorkBuddy\2026-08-16-00-05-21\secondary-display\
├── desktop-server\           # 电脑端服务
│   ├── server.py             # 主服务 (WS/HTTP/UDP/歌词/媒体)
│   ├── media_watcher.ps1     # SMTC 轮询脚本 (300ms, 备用通道)
│   └── web\index.html        # UI 源文件 (改这个!)
├── android-app\              # APK 工程 (Kotlin + WebView)
│   └── app\src\main\assets\web\index.html   # 打包进 APK 的 UI (改完源要同步过来)
└── (构建产物) android-app\app\build\outputs\apk\debug\
```

**核心原则：UI 只改 `desktop-server/web/index.html`，然后同步到 `android-app/app/src/main/assets/web/index.html`，再打包安装。**

---

## 3. 当前状态（2026-08-16 交接）

- ✅ **开发基准**：用户指定从微信上传的早期版 APK 的 index.html（44218B）继续开发（含连接面板、自动发现、无 InfLinkApi 精确进度代码）
- ✅ **手机已装**：用户早期版（连接正常，见 `_server.log`）
- ✅ **服务器运行中**：端口 8080/8765/8888
- ✅ **精确进度插件在位**：`<BetterNCM安装目录>\plugins\secdisp-probe.plugin` + InfLink-rs 3.2.11 + 网易云 3.1.20（旧版, 因 SMTC 兼容性问题降级）
- ⚠️ 当前基准版 index.html **不含** `api/inflink` 代码 → 快进/后退歌词不实时。恢复方法见 §7.2

---

## 4. 开发流程（标准操作）

### 4.1 改 UI
1. 编辑 `desktop-server/web/index.html`
2. 浏览器预览：打开 `http://127.0.0.1:8080`（服务器在跑时自动连接）
3. 同步：`cp desktop-server/web/index.html android-app/app/src/main/assets/web/index.html`

### 4.2 打包 APK（关键！勿用 Gradle）
**Gradle 在此环境不可靠**（cmd 长命令被截断、锁文件卡死）。用**快速替换方案**：

```bash
# 1. 用 Python zipfile 复制原 APK 并替换 assets/web/index.html
#    关键: 逐 entry 复制, 保留 compress_type/external_attr,
#    resources.arsc / AndroidManifest.xml 保持 STORED
#    (脚本模板见文末附录 A)

# 2. 签名 (输出文件名必须是"不存在的", 否则不覆盖)
java -jar "<你的Android SDK路径>/build-tools/33.0.1/lib/apksigner.jar" sign \
  --ks "<你的debug.keystore路径>" --ks-pass pass:android \
  --key-pass pass:android --out app-new-signed.apk app-new.apk

# 3. 安装 (MIUI 会弹窗, 需用户手动点"安装")
adb push app-new-signed.apk /sdcard/Download/secdisp.apk
adb shell am start -a android.intent.action.VIEW \
  -d file:///sdcard/Download/secdisp.apk -t application/vnd.android.package-archive
```

### 4.3 验证
```bash
adb shell dumpsys package com.secondarydisplay.app | grep lastUpdateTime  # 确认装的是新版
tail -20 desktop-server/_server.log                                       # 看连接/歌词日志
```

---

## 5. 环境速查

| 项 | 路径 |
|---|---|
| Python | `<你的Python路径>` |
| JDK 17 | `<你的JDK路径>` |
| apksigner | `<你的Android SDK路径>/build-tools/33.0.1/lib/apksigner.jar` |
| adb | `<你的Android SDK路径>/platform-tools/adb.exe` |
| 手机 adb id | `<你的设备序列号>`（安卓手机） |
| APK 包名 | `com.secondarydisplay.app`（debug 证书） |
| 网易云 | `C:\Program Files\NetEase\CloudMusic\`（`msimg32.dll`=BetterNCM 注入） |
| BetterNCM 插件 | `<BetterNCM安装目录>\plugins\`（`*.plugin` 是 ZIP） |
| 服务器日志 | `desktop-server/_server.log` |
| 手机 IP | `<手机IP>`（电脑 `<电脑IP>` 左右） |

**运行环境示例**：AMD Ryzen CPU + Intel Arc GPU（Windows 11）。

---

## 6. 核心机制详解

### 6.1 歌词获取（server.py ContentManager）
- `_fetch_lyrics(title, artist)` → 网易云搜索 API → 解析 LRC → 推送手机
- 每 1s 推一次 `{type:'lyrics', data:{song, current_time, progress, lines, current_line}}`
- **切歌检测**：`song_key = title||artist` 变化时重新拉歌词
- 歌词行含 `syncedLyrics`(原词) + `translation`(翻译)

### 6.2 媒体状态优先级（MediaWatcher.get_current_media）
```
0. inflink_data (BetterNCM 插件推送, 2 秒内新鲜 → 最高优先)   ← 精确进度
1. PowerShell SMTC (media_watcher.ps1, 每 300ms)             ← 一直无会话, 勿依赖
2. winsdk SMTC
3. 窗口标题 "歌名 - 歌手"                                     ← 兜底
4. 窗口进程识别
```

### 6.3 精确进度通道（InfLinkApi）
- 网易云内 BetterNCM 插件 `secdisp-probe.plugin`（JS）读 `window.InfLinkApi`
- 订阅 `timelineUpdate/songChange/playStateChange` → `fetch POST http://127.0.0.1:8080/api/inflink`
- server.py 端点 `/api/inflink` → `update_inflink()` 存最新精确进度
- **前端需要对应代码**才显示：收到 `stats` 消息时读 `d.media` 或走 `/api/media`（`source==="InfLinkApi"` 表示精确进度）
- InfLinkApi 文档：`C:\Users\lin\Downloads\inflink-api.md`（官方！含全部方法/事件）

### 6.4 自动发现/连接（APK 内）
- Android 原生 UDP 广播发现（JS 做不了 UDP）→ `AndroidBridge.startDiscovery()`
- index.html 加载后 500ms 自动 `startDiscovery()`（若未连接）
- 手动入口：底部"⚙️ 设置"按钮唤出连接面板（IP/端口/自动发现）

---

## 7. 已知问题 & 待办

### 7.1 网易云 SMTC 通道
- 整个项目周期 SMTC 从未注册成功（网易云 3.1.x 兼容性问题）
- **不要浪费时间在 SMTC 上**，用 InfLinkApi 插件通道（工作正常）

### 7.2 恢复"快进/后退歌词实时"（重要待办）
当前基准版（用户早期版）index.html 无 InfLinkApi 支持，用户反馈快进后退歌词不跟跳。
**恢复方案**：把含 `api/inflink` 的版本（`web/index.html.pre-userbak` 中有）的 JS 合并到基准版，
或直接对比 `.pre-userbak` 与当前文件的差异移植。服务端 + 插件都已就绪，改完前端即生效。

### 7.3 服务器保活
- server.py 手动后台启动，进程被杀/崩溃后手机断连
- 建议：做成开机自启（启动文件夹放静默启动脚本）

### 7.4 用户曾尝试但放弃的需求（避免重做）
- 横屏双列仪表盘（性能左列+歌词右列）→ 用户不满意，已回退
- 硬件 logo（AMD/Intel）+ 实时功耗 + 性能趋势图 → 服务端字段已实现（`hardware/power/perf_history`），
  前端已移除；如需可复用 server.py 的 `HardwareInfo/PowerReader` 类（WMI 读取 AMD Ryzen 5 5600X + Intel Arc A750）
- 背景上传 + 毛玻璃 + 智能配色（浅色背景黑字/深色背景白字）→ 前端代码已移除，可参考早期提交

---

## 8. 踩坑记录（重要）

1. **cmd //c 长命令被截断**：只显示 banner 不执行 → 用 python subprocess 或 java 直接调 jar
2. **Gradle checksums.lock 拒绝访问**：中断的构建残留锁 → 删 `android-app/.gradle`
3. **apksigner --out 目标已存在不覆盖**：用不存在的新文件名
4. **zipfile 直接 append 到 APK 破坏中央目录**（v2 签名块在中央目录前）→ 必须整包重建逐 entry 复制
5. **BetterNCM 插件 manifest**：字段必须是 `injects.Main[].file`（不是 `main`）、`manifest_version:1`、`ncm3-compatible:true`
6. **msimg32.dll 部署**：网易云安装目录对当前用户可写，删掉即完全卸载 BetterNCM
7. **网易云降级**：3.1.38 → 3.1.20（旧版，为了 BetterNCM/InfLink-rs 兼容）；网易云会自动更新 → 可能需要阻止
8. **手机安装**：MIUI 弹窗需用户手动点"安装"；签名一致可覆盖安装不丢数据
9. **PowerShell 工具在此环境输出不可靠**（吞输出/写文件失败）→ 用 Python 脚本替代
10. **netstat 输出是 GBK**：Python 读取需 `decode('gbk', errors='replace')`

---

## 附录 A：APK 快速打包脚本模板

```python
import zipfile, os

src_apk = r'...\app-debug.apk'                    # 原 APK (上次成功安装的版本)
new_web = r'...\desktop-server\web\index.html'    # 新 UI
out_apk = r'...\app-new.apk'                      # 输出 (必须是新文件名)

new_data = open(new_web, 'rb').read()
with zipfile.ZipFile(src_apk) as zin, zipfile.ZipFile(out_apk, 'w') as zout:
    for info in zin.infolist():
        data = zin.read(info.filename)
        if info.filename == 'assets/web/index.html':
            data = new_data
        zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        zi.compress_type = info.compress_type      # 保留原压缩方式
        zi.external_attr = info.external_attr
        zi.create_system = info.create_system
        zout.writestr(zi, data,
            compress_type=zipfile.ZIP_STORED if info.compress_type == zipfile.ZIP_STORED
            else zipfile.ZIP_DEFLATED)
print('repacked:', out_apk, os.path.getsize(out_apk))
```

---

*生成日期: 2026-08-16 · 若环境/状态变化请同步更新本文档*

---

## 9. 防故障守护机制 (watch_plugin.py, 2026-08-17 新增)

**问题**: 插件 main.js 更新后, 网易云若未重启则加载旧插件 → 功能不生效
**方案**: 常驻守护进程 `desktop-server/watch_plugin.py`

功能:
1. **插件监视**: 每 2s 检查 `<BetterNCM安装目录>/plugins_runtime/secdisp-probe/main.js` 和 `<BetterNCM安装目录>/plugins/secdisp-probe.plugin` 的 mtime, 变化 → 自动重启网易云 (防抖 15s)
2. **服务器保活**: 检测 8080 端口, 离线 → 自动重启 server.py (系统 Python, 路径可用环境变量 SECDISP_PYTHON 覆盖)

启动: 已合并进 `后台静默启动(推荐).vbs` (同时启动 server.py + watch_plugin.py)
日志: `desktop-server/watch_plugin.log`
自启: 双击 `配置开机自启.bat` 选 [1] (或手动复制 vbs 到 shell:startup)

已验证: 插件 touch → 自动重启网易云 ✓; 杀 server → 自动拉起 ✓
