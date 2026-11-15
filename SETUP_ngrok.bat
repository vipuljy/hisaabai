@echo off
REM Double-click to run. One-time setup of ngrok (gives the bot a public link for Meta).
cd /d "%~dp0"

where ngrok >nul 2>nul
if errorlevel 1 (
  echo Step 1 of 2: installing ngrok from the Microsoft Store...
  echo If asked to accept the Store terms, type Y and press Enter.
  echo.
  winget install ngrok -s msstore
  echo.
  where ngrok >nul 2>nul
  if errorlevel 1 (
    echo Install finished, but this window cannot see ngrok yet.
    echo Close this window and double-click SETUP_ngrok.bat again.
    pause
    exit /b 0
  )
) else (
  echo Step 1 of 2: ngrok is already installed.
)

echo.
echo Step 2 of 2: connect ngrok to your free account.
echo   a. Sign up or log in at https://dashboard.ngrok.com
echo   b. Left menu: "Your Authtoken". Click Copy.
echo   c. Right-click in this window to paste it, then press Enter.
echo   (Keep this token private. Do not paste it into any chat.)
echo.
set /p NGROK_TOKEN=Paste your ngrok authtoken here:
if "%NGROK_TOKEN%"=="" (
  echo No token entered. Run this file again when you have it.
  pause
  exit /b 1
)
ngrok config add-authtoken %NGROK_TOKEN%
set NGROK_TOKEN=
echo.
echo Done. If you see "Authtoken saved", tell Claude: "ngrok ready".
pause
