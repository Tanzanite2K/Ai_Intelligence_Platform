@echo off
rem Final Gemini-only verification (no database writes, no crawl, no enrichment).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_llm_final.ps1"
pause
