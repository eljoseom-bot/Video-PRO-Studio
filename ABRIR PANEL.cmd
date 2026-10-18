@echo off
setlocal
title Video PRO Studio  --  CIERRA ESTA VENTANA PARA APAGARLO
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Todavia no esta instalado. Haz doble clic en  INSTALAR.bat  primero.
  echo.
  pause
  exit /b 1
)

if not exist "work" mkdir "work"
set "LOG=work\panel.log"
set "URL=http://127.0.0.1:8010"

rem  ffmpeg instalado con winget: lo anade al PATH de esta ventana (por si Windows aun no lo ha refrescado)
for /f "delims=" %%F in ('dir /s /b "%LOCALAPPDATA%\Microsoft\WinGet\Packages\ffmpeg.exe" 2^>nul') do set "PATH=%%~dpF;%PATH%"

rem  Si ya hay un panel abierto, no lanza otro (dos paneles a la vez se pisan el estado de los trabajos): te abre el que ya esta.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{ $null = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8010' -TimeoutSec 3; exit 0 }catch{ exit 1 }"
if not errorlevel 1 goto YA_ABIERTO

if exist "%LOG%" move /y "%LOG%" "work\panel.prev.log" >nul 2>&1

echo ================================================================
echo   VIDEO PRO STUDIO
echo ----------------------------------------------------------------
echo   Se abre solo en:  %URL%
echo.
echo   Para apagarlo: cierra ESTA ventana, o usa el boton
echo   "Cerrar panel" de la pagina web.
echo   No queda nada corriendo en segundo plano.
echo ----------------------------------------------------------------
echo   Registro (por si algo falla):  %CD%\%LOG%
echo.
echo   Esta ventana queda muda a proposito: toda la salida va al
echo   registro (asi, si haces clic dentro, Windows no congela el panel).
echo ================================================================
echo.
echo   Arrancando... (el navegador se abre solo en cuanto responda)
echo.

rem  Espera hasta 90 s a que el panel responda y ENTONCES abre el navegador.
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='http://127.0.0.1:8010';for($i=0;$i -lt 180;$i++){try{ $null = Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 2;Start-Process $u;Write-Host '  >> Panel LISTO. Te lo he abierto en el navegador.' -Foreground Green;exit }catch{ Start-Sleep -Milliseconds 500 } };Write-Host '  >> El panel tarda mas de 90 s en responder. Abrelo tu mismo:  http://127.0.0.1:8010' -Foreground Yellow"

".venv\Scripts\python.exe" -u webapp.py >> "%LOG%" 2>&1
set "SALIDA=%ERRORLEVEL%"

echo.
if "%SALIDA%"=="0" goto FIN_NORMAL
echo ================================================================
echo   EL PANEL SE HA CERRADO CON ERROR (codigo %SALIDA%)
echo ----------------------------------------------------------------
echo   Ultimas lineas del registro:
echo.
powershell -NoProfile -Command "if(Test-Path 'work\panel.log'){Get-Content 'work\panel.log' -Tail 15 }else{Write-Host '  (no llego ni a escribir el registro)' }"
echo.
echo   Registro completo:  %CD%\%LOG%
echo ================================================================
echo.
pause
exit /b %SALIDA%

:FIN_NORMAL
echo   El panel se ha cerrado. No queda nada corriendo.
timeout /t 3 >nul
exit /b 0

:YA_ABIERTO
echo ================================================================
echo   YA HAY UN PANEL ABIERTO: te abro el que esta corriendo.
echo   Si no lo ves o va raro: cierra esta ventana, haz doble clic
echo   en "CERRAR PANEL.cmd" y vuelve a abrir este.
echo ================================================================
start "" "%URL%"
timeout /t 8 >nul
exit /b 0
