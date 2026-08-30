@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1

echo ========================================
echo   基金小助手 - Web 启动脚本
echo ========================================
echo.

REM ---------- 查找 Python ----------
set "PY=python"
where python >nul 2>&1
if errorlevel 1 (
    set "PY=py -3"
    py -3 --version >nul 2>&1
    if errorlevel 1 goto no_python
)

for /f "delims=" %%i in ('%PY% --version 2^>^&1') do echo [信息] 已找到 %%i
echo.

REM ---------- 检查入口文件 ----------
if not exist "app.py" goto no_file

REM ---------- 检查依赖 ----------
echo [信息] 正在检查依赖包...
%PY% -c "import streamlit" >nul 2>&1
if errorlevel 1 goto no_deps
%PY% -c "import requests" >nul 2>&1
if errorlevel 1 goto no_deps
%PY% -c "import matplotlib" >nul 2>&1
if errorlevel 1 goto no_deps
%PY% -c "import openai" >nul 2>&1
if errorlevel 1 goto no_deps
echo [信息] 依赖检查通过
echo.

REM ---------- API Key：优先读 local_env.bat，其次读系统环境变量 ----------
if exist "local_env.bat" call "local_env.bat"
if "%DEEPSEEK_API_KEY%"=="" (
    echo [提示] 未设置 DEEPSEEK_API_KEY，AI 功能可能不可用
    echo [提示] 方式1: 在项目目录创建 local_env.bat，内容为:
    echo         set DEEPSEEK_API_KEY=你的key
    echo [提示] 方式2: 系统环境变量 setx DEEPSEEK_API_KEY "你的key" 后重启终端
    echo.
) else (
    echo [信息] 已检测到 DEEPSEEK_API_KEY
    echo.
)

REM ---------- 启动服务 ----------
echo [信息] 正在启动 Streamlit 服务...
echo [成功] 启动后请访问: http://localhost:8501
echo [信息] 浏览器会自动打开，按 Ctrl+C 可停止服务
echo ----------------------------------------
echo.

%PY% -m streamlit run app.py
set ERR=%ERRORLEVEL%

echo.
echo ----------------------------------------
if not "%ERR%"=="0" goto start_fail
echo [信息] 服务已正常停止
goto end

:no_python
echo [错误] 未找到 Python
echo [错误] 请安装 Python 3.9 及以上版本，并勾选 Add Python to PATH
goto end

:no_file
echo [错误] 找不到 app.py
echo [错误] 请确认 start.bat 位于项目根目录
goto end

:no_deps
echo [错误] 缺少必要的依赖包
echo [错误] 请在项目目录执行以下命令后重试:
echo.
echo     pip install streamlit requests matplotlib openai
echo.
goto end

:start_fail
echo [错误] 服务异常退出，退出码: %ERR%
echo [错误] 请查看上方报错信息，常见原因:
echo        - 8501 端口被占用
echo        - 依赖未安装完整
echo        - app.py 运行报错
goto end

:end
echo.
pause
