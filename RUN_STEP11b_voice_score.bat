@echo off
REM Double-click AFTER filling the true_ columns in outputs\eval\voice_results.csv. No API calls, no cost.
cd /d "%~dp0"
python src\11_voice_eval.py --score
echo.
echo Tell Claude: "voice scored" and paste the VOICE EVAL lines.
pause
