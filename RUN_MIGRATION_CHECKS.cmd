@echo off
rem Migration compatibility checks on copies only. Production data\platform.db is hashed, never written.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_migration_checks.ps1"
echo.
pause
