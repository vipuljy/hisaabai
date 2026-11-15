@echo off
REM Double-click to run AFTER putting DATABASE_URL (and optionally SUPABASE_URL + SUPABASE_SERVICE_KEY) in .env.
REM Loads the tables into Supabase, runs a quick test, and makes the private photo bucket.
REM Safe to run again any time: it never deletes your data.
cd /d "%~dp0"
if not exist ".env" (
  echo No .env file found. Copy .env.example to .env and fill it in first.
  pause
  exit /b 1
)
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
echo.
python -m bot.setup_db
echo.
pause
