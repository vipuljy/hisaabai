@echo off
REM Speed test (21 Nov): can the AI read bills faster by thinking less, without reading worse?
REM Haiku: about Rs 10-15 (the 136 bills at effort low and off; the default is already cached, free).
REM The rule is set before the run (docs/08_eval.md): adopt only if accuracy holds and silent errors do not rise.
cd /d "%~dp0"
python src\14_effort_speed.py --model haiku
if errorlevel 1 goto end
echo.
set /p TRICKY=Safety check: run the 14 tricky bills at effort low too (about Rs 5)? (y/n):
if /i "%TRICKY%"=="y" (
  set BILL_PROMPT=v3
  set BILL_EFFORT=low
  python src\13_tricky_bills.py
)
echo.
set /p SON=Also test Sonnet (the re-read) at medium effort (about Rs 90)? (y/n):
if /i "%SON%"=="y" python src\14_effort_speed.py --model sonnet
:end
echo.
echo Paste every line that starts with SPEED TEST (and TRICKY BILLS) to Claude.
pause
