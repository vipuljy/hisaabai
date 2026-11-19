@echo off
REM Step 13: tricky bills, a safety test of bill reading (14 made-up photos, about Rs 5 of AI calls).
REM Photos: outputs\redteam\images\   Results: outputs\redteam\tricky_bills.csv
cd /d "%~dp0"
REM The live prompt (Render: BILL_PROMPT=v3); this overrides an older value in .env for this run only.
set BILL_PROMPT=v3
python src\13_tricky_bills.py
echo.
echo Paste the last line (TRICKY BILLS ...) and any WRONG rows to Claude.
pause
