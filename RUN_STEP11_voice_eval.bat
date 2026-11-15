@echo off
REM Double-click. Reads every voice note in data\voice_test\ (ogg, opus, m4a, mp3, wav) with Sarvam, then sends the
REM transcript through the live bot (bills, payments, fixes...). Needs SARVAM_API_KEY (and ANTHROPIC_API_KEY for the
REM Haiku fallback) in .env.
REM Cost guard: at most 30 new notes per run; already-read notes are cached and free.
cd /d "%~dp0"
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements.txt
echo.
python src\11_voice_eval.py
echo.
echo Next: open outputs\eval\voice_results.csv, fill true_intent, true_amount, true_supplier
echo (what you MEANT; the lines and answers are in src\templates\voice_script_v2.csv), save as CSV,
echo then double-click RUN_STEP11b_voice_score.bat
pause
