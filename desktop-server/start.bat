@echo off
chcp 65001 >nul 2>&1
echo ==========================================
echo   桌面副屏推送服务 - 快速启动
echo ==========================================
echo.

cd /d "%~dp0"

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python, 请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 检查依赖
python -c "import psutil, websockets" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装依赖...
    pip install psutil websockets
)

echo.
echo 正在启动服务...
echo.
python server.py %*

pause
