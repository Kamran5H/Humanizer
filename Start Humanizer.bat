@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Humanizer Pro

if exist "Install Desktop Shortcut.vbs" (
    wscript //nologo "Install Desktop Shortcut.vbs" /quiet
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" humanizer_pro.py
    goto :eof
)

echo ============================================================================
echo   Humanizer Pro - First-Time Environment Setup
echo ============================================================================

set "PY_CMD="
where py >nul 2>nul && (
    py -3 --version >nul 2>nul && set "PY_CMD=py -3"
)
if not defined PY_CMD (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set "PY_CMD="%%D\python.exe""
    )
)
if not defined PY_CMD (
    python --version >nul 2>nul && set "PY_CMD=python"
)

if not defined PY_CMD (
    echo [1/3] Python 3 not found. Installing Python 3.12 automatically...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
        "$p = Join-Path $env:TEMP 'python-3.12.8-amd64.exe'; " ^
        "Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile $p; " ^
        "Start-Process -FilePath $p -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1' -Wait; " ^
        "Remove-Item $p -Force -ErrorAction SilentlyContinue"
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set "PY_CMD="%%D\python.exe""
    )
)

if not defined PY_CMD (
    echo [ERROR] Could not locate Python 3. Please install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [2/3] Creating virtual environment (.venv) and installing dependencies...
%PY_CMD% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\pip.exe" install -r requirements.txt -q

echo [3/3] Starting Humanizer Pro Studio...
".venv\Scripts\python.exe" humanizer_pro.py
pause
