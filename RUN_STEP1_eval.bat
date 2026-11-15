@echo off
REM Double-click to run. Scores how well the AI reads the bills in data\eval\ (Phase 2, after discovery).
REM First time: it only CHECKS your labels and photos (free). Then it asks before spending on API calls.
cd /d "%~dp0"
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
echo.
python src\01_eval_bill_reading.py --check
if errorlevel 1 (
  echo.
  echo Fix the problems above, then run this file again.
  pause
  exit /b 1
)
echo.
set /p GO=Labels look good. Run the AI on 5 bills per run as a trial? (y/n):
if /i not "%GO%"=="y" goto end
python src\01_eval_bill_reading.py --limit 5
echo.
set /p ALL=Trial done. Run ALL bills now? Already-read bills are not charged again. (y/n):
if /i not "%ALL%"=="y" goto end
python src\01_eval_bill_reading.py
:end
pause
