@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "CLIENT_EXE=%ROOT%client\Secret Flasher Manaka Vibrator Coyote Client.exe"

if not exist "%CLIENT_EXE%" (
    echo ERROR: client EXE not found:
    echo %CLIENT_EXE%
    pause
    exit /b 1
)

start "" "%CLIENT_EXE%"
if errorlevel 1 (
    echo ERROR: failed to start the client.
    pause
    exit /b 1
)

exit /b 0
