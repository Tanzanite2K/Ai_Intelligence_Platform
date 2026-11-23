@echo off
rem Phase 7B Run A: full rehearsal on a COPY of data\platform.db. The live file is only read (hash, copy, read-only queries).
rem Backups: C:\FrontierAtlas-backups\phase7b-rehearsal-<stamp>   Work: C:\FrontierAtlas-test\phase7b-rehearsal-<stamp>
rem No need to stop the app or pause OneDrive. Nothing is registered, scheduled, crawled or enabled on the live database.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (echo Python venv not found ^(.venv^). & pause & exit /b 1)
".venv\Scripts\python.exe" -c "import alembic" 2>nul || (echo alembic missing: run RUN_MIGRATION_TEST.cmd first. & pause & exit /b 1)
echo === 1/2 Run A: rehearsal on a copy ===
".venv\Scripts\python.exe" scripts\phase7b_execute.py --target copy
set RUN_A_RC=%ERRORLEVEL%
echo.
echo === 2/2 Regression test suite ===
".venv\Scripts\python.exe" -m pytest -q -p no:cacheprovider --junitxml=docs\test-results-phase7b-runA-windows.xml
set PYTEST_RC=%ERRORLEVEL%
echo.
echo Run A exit code: %RUN_A_RC%   Test suite exit code: %PYTEST_RC%
pause
