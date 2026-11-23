@echo off
rem Phase 7B Run B: writes to the LIVE data\platform.db. Run only after explicit approval.
rem Order: you pause OneDrive -> you type RUN B -> stop API/web -> backups + verification -> Alembic stamp/upgrade
rem -> baseline backfill -> verification -> restart the app ONLY if everything passed.
rem On any failure the database is left exactly as it is, nothing is restored, and the app is NOT restarted.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set REHEARSAL=docs\phase7b_rehearsal_20261122T171614Z.json
if not exist ".venv\Scripts\python.exe" (echo Python venv not found ^(.venv^). & pause & exit /b 1)
if not exist "%REHEARSAL%" (echo Run A report %REHEARSAL% not found. & pause & exit /b 1)
echo ======================================================================
echo  PHASE 7B RUN B - this WRITES to data\platform.db
echo  1. Pause OneDrive now: tray icon ^> Pause syncing ^> 2 hours.
echo  2. Save any work in the FrontierAtlas web app; it will be stopped.
echo ======================================================================
set /p ANSWER=Type RUN B to continue, anything else to cancel:
if /I not "%ANSWER%"=="RUN B" (echo Cancelled. Nothing was changed. & pause & exit /b 2)

echo.
echo === 1/3 Stop the API and web app, check for other writers ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_app.ps1"
if errorlevel 1 (echo. & echo Stopped before any database work. The database was not touched. & pause & exit /b 1)

echo.
echo === 2/3 Backups, migration, baseline backfill, verification ===
".venv\Scripts\python.exe" scripts\phase7b_live.py --confirm RUN-B --rehearsal-report "%REHEARSAL%"
set RC=%ERRORLEVEL%
if "%RC%"=="0" goto restart
echo.
if "%RC%"=="3" (
  echo FAILED AFTER A WRITE. The database was left exactly as the failure left it. NOTHING was restored.
  echo The report in docs\ lists the restore command. Do not run it until it has been reviewed and approved.
) else (
  echo FAILED BEFORE ANY WRITE. The database was not modified.
)
echo The application was NOT restarted. Resume OneDrive only after deciding what to do.
pause
exit /b %RC%

:restart
echo.
echo === 3/3 All checks passed: restarting the application ===
echo Resume OneDrive sync now (tray icon ^> Resume syncing).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_app.ps1"
pause
