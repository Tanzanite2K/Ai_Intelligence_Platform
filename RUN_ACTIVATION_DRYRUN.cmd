@echo off
rem Rehearses the activation commands on a DISPOSABLE COPY of production (C:\FrontierAtlas-test). Production is hashed, never written.
rem Contacts the 11 selected public news/job sources. No LLM calls.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\activation_dryrun.py > docs\activation_dryrun_last.log 2>&1
type docs\activation_dryrun_last.log
echo.
pause
