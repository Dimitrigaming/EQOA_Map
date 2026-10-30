@echo off
rem Builds NorrathFieldMap.exe in the "dist" folder.
rem Keep "maps", quests.json, guides.json, atlas.json, places.json, pins.json and settings.json next to the exe (or download maps from inside it).
cd /d "%~dp0"
python -m pip install -q -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name NorrathFieldMap ^
  --collect-submodules scapy --hidden-import psutil eqoa_map.py
echo.
copy /y quests.json dist\ >nul
copy /y places.json dist\ >nul
copy /y guides.json dist\ >nul
copy /y atlas.json dist\ >nul
echo Done. Your app is in: %~dp0dist\NorrathFieldMap.exe
pause
