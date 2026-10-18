"""Tu marca personal: tu retrato, tu logo y tus caras (las subes desde el asistente de tu canal; nada de esto sale de tu PC).

  · `mi_marca/retrato.webp` y `mi_marca/logo.webp`: los usan los carruseles y los posts (portadas con tu retrato o tu logo, sello en el pie).
  · `mis_caras/cara <gesto> mirando hacia <izquierda|derecha|frente>.png`: recortes con FONDO TRANSPARENTE para las miniaturas, los carruseles y los posts.
    El gesto y la dirección salen del nombre del archivo (`clipper/miniaturas_ia.py`), así que aquí se guardan con ese nombre.
Si faltan, esas piezas simplemente no salen (nunca rompen nada).
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from . import config

GESTOS = ("feliz", "sorpresa", "pensando", "decepcion", "asco", "triste", "what")
MIRADAS = ("izquierda", "derecha", "frente")
MAX_LADO = 1400
MAX_BYTES = 12 * 1024 * 1024


def _carpetas(raiz: Path | None = None) -> tuple[Path, Path, Path]:
    r = Path(raiz) if raiz else config.ROOT
    return r / "mi_marca", r / "mis_caras", r / "musica_fondo"


def _abre(datos: bytes):
    from PIL import Image
    if not datos or len(datos) > MAX_BYTES:
        raise ValueError("El archivo está vacío o pesa más de 12 MB.")
    try:
        im = Image.open(io.BytesIO(datos))
        im.load()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Eso no parece una imagen (usa PNG, JPG o WEBP).") from exc
    return im


def estado(raiz: Path | None = None) -> dict:
    """Qué hay subido: retrato, logo, las caras (con su gesto y su mirada) y cuántas canciones hay."""
    marca, caras, musica = _carpetas(raiz)
    from .miniaturas_ia import gesto_de
    lista = []
    for f in sorted(caras.glob("*.png")) if caras.is_dir() else []:
        g, m = gesto_de(f.stem)
        lista.append({"nombre": f.stem, "archivo": f.name, "gesto": g, "mira": m or "frente"})
    canciones = [f for f in musica.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")] if musica.is_dir() else []
    return {"retrato": (marca / "retrato.webp").is_file(), "logo": (marca / "logo.webp").is_file(), "caras": lista,
            "gestos": list(GESTOS), "miradas": list(MIRADAS), "canciones": len(canciones)}


def guarda_imagen(tipo: str, datos: bytes, raiz: Path | None = None) -> tuple[bool, str]:
    """Guarda tu retrato o tu logo (`tipo` = retrato | logo) como WEBP. -> (ok, mensaje)."""
    if tipo not in ("retrato", "logo"):
        return False, "Tipo de imagen desconocido."
    try:
        im = _abre(datos).convert("RGBA")
    except ValueError as exc:
        return False, str(exc)
    from PIL import Image
    if max(im.size) > 1024:
        im.thumbnail((1024, 1024), Image.LANCZOS)
    marca, _c, _m = _carpetas(raiz)
    marca.mkdir(parents=True, exist_ok=True)
    tmp = marca / f".{tipo}.tmp.webp"
    im.save(tmp, "WEBP", quality=92)
    tmp.replace(marca / f"{tipo}.webp")
    return True, "Guardado."


def guarda_cara(gesto: str, mira: str, datos: bytes, raiz: Path | None = None) -> tuple[bool, str]:
    """Guarda un recorte de tu cara (PNG con fondo transparente) con el nombre que entiende el programa. -> (ok, mensaje)."""
    gesto = (gesto or "").strip().lower()
    if gesto not in GESTOS:
        return False, "Elige uno de los gestos de la lista."
    if mira not in MIRADAS:
        return False, "Elige hacia dónde mira."
    try:
        im = _abre(datos).convert("RGBA")
    except ValueError as exc:
        return False, str(exc)
    alfa = im.getchannel("A")
    hist = alfa.histogram()
    transparentes = sum(hist[:200]) / max(1, im.width * im.height)
    if transparentes < 0.08:
        return False, ("Esta foto NO tiene el fondo transparente: tiene que ser un recorte de TU CARA (o de tu cuerpo) en PNG sin fondo. "
                       "Puedes quitarle el fondo gratis en remove.bg, en Canva o con Photoshop y volver a subirla.")
    from PIL import Image
    caja = alfa.point(lambda v: 255 if v > 24 else 0).getbbox()
    if caja:
        im = im.crop(caja)                               # recortada a la silueta: el programa la coloca sola
    if max(im.size) > MAX_LADO:
        im.thumbnail((MAX_LADO, MAX_LADO), Image.LANCZOS)
    _m, caras, _mu = _carpetas(raiz)
    caras.mkdir(parents=True, exist_ok=True)
    nombre = f"cara {gesto} mirando hacia {'la ' if mira != 'frente' else 'el '}{mira}"
    n, destino = 1, caras / f"{nombre}.png"
    while destino.is_file():                             # ya hay una igual: otra más (el programa las mezcla)
        n += 1
        destino = caras / f"{nombre} {n}.png"
    tmp = caras / ".cara.tmp.png"
    im.save(tmp, "PNG", optimize=True)
    tmp.replace(destino)
    return True, f"Guardada como «{destino.stem}»."


def borra_cara(archivo: str, raiz: Path | None = None) -> bool:
    _m, caras, _mu = _carpetas(raiz)
    nombre = Path(str(archivo or "")).name
    if not nombre.lower().endswith(".png") or nombre.startswith("."):
        return False
    f = caras / nombre
    if f.is_file() and f.resolve().parent == caras.resolve():
        f.unlink()
        return True
    return False


def ruta_publica(carpeta: str, archivo: str, raiz: Path | None = None) -> Path | None:
    """El archivo a servir para verlo en el panel (solo de `mi_marca` o `mis_caras`, sin salirse de ahí)."""
    marca, caras, _mu = _carpetas(raiz)
    base = {"marca": marca, "caras": caras}.get(carpeta)
    nombre = Path(str(archivo or "")).name
    if base is None or not nombre or nombre.startswith("."):
        return None
    f = base / nombre
    return f if f.is_file() and f.resolve().parent == base.resolve() else None


# ------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    import tempfile

    from PIL import Image, ImageDraw
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)[:300]})

    def png(transparente: bool, lado: int = 300) -> bytes:
        im = Image.new("RGBA", (lado, lado), (0, 0, 0, 0) if transparente else (200, 120, 60, 255))
        ImageDraw.Draw(im).ellipse([lado // 4, lado // 4, lado * 3 // 4, lado * 3 // 4], fill=(240, 200, 170, 255))
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        e0 = estado(t)
        caso("sin nada subido: ni retrato ni logo ni caras (y no revienta aunque no existan las carpetas)", not e0["retrato"] and not e0["logo"] and e0["caras"] == [] and e0["canciones"] == 0)
        ok, msg = guarda_imagen("retrato", png(False, 1500), t)
        with Image.open(t / "mi_marca" / "retrato.webp") as im:
            formato, tam = im.format, im.size
        caso("el retrato se guarda como WEBP y se reduce a 1024 px como mucho", ok and formato == "WEBP" and max(tam) <= 1024, (ok, msg, tam))
        caso("un archivo que no es una imagen se rechaza con un mensaje claro", not guarda_imagen("logo", b"esto no es una imagen", t)[0] and not guarda_imagen("logo", b"", t)[0]
             and not guarda_imagen("otro", png(False), t)[0])
        ok1, m1 = guarda_cara("feliz", "izquierda", png(True), t)
        ok2, m2 = guarda_cara("feliz", "izquierda", png(True), t)
        e1 = estado(t)
        caso("una cara con fondo transparente se guarda con el nombre que entiende el programa (gesto y mirada) y una repetida no pisa a la anterior",
             ok1 and ok2 and sorted(c["nombre"] for c in e1["caras"]) == ["cara feliz mirando hacia la izquierda", "cara feliz mirando hacia la izquierda 2"]
             and all(c["gesto"] == "feliz" and c["mira"] == "izquierda" for c in e1["caras"]), (m1, m2, e1["caras"]))
        caso("una foto SIN fondo transparente se rechaza explicando cómo arreglarlo", not guarda_cara("feliz", "derecha", png(False), t)[0] and "transparente" in guarda_cara("feliz", "derecha", png(False), t)[1])
        caso("gesto o mirada raros se rechazan", not guarda_cara("enfadado", "derecha", png(True), t)[0] and not guarda_cara("feliz", "arriba", png(True), t)[0])
        guarda_cara("what", "frente", png(True), t)
        from .miniaturas_ia import caras as lee_caras
        lc = lee_caras(t / "mis_caras")
        caso("el programa lee esas caras tal cual (gesto y dirección salen del nombre)", {(c["gesto"], c["mira"]) for c in lc} >= {("feliz", "izquierda"), ("what", "frente")}, [(c["gesto"], c["mira"]) for c in lc])
        caso("borrar una cara quita su archivo; un nombre raro (con rutas) no borra nada fuera",
             borra_cara("cara feliz mirando hacia la izquierda 2.png", t) and not borra_cara("../mi_marca/retrato.webp", t) and not borra_cara("..\\x.png", t) and (t / "mi_marca" / "retrato.webp").is_file())
        caso("solo se sirven archivos de mi_marca y mis_caras, sin salirse de ahí",
             ruta_publica("marca", "retrato.webp", t) is not None and ruta_publica("caras", "../mi_marca/retrato.webp", t) is None and ruta_publica("otra", "retrato.webp", t) is None
             and ruta_publica("marca", "no-existe.webp", t) is None)
    return filas
