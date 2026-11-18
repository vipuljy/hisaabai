@echo off
REM Double-click to submit the 3 WhatsApp templates (reminder, Sunday summary, owner reminder) to Meta.
REM Shows each message first and asks before sending. Never prints a token. Safe to run again.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" (
  echo No .env file found.
  pause
  exit /b 1
)
python scripts\create_templates.py
echo.
pause
