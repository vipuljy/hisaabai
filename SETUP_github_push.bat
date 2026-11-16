@echo off
REM Run ONCE, after SETUP_github.bat made the first commit and after you created the EMPTY repo
REM github.com/vipuljy/hisaabai (no README). A browser window may ask you to sign in to GitHub the first time.
cd /d "%~dp0"
git remote get-url origin >nul 2>nul
if errorlevel 1 git remote add origin https://github.com/vipuljy/hisaabai.git
git remote -v
git push -u origin main
echo.
echo If there is no error above: open https://github.com/vipuljy/hisaabai and the Actions tab.
echo The "tests" workflow should turn green in a few minutes. Tell Claude: "github done".
pause
