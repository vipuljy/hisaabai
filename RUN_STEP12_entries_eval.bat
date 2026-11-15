@echo off
REM Double-click. How well does the text engine understand what owners want to RECORD (bills, payments, fixes)?
REM Rules only: no AI, no cost, no internet. Three frozen message sets (data\eval\): main (79, written before the
REM engine), blind (70) and blind2 (80), each written by a separate Claude that never saw the code.
cd /d "%~dp0"
python src\12_entries_eval.py
echo.
python src\12_entries_eval.py --set blind
echo.
python src\12_entries_eval.py --set blind2
echo.
echo Tell Claude: "step 12 done" and paste the ALL lines.
pause
