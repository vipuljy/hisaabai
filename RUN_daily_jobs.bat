@echo off
REM Sends today's due reminders (and the weekly summary on Sundays) to TREATMENT shops.
REM Needs DATABASE_URL in .env and the two templates approved (docs\10_templates.md).
REM During the pilot this runs automatically once a day; this file is for checking or running it by hand.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python -m bot.jobs --dry-run
echo.
set /p GO=Send these for real? (y/n):
if /i "%GO%"=="y" python -m bot.jobs
pause
