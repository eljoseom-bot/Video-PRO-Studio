"""Copia de seguridad de lo que NO va a Git.

Git (GitHub) guarda el código y la configuración. Aquí va lo demás, que no se
puede regenerar gratis o no se debe subir:
  - musica_fondo/      tus canciones (75 MB; no se redistribuyen)
  - transcripciones y planes de Gemini (work/<vídeo>/words.json, director_*.json):
    sin ellos, editar o rehacer un vídeo viejo costaría Whisper + créditos otra vez
  - work/taste_videos/ lo que el programa aprende de tus vídeos
  - videos/<vídeo>/manifest.json, videos.json, analisis_ia.json: títulos, capítulos,
    descripciones y prompts de miniatura ya generados
  - work/webui_jobs.json: la lista de trabajos del panel
  - work/yt_desc/: las descripciones ORIGINALES de tus vídeos de YouTube guardadas antes de
    cambiarlas (para poder deshacer) y qué vídeos ya se actualizaron

NO se copia por defecto (pesan GB o son secretos): los .mp4, los originales de
inbox/, el .venv, y .env / client_secret.json. Si quieres los secretos dentro
(cuidado con dónde guardas el .zip), usa --con-secretos.

Uso:
    python backup.py                      # a la carpeta por defecto
    python backup.py --destino "E:\\Copias"
    python backup.py --conservar 12 --con-secretos

Restaurar: descomprime el .zip encima de la carpeta del proyecto (las rutas dentro
del zip ya son relativas a ella).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
PREFIJO = "video-edit-pro_"


def _config_backup() -> dict:
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        return cfg.get("backup") or {}
    except Exception:  # noqa: BLE001 — sin config o sin PyYAML: valores por defecto
        return {}


def carpeta_por_defecto() -> Path:
    """OneDrive si existe (así la copia sale del disco); si no, Documentos."""
    for var in ("OneDrive", "OneDriveConsumer"):
        od = os.environ.get(var)
        if od and Path(od).is_dir():
            return Path(od) / "Copias de seguridad" / "video-edit-pro"
    return Path.home() / "Documents" / "video-edit-pro-backups"


def archivos_a_copiar(con_secretos: bool = False):
    """(ruta real, ruta dentro del zip) de todo lo que entra en la copia."""
    vistos: set[Path] = set()

    def _uno(p: Path):
        if p.is_file() and p not in vistos:
            vistos.add(p)
            return p, p.relative_to(ROOT).as_posix()
        return None

    musica = ROOT / "musica_fondo"
    if musica.is_dir():
        for p in sorted(musica.iterdir()):
            if p.suffix.lower() in AUDIO or p.name.lower() == "readme.md":
                if (r := _uno(p)):
                    yield r
    for nombre in ("config.yaml", "solo-local.flag"):
        if (r := _uno(ROOT / nombre)):
            yield r
    work = ROOT / "work"
    for patron in ("taste_videos/**/*", "webui_jobs.json", "*/words.json", "*/director_*.json", "*/vision.json",
                   "yt_desc/estado.json", "yt_desc/backup/*.json", "gemini_uso.jsonl", "uso_limites.json",
                   "reglas_director.json", "nombres_validados.json", "ideas/estado.json", "ideas/tanda_*.json", "ideas/perfil_*.json"):
        for p in sorted(work.glob(patron)):
            if (r := _uno(p)):
                yield r
    videos = ROOT / "videos"
    for patron in ("*/manifest.json", "*/videos.json", "*/analisis_ia.json"):
        for p in sorted(videos.glob(patron)):
            if (r := _uno(p)):
                yield r
    if con_secretos:
        for nombre in (".env", "client_secret.json"):
            if (r := _uno(ROOT / nombre)):
                yield r


def haz_copia(destino: Path, conservar: int = 8, con_secretos: bool = False) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    nombre = f"{PREFIJO}{time.strftime('%Y-%m-%d_%H%M%S')}.zip"
    final, tmp = destino / nombre, destino / (nombre + ".tmp")
    n, crudo = 0, 0
    try:
        with zipfile.ZipFile(tmp, "w") as z:
            for real, rel in archivos_a_copiar(con_secretos):
                # el audio ya viene comprimido: guardarlo tal cual ahorra tiempo
                modo = zipfile.ZIP_STORED if real.suffix.lower() in AUDIO else zipfile.ZIP_DEFLATED
                z.write(real, rel, compress_type=modo)
                n += 1
                crudo += real.stat().st_size
        os.replace(tmp, final)          # el zip aparece completo o no aparece
    finally:
        tmp.unlink(missing_ok=True)

    # retención: se borran solo NUESTROS zips más viejos que los últimos `conservar`
    viejos = sorted(destino.glob(f"{PREFIJO}*.zip"), reverse=True)[max(1, conservar):]
    for v in viejos:
        v.unlink(missing_ok=True)
    return {"zip": str(final), "archivos": n, "mb": round(final.stat().st_size / 1e6, 1),
            "mb_origen": round(crudo / 1e6, 1), "borradas": len(viejos),
            "con_secretos": bool(con_secretos)}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    cfg = _config_backup()
    ap = argparse.ArgumentParser(description="Copia de seguridad de lo que no va a Git.")
    ap.add_argument("--destino", help="carpeta donde guardar el .zip")
    ap.add_argument("--conservar", type=int, help="cuántas copias mantener (defecto 8)")
    ap.add_argument("--con-secretos", action="store_true",
                    help="incluye .env y client_secret.json (cuidado dónde guardas el zip)")
    a = ap.parse_args()

    destino = Path(a.destino or cfg.get("carpeta") or carpeta_por_defecto()).expanduser()
    conservar = a.conservar or int(cfg.get("conservar") or 8)
    try:
        r = haz_copia(destino, conservar, a.con_secretos)
    except OSError as exc:
        print(f"ERROR: no pude escribir la copia en {destino} ({exc}).\n"
              f"Prueba con otra carpeta:  python backup.py --destino \"D:\\Copias\"")
        return 1
    print(f"Copia lista: {r['zip']}")
    print(f"  {r['archivos']} archivos · {r['mb']} MB"
          + (f" · borré {r['borradas']} copia(s) antigua(s)" if r["borradas"] else ""))
    print("  Incluye: música, transcripciones y planes de Gemini, aprendizaje, manifests."
          + (" También .env y client_secret.json." if r["con_secretos"]
             else " NO incluye .env ni credenciales (la clave de Gemini se vuelve a pedir en AI Studio)."))
    print("  Para restaurar: descomprime el .zip encima de la carpeta del proyecto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
