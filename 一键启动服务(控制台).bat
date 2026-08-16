@echo off
chcp 65001 >nul 2>&1
title 🖥️ 电脑副屏服务 (Secondary Display Server)
cd /d "%~dp0desktop-server"

echo ============================================================
echo   🖥️ 桌面副屏推送服务 - 控制台启动
echo ============================================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python 环境，请先安装 Python 3.10+ 并勾选 Add to PATH。
    pause
    exit /b 1
)

REM 启动服务端
python server.py
pause
