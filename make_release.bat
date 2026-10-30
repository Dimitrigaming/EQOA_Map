@echo off
rem Builds the exe and dist\NorrathFieldMap.zip: the file players download from GitHub Releases.
rem The zip holds the exe, the four data files, README.txt and the maps (never your pins or settings).
cd /d "%~dp0"
tasklist /fi "imagename eq NorrathFieldMap.exe" 2>nul | find /i "NorrathFieldMap.exe" >nul && (
  echo Close Norrath Field Map first ^(the exe can't be rebuilt while it runs^), then run this again.
  pause
  exit /b 1
)
python -m pip install -q -r requirements.txt pyinstaller || goto :fail
python -m PyInstaller --noconfirm --onefile --windowed --name NorrathFieldMap ^
  --collect-submodules scapy --hidden-import psutil eqoa_map.py || goto :fail
if exist release rmdir /s /q release
mkdir release
copy /y dist\NorrathFieldMap.exe release\ >nul
copy /y quests.json release\ >nul
copy /y guides.json release\ >nul
copy /y places.json release\ >nul
copy /y atlas.json release\ >nul
copy /y README.txt release\ >nul
echo Adding the maps:
python release_maps.py release || goto :fail
if exist dist\NorrathFieldMap.zip del dist\NorrathFieldMap.zip
echo Zipping (a few minutes with the maps)...
powershell -NoProfile -Command "Compress-Archive -Path release\* -DestinationPath dist\NorrathFieldMap.zip -CompressionLevel Fastest" || goto :fail
rmdir /s /q release
copy /y quests.json dist\ >nul
copy /y guides.json dist\ >nul
copy /y places.json dist\ >nul
copy /y atlas.json dist\ >nul
echo.
echo Done. Upload this file to a new GitHub release:
echo   %~dp0dist\NorrathFieldMap.zip
explorer /select,"%~dp0dist\NorrathFieldMap.zip"
pause
exit /b 0
:fail
echo.
echo Something went wrong (see above). Nothing was zipped.
pause
exit /b 1
