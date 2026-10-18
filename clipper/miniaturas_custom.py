"""Miniaturas propias que el autor sube a mano (las retoca en Photoshop) como
candidatas "A/B/C" junto a la que se genera sola. Cada clip guarda las suyas
en una carpeta aparte de la miniatura automática (que vive junto al .mp4,
con su mismo nombre) para no mezclarlas ni arriesgar que una limpieza de
disco borre la que el autor quiso conservar.

Nunca se sube tal cual el archivo que llega del navegador: YouTube exige
JPG/PNG/... de máximo 2MB, y una foto con capas/RGBA o un lado gigante lo
pasarían de largo sin avisar hasta el momento de publicar — mejor fallar
aquí, con un error claro, que fallar en pleno `Publisher._worker()`.
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

from .config import paths

_ID_OK = re.compile(r"^[0-9]{10,20}$")
MAX_BYTES = 2 * 1024 * 1024        # tope real de YouTube para miniaturas
MAX_LADO = 2048                    # nadie necesita más para una miniatura
MIN_LADO = 250                     # por debajo de esto YouTube ya la rechaza


def _carpeta(jid: str, archivo: str) -> Path:
    return paths()["outbox"] / jid / "miniaturas_custom" / Path(archivo).stem


def listar(jid: str, archivo: str) -> list[dict]:
    """Candidatas ya subidas para este clip, más nuevas primero."""
    carpeta = _carpeta(jid, archivo)
    if not carpeta.is_dir():
        return []
    out = [{"id": f.stem, "creado": f.stat().st_mtime}
           for f in carpeta.glob("*.jpg") if _ID_OK.match(f.stem)]
    out.sort(key=lambda d: -d["creado"])
    return out


def ruta(jid: str, archivo: str, cand_id: str) -> Path | None:
    """La ruta de una candidata por id, o None si no existe o el id es raro.
    Nunca se confía en un id que venga del cliente para construir una ruta
    sin antes comprobar que tiene la forma exacta que nosotros generamos."""
    if not _ID_OK.match(cand_id or ""):
        return None
    f = _carpeta(jid, archivo) / f"{cand_id}.jpg"
    return f if f.is_file() else None


def marca_preferida(jid: str, archivo: str, cand_id: str) -> bool:
    """La que el autor eligió con «Usar esta» (p. ej. una miniatura hecha con IA, clipper/miniaturas_ia.py): al programar
    sale YA elegida, sin descargar ni subir nada."""
    if not ruta(jid, archivo, cand_id):
        return False
    (_carpeta(jid, archivo) / "preferida.txt").write_text(cand_id, encoding="utf-8")
    return True


def preferida(jid: str, archivo: str) -> str | None:
    try:
        v = (_carpeta(jid, archivo) / "preferida.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v if ruta(jid, archivo, v) else None


def guardar(jid: str, archivo: str, datos: bytes) -> dict:
    """Valida y normaliza (RGB, orientación EXIF, tamaño, peso) una miniatura
    subida a mano, y la guarda como candidata nueva. {"ok": True, "id": ...}
    o {"ok": False, "error": "..."}."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(datos))
        img.load()
    except (UnidentifiedImageError, OSError):
        return {"ok": False, "error": "Ese archivo no es una imagen que se pueda abrir."}

    img = ImageOps.exif_transpose(img) or img
    if img.mode != "RGB":
        img = img.convert("RGB")
    if min(img.size) < MIN_LADO:
        return {"ok": False, "error": f"Muy pequeña ({img.size[0]}x{img.size[1]}px) — "
                "sube una miniatura más grande (YouTube pide 1280x720 o más)."}
    if max(img.size) > MAX_LADO:
        img.thumbnail((MAX_LADO, MAX_LADO), Image.LANCZOS)

    calidad = 92
    buf = io.BytesIO()
    while True:
        buf.seek(0)
        buf.truncate(0)
        img.save(buf, format="JPEG", quality=calidad)
        if buf.tell() <= MAX_BYTES or calidad <= 40:
            break
        calidad -= 12
    if buf.tell() > MAX_BYTES:
        return {"ok": False, "error": "La imagen pesa demasiado incluso comprimida. Prueba con otra."}

    carpeta = _carpeta(jid, archivo)
    carpeta.mkdir(parents=True, exist_ok=True)
    cand_id = str(int(time.time() * 1000))
    destino = carpeta / f"{cand_id}.jpg"
    while destino.exists():          # dos subidas en el mismo milisegundo: casi imposible, pero...
        cand_id = str(int(cand_id) + 1)
        destino = carpeta / f"{cand_id}.jpg"
    destino.write_bytes(buf.getvalue())
    return {"ok": True, "id": cand_id}


def prueba() -> list[dict]:
    import shutil
    import tempfile
    from unittest.mock import patch

    from PIL import Image

    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    tmp = Path(tempfile.mkdtemp(prefix="zz_miniaturas_custom_"))
    try:
        with patch("clipper.miniaturas_custom.paths", lambda: {"outbox": tmp}):
            jid, arch = "zz-job", "clip.mp4"

            r("sin candidatas al principio", listar(jid, arch) == [])

            buf = io.BytesIO()
            Image.new("RGB", (1280, 720), (200, 80, 10)).save(buf, format="PNG")
            res = guardar(jid, arch, buf.getvalue())
            r("guarda una PNG válida como candidata JPEG", res.get("ok") is True and "id" in res, res)
            cid = res.get("id", "")

            l1 = listar(jid, arch)
            r("aparece en el listado tras guardarla", len(l1) == 1 and l1[0]["id"] == cid, l1)

            p = ruta(jid, arch, cid)
            r("ruta() resuelve un id real y pesa dentro del tope",
              p is not None and p.is_file() and p.stat().st_size <= MAX_BYTES,
              p.stat().st_size if p else None)

            r("ruta() rechaza un id con pinta de traversal", ruta(jid, arch, "../../../etc/passwd") is None)
            r("ruta() rechaza un id inventado que no existe", ruta(jid, arch, "9999999999") is None)

            res2 = guardar(jid, arch, b"esto no es una imagen")
            r("rechaza un archivo que no es imagen", res2.get("ok") is False, res2)

            buf3 = io.BytesIO()
            Image.new("RGB", (100, 60), (0, 0, 0)).save(buf3, format="JPEG")
            res3 = guardar(jid, arch, buf3.getvalue())
            r("rechaza una miniatura demasiado pequeña", res3.get("ok") is False, res3)

            buf4 = io.BytesIO()
            Image.new("RGB", (4000, 2200), (10, 120, 200)).save(buf4, format="PNG")
            res4 = guardar(jid, arch, buf4.getvalue())
            p4 = ruta(jid, arch, res4.get("id", "")) if res4.get("ok") else None
            r("una imagen enorme se reduce y queda dentro del tope de peso",
              res4.get("ok") is True and p4 is not None and p4.stat().st_size <= MAX_BYTES,
              p4.stat().st_size if p4 else res4)

            r("dos clips distintos del mismo trabajo no comparten candidatas",
              listar(jid, "otro.mp4") == [])

            # «Usar esta» de una miniatura hecha con IA (el autor, 2026-09-28): sale ya elegida al programar
            r("la preferida: solo una candidata que existe, y se recuerda",
              marca_preferida(jid, arch, res4["id"]) and preferida(jid, arch) == res4["id"]
              and not marca_preferida(jid, arch, "9999999999") and preferida(jid, "otro.mp4") is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
