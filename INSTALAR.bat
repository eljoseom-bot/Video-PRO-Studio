@echo off
rem  Doble clic aqui para instalar Video PRO Studio (solo la primera vez).
rem  Prepara Python, ffmpeg y las librerias, y crea las carpetas. No toca nada fuera de esta carpeta
rem  (salvo instalar Python/ffmpeg con winget si no los tienes, y siempre te lo dice antes).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
echo.
pause
