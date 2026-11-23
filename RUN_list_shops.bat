@echo off
REM Every shop: code, type, status, group, day joined (no phone numbers).
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python -m bot.admin list-shops
pause
