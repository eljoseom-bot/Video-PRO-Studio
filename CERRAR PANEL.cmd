@echo off
cd /d "%~dp0"
echo Cerrando el panel de Videos (Mi canal)...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$aqui = (Get-Location).Path;" ^
  "$r = $null;" ^
  "try { $r = Invoke-RestMethod -Method POST -Uri 'http://127.0.0.1:8010/apagar' -TimeoutSec 4 } catch { }" ^
  "if ($r -and -not $r.ok) { Write-Host ('  NO se cerro: ' + $r.error) -Foreground Yellow; exit 1 }" ^
  "Start-Sleep -Milliseconds 800;" ^
  "$q = Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='powershell.exe'\" | Where-Object { ($_.CommandLine -match 'webapp\.py' -or $_.CommandLine -match 'panel-siempre\.ps1') -and $_.CommandLine -like ('*' + $aqui + '*') };" ^
  "foreach ($p in $q) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }" ^
  "Write-Host '  Listo. No queda nada del programa corriendo.' -Foreground Green"

echo.
timeout /t 3 >nul
