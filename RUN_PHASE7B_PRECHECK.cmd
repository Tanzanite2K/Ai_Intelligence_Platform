@echo off
rem Phase 7B pre-check. Changes nothing: it does NOT stop the app, pause OneDrive or write data\platform.db.
rem 1. stop_app.ps1 -CheckOnly : shows which FrontierAtlas processes Run B would stop, and any blocker.
rem 2. Dress rehearsal: the exact Run B code on a COPY in C:\FrontierAtlas-test (the live file is only read).
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo === 1/2 Which processes Run B would stop (check only) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_app.ps1" -CheckOnly
echo.
echo === 2/2 Run B code on a copy (dress rehearsal) ===
".venv\Scripts\python.exe" scripts\phase7b_live.py --rehearsal-report docs\phase7b_rehearsal_20261122T171614Z.json --simulate-on C:\FrontierAtlas-test
echo.
echo Pre-check finished. The live database and the running app were not touched.
pause
