@echo off
REM Split the pilot shops into treatment / control, ONCE, after the last shop is onboarded (23 Nov).
REM Groups never change after this (docs/09_experiment.md). Test shops (T01 ...) are left out.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo Shops now:
python -m bot.admin list-shops
echo.
echo This splits the pilot shops into two groups and can NOT be undone.
set /p OK=Are ALL pilot shops onboarded? Type YES to split: 
if /i not "%OK%"=="YES" (
  echo Nothing changed.
  pause
  exit /b
)
python -m bot.admin assign-groups
pause
