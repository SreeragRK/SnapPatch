@echo off
REM Builds SnapPatch-Setup.exe - a one-click installer (Start menu + desktop shortcut, uninstaller).
REM Needs Inno Setup 6 (free). If it is missing this script installs it (deps folder, winget, or download).
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "OUT=%USERPROFILE%\SnapPatch"
set "LOG=%OUT%\build_installer.log"
if not exist "%OUT%" mkdir "%OUT%"
echo ==== %DATE% %TIME% ==== > "%LOG%"

REM --- the version always comes from snappatch.py, so installer and app can never disagree ---
set "VER="
for /f "tokens=2 delims==" %%v in ('findstr /B /C:"APP_VERSION = " snappatch.py') do set "VER=%%v"
set "VER=%VER:"=%"
set "VER=%VER: =%"
if not defined VER (
    echo ERROR: could not read APP_VERSION from snappatch.py
    goto :fail
)
echo Building SnapPatch version %VER%

echo.
echo [1/3] Building SnapPatch.exe ...
call build_exe.bat nopause
if not exist "%OUT%\SnapPatch.exe" (
    echo ERROR: SnapPatch.exe was not built - see the messages above.
    goto :fail
)
echo       OK - %OUT%\SnapPatch.exe

echo.
echo [2/3] Looking for Inno Setup ...
set "ISCC="
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
    if exist "%%~p" set "ISCC=%%~p"
)
if not defined ISCC if exist "%~dp0deps\innosetup.exe" (
    echo       Not installed. Installing OFFLINE from deps\innosetup.exe ^(current user, no admin^) ...
    "%~dp0deps\innosetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER
    for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
        if exist "%%~p" set "ISCC=%%~p"
    )
)
if not defined ISCC (
    echo       Not installed. Trying winget ...
    where winget >nul 2>&1 && winget install --id JRSoftware.InnoSetup -e --silent --accept-source-agreements --accept-package-agreements
    for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
        if exist "%%~p" set "ISCC=%%~p"
    )
)
if not defined ISCC (
    echo       winget not available or failed. Downloading Inno Setup from jrsoftware.org ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://jrsoftware.org/download.php/is.exe' -OutFile \"$env:TEMP\innosetup.exe\""
    if exist "%TEMP%\innosetup.exe" (
        echo       Installing Inno Setup for the current user ^(no admin needed^) ...
        "%TEMP%\innosetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER
        for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
            if exist "%%~p" set "ISCC=%%~p"
        )
    )
)
if not defined ISCC (
    echo ERROR: Inno Setup could not be installed automatically.
    echo        Install it once from https://jrsoftware.org/isdl.php  ^(choose "Install for me only"^)
    echo        and run build_installer.bat again.
    goto :fail
)
echo       OK - !ISCC!

echo.
echo [3/3] Compiling the installer (version %VER%) ...
if exist "%OUT%\SnapPatch-Setup.exe" del /f /q "%OUT%\SnapPatch-Setup.exe"
"!ISCC!" /Qp /DAppVersion=%VER% /DExeDir="%OUT%" /O"%OUT%" "%~dp0installer.iss" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Inno Setup reported a problem. Last lines of the log:
    powershell -NoProfile -Command "Get-Content -Tail 25 '%LOG%'"
    goto :fail
)
if not exist "%OUT%\SnapPatch-Setup.exe" (
    echo ERROR: SnapPatch-Setup.exe was not produced. Log: %LOG%
    goto :fail
)

echo.
echo ===========================================================
echo  Done.  SnapPatch %VER%  -  share this file with colleagues:
echo    %OUT%\SnapPatch-Setup.exe
echo.
echo  To update a PC that has an older version: just run this
echo  Setup there. It closes the old SnapPatch and replaces it.
echo ===========================================================
explorer "%OUT%"
pause
exit /b 0

:fail
echo.
echo Installer build FAILED. Full log: %LOG%
pause
exit /b 1
