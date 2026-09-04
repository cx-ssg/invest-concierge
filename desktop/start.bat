@echo off
REM M3 桌面壳双击入口：一条命令进桌面版（uvicorn + pywebview 窗口 + 托盘）
cd /d "%~dp0.."
chcp 65001 >nul 2>&1

REM 优先 desktop\.venv（沙箱自测环境）；真机直接装好依赖后走 python
set "PY=python"
if exist "desktop\.venv\Scripts\python.exe" set "PY=desktop\.venv\Scripts\python.exe"

echo ========================================
echo   invest-concierge 桌面版（pywebview）
echo ========================================
echo.

REM 依赖自检：缺 pywebview 时提示装依赖（浏览器回退由 launcher 处理）
%PY% -c "import pywebview" >nul 2>&1
if errorlevel 1 (
    echo [信息] 未检测到 pywebview，请先安装依赖：
    echo.
    echo     pip install -r requirements.txt
    echo.
    echo [信息] 也可以直接启动后端并用浏览器访问：
    echo     %PY% desktop\launcher.py --browser
    echo.
    pause
    exit /b 1
)

echo [信息] 正在启动桌面版（关窗最小化到托盘；托盘「退出」结束程序）...
echo.

REM API Key：兼容旧式 local_env.bat（如有）；新推荐方式见 README（复制 .env.example 为 .env）
if exist "local_env.bat" call "local_env.bat"

%PY% desktop\launcher.py %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    echo [错误] 桌面版异常退出，退出码: %ERR%
    echo [信息] 可改用浏览器模式：%PY% desktop\launcher.py --browser
    echo.
    pause
)