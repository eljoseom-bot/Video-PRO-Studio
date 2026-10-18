"""🎨 MINIATURAS CON IA (el autor, 2026-09-28): de los prompts A/B/C del director a 3 miniaturas listas para YouTube, GRATIS.

El fondo lo dibuja FLUX.2 [klein] 4B en Cloudflare Workers AI (plan gratis: 10.000 neuronas al día; en el plan Free
pasarse FALLA, no cobra; licencia Apache 2.0: uso comercial permitido). Encima, por CÓDIGO:
- su cara REAL (un recorte PNG de `mis_caras/`), la que eligió el director para esa miniatura (la feliz si no lo tiene
  claro: su favorita), con el contorno blanco de su truco de Photoshop (la silueta un poco más grande, en blanco, detrás);
- el titular (el que pide el director en `miniatura_titulares`, o el entrecomillado de un prompt viejo) con la letra
  Rubik y los colores de su marca: siempre bien escrito. La IA NO escribe NUNCA texto: se limpia del prompt, se le dice que
  no lo haga y Flash-Lite mira cada imagen (si trae texto, otra semilla);
- su naranja EXACTO: la IA lo pinta más amarillo (tono 25-29° frente a sus 10°): se corrige el tono al de la marca.

Lo aprendido en la prueba del 28-sep (vídeo de Canibalización SEO): si el prompt habla de la cara del presentador, la IA
DIBUJA a un hombre inventado y el filtro de Cloudflare marca la imagen («flagged»); con «URLs» escribe letras sin
sentido; «canibalización» suena a canibalismo; y el titular lo escribía mal. Por eso el prompt que va a la IA se ADAPTA
aquí (`para_ia`) sin tocar el que el autor ve en el panel (ese sigue sirviendo para ChatGPT).

Una detrás de otra (A, luego B, luego C): si el SERVIDOR falla, se espera 5 min y se reintenta UNA vez; si vuelve a
fallar, se para y se queda con las que salieron (regla del autor: «si ya salió la 1… me sirve tener solo una»). Si el
filtro marca UN dibujo, otra semilla al momento (no es el servidor). Un tope diario por código (`tope_dia`) y la cuenta de
neuronas de hoy para la batería del panel. El token nunca se escribe en ningún log.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .util import log, reemplaza

LETRAS = ("A", "B", "C")
FORMATOS = ("derecha", "centro", "minimalista")        # mismo orden que director._FORMATOS_MINIATURA
_DEF = {
    "activo": True,
    "modelo": "@cf/black-forest-labs/flux-2-klein-4b",
    "ancho": 1280, "alto": 720,
    "neuronas_dia": 10000,         # lo gratis de Cloudflare (se recarga a las 00:00 UTC)
    "tope_dia": 9000,              # lo que nos permitimos gastar (margen por si la estimación se queda corta)
    "reintento_min": 5.0,          # regla del autor: 5 min y un reintento
    "timeout_s": 240,              # casi siempre 20-60 s por imagen; alguna se cuelga más de 3 min (28-sep)
    "contorno_px": 6,              # el contorno blanco de la cara (su truco de Photoshop: «unos 5 px»)
    "carpeta_caras": "mis_caras",
    "cara_defecto": "feliz",       # «cuando no determine bien la intención… que ponga la feliz»
    "titular": True,               # el titular corto con Rubik, escrito por el código (false = miniaturas sin ningún texto)
    "revisa_texto": True,          # Flash-Lite mira cada imagen y, si la IA escribió texto, la repite con otra semilla
    "reintentos_texto": 2,         # como mucho 2 repeticiones por miniatura por esa causa (≈156 neuronas cada una)
    "sueltas_max_dia": 15,         # «Tools → Miniaturas»: como mucho 15 llamadas a Cloudflare al día para miniaturas SIN vídeo (cada intento cuenta, también los reintentos)
    "sueltas_historial": 12,       # cuántas miniaturas sueltas se guardan para volver a verlas/descargarlas (las más viejas se borran)
    "articulo_max_dia": 15,        # imágenes de artículo (la destacada de cada vídeo + «Tools → Miniaturas de artículos»): como mucho 15 llamadas a Cloudflare al día (cada intento cuenta)
    "espera_max_min": 10.0,        # «Terminado» espera como mucho esto a la PRIMERA miniatura (el autor, 11-oct-2026: «al menos una»; lo importante es el vídeo)
    "caida_min": 90.0,             # si Cloudflare falló del todo hace menos de esto y no ha vuelto a responder, los vídeos NO esperan a sus miniaturas
}
_USO_LOCK = threading.Lock()
_CARAS_CACHE: dict = {}


def parametros(cfg=None) -> dict:
    """`miniaturas_ia` de config.yaml encima de los valores por defecto (un valor raro se ignora)."""
    p = dict(_DEF)
    try:
        propios = cfg.get_path("miniaturas_ia") or {} if cfg is not None else {}
    except Exception:  # noqa: BLE001
        propios = {}
    for k, v in (propios.items() if isinstance(propios, dict) else []):
        if k not in p or v is None:
            continue
        try:
            p[k] = bool(v) if isinstance(p[k], bool) else type(p[k])(v)
        except (TypeError, ValueError):
            pass
    return p


def _rutas() -> dict:
    from . import config
    return config.paths()


def _raiz() -> Path:
    from . import config
    return config.ROOT


def credenciales() -> tuple[str, str]:
    return os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip(), os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()


def disponible(cfg=None) -> tuple[bool, str]:
    """¿Se pueden generar? -> (sí/no, por qué no, en palabras del autor)."""
    p = parametros(cfg)
    if not p["activo"]:
        return False, "Las miniaturas con IA están apagadas (miniaturas_ia.activo en config.yaml)."
    acc, tok = credenciales()
    if not acc or not tok:
        return False, "Faltan CLOUDFLARE_ACCOUNT_ID y CLOUDFLARE_API_TOKEN en el archivo .env."
    return True, ""


# ============================================================================================ neuronas y batería
def neuronas(ancho: int, alto: int, n_ref: int = 0) -> float:
    """Lo que cuesta UNA imagen de FLUX.2 [klein] 4B (página de precios de Cloudflare, 28-sep-2026): 26,05 neuronas por
    cada trozo de 512×512 de la SALIDA y 5,37 por cada uno de ENTRADA. Se cuentan trozos enteros (por lo alto: 1280×720
    = 3×2 = 6 trozos ≈ 156 neuronas)."""
    return math.ceil(ancho / 512) * math.ceil(alto / 512) * 26.05 + n_ref * 5.37


def _dia_utc(ahora: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ahora is None else ahora, timezone.utc).strftime("%Y-%m-%d")


def _uso_ruta(dir_work=None) -> Path:
    return Path(dir_work or _rutas()["work"]) / "cloudflare_uso.json"


def uso_hoy(dir_work=None, ahora: float | None = None) -> dict:
    try:
        d = json.loads(_uso_ruta(dir_work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if d.get("dia") != _dia_utc(ahora):
        return {"dia": _dia_utc(ahora), "neuronas": 0.0, "imagenes": 0, "intentos": 0}
    return {"dia": d["dia"], "neuronas": float(d.get("neuronas") or 0), "imagenes": int(d.get("imagenes") or 0),
            "intentos": int(d.get("intentos") or 0)}


def apunta(n: float, imagen: bool, dir_work=None, ahora: float | None = None) -> None:
    """Suma a la cuenta de HOY (UTC) lo que costó un intento. Los intentos que el servidor llegó a procesar (una imagen
    marcada, uno que se colgó) también cuentan: mejor pasarse en la estimación que en la factura."""
    with _USO_LOCK:
        d = uso_hoy(dir_work, ahora)
        d["neuronas"] = round(d["neuronas"] + float(n), 2)
        d["intentos"] += 1
        d["imagenes"] += 1 if imagen else 0
        p = _uso_ruta(dir_work)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d), encoding="utf-8")
            tmp.replace(p)
        except OSError as exc:
            log(f"AVISO: no se pudo apuntar el gasto de Cloudflare ({exc}).")


def recarga_local(ahora: float | None = None) -> str:
    """A qué hora (del PC) se recargan las neuronas: las 00:00 UTC del día siguiente, p. ej. «7:00 p. m.»."""
    t = datetime.fromtimestamp(time.time() if ahora is None else ahora, timezone.utc)
    manana = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone()
    h = manana.hour % 12 or 12
    return f"{h}:{manana.minute:02d} {'a. m.' if manana.hour < 12 else 'p. m.'}"


def _salud_ruta(dir_work=None) -> Path:
    return Path(dir_work or _rutas()["work"]) / "cloudflare_salud.json"


def anota_salud(ok: bool, dir_work=None, ahora: float | None = None) -> None:
    """Apunta que Cloudflare respondió (`ok`) o que falló del todo (tras esperar y reintentar). Así, si está caído, los vídeos que vienen detrás no se quedan
    esperando sus miniaturas (`cloudflare_caido`). Nunca lanza: apuntar la salud jamás tumba una miniatura."""
    try:
        with _USO_LOCK:
            p = _salud_ruta(dir_work)
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
            d["ok" if ok else "fallo"] = time.time() if ahora is None else float(ahora)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(d), encoding="utf-8")
            reemplaza(tmp, p)
    except Exception:  # noqa: BLE001
        pass


def cloudflare_caido(cfg=None, dir_work=None, ahora: float | None = None) -> bool:
    """¿Cloudflare falló del todo hace poco (`caida_min`) y no ha vuelto a responder desde entonces? Entonces un vídeo NO espera a sus miniaturas con IA."""
    try:
        d = json.loads(_salud_ruta(dir_work).read_text(encoding="utf-8"))
        fallo, bien = float(d.get("fallo") or 0), float(d.get("ok") or 0)
    except (OSError, ValueError, TypeError):
        return False
    t = time.time() if ahora is None else float(ahora)
    return fallo > 0 and fallo >= bien and t - fallo < float(parametros(cfg)["caida_min"]) * 60


def bateria(cfg=None, dir_work=None, ahora: float | None = None) -> dict:
    """La batería de la sección de miniaturas: cuánto se gastó hoy y cuántas miniaturas quedan."""
    p = parametros(cfg)
    u = uso_hoy(dir_work, ahora)
    tope = min(float(p["tope_dia"]), float(p["neuronas_dia"]))
    por_img = neuronas(p["ancho"], p["alto"])
    quedan = max(0.0, tope - u["neuronas"])
    return {"usadas": round(u["neuronas"]), "tope": round(tope), "gratis": int(p["neuronas_dia"]),
            "imagenes_hoy": u["imagenes"], "quedan_miniaturas": int(quedan // por_img),
            "pct": round(100 * quedan / tope) if tope else 0, "por_miniatura": round(por_img),
            "recarga": recarga_local(ahora)}


# ============================================================================================ las caras del autor
_DIR_RE = re.compile(r"\s*\b(?:mirando\s+)?(?:hacia\s+)?(?:la\s+|el\s+|al\s+)?(izquierda|derecha|frente|abajo|arriba)\b")


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def gesto_de(nombre: str) -> tuple[str, str | None]:
    """«cara feliz mirando hacia la izquierda» -> ("feliz", "izquierda"); «what» -> ("what", None). La dirección es la
    que se ve en la foto (como la nombró el autor)."""
    n = _sin_tildes(str(nombre).lower()).replace("_", " ").replace("-", " ")
    m = _DIR_RE.search(n)
    mira = m.group(1) if m else None
    n = _DIR_RE.sub(" ", n)
    n = re.sub(r"^\s*cara\s+(?:de\s+)?", "", n)
    n = re.sub(r"\s+\d+\s*$", "", n)                       # («cara feliz mirando hacia la izquierda 2»: la 2.ª foto del mismo gesto)
    n = re.sub(r"\s+", " ", n).strip()
    return (n or _sin_tildes(str(nombre).lower()).strip()), mira


def caras(dir_caras=None, cfg=None) -> list[dict]:
    """Los recortes de `mis_caras/` (PNG con transparencia): gesto, hacia dónde mira, si es ANCHA (brazos abiertos o
    arriba) y la caja de la silueta. Los sinónimos se juntan («sorprendido» y «sorpresa» = un gesto)."""
    from PIL import Image
    carpeta = Path(dir_caras) if dir_caras else _raiz() / parametros(cfg)["carpeta_caras"]
    out: list[dict] = []
    for f in sorted(carpeta.glob("*.png")) if carpeta.is_dir() else []:
        try:
            clave = (str(f), f.stat().st_mtime, f.stat().st_size)
            info = _CARAS_CACHE.get(clave)
            if info is None:
                im = Image.open(f).convert("RGBA")
                caja = im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
                if not caja:
                    continue
                info = {"caja": caja, "ancha": (caja[2] - caja[0]) / max(1, caja[3] - caja[1]) > 1.3}
                _CARAS_CACHE[clave] = info
        except OSError:
            continue
        g, mira = gesto_de(f.stem)
        out.append({"ruta": f, "nombre": f.stem, "gesto": g, "mira": mira, **info})
    canon: dict[str, str] = {}
    for c in sorted(out, key=lambda c: len(c["gesto"])):
        k = c["gesto"][:6]
        canon.setdefault(k, c["gesto"])
        c["gesto"] = canon[k]
    return out


def gestos(lista: list[dict], defecto: str = "feliz") -> list[str]:
    """Los gestos distintos (para el director), con el de por defecto el primero."""
    vistos = sorted({c["gesto"] for c in lista})
    return sorted(vistos, key=lambda g: (g != defecto, g))


def elige_cara(lista: list[dict], pedido: str, formato: str, defecto: str = "feliz") -> tuple[dict | None, bool]:
    """La cara para esa miniatura: el gesto que pidió el director (o el de por defecto si no lo pidió o no existe) y,
    dentro del gesto, la foto que mira HACIA EL CONTENIDO: con la cara a la derecha, una que mire a la izquierda; si solo
    hay una que mira a la derecha, se usa en ESPEJO. -> (cara, espejo)."""
    if not lista:
        return None, False
    ped = gesto_de(pedido or "")[0]

    def del_gesto(g: str) -> list[dict]:
        return [c for c in lista if c["gesto"] == g] or [c for c in lista if g and c["gesto"][:6] == g[:6]] \
            or [c for c in lista if g and (g in c["gesto"] or c["gesto"] in g)]

    grupo = del_gesto(ped) or del_gesto(gesto_de(defecto)[0]) or lista
    if formato in ("derecha", "minimalista"):
        for m in ("izquierda", "frente", None, "abajo", "arriba"):
            hay = [c for c in grupo if c["mira"] == m]
            if hay:
                return hay[0], False
        return grupo[0], grupo[0]["mira"] == "derecha"
    for m in ("frente", None, "abajo", "izquierda", "derecha", "arriba"):
        hay = [c for c in grupo if c["mira"] == m]
        if hay:
            return hay[0], False
    return grupo[0], False


# ============================================================================================ el prompt que va a la IA
_RIESGO = [(r"\bcannibaliz\w*|\bcanibaliz\w*", "competing"), (r"\bguerra\b|\bwars?\b", "rivalry"),
           (r"\bbattling\b|\bbattle\b|\bfighting\b|\bfight\b", "competing"), (r"\bexplosive\b|\bexplod\w*", "bright"),
           (r"\bkill\w*|\bdead\b|\bdeath\b|\bblood\w*|\bweapon\w*|\bgun\w*|\bbomb\w*", "striking")]
ZONA_TITULO = {"derecha": (0.035, 0.04, 0.60, 0.30), "centro": (0.17, 0.035, 0.83, 0.27),
               "minimalista": (0.04, 0.05, 0.64, 0.31)}
_FRASE_TITULO = {"derecha": "the top 30% of the left 60% of the frame",
                 "centro": "the top 28% of the frame, across the middle",
                 "minimalista": "the top 30% of the left two thirds of the frame"}
_FRASE_CARA = {"derecha": "the right 40% of the frame",
               "centro": "the central third of the frame, from the bottom up to two thirds of the height",
               "minimalista": "the right 30% of the frame"}
_TOPE_PROMPT = 2000


SIN_TEXTO = ("No text of any kind anywhere in the image: no words, letters, numbers, digits, percentages, logos with "
             "lettering, captions, signs, labels or interface text; screens, pages, books, badges and stickers show only "
             "abstract shapes, icons and charts without any writing.")
_REFUERZO_TEXTO = ("STRICT RULE: this image must contain ZERO text, not even tiny or fake lettering: no letters, no "
                   "words, no numbers and no symbols that look like writing anywhere.")
# un texto entre comillas: dobles, angulares, curvas o SIMPLES (el director escribe «reading '¿NO APARECES?'» con comillas
# simples; un apóstrofo como el de «presenter's» no cuenta: va pegado a una letra por los dos lados)
_CITA_RE = re.compile(r"(?<![\w])[\"“«]([^\"”»\n]{1,60})[\"”»]|(?<![\w])['‘]([^'‘’\n]{1,60})['’](?![\w])")
_TEXTO_FUERTE = r"\b(?:banner|headline|lettering|captions?|text block|text banner|title card|typography|fonts?|slogan|tagline)\b"
_TEXTO_VERBO = (r"\b(?:text|reading|reads?|says?|saying|labels?|labeled|labelled|signs?|marked|titled|words?|writing|"
                r"written|spelled)\b")


def _sin_siglas(out: str) -> str:
    """Fuera las SIGLAS y los rótulos EN MAYÚSCULAS («SEO», «NOT INDEXED»): a FLUX, si se lo nombras, lo escribe — y lo
    escribe mal. No toca los códigos de color («#FE330A»), ni «URLs»."""
    return re.sub(r"\s{2,}", " ", re.sub(r"(?<![#\w])[A-ZÁÉÍÓÚÑ]{2,}[A-ZÁÉÍÓÚÑ0-9]*\b", "", out)).strip()


# el texto como COMPLEMENTO de un objeto («un calendario marcado con '15 DÍAS'», «una página con un '404' y una etiqueta
# 'NOT INDEXED'», «insignias sobre '6 MESES'»): se quita el complemento y el objeto se queda (la idea de la miniatura
# sigue entera); si después aún queda algo de texto en la frase, se tira la frase entera
_Q = r"(?:[\"“«][^\"”»\n]{1,60}[\"”»]|(?<![\w])['‘][^'‘’\n]{1,60}['’](?![\w]))"
_PAL = r"(?:[\w-]+\s+){0,3}?"
_COMPLEMENTO = [
    re.compile(r"\s*,?\s*\b(?:marked|labell?ed|titled|inscribed|stamped|printed|written|spelled|saying|says|reading|reads)\b"
               r"(?:\s+(?:with|as|in|on\s+it))?\s*" + _Q, re.I),
    re.compile(r"\s+(?:over|on|above|below|under|beside|next\s+to|with|showing|displaying|featuring|bearing)\s+"
               r"(?:(?:a|an|the)\s+)?" + _PAL + _Q + r"(?:\s*(?:,|and|&)\s*(?:(?:a|an|the)\s+)?" + _PAL + _Q + r")*"
               r"(?:\s+(?:labels?|signs?|stickers?|badges?|stamps?|texts?|captions?))?", re.I),
]


def sin_texto(prompt: str, siglas: bool = True) -> str:
    """El prompt SIN nada que pida dibujar texto: fuera las frases del rótulo/banner/titular/etiqueta, cualquier texto entre
    comillas (de cualquier tipo; si es el complemento de un objeto, se quita solo el complemento), los logos de marcas
    (llevan letras: pasan a un pictograma sencillo) y, con `siglas`, las siglas en mayúsculas."""
    quedan = []
    for f in re.split(r"(?<=[.!?])\s+", str(prompt or "").strip()):
        if re.search(_TEXTO_FUERTE, f, re.I):
            continue
        if _CITA_RE.search(f):
            for rx in _COMPLEMENTO:
                f = rx.sub("", f)
            if _CITA_RE.search(f) and not re.search(_TEXTO_VERBO, f, re.I):
                f = _CITA_RE.sub("", f)                            # una cita suelta sin verbo de texto: solo se quita
            if _CITA_RE.search(f) or re.search(_TEXTO_VERBO, f, re.I):
                continue                                           # aún pide o nombra texto: fuera la frase entera
        if f.strip():
            quedan.append(f)
    out = " ".join(quedan)
    out = re.sub(r"(?:(?:the|a|an)\s+)?(?:[A-Z][\w.+-]*\s+){1,4}logos?\b", "simple pictogram", out)      # «Google Search Console logo»
    out = re.sub(r"\bbrand\s+(badges?|icons?|stickers?)\b", r"round pictogram \1", out, flags=re.I)
    out = re.sub(r"\blogos?\b", "pictogram", out, flags=re.I)
    if siglas:
        out = _sin_siglas(out)
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.;:])", r"\1", out)).strip()


