@echo off
setlocal EnableExtensions

rem This launcher is ASCII-only for maximum cmd.exe compatibility.
set "ROOT=%~dp0"
set "CLIENT_DIR=%ROOT%client"
set "CLIENT_EXE=Secret Flasher Manaka Vibrator Coyote Client.exe"
set "GAME_EXE=SecretFlasherManaka.exe"

if not exist "%CLIENT_DIR%\%CLIENT_EXE%" goto client_missing
if not exist "%ROOT%%GAME_EXE%" goto game_missing

echo Starting client...
pushd "%CLIENT_DIR%"
start "" "%CLIENT_EXE%"
if errorlevel 1 (
    popd
    goto client_failed
)
popd

timeout /t 3 /nobreak >nul

echo Starting game...
pushd "%ROOT%"
start "" "%GAME_EXE%"
if errorlevel 1 (
    popd
    goto game_failed
)
popd
exit /b 0

:client_missing
echo ERROR: client EXE not found:
echo %CLIENT_DIR%\%CLIENT_EXE%
pause
exit /b 1

:game_missing
echo ERROR: game EXE not found:
echo %ROOT%%GAME_EXE%
pause
exit /b 1

:client_failed
echo ERROR: failed to start the client.
pause
exit /b 1

:game_failed
echo ERROR: failed to start the game.
pause
exit /b 1
