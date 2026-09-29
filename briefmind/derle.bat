@echo off
chcp 65001 >nul
REM derle.bat — BriefMind.exe uretir (PyInstaller). Bu klasorde calistir.
cd /d "%~dp0"

echo [1/4] PyInstaller ve Pillow kuruluyor (bankanin PyPI aynasi)...
pip install pyinstaller pillow -q
if errorlevel 1 ( echo pip kurulumu basarisiz & pause & exit /b 1 )

echo [2/4] logo1.jpeg -> logo.ico ...
python -c "from PIL import Image; im=Image.open('logo1.jpeg').convert('RGBA'); im.save('logo.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]); print('logo.ico hazir')"
if errorlevel 1 ( echo logo donusumu basarisiz & pause & exit /b 1 )

echo [3/4] exe derleniyor (birkac dakika)...
python -m PyInstaller --noconfirm --clean --onedir --windowed --name BriefMind --icon logo.ico ^
  --collect-all uiautomation --collect-all soundcard --collect-all sounddevice --collect-all soundfile ^
  --hidden-import win32com --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes ^
  --hidden-import PyQt5.QtSvg ^
  app.py
if errorlevel 1 ( echo derleme basarisiz & pause & exit /b 1 )

echo [4/4] calisma dosyalari exe'nin yanina kopyalaniyor...
copy /y logo1.jpeg dist\BriefMind\ >nul
copy /y logo.ico   dist\BriefMind\ >nul
if exist sozluk.json copy /y sozluk.json dist\BriefMind\ >nul
if exist sozluk.txt  copy /y sozluk.txt  dist\BriefMind\ >nul
if exist config.json copy /y config.json dist\BriefMind\ >nul
if not exist dist\BriefMind\toplantilar mkdir dist\BriefMind\toplantilar

echo.
echo Hazir:  dist\BriefMind\BriefMind.exe
echo Klasorun tamamini (dist\BriefMind) istedigin yere tasi; masaustune kisayol olustur.
pause
