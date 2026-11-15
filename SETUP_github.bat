@echo off
REM Run ONCE, when you are ready to put HisaabAI on GitHub. Safe to run again.
REM It turns this folder into a git repo, switches on the secret scan before every commit,
REM shows you exactly what will be committed, and asks before committing. It never pushes.
cd /d "%~dp0"
where git >nul 2>nul
if errorlevel 1 (
  echo Git is not installed. Install it with:  winget install --id Git.Git -e
  echo Then close this window and double-click this file again.
  pause
  exit /b 1
)
if not exist ".git" git init -b main
git config core.hooksPath .githooks
for /f "delims=" %%e in ('git config user.email') do set GEMAIL=%%e
if "%GEMAIL%"=="" (
  set /p GNAME=Your name for commits:
  set /p GEMAIL=Your GitHub email:
  call git config --global user.name "%%GNAME%%"
  call git config --global user.email "%%GEMAIL%%"
)
echo.
echo Scanning every file for keys, tokens, phone numbers and private data...
python scripts\secret_scan.py --all
if errorlevel 1 (
  echo Fix the lines above first. Nothing was committed.
  pause
  exit /b 1
)
REM GitHub only reads workflows from .github\workflows: copy the ready-made ones there
if not exist ".github\workflows" mkdir ".github\workflows"
copy /Y "deploy\github-workflows\*.yml" ".github\workflows\" >nul
git add -A
git update-index --chmod=+x .githooks/pre-commit
echo.
echo These files will be committed. Nothing from .env, data\ or discovery\private\ should be in the list:
git status --short
echo.
choice /M "Make the commit now"
if errorlevel 2 (
  echo Not committed. Nothing changed on GitHub.
  pause
  exit /b 0
)
git commit -m "HisaabAI: WhatsApp bookkeeping assistant for small shops"
echo.
echo Done locally. To put it online:
echo   1. github.com ^> New repository ^> name: hisaabai ^> do NOT add a README ^> Create
echo   2. Then run these two lines here:
echo        git remote add origin https://github.com/YOUR-USERNAME/hisaabai.git
echo        git push -u origin main
pause
