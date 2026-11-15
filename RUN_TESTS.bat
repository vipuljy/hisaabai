@echo off
REM Double-click to run. Checks the bot works on this machine (no WhatsApp needed).
cd /d "%~dp0"
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
echo.
python -m pytest -q
echo.
echo If the last line says "passed" with no "failed", tell Claude: "tests pass".
pause
