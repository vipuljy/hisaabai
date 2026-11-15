@echo off
REM Double-click to run. Scores the FREE bill readers (PDF text, RapidOCR, Tesseract): no API key, no cost.
REM RapidOCR (the default photo engine) installs with requirements-ocr-extra.txt on the first run (~80 MB).
REM One-time Tesseract install (Windows): download the installer from
REM   https://github.com/UB-Mannheim/tesseract/wiki  and install to the default folder
REM   C:\Program Files\Tesseract-OCR\  (English is included). Then run this file.
REM Installed somewhere else? Set an environment variable TESSERACT_CMD to the full path of tesseract.exe.
REM First OCR run takes a while (about 10 s per photo per engine); later runs reuse the cache in data\eval\runs\.
cd /d "%~dp0"
echo Installing packages (first run only)...
python -m pip install --quiet -r requirements-ocr-extra.txt
echo.
python src\06_free_readers_eval.py
echo.
echo Tell Claude: "free readers done" and paste the table.
pause
