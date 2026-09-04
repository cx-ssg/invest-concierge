@echo off
REM invest-concierge desktop launcher (uvicorn + pywebview + tray)
REM NOTE: this file MUST stay ASCII-only with CRLF line endings
REM (cmd.exe parses bat by raw bytes; UTF-8 Chinese + LF breaks parsing)

REM go to project root (parent of this script folder)
cd /d "%~dp0.."

set "PY=python"
if exist "desktop\.venv\Scripts\python.exe" set "PY=desktop\.venv\Scripts\python.exe"

echo ========================================
echo   invest-concierge desktop (pywebview)
echo ========================================
echo.

REM dependency check (import name of pip package pywebview is "webview")
%PY% -c "import webview" >nul 2>&1
if errorlevel 1 (
    echo [info] pywebview not found. Install dependencies first:
    echo.
    echo     pip install -r requirements.txt
    echo.
    echo [info] Or run backend-only browser mode:
    echo     %PY% desktop\launcher.py --browser
    echo.
    pause
    exit /b 1
)

echo [info] Starting desktop app...
echo [info] Close window = minimize to tray. Tray menu "Exit" to quit.
echo.

REM API key: read directly by config.py (.env / local_env.bat) - no cmd call here
REM (calling a bat with UTF-8 comments breaks under GBK codepage)

%PY% desktop\launcher.py %*
if errorlevel 1 (
    echo.
    echo [error] desktop exited abnormally. Browser fallback:
    echo     %PY% desktop\launcher.py --browser
    echo.
    pause
)
