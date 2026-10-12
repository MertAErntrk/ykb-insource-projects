@echo off
chcp 65001 >nul
REM derle.bat — BriefMind.exe uretir (PyInstaller). Bu klasorde calistir.
cd /d "%~dp0"

echo [1/5] Bagimliliklar (requirements.txt sabit surumler), PyInstaller ve Pillow kuruluyor (bankanin PyPI aynasi)...
pip install -r requirements.txt pyinstaller==6.22.3 pillow==11.3.0 -q
if errorlevel 1 ( echo pip kurulumu basarisiz & pause & exit /b 1 )

echo [2/5] logo1.jpeg -> logo.ico ...
python -c "from PIL import Image; im=Image.open('logo1.jpeg').convert('RGBA'); im.save('logo.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]); print('logo.ico hazir')"
if errorlevel 1 ( echo logo donusumu basarisiz & pause & exit /b 1 )

echo [3/5] exe derleniyor (birkac dakika)...
python -m PyInstaller --noconfirm --clean --onedir --windowed --name BriefMind --icon logo.ico ^
  --collect-all uiautomation --collect-all soundcard --collect-all sounddevice --collect-all soundfile ^
  --hidden-import win32com --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes ^
  --hidden-import PyQt5.QtSvg ^
  --hidden-import konusmaci --hidden-import stt --hidden-import ses --hidden-import saat --hidden-import terim ^
  --hidden-import duzelt --hidden-import kelime --hidden-import rag --hidden-import tarih --hidden-import metin ^
  --hidden-import uzun --hidden-import ses_izi --hidden-import cihaz ^
  --add-data "veri;veri" ^
  app.py
if errorlevel 1 ( echo derleme basarisiz & pause & exit /b 1 )

echo [4/5] calisma dosyalari exe'nin yanina kopyalaniyor...
copy /y logo1.jpeg dist\BriefMind\ >nul
copy /y logo.ico   dist\BriefMind\ >nul
if exist terimler xcopy /e /i /y terimler dist\BriefMind\terimler >nul
if exist config.json copy /y config.json dist\BriefMind\ >nul
if not exist dist\BriefMind\toplantilar mkdir dist\BriefMind\toplantilar

echo [5/5] duman testi: exe pencereyi ekransiz kuruyor, modulleri ve veri/ dosyalarini yokluyor...
if exist "%LOCALAPPDATA%\BriefMind\duman.txt" del "%LOCALAPPDATA%\BriefMind\duman.txt"
start /wait "" "dist\BriefMind\BriefMind.exe" --duman
set DUMAN=%errorlevel%
if exist "%LOCALAPPDATA%\BriefMind\duman.txt" type "%LOCALAPPDATA%\BriefMind\duman.txt"
if not "%DUMAN%"=="0" ( echo duman testi basarisiz ^(cikis kodu %DUMAN%^) & pause & exit /b 1 )

echo.
echo Hazir:  dist\BriefMind\BriefMind.exe
echo Klasorun tamamini (dist\BriefMind) istedigin yere tasi; masaustune kisayol olustur.
pause
