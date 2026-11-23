@echo off
rem Read-only preflight before production activation. Does NOT write to data\platform.db.
rem Stop the app first (scripts\stop_app.ps1) and pause OneDrive.
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\activation_preflight.py
echo.
pause
