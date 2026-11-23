@echo off
rem Gemini-only live verification (no database writes, no enrichment, no crawl).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_gemini_check.ps1"
pause
