@echo off
REM Double-click to run. Checks every key and connection in .env, one line each.
REM Never prints a token, key or password: the output is safe to paste to Claude.
cd /d "%~dp0"
if not exist ".env" (
  echo No .env file found. Copy .env.example to .env and fill it in first.
  pause
  exit /b 1
)
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
echo.
python -m bot.check_setup
echo.
pause
