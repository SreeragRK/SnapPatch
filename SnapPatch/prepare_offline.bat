@echo off
REM Run this ONCE on a PC with internet. It downloads everything the build needs into the "deps"
REM folder next to this script (~70 MB). After that, build_exe.bat and build_installer.bat work
REM completely offline - nothing is fetched from the internet during a build.
setlocal
cd /d "%~dp0"
set "DEPS=%~dp0deps"
if not exist "%DEPS%" mkdir "%DEPS%"

echo [1/2] Downloading Python packages (wheels) for this PC's Python ...
python -m pip download -d "%DEPS%" pillow numpy imageio-ffmpeg pyinstaller || goto :fail

echo.
echo [2/2] Downloading the Inno Setup installer (free, makes SnapPatch-Setup.exe) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://jrsoftware.org/download.php/is.exe' -OutFile '%DEPS%\innosetup.exe'" || goto :fail

echo.
echo Done. The deps folder now contains everything. Copy the whole SnapPatch folder
echo (including deps) to any PC and the build scripts will run without internet.
dir /b "%DEPS%"
pause
exit /b 0

:fail
echo.
echo Download FAILED - see the messages above.
pause
exit /b 1
