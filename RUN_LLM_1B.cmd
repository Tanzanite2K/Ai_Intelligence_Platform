@echo off
rem Re-run only live check 1b + tests + report. Read-only on the database.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_llm_1b.ps1"
pause