def titular_de(prompt: str) -> str:
    """El titular que pide un prompt VIEJO (los nuevos no llevan ninguno: el director lo da aparte): el texto entre comillas de
    la frase del rótulo/banner/titular. Primero las frases que dicen «banner / headline / reading…»; solo si no hay, las de
    «label / sign / caption» (en una miniatura vieja: «a giant '404' and 'NOT INDEXED' label» no es el titular)."""
    frases = re.split(r"(?<=[.!?])\s+", str(prompt or ""))
    for palabras in (r"\b(banner|headline|reading|reads?|text block|bold text|title)\b",
                     r"\b(header|text|sign|label|caption)\b"):
        for f in frases:
            m = _CITA_RE.search(f)
            if m and re.search(palabras, f, re.I):
                return (m.group(1) or m.group(2) or "").strip()
    return ""


# El fondo OSCURO que escribe el director («Ink background», «Dark ink-colored background», «Background in deep dark ink color», «on a flat dark background»): en la B (la que
# prueba OTROS colores: el autor, 2026-10-04) pasa a un fondo de color. Los contornos («thick black ink outlines») no se tocan.
_FONDO_OSCURO = (
    re.compile(r"\b((?:a|an|the)\s+)?(?:(?:flat|solid|plain|calm|deep|dark|near-black|black|ink|ink-colou?red|subtle|textured|matte)[ -]+)*"
               r"(?:dark|black|near-black|ink|ink-colou?red)(?:[ -]colou?red)?\s+(?:backgrounds?|backdrops?)\b", re.I),
    re.compile(r"\b(backgrounds?)\s+(?:in|of|with)\s+(?:an?\s+)?(?:(?:flat|solid|plain|deep|dark|near-black|black|ink)[ -]+)*"
               r"(?:dark|black|near-black|ink)(?:[ -]colou?r(?:ed)?)?\b", re.I),
)


def sin_fondo_oscuro(texto: str) -> str:
    """El texto sin un fondo oscuro/negro/de tinta: pasa a «a bold flat colour background» (el sistema dice después de qué color)."""
    def _uno(m):
        nuevo = "a bold flat colour background"
        return nuevo[0].upper() + nuevo[1:] if m.group(0)[:1].isupper() else nuevo

    def _dos(m):
        return f"{m.group(1)} in bold flat colour"

    out = _FONDO_OSCURO[0].sub(_uno, str(texto or ""))
    return _FONDO_OSCURO[1].sub(_dos, out)


# Los colores AJENOS a la marca que pueda traer el texto de la escena («green checkmarks», «red error symbols», «blue browser bar», «yellow bolt»): en la B, que juega con los colores
# del fondo, desentonan («sin olvidar mis colores del tu marca»); se quitan (el amarillo pasa a dorado). «red-orange» (el naranja de la marca) se queda.
_COLOR_AJENO = re.compile(r"\b(?:(?:bright|vibrant|vivid|deep|dark|light|pale|neon|glowing)[ -]+)?"
                          r"(?:green|blue|purple|violet|pink|magenta|teal|cyan|turquoise|lime|red(?![ -]?orange))(?:[ -]?(?:toned|colou?red|ish))?\b[ -]*", re.I)


