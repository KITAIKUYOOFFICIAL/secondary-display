@echo off
chcp 65001 >nul 2>&1
title 🛑 停止电脑副屏服务
cd /d "%~dp0"

echo ============================================================
echo   🛑 正在停止电脑副屏服务...
echo ============================================================
echo.

powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[OK] 已停止副屏服务进程 (PID: ' + $_.ProcessId + ')') }"

echo.
echo [完成] 副屏服务已安全退出。
timeout /t 2 >nul
