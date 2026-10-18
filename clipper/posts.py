"""📣 POSTS (Tools → Posts, el autor 13-oct-2026): 1 o 2 imágenes sobre UNA idea concreta (no un carrusel) de un vídeo, un artículo de tu web o un texto, con el estilo gráfico de Mi canal.

- UN diseño genérico para todas las redes, exportado en 2 tamaños: 4:5 (Instagram, 1080×1350, con el símbolo de «guardar») y 16:9 (1600×900: LinkedIn, Bluesky, Discord, Reddit, publicación de YouTube).
- UNA petición a Flash-Lite (500 al día; 0 de las 20 de los Flash): elige la idea MÁS valiosa o sorprendente de la fuente, el gancho (≤ 8 palabras), hasta 3 puntos cortos (≤ 8 palabras), una CITA copiada
  del texto (el código comprueba que existe: lo que no se apoya en la fuente se rechaza y la cadena prueba otro modelo), el símbolo de tu biblioteca y los textos de la publicación (Instagram y uno corto
  para las demás redes, con {ENLACE}). Con 0 puntos sale UNA imagen (la portada); con puntos, DOS (portada + «la idea»).
- LA PORTADA ES VARIABLE con el MISMO peso (1/3 cada una): tu cara sola (`mis_caras/`, el gesto según el contexto de la idea), el logo de Mi canal web en grande, o tu retrato en cómic (el de tu web).
  `posts.portadas` en config.yaml cambia los pesos; si no hay fotos de tu cara, reparte entre las otras dos. (Los carruseles no cambian: siempre tu cara.)
- Se guarda en `videos/<fuente>/posts/<id>/` (post.json, ig/, wide/): el 🗑 de la fuente lo borra con lo demás. «✏ Editar» y «↻ Otra portada» vuelven a dibujar SIN IA; «↻ Otro post» pide otra idea (1 petición).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .util import log

VERSION = 1
SS = 2
# Un lienzo PROPIO por red (el autor, 14-oct-2026: «revisa LinkedIn, YouTube… algo me dice que las mediciones son distintas»). Medidas recomendadas en 2026 por cada red para imágenes de una publicación:
# Instagram 4:5 vertical (el feed lo muestra entero); LinkedIn 1,91:1 (1200×627, lo que su feed enseña sin recortar); YouTube Comunidad 1:1; Bluesky ~16:9 y cada imagen ≤ ~1 MB (su límite
# es 976 KB: si no cabe, la sube rechazada); Reddit 4:3 (su feed recorta lo más alto) y Discord 16:9 (el embed lo reduce, pero respeta la proporción).
FORMATOS = {
    "instagram": {"nombre": "Instagram", "dim": "4:5", "ancho": 1080, "alto": 1350, "x0": 72, "x1": 1008, "y0": 88, "y1": 1262, "guardar": True},
    "linkedin": {"nombre": "LinkedIn", "dim": "1,91:1", "ancho": 1200, "alto": 627, "x0": 63, "x1": 1137, "y0": 44, "y1": 583, "dise": 1760},
    "youtube": {"nombre": "YouTube (Comunidad)", "dim": "1:1", "ancho": 1080, "alto": 1080, "x0": 72, "x1": 1008, "y0": 66, "y1": 1014},
    "bluesky": {"nombre": "Bluesky", "dim": "16:9", "ancho": 1200, "alto": 675, "x0": 63, "x1": 1137, "y0": 48, "y1": 627, "max_kb": 900, "dise": 1600},
    "reddit": {"nombre": "Reddit", "dim": "4:3", "ancho": 1440, "alto": 1080, "x0": 76, "x1": 1364, "y0": 62, "y1": 1018},
    "discord": {"nombre": "Discord", "dim": "16:9", "ancho": 1600, "alto": 900, "x0": 84, "x1": 1516, "y0": 64, "y1": 836},
}
LEGADO = {"ig": "Instagram", "wide": "LinkedIn · Bluesky · Discord · Reddit · YouTube"}   # (los posts de antes del 14-oct tienen solo estas dos carpetas)
CLAVES = tuple(FORMATOS) + tuple(LEGADO)
PORTADAS = ("cara", "logo", "comic")
PESOS_PORTADA = {"cara": 1, "logo": 1, "comic": 1}
MAX_PALABRAS_GANCHO, MAX_PALABRAS_PUNTO, MAX_PUNTOS = 8, 8, 3
CALIDAD_JPG = 93
PIE_ALTO_P = 54                                                  # (la franja del pie: la misma que la del carrusel)
_ID_POST = re.compile(r"p-\d{8}-\d{6}-[0-9a-f]{4}")
_RETRATO = "mi_marca/retrato.webp"          # (los subes desde el asistente de tu canal: clipper/mi_marca.py)
_LOGO = "mi_marca/logo.webp"


# ------------------------------------------------------------------ dónde se guarda
def _outbox(dir_outbox=None) -> Path:
    from . import config
    return Path(dir_outbox or config.paths()["outbox"])


def ruta(jid: str, pid: str, dir_outbox=None) -> Path:
    return _outbox(dir_outbox) / jid / "posts" / pid


def lee(jid: str, pid: str, dir_outbox=None) -> dict | None:
    if not _ID_POST.fullmatch(str(pid or "")):
        return None
    try:
        d = json.loads((ruta(jid, pid, dir_outbox) / "post.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def lista(jid: str | None = None, dir_outbox=None) -> list[dict]:
    """Los posts hechos (los más nuevos primero): los de una fuente, o todos."""
    base = _outbox(dir_outbox)
    patron = f"{jid}/posts/*/post.json" if jid else "*/posts/*/post.json"
    out = []
    for f in base.glob(patron):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and _ID_POST.fullmatch(f.parent.name):
            out.append(d)
    return sorted(out, key=lambda d: -(d.get("ts") or 0))


def borra(jid: str, pid: str, dir_outbox=None) -> bool:
    if not _ID_POST.fullmatch(str(pid or "")) or not re.fullmatch(r"[\w.\-]+", str(jid or "")):
        return False
    p = ruta(jid, pid, dir_outbox)
    if not p.is_dir():
        return False
    shutil.rmtree(p, ignore_errors=True)
    return True


# ------------------------------------------------------------------ la portada variable (1/3 cada una)
def elige_portada(cfg=None, dir_caras=None, rng=None) -> str:
    """«cara», «logo» o «comic» con el MISMO peso (`posts.portadas` en config.yaml). Sin fotos de tu cara, se reparte entre las otras dos."""
    import random

    from . import miniaturas_ia as mia
    try:
        crudo = cfg.get_path("posts.portadas") if cfg is not None else None
        pesos = {k: float(crudo.get(k, 0)) for k in PORTADAS} if isinstance(crudo, dict) else dict(PESOS_PORTADA)
    except (TypeError, ValueError, AttributeError):
        pesos = dict(PESOS_PORTADA)
    try:
        hay_caras = bool(mia.caras(dir_caras, cfg))
    except Exception:  # noqa: BLE001
        hay_caras = False
    pool = {k: w for k, w in pesos.items() if w > 0 and (k != "cara" or hay_caras) and (k != "logo" or _hay(_LOGO)) and (k != "comic" or _hay(_RETRATO))}
    if not pool:
        pool = {k: 1.0 for k, ok in (("logo", _hay(_LOGO)), ("comic", _hay(_RETRATO)), ("cara", hay_caras)) if ok}
    if not pool:
        return "ninguna"                                           # sin cara, ni retrato, ni logo: portada solo con el texto
    return (rng or random).choices(list(pool), weights=list(pool.values()))[0]


# ------------------------------------------------------------------ la petición
_SYSTEM = """Eres el editor de contenido de «{canal}»: conviertes UNA idea de sus vídeos y artículos en posts de una o dos imágenes para redes que la gente guarda y comparte.
Respondes SOLO con JSON válido, sin markdown."""


def arma_prompt(d: dict, canal: str, objetivo: str, evita: list[str] | None = None, anterior: str = "") -> str:
    from . import ilustraciones
    from . import posts_estilos as PE
    de_texto = bool(d.get("texto_mode"))
    fuente = "ARTÍCULO" if de_texto else "VÍDEO"
    L = [f"Convierte UNA idea de este {fuente.lower()} de «{canal}» en un POST para redes (Instagram, LinkedIn, Bluesky, Discord, Reddit y la pestaña de publicaciones de YouTube): 1 o 2 imágenes. "
         "NO es un carrusel: es UNA sola idea, la más valiosa, útil o sorprendente, tan concreta que se entienda de un vistazo, sin haber visto el vídeo ni leído el artículo.", "",
         f"Objetivo del negocio (tenlo en cuenta, sin vender en cada frase): {objetivo}", "",
         "REGLAS:",
         f"- Solo lo que dice el {fuente.lower()}: no inventes datos, cifras, pasos ni herramientas. Una cifra que se da como estimación va como aproximada.",
         "- «tipo» (el tono de la idea): \"pasos\" (cómo se hace algo), \"errores\" (lo que se hace mal), \"consejos\" (recomendaciones), \"ideas\" (un concepto o una observación).",
         "- «idea»: la idea completa en UNA frase de 16 palabras como máximo (será el texto principal de la segunda imagen).",
         f"- «gancho»: de 3 a {MAX_PALABRAS_GANCHO} palabras que enganchan y prometen esa idea; es el título de la portada. Sin cifras que no estén en el texto y sin prometer lo que no cumple. NO pongas un número de pasos, errores, consejos o claves en el gancho: la imagen solo enseña hasta {MAX_PUNTOS} puntos y un «6 pasos» que muestra 3 es una promesa rota.",
         "- «subtitulo»: hasta 10 palabras (para quién es o qué consigues).",
         f"- «puntos»: de 0 a {MAX_PUNTOS} puntos de HASTA {MAX_PALABRAS_PUNTO} palabras cada uno que explican o demuestran la idea. Con 0 puntos solo hay portada (1 imagen); con puntos, 2 imágenes. "
         "Solo ponlos si aportan; si la idea se entiende con el gancho, 0.",
         "- «cita»: de 4 a 12 palabras COPIADAS tal cual, seguidas, del texto, de la línea en que se basa la idea (sin cambiar ninguna palabra). El programa comprueba que existen: una idea que no se apoya en el texto se rechaza.",
         f"- «minuto»: el {'[Pn]' if de_texto else '[m:ss]'} EXACTO, tal como sale entre corchetes, de la línea donde está esa idea.",
         "- «icono»: la clave del SÍMBOLO que mejor representa la idea, de esta lista (o \"ninguno\" si ninguno encaja de verdad):", ilustraciones.catalogo_para_prompt(),
         "- «pie_instagram»: el texto de la publicación de Instagram (500 caracteres como mucho): la 1.ª línea con gancho, 2-4 líneas con lo útil y una línea que invite a guardar y a ver "
         f"{'el artículo completo en mi web' if de_texto else 'el vídeo completo en YouTube'} («enlace en mi perfil»). SIN hashtags ni URLs. 0-3 emojis.",
         f"- «texto_corto»: el texto para LinkedIn, Bluesky, Discord, Reddit y YouTube (230 caracteres como mucho): 2-3 frases con la idea y una invitación a {'leer el artículo' if de_texto else 'ver el vídeo'}. "
         "SIN hashtags, SIN URLs y sin emojis de relleno (el programa añade el enlace donde pone {ENLACE}).",
         "- Nunca escribas URLs.",
         "- «estilo»: el ESTILO GRÁFICO que mejor comunica ESTA idea (elige el que de verdad se acomoda; varía entre posts, no uses siempre el mismo). Opciones:"]
    L += [f"    · {k}: {v}" for k, v in PE.AYUDA.items()]
    L += ["- «datos»: SOLO los campos que pide el estilo elegido (los demás, vacíos). Todo con lo que dice el texto: ninguna cifra ni dato inventado. Con «clasico» déjalo vacío."]
    if anterior:
        L += [f"- El post anterior de esta fuente usó el estilo «{anterior}»: elige otro si encaja."]
    if evita:
        L += ["", "IDEAS QUE YA SE USARON EN OTROS POSTS (elige una DISTINTA):"] + [f"  · {x}" for x in evita[:8]]
    L += ["", f"DATOS DEL {fuente}: «{d['titulo']}» · etiquetas: {', '.join(d['tags']) or '—'}", "Capítulos:", d["capitulos"] or "(sin capítulos)", "",
          "TEXTO (cada línea = una frase, en orden):" if de_texto else "TRANSCRIPCIÓN del vídeo ya editado ([m:ss] = reloj del vídeo):"]
    if de_texto:
        L += [f"[P{k + 1}] {f['texto']}" for k, f in enumerate(d["frases"])]
    else:
        from .articulo import _mmss
        L += [f"[{_mmss(f['t'])}] {f['texto']}" for f in d["frases"]]
    L += ["", 'Responde SOLO con JSON: {"tipo", "idea", "gancho", "subtitulo", "puntos": [..], "cita", "minuto", "icono", "pie_instagram", "texto_corto", "estilo", "datos": {..}}']
    return "\n".join(L)


def esquema():
    try:
        from pydantic import BaseModel

        class Datos(BaseModel):
            frase: str = ""
            contexto: str = ""
            consejo: str = ""
            antes_titulo: str = ""
            antes: list[str] = []
            despues_titulo: str = ""
            despues: list[str] = []
            titulo: str = ""
            items: list[str] = []
            cifra: str = ""
            explica: str = ""
            fuente: str = ""
            etiqueta: str = ""
            donde: str = ""
            subtitulo: str = ""
            pregunta: str = ""
            respuesta: str = ""
            lineas: list[str] = []

        class Post(BaseModel):
            tipo: str
            idea: str
            gancho: str
            subtitulo: str
            puntos: list[str]
            cita: str
            minuto: str
            icono: str
            pie_instagram: str
            texto_corto: str
            estilo: str = "clasico"
            datos: Datos = Datos()
        return Post
    except Exception:  # noqa: BLE001
        return None


_PROMESA = re.compile(
    r"(?i)(?:\b(?:en|con|de|los|las|tus|estos|estas)\s+)*\b(\d{1,2})\s+(pasos|errores|consejos|claves|razones|ideas|formas|trucos|puntos|reglas|tips|herramientas|maneras|estrategias|"
    r"se\u00f1ales|fases|etapas|cosas|h\u00e1bitos|mitos|secretos|m\u00e9todos|t\u00e1cticas|ejemplos|factores|motivos|tareas|acciones|elementos)\b")


def sin_promesa_de_numero(texto: str, n_puntos: int) -> str:
    """Un titular que PROMETE «6 pasos» no puede salir con 2 o 3 puntos en la imagen (el autor, 14-oct-2026: «dice 6 pasos y muestra 2»): la imagen enseña como mucho `MAX_PUNTOS`.
    Si el número prometido es mayor que los puntos que se enseñan, se quita esa promesa («Plan de keyword research en 6 pasos» → «Plan de keyword research»); si no, se deja."""
    m = _PROMESA.search(texto or "")
    if not m:
        return texto
    try:
        n = int(m.group(1))
    except ValueError:
        return texto
    if n <= max(1, n_puntos):
        return texto
    if m.start() == 0 and m.group(0).strip()[0].isdigit():       # «5 errores que…» → «Errores que…» (se queda el sustantivo)
        t = (m.group(2) + texto[m.end():]).strip(" ,.:;-")
    else:                                                       # «…en 6 pasos» → se quita toda la promesa
        t = (texto[:m.start()] + texto[m.end():]).strip(" ,.:;-")
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"(?i)\s+(?:en|con|de|los|las|tus|estos|estas|y|para)$", "", t).strip(" ,.:;-")
    if len(t.split()) < 2:
        return texto                               # quedaría vacío: mejor dejarlo (el parser lo rechaza o lo corrige quien edita)
    return t[0].upper() + t[1:]


def parsea(texto: str, d: dict) -> dict:
    """El JSON de la IA -> el post LIMPIO: la cita comprobada contra la fuente (una idea que no se apoya en nada que se diga se RECHAZA para probar otro modelo), el gancho corto y que hable de lo que
    dice la fuente, los puntos cortos, el símbolo solo si existe en la biblioteca y los textos sin URLs ni hashtags."""
    from . import carrusel as K
    x = K._json_de(texto)
    if not isinstance(x, dict):
        raise ValueError("la respuesta no es un post")
    if K.ubica_cita(x.get("cita"), d["frases"]) < 0:
        raise ValueError("la idea no se apoya en nada que diga la fuente (su cita no está en el texto)")
    gancho = K._txt(x.get("gancho"), 70).strip(" .:")
    if not gancho or len(gancho.split()) > MAX_PALABRAS_GANCHO + 2 or not K._habla_del_video(gancho, K._vocabulario(d["frases"])):
        raise ValueError("el gancho está vacío, es demasiado largo o no habla de lo que dice la fuente")
    idea = K._txt(x.get("idea"), 140)
    if not idea:
        raise ValueError("falta la idea")
    puntos = []
    for p in x.get("puntos") or []:
        t = K._txt(p, 70).strip(" .:-·")
        if t and len(t.split()) <= MAX_PALABRAS_PUNTO + 3 and t not in puntos:
            puntos.append(t)
    puntos = puntos[:MAX_PUNTOS]
    gancho = sin_promesa_de_numero(gancho, len(puntos))     # nada de «6 pasos» si la imagen enseña 3
    idea = sin_promesa_de_numero(idea, len(puntos))
    from .ilus_escenas import CATALOGO
    ic = str(x.get("icono") or "").strip().lower()
    tipo = str(x.get("tipo") or "").strip().lower()
    corto = K._pie(x.get("texto_corto"), 260).replace("\n", " ")
    corto = re.sub(r"\{ENLACE\}", "", corto).strip()
    from . import posts_estilos as PE
    est = str(x.get("estilo") or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {"estilo_ia": est if est in PE.TODOS else "", "datos": PE.limpia_datos(x.get("datos")),
            "tipo": tipo if tipo in K.TIPOS else "ideas", "idea": idea, "gancho": gancho,
            "subtitulo": sin_promesa_de_numero(K._txt(x.get("subtitulo"), 90), len(puntos)), "puntos": puntos,
            "cita": K._txt(x.get("cita"), 200), "icono": ic if ic in CATALOGO else "",
            "pie_instagram": K._pie(x.get("pie_instagram"), 700) or f"{gancho}\n\n{idea}\n\nGuárdalo para cuando lo necesites.",
            "texto_corto": corto or K._txt(idea, 220)}


# ------------------------------------------------------------------ el dibujo (el estilo de las diapositivas del carrusel)
def _ruta_marca(ruta_rel: str) -> Path:
    p = Path(ruta_rel)
    return p if p.is_absolute() else Path(__file__).resolve().parent.parent / ruta_rel


def _hay(ruta_rel: str) -> bool:
    """¿Subiste este archivo de tu marca (retrato o logo)?"""
    try:
        return _ruta_marca(ruta_rel).is_file()
    except OSError:
        return False


def _retrato_circulo(diam_px: int, ruta_rel: str):
    p = _ruta_marca(ruta_rel)
    if not p.is_file():                                            # sin retrato ni logo subidos: un círculo neutro (la portada ni lo elige: `elige_portada`)
        return Image.new("RGBA", (diam_px, diam_px), (215, 215, 215, 255))
    return ImageOps.fit(Image.open(p).convert("RGBA"), (diam_px, diam_px), Image.LANCZOS)


def _circulo(h, cx: float, cy: float, diam: float, imagen: Image.Image, sombra: float = 12) -> None:
    """La imagen en un círculo con anillo crema, contorno de tinta y sombra dura (el lenguaje de las tarjetas)."""
    px, d = h.px, h.d
    r = diam / 2
    d.ellipse([px(cx - r + sombra), px(cy - r + sombra), px(cx + r + sombra), px(cy + r + sombra)], fill=h.tinta)
    d.ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)], fill=h.crema, outline=h.tinta, width=px(6))
    ri = r - 14
    lado = px(2 * ri)
    im = ImageOps.fit(imagen.convert("RGBA"), (lado, lado), Image.LANCZOS)
    mascara = Image.new("L", (lado, lado), 0)
    ImageDraw.Draw(mascara).ellipse([0, 0, lado - 1, lado - 1], fill=255)
    h.im.paste(im, (px(cx - ri), px(cy - ri)), mascara)
    d.ellipse([px(cx - ri), px(cy - ri), px(cx + ri), px(cy + ri)], outline=h.tinta, width=px(4))


def dibuja_portada(p: dict, fmt: dict, marca: dict, cfg=None, dir_caras=None) -> object:
    """La portada: fondo naranja con trama, «IDEA» + el gancho en grande (crema con contorno de tinta y sombra dura, la última palabra en dorado), el subtítulo en una tarjeta y el elemento
    VARIABLE (`p["portada"]`: tu cara, el logo en grande o tu retrato en cómic). En Instagram, el marcador de «guardar»."""
    from . import carrusel as K
    pal = K._pal_de(marca)
    h = K._Hoja(fmt, pal, "naranja")
    x0, x1, y0, y1 = fmt["x0"], fmt["x1"], fmt["y0"], fmt["y1"]
    ancho = fmt["ancho"] > fmt["alto"]
    zona_w = x1 - x0
    col_w = zona_w * (0.57 if ancho else 1.0)
    chip = h.pastilla(x0, y0, "IDEA", pal.fuente_numero, 36, h.dorado, h.tinta, nombre="chip")
    if fmt.get("guardar"):                                     # el símbolo de «guardar» (solo Instagram)
        mw, mh = 70, 100
        h.marcador(x1 - mw - 8, y0 - 6, mw, mh, h.crema)
        h.anota("marcador", (x1 - mw - 8, y0 - 6, x1 - 8, y0 + mh))
    y_t = chip[3] + 36
    alto_t = (fmt["alto"] * 0.36) if not ancho else (fmt["alto"] * 0.46)
    b_t = h.bloque(p["gancho"], pal.fuente_numero, col_w, alto_t, 128 if not ancho else 112, 60, 5, inter=1.04, mayus=True)
    palabras = p["gancho"].upper().split()
    con_cifra = {i for i, w in enumerate(palabras) if any(c.isdigit() for c in w)}
    realce = con_cifra or {len(palabras) - 1}
    trazo = max(3.0, b_t["tam"] * 0.075)
    caja_t = h.dibuja(b_t, x0, y_t, h.crema, col_w, False, trazo=trazo, sombra=max(4.0, b_t["tam"] * 0.07), realce=realce, color_realce=h.dorado, nombre="titulo")
    b_s, y_s = None, 0.0
    if p.get("subtitulo"):
        b_s = h.bloque(p["subtitulo"], pal.fuente_titulo, col_w - 64, 160, 44, 30, 3, inter=1.2)
        y_s = caja_t[3] + 30
        h.tarjeta(x0, y_s, x0 + col_w - 12, y_s + b_s["alto"] + 52, h.crema, radio=18, borde=5, sombra=9)
        h.dibuja(b_s, x0 + 32, y_s + 26, h.tinta, col_w - 64, False, nombre="subtitulo")
        h.anota("tarjeta", (x0, y_s, x0 + col_w, y_s + b_s["alto"] + 52))
    y_libre = (y_s + b_s["alto"] + 52 if b_s else caja_t[3]) + 30           # (donde acaba el texto: de ahí para abajo, en Instagram, va lo visual)
    hueco = fmt["alto"] - y_libre - PIE_ALTO_P - 10
    tipo = p.get("portada") or "logo"
    if tipo == "cara":
        alto_f = (fmt["alto"] * 0.80) if ancho else max(260.0, min(hueco + PIE_ALTO_P, fmt["alto"] * 0.46))
        cara = K._cara_para_portada(cfg, alto_f, dir_caras, p.get("cara_gesto"))
        if cara is not None:
            im, pad = cara
            tope_w = max(300.0, zona_w - col_w - 20) if ancho else zona_w * 0.58
            esc = min(1.0, tope_w / max(1.0, (im.width - 2 * pad) / SS))
            if esc < 1.0:
                im = im.resize((max(1, round(im.width * esc)), max(1, round(im.height * esc))), Image.LANCZOS)
                pad = round(pad * esc)
            aw, ah = im.width / SS, im.height / SS
            h.im.paste(im, (h.px(x1 + 26 - (aw - 2 * pad / SS)), h.px(fmt["alto"] - (ah - pad / SS))), im)
            h.anota("visual", (x1 + 26 - (aw - 2 * pad / SS), fmt["alto"] - (ah - 2 * pad / SS), x1 + 26, fmt["alto"]))
        else:
            tipo = "logo" if _hay(_LOGO) else "ninguna"
    if tipo in ("logo", "comic"):
        if ancho:
            diam = max(220.0, min(fmt["alto"] * 0.64, zona_w - col_w - 36))     # (nunca más ancho que lo que deja libre el texto: en 4:3 el círculo tapaba el titular)
            cx, cy = x1 - diam / 2 - 8, fmt["alto"] / 2 + 6
        else:
            diam = max(240.0, min(zona_w * 0.46, hueco - 20))
            cx, cy = x1 - diam / 2 - 8, fmt["alto"] - diam / 2 - PIE_ALTO_P - 28
        im = _retrato_circulo(h.px(diam), _LOGO if tipo == "logo" else _RETRATO)
        _circulo(h, cx, cy, diam, im)
        h.anota("visual", (cx - diam / 2, cy - diam / 2, cx + diam / 2, cy + diam / 2))
    if not ancho and p.get("imagenes", 1) > 1:
        des = h.pastilla(x0, y1 - PIE_ALTO_P - 64 - 22, "DESLIZA", pal.fuente_numero, 34, h.tinta, h.crema, pad_x=28, pad_y=14, extra_der=64, nombre="desliza")
        h.flecha(des[2] - 28 - 40, des[1] + (des[3] - des[1]) / 2 - 13, 40, 26, h.dorado)
    h.pie(p.get("canal", ""), p.get("web", ""), linea=False, derecha=False)
    return h


def dibuja_idea(p: dict, fmt: dict, marca: dict, simbolo: Image.Image | None, logo=None) -> object:
    """La segunda imagen, «LA IDEA»: la idea completa en una tarjeta, hasta 3 puntos y el símbolo de tu biblioteca; fondo crema. En Instagram, el marcador de guardar."""
    from . import carrusel as K
    pal = K._pal_de(marca)
    h = K._Hoja(fmt, pal, "crema")
    x0, x1, y0, y1 = fmt["x0"], fmt["x1"], fmt["y0"], fmt["y1"]
    ancho = fmt["ancho"] > fmt["alto"]
    zona_w = x1 - x0
    chip = h.pastilla(x0, y0, "LA IDEA", pal.fuente_numero, 38, h.naranja, h.crema, nombre="chip")
    x_cont = x1 - (84 if not ancho else 0)
    h.d.text((h.px(x_cont), h.px(chip[1] + (chip[3] - chip[1]) / 2 + 12)), "2/2", font=h.fuente(pal.fuente_texto, 34), fill=h.tinta, anchor="rs")
    if fmt.get("guardar"):                                     # el símbolo de «guardar» (solo Instagram)
        h.marcador(x1 - 62, y0 - 6, 54, 78, h.naranja, sombra=7)
        h.anota("marcador", (x1 - 62, y0 - 6, x1 - 8, y0 + 72))
    arriba, abajo = chip[3] + 36, y1 - K.PIE_ALTO - 26
    puntos = p.get("puntos") or []
    col_w = zona_w * (0.56 if ancho else 1.0) if (simbolo is not None and ancho) else zona_w
    # la idea en una tarjeta
    pad = 34
    b_i = h.bloque(p["idea"], pal.fuente_titulo, col_w - 2 * pad - 12, max(120.0, (abajo - arriba) - 2 * pad - 34 - 98 * len(puntos[:MAX_PUNTOS])) if (puntos and ancho) else (abajo - arriba) * (0.42 if puntos else 0.7),
                 62 if not ancho else 54, 34, 6, inter=1.2)      # (apaisado: la idea deja sitio a TODOS los puntos; antes el 3.º se caía en silencio)
    alto_i = b_i["alto"] + 2 * pad
    y = arriba
    if simbolo is not None and not ancho:                      # (Instagram: el símbolo arriba, con el hueco que sobre)
        resto = (abajo - arriba) - alto_i - (len(puntos) * 98 if puntos else 0) - 60
        if resto >= 230:
            ch = min(resto, 400)
            cw = min(zona_w - 24, ch * 1.6)
            ch = cw / 1.6
            cx0 = x0 + (zona_w - 12 - cw) / 2
            h.tarjeta_imagen(cx0, y, cx0 + cw, y + ch, simbolo, radio=16, borde=5, sombra=11)
            h.anota("simbolo", (cx0, y, cx0 + cw + 11, y + ch + 11))
            y += ch + 34
    h.tarjeta(x0, y, x0 + col_w - 12, y + alto_i, h.crema, radio=20, borde=5, sombra=12)
    h.dibuja(b_i, x0 + pad, y + pad, h.tinta, col_w - 2 * pad - 12, False, nombre="idea")
    h.anota("tarjeta", (x0, y, x0 + col_w, y + alto_i + 12))
    y += alto_i + 34
    for k, t in enumerate(puntos[:MAX_PUNTOS], 1):
        b_p = h.bloque(t, pal.fuente_titulo, col_w - 150, 90, 40 if not ancho else 36, 26, 2, inter=1.15)
        alto_p = max(78, b_p["alto"] + 28)
        if y + alto_p > abajo:
            break
        h.tarjeta(x0, y, x0 + col_w - 12, y + alto_p, h.crema, radio=16, borde=4, sombra=8)
        cx, cy = x0 + 46, y + alto_p / 2
        h.d.ellipse([h.px(cx - 27), h.px(cy - 27), h.px(cx + 27), h.px(cy + 27)], fill=h.naranja, outline=h.tinta, width=h.px(4))
        h.d.text((h.px(cx), h.px(cy + 1)), str(k), font=h.fuente(pal.fuente_numero, 34), fill=h.crema, anchor="mm")
        h.dibuja(b_p, x0 + 96, y + (alto_p - b_p["alto"]) / 2, h.tinta, col_w - 150, False, nombre=f"punto{k}")
        y += alto_p + 20
    if simbolo is not None and ancho:                          # (16:9: el símbolo a la derecha, en una tarjeta)
        cw = zona_w - col_w - 36
        ch = min(cw / 1.6, abajo - arriba)
        cx0, cy0 = x1 - cw - 8, arriba + max(0.0, (abajo - arriba - ch) / 2)
        h.tarjeta_imagen(cx0, cy0, cx0 + cw, cy0 + ch, simbolo, radio=16, borde=5, sombra=11)
        h.anota("simbolo", (cx0, cy0, cx0 + cw + 11, cy0 + ch + 11))
    h.pie(p.get("canal", ""), p.get("web", ""), logo=logo)
    return h


def dise_de(fmt: dict) -> tuple[dict, tuple[int, int] | None]:
    """Las medidas del diseño están pensadas para un lienzo de ~1600 (apaisado) o ~1080 (vertical) de ancho: en un lienzo pequeño (LinkedIn 1200×627, Bluesky 1200×675) el texto no cabe.
    Con `dise` se dibuja en un lienzo MAYOR de la MISMA proporción y se reduce al tamaño final. -> (el formato con que dibujar, el tamaño final si hay que reducir o None)."""
    anch = fmt.get("dise")
    if not anch:
        return fmt, None
    k = anch / fmt["ancho"]
    g = dict(fmt)
    for c in ("ancho", "alto", "x0", "x1", "y0", "y1"):
        g[c] = int(round(fmt[c] * k))
    return g, (fmt["ancho"], fmt["alto"])


def _guarda_jpg(img, destino: Path, max_kb: int | None = None) -> None:
    """Guarda el JPEG; si la red pone un tope de peso (Bluesky ~1 MB por imagen), baja la calidad hasta que quepa."""
    q = CALIDAD_JPG
    img.save(destino, "JPEG", quality=q, subsampling=0, optimize=True)
    while max_kb and destino.stat().st_size > max_kb * 1024 and q > 55:
        q -= 6
        img.save(destino, "JPEG", quality=q, subsampling=0 if q > 85 else 2, optimize=True)


def renderiza(p: dict, carpeta: Path, marca: dict, cfg=None, dir_caras=None, progreso=None) -> dict:
    """Dibuja las imágenes del post, UNA carpeta por red con SU tamaño. -> {"instagram": [archivos], "linkedin": [...], …}"""
    from . import carrusel as K
    from . import carrusel_texto as CT
    from . import escena_completa
    out: dict = {}
    simbolo = None
    if p.get("icono") and p.get("imagenes", 1) > 1:
        simbolo = CT.render_icono(p["icono"], marca, "naranja")
    logo = K._logo_pie(cfg)
    from . import posts_estilos as PE
    est = p.get("estilo") or "clasico"
    p["dims"] = {}
    try:
        if est != "clasico":                                   # un estilo nuevo = UNA imagen cuadrada, la misma en todas las redes
            if progreso:
                progreso(f"Dibujando el estilo «{PE.NOMBRE.get(est, est)}»…")
            img = PE.dibuja(est, PE.completa(est, p.get("datos"), p) or {}, marca, p.get("canal", ""), p.get("web", ""), cfg, dir_caras)
            for clave, fmt in FORMATOS.items():
                sub = carpeta / clave
                if sub.exists():
                    shutil.rmtree(sub, ignore_errors=True)
                sub.mkdir(parents=True, exist_ok=True)
                _guarda_jpg(img, sub / "post-01.jpg", fmt.get("max_kb"))
                out[clave] = ["post-01.jpg"]
                p["dims"][clave] = list(img.size)
            return out
        for clave, fmt in FORMATOS.items():
            sub = carpeta / clave
            if sub.exists():
                shutil.rmtree(sub, ignore_errors=True)
            sub.mkdir(parents=True, exist_ok=True)
            if progreso:
                progreso(f"Dibujando el post de {fmt['nombre']}…")
            fd, final = dise_de(fmt)
            hojas = [dibuja_portada(p, fd, marca, cfg, dir_caras)]
            if p.get("imagenes", 1) > 1:
                hojas.append(dibuja_idea(p, fd, marca, simbolo, logo=logo))
            nombres = []
            for k, hoja in enumerate(hojas, 1):
                nombre = f"post-{k:02d}.jpg"
                img = hoja.final()
                if final and img.size != final:
                    img = img.resize(final, Image.LANCZOS)
                _guarda_jpg(img, sub / nombre, fmt.get("max_kb"))
                nombres.append(nombre)
            out[clave] = nombres
            p["dims"][clave] = [fmt["ancho"], fmt["alto"]]
    finally:
        escena_completa._fondo.cache_clear()
    return out


# ------------------------------------------------------------------ crear / rehacer
def _id_nuevo() -> str:
    return f"p-{time.strftime('%Y%m%d-%H%M%S')}-{hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:4]}"


def _termina(p: dict, carpeta: Path, ctx: dict, cfg, dir_caras, progreso) -> dict:
    from . import posts_estilos as PE
    est = p.get("estilo") or "clasico"
    if est != "clasico" and PE.completa(est, p.get("datos"), p) is None:
        est = p["estilo"] = "clasico"                            # (el estilo ya no se puede hacer con lo que hay: vuelve al de siempre)
    p["imagenes"] = 2 if (est == "clasico" and p.get("puntos")) else 1
    p["avisos"] = [] if (est != "clasico" or p.get("puntos")) else ["Esta idea se explica sola con el gancho: sale UNA imagen (la portada)."]
    p["archivos"] = renderiza(p, carpeta, ctx["marca"], cfg, dir_caras, progreso)
    (carpeta / "post.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Post {p['id']} de {p['jid']}: estilo «{p.get('estilo', 'clasico')}», {p['imagenes']} imagen(es), portada «{p['portada']}»{' (' + p['cara_gesto'] + ')' if p.get('portada') == 'cara' else ''}.")
    return {"ok": True, "post": p}


def crea(jid: str, cfg, dir_outbox=None, dir_work=None, _llama=None, progreso=None, dir_caras=None, rng=None, evita: list[str] | None = None, enlace: str = "") -> dict:
    """Un post de una fuente (un vídeo terminado, un `texto-…--pro` o un `enlace-…--pro`): UNA petición a Flash-Lite -> la idea -> 1-2 imágenes en 4:5 y 16:9. Nunca lanza.
    `evita`: ideas de posts anteriores de esa fuente (para «↻ Otro post»). -> {"ok": True, "post": {...}} o {"ok": False, "error"}."""
    from . import carrusel as K
    from .util import nombre_modelo
    try:
        d = K._datos(jid, dir_outbox, dir_work)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": f"No encuentro todo lo de esta fuente en el PC ({str(exc)[:160]})."}
    if len(d["frases"]) < 5:
        return {"ok": False, "error": "Hace falta más texto (o voz transcrita) para sacar de ahí una idea."}
    ctx = K._contexto(cfg, d)
    objetivo = str((cfg.get_path("ideas.objetivo") if cfg is not None else "") or "Conseguir clientes para sus servicios de SEO; crecer la audiencia es el medio.")
    previos = sorted(lista(jid, dir_outbox), key=lambda x: -float(x.get("ts") or 0))
    anterior = str(previos[0].get("estilo") or "clasico") if previos else ""
    prompt = arma_prompt(d, ctx["nombre"] or "tu canal", objetivo, evita, anterior)
    if progreso:
        progreso("Eligiendo la idea y escribiendo el post (Flash-Lite)…")
    try:
        from . import uso as _uso
        _uso.contexto(job=jid)
        try:
            llama = _llama or (lambda pr, va: _post_gemini(cfg, pr, ctx["nombre"] or "tu canal", va))
            p, modelo = llama(prompt, lambda t: parsea(t, d))
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Post de {jid}: {exc}")
        from .ideas import _error_claro
        return {"ok": False, "error": _error_claro(exc).replace("las ideas", "el post")}
    pid = _id_nuevo()
    carpeta = ruta(jid, pid, dir_outbox)
    carpeta.mkdir(parents=True, exist_ok=True)
    portada = elige_portada(cfg, dir_caras, rng)
    p.update(id=pid, version=VERSION, jid=jid, ts=time.time(), modelo=modelo, nombre_modelo=nombre_modelo(modelo), canal=ctx["nombre"], web=ctx["web"], fuente_titulo=d["titulo"],
             origen=d.get("origen", "video"), portada=portada, hashtags=K._hashtags(cfg, d), enlace=enlace)
    if portada == "cara":
        p["cara_gesto"] = K.elige_gesto(cfg, dir_caras, rng, tipo=p.get("tipo"))
    from . import posts_estilos as PE
    try:
        permitidos = cfg.get_path("posts.estilos") if cfg is not None else None
        permitidos = set(permitidos) & set(PE.TODOS) if isinstance(permitidos, list) and permitidos else None
    except Exception:  # noqa: BLE001
        permitidos = None
    p["estilo"] = PE.elige(p, rng, anterior, permitidos)
    return _termina(p, carpeta, ctx, cfg, dir_caras, progreso)


def _post_gemini(cfg, prompt: str, canal: str, valida):
    from . import modelos as _m
    from . import util as _util
    from .director import _gemini as _abre
    g = _abre(cfg)
    if not g:
        raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso».")
    client, types = g[:2]
    res, modelo, _u = _util.call_gemini_cadena(client, types, _m.cadena_lite(cfg, client), prompt, system=_SYSTEM.format(canal=canal), valida=valida, reintento_s=60.0, pasadas=1,
                                               timeout_s=150.0, esquema=esquema(), etiqueta="post")
    return res, modelo


def rehaz(jid: str, pid: str, cambios: dict, cfg, dir_outbox=None, dir_work=None, progreso=None, dir_caras=None, rng=None) -> dict:
    """Vuelve a dibujar con lo que cambiaste a mano (gancho, subtítulo, idea, puntos, textos y/o la portada: «cara», «logo», «comic» o «azar») SIN IA. Nunca lanza."""
    from . import carrusel as K
    p = lee(jid, pid, dir_outbox)
    if not p:
        return {"ok": False, "error": "No encuentro ese post."}
    cambios = cambios if isinstance(cambios, dict) else {}
    try:
        d = K._datos(jid, dir_outbox, dir_work)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": f"No encuentro todo lo de esta fuente en el PC ({str(exc)[:160]})."}
    ctx = K._contexto(cfg, d)
    g = K._txt(cambios.get("gancho"), 70).strip(" .:") if "gancho" in cambios else p["gancho"]
    if g:
        p["gancho"] = g
    for k, tope in (("subtitulo", 90), ("idea", 140)):
        if k in cambios:
            v = K._txt(cambios.get(k), tope)
            if v or k == "subtitulo":
                p[k] = v
    if "puntos" in cambios and isinstance(cambios["puntos"], list):
        p["puntos"] = [K._txt(x, 70).strip(" .:-·") for x in cambios["puntos"] if K._txt(x, 70).strip(" .:-·")][:MAX_PUNTOS]
    if "pie_instagram" in cambios:
        p["pie_instagram"] = K._pie(cambios["pie_instagram"], 2000) or p["pie_instagram"]
    if "texto_corto" in cambios:
        p["texto_corto"] = K._pie(cambios["texto_corto"], 400).replace("\n", " ") or p["texto_corto"]
    from . import posts_estilos as PE
    if any(k in cambios for k in ("gancho", "idea", "puntos", "subtitulo")):
        p["datos"] = {}                                         # (editaste el texto: los estilos se rehacen con lo que escribiste, no con lo que dio la IA)
    pe = str(cambios.get("estilo") or "")
    if pe == "azar":
        pos = [e for e in PE.posibles(p) if e != p.get("estilo")] or ["clasico"]
        p["estilo"] = (rng or __import__("random")).choices(pos)[0]
    elif pe in PE.TODOS:
        if PE.completa(pe, p.get("datos"), p) is None:
            return {"ok": False, "error": f"El estilo «{PE.NOMBRE.get(pe, pe)}» necesita datos que esta idea no tiene (p. ej. una cifra o un antes/después). Prueba otro estilo."}
        p["estilo"] = pe
    elif p.get("estilo") not in (None, "clasico") and PE.completa(p["estilo"], p.get("datos"), p) is None:
        p["estilo"] = "clasico"
    pedida = str(cambios.get("portada") or "")
    if pedida == "azar":
        p["portada"] = elige_portada(cfg, dir_caras, rng)
        if p["portada"] == "cara":
            p["cara_gesto"] = K.elige_gesto(cfg, dir_caras, rng, tipo=p.get("tipo"))
    elif pedida in PORTADAS and pedida != p.get("portada"):
        p["portada"] = pedida
        if pedida == "cara":
            p["cara_gesto"] = K.elige_gesto(cfg, dir_caras, rng, tipo=p.get("tipo"))
    p["ts_edicion"] = time.time()
    return _termina(p, ruta(jid, pid, dir_outbox), ctx, cfg, dir_caras, progreso)


# ------------------------------------------------------------------ descargas
def _corta(texto: str, tope: int) -> str:
    """Corta en palabra entera con «…» si pasa del tope."""
    texto = texto.strip()
    if len(texto) <= tope:
        return texto
    return texto[:max(1, tope - 1)].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def textos(p: dict) -> dict:
    """Los textos listos para pegar, UNO POR RED (sin IA: salen del texto de Instagram y del corto que escribió la IA): Instagram (con hashtags); LinkedIn y YouTube y Discord (el corto con el enlace);
    Bluesky (≤ 300 caracteres CONTANDO el enlace) y Reddit (un título ≤ 120 y el cuerpo). `corto` = el texto de las demás redes (se mantiene por compatibilidad)."""
    ig = (p.get("pie_instagram", "").rstrip() + ("\n\n" + p["hashtags"] if p.get("hashtags") else "")).strip()
    enlace = p.get("enlace") or ""
    base = (p.get("texto_corto") or "").strip()
    corto = (base + (f"\n{enlace}" if enlace else "")).strip()
    gancho = str(p.get("gancho") or "").strip()
    cola = f"\n{enlace}" if enlace else ""
    bluesky = _corta(base, 300 - len(cola)) + cola
    reddit = f"Título: {_corta(gancho, 120)}\n\n{corto}".strip()
    return {"instagram": ig, "corto": corto, "redes": {"instagram": ig, "linkedin": corto, "youtube": corto, "bluesky": bluesky, "reddit": reddit, "discord": corto}}


def _carpeta_zip(clave: str) -> str:
    """El nombre de la carpeta de cada red dentro del .zip de «todo»."""
    return re.sub(r"[^A-Za-z0-9]+", "-", (FORMATOS.get(clave, {}).get("nombre") or LEGADO.get(clave) or clave)).strip("-")


def zip_de(jid: str, pid: str, formato: str, dir_outbox=None) -> tuple[bytes, str] | None:
    """`formato` = la clave de una red (sus imágenes + su texto, sueltas) o «todo»: UNA CARPETA por red (Instagram/, LinkedIn/, …) con sus imágenes en SU tamaño y su `texto.txt`."""
    p = lee(jid, pid, dir_outbox)
    if not p or formato not in CLAVES + ("todo",):
        return None
    carpeta = ruta(jid, pid, dir_outbox)
    t = textos(p)
    archivos = p.get("archivos") or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for clave in [c for c in CLAVES if c in archivos]:
            if formato not in (clave, "todo"):
                continue
            pref = (_carpeta_zip(clave) + "/") if formato == "todo" else ""
            for n in archivos.get(clave, []):
                if (carpeta / clave / n).is_file():
                    z.write(carpeta / clave / n, pref + n)
            texto = t["redes"].get(clave) or (t["instagram"] if clave == "ig" else t["corto"])
            z.writestr(pref + "texto.txt", texto + "\n")
    from .carrusel import _sin_tildes
    nombre = re.sub(r"[^a-z0-9]+", "-", _sin_tildes(str(p.get("gancho") or pid).lower())).strip("-")[:50] or "post"
    return buf.getvalue(), f"post-{nombre}-{formato}.zip"


# ------------------------------------------------------------------ pruebas (sin red: una IA de mentira y un texto pegado; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import collections
    import random
    import tempfile

    from PIL import ImageChops

    from . import carrusel as K
    from . import carrusel_texto as CT
    from . import config as _config
    global _RETRATO, _LOGO
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    # (la copia que se comparte no trae retrato ni logo tuyos: las pruebas usan unos de mentira)
    _dir_marca = Path(tempfile.mkdtemp(prefix="posts_marca_"))
    for _n_, _col_ in (("retrato", (200, 90, 50)), ("logo", (40, 90, 200))):
        _im_ = Image.new("RGB", (300, 300), _col_)
        ImageDraw.Draw(_im_).ellipse([60, 60, 240, 240], fill=(250, 230, 200))
        _im_.save(_dir_marca / f"{_n_}.png")
    _RETRATO, _LOGO = str(_dir_marca / "retrato.png"), str(_dir_marca / "logo.png")

    cfg = _config.load()
    frases_t = ["Primero revisa el sitemap de tu sitio con calma. Debe llevar solo las páginas que quieres que Google indexe.",
                "Después comprueba que el archivo robots no bloquee las páginas importantes. Un solo error ahí puede esconder todo tu sitio.",
                "Usa la etiqueta canonical para decirle a Google cuál es la página original. Así evitas el contenido duplicado entre direcciones parecidas.",
                "Revisa tus enlaces internos para que lleven a las páginas más valiosas. Cada enlace reparte autoridad dentro de tu sitio.",
                "Mide la velocidad de carga de tus páginas con una herramienta gratis. Una página lenta pierde visitas antes de mostrar nada."]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        out = t / "videos"
        caras = t / "mis_caras"
        caras.mkdir()
        im_c = Image.new("RGBA", (340, 440), (0, 0, 0, 0))
        ImageDraw.Draw(im_c).ellipse([20, 20, 320, 420], fill=(200, 150, 120, 255))
        im_c.save(caras / "cara feliz mirando hacia la derecha.png")
        im_c.save(caras / "cara decepcion mirando al frente.png")
        r0 = CT.prepara("\n\n".join(frases_t), "Guía de indexación", "", cfg, out)
        jid = r0["jid"]
        d = K._datos(jid, out, out)
        k = next(i for i, f in enumerate(d["frases"]) if "canonical" in f["texto"].lower())
        base = {"tipo": "errores", "idea": "Si no le dices a Google cuál es la página principal, elige él solo.", "gancho": "Google elige tu página principal", "subtitulo": "Evita contenido duplicado",
                "puntos": ["Elige la URL principal", "Añade la canonical", "Comprueba en Search Console"], "cita": " ".join(d["frases"][k]["texto"].split()[:7]), "minuto": f"P{k + 1}",
                "icono": "lupa", "pie_instagram": "Una sola idea\nGuárdala https://x.com #seo", "texto_corto": "Si no le dices a Google cuál es tu página principal, elige él. {ENLACE} https://x.com #seo"}
        pedidos: list = []

        def llama(resp):
            def f(pr, va):
                pedidos.append(pr)
                return va(json.dumps(resp)), "gemini-fake-lite"
            return f

        # --- la portada variable: 1/3 cada una
        cuenta = collections.Counter(elige_portada(cfg, caras, random.Random(i)) for i in range(3000))
        caso("la portada sale al azar con el MISMO peso: ~1/3 tu cara, ~1/3 el logo en grande y ~1/3 tu retrato en cómic", all(0.30 < cuenta[k_] / 3000 < 0.37 for k_ in PORTADAS), cuenta)
        sin_caras = collections.Counter(elige_portada(cfg, t / "no_hay", random.Random(i)) for i in range(600))
        caso("sin fotos de tu cara se reparte entre el logo y el retrato (nunca falla ni sale «cara»)", set(sin_caras) == {"logo", "comic"} and 0.4 < sin_caras["logo"] / 600 < 0.6, sin_caras)

        class CfgPesos:
            def get_path(self, k_, d_=None):
                return {"cara": 0, "logo": 3, "comic": 1} if k_ == "posts.portadas" else cfg.get_path(k_, d_)
        cp = collections.Counter(elige_portada(CfgPesos(), caras, random.Random(i)) for i in range(1200))
        caso("los pesos se cambian en config.yaml (posts.portadas): con cara 0 no sale tu cara y el logo sale ~3 veces más que el retrato", "cara" not in cp and 2.4 < cp["logo"] / max(1, cp["comic"]) < 3.8, cp)

        # --- el parser
        ok = parsea(json.dumps(base), d)
        caso("el parser deja el post limpio: puntos (3 como mucho), símbolo válido, y los textos sin URLs, hashtags ni {ENLACE}",
             ok["gancho"] == "Google elige tu página principal" and len(ok["puntos"]) == 3 and ok["icono"] == "lupa" and "http" not in ok["pie_instagram"] + ok["texto_corto"]
             and "#" not in ok["pie_instagram"] + ok["texto_corto"] and "{ENLACE}" not in ok["texto_corto"], ok)
        caso("un titular que promete «6 pasos» pero enseña 3 puntos pierde la promesa; con los puntos que dice (o sin número) se deja",
             sin_promesa_de_numero("Plan de keyword research en 6 pasos", 3) == "Plan de keyword research"
             and sin_promesa_de_numero("3 errores que te cuestan tráfico", 3) == "3 errores que te cuestan tráfico"
             and sin_promesa_de_numero("5 errores que te cuestan tráfico", 3) == "Errores que te cuestan tráfico"
             and sin_promesa_de_numero("Google elige tu página principal", 0) == "Google elige tu página principal"
             and sin_promesa_de_numero("6 pasos", 2) == "6 pasos",
             [sin_promesa_de_numero("Plan de keyword research en 6 pasos", 3), sin_promesa_de_numero("5 errores que te cuestan tráfico", 3)])
        pr = parsea(json.dumps({**base, "gancho": "Google elige tu página principal en 6 pasos"}), d)
        caso("el parser lo aplica: el gancho que sale no promete más pasos de los que hay en la imagen", "6" not in pr["gancho"] and len(pr["puntos"]) == 3, pr["gancho"])
        caso("el símbolo que no existe en la biblioteca se quita y más de 3 puntos se recortan a 3", parsea(json.dumps({**base, "icono": "inventado"}), d)["icono"] == ""
             and len(parsea(json.dumps({**base, "puntos": base["puntos"] + ["Otro punto más"]}), d)["puntos"]) == 3)
        for nombre, malo in (("una idea cuya cita no está en la fuente", {**base, "cita": "instala el pixel de facebook en tu tienda online ya"}),
                             ("un gancho larguísimo", {**base, "gancho": "Cómo conseguir que Google elija siempre la página principal de tu sitio web"}),
                             ("un gancho de otro tema", {**base, "gancho": "Instala el pixel de seguimiento"}), ("una idea vacía", {**base, "idea": ""})):
            try:
                parsea(json.dumps(malo), d)
                caso(f"se rechaza {nombre} (para probar otro modelo)", False)
            except ValueError:
                caso(f"se rechaza {nombre} (para probar otro modelo)", True)

        # --- de punta a punta
        class Fijo(random.Random):
            def __init__(self, elegida):
                super().__init__(1)
                self.elegida = elegida

            def choices(self, pop, weights=None, k=1, **kw):
                return [self.elegida if self.elegida in pop else pop[0]]
        r = crea(jid, cfg, out, out, llama(base), dir_caras=caras, rng=Fijo("cara"), enlace="https://youtu.be/abc")
        p = r.get("post") or {}
        carp = ruta(jid, p.get("id", "p-00000000-000000-0000"), out)
        med = {c: [Image.open(carp / c / n).size for n in (p.get("archivos") or {}).get(c, [])] for c in FORMATOS}
        caso("de punta a punta: UNA petición, con puntos salen 2 imágenes POR RED y cada una en SU tamaño (Instagram 1080×1350, LinkedIn 1200×627, YouTube 1080×1080, Bluesky 1200×675, Reddit 1440×1080, Discord 1600×900)",
             r.get("ok") and len(pedidos) == 1 and all(med[k_] == [(f["ancho"], f["alto"])] * 2 for k_, f in FORMATOS.items()) and (carp / "post.json").is_file() and p["portada"] == "cara", r.get("error") or med)
        caso("Bluesky: cada imagen pesa menos de su tope (su límite es ~1 MB por imagen)", all((carp / "bluesky" / n).stat().st_size <= FORMATOS["bluesky"]["max_kb"] * 1024 for n in p["archivos"]["bluesky"]),
             [(carp / "bluesky" / n).stat().st_size for n in p["archivos"]["bluesky"]])
        caso("la cara depende del contexto de la idea: un post de «errores» no sonríe (no sale «feliz»)", p.get("cara_gesto") not in (None, "feliz"), p.get("cara_gesto"))
        sin_p = crea(jid, cfg, out, out, llama({**base, "puntos": []}), dir_caras=caras, rng=Fijo("logo"))
        caso("sin puntos sale UNA imagen (la portada) por tamaño y se avisa", sin_p["ok"] and all(len(sin_p["post"]["archivos"][k_]) == 1 for k_ in FORMATOS)
             and sin_p["post"]["avisos"], sin_p.get("error"))
        ev: list = []
        crea(jid, cfg, out, out, lambda pr, va: (ev.append(pr), (va(json.dumps(base)), "fake"))[1], dir_caras=caras, evita=["IDEA_YA_USADA_XYZ"])
        caso("«↻ Otro post» le dice a la IA las ideas ya usadas para que elija otra distinta", ev and "IDEA_YA_USADA_XYZ" in ev[0])

        def cae(pr, va):
            raise RuntimeError("Gemini saturado")
        n_antes = len(lista(jid, out))
        mal = crea(jid, cfg, out, out, cae, dir_caras=caras)
        caso("si Gemini no responde no se crea nada a medias y el error dice qué hacer", not mal["ok"] and mal["error"] and len(lista(jid, out)) == n_antes, mal)
        # --- las tres portadas se dibujan distintas
        marca = dict(cfg.get_path("videos.marca") or {})
        hojas = {}
        for tp in PORTADAS:
            hojas[tp] = dibuja_portada({**p, "portada": tp, "cara_gesto": "feliz", "imagenes": 2}, FORMATOS["instagram"], marca, cfg, caras).final()
        dif = [ImageChops.difference(hojas[a], hojas[b]).getbbox() is not None for a, b in (("cara", "logo"), ("cara", "comic"), ("logo", "comic"))]
        caso("las tres portadas (cara, logo, cómic) se dibujan distintas, y el marcador de «guardar» sale en Instagram pero no en 16:9", all(dif)
             and any(n == "marcador" for n, _ in dibuja_portada(p, FORMATOS["instagram"], marca, cfg, caras).cajas)
             and not any(n == "marcador" for k_ in ("linkedin", "youtube", "bluesky", "reddit", "discord") for n, _ in dibuja_portada(p, FORMATOS[k_], marca, cfg, caras).cajas), dif)
        caso("en CADA red la segunda imagen enseña los 3 puntos (en los formatos apaisados antes se caía el tercero)",
             all(any(n == "punto3" for n, _ in dibuja_idea(p, dise_de(f)[0], marca, None).cajas) for f in FORMATOS.values()), [k_ for k_, f in FORMATOS.items()
                 if not any(n == "punto3" for n, _ in dibuja_idea(p, dise_de(f)[0], marca, None).cajas)])
        def _choca(f_):
            hh = dibuja_portada({**p, "portada": "logo", "imagenes": 2}, dise_de(f_)[0], marca, cfg, caras)
            cj = {n: c for n, c in hh.cajas}
            if f_["ancho"] > f_["alto"]:                                    # (apaisado: texto a la izquierda, círculo a la derecha)
                return cj["visual"][0] < cj["titulo"][2] - 1
            return cj["visual"][1] < max(cj["titulo"][3], cj.get("tarjeta", (0, 0, 0, 0))[3]) - 1   # (vertical: el círculo va debajo del texto)
        caso("la portada con el logo grande no pisa el titular en NINGUNA red (en 4:3 de Reddit el círculo tapaba «SEO en YouTube»)", not any(_choca(f_) for f_ in FORMATOS.values()),
             [k_ for k_, f_ in FORMATOS.items() if _choca(f_)])
        # --- editar sin IA
        n_ped = len(pedidos)
        rh = rehaz(jid, p["id"], {"gancho": "Elige tú la principal", "portada": "comic", "puntos": ["Solo uno"], "texto_corto": "Texto nuevo"}, cfg, out, out, dir_caras=caras)
        p2 = lee(jid, p["id"], out) or {}
        caso("«✏ Editar» vuelve a dibujar SIN IA: cambia el gancho, los puntos y la portada (a «comic»)", rh.get("ok") and len(pedidos) == n_ped and p2.get("gancho") == "Elige tú la principal"
             and p2.get("portada") == "comic" and p2.get("puntos") == ["Solo uno"] and len(p2["archivos"]["instagram"]) == 2, rh.get("error") or p2.get("portada"))
        rh2 = rehaz(jid, p["id"], {"portada": "azar"}, cfg, out, out, dir_caras=caras, rng=Fijo("logo"))
        p3 = lee(jid, p["id"], out) or {}
        caso("«↻ Otra portada» (azar) sortea de nuevo y mantiene la idea", rh2.get("ok") and p3.get("portada") == "logo" and p3.get("gancho") == "Elige tú la principal", rh2.get("error"))
        caso("un post que no existe, o con un id raro, no se puede editar ni leer", not rehaz(jid, "p-00000000-000000-zzzz", {}, cfg, out, out)["ok"] and lee(jid, "../x", out) is None)
        # --- textos y descargas
        tx = textos({**lee(jid, p["id"], out), "hashtags": "#seo #canonical"})
        caso("los textos: Instagram lleva los hashtags y el corto lleva el enlace del vídeo", "#" in tx["instagram"] and tx["corto"].endswith("https://youtu.be/abc"), tx)
        caso("un texto POR RED: Bluesky cabe en 300 caracteres CONTANDO el enlace y Reddit lleva su título",
             len(textos({**p, "texto_corto": "palabra " * 80, "enlace": "https://youtu.be/abc"})["redes"]["bluesky"]) <= 300
             and textos({**p, "enlace": ""})["redes"]["reddit"].startswith("Título: ") and set(tx["redes"]) == set(FORMATOS), tx["redes"].get("bluesky"))
        zi, zt = zip_de(jid, p["id"], "instagram", out), zip_de(jid, p["id"], "todo", out)
        ni = zipfile.ZipFile(io.BytesIO(zi[0])).namelist() if zi else []
        nt = zipfile.ZipFile(io.BytesIO(zt[0])).namelist() if zt else []
        carpetas = {n.split("/")[0] for n in nt if "/" in n}
        caso("el .zip de una red trae SUS imágenes y su texto; el de «todo» trae UNA CARPETA por red (Instagram, LinkedIn, YouTube-Comunidad, Bluesky, Reddit, Discord) con sus imágenes y su texto.txt; un formato raro no da nada",
             "texto.txt" in ni and len([n for n in ni if n.endswith(".jpg")]) == 2 and len(carpetas) == len(FORMATOS) and "Instagram" in carpetas and "LinkedIn" in carpetas
             and all(f"{c}/texto.txt" in nt and f"{c}/post-01.jpg" in nt for c in carpetas) and zip_de(jid, p["id"], "xx", out) is None, (ni, nt))
        # --- un post de ANTES (solo «ig» y «wide»): se sigue pudiendo leer y descargar
        (carp / "ig").mkdir(exist_ok=True)
        (carp / "wide").mkdir(exist_ok=True)
        shutil.copy(carp / "instagram" / "post-01.jpg", carp / "ig" / "post-01.jpg")
        shutil.copy(carp / "discord" / "post-01.jpg", carp / "wide" / "post-01.jpg")
        viejo = {**(lee(jid, p["id"], out) or {}), "archivos": {"ig": ["post-01.jpg"], "wide": ["post-01.jpg"]}}
        (carp / "post.json").write_text(json.dumps(viejo, ensure_ascii=False), encoding="utf-8")
        zv = zip_de(jid, p["id"], "todo", out)
        nv = zipfile.ZipFile(io.BytesIO(zv[0])).namelist() if zv else []
        caso("un post de antes (carpetas «ig» y «wide») se sigue descargando: una carpeta por tamaño con su texto", "Instagram/post-01.jpg" in nv and any(n.endswith("/texto.txt") for n in nv) and len(nv) == 4, nv)
        # --- los estilos nuevos
        from . import posts_estilos as PE
        ck = {**base, "estilo": "checklist", "datos": {"titulo": "Antes de publicar", "items": ["Sitemap enviado", "Una canonical por página", "Enlaces revisados"]}}
        re_ = crea(jid, cfg, out, out, llama(ck), dir_caras=caras, rng=Fijo("logo"))
        pe_ = re_.get("post") or {}
        c_e = ruta(jid, pe_.get("id", "p-00000000-000000-0000"), out)
        tam_e = {c: [Image.open(c_e / c / n).size for n in pe_.get("archivos", {}).get(c, [])] for c in FORMATOS}
        caso("con «estilo»: la IA elige «checklist» → UNA imagen cuadrada de 1080×1080 en cada red (la misma) y queda apuntado el estilo",
             re_.get("ok") and pe_.get("estilo") in PE.TODOS and (pe_.get("estilo") != "checklist" or (pe_["imagenes"] == 1 and all(v == [(1080, 1080)] for v in tam_e.values()))), (pe_.get("estilo"), tam_e))
        pe_ok = crea(jid, cfg, out, out, llama(ck), dir_caras=caras, rng=random.Random(5))
        pee = pe_ok.get("post") or {}
        caso("el prompt le pide a la IA el estilo y le dice cuál usó el post anterior", any("«estilo»" in x and "checklist" in x or "estilo «" in x for x in pedidos[-2:]), pedidos[-1][-400:] if pedidos else "")
        r_est = rehaz(jid, pee["id"], {"estilo": "top3"}, cfg, out, out, dir_caras=caras)
        p_est = lee(jid, pee["id"], out) or {}
        caso("«↻ Otro estilo»: se cambia a «top3» sin IA y vuelve a dibujar en cuadrado", r_est.get("ok") and p_est.get("estilo") == "top3" and len(p_est["archivos"]["instagram"]) == 1, r_est.get("error"))
        r_mal = rehaz(jid, pee["id"], {"estilo": "dato"}, cfg, out, out, dir_caras=caras)
        caso("pedir un estilo que esta idea no puede hacer (dato sin cifra) da un error que lo explica y no rompe el post", not r_mal.get("ok") and "necesita" in (r_mal.get("error") or "")
             and (lee(jid, pee["id"], out) or {}).get("estilo") == "top3")
        r_cl = rehaz(jid, pee["id"], {"estilo": "clasico"}, cfg, out, out, dir_caras=caras)
        caso("se puede volver a «clasico» (portada + idea, por red)", r_cl.get("ok") and (lee(jid, pee["id"], out) or {}).get("estilo") == "clasico"
             and Image.open(ruta(jid, pee["id"], out) / "linkedin" / "post-01.jpg").size == (1200, 627))
        # --- borrar
        n_t = len(lista(jid, out))
        caso("borrar quita solo ese post y se niega con ids raros", borra(jid, p["id"], out) and lee(jid, p["id"], out) is None and len(lista(jid, out)) == n_t - 1 and not borra(jid, "../../x", out)
             and not borra("../x", p["id"], out))
    return filas
