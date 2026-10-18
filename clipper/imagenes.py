"""Imágenes ilustrativas de Vídeos PRO: cuando el presentador hace una
analogía o menciona algo concreto que no está en pantalla, el director
(clipper/director.py, campo "imagenes") apunta qué buscar, en inglés. Este
módulo las trae de Openverse (imágenes libres de verdad, sin API key) y las
enmarca con el mismo estilo que las tarjetas (borde de tinta + sombra dura)
antes de pegarlas al vídeo — mismo mecanismo de overlay, esquina opuesta.

Cómo se elige la foto (el autor, 2026-10-05: «algunas imágenes son buenas y otras no… que sea más accurate»):
1. Openverse busca por TEXTO (título y etiquetas), no por lo que se ve: para «black t shirts» devolvía una multitud de
   un concierto, y para «frustrated slow website user» una infografía de 180×546 px. Por eso primero se tiran, por
   código, las que no sirven por su FORMA (tiras altas, banners, miniaturas diminutas) o por ser texto en vez de foto
   (infografías, diagramas, capturas) y se ordenan las demás por lo bien que su título encaja (`busca_lista`).
2. Con las 3-6 mejores de cada hueco, Flash-Lite (500 peticiones al día, no toca las 20 de los Flash) MIRA las fotos
   —una sola petición por vídeo para todos los huecos— junto con lo que el presentador dice en ese momento, y elige
   la que de verdad lo ilustra o NINGUNA: mejor sin imagen que con una que no pinta nada (`cura`). Si rechaza todas y
   quedan búsquedas más generales, se prueba una segunda ronda con ellas.
3. Lo elegido se recuerda por hueco en `work/<vídeo>--pro/imagenes/eleccion.json`: «Reintentar», una mejora o un
   Reshuffle con la misma búsqueda ven la MISMA foto (el curador no es determinista y Openverse cambia el orden).
Sin clave de Gemini o con Flash-Lite caído NADIE mira las fotos: entonces solo se usan las relevantes de la búsqueda EXACTA del director
(`_por_reglas`; mejor sin imagen que una sin mirar).

Licencia: SOLO license_type=commercial,modification (un vídeo que genera
ingresos y que además se recorta/enmarca necesita las dos cosas). La
atribución que exige la licencia CC se añade a la descripción de YouTube
(ver `creditos` en `genera_pngs`) — nunca se intenta meter en pantalla.

Si Openverse no responde o no hay resultado usable, esa imagen se omite y
el render sigue sin ella: una imagen de más nunca debe tumbar el vídeo.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from . import util as _util
from .util import log, normalize, reemplaza

_API = "https://api.openverse.org/v1/images/"
_TIMEOUT = 12
_UA = {"User-Agent": "otros programas/1.0 (uso personal, no comercial masivo)"}

VERSION = 1                        # sube al cambiar CÓMO se elige: invalida lo recordado en eleccion.json
PAGINA = 20                        # resultados por búsqueda (el máximo sin clave de Openverse: con 30 da 401)
LADO_MIN, CORTO_MIN = 480, 280     # una foto más chica se vería pequeña y borrosa en el recuadro (lado largo / corto, px)
ASPECTO_MIN, ASPECTO_MAX = 0.5, 3.2    # ancho/alto: fuera las tiras altas (infografías de 304×1024) y los banners panorámicos (un «Domain Registration» de 960×306 sí pasa)
CANDIDATAS = 6                     # lo máximo que ve el curador por hueco y ronda
MIN_OFRECER = 3                    # con tantas fotos buenas ya no se gastan más búsquedas en la ronda
POR_CONSULTA = 5                   # lo máximo que se toma de cada búsqueda
MAX_BUSQUEDAS = 3                  # búsquedas a Openverse por hueco: la del director y 2 más generales
MINIATURA_PX = 512                 # lado largo de la copia que mira el curador (a 384 no se veía una marca de agua tenue; ~260-500 tokens por foto)
_CACHE_H = 12.0                    # horas que se recuerda el resultado de una búsqueda (Openverse limita a 200 al día sin clave)
_limitado = [False]                # Openverse dijo «demasiadas peticiones» en esta ejecución: no se insiste

# fotos que son TEXTO, no ilustración (por sus etiquetas o su título): infografías, diagramas, capturas, presentaciones…
_TEXTO_PESADO = ("infographic", "infographics", "infografia", "infografias", "diagram", "diagrams", "flowchart", "chart", "charts",
                 "screenshot", "screenshots", "screen shot", "powerpoint", "slideshow", "flyer", "brochure", "template", "templates",
                 "cheat sheet", "cheatsheet", "word cloud", "wordcloud", "statistics", "datasheet")


# ------------------------------------------------------------------ qué sirve (por código)
def relevante(r: dict, termino: str) -> bool:
    """¿El título o las etiquetas del resultado contienen TODAS las palabras de la búsqueda? Openverse busca por texto
    suelto: para «tired editor» devolvía un cómic de Silver Surfer y un cementerio, y para «albino bear», un «bear»
    que no es un oso (etiquetas de otra cosa) — medido en vivo el 2026-09-24."""
    palabras = [p for p in normalize(termino).split() if p not in _VACIAS]
    texto = normalize(" ".join([str(r.get("title") or "")] + [str(t.get("name") or "") for t in r.get("tags") or []]))
    return bool(palabras) and all(p in texto for p in palabras)


def _forma_ok(r: dict) -> bool:
    """¿Por su tamaño y su forma se vería bien en el recuadro de arriba a la derecha? Una tira alta (la infografía de 180×546 px),
    un banner panorámico o una miniatura de 214×240 px no. Si Openverse no dice el tamaño, pasa (se vuelve a mirar al bajarla)."""
    try:
        w, h = int(r.get("width") or 0), int(r.get("height") or 0)
    except (TypeError, ValueError):
        return True
    if w <= 0 or h <= 0:
        return True
    return max(w, h) >= LADO_MIN and min(w, h) >= CORTO_MIN and ASPECTO_MIN <= w / h <= ASPECTO_MAX


def _tamano_ok(size: tuple[int, int]) -> bool:
    """Lo mismo que `_forma_ok`, con el tamaño REAL de la foto ya bajada (el que dice Openverse a veces falta o miente)."""
    return _forma_ok({"width": size[0], "height": size[1]})


def _texto_pesado(r: dict) -> bool:
    """¿Sus etiquetas o su título dicen infografía, diagrama, captura de pantalla…? Es texto en vez de una foto: no ilustra nada en 3 s."""
    txt = " " + " ".join(normalize(" ".join([str(r.get("title") or "")] + [str(t.get("name") or "") for t in r.get("tags") or []])).split()) + " "
    return any(f" {p} " in txt for p in _TEXTO_PESADO)


def _sobrantes(r: dict, termino: str) -> int:
    """Palabras del título que NO son de la búsqueda: cuantas menos, más probable que la foto sea ESO. Para «polar
    bear», «Polar Bear Swim 2017» (gente disfrazada nadando: etiquetas swim, costume) pierde contra «Polar Bear»."""
    pedidas = set(normalize(termino).split())
    return sum(1 for p in normalize(str(r.get("title") or "")).split()
               if p not in pedidas and p not in _VACIAS and not p.isdigit() and len(p) > 2)


def elige(usables: list[dict], termino: str, estricta: bool = False) -> dict | None:
    """De los resultados de Openverse, el relevante (`relevante`) con el título más limpio (`_sobrantes`; a igualdad,
    el orden de Openverse). Si ninguno es relevante: None con `estricta`, y si no, el primero (lo de siempre)."""
    buenos = [r for r in usables if relevante(r, termino)]
    if buenos:
        return min(buenos, key=lambda r: _sobrantes(r, termino))       # min es estable: a igualdad, el primero
    return None if estricta or not usables else usables[0]


# ------------------------------------------------------------------ Openverse
def _ruta_cache() -> Path:
    from . import config
    return config.paths()["work"] / "openverse_cache.json"


def _recorta(r: dict) -> dict:
    """De un resultado de Openverse, solo lo que se usa (la caché y lo recordado no crecen de más)."""
    return {"id": r.get("id"), "title": r.get("title"), "url": r.get("url"), "thumbnail": r.get("thumbnail"),
            "width": r.get("width"), "height": r.get("height"), "attribution": r.get("attribution"),
            "mature": bool(r.get("mature")), "category": r.get("category"),
            "tags": [{"name": t.get("name")} for t in (r.get("tags") or [])[:15] if isinstance(t, dict)]}


def _consulta(termino: str) -> list[dict] | None:
    """Los resultados (recortados) de Openverse para `termino`, de la caché de 12 h o de la red; None si falla (sin red, límite…).
    Sin clave Openverse da 200 búsquedas al día y 20 por minuto: la caché evita repetir la misma en cada versión de un vídeo, y un
    429 (límite) apaga las siguientes de esta ejecución en vez de insistir."""
    clave = " ".join(normalize(termino).split())
    ahora = time.time()
    try:
        cache = json.loads(_ruta_cache().read_text(encoding="utf-8"))
        cache = cache if isinstance(cache, dict) else {}
    except Exception:  # noqa: BLE001
        cache = {}
    c = cache.get(clave)
    if isinstance(c, dict) and isinstance(c.get("res"), list) and ahora - float(c.get("ts") or 0) < _CACHE_H * 3600:
        return c["res"]
    if _limitado[0]:
        return None
    qs = urllib.parse.urlencode({"q": termino, "license_type": "commercial,modification", "page_size": PAGINA, "mature": "false"})
    try:
        req = urllib.request.Request(f"{_API}?{qs}", headers=_UA)
        data = json.loads(urllib.request.urlopen(req, timeout=_TIMEOUT).read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            _limitado[0] = True
            log("AVISO: Openverse pide esperar (sin clave solo da 200 búsquedas al día y 20 por minuto): no busco más imágenes por ahora.")
        else:
            log(f"AVISO: Openverse no respondió para «{termino}» (HTTP {exc.code}).")
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: Openverse no respondió para «{termino}» ({exc}).")
        return None
    res = [_recorta(r) for r in data.get("results") or [] if r.get("url") and not r.get("mature")]
    cache = {k: v for k, v in cache.items() if isinstance(v, dict) and ahora - float(v.get("ts") or 0) < 4 * _CACHE_H * 3600}
    cache[clave] = {"ts": ahora, "res": res}
    try:
        p = _ruta_cache()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        reemplaza(tmp, p)
    except Exception:  # noqa: BLE001 — la caché es un lujo: si no se puede guardar, se sigue
        pass
    return res


def busca_lista(termino: str, estricta: bool = False, _consulta_fn=None) -> list[dict]:
    """Las fotos utilizables de Openverse para `termino` (en inglés), con licencia que permite uso comercial Y modificación, de mejor
    a peor: primero las RELEVANTES (`relevante`: todas sus palabras en el título o las etiquetas; llevan `_rel`: True) por título
    más limpio (`_sobrantes`) y, si no es `estricta` (la búsqueda del director tal cual), detrás las demás (`_rel`: False). Ya sin las
    de forma o tamaño que no sirven (`_forma_ok`) ni las que son texto (`_texto_pesado`). [] si no hay o falla la red."""
    if not termino:
        return []
    res = (_consulta_fn or _consulta)(termino)
    if not res:
        return []
    usables = [r for r in res if r.get("url") and not r.get("mature") and _forma_ok(r) and not _texto_pesado(r)]
    buenos = sorted((dict(r, _rel=True) for r in usables if relevante(r, termino)), key=lambda r: _sobrantes(r, termino))   # sorted es estable
    resto = [] if estricta else [dict(r, _rel=False) for r in usables if not relevante(r, termino)]
    return buenos + resto


def busca(termino: str, estricta: bool = False) -> dict | None:
    """La mejor foto de `busca_lista` (la primera) o None. Con `estricta=False` (la búsqueda del director tal cual) si ninguna es
    relevante se queda con la primera, como siempre; con `estricta=True` (las más generales) no: mejor sin imagen que una que no
    pinta nada."""
    lista = busca_lista(termino, estricta)
    return lista[0] if lista else None


def descarga(resultado: dict) -> Image.Image | None:
    """La foto (la original y, si no se deja bajar —algunos sitios bloquean el enlace directo—, la miniatura de 600 px de Openverse)."""
    for u in (resultado.get("url"), resultado.get("thumbnail")):
        if not u:
            continue
        try:
            raw = urllib.request.urlopen(urllib.request.Request(u, headers=_UA), timeout=_TIMEOUT).read()
            return Image.open(BytesIO(raw)).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: no se pudo descargar la imagen ({exc}).")
    return None


def miniatura(resultado: dict, lado: int = MINIATURA_PX) -> bytes | None:
    """La foto en pequeño (JPEG, `lado` px) para que el curador la mire: la miniatura de Openverse (600 px; cuota aparte de 1000 al
    día) o, si no hay, la original. None si no se pudo bajar: esa foto no se le enseña."""
    for u in (resultado.get("thumbnail"), resultado.get("url")):
        if not u:
            continue
        try:
            raw = urllib.request.urlopen(urllib.request.Request(u, headers=_UA), timeout=_TIMEOUT).read(8_000_000)
            im = Image.open(BytesIO(raw)).convert("RGB")
            im.thumbnail((lado, lado), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=80)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            continue
    return None


def enmarca(foto: Image.Image, marca: dict, alto_canvas: int = 1080,
           lado_max: int = 520) -> Image.Image:
    """La foto -> misma familia visual que las tarjetas: marco de tinta +
    sombra dura, para que no se sienta pegada de cualquier forma."""
    from .tarjetas import _hex

    e = alto_canvas / 1080.0
    lado = max(1, round(lado_max * e))
    foto = foto.copy()
    foto.thumbnail((lado, lado), Image.LANCZOS)
    borde = max(2, round(6 * e))
    sombra = max(4, round(14 * e))
    radio = max(3, round(8 * e))
    tinta, crema = _hex(marca["tinta"]), _hex(marca["crema"])

    w, h = foto.size
    W, H = w + borde * 2, h + borde * 2
    lienzo = Image.new("RGBA", (W + sombra, H + sombra), (0, 0, 0, 0))
    d = ImageDraw.Draw(lienzo)
    d.rounded_rectangle((sombra, sombra, W + sombra, H + sombra), radius=radio, fill=tinta)
    d.rounded_rectangle((0, 0, W, H), radius=radio, fill=crema, outline=tinta, width=borde)
    lienzo.paste(foto, (borde, borde))
    return lienzo


_VACIAS = {"a", "an", "the", "in", "on", "at", "of", "with", "for", "and", "to", "from", "by", "into", "over", "under",
           "near", "inside", "outside", "while", "during", "about", "like", "as"}
_PREPOSICIONES = {"in", "on", "at", "of", "with", "for", "to", "from", "by", "into", "over", "under", "near", "inside",
                  "outside", "while", "during", "about", "like", "as"}


def consultas(im: dict, maximo: int = 5) -> list[str]:
    """Las búsquedas a probar para una imagen del plan, de la más concreta a la más general: la del director, sus
    `alternativas` (más generales, las pide el prompt: Gemini sabe que «albino bear in antarctica» -> «polar bear») y,
    por código, versiones más cortas de la primera — sin palabras vacías, el núcleo antes de la preposición y sus 2
    últimas palabras («albino bear in antarctica» → «albino bear»; «retro comic book style pop art» → «pop art»). Por
    código nunca UNA sola palabra: «editor» o «art» solos traen cualquier cosa. Así una imagen que el director pidió muy
    concreta no se pierde solo porque el banco libre de Openverse no tenga esa foto exacta: en los vídeos reales del autor
    del 22-sep, 5 análisis pidieron «albino bear in antarctica» y los 5 se quedaron sin imagen (2026-09-24)."""
    out: list[str] = []

    def mete(q: str) -> None:
        q = " ".join(str(q or "").split()).strip()
        if q and q.lower() not in (x.lower() for x in out):
            out.append(q)

    base = str(im.get("busqueda", "") or "")
    mete(base)
    for a in im.get("alternativas") or []:
        mete(a)
    palabras = [p for p in base.lower().replace(",", " ").split() if p]
    nucleo: list[str] = []
    for p in palabras:
        if nucleo and p in _PREPOSICIONES:
            break                                   # «albino bear | in antarctica»: lo de antes es lo que se ve
        if p not in _VACIAS:
            nucleo.append(p)
    mete(" ".join(p for p in palabras if p not in _VACIAS))
    mete(" ".join(nucleo))
    if len(nucleo) >= 2:
        mete(" ".join(nucleo[-2:]))
    return out[:maximo]


# ------------------------------------------------------------------ el curador (Flash-Lite mira las fotos)
_PROMPT_CURADOR = """Eres el editor de imágenes de un canal de YouTube sobre SEO. Mientras el presentador habla, sale unos 3 segundos UNA imagen \
pequeña (un recuadro de la cuarta parte de la pantalla) arriba a la derecha, para ilustrar lo que dice en ese momento. Te doy varios HUECOS; \
cada uno trae lo que el presentador dice, qué imagen se quería y entre 1 y 6 fotos candidatas de un banco libre. Para cada hueco elige la foto \
que mejor lo ilustre, o NINGUNA.

