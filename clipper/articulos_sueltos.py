"""📝 Tools → Miniaturas → «Imágenes destacadas»: la imagen destacada de un artículo SIN vídeo (el autor, 11-oct-2026: «añade otro tab que sea miniatura para artículos y que tenga todo igual»).

Es la misma herramienta que «Miniaturas» pero para tu web: tú escribes (o dictas) de qué va el artículo —o pegas su texto— y sale una viñeta de cómic en blanco y negro, profesional y ESPECÍFICA de ese
tema, lista para descargar. Reusa por completo el motor de las imágenes destacadas (`imagen_articulo.py`: Flash-Lite saca el tema, los objetos propios y la escena; Cloudflare FLUX la dibuja; otra
mirada comprueba que no haya texto; sale en escala de grises). Cada idea es un «artículo» de mentira dentro de `work/articulos_sueltos/<id>/articulo/` (su articulo.json, hero-N.jpg y hero.json), así que
no hay código duplicado. Hasta 4 variantes por idea (`imagen_articulo.MAX_IMG`) y las últimas `MAX_ITEMS` ideas.

Topes (abogado del diablo): gratis. Las llamadas a Cloudflare cuentan en el MISMO cupo diario de «imágenes de artículo» que las imágenes destacadas de cada vídeo (`miniaturas_ia.articulo_max_dia`, 15) y
las neuronas, en el cupo general (`tope_dia`), que se comparte con las miniaturas de tus vídeos. Gemini: 1 Flash-Lite por idea nueva + 1 por imagen para mirar el texto (0 de las 20 de los Flash).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import imagen_articulo as _ia
from . import miniaturas_ia as _mia
from .util import log

MAX_ITEMS = 12
MIN_TEXTO = 6
MAX_TEXTO = 6000
_LOCK = threading.RLock()
_ID = re.compile(r"a-\d{8}-\d{6}-[0-9a-f]{6}")


def _dir(dir_work=None) -> Path:
    return Path(dir_work or _mia._rutas()["work"]) / "articulos_sueltos"


def _lee(p: Path, defecto):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _escribe(p: Path, d) -> None:
    from .util import reemplaza
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    reemplaza(tmp, p)


def libre_a_articulo(texto: str, titulo: str = "") -> dict:
    """Lo que escribe el autor (un tema en una frase, o un artículo pegado) -> el «artículo» que lee `imagen_articulo.resumen_articulo`."""
    texto = " ".join(str(texto or "").split())[:MAX_TEXTO]
    titulo = " ".join(str(titulo or "").split())[:150]
    if not titulo:
        titulo = re.split(r"(?<=[.!?:])\s", texto, maxsplit=1)[0][:120] if len(texto) > 140 else texto
    return {"h1": titulo, "title_seo": titulo, "keyword": "", "meta_descripcion": "", "intro": [{"tipo": "parrafo", "texto": texto}], "secciones": [], "conclusion": ""}


def firma(texto: str, titulo: str) -> str:
    return hashlib.sha1(f"{' '.join(str(texto).split())}|{' '.join(str(titulo).split())}".lower().encode("utf-8")).hexdigest()[:12]


# ============================================================================================ las ideas guardadas
def _meta(item_id: str, dir_work=None) -> dict | None:
    if not _ID.fullmatch(str(item_id or "")):
        return None
    m = _lee(_dir(dir_work) / item_id / "meta.json", None)
    return m if isinstance(m, dict) else None


def items(dir_work=None) -> list[dict]:
    """Las ideas guardadas con sus variantes, de la más reciente a la más vieja (solo las que tienen al menos una imagen en disco)."""
    base, out = _dir(dir_work), []
    if not base.is_dir():
        return out
    for d in base.iterdir():
        m = _meta(d.name, dir_work)
        if not m:
            continue
        est = _ia.estado(d.name, base)
        if not est["imagenes"]:
            continue
        c = est["concepto"] or {}
        ultima = max(float(i.get("ts") or 0) for i in est["imagenes"])
        out.append({"id": d.name, "ts": ultima or float(m.get("ts") or 0), "texto": m.get("texto", ""), "titulo": m.get("titulo", ""), "firma": m.get("firma", ""),
                    "tema": c.get("tema", ""), "objetos": c.get("objetos", []), "escena": c.get("escena", ""), "alt": c.get("alt", ""), "con_persona": c.get("con_persona", True), "composicion": c.get("composicion", ""), "nombre_modelo": c.get("nombre_modelo", ""),
                    "imagenes": est["imagenes"]})
    return sorted(out, key=lambda x: -x["ts"])


def item(item_id: str, dir_work=None) -> dict | None:
    return next((x for x in items(dir_work) if x["id"] == item_id), None)


def ruta_imagen(item_id: str, archivo: str, dir_work=None) -> Path | None:
    if not _ID.fullmatch(str(item_id or "")) or not re.fullmatch(r"hero-\d+\.jpg", str(archivo or "")):
        return None
    p = _dir(dir_work) / item_id / "articulo" / archivo
    return p if p.is_file() else None


def quita(item_id: str, dir_work=None) -> bool:
    """Quita una idea (su carpeta entera: son archivos de esta herramienta). No toca nada fuera de `work/articulos_sueltos/`."""
    if _meta(item_id, dir_work) is None:
        return False
    with _LOCK:
        shutil.rmtree(_dir(dir_work) / item_id, ignore_errors=True)
    return True


def _poda(dir_work=None) -> None:
    for viejo in items(dir_work)[MAX_ITEMS:]:
        quita(viejo["id"], dir_work)


def cupo(cfg=None, dir_work=None, ahora: float | None = None) -> dict:
    """Llamadas a Cloudflare de «imágenes de artículo» de hoy (se comparte con las destacadas de los vídeos) y la batería general de neuronas."""
    p = _mia.parametros(cfg)
    usadas, tope = _ia.intentos_hoy(dir_work, ahora), int(p.get("articulo_max_dia", 15))
    return {"usadas": usadas, "max": tope, "quedan": max(0, tope - usadas), "recarga": _mia.recarga_local(ahora), "bateria": _mia.bateria(cfg, dir_work, ahora)}


# ============================================================================================ generar
def genera(texto: str, titulo: str = "", de: str | None = None, nueva_idea: bool = False, cfg=None, dir_work=None, progreso=None, ahora=time.time, **pruebas) -> dict:
    """«Generar» (idea nueva), «↻ Otra» (`de` = la idea desde la que se pulsó y el formulario IGUAL: la MISMA escena con otra semilla, cero Gemini de concepto) y «💡 Otra idea»
    (`nueva_idea=True`: otra escena para esa misma idea). Si cambiaste el texto o el título, es una idea nueva. Nunca lanza.
    -> {"ok": True, "id", "estado"} o {"ok": False, "error"}. `pruebas`: lo de `imagen_articulo.genera` con «_» (sin red)."""
    texto = " ".join(str(texto or "").split())
    titulo = " ".join(str(titulo or "").split())
    if len(texto) < MIN_TEXTO:
        return {"ok": False, "error": "Cuéntale con unas palabras de qué va el artículo (el tema, o pega su texto)."}
    if len(texto) > MAX_TEXTO:
        texto = texto[:MAX_TEXTO]
    f = firma(texto, titulo)
    base = _dir(dir_work)
    existente = _meta(de, dir_work) if de else None
    if existente and existente.get("firma") == f:
        item_id, nuevo = de, False
    else:
        item_id, nuevo = f"a-{time.strftime('%Y%m%d-%H%M%S', time.localtime(ahora()))}-{hashlib.sha1(f'{f}{ahora()}'.encode()).hexdigest()[:6]}", True
        nueva_idea = True
    carpeta = base / item_id / "articulo"
    if nuevo:
        carpeta.mkdir(parents=True, exist_ok=True)
        _escribe(carpeta / "articulo.json", {**libre_a_articulo(texto, titulo), "jid": item_id})
        _escribe(base / item_id / "meta.json", {"id": item_id, "ts": ahora(), "texto": texto, "titulo": titulo, "firma": f})
    r = _ia.genera(item_id, cfg, nueva_idea=nueva_idea, dir_outbox=base, dir_work=dir_work, progreso=progreso, **pruebas)
    if not r.get("ok"):
        if nuevo:
            shutil.rmtree(base / item_id, ignore_errors=True)       # no quedó nada que enseñar: no dejamos una idea vacía
        return r
    _poda(dir_work)
    return {"ok": True, "id": item_id, "estado": r.get("estado"), "item": item(item_id, dir_work)}


# ============================================================================================ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import io
    import tempfile

    from PIL import Image
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:300]})

    def jpeg():
        b = io.BytesIO()
        Image.new("RGB", (1280, 720), (200, 40, 40)).save(b, "JPEG")
        return b.getvalue()

    objetos = ["search results page with a rich result card", "code block with curly braces", "magnifying glass over the result"]
    escena = ("An adult specialist points at a large screen showing a search results page with a rich result card full of stars, next to a code block with curly braces, "
              "while a colleague holds a magnifying glass over the result, bright office.")
    respuesta = json.dumps({"tema": "Structured data for rich results", "objetos": objetos, "escena": escena, "alt": "Dos especialistas revisan un resultado enriquecido"})
    pedidos: list = []

    def concepto(pr):
        pedidos.append(pr)
        return respuesta

    prompts: list = []

    def llama(pr, sd):
        prompts.append(pr)
        return jpeg()

    a = libre_a_articulo("Guía de schema markup", "")
    larga = libre_a_articulo("Los datos estructurados ayudan a Google a entender tu página. " * 8, "")
    caso("lo que escribes pasa a ser un «artículo»: una frase corta es el título; un texto largo, la introducción (con su primera frase de título)",
         a["h1"] == "Guía de schema markup" and larga["h1"].startswith("Los datos estructurados ayudan") and len(larga["h1"]) <= 120 and larga["intro"][0]["texto"].startswith("Los datos"), (a["h1"], larga["h1"]))
    resumen = _ia.resumen_articulo(larga)
    caso("el texto pegado le llega ENTERO a la IA (no solo 700 caracteres) para que el dibujo sea específico de ese artículo", "Los datos estructurados" in resumen and len(resumen) > 500, len(resumen))

    with tempfile.TemporaryDirectory() as t:
        w = Path(t) / "work"
        w.mkdir()
        corto = genera("hola", dir_work=w, _llama=llama)
        caso("con muy poco texto avisa en vez de gastar nada", not corto["ok"] and not pedidos and not prompts, corto)
        r1 = genera("Guía de schema markup y datos estructurados para SEO", dir_work=w, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("«Generar»: idea nueva (1 petición de concepto, 1 dibujo con el ESTILO del cómic en blanco y negro) y queda guardada con su imagen",
             r1["ok"] and len(pedidos) == 1 and len(prompts) == 1 and prompts[0].startswith(_ia.estilo(False)) and "search results page" in prompts[0] and "no people" in prompts[0]
             and ruta_imagen(r1["id"], "hero-1.jpg", w) is not None and len(items(w)) == 1 and items(w)[0]["tema"].startswith("Structured data"), r1.get("error") or items(w))
        with Image.open(ruta_imagen(r1["id"], "hero-1.jpg", w)) as im:
            px = im.convert("RGB").getpixel((10, 10))
            caso("la imagen sale en blanco y negro de verdad (aunque la IA se cuele con color)", abs(px[0] - px[1]) < 6 and abs(px[1] - px[2]) < 6, px)
        r2 = genera("Guía de schema markup y datos estructurados para SEO", de=r1["id"], dir_work=w, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("«↻ Otra» con el formulario igual: la MISMA idea y escena con otra semilla (sin pedir otro concepto) y 2 variantes en la misma idea",
             r2["ok"] and r2["id"] == r1["id"] and len(pedidos) == 1 and len(prompts) == 2 and prompts[0] == prompts[1] and len(items(w)) == 1 and len(items(w)[0]["imagenes"]) == 2,
             (r2.get("error"), len(pedidos), len(items(w))))
        r3 = genera("Guía de schema markup y datos estructurados para SEO", de=r1["id"], nueva_idea=True, dir_work=w, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("«💡 Otra idea» con el formulario igual: otro concepto (1 petición) en la misma idea, con una variante más", r3["ok"] and r3["id"] == r1["id"] and len(pedidos) == 2 and len(items(w)[0]["imagenes"]) == 3, r3.get("error"))
        r4 = genera("Guía de canonical y contenido duplicado", de=r1["id"], dir_work=w, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("si cambias el texto es otra idea (otra carpeta), aunque se pulse desde la anterior", r4["ok"] and r4["id"] != r1["id"] and len(items(w)) == 2, (r4.get("error"), len(items(w))))
        c = cupo(None, w)
        caso("el cupo cuenta cada dibujo (4 hasta aquí) y se comparte con las imágenes destacadas de los vídeos", c["usadas"] == 4 and c["max"] >= 10 and c["quedan"] == c["max"] - 4, c)
        malo = genera("Un tema cualquiera del que escribir", dir_work=w, _llama=llama, _texto_concepto=lambda pr: json.dumps({"escena": "corta", "alt": "x"}))
        caso("si la IA no da un concepto válido, avisa y NO deja una idea vacía guardada", not malo["ok"] and len(items(w)) == 2 and len(list(_dir(w).iterdir())) == 2, (malo, len(list(_dir(w).iterdir()))))
        caso("rutas seguras: un id raro o un archivo que no es hero-N.jpg no sirve nada",
             ruta_imagen("../x", "hero-1.jpg", w) is None and ruta_imagen(r1["id"], "../meta.json", w) is None and ruta_imagen(r1["id"], "hero-9.jpg", w) is None
             and ruta_imagen(r1["id"], "hero-1.jpg", w) is not None)
        for k in range(MAX_ITEMS + 2):
            genera(f"Tema numero {k} con suficiente texto para dibujar", dir_work=w, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""), ahora=lambda k=k: 2_000_000_000 + k * 10)
        caso(f"solo se guardan las últimas {MAX_ITEMS} ideas (las más viejas se borran del disco, solo ahí)", len(items(w)) == MAX_ITEMS and len(list(_dir(w).iterdir())) == MAX_ITEMS, len(items(w)))
        nuevo_id = items(w)[0]["id"]
        caso("«Quitar» borra la idea entera y nada más", quita(nuevo_id, w) and item(nuevo_id, w) is None and not quita("a-00000000-000000-aaaaaa", w) and not quita("../..", w))
    return filas
