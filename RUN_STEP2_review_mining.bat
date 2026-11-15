@echo off
REM Double-click to run. Downloads Play Store reviews of Khatabook, OkCredit, Vyapar, myBillBook
REM and ranks what small-business owners complain about. Takes about 10-30 minutes. Keep this window open.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
echo.
python src\03_scrape_bookkeeping_reviews.py
if errorlevel 1 (
  echo.
  echo Download failed. Copy the lines above and send them to Claude.
  pause
  exit /b 1
)
python src\04_review_pains.py
pause
