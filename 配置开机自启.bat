@echo off
chcp 65001 >nul 2>&1
title ⚙️ 电脑副屏服务 - 开机自启动配置
cd /d "%~dp0"

echo ============================================================
echo   ⚙️ 电脑副屏服务 - 开机自启动配置
echo ============================================================
echo.
echo   [1] 启用开机自启 (静默后台无黑框运行)
echo   [2] 取消开机自启
echo   [0] 退出
echo.
set /p choice="请输入选项 [1/2/0]: "

if "%choice%"=="1" goto enable
if "%choice%"=="2" goto disable
goto exit

:enable
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut(\"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SecondaryDisplay.lnk\"); $s.TargetPath = \"wscript.exe\"; $s.Arguments = \"`\"$PSScriptRoot\后台静默启动(推荐).vbs`\"\"; $s.WorkingDirectory = \"$PSScriptRoot\"; $s.WindowStyle = 7; $s.Save(); Write-Host '[成功] 已将副屏后台静默服务添加到 Windows 开机自启！' -ForegroundColor Green"
goto done

:disable
powershell -Command "Remove-Item \"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SecondaryDisplay.lnk\" -Force -ErrorAction SilentlyContinue; Write-Host '[成功] 已从开机启动项中移除副屏服务！' -ForegroundColor Yellow"
goto done

:done
echo.
pause
:exit
