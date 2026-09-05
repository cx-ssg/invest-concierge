@echo off
rem ============================================================
rem invest-concierge installer build (SHELL_UPGRADE_PLAN 1.3)
rem Usage: scripts\build_installer.bat
rem   1. verify main exe exists (dist_m4\invest-concierge.exe)
rem   2. locate ISCC (where -> user path -> Program Files)
rem   3. compile desktop\installer\invest-concierge-setup.iss
rem   4. verify output exists and size > 50MB
rem Output: dist_m4\invest-concierge-setup-v1.0.0.exe
rem NOTE: keep this file ASCII-only + CRLF (cmd breaks on UTF-8 CN)
rem ============================================================
setlocal enabledelayedexpansion

set ROOT=%~dp0..
set EXE=%ROOT%\dist_m4\invest-concierge.exe
set ISS=%ROOT%\desktop\installer\invest-concierge-setup.iss

if not exist "%EXE%" (
    echo [ERROR] main exe not found: %EXE%
    echo         rebuild it first, see docs\PACKAGING.md
    exit /b 1
)
if not exist "%ISS%" (
    echo [ERROR] iss script not found: %ISS%
    exit /b 1
)

rem --- locate ISCC: where first, then known install paths ---
set ISCC=
for /f "delims=" %%i in ('where ISCC 2^>nul') do (
    set ISCC=%%i
    goto :found
)
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not defined ISCC if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set ISCC=C:\Program Files\Inno Setup 6\ISCC.exe

:found
if not defined ISCC (
    echo [ERROR] Inno Setup 6 not found. Install: winget install JRSoftware.InnoSetup
    exit /b 1
)
echo [1/3] ISCC = %ISCC%

echo [2/3] compiling installer ...
"%ISCC%" "%ISS%"
if errorlevel 1 (
    echo [ERROR] ISCC compile failed, see log above
    exit /b 1
)

rem --- verify output ---
set OUT=%ROOT%\dist_m4\invest-concierge-setup-v1.0.0.exe
if not exist "%OUT%" (
    echo [ERROR] output not generated: %OUT%
    exit /b 1
)
for %%A in ("%OUT%") do set SIZE=%%~zA
if !SIZE! LSS 50000000 (
    echo [ERROR] output size abnormal ^(!SIZE! bytes ^< 50MB^), main exe missing?
    exit /b 1
)
echo [3/3] installer ready: %OUT% ^(!SIZE! bytes^)
exit /b 0
