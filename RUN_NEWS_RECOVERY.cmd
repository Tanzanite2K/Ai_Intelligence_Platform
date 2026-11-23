@echo off
rem Targeted recovery of the 16 dead-lettered news articles: checks, backup, confirmation, extraction, verification.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_news_recovery.ps1"
pause
