' 🖥️ 启动电脑副屏服务图形化管理器 (GUI)
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

strCurDir = fso.GetParentFolderName(WScript.ScriptFullName)
strGuiPy = strCurDir & "\desktop-server\gui_launcher.py"

' 使用 pythonw.exe 启动 GUI 管理器（无黑框）
ws.Run "pythonw """ & strGuiPy & """", 0, False
