@echo off
REM Double-click to chat with the bot in this window, like a shop owner on WhatsApp. No Meta needed.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
cls
python -m bot.simulate
pause
