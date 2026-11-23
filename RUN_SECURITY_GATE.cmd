@echo off
rem Final security gate: credential check + activation preflight. No production writes.
rem Stop the app first (scripts\stop_app.ps1) so the preflight can confirm no writer.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_security_gate.ps1"
echo.
pause
