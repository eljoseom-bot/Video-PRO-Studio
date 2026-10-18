@echo off
rem  Trae la ultima version del programa (solo funciona si lo instalaste con "git clone"; tus canales, claves y videos no se tocan).
cd /d "%~dp0"
if not exist ".git" (
  echo.
  echo   Esta copia viene de un .zip, asi que no se puede actualizar sola.
  echo   Descarga el .zip nuevo desde GitHub y copia ENCIMA de esta carpeta
  echo   (tus carpetas canales, mi_marca, mis_caras, musica_fondo, work, videos y el archivo .env se conservan).
  echo.
  pause
  exit /b 0
)
git pull
echo.
echo   Si dice "Already up to date" ya tienes la ultima. Si bajo cambios, cierra y vuelve a abrir el panel.
pause
