@echo off
rem Targeted verification (no crawling, no record changes): integrations, Gemini model, live LLM checks, PRIVATE Sheets export.
rem Optional: RUN_VERIFY.cmd -Enrich   (also updates 16 news records with organisation tags via the LLM)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_verify.ps1" %*
echo.
echo Done. Evidence: docs\verify_run.log, docs\integration_report.md, docs\gemini_model_selection.json, docs\llm_live_check.json, docs\sheets_export.json
pause
