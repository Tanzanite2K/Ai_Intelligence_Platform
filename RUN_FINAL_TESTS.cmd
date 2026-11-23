@echo off
rem Final Windows test run: pytest, Ruff, migration test on copies, TypeScript, ESLint, production build (isolated copy).
rem Does not write to data\platform.db, does not stop the running app, does not crawl.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Python venv not found ^(.venv^). & pause & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_final_tests.ps1"
pause
