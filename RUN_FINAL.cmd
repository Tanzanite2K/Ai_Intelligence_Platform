@echo off
rem One-click final run. Opens a PowerShell window, runs scripts\run_final.ps1, keeps the window open.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_final.ps1" %*
echo.
echo Finished with exit code %ERRORLEVEL%. Evidence: docs\final_run.log and FINAL_REPORT.md
pause
