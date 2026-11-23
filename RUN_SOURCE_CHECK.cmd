@echo off
rem Read-only live check of the six sources fixed on 2026-11-22. Never writes to the database.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set LOG_LEVEL=WARNING
if not exist ".venv\Scripts\python.exe" (echo Python venv not found ^(.venv^). & pause & exit /b 1)
".venv\Scripts\python.exe" scripts\check_sources.py %*
pause
