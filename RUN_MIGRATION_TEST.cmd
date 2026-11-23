@echo off
rem Phase 7A: test the Alembic migrations on COPIES of data\platform.db in C:\FrontierAtlas-test (outside OneDrive).
rem The live database is only read (online backup API, read-only). Nothing is scheduled, no history is collected.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (echo Python venv not found ^(.venv^). & pause & exit /b 1)
".venv\Scripts\python.exe" -c "import alembic" 2>nul
if errorlevel 1 (
  echo Installing alembic==1.20.1 into .venv ^(pinned in requirements.txt^) ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q alembic==1.20.1 || (echo pip install failed. & pause & exit /b 1)
)
echo.
echo === 1/2 Migration test on copies ===
".venv\Scripts\python.exe" scripts\phase7a_migration_test.py %*
set MIGRATION_RC=%ERRORLEVEL%
echo.
echo === 2/2 Regression test suite ===
".venv\Scripts\python.exe" -m pytest -q -p no:cacheprovider --junitxml=docs\test-results-phase7a-windows.xml
set PYTEST_RC=%ERRORLEVEL%
echo.
echo Migration test exit code: %MIGRATION_RC%   Test suite exit code: %PYTEST_RC%
pause
