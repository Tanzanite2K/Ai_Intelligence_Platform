@echo off
rem AUTHORIZED production activation. Writes to data\platform.db. Do not run without authorization.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_activation_execute.ps1"
echo.
pause
