@echo off
REM Add ONE pilot shop before the visit (23 Nov). The owner's number goes only into the database, never into a file.
REM Then the owner sends "hi" from their own phone. Run once per shop: S01, S02, S03 ...
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo.
echo Shop code: S01, S02 ... for real pilot shops.
echo            F01, F02 ... for family and friends (they never join the experiment or the case-study numbers).
set /p CODE=Shop code: 
echo Shop type: kirana, medical, hardware or other
set /p SEG=Shop type: 
set /p NUM=Owner's WhatsApp number (10 digits): 
set /p NAME=Shop name (optional, press Enter to skip): 
echo.
if "%NAME%"=="" (
  python -m bot.admin add-shop %CODE% %SEG% %NUM%
) else (
  python -m bot.admin add-shop %CODE% %SEG% %NUM% "%NAME%"
)
echo.
echo All shops now:
python -m bot.admin list-shops
pause
