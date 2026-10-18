"""Ingesta de video: URL de YouTube/Twitch o archivo local."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .util import log, run


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def ytdlp_cmd() -> list[str]:
    """Cómo lanzar yt-dlp: con el MISMO Python del programa (`-m yt_dlp`) si está instalado en su entorno —así no depende de que su carpeta Scripts esté en el PATH del panel
    (el panel que abre el acceso directo no la tiene: «No se encontró yt-dlp», 10-oct-2026)— y, si no, el `yt-dlp` del PATH."""
    import importlib.util
    import sys
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return ["yt-dlp"]


def fetch(source: str, workdir: Path) -> Path:
    """Devuelve la ruta al mp4 local. Descarga si es URL."""
    workdir.mkdir(parents=True, exist_ok=True)

    if not is_url(source):
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"No existe el archivo: {src}")
        log(f"Usando archivo local: {src.name}")
        return src

    # Si ya esta descargado, ni se llama a yt-dlp. Antes se llamaba igual (y
    # yt-dlp respondia "already downloaded"), lo que obligaba a tener yt-dlp y
    # a esperar una consulta a YouTube para nada. El generador de VIDEOS vive
    # de esta cache, asi que aqui se nota.
    for ext in (".mp4", ".mkv", ".webm"):
        ya = workdir / f"source{ext}"
        if ya.is_file() and ya.stat().st_size > 1_000_000:
            log(f"Descarga lista (estaba en caché): {ya.name}")
            return ya

    out = workdir / "source.%(ext)s"
    log(f"Descargando con yt-dlp: {source}")
    run([
        *ytdlp_cmd(),
        # YouTube 2026 exige un runtime JS para resolver formatos. yt-dlp solo
        # activa deno por defecto; aquí también aceptamos node (Node 22+).
        "--js-runtimes", "deno", "--js-runtimes", "node",
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", str(out),
        source,
    ])

    downloaded = sorted(workdir.glob("source.*"))
    downloaded = [p for p in downloaded if p.suffix in (".mp4", ".mkv", ".webm")]
    if not downloaded:
        raise RuntimeError("yt-dlp no produjo archivo de video")
    log(f"Descargado: {downloaded[0].name}")
    return downloaded[0]


def probe(video: Path) -> dict:
    """Metadatos del video: duración, resolución, fps."""
    out = run([
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video),
    ], capture=True)
    data = json.loads(out)

    vstream = next(
        (s for s in data["streams"] if s.get("codec_type") == "video"), None
    )
    if vstream is None:
        raise RuntimeError("El archivo no tiene pista de video")

    fps_raw = vstream.get("r_frame_rate", "30/1")
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except Exception:
        fps = 30.0

    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])

    return {
        "duration": float(data["format"]["duration"]),
        "width": int(vstream["width"]),
        "height": int(vstream["height"]),
        "fps": fps,
        "has_audio": has_audio,
        "path": str(video),
    }


def extract_audio(video: Path, workdir: Path) -> Path:
    """Audio mono 16 kHz WAV — lo que WHISPER quiere (no vale para las kills)."""
    wav = workdir / "audio.wav"
    if wav.exists():
        return wav
    log("Extrayendo audio a 16 kHz mono (para Whisper)...")
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(wav),
    ])
    return wav


def extract_audio_hifi(video: Path, workdir: Path, rate: int = 32000) -> Path:
    """Audio mono a 32 kHz para el ANÁLISIS de kills.

    El detector de kills mira la banda 3800-9000 Hz (el 'tin' agudo de la baja)
    y la planitud espectral. A 16 kHz el techo físico son 8000 Hz: le faltaba
    justo la parte de arriba de su banda principal, y encima llegaba dentro del
    filtro anti-aliasing. A 32 kHz el techo son 16000 Hz, con margen de sobra.
    """
    wav = workdir / f"audio{rate // 1000}.wav"
    if wav.exists():
        return wav
    log(f"Extrayendo audio a {rate // 1000} kHz mono (para detectar kills)...")
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", str(rate),
        "-c:a", "pcm_s16le",
        str(wav),
    ])
    return wav
