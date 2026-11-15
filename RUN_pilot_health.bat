@echo off
REM The pilot health report: today and the last 7 days, from the events table (codes only, no phone numbers).
REM Needs DATABASE_URL in .env. Saved to outputs\pilot\health_<date>.md (private folder, not committed).
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python -m bot.admin health
pause