def sin_colores_ajenos(texto: str) -> str:
    out = re.sub(r"\byellow(?:ish)?\b", "gold", str(texto or ""), flags=re.I)
    out = _COLOR_AJENO.sub("", out)
    out = re.sub(r"\s+([,.;])", r"\1", out)
    out = re.sub(r"(?:,\s*){2,}", ", ", out)
    out = re.sub(r"\b(?:and|with|in|of|on)\s*(?=[.,;])", "", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def para_ia(prompt: str, formato: str, completa: bool = False, reserva: int = 0) -> str:
    """El prompt del director, ADAPTADO para FLUX (el original no se toca: sirve para ChatGPT): sin la frase del titular
    (lo pone el programa), sin nombrar caras ni personas (la IA las dibuja y el filtro marca la imagen), sin las palabras
    que suenan a violencia, las «URLs» como ventanas de navegador en blanco, y con las zonas libres de la cara y del
    titular. `completa` (la A): más elementos, como las miniaturas que el autor ya publica."""
    quedan = []
    for f in re.split(r"(?<=[.!?])\s+", sin_texto(prompt, siglas=False)):
        if re.search(r"\b(face|faces|presenter|portrait|person|people|cut-?out|man|woman|guy|host)\b", f, re.I):
            continue
        if re.search(r"\b(use only this exact color palette|every element, including|keep the orange exactly)\b", f,
                     re.I):
            continue                                              # la paleta se dice abajo (y se corrige por código)
        if re.search(r"\bwhole background is a bold, saturated colou?r field\b", f, re.I):
            continue                                              # la frase fija del fondo de la B: aquí va la de FLUX (más abajo)
        quedan.append(f)
    out = " ".join(quedan)
    if formato == "centro":
        out = sin_colores_ajenos(sin_fondo_oscuro(out))
    for patron, cambio in _RIESGO:
        out = re.sub(patron, cambio, out, flags=re.I)
    out = re.sub(r"\b(?:website |webpage |web page )?URLs?\b(?: icons?)?", "blank browser windows", out, flags=re.I)
    out = re.sub(r"\bweb ?page icons?\b", "blank browser windows", out, flags=re.I)
    out = _sin_siglas(_CITA_RE.sub("", out))                       # nada entre comillas ni siglas: sería texto dibujado
    extra = ""
    if completa:
        extra += (" Richly detailed and busy like a premium YouTube thumbnail: around the hero element add several "
                  "related props (screens or phones with charts and rising arrows, round badges with simple pictograms, "
                  "simple flat pictogram symbols, lightning bolts, sparkles, comic burst shapes).")
    if formato == "centro":
        # la B prueba OTROS colores de fondo (el autor, 2026-10-04: «que en el ABC test haya una que pruebe otros colores… que juegue con los colores del
        # fondo, sin olvidar mis colores del tu marca»): un fondo de COLOR de la marca, no negro
        extra += (" Colors: the whole background is a bold, saturated colour field that makes the image pop: flat warm red-orange #FE330A "
                  "(a red-orange, never yellow-orange) or flat gold #FFC93C, or a clean diagonal split of those two, with fine halftone dots "
                  "and bold burst lines. The background is not black and not dark: deep near-black ink #14100F is only for outlines, "
                  "shadows and small accents; cream #FFFBF2 for highlights.")
    else:
        extra += (" Colors: warm red-orange #FE330A (a red-orange, never yellow-orange), deep near-black ink #14100F "
                  "backgrounds with halftone dots, cream #FFFBF2 and small gold #FFC93C accents only.")
    extra += (f" Keep {_FRASE_CARA[formato]} as plain background only (halftone texture, soft glow), with no objects "
              f"in it. Leave {_FRASE_TITULO[formato]} as plain, empty background too."
              " There are no people, no faces and no hands anywhere. " + SIN_TEXTO)
    if len(out) + len(extra) > _TOPE_PROMPT - reserva:
        out = out[: max(200, _TOPE_PROMPT - reserva - len(extra))].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return (out + extra).strip()


# ============================================================================================ color de la marca
def _tono(hexa: str) -> float:
    import colorsys
    r, g, b = (int(hexa.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def a_tu_paleta(im, marca: dict | None = None):
    """El naranja de la IA al naranja de la marca (y el amarillo al dorado), sin tocar luces ni sombras: se corrige solo
    el TONO de los colores vivos. Medido el 28-sep: la IA pinta el naranja a 25-29°, la marca es 10° (sus miniaturas,
    11-15°) y el dorado 43°."""
    import numpy as np
    from PIL import Image
    marca = marca or {}
    h_nar = _tono(marca.get("naranja") or "#FE330A")
    h_dor = _tono(marca.get("dorado") or "#FFC93C")
    hsv = np.asarray(im.convert("RGB").convert("HSV")).astype(np.float32)
    h = hsv[..., 0] * 360 / 255
    s = hsv[..., 1] / 255
    hh = np.where(h > 340, h - 360, h)                              # los rojos (350°) cuentan como «0°»
    m_nar = hh + 0                                                   # (copia)
    nar = h_nar + (hh - h_nar) * 0.15                                # 0-30° -> alrededor del naranja de la marca
    dor = h_dor + (hh - h_dor) * 0.4                                 # 38-60° -> alrededor del dorado
    nuevo = np.where(hh <= 30, nar, np.where(hh >= 38, dor, 0))
    t = np.clip((hh - 30) / 8, 0, 1)                                 # 30-38°: transición suave (sin bandas)
    medio = (h_nar + (30 - h_nar) * 0.15) * (1 - t) + (h_dor + (38 - h_dor) * 0.4) * t
    nuevo = np.where((hh > 30) & (hh < 38), medio, nuevo)
    aplica = (hh >= -20) & (hh <= 60)
    peso = np.clip((s - 0.2) / 0.2, 0, 1) * aplica                   # solo colores vivos: grises y cremas, igual
    final = (h + (nuevo - hh) * peso) % 360
    del m_nar
    hsv[..., 0] = final * 255 / 360
    return Image.fromarray(hsv.clip(0, 255).astype(np.uint8), "HSV").convert("RGB")


# ============================================================================================ la composición
CREMA, TINTA, DORADO, NARANJA = (255, 251, 242), (20, 16, 15), (255, 201, 60), (254, 51, 10)
# por formato y según la pose: (x del centro de la cabeza, alto de la figura, dónde empieza arriba), en fracción del
# cuadro. La ANCHA (brazos arriba) a la derecha, como en sus miniaturas: más pequeña y más al centro, el brazo de fuera
# cortado por el borde. En el centrado (B) la figura baja: el titular va encima.
COLOCA = {("derecha", False): (0.80, 1.12, 0.03), ("derecha", True): (0.73, 0.84, 0.17),
          ("centro", False): (0.50, 1.00, 0.29), ("centro", True): (0.50, 0.92, 0.30),
          ("minimalista", False): (0.84, 1.12, 0.03), ("minimalista", True): (0.76, 0.84, 0.17)}


# La figura ANCHA (brazos abiertos o arriba: `cara["ancha"]`) abarca casi todo el ancho y, con la cara a un lado, su brazo de fuera se metía sobre el contenido (el autor, 2026-10-04:
# «mi brazo estorba parte del diseño… tapa como el 30 % de la imagen… mi cara un poquito más pequeña, un poquito nada más»). `coloca` la achica como mucho hasta ENCOGE_MAX de su
# tamaño y la corre hacia la derecha como mucho CORRE_MAX (del ancho) hasta que su silueta no tape más de TAPA_MAX de la ZONA DE CONTENIDO de ese formato (la parte izquierda, donde
# va lo importante). Una foto que no es ancha y el formato centrado (que reparte lo importante a los dos lados) se quedan como están.
ZONA_CONTENIDO = {"derecha": 0.60, "minimalista": 0.66}
TAPA_MAX = 0.12
ENCOGE_MAX = 0.86
CORRE_MAX = 0.08
_PASO_ENCOGE, _PASO_CORRE = 0.02, 0.01


def _mascara_silueta(cara: dict, espejo: bool, alto: int = 360):
    """La silueta de la foto (recortada a su caja) como máscara numpy bool de `alto` px de alto, y la x de su cabeza (el centro de lo de arriba)."""
    import numpy as np
    from PIL import Image, ImageOps
    im = Image.open(cara["ruta"]).convert("RGBA").crop(cara["caja"])
    if espejo:
        im = ImageOps.mirror(im)
    im = im.resize((max(1, round(im.width * alto / im.height)), alto), Image.BILINEAR)
    a = np.array(im.getchannel("A")) > 110
    cols = np.where(a[: max(1, int(alto * 0.22))].any(axis=0))[0]
    return a, float((cols.min() + cols.max()) / 2 if len(cols) else a.shape[1] / 2)


def tapa_zona(masc, cx: float, formato: str, x_rel: float, alto_rel: float, arriba: float, grosor: int = 6, W: int = 1280, H: int = 720) -> float:
    """Qué fracción de la zona de contenido de ese formato tapa la silueta (con su contorno blanco) colocada así (centro de la cabeza en `x_rel` del ancho, `alto_rel` del alto,
    empezando en `arriba`). Se mide en pequeño (1/4): vale para elegir tamaño y sitio sin dibujar la figura entera."""
    import numpy as np
    from PIL import Image, ImageFilter
    esc = 0.25
    Ws, Hs = int(W * esc), int(H * esc)
    th = max(1, round(alto_rel * H * esc))
    tw = max(1, round(masc.shape[1] * th / masc.shape[0]))
    m = Image.fromarray((masc * 255).astype("uint8")).resize((tw, th), Image.BILINEAR).point(lambda v: 255 if v > 110 else 0)
    k = max(1, round(grosor * esc))
    arr = np.array(m.filter(ImageFilter.MaxFilter(2 * k + 1))) > 0
    x0, y0 = round(x_rel * Ws - cx * th / masc.shape[0]), round(arriba * Hs)
    lienzo = np.zeros((Hs, Ws), bool)
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(Ws, x0 + tw), min(Hs, y0 + th)
    if xb > xa and yb > ya:
        lienzo[ya:yb, xa:xb] = arr[ya - y0: yb - y0, xa - x0: xb - x0]
    return float(lienzo[:, : int(ZONA_CONTENIDO[formato] * Ws)].mean())


def coloca(cara: dict, espejo: bool, formato: str, grosor: int = 6, W: int = 1280, H: int = 720) -> tuple[float, float, float]:
    """(x del centro de la cabeza, alto de la figura, dónde empieza arriba), en fracción del cuadro: lo de `COLOCA`, salvo en una foto ANCHA con la cara a un lado, que se achica y se
    corre a la derecha lo justo (el que menos cambie: primero el tamaño más grande y, con él, el menor desplazamiento) para no tapar más de TAPA_MAX del contenido. El pie de la
    figura no se mueve (sigue pegado al borde de abajo). Si ni al máximo llega, se queda con el máximo (el que menos tapa)."""
    x0, alto0, arriba0 = COLOCA[(formato, bool(cara.get("ancha")))]
    if not cara.get("ancha") or formato not in ZONA_CONTENIDO:
        return x0, alto0, arriba0
    try:
        masc, cx = _mascara_silueta(cara, espejo)
    except Exception:  # noqa: BLE001 — si no se puede medir, se coloca como siempre
        return x0, alto0, arriba0
    abajo = arriba0 + alto0
    mejor = None
    for i in range(int(round((1 - ENCOGE_MAX) / _PASO_ENCOGE)) + 1):
        alto = alto0 * (1 - i * _PASO_ENCOGE)
        for j in range(int(round(CORRE_MAX / _PASO_CORRE)) + 1):
            x = x0 + j * _PASO_CORRE
            t = tapa_zona(masc, cx, formato, x, alto, abajo - alto, grosor, W, H)
            if t <= TAPA_MAX:
                return round(x, 3), round(alto, 3), round(abajo - alto, 3)
            if mejor is None or t <= mejor[0]:
                mejor = (t, x, alto)
    return round(mejor[1], 3), round(mejor[2], 3), round(abajo - mejor[2], 3)


def _con_contorno(cara, grosor: int):
    """Su truco de Photoshop: una copia de la silueta un poco más grande, rellena de blanco, DETRÁS de él."""
    from PIL import Image, ImageFilter
    pad = grosor + 3
    lienzo = Image.new("RGBA", (cara.width + 2 * pad, cara.height + 2 * pad), (0, 0, 0, 0))
    lienzo.paste(cara, (pad, pad), cara)
    m = lienzo.getchannel("A").point(lambda v: 255 if v > 110 else 0)
    for _ in range(max(0, grosor)):
        m = m.filter(ImageFilter.MaxFilter(3))
    m = m.filter(ImageFilter.GaussianBlur(0.7))
    blanco = Image.new("RGBA", lienzo.size, (255, 255, 255, 0))
    blanco.putalpha(m)
    return Image.alpha_composite(blanco, lienzo), pad


def _centro_cabeza(cara) -> float:
    arriba = cara.crop((0, 0, cara.width, max(1, int(cara.height * 0.22)))).getchannel("A")
    caja = arriba.point(lambda v: 255 if v > 110 else 0).getbbox()
    return (caja[0] + caja[2]) / 2 if caja else cara.width / 2


def capa_cara(cara: dict, espejo: bool, formato: str, W: int, H: int, grosor: int):
    """La figura ya colocada, en una capa del tamaño del cuadro (RGBA)."""
    from PIL import Image, ImageOps
    im = Image.open(cara["ruta"]).convert("RGBA").crop(cara["caja"])
    if espejo:
        im = ImageOps.mirror(im)
    x_rel, alto_rel, arriba = coloca(cara, espejo, formato, grosor, W, H)
    alto = max(1, int(H * alto_rel))
    im = im.resize((max(1, round(im.width * alto / im.height)), alto), Image.LANCZOS)
    cx = _centro_cabeza(im)
    im, pad = _con_contorno(im, grosor)
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    capa.paste(im, (round(x_rel * W - cx) - pad, round(arriba * H) - pad), im)
    return capa


def _lineas(texto: str) -> list[list[str]]:
    p = texto.split()
    if len(p) <= 1:
        return [p]
    if len(p) == 2:
        return [p] if len(" ".join(p)) <= 12 else [[p[0]], [p[1]]]
    mejor = None
    for k in range(1, len(p)):
        a, b = p[:k], p[k:]
        dif = abs(len(" ".join(a)) - len(" ".join(b)))
        if mejor is None or dif < mejor[0]:
            mejor = (dif, [a, b])
    return mejor[1]


def _dibuja_titular(texto: str, ancho: int, alto: int, fuente: Path, realce):
    """El titular como en sus miniaturas: MAYÚSCULAS, gordo, en cursiva, un poco inclinado hacia arriba, crema con la
    última palabra resaltada, contorno de tinta y sombra dura. Lo más grande que quepa en ancho×alto. -> RGBA o None."""
    from PIL import Image, ImageDraw, ImageFont
    lineas = _lineas(texto.upper())
    if not lineas or not lineas[0]:
        return None
    for tam in range(170, 17, -3):
        f = ImageFont.truetype(str(fuente), tam)
        borde = max(3, round(tam * 0.1))
        sombra = max(3, round(tam * 0.075))
        alto_l = f.getbbox("ÁÑQ", stroke_width=borde)
        paso = int((alto_l[3] - alto_l[1]) * 0.98)
        anchos = [f.getlength(" ".join(l)) + 2 * borde for l in lineas]
        w0, h0 = int(max(anchos)) + sombra + 8, paso * len(lineas) + sombra + borde + 8
        capa = Image.new("RGBA", (w0, h0), (0, 0, 0, 0))
        d = ImageDraw.Draw(capa)
        for n, l in enumerate(lineas):
            y = n * paso - alto_l[1] + borde // 2
            x = borde
            d.text((x + sombra, y + sombra), " ".join(l), font=f, fill=TINTA, stroke_width=borde, stroke_fill=TINTA)
            for k, w in enumerate(l):
                ultima = n == len(lineas) - 1 and k == len(l) - 1 and len(texto.split()) > 1
                d.text((x, y), w, font=f, fill=realce if ultima else CREMA, stroke_width=borde, stroke_fill=TINTA)
                x += f.getlength(w + " ")
        k = 0.16                                                    # cursiva: lo de arriba se corre a la derecha
        capa = capa.transform((capa.width + int(k * capa.height), capa.height), Image.AFFINE,
                              (1, k, -k * capa.height, 0, 1, 0), resample=Image.BICUBIC)
        capa = capa.rotate(3, resample=Image.BICUBIC, expand=True)
        caja = capa.getbbox()
        if caja:
            capa = capa.crop(caja)
        if capa.width <= ancho and capa.height <= alto:
            return capa
    return None


_REALCE_DIST_MIN = 110


def _realce_para(zona_rgb, defecto):
    """El color del resalte de la última palabra del titular: el de siempre (naranja; dorado en la B) salvo que el fondo de ESA zona se le parezca (un fondo naranja o dorado, como en
    la B de colores): entonces el otro (el que más se aleje del fondo). Con el fondo oscuro de siempre no cambia nada."""
    def dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
    if dist(defecto, zona_rgb) >= _REALCE_DIST_MIN:
        return defecto
    return max((c for c in (NARANJA, DORADO) if c != defecto), key=lambda c: dist(c, zona_rgb))


def _color_medio(im, caja) -> tuple[float, float, float]:
    from PIL import ImageStat
    return tuple(ImageStat.Stat(im.crop(caja).convert("RGB")).mean[:3])


def _caja_libre(caja: tuple[int, int, int, int], figura, formato: str, margen: int = 14) -> tuple[int, int, int, int]:
    """La caja del titular recortada para que la figura (su silueta con contorno) no la tape: con la cara a un lado se
    acorta por ese lado; en el centrado, por abajo (las manos en la cabeza)."""
    x0, y0, x1, y1 = caja
    a = figura.getchannel("A").crop(caja).point(lambda v: 255 if v > 40 else 0)
    bb = a.getbbox()
    if not bb:
        return caja
    if formato == "centro":
        return x0, y0, x1, max(y0 + 40, y0 + bb[1] - margen)
    return x0, y0, max(x0 + 80, x0 + bb[0] - margen), y1


def compone(fondo, cara: dict | None, espejo: bool, formato: str, titular: str, marca: dict | None = None,
            grosor: int = 6, fuente: Path | None = None, W: int = 1280, H: int = 720):
    """Fondo de la IA (con el naranja de la marca) + titular (letra de la marca, sin que la figura lo tape) + su cara
    con contorno blanco por encima. -> Imagen RGB W×H."""
    from PIL import Image
    marca = marca or {}
    im = a_tu_paleta(fondo.convert("RGB").resize((W, H), Image.LANCZOS), marca).convert("RGBA")
    figura = capa_cara(cara, espejo, formato, W, H, grosor) if cara else Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if titular:
        fx0, fy0, fx1, fy1 = ZONA_TITULO[formato]
        x0, y0, x1, y1 = _caja_libre((int(fx0 * W), int(fy0 * H), int(fx1 * W), int(fy1 * H)), figura, formato)
        fuente = fuente or (_raiz() / (marca.get("fuente_numero") or "fonts/Rubik-Black.ttf"))
        realce = _realce_para(_color_medio(im, (x0, y0, x1, y1)), DORADO if formato == "centro" else NARANJA)
        t = _dibuja_titular(titular, x1 - x0, y1 - y0, fuente, realce)
        if t is not None:
            x = x0 + ((x1 - x0) - t.width) // 2 if formato == "centro" else x0
            y = y0 + ((y1 - y0) - t.height) // 2
            capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            capa.paste(t, (x, y), t)
            im = Image.alpha_composite(im, capa)
    return Image.alpha_composite(im, figura).convert("RGB")


def a_jpg(im, tope: int = 2 * 1024 * 1024) -> bytes:
    """JPEG de menos de 2 MB (el tope de YouTube)."""
    for calidad in (92, 86, 78, 70, 60):
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=calidad)
        if buf.tell() <= tope:
            break
    return buf.getvalue()


# ============================================================================================ ¿trae texto la imagen?
_PROMPT_TEXTO = ("Mira esta imagen: es el FONDO de una miniatura de YouTube dibujado por una IA. ¿Hay en ella CUALQUIER "
                 "texto: letras, palabras, números, cifras, siglas, logos con letras, carteles, etiquetas, texto en "
                 "pantallas, libros, botones o insignias — incluido texto deformado, inventado, diminuto o ilegible? "
                 'Responde SOLO con JSON: {"hay_texto": true o false, "donde": "dónde y qué ves, en menos de 12 palabras"}. '
                 "Si dudas, true.")


def _lee_veredicto(texto: str) -> tuple[bool, str]:
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    d = json.loads(t)
    if not isinstance(d, dict) or not isinstance(d.get("hay_texto"), bool):
        raise ValueError("la IA no dijo si había texto")
    return d["hay_texto"], str(d.get("donde") or "")[:100]


def hay_texto(jpeg: bytes, cfg, jid: str = "", _gemini=None, _modelos=None) -> tuple[bool | None, str]:
    """Flash-Lite mira la imagen (UNA petición, 500 al día: no toca las 20 de los Flash) -> (True/False, qué ve) o (None, por
    qué no se pudo mirar: sin clave, saturado…; en ese caso la imagen se acepta tal cual). Nunca lanza."""
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                return None, "sin clave de Gemini"
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from . import modelos as _m
            _modelos = _m.cadena_lite(cfg, client)
        from . import uso as _uso
        from . import util as _util
        _uso.contexto(job=jid or None)
        try:
            res, _modelo, _gasto = _util.call_gemini_cadena(
                client, types, _modelos, [types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), _PROMPT_TEXTO],
                system=None, valida=_lee_veredicto, reintento_s=15.0, pasadas=1, timeout_s=45.0, esquema=None,
                etiqueta="miniatura_texto")
        finally:
            _uso.contexto(job=None)
        return res
    except Exception as exc:  # noqa: BLE001 — no poder mirar nunca frena la miniatura
        return None, str(exc).replace("\n", " ")[:90]


def _chico(fondo, ancho: int = 800) -> bytes:
    """El fondo en pequeño (800 px, ~50 KB) para que Flash-Lite lo mire sin gastar casi nada."""
    from PIL import Image
    im = fondo.convert("RGB")
    if im.width > ancho:
        im = im.resize((ancho, round(im.height * ancho / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


# ============================================================================================ Cloudflare
class ErrorServidor(RuntimeError):
    """El servidor no respondió o está saturado: 5 min y un reintento."""


class Marcada(RuntimeError):
    """El filtro de contenido de Cloudflare marcó ESE dibujo: otra semilla."""


class ErrorCuenta(RuntimeError):
    """Token, cuenta o cupo del día: reintentar no sirve."""


def llama_cloudflare(prompt: str, seed: int, cfg=None, ancho: int | None = None, alto: int | None = None) -> bytes:
    """UNA imagen de FLUX.2 [klein] 4B (multipart, como pide Cloudflare). -> los bytes (JPEG). Nunca escribe el token."""
    import requests
    p = parametros(cfg)
    acc, tok = credenciales()
    if not acc or not tok:
        raise ErrorCuenta("Faltan CLOUDFLARE_ACCOUNT_ID y CLOUDFLARE_API_TOKEN en el .env.")
    campos = {"prompt": (None, prompt), "width": (None, str(ancho or p["ancho"])),
              "height": (None, str(alto or p["alto"])), "seed": (None, str(int(seed)))}
    try:
        r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{p['modelo']}",
                          headers={"Authorization": f"Bearer {tok}"}, files=campos,
                          timeout=(15, float(p["timeout_s"])))
    except requests.RequestException as exc:
        raise ErrorServidor(f"sin respuesta de Cloudflare ({type(exc).__name__})") from None
    try:
        j = r.json()
    except ValueError:
        j = {}
    errores = json.dumps(j.get("errors") or [], ensure_ascii=False)[:300]
    if r.status_code == 200 and (j.get("result") or {}).get("image"):
        return base64.b64decode(j["result"]["image"])
    if "flagged" in errores.lower() or '"code": 3030' in errores:
        raise Marcada("el filtro de Cloudflare marcó ese dibujo")
    if r.status_code in (401, 403):
        raise ErrorCuenta(f"Cloudflare no acepta el token (HTTP {r.status_code}): revisa CLOUDFLARE_API_TOKEN.")
    if r.status_code == 429 and re.search(r"neuron|daily|limit", errores, re.I):
        raise ErrorCuenta("Se acabaron las neuronas gratis de hoy en Cloudflare.")
    if r.status_code in (408, 429) or r.status_code >= 500:
        raise ErrorServidor(f"Cloudflare saturado (HTTP {r.status_code})")
    raise ErrorServidor(f"HTTP {r.status_code}: {errores or r.text[:200]}")


# ============================================================================================ generarlas (A, B, C)
def carpeta(jid: str, dir_outbox=None) -> Path:
    return Path(dir_outbox or _rutas()["outbox"]) / jid / "miniaturas_ia"


def _estado_ruta(jid: str, dir_outbox=None) -> Path:
    return carpeta(jid, dir_outbox) / "estado.json"


def lee(jid: str, dir_outbox=None) -> dict:
    try:
        return json.loads(_estado_ruta(jid, dir_outbox).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guarda(jid: str, d: dict, dir_outbox=None) -> None:
    p = _estado_ruta(jid, dir_outbox)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _firma(prompts: list[str]) -> str:
    return hashlib.sha1(json.dumps(prompts, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _semilla(jid: str, letra: str, n: int = 0) -> int:
    return int(hashlib.sha1(f"{jid}|{letra}".encode("utf-8")).hexdigest()[:7], 16) + 7919 * n


def datos_del_video(jid: str, dir_outbox=None, dir_work=None) -> dict:
    """Los prompts (del manifest de esa versión) y las caras que eligió el director (de su plan)."""
    out = Path(dir_outbox or _rutas()["outbox"]) / jid
    prompts: list[str] = []
    x: dict = {}
    try:
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        x = m[0] if isinstance(m, list) and m else (m if isinstance(m, dict) else {})
        prompts = [str(p) for p in (x.get("miniatura_prompts") or []) if str(p).strip()][:3]
    except (OSError, ValueError):
        pass
    elegidas: list[str] = []
    mo = re.match(r"^(.*)--(pro(?:-v\d+)?)$", jid)
    if mo:
        try:
            from . import director
            plan = director.cargar(Path(dir_work or _rutas()["work"]) / f"{mo.group(1)}--pro", mo.group(2)) or {}
            elegidas = [str(c) for c in (plan.get("miniatura_caras") or [])][:3]
        except Exception:  # noqa: BLE001
            elegidas = []
    titulares: list[str] = []
    try:
        titulares = [str(t or "").strip() for t in (x.get("miniatura_titulares") or [])][:3]      # (x: la entrada del manifest)
    except (NameError, AttributeError):
        titulares = []
    return {"prompts": prompts, "caras": elegidas, "titulares": titulares}


def _titular(datos: dict, i: int, prompt: str, p: dict) -> str:
    """El titular de la miniatura `i`: el que dio el director (`miniatura_titulares`) o, en un vídeo de antes, el
    entrecomillado de su prompt; corto (≤ 5 palabras) y vacío si el titular está apagado (`miniaturas_ia.titular: false`)."""
    if not p.get("titular", True):
        return ""
    lista = datos.get("titulares") or []
    t = (lista[i] if i < len(lista) else "") or titular_de(prompt)
    return " ".join(str(t).split()[:5])[:40]


def vigentes(jid: str, dir_outbox=None, dir_work=None) -> dict:
    """{letra: ¿la miniatura hecha de esa letra es de su concepto de AHORA?} — solo las que están hechas. (Tras el ↻ junto a
    su prompt, la imagen que ya había es de la idea anterior: sigue ahí, pero hasta dibujar la nueva con «↻ Otra» no es la
    de este prompt.)"""
    datos = datos_del_video(jid, dir_outbox, dir_work)
    est = lee(jid, dir_outbox)
    out = {}
    for i, letra in enumerate(LETRAS):
        it = (est.get("items") or {}).get(letra) or {}
        if not it.get("ok") or i >= len(datos["prompts"]):
            continue
        out[letra] = (it["prompt_f"] == _firma([datos["prompts"][i]])) if "prompt_f" in it \
            else est.get("firma") == _firma(datos["prompts"])                      # (de antes de este cambio)
    return out


def vigente(jid: str, letra: str, dir_outbox=None, dir_work=None) -> bool:
    return bool(vigentes(jid, dir_outbox, dir_work).get(letra))


def titulares_de(jid: str, cfg=None, dir_outbox=None, dir_work=None) -> list[str]:
    """El titular de cada miniatura tal como se escribirá con Rubik: el del director o, en un vídeo de antes, el entrecomillado
    de su prompt ('' = esa miniatura sale sin ningún texto)."""
    datos = datos_del_video(jid, dir_outbox, dir_work)
    p = parametros(cfg)
    return [_titular(datos, i, pr, p) for i, pr in enumerate(datos["prompts"])]


def sella_vigentes(jid: str, dir_outbox=None, dir_work=None) -> bool:
    """Las miniaturas hechas ANTES de «cada letra por sí sola» solo apuntaron la firma de los TRES prompts juntos. Mientras esa
    firma aún cuadra con los prompts de ahora, se le apunta a cada una el suyo (`prompt_f`): así, al cambiar el concepto de UNA
    letra (clipper/conceptos_miniatura.py), las otras dos siguen al día. -> ¿se cambió algo?"""
    est = lee(jid, dir_outbox)
    datos = datos_del_video(jid, dir_outbox, dir_work)
    if not est.get("items") or est.get("firma") != _firma(datos["prompts"]):
        return False
    cambio = False
    for i, letra in enumerate(LETRAS):
        it = est["items"].get(letra)
        if isinstance(it, dict) and it.get("ok") and "prompt_f" not in it and i < len(datos["prompts"]):
            it["prompt_f"] = _firma([datos["prompts"][i]])
            cambio = True
    if cambio:
        _guarda(jid, est, dir_outbox)
    return cambio


def crea(jid: str, cfg=None, letras: list[str] | None = None, otra: bool = False, dir_outbox=None, dir_work=None,
         _llama=None, _dormir=time.sleep, _ahora=time.time, _caras=None, marca: dict | None = None, _texto=None) -> dict:
    """Genera las miniaturas de ESE vídeo: A, luego B, luego C (o solo `letras`; `otra` = otra versión de esas).
    Deja `videos/<jid>/miniaturas_ia/{A,B,C}.jpg` (+ el fondo de la IA) y su `estado.json`, que el panel va leyendo.
    Cada letra vale por sí sola (`prompt_f`): una ya hecha con SU concepto de ahora no se vuelve a generar, y cambiar el
    concepto de una (↻ junto a su prompt) no tira las otras. Si la IA escribió texto en la imagen (la mira Flash-Lite), se
    repite con otra semilla, como mucho `reintentos_texto` veces. -> el estado final."""
    p = parametros(cfg)
    if marca is None:
        try:
            marca = dict(cfg.get_path("videos.marca") or {}) if cfg is not None else {}
        except Exception:  # noqa: BLE001
            marca = {}
    datos = datos_del_video(jid, dir_outbox, dir_work)
    prompts = datos["prompts"]
    est = lee(jid, dir_outbox)
    if not prompts:
        est.update(estado="sin_prompts", mensaje="Este vídeo no tiene prompts de miniatura.")
        return est
    firma = _firma(prompts)
    items = {l: dict(it) for l, it in (est.get("items") or {}).items() if isinstance(it, dict)}
    pedidas = [l for l in (letras or LETRAS[:len(prompts)]) if l in LETRAS[:len(prompts)]]

    def _al_dia(l: str) -> bool:           # ¿hecha y de ESTE concepto? (las de antes de `prompt_f` valen si no cambió nada)
        it = items.get(l) or {}
        if not it.get("ok"):
            return False
        return (it["prompt_f"] == _firma([prompts[LETRAS.index(l)]])) if "prompt_f" in it else est.get("firma") == firma

    if not otra:
        pedidas = [l for l in pedidas if not _al_dia(l)]
    if not pedidas:
        return est
    lista = _caras if _caras is not None else caras(cfg=cfg)
    llama = _llama or (lambda pr, sd: llama_cloudflare(pr, sd, cfg))
    revisa = bool(p["revisa_texto"]) and (_texto is not None or cfg is not None)
    mira = _texto or (lambda b: hay_texto(b, cfg, jid))
    est = {"firma": firma, "items": items, "estado": "generando", "mensaje": "", "inicio": _ahora()}
    _guarda(jid, est, dir_outbox)
    coste = neuronas(p["ancho"], p["alto"])
    esperado = False
    for letra in pedidas:
        i = LETRAS.index(letra)
        formato = FORMATOS[i]
        previa = items.get(letra) or {}
        n = int(previa.get("n", -1)) + 1 if otra else int(previa.get("n", 0))
        pedido = datos["caras"][i] if i < len(datos["caras"]) else ""
        cara, espejo = elige_cara(lista, pedido, formato, p["cara_defecto"])
        titular = _titular(datos, i, prompts[i], p)
        prompt_base = para_ia(prompts[i], formato, completa=(i == 0))
        prompt_refuerzo = f"{_REFUERZO_TEXTO} " + para_ia(prompts[i], formato, completa=(i == 0),
                                                          reserva=len(_REFUERZO_TEXTO) + 1)     # (sigue cabiendo en el tope)
        resembrado = False
        retextos = 0
        while True:
            u = uso_hoy(dir_work, _ahora())
            if u["neuronas"] + coste > min(p["tope_dia"], p["neuronas_dia"]):
                items[letra] = {"ok": False, "error": f"Tope diario alcanzado: se recarga a las {recarga_local(_ahora())}."}
                est.update(estado="parado", mensaje=items[letra]["error"])
                _guarda(jid, est, dir_outbox)
                return est
            items[letra] = {**{k: v for k, v in previa.items() if k in ("usada",)}, "estado": "generando", "n": n}
            est["estado"], est["mensaje"] = "generando", f"Generando la {letra}…"
            _guarda(jid, est, dir_outbox)
            t0 = _ahora()
            prompt = prompt_refuerzo if retextos else prompt_base
            try:
                bruto = llama(prompt, _semilla(jid, letra, n))
                apunta(coste, True, dir_work, _ahora())
                from PIL import Image
                fondo = Image.open(io.BytesIO(bruto))
                fondo.load()
                veo, donde = (None, "")
                if revisa:
                    est["mensaje"] = f"Revisando que la {letra} no traiga texto…"
                    _guarda(jid, est, dir_outbox)
                    veo, donde = mira(_chico(fondo))
                    if veo is True and retextos < int(p["reintentos_texto"]):
                        retextos += 1
                        n += 1
                        log(f"Miniatura IA {letra} de {jid}: la IA escribió texto ({donde}); otra semilla "
                            f"({retextos} de {int(p['reintentos_texto'])}).")
                        continue
                final = compone(fondo, cara, espejo, formato, titular, marca, int(p["contorno_px"]),
                                W=int(p["ancho"]), H=int(p["alto"]))
                dest = carpeta(jid, dir_outbox)
                dest.mkdir(parents=True, exist_ok=True)
                (dest / f"fondo_{letra}.jpg").write_bytes(a_jpg(fondo.convert("RGB")))
                (dest / f"{letra}.jpg").write_bytes(a_jpg(final))
                items[letra] = {"ok": True, "n": n, "cara": cara["nombre"] if cara else "", "espejo": espejo,
                                "gesto_pedido": pedido, "titular": titular, "hecha": _ahora(),
                                "segundos": round(_ahora() - t0, 1), "prompt_f": _firma([prompts[i]]),
                                "texto_revisado": veo is not None, "con_texto": veo is True,
                                **({"aviso_texto": donde} if veo is True else {})}
                log(f"Miniatura IA {letra} de {jid}: lista en {_ahora() - t0:.0f} s (cara «{items[letra]['cara']}»"
                    + (f", ojo: puede traer texto ({donde})" if veo is True else "") + ").")
                anota_salud(True, dir_work, _ahora())               # Cloudflare responde: los vídeos vuelven a esperar sus miniaturas
                break
            except Marcada:
                apunta(coste, False, dir_work, _ahora())
                if not resembrado:
                    resembrado, n = True, n + 1
                    continue
                items[letra] = {"ok": False, "n": n, "error": "El filtro de Cloudflare marcó dos dibujos seguidos: "
                                                             "pulsa «↻ Otra» para intentar con otro."}
                break                                            # es ese dibujo, no el servidor: sigue con la siguiente
            except ErrorCuenta as exc:
                items[letra] = {"ok": False, "n": n, "error": str(exc)}
                est.update(estado="parado", mensaje=str(exc))
                _guarda(jid, est, dir_outbox)
                return est
            except ErrorServidor as exc:
                apunta(coste, False, dir_work, _ahora())       # se colgó: puede que sí lo procesara (se cuenta)
                if esperado:
                    items[letra] = {"ok": False, "n": n, "error": f"Cloudflare no respondió ({exc}) ni 5 min después."}
                    est.update(estado="parado", mensaje="Cloudflare no respondió ni después de esperar 5 min: se "
                                                        "queda con las que salieron (puedes pulsar «↻ Otra» luego).")
                    _guarda(jid, est, dir_outbox)
                    anota_salud(False, dir_work, _ahora())          # caído del todo: los vídeos que vienen detrás ya no lo esperan
                    return est
                esperado = True
                hasta = _ahora() + float(p["reintento_min"]) * 60
                items[letra] = {"estado": "esperando", "n": n, "hasta": hasta}
                est.update(estado="esperando", mensaje=f"Cloudflare no respondió ({exc}): reintento a las "
                                                       f"{datetime.fromtimestamp(hasta).strftime('%H:%M')}.")
                _guarda(jid, est, dir_outbox)
                log(f"Miniatura IA {letra} de {jid}: {exc}; espero {p['reintento_min']:g} min y reintento una vez.")
                _dormir(float(p["reintento_min"]) * 60)
            except Exception as exc:  # noqa: BLE001 — la imagen no se pudo leer o componer: esa no sale
                items[letra] = {"ok": False, "n": n, "error": f"No se pudo armar la miniatura ({str(exc)[:160]})."}
                break
    hechas = sum(1 for l in LETRAS if (items.get(l) or {}).get("ok"))
    est.update(estado="listo", mensaje=f"{hechas} de {len(prompts)} miniatura(s) lista(s).", fin=_ahora())
    _guarda(jid, est, dir_outbox)
    return est


def ruta_imagen(jid: str, letra: str, dir_outbox=None) -> Path | None:
    if letra not in LETRAS:
        return None
    f = carpeta(jid, dir_outbox) / f"{letra}.jpg"
    return f if f.is_file() else None


def marca_arranque(jid: str, letras: list[str] | None = None, dir_outbox=None) -> None:
    """Al pulsar «Generar» / «↻ Otra»: el estado pasa a «Arrancando…» YA (si no, durante unos segundos seguía el
    mensaje viejo, p. ej. «Cloudflare no respondió…»). `crea` lo pisa enseguida con el suyo."""
    if not (Path(dir_outbox or _rutas()["outbox"]) / jid).is_dir():
        return
    est = lee(jid, dir_outbox)
    items = dict(est.get("items") or {})
    for l in letras or [l for l in LETRAS if not (items.get(l) or {}).get("ok")]:
        items[l] = {**{k: v for k, v in (items.get(l) or {}).items() if k in ("usada", "n")}, "estado": "generando"}
    est.update(items=items, estado="generando", mensaje="Arrancando…")
    _guarda(jid, est, dir_outbox)


def marca_usada(jid: str, letra: str, cand_id: str, dir_outbox=None) -> None:
    """«Usar esta»: esa letra queda «en uso» (y ninguna otra: al programar se sube UNA)."""
    est = lee(jid, dir_outbox)
    for l, it in (est.get("items") or {}).items():
        if isinstance(it, dict):
            it.pop("usada", None)
            if l == letra:
                it["usada"] = cand_id
    _guarda(jid, est, dir_outbox)


# ============================================================================================ pruebas (sin red)
def prueba() -> list[dict]:
    """Casos sin red ni neuronas (Cloudflare de mentira), en «Probar el sistema»."""
    import shutil
    import tempfile
    from PIL import Image, ImageDraw
    filas: list[dict] = []

    def r(caso, ok, detalle=""):
        filas.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)[:600]})

    # --- el prompt que va a la IA (lo aprendido en la prueba del 28-sep)
    a = ('YouTube thumbnail, 16:9 widescreen (1920x1080). On the left side, a giant orange radar screen displaying two '
         'battling website URLs colliding with explosive energy bursts. Above the radar, a large bold banner with the '
         'text "CANIBALIZACIÓN SEO". Small badge with Google Search Console single-color logo next to it. Keep the '
         'right 35-40% of the frame to low-importance background details only: a face cutout of the presenter will be '
         'placed there, so nothing important goes on the right. Use ONLY this exact color palette and nothing else: '
         'vivid orange #FE330A. Keep the orange exactly as given, never shifted toward red or yellow.')
    ia = para_ia(a, "derecha", completa=True)
    r("titular: el texto entre comillas del rótulo (lo escribe el programa, no la IA)",
      titular_de(a) == "CANIBALIZACIÓN SEO", titular_de(a))
    r("el prompt para la IA no nombra caras ni personas (la IA dibujaba a un hombre y el filtro marcaba la imagen)",
      not re.search(r"\b(face cutout|presenter)\b", ia, re.I) and "no people, no faces" in ia, ia)
    r("…ni lleva el titular, ni «canibalización», «battling» o «explosive», y las URLs son ventanas en blanco",
      "CANIBALIZ" not in ia.upper() and "battling" not in ia and "explosive" not in ia and "URL" not in ia
      and "blank browser windows" in ia and '"' not in ia, ia)
    r("…deja libres la zona de la cara y la del titular, pide el naranja ROJIZO de la marca, y la A va más cargada",
      "right 40% of the frame" in ia and "plain, empty background too" in ia and "headline" not in ia
      and "logo" not in ia.replace("logos with lettering", "") and "red-orange #FE330A" in ia
      and "Richly detailed" in ia and "Richly detailed" not in para_ia(a, "minimalista"), ia)
    r("un prompt larguísimo se recorta sin pasar de lo que acepta la IA",
      len(para_ia(a + " More detail." * 300, "centro")) <= _TOPE_PROMPT + 5)
    # el FONDO DE COLOR de la B (el autor, 2026-10-04: «en el ABC test haya una que pruebe otros colores… que juegue con los colores del fondo, sin olvidar mis colores del tu marca»)
    oscuro = ("A stopwatch on the left. Dark ink-colored background with a subtle Ben-Day dot pattern. Background in deep dark ink color with orange speed lines. "
              "On a flat dark background, a chart. Ink background with cream halftone. Thick black ink outlines and Ben-Day dot pattern background.")
    sf = sin_fondo_oscuro(oscuro)
    r("el fondo oscuro/negro/de tinta del prompt pasa a un fondo de color («Ink background» → «A bold flat colour background»…), con su mayúscula y sin tocar los contornos «black ink outlines»",
      sf.count("bold flat colour") == 4 and "Dark ink" not in sf and "dark background" not in sf and "Ink background" not in sf and "deep dark ink color" not in sf
      and "A bold flat colour background with a subtle" in sf and "Thick black ink outlines" in sf and "Ben-Day dot pattern background" in sf, sf)
    ia_b, ia_a = para_ia(oscuro, "centro"), para_ia(oscuro, "derecha")
    r("la B pide a la IA un fondo de COLOR de la marca (naranja, dorado o dividido; no negro) y su texto ya no habla de fondo oscuro; la A y la C siguen con su fondo tinta de siempre",
      "whole background is a bold, saturated colour field" in ia_b and "flat gold #FFC93C" in ia_b and "not black and not dark" in ia_b
      and "near-black ink #14100F backgrounds" not in ia_b and "Dark ink" not in ia_b and "bold flat colour" in ia_b
      and "near-black ink #14100F backgrounds" in ia_a and "Dark ink-colored background" in ia_a and "bold flat colour" not in ia_a
      and "near-black ink #14100F backgrounds" in para_ia(oscuro, "minimalista"), (ia_b[-420:], ia_a[-300:]))
    r("…y la frase fija del fondo de la B que ya traiga el prompt del director no se duplica en el de FLUX",
      para_ia(oscuro + " Background: the whole background is a bold, saturated COLOUR field that makes it pop, flat vivid red-orange #FE330A, with small accents.", "centro")
      .count("whole background is a bold, saturated") == 1)
    ajeno = ("On the left a website page with red error symbols and a red-toned chart; on the right green checkmarks over a vivid red-orange arrow, a blue browser bar, "
             "dark blue ink outlines and a yellow lightning bolt.")
    sc = sin_colores_ajenos(ajeno)
    r("la B no lleva colores ajenos a la marca en su texto (verde, azul, rojo, amarillo…): se quitan, el amarillo pasa a dorado y el «red-orange» de la marca se queda",
      not re.search(r"(green|blue|yellow)", sc, re.I) and not re.search(r"red(?![ -]?orange)", sc, re.I) and "vivid red-orange arrow" in sc and "gold lightning bolt" in sc
      and "error symbols" in sc and "checkmarks" in sc and "browser bar" in sc and ",," not in sc and " ," not in sc, sc)
    r("…y solo en la B: la A y la C no cambian su texto",
      "green checkmarks" in para_ia(ajeno, "derecha") and "green" not in para_ia(ajeno, "centro").split(" Colors:")[0], para_ia(ajeno, "centro")[:300])
    naranja, dorado_, oscuro_ = (254, 51, 10), (255, 201, 60), (24, 20, 18)
    r("el resalte del titular se aparta del fondo: sobre uno naranja la A pasa a dorado, sobre uno dorado la B pasa a naranja, y con el fondo oscuro de siempre no cambia",
      _realce_para(naranja, NARANJA) == DORADO and _realce_para(dorado_, DORADO) == NARANJA and _realce_para(oscuro_, NARANJA) == NARANJA
      and _realce_para(oscuro_, DORADO) == DORADO and _realce_para(naranja, DORADO) == DORADO and _realce_para((235, 70, 20), NARANJA) == DORADO)

    # --- neuronas y batería
    r("una miniatura 1280×720 cuesta 6 trozos × 26,05 ≈ 156 neuronas", round(neuronas(1280, 720)) == 156, neuronas(1280, 720))
    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-miniaturas-ia-"))
    try:
        t0 = 1_900_000_000.0
        apunta(156.3, True, tmp, t0)
        apunta(156.3, False, tmp, t0 + 60)
        b = bateria(None, tmp, t0 + 120)
        r("la batería cuenta lo gastado hoy (también los intentos fallidos) y cuántas miniaturas quedan",
          b["usadas"] == 313 and b["imagenes_hoy"] == 1 and b["quedan_miniaturas"] == int((9000 - 312.6) // 156.3), b)
        r("al día siguiente (00:00 UTC) la batería vuelve a estar llena", bateria(None, tmp, t0 + 86400)["usadas"] == 0)

        # --- caras (recortes de mentira)
        dc = tmp / "caras"
        dc.mkdir()

        def silueta(nombre, w, h):
            im = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse(((1280 - w) // 2, 720 - h, (1280 + w) // 2, 720 + h // 3), fill=(120, 40, 40, 255))
            im.save(dc / f"{nombre}.png")

        silueta("cara feliz mirando hacia la izquierda", 700, 700)
        silueta("cara feliz mirando hacia la derecha", 700, 700)
        silueta("cara de sorpresa", 1200, 600)
        silueta("cara de sorprendido mirando derecha", 1200, 600)
        silueta("cara pensando mirando derecha", 720, 720)
        silueta("what", 1250, 700)
        lista = caras(dc)
        r("gesto y dirección salen del nombre del archivo («cara feliz mirando hacia la izquierda» = feliz, izquierda)",
          gesto_de("cara feliz mirando hacia la izquierda") == ("feliz", "izquierda")
          and gesto_de("cara de sorprendido mirando derecha") == ("sorprendido", "derecha") and gesto_de("what") == ("what", None))
        r("los sinónimos se juntan («sorprendido» y «sorpresa» = un gesto) y la feliz va la primera para el director",
          gestos(lista) == ["feliz", "pensando", "sorpresa", "what"], gestos(lista))
        c1, e1 = elige_cara(lista, "", "derecha")
        r("sin pedir nada, la FELIZ (su favorita); con la cara a la derecha, la que mira a la izquierda (al contenido)",
          c1["nombre"] == "cara feliz mirando hacia la izquierda" and not e1, c1["nombre"])
        c2, e2 = elige_cara(lista, "pensando", "minimalista")
        r("si solo hay una que mira a la derecha y va a la derecha, se usa en espejo (mira al contenido)",
          c2["nombre"] == "cara pensando mirando derecha" and e2)
        c3, _ = elige_cara(lista, "sorpresa", "centro")
        r("el director elige libremente el gesto (sorpresa en B) y un gesto que no existe cae a la feliz",
          c3["gesto"] == "sorpresa" and c3["ancha"] and elige_cara(lista, "enfadado", "centro")[0]["gesto"] == "feliz")

        # --- color de la marca
        amarillo = Image.new("RGB", (60, 20), (255, 140, 20))            # el naranja de la IA (~28°)
        gris = Image.new("RGB", (60, 20), (40, 40, 40))
        hsv = a_tu_paleta(amarillo, {"naranja": "#FE330A", "dorado": "#FFC93C"}).convert("HSV").getpixel((5, 5))
        r("el naranja amarillento de la IA pasa al naranja de la marca (≈10-13°), sin tocar el brillo",
          7 <= hsv[0] * 360 / 255 <= 15 and hsv[2] >= 250, hsv)
        dorado = a_tu_paleta(Image.new("RGB", (8, 8), (255, 215, 0))).convert("HSV").getpixel((1, 1))
        r("el amarillo pasa al dorado de la marca (≈43°) y los grises no cambian",
          38 <= dorado[0] * 360 / 255 <= 48 and a_tu_paleta(gris).getpixel((3, 3)) == (40, 40, 40), dorado)

        # --- composición: la figura nunca tapa el titular
        fondo = Image.new("RGB", (1280, 720), (30, 25, 22))
        fuente = _raiz() / "fonts" / "Rubik-Black.ttf"
        ancha, _ = elige_cara(lista, "sorpresa", "derecha")
        fig = capa_cara(ancha, False, "derecha", 1280, 720, 6)
        libre = _caja_libre((45, 29, 768, 216), fig, "derecha")
        tapa = fig.getchannel("A").crop(libre).point(lambda v: 255 if v > 40 else 0).getbbox()
        r("con una pose ancha a la derecha, la caja del titular se acorta para que el brazo no lo tape", tapa is None, libre)
        img = compone(fondo, c1, False, "derecha", "CANIBALIZACIÓN SEO", {}, 6, fuente)
        r("la miniatura sale a 1280×720, con la cara a la derecha y el titular arriba a la izquierda",
          img.size == (1280, 720) and img.getpixel((1000, 600)) != (30, 25, 22) and img.getpixel((200, 90)) != (30, 25, 22))
        r("…y pesa menos de 2 MB en JPEG (el tope de YouTube)", len(a_jpg(img)) < 2 * 1024 * 1024)

        # --- brazos abiertos (el autor, 2026-10-04: «mi brazo estorba parte del diseño… tapa como el 30 % de la imagen… mi cara un poquito más pequeña, un poquito nada más»)
        import numpy as np

        def silueta_brazos(nombre):
            im_ = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
            d_ = ImageDraw.Draw(im_)
            col = (120, 40, 40, 255)
            d_.ellipse((520, 90, 760, 400), fill=col)                     # la cabeza
            d_.rectangle((360, 380, 920, 720), fill=col)                  # el torso
            d_.line((450, 450, 40, 300), fill=col, width=220)             # el brazo izquierdo, abierto
            d_.line((830, 450, 1240, 300), fill=col, width=220)           # el derecho
            im_.save(dc / f"{nombre}.png")

        silueta_brazos("cara de brazos abiertos")
        lista2 = caras(dc)
        brazos = next(c for c in lista2 if c["nombre"] == "cara de brazos abiertos")
        feliz = next(c for c in lista2 if c["nombre"] == "cara feliz mirando hacia la izquierda")
        x0_, a0_, ar0_ = COLOCA[("derecha", True)]

        def figura_real(c, esp, formato):
            a_ = np.array(capa_cara(c, esp, formato, 1280, 720, 6).getchannel("A")) > 40
            return float(a_[:, : int(ZONA_CONTENIDO[formato] * 1280)].mean()), int(np.where(a_.any(axis=0))[0].min())

        viejo_tapa = TAPA_MAX
        globals()["TAPA_MAX"] = 1.0                                           # (con un tope que todo cumple, sale la colocación de siempre)
        try:
            antes_t, antes_izq = figura_real(brazos, False, "derecha")
        finally:
            globals()["TAPA_MAX"] = viejo_tapa
        x1_, a1_, ar1_ = coloca(brazos, False, "derecha")
        despues_t, despues_izq = figura_real(brazos, False, "derecha")
        r("brazos abiertos: la foto ANCHA se achica un poco y se corre a la derecha hasta no tapar más del 12 % de la zona de contenido (antes tapaba más)",
          brazos["ancha"] and antes_t > TAPA_MAX + 0.03 and despues_t <= TAPA_MAX + 0.02 and a1_ < a0_ and x1_ > x0_ and abs((ar1_ + a1_) - (ar0_ + a0_)) < 1e-6,
          (round(antes_t, 3), round(despues_t, 3), (x1_, a1_, ar1_)))
        r("…con el cambio justo: nunca más del 14 % más pequeña ni más de 0,08 corrida, y su brazo de fuera ya no llega tan a la izquierda",
          a1_ >= a0_ * ENCOGE_MAX - 1e-6 and x1_ <= x0_ + CORRE_MAX + 1e-6 and despues_izq > antes_izq + 80, (a1_, a0_, x1_, x0_, antes_izq, despues_izq))
        r("una foto que NO es ancha, y el formato centrado (que reparte lo importante a los dos lados), se colocan como siempre",
          not feliz["ancha"] and coloca(feliz, False, "derecha") == COLOCA[("derecha", False)]
          and coloca(brazos, False, "centro") == COLOCA[("centro", True)] and coloca(feliz, False, "centro") == COLOCA[("centro", False)])
        globals()["TAPA_MAX"] = 0.0                                           # (ni al máximo llegaría: se queda con el máximo)
        try:
            xm, am, arm = coloca(brazos, False, "derecha")
        finally:
            globals()["TAPA_MAX"] = viejo_tapa
        r("si ni encogiéndola y corriéndola al máximo llega, se queda con el máximo (el que menos tapa), sin pasarse de los topes",
          abs(am - a0_ * ENCOGE_MAX) < 1e-3 and abs(xm - (x0_ + CORRE_MAX)) < 1e-3, (xm, am, arm))
        masc_e, cx_e = _mascara_silueta(brazos, True)
        xe, ae, are = coloca(brazos, True, "derecha")
        r("en espejo (una foto que mira a la derecha, que se usa al revés) también se mide su silueta de verdad",
          xe >= x0_ and tapa_zona(masc_e, cx_e, "derecha", xe, ae, are) <= TAPA_MAX + 0.02, (xe, ae, are))
        r("la «minimalista» (cara también a la derecha) cuida su zona de contenido igual",
          coloca(brazos, False, "minimalista")[0] > COLOCA[("minimalista", True)][0] or coloca(brazos, False, "minimalista")[1] < COLOCA[("minimalista", True)][1])

        # --- generar A, B, C con un Cloudflare de mentira
        out = tmp / "videos"
        jid = "zz-mini--pro"
        (out / jid).mkdir(parents=True)
        (out / jid / "manifest.json").write_text(json.dumps([{"miniatura_prompts": [a, a.replace("CANIBALIZACIÓN SEO", "B"),
                                                                                     a.replace("CANIBALIZACIÓN SEO", "C")]}]),
                                                 encoding="utf-8")
        buf = io.BytesIO()
        Image.new("RGB", (1280, 720), (255, 140, 20)).save(buf, format="JPEG")
        llamadas: list = []
        dormidas: list = []

        def bien(pr, sd):
            llamadas.append(sd)
            return buf.getvalue()

        e = crea(jid, None, dir_outbox=out, dir_work=tmp, _llama=bien, _dormir=dormidas.append, _caras=lista, marca={})
        r("genera A, B y C una detrás de otra y deja su estado «listo»",
          e["estado"] == "listo" and len(llamadas) == 3 and all((out / jid / "miniaturas_ia" / f"{l}.jpg").is_file() for l in LETRAS)
          and e["items"]["A"]["cara"] == "cara feliz mirando hacia la izquierda", e)
        crea(jid, None, dir_outbox=out, dir_work=tmp, _llama=bien, _dormir=dormidas.append, _caras=lista, marca={})
        r("con los mismos prompts no se vuelve a generar nada (no gasta neuronas)", len(llamadas) == 3, len(llamadas))
        e2 = crea(jid, None, letras=["B"], otra=True, dir_outbox=out, dir_work=tmp, _llama=bien, _dormir=dormidas.append,
                  _caras=lista, marca={})
        r("«↻ Otra» rehace SOLO esa, con otra semilla", len(llamadas) == 4 and llamadas[-1] != llamadas[1]
          and e2["items"]["B"]["n"] == 1, (llamadas, e2["items"]["B"]))
        marca_arranque(jid, ["C"], out)
        ea = lee(jid, out)
        r("al pulsar «↻ Otra», el estado pasa a «Arrancando…» al momento (no se queda el mensaje de antes) y las demás "
          "siguen listas", ea["estado"] == "generando" and ea["mensaje"] == "Arrancando…"
          and ea["items"]["C"]["estado"] == "generando" and ea["items"]["A"]["ok"], ea)

        jid2 = "zz-mini2--pro"
        shutil.copytree(out / jid, out / jid2, ignore=shutil.ignore_patterns("miniaturas_ia"))
        pasos: list = []

        def cae_b(pr, sd):
            pasos.append(sd)
            if len(pasos) >= 2:
                raise ErrorServidor("Read timed out")
            return buf.getvalue()

        e3 = crea(jid2, None, dir_outbox=out, dir_work=tmp, _llama=cae_b, _dormir=dormidas.append, _caras=lista, marca={})
        r("regla del autor: si el servidor falla, 5 min y UN reintento; si vuelve a fallar, para y se queda con las que salieron",
          e3["estado"] == "parado" and dormidas == [300.0] and e3["items"]["A"]["ok"] and not e3["items"]["B"]["ok"]
          and "C" not in e3["items"] and len(pasos) == 3, (e3, dormidas, pasos))

        r("…y se apunta que Cloudflare está CAÍDO (falló del todo después de esperar): los vídeos de después no esperarán sus miniaturas",
          cloudflare_caido(None, tmp) is True and cloudflare_caido(None, tmp, ahora=time.time() + 91 * 60) is False, cloudflare_caido(None, tmp))
        time.sleep(0.05)
        shutil.copytree(out / jid, out / "zz-mini2b--pro", ignore=shutil.ignore_patterns("miniaturas_ia"))
        crea("zz-mini2b--pro", None, letras=["A"], dir_outbox=out, dir_work=tmp, _llama=lambda pr, sd: buf.getvalue(), _dormir=dormidas.append, _caras=lista, marca={})
        r("…y en cuanto responde otra vez (una miniatura hecha) vuelve a no estar caído: el siguiente vídeo la espera", cloudflare_caido(None, tmp) is False,
          cloudflare_caido(None, tmp))
        h0 = Path(tmp) / "salud_a_mano"
        anota_salud(False, h0, 1000.0)
        anota_salud(True, h0, 900.0)                                 # (un «ok» más viejo que el fallo no lo cura)
        r("el fallo más reciente manda sobre un «ok» más viejo, y pasados los 90 min se vuelve a intentar",
          cloudflare_caido(None, h0, 1001.0) is True and cloudflare_caido(None, h0, 1000.0 + 90 * 60 + 1) is False and cloudflare_caido(None, Path(tmp) / "nunca") is False)

        jid3 = "zz-mini3--pro"
        shutil.copytree(out / jid, out / jid3, ignore=shutil.ignore_patterns("miniaturas_ia"))
        marcas: list = []

        def marca_una(pr, sd):
            marcas.append(sd)
            if len(marcas) == 1:
                raise Marcada("flagged")
            return buf.getvalue()

        e4 = crea(jid3, None, letras=["A"], dir_outbox=out, dir_work=tmp, _llama=marca_una, _dormir=dormidas.append,
                  _caras=lista, marca={})
        r("si el filtro marca un dibujo, otra semilla AL MOMENTO (sin esperar 5 min)",
          e4["items"]["A"]["ok"] and len(marcas) == 2 and marcas[0] != marcas[1] and dormidas == [300.0], (marcas, e4))

        jid4 = "zz-mini4--pro"
        shutil.copytree(out / jid, out / jid4, ignore=shutil.ignore_patterns("miniaturas_ia"))
        apunta(9000, False, tmp, time.time())
        e5 = crea(jid4, None, dir_outbox=out, dir_work=tmp, _llama=bien, _dormir=dormidas.append, _caras=lista, marca={})
        r("con el tope diario alcanzado no llama a Cloudflare y dice a qué hora se recarga",
          e5["estado"] == "parado" and "Tope diario" in e5["mensaje"] and len(llamadas) == 4, e5)

        _uso_ruta(tmp).unlink(missing_ok=True)       # (el caso del tope diario dejó la cuenta de hoy llena: de cero)

        # --- NINGÚN texto de la IA (el autor, 3-oct-2026: «la IA es súper mala poniendo textos»)
        viejo = ("YouTube thumbnail, 16:9. A giant glowing stopwatch on the left. Bold text banner on the top left reading "
                 "'¿NO APARECES?'. A sad browser page with a giant orange '404' and 'NOT INDEXED' label. The Google Search "
                 "Console logo on a laptop screen. A SEO trophy next to the presenter's laptop. Bold orange text on the "
                 "top left reading 'EL ERROR'.")
        r("titular de un prompt viejo: el entrecomillado con comillas SIMPLES (el director las usa: por eso FLUX recibía el "
          "texto), y no el de una «label»", titular_de(viejo) == "¿NO APARECES?"
          and titular_de("a giant '404' and 'NOT INDEXED' label. Bold orange text on the top left reading 'EL ERROR'.")
          == "EL ERROR" and titular_de("Sin comillas aquí. A banner with no quotes.") == "", titular_de(viejo))
        previo = para_ia(viejo, "derecha").split(" Colors:")[0]
        r("lo que va a FLUX no pide texto: sin banner, entrecomillados, siglas ni «label», los logos pasan a pictogramas y el "
          "apóstrofo de «presenter's» no rompe nada", not re.search(r"banner|reading|APARECES|404|INDEXED|ERROR|SEO|logo|"
          r"Google|label|'", previo.replace("presenter's", "")) and "simple pictogram" in previo, previo)
        r("…y lleva la regla fija de «sin texto»", SIN_TEXTO in para_ia(viejo, "derecha"))
        largo = "A giant stopwatch. " + "More detail about the scene. " * 300
        r("las repeticiones llevan el refuerzo de «sin texto» DELANTE y aun así el prompt cabe en lo que acepta la IA",
          len(f"{_REFUERZO_TEXTO} " + para_ia(largo, "centro", reserva=len(_REFUERZO_TEXTO) + 1)) <= _TOPE_PROMPT + 5
          and SIN_TEXTO in para_ia(largo, "centro", reserva=len(_REFUERZO_TEXTO) + 1))
        r("las siglas se quitan, los códigos de color no: «A SEO robot with #FE330A glow» -> sin SEO y con #FE330A",
          "SEO" not in sin_texto("A SEO robot with #FE330A glow.") and "#FE330A" in sin_texto("A SEO robot with #FE330A glow."))
        real_b = ("YouTube thumbnail, 16:9. Symmetrical split composition comparing time and growth. On the left side, a "
                  "flatlined red-toned chart inside a calendar marked with '15 DÍAS' crossed out in ink style. On the right "
                  "side, a massive ascending growth arrow in intense brand orange with dollar and lead badges over '6 MESES'. "
                  "A large bold text banner at the top left reads 'LA VERDAD'. On the left, a sad cartoon website page with a "
                  "giant orange '404' and 'NOT INDEXED' label.")
        pb = sin_texto(real_b)
        r("un texto que es COMPLEMENTO de un objeto («marked with '15 DÍAS'», «badges over '6 MESES'», «page with a '404' "
          "label») se quita y el objeto se queda: la idea de la B no se pierde", "calendar" in pb and "arrow" in pb
          and "badges" in pb and "website page" in pb and not re.search(r"15|MESES|404|INDEXED|VERDAD|banner|marked|label|'", pb), pb)
        r("pero si el texto ES la frase (un rótulo «reading 'X'» sin objeto) la frase entera se va", "EL ERROR" not in
          sin_texto("A tree. Bold orange text on the top left reading 'EL ERROR'. A river.")
          and sin_texto("A tree. Bold orange text on the top left reading 'EL ERROR'. A river.") == "A tree. A river.")

        # --- Flash-Lite mira la imagen (de mentira) y, si trae texto, se repite con otra semilla
        class _Tipos:
            class Part:
                @staticmethod
                def from_bytes(data, mime_type):
                    return ("imagen", len(data), mime_type)

        from . import util as _u
        real_cadena = _u.call_gemini_cadena
        try:
            _u.call_gemini_cadena = lambda c, t, m, pr, **k: (k["valida"]('{"hay_texto": true, "donde": "un cartel"}'),
                                                              "gemini-x", {})
            v1 = hay_texto(b"x", None, "j", _gemini=(object(), _Tipos), _modelos=["m"])

            def cae(*a, **k):
                raise RuntimeError("503 UNAVAILABLE")
            _u.call_gemini_cadena = cae
            v2 = hay_texto(b"x", None, "j", _gemini=(object(), _Tipos), _modelos=["m"])
        finally:
            _u.call_gemini_cadena = real_cadena
        r("mirar la imagen: «hay texto» -> (True, dónde); si Gemini falla -> (None, motivo) y nunca lanza (la imagen se acepta)",
          v1 == (True, "un cartel") and v2[0] is None and "503" in v2[1], (v1, v2))
        try:
            _lee_veredicto("no es json")
            malo = False
        except ValueError:
            malo = True
        r("una respuesta que no dice true/false no vale (la cadena prueba otro modelo)", malo and _lee_veredicto('{"hay_texto": false}')[0] is False)

        prompts_vistos: list = []

        def bien2(pr, sd):
            prompts_vistos.append((pr, sd))
            return buf.getvalue()

        jid5 = "zz-mini5--pro"
        shutil.copytree(out / jid, out / jid5, ignore=shutil.ignore_patterns("miniaturas_ia"))
        cola = [True, True, False]
        e6 = crea(jid5, None, letras=["A"], dir_outbox=out, dir_work=tmp, _llama=bien2, _dormir=dormidas.append,
                  _caras=lista, marca={}, _texto=lambda b: ((cola.pop(0) if cola else False), "un cartel con letras"))
        sds = [x[1] for x in prompts_vistos]
        r("si la IA escribió texto, repite con OTRA semilla (hasta 2 veces), refuerza lo de «sin texto» y la que sale ya no lo trae",
          len(prompts_vistos) == 3 and len(set(sds)) == 3 and _REFUERZO_TEXTO not in prompts_vistos[0][0]
          and all(_REFUERZO_TEXTO in x[0] for x in prompts_vistos[1:]) and e6["items"]["A"]["ok"] and e6["items"]["A"]["n"] == 2
          and e6["items"]["A"]["texto_revisado"] and not e6["items"]["A"]["con_texto"], (e6["items"]["A"], len(prompts_vistos)))
        prompts_vistos.clear()
        jid6 = "zz-mini6--pro"
        shutil.copytree(out / jid, out / jid6, ignore=shutil.ignore_patterns("miniaturas_ia"))
        e7 = crea(jid6, None, letras=["A"], dir_outbox=out, dir_work=tmp, _llama=bien2, _dormir=dormidas.append, _caras=lista,
                  marca={}, _texto=lambda b: (True, "letras en una pantalla"))
        r("si TODAS traen texto, tras 2 repeticiones se queda con la última y avisa («con_texto»: pulsa ↻ Otra)",
          len(prompts_vistos) == 3 and e7["items"]["A"]["ok"] and e7["items"]["A"]["con_texto"]
          and "letras" in e7["items"]["A"]["aviso_texto"], e7["items"]["A"])
        prompts_vistos.clear()
        jid8 = "zz-mini8--pro"
        shutil.copytree(out / jid, out / jid8, ignore=shutil.ignore_patterns("miniaturas_ia"))
        e8 = crea(jid8, None, letras=["A"], dir_outbox=out, dir_work=tmp, _llama=bien2, _dormir=dormidas.append, _caras=lista,
                  marca={}, _texto=lambda b: (None, "sin clave de Gemini"))
        r("si no se puede mirar la imagen (sin clave, saturado), se acepta tal cual: nunca frena la miniatura",
          len(prompts_vistos) == 1 and e8["items"]["A"]["ok"] and not e8["items"]["A"]["texto_revisado"], e8["items"]["A"])

        # --- cada letra por sí sola: cambiar el concepto de la B no tira la A ni la C
        prompts_vistos.clear()
        jid9 = "zz-mini9--pro"
        shutil.copytree(out / jid, out / jid9, ignore=shutil.ignore_patterns("miniaturas_ia"))
        sin_mirar = lambda b: (False, "")  # noqa: E731
        crea(jid9, None, dir_outbox=out, dir_work=tmp, _llama=bien2, _dormir=dormidas.append, _caras=lista, marca={},
             _texto=sin_mirar)
        man9 = json.loads((out / jid9 / "manifest.json").read_text(encoding="utf-8"))
        man9[0]["miniatura_prompts"][1] = "A brand new concept for B: a rocket over a calendar. " + man9[0]["miniatura_prompts"][1]
        (out / jid9 / "manifest.json").write_text(json.dumps(man9), encoding="utf-8")
        r("tras el ↻ del prompt de la B, su imagen queda marcada «de la idea anterior» y la A y la C siguen al día",
          not vigente(jid9, "B", out, tmp) and vigente(jid9, "A", out, tmp) and vigente(jid9, "C", out, tmp))
        prompts_vistos.clear()
        e9 = crea(jid9, None, dir_outbox=out, dir_work=tmp, _llama=bien2, _dormir=dormidas.append, _caras=lista, marca={},
                  _texto=sin_mirar)
        r("«generar» rehace SOLO la B, con el prompt NUEVO, y la A y la C siguen ahí, hechas (no se pierden del estado)",
          len(prompts_vistos) == 1 and "brand new concept" in prompts_vistos[0][0]
          and all(e9["items"][l]["ok"] for l in LETRAS) and vigente(jid9, "B", out, tmp), (len(prompts_vistos), list(e9["items"])))

        # --- el titular lo escribe el código (Rubik): el del director, o el de un prompt viejo; y se puede apagar
        jid10 = "zz-mini10--pro"
        shutil.copytree(out / jid, out / jid10, ignore=shutil.ignore_patterns("miniaturas_ia"))
        man10 = json.loads((out / jid10 / "manifest.json").read_text(encoding="utf-8"))
        man10[0]["miniatura_titulares"] = ["TITULAR DEL DIRECTOR", "", "OTRO UNO DOS TRES CUATRO CINCO SEIS"]
        (out / jid10 / "manifest.json").write_text(json.dumps(man10), encoding="utf-8")
        e10 = crea(jid10, None, dir_outbox=out, dir_work=tmp, _llama=bien2, _dormir=dormidas.append, _caras=lista, marca={},
                   _texto=sin_mirar)
        tit10 = [(e10["items"].get(l) or {}).get("titular") for l in LETRAS]
        r("el titular sale de «miniatura_titulares» (el del director), el de un prompt viejo si ese está vacío, y como mucho "
          "5 palabras", tit10 == ["TITULAR DEL DIRECTOR", "B", "OTRO UNO DOS TRES CUATRO"], (tit10, e10["items"]))
        # las miniaturas de ANTES (sin `prompt_f` por letra): mientras la firma de los tres aún cuadra se les apunta el suyo, y
        # entonces cambiar el concepto de UNA no hace que las otras dos parezcan «de la idea anterior»
        jid12 = "zz-mini12--pro"
        shutil.copytree(out / jid, out / jid12, ignore=shutil.ignore_patterns("miniaturas_ia"))
        pr12 = datos_del_video(jid12, out, tmp)["prompts"]
        (out / jid12 / "miniaturas_ia").mkdir()
        _guarda(jid12, {"firma": _firma(pr12), "estado": "listo", "items": {l: {"ok": True, "n": 0, "hecha": 1.0} for l in LETRAS}}, out)
        antes12 = vigentes(jid12, out, tmp)
        sello1 = sella_vigentes(jid12, out, tmp)
        man12 = json.loads((out / jid12 / "manifest.json").read_text(encoding="utf-8"))
        man12[0]["miniatura_prompts"][2] = "Otro concepto para la C. " + man12[0]["miniatura_prompts"][2]
        (out / jid12 / "manifest.json").write_text(json.dumps(man12), encoding="utf-8")
        r("miniaturas de antes de «cada letra sola»: se sellan mientras la firma cuadra, y al cambiar UNA, solo esa queda «de la "
          "idea anterior»", antes12 == {"A": True, "B": True, "C": True} and sello1 is True
          and sella_vigentes(jid12, out, tmp) is False and vigentes(jid12, out, tmp) == {"A": True, "B": True, "C": False},
          (antes12, vigentes(jid12, out, tmp)))
        _guarda(jid12, {"firma": "otra-firma", "items": {"A": {"ok": True}}}, out)
        r("si la firma ya no cuadra (el prompt cambió sin sellar) no se inventa nada: no se sella", sella_vigentes(jid12, out, tmp) is False
          and "prompt_f" not in lee(jid12, out)["items"]["A"])
        r("para el panel: «titulares_de» da lo que se escribirá con Rubik en cada letra y «vigentes» cuáles están al día",
          titulares_de(jid10, None, out, tmp) == tit10 and vigentes(jid9, out, tmp) == {"A": True, "B": True, "C": True}
          and vigentes("no-existe", out, tmp) == {}, (titulares_de(jid10, None, out, tmp), vigentes(jid9, out, tmp)))

        class _SinTitular:
            def get_path(self, k, d=None):
                return {"miniaturas_ia": {"titular": False, "revisa_texto": False}}.get(k, d)

        jid11 = "zz-mini11--pro"
        shutil.copytree(out / jid, out / jid11, ignore=shutil.ignore_patterns("miniaturas_ia"))
        e11 = crea(jid11, _SinTitular(), dir_outbox=out, dir_work=tmp, _llama=bien2, _dormir=dormidas.append, _caras=lista,
                   marca={})
        tit11 = [(e11["items"].get(l) or {}).get("titular") for l in LETRAS]
        r("con `miniaturas_ia.titular: false` salen sin NINGÚN texto", tit11 == ["", "", ""], (tit11, e11["items"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return filas


if __name__ == "__main__":
    import sys
    filas_ = prueba()
    for f_ in filas_:
        print(("OK   " if f_["ok"] else "FALLA") + " " + f_["caso"] + ("" if f_["ok"] else f"  -> {f_['detalle']}"))
    sys.exit(1 if any(not f_["ok"] for f_ in filas_) else 0)
