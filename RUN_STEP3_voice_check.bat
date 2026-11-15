@echo off
REM Double-click to run. Scores how well the bot understands voice notes.
REM First: record the 25 lines in src\templates\voice_script.csv as WhatsApp voice notes,
REM save them as data\voice\V01.ogg ... V25.ogg, and put SARVAM_API_KEY in .env.
cd /d "%~dp0"
python -m pip install --quiet -r requirements.txt
echo.
python src\05_voice_check.py
echo.
pause
