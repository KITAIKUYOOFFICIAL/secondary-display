' ============================================================
' 电脑副屏服务 - 后台静默无窗口启动 (日常与开机自启推荐)
' 同时启动: 1) 主服务器 server.py  2) 守护进程 watch_plugin.py
'   (watch_plugin: 插件更新自动重启网易云 + 服务器保活)
' ============================================================
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

strCurDir = fso.GetParentFolderName(WScript.ScriptFullName)
strServerPy = strCurDir & "\desktop-server\server.py"
strWatchPy = strCurDir & "\desktop-server\watch_plugin.py"

' 使用 pythonw.exe 后台无黑框静默运行 (主服务 + 守护进程)
ws.Run "pythonw """ & strServerPy & """", 0, False
ws.Run "pythonw """ & strWatchPy & """", 0, False
