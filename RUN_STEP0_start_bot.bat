@echo off
REM Double-click to run. Starts the HisaabAI bot on http://localhost:8000
cd /d "%~dp0"
if not exist ".env" (
  echo No .env file found. Copy .env.example to .env and fill it in first.
  pause
  exit /b 1
)
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
echo.
echo Bot starting. Keep this window open. Press Ctrl+C to stop.
echo Check it works: open http://localhost:8000 in your browser.
python -m uvicorn bot.app:app --port 8000
pause
