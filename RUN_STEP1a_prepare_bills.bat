@echo off
REM Double-click after putting bill photos in data\raw\bills_inbox\
REM Renames them B001.jpg..., removes hidden metadata (GPS etc.), and starts data\eval\labels.csv
cd /d "%~dp0"
if not exist "data\raw\bills_inbox" mkdir "data\raw\bills_inbox"
python src\00_prepare_bills.py
echo.
pause
