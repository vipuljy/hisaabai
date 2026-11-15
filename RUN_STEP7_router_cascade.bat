@echo off
REM Double-click AFTER RUN_STEP1_eval.bat (Claude runs haiku_v2 + sonnet_v2) and RUN_STEP6_free_readers.bat.
REM Step 7 trains the router (Haiku or Sonnet per photo) with grouped cross-validation; step 8 scores the cascade.
REM Uses cached results only: no API calls, no cost. Fill prices + usd_to_inr in src\eval_config.json for the cost column.
cd /d "%~dp0"
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements-ocr-extra.txt
echo.
python src\07_router.py
if errorlevel 1 goto end
echo.
python src\08_cascade_eval.py
:end
echo.
echo Tell Claude: "cascade done" and paste the two tables.
pause
