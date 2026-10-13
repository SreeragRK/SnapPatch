@echo off
REM Starts SnapPatch with the same Python that has Pillow + numpy + imageio-ffmpeg installed.
cd /d "%~dp0"
python -c "import PIL, numpy, imageio_ffmpeg" 2>nul || (
    if exist "%~dp0deps\*.whl" (
        python -m pip install --user --no-index --find-links "%~dp0deps" pillow numpy imageio-ffmpeg
    ) else (
        python -m pip install --user pillow numpy imageio-ffmpeg
    )
)
set "PYW="
for /f "usebackq delims=" %%i in (`python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"`) do set "PYW=%%i"
if defined PYW if exist "%PYW%" (
    start "" "%PYW%" snappatch.py %*
    exit /b 0
)
start "" python snappatch.py %*
