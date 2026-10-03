@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0backend\scripts\launch_desk.py"
if errorlevel 1 pause
