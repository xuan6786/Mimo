@echo off
chcp 65001 >nul
setlocal

rem ===== MiMo 本地助手 启动脚本 =====
rem 如果你的 Python 不在 PATH 中，请把下面的 python 改成完整路径

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if "%MIMO_API_KEY%"=="" (
    echo [提示] 尚未检测到环境变量 MIMO_API_KEY
    echo        可先执行： set MIMO_API_KEY=sk-你的密钥
    echo        或在 config.json 中填写 api_key 字段
    echo.
)

python "%SCRIPT_DIR%assistant.py" %*
if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请确认已安装 Python 3.8+ 并加入 PATH
    pause
)
endlocal
