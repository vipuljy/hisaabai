@echo off
REM Double-click to upload your latest changes to GitHub (Render redeploys from GitHub by itself).
REM The secret scan runs before the commit, so nothing from .env, data\ or discovery\private\ can go up.
cd /d "%~dp0"
where git >nul 2>nul || (echo Git is not installed: run SETUP_github.bat first. & pause & exit /b 1)
if not exist ".git" (echo Run SETUP_github.bat first. & pause & exit /b 1)
git add -A
git diff --cached --quiet && (echo Nothing new to upload. & pause & exit /b 0)
echo These files changed:
git status --short
echo.
set MSG=
set /p MSG=One line on what changed (Enter for "Update"): 
if "%MSG%"=="" set MSG=Update
git commit -m "%MSG%"
if errorlevel 1 (echo Not uploaded: see the lines above. & pause & exit /b 1)
git push
echo.
echo Done: https://github.com/vipuljy/hisaabai  (Actions tab: "tests" should turn green)
pause