VALE una foto o ilustración LIMPIA donde, de un vistazo y en tamaño pequeño, se ve claro un sujeto que representa lo que dice el presentador \
(literalmente o como analogía EVIDENTE: tiene que entenderse al verla, sin leer su título; si hay que explicarla, no vale).
NO VALE (recházala): infografías, diagramas, gráficos, capturas de pantalla, presentaciones o carteles con mucho texto; collages o varias \
imágenes en una; marcas de agua (aunque sean tenues o se repitan por toda la foto) o logotipos grandes; fotos borrosas, oscuras o lejanas donde el sujeto no se distingue; fotos que enseñan OTRA \
cosa (otro animal, otro objeto, una multitud o un lugar que no pinta nada con lo que se dice); imágenes raras, incómodas o que puedan ofender; \
personas reconocibles que no se mencionan. Una o dos palabras de texto están bien solo si SON lo que se quiere mostrar (p. ej. un cartel «domain»).
Mejor NINGUNA que una foto dudosa: si ninguna encaja con claridad, deja la lista vacía. No fuerces una elección.

Responde SOLO con JSON, una entrada por hueco: {"huecos": [{"hueco": 1, "elegidas": [3, 1], "razon": "..."}]}. "elegidas" son los números de las \
candidatas que valen, la mejor primero (como mucho 2; [] si ninguna) y "razon", en menos de 14 palabras, qué ves en la elegida o por qué las \
rechazas todas."""


def arma_prompt_curador(huecos: list[dict], types) -> list:
    """El pedido multimodal: las reglas, y por cada hueco lo que el presentador dice, lo que se quería y sus candidatas (título + foto)."""
    partes: list = [_PROMPT_CURADOR]
    for h in huecos:
        motivo = f" ({h['motivo']})" if h.get("motivo") else ""
        partes.append(f"\n=== HUECO {h['n']} ===\nEl presentador dice: «{h.get('frase') or '(no consta)'}»\n"
                      f"Se quería ilustrar: {h.get('busqueda') or '?'}{motivo}")
        for j, c in enumerate(h["candidatas"], 1):
            partes.append(f"Candidata {j} — título: «{c.get('titulo') or '(sin título)'}»")
            partes.append(types.Part.from_bytes(data=c["jpeg"], mime_type="image/jpeg"))
    partes.append("\nResponde SOLO con el JSON.")
    return partes


def _lee_curador(texto: str, n_cand: dict[int, int]) -> dict[int, dict]:
    """La respuesta del curador -> {hueco: {"elegidas": [nº de candidata…], "razon": str}}. Estricta: tiene que opinar de TODOS los huecos
    (si falta alguno, la cadena prueba otro modelo); los números fuera de rango o repetidos se ignoran y como mucho 2 por hueco."""
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    d = json.loads(t)
    filas = d.get("huecos") if isinstance(d, dict) else d
    if not isinstance(filas, list):
        raise ValueError("la IA no devolvió la lista de huecos")
    out: dict[int, dict] = {}
    for f in filas:
        if not isinstance(f, dict):
            continue
        try:
            n = int(f.get("hueco"))
        except (TypeError, ValueError):
            continue
        if n not in n_cand or n in out:
            continue
        crudo = f.get("elegidas")
        crudo = crudo if isinstance(crudo, list) else ([crudo] if isinstance(crudo, (int, str)) and str(crudo).strip() else [])
        el: list[int] = []
        for x in crudo:
            try:
                j = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= j <= n_cand[n] and j not in el:
                el.append(j)
        out[n] = {"elegidas": el[:2], "razon": " ".join(str(f.get("razon") or "").split())[:140]}
    faltan = [n for n in n_cand if n not in out]
    if faltan:
        raise ValueError(f"la IA no opinó de los huecos {faltan}")
    return out


def cura(huecos: list[dict], cfg, _gemini=None, _modelos=None, _llama=None) -> dict[int, dict] | None:
    """UNA petición a Flash-Lite (500 al día: no toca las 20 de los Flash) con las fotos candidatas de TODOS los huecos de un vídeo ->
    {hueco: {"elegidas": [...], "razon": ...}} o None si no se pudo mirar (sin clave, saturado…): entonces se elige por reglas. Nunca lanza.
    `huecos` = [{"n", "frase", "busqueda", "motivo", "candidatas": [{"titulo", "jpeg"}]}]."""
    if not huecos:
        return {}
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                log("  curador de imágenes: no hay clave de Gemini; sin él solo uso las fotos de la búsqueda exacta del director (nadie las ha mirado).")
                return None
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from . import modelos as _m
            _modelos = _m.cadena_lite(cfg, client)
        n_cand = {h["n"]: len(h["candidatas"]) for h in huecos}
        llama = _llama or _util.call_gemini_cadena
        res, modelo, _gasto = llama(client, types, _modelos, arma_prompt_curador(huecos, types), system=None,
                                    valida=lambda t: _lee_curador(t, n_cand), reintento_s=20.0, pasadas=2, timeout_s=75.0,
                                    esquema=None, etiqueta="imagenes")
        log(f"  curador de imágenes ({_util.nombre_modelo(modelo)}): miró {sum(n_cand.values())} foto(s) de {len(huecos)} hueco(s).")
        return res
    except Exception as exc:  # noqa: BLE001 — no poder mirar nunca frena el vídeo
        log(f"AVISO: el curador de imágenes no pudo ({str(exc).replace(chr(10), ' ')[:120]}); sin él solo uso las fotos de la búsqueda exacta del director (nadie las ha mirado).")
        return None


# ------------------------------------------------------------------ lo elegido se recuerda por hueco
def _clave_hueco(im: dict) -> str:
    """Identifica un hueco por QUÉ se busca (la búsqueda del director y sus alternativas), no por el segundo: así la misma imagen sigue
    siendo la misma tras un «Reintentar», una mejora o un corte que mueve el instante."""
    base = "|".join([" ".join(normalize(str(im.get("busqueda") or "")).split())]
                    + [" ".join(normalize(str(a)).split()) for a in im.get("alternativas") or []])
    return hashlib.sha1(f"v{VERSION}|{base}".encode("utf-8")).hexdigest()[:16]


def _lee_eleccion(out_dir: Path) -> dict:
    try:
        d = json.loads((out_dir / "eleccion.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _guarda_eleccion(out_dir: Path, d: dict) -> None:
    try:
        p = out_dir / "eleccion.json"
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        reemplaza(tmp, p)
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no pude recordar qué imágenes elegí ({exc}).")


# ------------------------------------------------------------------ juntar candidatas de un hueco
def _amplia(h: dict, lista_fn, minimo: int = MIN_OFRECER) -> list[dict]:
    """Busca en las siguientes búsquedas del hueco (la del director, luego sus alternativas) hasta tener `minimo` fotos relevantes NUEVAS
    (o no quedar más) y las devuelve (como mucho `CANDIDATAS`), también anotadas en `h["pool"]`. Las no relevantes de la búsqueda del
    director quedan aparte (`h["sueltas"]`: solo valen si no hay ninguna relevante, como siempre)."""
    nuevas: list[dict] = []
    while h["k"] < len(h["qs"]) and len(nuevas) < minimo:
        k, q = h["k"], h["qs"][h["k"]]
        h["k"] += 1
        for r in (lista_fn(q, estricta=k > 0) or [])[:POR_CONSULTA]:
            ident = r.get("id") or r.get("url")
            if not ident or ident in h["vistos"]:
                continue
            h["vistos"].add(ident)
            c = dict(r, _q=q, _k=k)
            if c.get("_rel", True):
                nuevas.append(c)
            elif k == 0:
                h["sueltas"].append(c)
    nuevas = nuevas[:CANDIDATAS]
    h["pool"].extend(nuevas)
    return nuevas


def _con_miniaturas(cands: list[dict], miniatura_fn) -> list[dict]:
    """De `cands`, las que se pudieron bajar en pequeño (con su `_jpeg`): las demás el curador no las puede mirar. En paralelo (~0,5 s cada una)."""
    if not cands:
        return []

    def seguro(c):
        try:
            return miniatura_fn(c)
        except Exception:  # noqa: BLE001
            return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        jpegs = list(ex.map(seguro, cands))
    out = []
    for c, j in zip(cands, jpegs):
        if j:
            c["_jpeg"] = j
            out.append(c)
    return out


def _pregunta(hs: list[dict], cura_fn) -> dict | None:
    """Los huecos de `hs` que tienen fotos que mirar -> el curador (UNA petición). {} si no hay nada que preguntar; None si no pudo."""
    pedir = [{"n": h["i"] + 1, "frase": h["im"].get("contexto") or "", "busqueda": h["im"].get("busqueda") or "", "motivo": h["im"].get("motivo") or "",
              "candidatas": [{"titulo": c.get("title"), "jpeg": c["_jpeg"]} for c in h["vistas"]]} for h in hs if h["vistas"]]
    if not pedir:
        return {}
    try:
        return cura_fn(pedir)
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: el curador de imágenes falló ({str(exc)[:100]}); sin él solo uso las fotos de la búsqueda exacta del director.")
        return None


def _por_reglas(pool: list[dict]) -> list[dict]:
    """Sin curador (Gemini saturado o sin clave) NADIE mira la foto, así que solo vale lo más fiel a lo pedido: las relevantes de la búsqueda EXACTA
    del director, por orden. Las de las búsquedas más generales las puede dar por buenas solo el curador: sin él, en la prueba real del 5-oct salieron el
    «Bitcoin QR code» de «internet address», la amapola de «pop art» y los dibujos de focas de «bear antarctica». Mejor sin imagen que una sin mirar."""
    return [c for c in pool if c.get("_rel", True) and c.get("_k", 0) == 0]


def genera_pngs(imagenes: list[dict], marca: dict, out_dir: Path, alto_canvas: int = 1080,
                _busca=None, _descarga=None, *, cfg=None, info: list | None = None,
                _lista=None, _miniatura=None, _cura=None) -> tuple[list[dict], list[str], list[dict]]:
    """Busca+elige+descarga+enmarca cada imagen del plan. Devuelve (overlays, creditos, eventos):
    `overlays` en el mismo formato que usa render.render_merged (path/w/h/inicio/fin + "ancla"
    para no pisar las tarjetas), `creditos` = las líneas de atribución CC que exige la licencia,
    listas para meter en la descripción, y `eventos` = una fila por imagen que SÍ se logró meter,
    lista para «Ritmo del vídeo» (pedido del autor, 2026-09-23: antes una imagen no salía ahí con
    NINGÚN indicativo — ni el suyo propio ni, por error, el de otro tipo).
    Con `cfg` (el de verdad: lo pasa el pipeline) elige el curador de Flash-Lite (ver arriba; `videos.imagenes_curador: false` lo apaga);
    sin `cfg`, por reglas. Cada hueco puede traer `contexto` (lo que se oye en ese momento) que el curador lee. Si se pasa `info` (una lista),
    se le añade una fila por hueco (elegida u omitida, quién la eligió y por qué) para el manifest.
    `_busca`/`_lista`/`_descarga`/`_miniatura`/`_cura` son solo para las pruebas (sin red); por defecto, las de este módulo."""
    if _lista is not None:
        lista_fn = _lista
    elif _busca is not None:
        def lista_fn(q, estricta=False):
            r = _busca(q, estricta=estricta)
            return [dict(r, _rel=True)] if r else []
    else:
        lista_fn = busca_lista
    descarga_fn, miniatura_fn = _descarga or descarga, _miniatura or miniatura
    if _cura is not None:
        cura_fn = _cura
    elif cfg is not None and cfg.get_path("videos.imagenes_curador", True):
        def cura_fn(huecos):
            return cura(huecos, cfg)
    else:
        cura_fn = None

    out_dir.mkdir(parents=True, exist_ok=True)
    overlays: list[dict] = []
    creditos: list[str] = []
    eventos: list[dict] = []
    memoria = _lee_eleccion(out_dir) if cura_fn else {}
    nuevas_dec: dict[str, dict] = {}
    cambio_memoria = False

    # 1) juntar las candidatas de cada hueco (lo ya recordado no busca nada)
    hs: list[dict] = []
    for i, im in enumerate(imagenes):
        clave = _clave_hueco(im)
        previa = memoria.get(clave) if isinstance(memoria.get(clave), dict) else None
        h = {"i": i, "im": im, "clave": clave, "previa": previa, "qs": consultas(im)[:MAX_BUSQUEDAS if cura_fn else 1], "k": 0, "pool": [], "sueltas": [],
             "vistos": set(), "vistas": [], "juzgadas": [], "veredicto": None}
        if not previa:
            _amplia(h, lista_fn)
            if not h["pool"]:
                h["pool"] = h["sueltas"][:2]       # lo de siempre: sin ninguna relevante, las primeras de la búsqueda del director
        hs.append(h)

    # 2) el curador las mira (una petición para todo el vídeo) y, donde rechaza todo y quedan búsquedas más generales, una 2.ª ronda
    if cura_fn:
        for h in hs:
            if not h["previa"] and h["pool"]:
                h["vistas"] = _con_miniaturas(h["pool"], miniatura_fn)
        res1 = _pregunta(hs, cura_fn)
        if res1:
            for h in hs:
                if h["vistas"] and res1.get(h["i"] + 1) is not None:
                    h["veredicto"], h["juzgadas"] = res1[h["i"] + 1], list(h["vistas"])
            otra = [h for h in hs if h["veredicto"] is not None and not h["veredicto"]["elegidas"] and h["k"] < len(h["qs"])]
            for h in otra:
                h["vistas"] = _con_miniaturas(_amplia(h, lista_fn), miniatura_fn)
            res2 = _pregunta(otra, cura_fn) if any(h["vistas"] for h in otra) else None
            for h in otra:
                if res2 and h["vistas"] and res2.get(h["i"] + 1) is not None:
                    h["veredicto"], h["juzgadas"] = res2[h["i"] + 1], list(h["vistas"])

    # 3) bajar la elegida, enmarcarla y apuntarla
    def apunta(h, estado, por, razon="", c=None, n=0):
        if info is not None:
            info.append({"t": round(float(h["im"].get("t", 0) or 0), 1), "busqueda": str(h["im"].get("busqueda") or ""), "estado": estado, "por": por,
                         "titulo": str((c or {}).get("title") or ""), "usada": str((c or {}).get("_q") or ""), "razon": razon, "candidatas": n})

    for h in hs:
        i, im = h["i"], h["im"]
        termino = im.get("busqueda", "")
        orden: list[dict] = []
        por, razon = "reglas", ""
        if h["previa"]:
            p = h["previa"]
            if p.get("ninguna"):
                log(f"  imagen «{termino}»: ya la miré antes y ninguna foto servía ({p.get('razon') or 'sin motivo'}); se omite.")
                apunta(h, "omitida", "recordada", str(p.get("razon") or ""))
                continue
            orden = [dict(p.get("resultado") or {}, _q=p.get("usada") or termino)]
            por, razon = "recordada", str(p.get("razon") or "")
        elif h["veredicto"] is not None:
            v = h["veredicto"]
            if not v["elegidas"]:
                log(f"  imagen «{termino}»: el curador rechazó las {len(h['juzgadas'])} foto(s) ({v['razon'] or 'ninguna encaja'}); sin imagen.")
                nuevas_dec[h["clave"]] = {"por": "ia", "ninguna": True, "razon": v["razon"]}
                apunta(h, "omitida", "ia", v["razon"], n=len(h["juzgadas"]))
                continue
            orden = [h["juzgadas"][j - 1] for j in v["elegidas"]]
            por, razon = "ia", v["razon"]
        else:
            orden = _por_reglas(h["pool"])
            if not orden:
                log(f"  imagen «{termino}»: sin resultado usable en Openverse (probé {', '.join(h['qs'])}), se omite.")
                apunta(h, "omitida", "reglas", "sin resultado en Openverse")
                continue
        foto, elegida = None, None
        for c in orden[:3]:
            f = descarga_fn(c)
            if f is not None and _tamano_ok(f.size):
                foto, elegida = f, c
                break
        if foto is None:
            log(f"  imagen «{termino}»: no se pudo bajar ninguna foto (probé {len(orden[:3])}), se omite.")
            if por == "recordada":                     # el enlace recordado ya no vale: la próxima vez se vuelve a buscar y a elegir
                memoria.pop(h["clave"], None)
                cambio_memoria = True
            apunta(h, "omitida", por, "no se pudo bajar")
            continue
        usada = str(elegida.get("_q") or "")
        if usada and usada.lower() != str(termino).lower():
            log(f"  imagen «{termino}»: sin foto libre exacta; uso una de «{usada}».")
        if por == "ia":
            log(f"  imagen «{termino}»: el curador eligió «{str(elegida.get('title') or '')[:50]}» ({razon}).")
            nuevas_dec[h["clave"]] = {"por": "ia", "resultado": _recorta(elegida), "razon": razon, "usada": usada}
        marco = enmarca(foto, marca, alto_canvas)
        p = out_dir / f"imagen_{i:02d}.png"
        marco.save(p)
        dur = float(im.get("duracion", 3.0) or 3.0)
        t0 = float(im["t"])
        overlays.append({"path": str(p), "w": marco.width, "h": marco.height,
                         "inicio": t0, "fin": t0 + dur, "ancla": "top-right"})
        atrib = elegida.get("attribution")
        if atrib:
            creditos.append(atrib)
        eventos.append({"t": round(t0, 1), "tipo": "imagen",
                        "detalle": str(im.get("motivo") or termino or "Imagen ilustrativa")[:80]})
        apunta(h, "elegida", por, razon, elegida, len(h["juzgadas"]) or len(h["pool"]))
    if cura_fn and (nuevas_dec or cambio_memoria):
        memoria.update(nuevas_dec)
        _guarda_eleccion(out_dir, memoria)
    return overlays, creditos, eventos


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    marca = {"tinta": "#14100F", "crema": "#FFFBF2"}

    def foto_falsa():
        return Image.new("RGB", (640, 420), (200, 50, 50))

    def busca_ok(t, estricta=False):
        return {"url": "http://x/y.jpg", "attribution": f'"{t}" by alguien, licencia CC BY 2.0.', "mature": False}

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ov, cr, ev = genera_pngs(
            [{"t": 10.0, "busqueda": "seo expert", "motivo": "Recomienda un experto en SEO", "duracion": 3.0},
             {"t": 40.0, "busqueda": "sin resultado", "duracion": 3.0},
             {"t": 70.0, "busqueda": "falla la descarga", "duracion": 3.0}],
            marca, out,
            _busca=lambda t, estricta=False: busca_ok(t) if t in ("seo expert", "falla la descarga") else None,
            _descarga=lambda r: foto_falsa() if "seo" in r["attribution"] else None)
        caso("una imagen que SÍ se descarga deja overlay + crédito + un evento «imagen» con el motivo como detalle (para «Ritmo del vídeo»)",
             len(ov) == 1 and ov[0]["inicio"] == 10.0 and ov[0]["ancla"] == "top-right" and len(cr) == 1
             and ev == [{"t": 10.0, "tipo": "imagen", "detalle": "Recomienda un experto en SEO"}], (ov, cr, ev))
        caso("sin resultado en Openverse (busca=None) no deja overlay, ni crédito, ni evento — y no revienta",
             not any(e["t"] == 40.0 for e in ev))
        caso("si Openverse encuentra algo pero la descarga falla, tampoco deja overlay/crédito/evento",
             not any(e["t"] == 70.0 for e in ev))
        caso("el archivo SÍ quedó guardado en disco para la única que funcionó", (out / "imagen_00.png").is_file())

        ov2, cr2, ev2 = genera_pngs([{"t": 5.0, "busqueda": "sin motivo"}], marca, out,
                                    _busca=busca_ok, _descarga=lambda r: foto_falsa())
        caso("sin «motivo» del director, el detalle del evento usa el término de búsqueda", ev2[0]["detalle"] == "sin motivo")

        ov3, cr3, ev3 = genera_pngs([], marca, out)
        caso("sin imágenes en el plan, las tres listas salen vacías y no toca la red", (ov3, cr3, ev3) == ([], [], []))

        # búsquedas más generales cuando la concreta no da foto (el «albino bear in antarctica» real del 22-sep)
        q = consultas({"busqueda": "albino bear in antarctica", "alternativas": ["polar bear", "bear"]})
        caso("las búsquedas van de la más concreta a la más general: la del director, sus alternativas y luego las "
             "cortas por código", q[:3] == ["albino bear in antarctica", "polar bear", "bear"]
             and "albino bear" in q and len(q) <= 5, q)
        q2 = consultas({"busqueda": "retro comic book style pop art"})
        caso("un plan viejo sin alternativas también prueba versiones más cortas («pop art»), pero por código nunca una "
             "sola palabra («art» trae cualquier cosa)", "pop art" in q2 and "art" not in q2
             and q2[0] == "retro comic book style pop art", q2)
        caso("sin palabras repetidas ni búsquedas vacías", len(q2) == len(set(x.lower() for x in q2)) and all(q2), q2)
        probadas: list[tuple[str, bool]] = []

        def solo_polares(t, estricta=False):
            probadas.append((t, estricta))
            return busca_ok(t) if t == "polar bear" else None
        plan4 = [{"t": 12.0, "busqueda": "albino bear in antarctica", "alternativas": ["polar bear", "bear"], "motivo": "algo rarísimo"}]
        ov4, cr4, ev4 = genera_pngs(plan4, marca, out, _busca=solo_polares, _descarga=lambda r: foto_falsa(), _miniatura=lambda r: b"j",
                                    _cura=lambda hs: {1: {"elegidas": [1], "razon": "un oso polar"}})
        caso("con curador: si solo hay foto para la alternativa («polar bear»), la imagen SALE (antes se omitía) con el crédito de "
             "esa foto", len(ov4) == 1 and '"polar bear"' in cr4[0] and ev4[0]["detalle"] == "algo rarísimo",
             (probadas, cr4))
        caso("…la concreta se prueba primero, tal cual (como siempre), y las generales con el filtro de relevancia",
             probadas[0] == ("albino bear in antarctica", False) and probadas[1] == ("polar bear", True), probadas)
        probadas.clear()
        ov4b, cr4b, ev4b = genera_pngs(plan4, marca, out, _busca=solo_polares, _descarga=lambda r: foto_falsa())
        caso("SIN curador nadie mira las fotos: una de las búsquedas generales no vale (mejor sin imagen que una sin mirar) y ni siquiera se gastan "
             "búsquedas en Openverse con ellas", ov4b == [] and probadas == [("albino bear in antarctica", False)], probadas)

        # el filtro de relevancia, con resultados de Openverse de verdad (2026-09-24)
        comic = {"title": "Silver Surfer - 12 in hand, 6 more to complete!", "tags": []}
        oso_no = {"title": "November 4, 2012 [Day 004]", "tags": [{"name": "bear"}, {"name": "bearpride"}]}
        oso_si = {"title": "Timber or gray wolf, Stuffed brown bear, albino beaver",
                  "tags": [{"name": "albinobeaver"}, {"name": "stuffedbrownbear"}]}
        caso("relevancia: el cómic de Silver Surfer NO vale para «tired editor»", not relevante(comic, "tired editor"))
        caso("relevancia: un «bear» sin «albino» NO vale para «albino bear»; uno con las dos palabras, sí",
             not relevante(oso_no, "albino bear") and relevante(oso_si, "albino bear"))
        caso("relevancia: las palabras vacías no cuentan («bear in antarctica» pide «bear» y «antarctica»)",
             relevante({"title": "Polar bear, Antarctica", "tags": []}, "bear in antarctica"))
        nado = {"title": "Polar Bear Swim 2017", "tags": [{"name": "swim"}, {"name": "costume"}, {"name": "polar"},
                                                           {"name": "bear"}], "url": "u1"}
        zoo = {"title": "Polar Bear", "tags": [{"name": "bear"}, {"name": "polar"}, {"name": "zoo"}], "url": "u2"}
        caso("entre los relevantes gana el de título más limpio: «Polar Bear» y no «Polar Bear Swim 2017» (gente "
             "nadando), aunque Openverse lo ponga primero", elige([nado, zoo], "polar bear") is zoo)
        caso("sin ninguno relevante: la búsqueda del director se queda con el primero (como siempre) y una general, con "
             "nada", elige([comic], "tired editor") is comic and elige([comic], "tired editor", estricta=True) is None)

        # ---------------------------------------------------------------- 2026-10-05: forma, texto y curador
        def foto(i, titulo, w=1024, h=683, tags=()):
            return {"id": f"id{i}", "title": titulo, "url": f"http://x/{i}.jpg", "thumbnail": f"http://x/{i}_t.jpg", "width": w,
                    "height": h, "attribution": f'"{titulo}" by alguien, CC BY 2.0.', "mature": False, "tags": [{"name": t} for t in tags]}

        caso("forma: las infografías altas de 180×546 y 304×1024 px, un banner de 1600×300, una miniatura de 214×240 y una de 300×200 NO sirven; "
             "una de 1024×683, una vertical de 600×800, el banner «Domain Registration» de 960×306 y una sin tamaño, sí",
             not _forma_ok({"width": 180, "height": 546}) and not _forma_ok({"width": 304, "height": 1024})
             and not _forma_ok({"width": 1600, "height": 300})
             and not _forma_ok({"width": 214, "height": 240}) and not _forma_ok({"width": 300, "height": 200})
             and _forma_ok({"width": 1024, "height": 683}) and _forma_ok({"width": 600, "height": 800})
             and _forma_ok({"width": 960, "height": 306}) and _forma_ok({"width": None, "height": None}))
        caso("forma: con la foto YA bajada se vuelve a comprobar con su tamaño real (el de Openverse a veces falta)",
             not _tamano_ok((200, 150)) and not _tamano_ok((300, 900)) and _tamano_ok((640, 420)))
        caso("texto: «infographic» en el título, «screenshot» en las etiquetas o «cheat-sheet» = es texto, no una foto; «Polar Bear» no",
             _texto_pesado({"title": "Website speed [Infographic]", "tags": []}) and _texto_pesado({"title": "x", "tags": [{"name": "Screenshot"}]})
             and _texto_pesado({"title": "SEO cheat-sheet", "tags": []})
             and not _texto_pesado({"title": "Polar Bear", "tags": [{"name": "zoo"}]}))

        crudo = [foto(1, "Polar Bear Swim 2017", tags=("polar", "bear")),                        # relevante, título sucio
                 foto(2, "Polar Bear", tags=("polar", "bear", "zoo")),                           # relevante y limpio
                 foto(3, "Polar bear stats infographic", w=180, h=546, tags=("infographic",)),   # tira alta Y texto
                 foto(4, "Chicago Bears fan", tags=("football",)),                               # no es un oso polar
                 foto(5, "Polar Bear banner", w=1600, h=300, tags=("polar", "bear"))]            # banner
        lista = busca_lista("polar bear", _consulta_fn=lambda q: crudo)
        caso("busca_lista: sin la tira alta, el banner ni la infografía; primero las relevantes (el título más limpio antes) y detrás "
             "las demás, marcadas", [r["id"] for r in lista] == ["id2", "id1", "id4"]
             and [r["_rel"] for r in lista] == [True, True, False], [(r["id"], r["_rel"]) for r in lista])
        caso("…con `estricta` (las búsquedas generales) solo las relevantes",
             [r["id"] for r in busca_lista("polar bear", True, _consulta_fn=lambda q: crudo)] == ["id2", "id1"])
        caso("…sin resultados, con la red caída o sin búsqueda: lista vacía y sin reventar",
             busca_lista("x", _consulta_fn=lambda q: None) == [] and busca_lista("x", _consulta_fn=lambda q: []) == []
             and busca_lista("", _consulta_fn=lambda q: crudo) == [])

        def nuevo_h(im):
            return {"i": 0, "im": im, "qs": consultas(im)[:MAX_BUSQUEDAS], "k": 0, "pool": [], "sueltas": [], "vistos": set()}
        pedidas: list = []

        def lista_q(q, estricta=False):
            pedidas.append((q, estricta))
            return {"polar bear": [dict(foto(10, "Polar Bear"), _rel=True), dict(foto(11, "Polar bear cub"), _rel=True)],
                    "bear": [dict(foto(10, "Polar Bear"), _rel=True), dict(foto(12, "Brown bear"), _rel=True)]}.get(q, [])
        h = nuevo_h({"busqueda": "albino bear in antarctica", "alternativas": ["polar bear", "bear"]})
        nuevas = _amplia(h, lista_q)
        caso("juntar candidatas: sigue con la siguiente búsqueda hasta tener 3 (sin repetir la misma foto de dos búsquedas) y anota de cuál salió",
             [c["id"] for c in nuevas] == ["id10", "id11", "id12"] and [c["_q"] for c in nuevas] == ["polar bear", "polar bear", "bear"]
             and h["k"] == 3 and pedidas == [("albino bear in antarctica", False), ("polar bear", True), ("bear", True)], (pedidas, h["k"]))
        pedidas.clear()
        h2 = nuevo_h({"busqueda": "polar bear", "alternativas": ["bear"]})
        n2 = _amplia(h2, lambda q, estricta=False: pedidas.append(q) or [dict(foto(20 + n, f"Polar bear {n}"), _rel=True) for n in range(8)])
        caso("…con la búsqueda del director ya hay de sobra: no se gasta otra búsqueda (Openverse limita a 200 al día) y de cada una se toman 5 como mucho",
             pedidas == ["polar bear"] and len(n2) == POR_CONSULTA and h2["k"] == 1, (pedidas, len(n2)))
        h3 = nuevo_h({"busqueda": "tired editor"})
        _amplia(h3, lambda q, estricta=False: [dict(foto(30, "Silver Surfer comic"), _rel=False), dict(foto(31, "Graveyard"), _rel=False)])
        caso("las que no son relevantes de la búsqueda del director no entran entre las candidatas: quedan aparte («sueltas»)",
             h3["pool"] == [] and len(h3["sueltas"]) == 2)

        # lo que contesta el curador
        n_cand = {1: 4, 2: 3}
        v = _lee_curador('{"huecos": [{"hueco": 1, "elegidas": [3, 1], "razon": "un osito"}, {"hueco": 2, "elegidas": [], "razon": "todas son infografías"}]}', n_cand)
        caso("curador: lee las elegidas por orden y los huecos sin ninguna ([])",
             v[1]["elegidas"] == [3, 1] and v[2]["elegidas"] == [] and v[2]["razon"].startswith("todas"), v)
        v2 = _lee_curador('```json\n{"huecos": [{"hueco": 1, "elegidas": [9, 2, 2, 3], "razon": "x"}, {"hueco": 2, "elegidas": 1}]}\n```', n_cand)
        caso("…con la respuesta dentro de un bloque ```json, ignora los números fuera de rango y los repetidos, toma 2 como mucho y acepta un número suelto",
             v2[1]["elegidas"] == [2, 3] and v2[2]["elegidas"] == [1], v2)
        for malo, motivo in (('{"huecos": [{"hueco": 1, "elegidas": [1]}]}', "falta un hueco"), ("no es json", "es texto suelto"),
                             ('{"otra": 1}', "no trae la lista de huecos")):
            try:
                _lee_curador(malo, n_cand)
                lanzo = False
            except Exception:  # noqa: BLE001
                lanzo = True
            caso(f"…es estricto: se niega si {motivo} (la cadena probaría otro modelo)", lanzo)

        class _Part:
            @staticmethod
            def from_bytes(data, mime_type):
                return ("IMG", len(data), mime_type)

        class _Tipos:
            Part = _Part
        huecos_p = [{"n": 1, "frase": "es como un oso polar en el desierto", "busqueda": "polar bear", "motivo": "analogía",
                     "candidatas": [{"titulo": "Polar Bear", "jpeg": b"abc"}, {"titulo": "Bear swim", "jpeg": b"abcd"}]}]
        pr = arma_prompt_curador(huecos_p, _Tipos)
        texto_pr = " ".join(p for p in pr if isinstance(p, str))
        caso("el pedido al curador lleva las reglas (infografías fuera, mejor NINGUNA), lo que dice el presentador, lo que se quería, el título "
             "de cada candidata y UNA foto por candidata",
             "NINGUNA" in texto_pr and "infografías" in texto_pr and "es como un oso polar en el desierto" in texto_pr and "polar bear" in texto_pr
             and "analogía" in texto_pr and "Candidata 2 — título: «Bear swim»" in texto_pr and sum(1 for p in pr if isinstance(p, tuple)) == 2,
             texto_pr[-160:])
        etiquetas: list = []

        def llama_ok(client, types, modelos, prompt, **kw):
            etiquetas.append(kw.get("etiqueta"))
            return kw["valida"]('{"huecos": [{"hueco": 1, "elegidas": [2], "razon": "bien"}]}'), "gemini-3.5-flash-lite", {}

        def llama_cae(*a, **kw):
            raise RuntimeError("503 saturado")
        r_ok = cura(huecos_p, None, _gemini=(None, _Tipos), _modelos=["m"], _llama=llama_ok)
        caso("cura: UNA petición con su propia etiqueta en «Uso» y devuelve lo que opinó",
             r_ok == {1: {"elegidas": [2], "razon": "bien"}} and etiquetas == ["imagenes"], (r_ok, etiquetas))
        caso("cura: si nadie responde devuelve None (se elige por reglas) y sin huecos no pregunta nada",
             cura(huecos_p, None, _gemini=(None, _Tipos), _modelos=["m"], _llama=llama_cae) is None and cura([], None) == {})

        # ---- el vídeo entero con un curador de mentira
        catalogo = {"seo expert": [foto(50, "Seo pyramid"), foto(51, "Man at computer"), foto(52, "Office desk")],
                    "slow website": [foto(60, "Speed infographic"), foto(61, "Snail on road"), foto(62, "Loading bar")],
                    "black t shirts": [foto(70, "Concert crowd"), foto(71, "Fans"), foto(72, "Metal fest")],
                    "t shirt": [foto(73, "Black t shirt on hanger"), foto(74, "Folded tee"), foto(75, "Tee mockup")]}

        def lista_cat(q, estricta=False):
            return [dict(r, _rel=True) for r in catalogo.get(q, [])]

        def mini(c):
            return b"j" + c["id"].encode()
        plan2 = [{"t": 10.0, "busqueda": "seo expert", "motivo": "habla de un experto", "contexto": "un experto en seo te diría que…"},
                 {"t": 40.0, "busqueda": "slow website", "motivo": "web lenta", "contexto": "si la web va lenta"}]
        preguntas: list = []

        def cura_f(huecos):
            preguntas.append(huecos)
            return {1: {"elegidas": [2, 1], "razon": "hombre ante el ordenador"}, 2: {"elegidas": [], "razon": "ninguna es un sitio lento"}}
        bajadas: list = []

        def desc(c):
            bajadas.append(c["id"])
            return foto_falsa()
        info1: list = []
        c1 = Path(tmp) / "c1"
        ov_c, cr_c, ev_c = genera_pngs(plan2, marca, c1, _lista=lista_cat, _descarga=desc, _miniatura=mini, _cura=cura_f, info=info1)
        caso("curador: UNA sola petición para todos los huecos del vídeo, con lo que se oye y el motivo de cada uno y sus 3 fotos",
             len(preguntas) == 1 and [h["n"] for h in preguntas[0]] == [1, 2] and preguntas[0][0]["frase"].startswith("un experto en seo")
             and preguntas[0][0]["motivo"] == "habla de un experto" and all(len(h["candidatas"]) == 3 for h in preguntas[0]), preguntas)
        caso("…usa la que elige (la 2.ª, no la primera de la lista), con SU crédito, y un hueco donde rechaza todas queda sin imagen ni crédito ni evento",
             len(ov_c) == 1 and ov_c[0]["inicio"] == 10.0 and len(cr_c) == 1 and "Man at computer" in cr_c[0] and bajadas == ["id51"]
             and [e["t"] for e in ev_c] == [10.0], (bajadas, cr_c))
        caso("…y deja constancia para el manifest: quién la eligió (ia), cuál y por qué, y el hueco omitido con su razón",
             [(x["estado"], x["por"]) for x in info1] == [("elegida", "ia"), ("omitida", "ia")] and info1[0]["titulo"] == "Man at computer"
             and info1[0]["razon"] == "hombre ante el ordenador" and info1[1]["razon"] == "ninguna es un sitio lento", info1)
        memo = _lee_eleccion(c1)
        caso("lo elegido (y lo rechazado) se recuerda por hueco en eleccion.json, con la foto elegida recortada a lo que hace falta",
             len(memo) == 2 and memo[_clave_hueco(plan2[0])]["resultado"]["url"] == "http://x/51.jpg"
             and memo[_clave_hueco(plan2[1])]["ninguna"] is True and "creator" not in memo[_clave_hueco(plan2[0])]["resultado"], memo)

        # otra pasada (Reintentar, una mejora): mismas búsquedas, otro segundo -> la MISMA foto, sin buscar ni preguntar
        n_pre, buscadas = len(preguntas), []

        def lista_cuenta(q, estricta=False):
            buscadas.append(q)
            return lista_cat(q, estricta)
        ov_r, cr_r, ev_r = genera_pngs([dict(plan2[0], t=12.0), dict(plan2[1], t=44.0)], marca, c1, _lista=lista_cuenta, _descarga=desc,
                                       _miniatura=mini, _cura=cura_f)
        caso("lo recordado vale en la siguiente pasada: ni busca en Openverse ni vuelve a preguntar, da la MISMA foto aunque cambie el segundo, y "
             "el hueco rechazado sigue sin imagen",
             len(preguntas) == n_pre and buscadas == [] and len(ov_r) == 1 and ov_r[0]["inicio"] == 12.0 and "Man at computer" in cr_r[0], (buscadas, len(preguntas)))
        ov_n, cr_n, ev_n = genera_pngs([{"t": 5.0, "busqueda": "seo expert", "alternativas": ["office desk"]}], marca, c1, _lista=lista_cat,
                                       _descarga=desc, _miniatura=mini, _cura=cura_f)
        caso("…pero si la BÚSQUEDA cambia, es otra imagen y se vuelve a elegir", len(preguntas) == n_pre + 1 and len(ov_n) == 1, len(preguntas))
        ov_m, cr_m, ev_m = genera_pngs([plan2[0]], marca, c1, _lista=lista_cat, _descarga=lambda c: None, _miniatura=mini, _cura=cura_f)
        caso("si el enlace recordado ya no se puede bajar, esa foto se olvida (la próxima vez se vuelve a elegir) y el vídeo sigue sin ella",
             ov_m == [] and _clave_hueco(plan2[0]) not in _lee_eleccion(c1) and _clave_hueco(plan2[1]) in _lee_eleccion(c1))

        # 2.ª ronda: rechaza todo lo de la búsqueda del director y queda una más general
        ronda: list = []

        def cura_2(huecos):
            ronda.append([[c["titulo"] for c in h["candidatas"]] for h in huecos])
            return ({1: {"elegidas": [], "razon": "multitudes de un concierto"}} if len(ronda) == 1
                    else {1: {"elegidas": [1], "razon": "camiseta negra colgada"}})
        info2: list = []
        ov_2, cr_2, ev_2 = genera_pngs([{"t": 20.0, "busqueda": "black t shirts", "alternativas": ["t shirt"], "contexto": "camisetas negras"}], marca,
                                       Path(tmp) / "c2", _lista=lista_cat, _descarga=desc, _miniatura=mini, _cura=cura_2, info=info2)
        caso("si el curador rechaza todo y quedan búsquedas más generales, hay una 2.ª ronda SOLO con fotos nuevas (no repite las rechazadas)",
             len(ronda) == 2 and ronda[0] == [["Concert crowd", "Fans", "Metal fest"]]
             and ronda[1] == [["Black t shirt on hanger", "Folded tee", "Tee mockup"]], ronda)
        caso("…y la foto elegida en la 2.ª ronda sale, con su crédito y la búsqueda de la que vino",
             len(ov_2) == 1 and "Black t shirt on hanger" in cr_2[0] and info2[0]["usada"] == "t shirt" and info2[0]["por"] == "ia", info2)
        ronda.clear()

        def cura_2b(huecos):
            ronda.append(1)
            if len(ronda) == 1:
                return {1: {"elegidas": [], "razon": "no"}}
            return None                                           # la 2.ª petición no responde
        ov_2b, *_ = genera_pngs([{"t": 20.0, "busqueda": "black t shirts", "alternativas": ["t shirt"]}], marca, Path(tmp) / "c2b", _lista=lista_cat,
                                _descarga=desc, _miniatura=mini, _cura=cura_2b)
        caso("…si la 2.ª petición falla, vale lo que dijo la 1.ª (ninguna): sin imagen, nunca una por reglas que el curador ya vio y rechazó", ov_2b == [])

        # sin curador (sin clave de Gemini, saturado…): por reglas, como antes, y sin recordar nada
        bajadas.clear()
        c3 = Path(tmp) / "c3"
        info3: list = []
        ov_3, cr_3, ev_3 = genera_pngs(plan2, marca, c3, _lista=lista_cat, _descarga=desc, _miniatura=mini, _cura=lambda hs: None, info=info3)
        caso("si el curador no puede (None), se elige por reglas —la primera relevante de cada hueco— y NO se recuerda nada (la próxima vez sí podrá)",
             len(ov_3) == 2 and bajadas == ["id50", "id60"] and all(x["por"] == "reglas" for x in info3) and not (c3 / "eleccion.json").exists(),
             (bajadas, info3))
        ov_3b, *_ = genera_pngs(plan2, marca, Path(tmp) / "c3b", _lista=lista_cat, _descarga=desc,
                                _miniatura=lambda c: (_ for _ in ()).throw(AssertionError("no hay curador: no debe bajar miniaturas")),
                                cfg=type("C", (), {"get_path": lambda self, k, d=None: False if k == "videos.imagenes_curador" else d})())
        caso("con `videos.imagenes_curador: false` no hay curador (ni se bajan miniaturas): reglas de siempre", len(ov_3b) == 2)

        # la primera elegida no se deja bajar o es diminuta -> la segunda que dijo
        bajadas.clear()

        def desc_mal(c):
            bajadas.append(c["id"])
            return None if c["id"] == "id50" else (Image.new("RGB", (200, 150)) if c["id"] == "id51" else foto_falsa())
        ov_4, cr_4, ev_4 = genera_pngs([plan2[0]], marca, Path(tmp) / "c4", _lista=lista_cat, _descarga=desc_mal, _miniatura=mini,
                                       _cura=lambda hs: {1: {"elegidas": [1, 2], "razon": "x"}})
        caso("si la primera elegida no se deja bajar o la foto real es diminuta, ni sale ni se recuerda: prueba la segunda de las que eligió el curador (y solo esas)",
             ov_4 == [] and bajadas == ["id50", "id51"], bajadas)

        # a las que no se les pudo bajar la miniatura el curador no las ve (no puede juzgarlas)
        preguntas.clear()
        ov_5, *_ = genera_pngs([plan2[0]], marca, Path(tmp) / "c5", _lista=lista_cat, _descarga=desc,
                               _miniatura=lambda c: None if c["id"] in ("id50", "id52") else mini(c), _cura=lambda hs: preguntas.append(hs) or {1: {"elegidas": [1], "razon": "y"}})
        caso("las fotos sin miniatura no se le enseñan al curador, y su número es el de la lista que SÍ vio (la «1» es la única que vio)",
             [c["titulo"] for c in preguntas[0][0]["candidatas"]] == ["Man at computer"] and len(ov_5) == 1, preguntas)
        ov_6, *_ = genera_pngs([plan2[0]], marca, Path(tmp) / "c6", _lista=lista_cat, _descarga=desc, _miniatura=lambda c: None,
                               _cura=lambda hs: {} if not hs else {1: {"elegidas": [1], "razon": "z"}})
        caso("si NINGUNA miniatura se pudo bajar no hay nada que preguntar y ese hueco se resuelve por reglas", len(ov_6) == 1)

        # ---- Openverse: la caché, el límite y las miniaturas (con `urlopen` de mentira)
        class _Resp:
            def __init__(self, b):
                self._b = b

            def read(self, n=None):
                return self._b if n is None else self._b[:n]

        def png(w, h):
            b = BytesIO()
            Image.new("RGB", (w, h), (10, 20, 30)).save(b, format="PNG")
            return b.getvalue()
        peticiones: list = []

        def urlopen_ok(req, timeout=None):
            peticiones.append(req.full_url)
            return _Resp(json.dumps({"results": [
                {"id": "a1", "title": "Polar Bear", "url": "http://x/1.jpg", "thumbnail": "http://x/t1", "width": 800, "height": 600,
                 "attribution": "att", "mature": False, "tags": [{"name": "bear", "accuracy": None}], "creator": "z", "extra": "no"},
                {"id": "a2", "title": "Adult", "url": "http://x/2.jpg", "mature": True}]}).encode())
        real_urlopen, real_ruta = urllib.request.urlopen, _ruta_cache
        g = globals()
        try:
            g["_ruta_cache"] = lambda: Path(tmp) / "ov_cache.json"
            urllib.request.urlopen = urlopen_ok
            _limitado[0] = False
            r1 = _consulta("Polar  Bear")
            r2 = _consulta("polar bear")
            caso("Openverse: pide 20 resultados con licencia comercial+modificación, la misma búsqueda no se repite (caché de 12 h) y de cada "
                 "resultado solo se guarda lo que hace falta (nada de «mature»)",
                 len(peticiones) == 1 and "page_size=20" in peticiones[0] and "license_type=commercial%2Cmodification" in peticiones[0]
                 and r1 == r2 and len(r1) == 1 and "extra" not in r1[0] and r1[0]["tags"] == [{"name": "bear"}], (peticiones, r1))
            cache = json.loads((Path(tmp) / "ov_cache.json").read_text(encoding="utf-8"))
            cache["polar bear"]["ts"] -= 13 * 3600
            (Path(tmp) / "ov_cache.json").write_text(json.dumps(cache), encoding="utf-8")
            _consulta("polar bear")
            caso("…y pasadas 12 h se vuelve a preguntar", len(peticiones) == 2, len(peticiones))

            def urlopen_429(req, timeout=None):
                peticiones.append(req.full_url)
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            urllib.request.urlopen = urlopen_429
            antes = len(peticiones)
            c_a = _consulta("otra cosa")
            c_b = _consulta("y otra más")
            caso("Openverse dice «demasiadas peticiones» (429): esa búsqueda sale sin resultado y las siguientes NO se intentan (cada intento cuenta)",
                 c_a is None and c_b is None and len(peticiones) == antes + 1 and _limitado[0] is True, (c_a, len(peticiones) - antes))
            c_c = _consulta("polar bear")
            caso("…pero lo que ya estaba en la caché sigue sirviendo con el límite puesto", c_c is not None and len(peticiones) == antes + 1)
            _limitado[0] = False

            def urlopen_fotos(req, timeout=None):
                if req.full_url.endswith("orig.jpg"):
                    raise OSError("403 enlace directo bloqueado")
                return _Resp(png(600, 400))
            urllib.request.urlopen = urlopen_fotos
            f_ok = descarga({"url": "http://x/orig.jpg", "thumbnail": "http://x/thumb.jpg"})
            f_no = descarga({"url": "http://x/orig.jpg"})
            caso("descarga: si la original no se deja bajar (enlace directo bloqueado) usa la miniatura de 600 px de Openverse; sin ninguna, None",
                 f_ok is not None and f_ok.size == (600, 400) and f_no is None)
            mini_jpg = miniatura({"thumbnail": "http://x/thumb.jpg", "url": "http://x/orig.jpg"})
            caso("miniatura para el curador: JPEG de 512 px de lado largo (una de 600×400 baja a 512×341; una más chica no se agranda), y None si no se puede bajar",
                 mini_jpg is not None and Image.open(BytesIO(mini_jpg)).size == (512, 341) and miniatura({"url": "http://x/orig.jpg"}) is None)
        finally:
            urllib.request.urlopen = real_urlopen
            g["_ruta_cache"] = real_ruta
            _limitado[0] = False
    return filas
