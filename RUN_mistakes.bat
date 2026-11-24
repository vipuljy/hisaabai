@echo off
REM Everything owners sent that the bot did not understand (their words, numbers hidden), cards cancelled, fixes,
REM and which features each shop has used. Paste the output to Claude: each repeated miss becomes a rule (24 Nov).
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python -m bot.admin mistakes 14
pause
