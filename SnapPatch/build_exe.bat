@echo off
REM Builds SnapPatch.exe (no Python needed on the target PC).
REM Builds in a short temp folder because Windows paths over 260 characters make PyInstaller fail.
REM OFFLINE: if a "deps" folder exists next to this script (created by prepare_offline.bat),
REM          the packages are installed from there and nothing is downloaded.
setlocal
cd /d "%~dp0"
set "WORK=%TEMP%\SnapPatchBuild"
set "OUT=%USERPROFILE%\SnapPatch"

REM close a running SnapPatch first - otherwise the old exe is locked and cannot be replaced
taskkill /F /IM SnapPatch.exe >nul 2>&1

if exist "%~dp0deps\*.whl" (
    echo Installing packages OFFLINE from %~dp0deps ...
    python -m pip install --user --no-index --find-links "%~dp0deps" pillow numpy imageio-ffmpeg pyinstaller || goto :fail
) else (
    python -c "import PIL, numpy, imageio_ffmpeg, PyInstaller" 2>nul && (
        echo Packages already installed - no download needed.
    ) || (
        echo Downloading packages from PyPI ^(run prepare_offline.bat once to avoid this^) ...
        python -m pip install --user pillow numpy imageio-ffmpeg pyinstaller || goto :fail
    )
)

if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%" || goto :fail
copy /y "snappatch.py" "%WORK%\" >nul || goto :fail
copy /y "snappatch.ico" "%WORK%\" >nul || goto :fail

pushd "%WORK%"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name SnapPatch ^
    --icon snappatch.ico --add-data "snappatch.ico;." --collect-all imageio_ffmpeg ^
    --distpath "%OUT%" --workpath "%WORK%\build" --specpath "%WORK%" snappatch.py
set "ERR=%ERRORLEVEL%"
popd
if not "%ERR%"=="0" goto :fail

echo.
echo Done. SnapPatch.exe (version 3.5) is in: %OUT%
echo To make a one-click installer for colleagues, run build_installer.bat next.
if "%1"=="" explorer "%OUT%"
if "%1"=="" pause
exit /b 0

:fail
echo.
echo Build FAILED - see the messages above.
pause
exit /b 1
