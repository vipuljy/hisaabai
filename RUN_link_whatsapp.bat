@echo off
REM Double-click once. Links your WhatsApp account to the Vahi app so real messages reach the bot.
REM Safe to run again. Never shows your token.
cd /d "%~dp0"
python -m bot.link_whatsapp
echo.
pause
