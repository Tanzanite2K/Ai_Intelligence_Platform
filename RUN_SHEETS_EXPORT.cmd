@echo off
rem Authorized Google Sheets export (writes to the configured Google Sheet; no database writes).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_sheets_export.ps1"
echo.
pause
