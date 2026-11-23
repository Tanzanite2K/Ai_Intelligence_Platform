@echo off
rem Start FrontierAtlas web app: FastAPI (read-only) + Next.js. Add -Production for a production build.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_app.ps1" %*
pause
