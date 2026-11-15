@echo off
REM Double-click to run AFTER RUN_STEP0_start_bot.bat is running.
REM Gives the bot a public https link that Meta can call. Needs ngrok installed (ngrok.com, free).
cd /d "%~dp0"
where ngrok >nul 2>nul
if errorlevel 1 (
  echo ngrok is not installed or not on PATH.
  echo 1. Sign up free at https://ngrok.com
  echo 2. Install it, then run once:  ngrok config add-authtoken YOUR_NGROK_TOKEN
  echo 3. Double-click this file again.
  pause
  exit /b 1
)
echo Copy the https://....ngrok-free.app link shown below.
echo In Meta: WhatsApp ^> Configuration ^> Callback URL = that link + /webhook
echo.
ngrok http 8000
pause
