# Instalador de Video PRO Studio (Windows). Solo ASCII a proposito: PowerShell 5.1 estropea las tildes de un .ps1 sin BOM.
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Paso($t)  { Write-Host ''; Write-Host ('>> ' + $t) -ForegroundColor Cyan }
function Ok($t)    { Write-Host ('   OK  ' + $t) -ForegroundColor Green }
function Aviso($t) { Write-Host ('   !!  ' + $t) -ForegroundColor Yellow }
function Falla($t) { Write-Host ''; Write-Host ('   ERROR: ' + $t) -ForegroundColor Red; exit 1 }

Write-Host '=====================================================' -ForegroundColor Cyan
Write-Host '   VIDEO PRO STUDIO - instalacion (unos minutos)'      -ForegroundColor Cyan
Write-Host '=====================================================' -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. Python 3.11 - 3.14
Paso '1/5  Buscando Python (3.11 a 3.14)'
$py = $null
foreach ($v in '3.12', '3.13', '3.11', '3.14') {
    try { $ruta = (& py "-$v" -c 'import sys; print(sys.executable)' 2>$null); if ($LASTEXITCODE -eq 0 -and $ruta) { $py = $ruta.Trim(); break } } catch { }
}
if (-not $py) {
    try {
        $ver = (& python -c 'import sys; print(sys.version_info.major*100+sys.version_info.minor)' 2>$null)
        if ($ver -and [int]$ver -ge 311 -and [int]$ver -le 314) { $py = (& python -c 'import sys; print(sys.executable)').Trim() }
    } catch { }
}
if (-not $py) {
    Aviso 'No encuentro Python 3.11-3.14. Lo instalo con winget (Python 3.12, gratis y oficial).'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Falla 'No tienes winget. Instala Python 3.12 desde https://www.python.org/downloads/ (marca "Add python.exe to PATH") y vuelve a abrir INSTALAR.bat.' }
    & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Write-Host ''
    Aviso 'Python instalado. CIERRA esta ventana y vuelve a hacer doble clic en INSTALAR.bat para seguir.'
    exit 0
}
Ok ('Python: ' + $py)

# ---------------------------------------------------------------- 2. ffmpeg
Paso '2/5  Buscando ffmpeg (el programa que corta y monta el video)'
function Busca-Ffmpeg {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { return $true }
    $cand = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter 'ffmpeg.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $env:Path = $cand.DirectoryName + ';' + $env:Path; return $true }
    return $false
}
if (-not (Busca-Ffmpeg)) {
    Aviso 'No tienes ffmpeg. Lo instalo con winget (gratis).'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Falla 'No tienes winget. Instala ffmpeg a mano: https://www.gyan.dev/ffmpeg/builds/ (y anade su carpeta bin al PATH).' }
    & winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if (-not (Busca-Ffmpeg)) { Falla 'ffmpeg se instalo pero no lo encuentro. Cierra esta ventana y vuelve a abrir INSTALAR.bat.' }
}
Ok 'ffmpeg listo'

# ---------------------------------------------------------------- 3. entorno + librerias
Paso '3/5  Creando el entorno e instalando las librerias (la parte mas larga)'
if (-not (Test-Path '.venv\Scripts\python.exe')) { & $py -m venv .venv; if ($LASTEXITCODE -ne 0) { Falla 'No pude crear el entorno (.venv).' } }
$vpy = (Resolve-Path '.venv\Scripts\python.exe').Path
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Falla 'Fallo la instalacion de librerias. Revisa tu conexion y vuelve a abrir INSTALAR.bat (continua donde se quedo).' }
Ok 'librerias instaladas'

# ---------------------------------------------------------------- 4. GPU (opcional)
Paso '4/5  Tarjeta grafica NVIDIA (hace la transcripcion mucho mas rapida)'
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Ok 'Detecte una NVIDIA: instalo sus librerias (~1,5 GB)'
    & $vpy -m pip install -r requirements-gpu.txt
    if ($LASTEXITCODE -ne 0) { Aviso 'No pude instalar las librerias de la GPU: el programa funciona igual, pero la transcripcion ira por CPU (mas lenta).' } else { Ok 'GPU lista' }
} else {
    Aviso 'No detecte una NVIDIA: todo funciona igual, solo que transcribir y renderizar tarda mas.'
}

# ---------------------------------------------------------------- 5. carpetas y acceso directo
Paso '5/5  Creando tus carpetas'
foreach ($d in 'work', 'videos', 'inbox', 'canales', 'mi_marca', 'mis_caras', 'musica_fondo') { New-Item -ItemType Directory -Force -Path $d | Out-Null }
Ok 'carpetas listas'
try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Video PRO Studio.lnk'))
    $lnk.TargetPath = (Join-Path $PSScriptRoot 'ABRIR PANEL.cmd')
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.Save()
    Ok 'acceso directo "Video PRO Studio" en tu escritorio'
} catch { Aviso 'No pude crear el acceso directo (no pasa nada: usa "ABRIR PANEL.cmd").' }

Write-Host ''
Write-Host '=====================================================' -ForegroundColor Green
Write-Host '   LISTO. Ahora haz doble clic en "ABRIR PANEL.cmd"'   -ForegroundColor Green
Write-Host '   (o en el acceso directo del escritorio).'           -ForegroundColor Green
Write-Host '   La primera vez te guia paso a paso para configurar' -ForegroundColor Green
Write-Host '   TU canal, tus claves y tu YouTube.'                 -ForegroundColor Green
Write-Host '=====================================================' -ForegroundColor Green
