@echo off
cd /d %~dp0
python app.py
if not errorlevel 1 goto :eof
set "HATALOG=hata.log"
for /f "usebackq delims=" %%i in (`python -c "import ayar;print(ayar.hata_log_yolu())" 2^>nul`) do set "HATALOG=%%i"
echo.
echo Uygulama hata verdi. Ayrinti: %HATALOG%
pause
