@echo off
setlocal
cd /d "%~dp0"
title Humanizer Pro

if exist "Install Desktop Shortcut.vbs" (
    wscript //nologo "Install Desktop Shortcut.vbs" /quiet
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" humanizer_pro.py
) else (
    python humanizer_pro.py
)
pause
