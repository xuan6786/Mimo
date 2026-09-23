@echo off
chcp 65001 >nul
setlocal

rem ===== MiMo 助手 · 网页版启动脚本 =====
rem 默认地址 http://127.0.0.1:8765   （仅本机可访问）

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if "%MIMO_API_KEY%"=="" (
    echo [提示] 未检测到环境变量 MIMO_API_KEY
    echo        可以启动后在页面右上角「设置」里填写，或先执行：
    echo        set MIMO_API_KEY=sk-你的密钥
    echo.
)

python "%SCRIPT_DIR%server.py" %*
if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请确认已安装 Python 3.8+ 并加入 PATH
    pause
)
endlocal
