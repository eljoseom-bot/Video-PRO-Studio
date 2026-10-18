"""🎨 IMAGEN DESTACADA del artículo, dibujada con la IA de Cloudflare en el estilo de las ilustraciones de TU web (el autor, 2026-10-11): cómic/caricatura a tinta,
BLANCO Y NEGRO, sin texto. Sale del propio artículo: Flash-Lite lee su título, su introducción y sus apartados y piensa UNA escena-metáfora (1 petición, 500 al día: no toca las 20
de los Flash); Cloudflare la dibuja (FLUX.2 klein, ≈156 neuronas = lo mismo que una miniatura); Flash-Lite mira que la IA no haya escrito texto (la IA escribe fatal) y, si lo hizo,
se repite con otra semilla. El estilo lo fija el CÓDIGO (`ESTILO`), no la IA.

Se guarda en `videos/<vídeo>/articulo/`: hero.json + hero-1.jpg…hero-4.jpg (el .zip del artículo las lleva). el autor elige una («⭐ Usar como destacada») y `sitio_astro` la pone como `heroImage`
de su web (con el texto alternativo que escribió la IA). Topes: `miniaturas_ia.articulo_max_dia` (10 llamadas a Cloudflare al día para esto) y el cupo general de neuronas, que comparte con las
miniaturas de los vídeos. Funciones puras donde se puede + `prueba()` (Cloudflare y Gemini de mentira)."""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path

from . import articulo as _art
from . import miniaturas_ia as _mia
from .util import log, nombre_modelo

ESTILO_BASE = ("Professional black-and-white comic-book illustration in the style of a classic graphic novel, drawn as a single comic panel with a thin black panel border: bold confident ink outlines, "
               "halftone dots, crosshatching and spot blacks, ")
ESTILO_COLA = ("every screen, browser window, page, card and block of code is drawn ONLY as plain solid bars, empty boxes, big curly braces and icons (never any letters, digits or writing-like squiggles), "
               "strictly black, white and grays only (absolutely no color), bright and clean on warm white paper with lots of white (NOT dark, NOT moody), "
               "wide 16:9 composition with generous margins.")
CON_PERSONA = "realistic adult characters with serious expressive faces (never cute, never childish, no mascots), "
# el autor, 17-oct-2026: «siempre sale una persona apuntando a un computador… en el 90 % de los casos no pondría una persona sino elementos (metadata → código, imágenes alusivas)»:
# por defecto la viñeta NO lleva a nadie: los objetos del tema son los protagonistas, dibujados grandes.
SOLO_OBJETOS = ("the panel shows ONLY objects and things as its heroes, drawn big and clear (absolutely no people, no human figures, no hands, no fingers, no faces, no characters, no mascots, no silhouettes), ")
ESTILO = ESTILO_BASE + CON_PERSONA + ESTILO_COLA            # (la versión con personas: la de antes)


def estilo(con_persona: bool = False) -> str:
    return ESTILO_BASE + (CON_PERSONA if con_persona else SOLO_OBJETOS) + ESTILO_COLA


COMPOSICIONES = {
    "primer_plano": "close-up of ONE big hero object filling the panel, with 2-3 smaller related objects around it",
    "mesa_cenital": "top-down flat lay: the related objects laid out neatly on a desk surface, seen from above",
    "pantalla_iconos": "one huge monitor or browser window in the middle drawn with shapes and icons (cards, stars, braces, arrows, check boxes), with objects coming out of or around it",
    "diagrama": "an illustrated diagram: objects drawn as nodes connected by arrows and lines, clear flow from one object to the next",
    "antes_despues": "a panel split in two halves, the same objects shown wrong on the left and right on the right (use ticks and crosses as icons, never words)",
    "apilado": "objects stacked, balanced, linked or piled up in a clever, literal arrangement that shows the idea of the topic",
}
# FLUX escribe garabatos cuando se le pide «código», «líneas de texto» o «una descripción» en una pantalla (se vio con «metadata»): esas frases pasan a barras lisas y llaves
_TEXTO_EN_PANTALLA = [
    (re.compile(r"\b(?:lines?|rows?|snippets?|blocks?) of (?:text|writing|code)\b|\btext (?:lines?|rows?|blocks?)\b|\bwriting lines\b", re.I), "plain solid bars"),
    (re.compile(r"\b(?:meta[- ]?tags?|html|source|css|json|xml) code\b|\bcode (?:snippets?|lines?|blocks?)\b|\bcode (?:inside|within|in) (?:curly )?braces\b", re.I), "big curly braces enclosing solid bars"),
    (re.compile(r"\b(?:a |the )?(?:title|description|headline|caption|label|url|link text)s? (?:and|&|with) (?:a |the )?(?:title|description|headline|caption|label|url)s?\b", re.I), "two plain bars of different length"),
]
# las personas, las manos y los pronombres: se quitan de la escena cuando el tema no es una persona (la IA a veces las cuela)
_HUMANO = re.compile(r"\b(?:an?\s+|the\s+|two\s+|three\s+|one\s+|some\s+)?(?:(?:adult|young|serious|focused|seo|web|senior|junior|male|female|professional)\s+)*"
                     r"(?:man|men|woman|women|person|people|persons|human|humans|specialist|developer|analyst|worker|professional|expert|consultant|user|users|character|characters|figure|figures|"
                     r"silhouettes?|hands?|fingers?|faces?|someone|somebody|colleagues?|teammates?|designer|marketer|writer|editor|owner|customer|client|shopper|visitor|reader|he|she|his|her|their|they)\b[\s,]*", re.I)
# palabras que vuelven la ilustración «de niños» (el autor, 11-oct-2026: «se ve muy de niño… no tan friendly cartoon»): se quitan de la escena aunque la IA las escriba
_INFANTIL = re.compile(r"\b(?:friendly|cheerful|happy|smiling|smiley|cute|adorable|kawaii|funny|playful|whimsical|charming|jolly|chibi|mascots?|childlike|cartoonish|cartoons?)\b[\s,]*", re.I)
MAX_IMG = 4                                  # imágenes que se guardan por artículo (al pasar, se borra la más vieja que no sea la elegida)
_SYSTEM = ("Eres el ilustrador del blog «{canal}». Dibujas, en una viñeta de cómic profesional en blanco y negro, EXACTAMENTE de lo que trata cada artículo: nunca una imagen genérica de «SEO» ni una "
           "metáfora lejana, sino los OBJETOS propios de ese tema concreto (códigos, páginas, iconos, herramientas, diagramas), casi nunca personas, de modo que quien vea la imagen sin leer el título adivine de qué va.")


def _dir(jid: str, dir_outbox=None) -> Path:
    return _art.ruta(jid, dir_outbox)


# ------------------------------------------------------------------------------------------------------------------ el concepto
def resumen_articulo(a: dict) -> str:
    """Lo que Flash-Lite necesita del artículo para dibujar ESE tema (y no uno genérico): título, palabra clave, meta, introducción y, de cada apartado, su título y el principio de lo que explica."""
    def texto(bl):
        t = " ".join(str(b.get("texto") or "") for b in (bl or []) if b.get("tipo") == "parrafo")
        return re.sub(r"\s+", " ", re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", t.replace("*", ""))).strip()
    apartados = "\n".join(f"- {s_.get('h2') or ''}: {texto(s_.get('bloques'))[:170]}" for s_ in (a.get("secciones") or []))[:2800]
    partes = [f"TÍTULO: {a.get('h1') or a.get('title_seo') or ''}", f"PALABRA CLAVE: {a.get('keyword') or ''}", f"META DESCRIPCIÓN: {a.get('meta_descripcion') or ''}",
              f"INTRODUCCIÓN: {texto(a.get('intro'))[:(700 if a.get('secciones') else 3500)]}", "APARTADOS (lo que explica cada uno):\n" + apartados, f"CONCLUSIÓN: {str(a.get('conclusion') or '')[:300]}"]
    return "\n".join(partes)


def arma_prompt(a: dict) -> str:
    return (f"{resumen_articulo(a)}\n\nDiseña la ilustración de ESTE artículo. Lo más importante: tiene que ser ESPECÍFICA de este tema, no genérica. Piensa qué se ve en la vida real o en pantalla cuando alguien trabaja con eso.\n"
            "Responde con estos campos:\n"
            "- «tema»: en INGLÉS, UNA frase: qué explica el artículo en concreto (la técnica, herramienta o problema; nunca «SEO» a secas).\n"
            "- «objetos»: lista de 3 a 5 elementos visuales CONCRETOS y propios de ese tema, en inglés (2-7 palabras cada uno). Ejemplos del nivel de concreción que quiero: para datos estructurados / schema markup: "
            "«search results page with a rich result card showing stars, price and a product photo», «code block drawn as lines with curly braces», «magnifying glass over the result»; para canonical: "
            "«two identical web pages stacked», «arrow pointing to the original page»; para interlinking: «web pages drawn as cards joined by arrows»; para velocidad web: «stopwatch next to a loading page». "
            "Esos ejemplos solo muestran el NIVEL de concreción: no los copies si el tema es otro. Los objetos salen de lo que REALMENTE explica ESTE artículo (400 artículos distintos = 400 imágenes distintas).\n"
            "- «con_persona»: true o false. FALSE en 9 de cada 10 artículos: la imagen son OBJETOS (código, páginas, iconos, herramientas, diagramas) y NO sale nadie. Pon TRUE solo si el tema trata de verdad de una persona o de su trabajo "
            "(una carrera, una entrevista, liderar un equipo, el comportamiento de un comprador, un hábito personal). Si dudas, FALSE. «Metadata» → código y etiquetas, no un señor con un ordenador.\n"
            "- «composicion»: UNA de estas, la que mejor enseñe el tema (varía: no uses siempre la misma): «primer_plano» (un objeto grande y protagonista), «mesa_cenital» (los objetos vistos desde arriba sobre un escritorio), "
            "«pantalla_iconos» (un monitor o navegador enorme dibujado con iconos y formas), «diagrama» (objetos unidos por flechas), «antes_despues» (dos mitades: mal y bien, con ✓ y ✗), «apilado» (objetos apilados o enlazados).\n"
            "- «escena»: en INGLÉS, 40-90 palabras, UNA viñeta de cómic con esa composición que muestre de forma LITERAL esos objetos (usa al menos 3 y nómbralos tal cual), grandes y claros. "
            "Con «con_persona» en false NO pongas a nadie: ni personas, ni manos, ni caras, ni siluetas, ni «un usuario», ni «un especialista»; la acción la hacen los objetos (una flecha que apunta, una lupa sobre el resultado, un cursor, un sello de ✓). "
            "Con «con_persona» en true, una o dos personas adultas interactuando con ellos. Nada de metáforas lejanas (hilos, arañas, mapas del tesoro, laberintos, arquitectos) salvo que el tema sea justo eso. Una pantalla, un navegador o un resultado de Google "
            "se dibujan con formas e iconos (estrellas, lupa, llaves { }, flechas, casillas), nunca con letras.\n"
            "- CÓDIGO y pantallas: NUNCA pidas «código», «líneas de texto» ni «una descripción» dentro de una pantalla (el dibujo las escribiría como garabatos ilegibles). El código se dibuja como llaves { } grandes con barras lisas dentro; "
            "un resultado de búsqueda, como una tarjeta con barras de distinto largo, estrellas y un icono; una página, como cajas vacías y barras.\n"
            "- NADA de texto en la imagen: sin letras, palabras, números, rótulos, carteles ni logos (los carteles, pantallas y libros se describen con formas e iconos, nunca con escritura).\n"
            "- Tono PROFESIONAL, no infantil: nada de mascotas, animales con sombrero, robots simpáticos, ojos enormes, caras sonrientes exageradas ni palabras como «friendly», «cheerful» o «cute». "
            "Si sale alguien (solo con «con_persona» en true): adultos reales, gesto sobrio, como en una novela gráfica.\n"
            "- Escena LUMINOSA y despejada (un despacho claro, un escritorio, de día), nunca oscura ni lúgubre: papel claro y mucho blanco.\n"
            "- Sin personas reales, famosas ni marcas ni logos (di «a search engine», no «Google»); nada de violencia ni armas.\n"
            "- «alt»: en ESPAÑOL, ≤ 110 caracteres, describe lo que se ve (texto alternativo de la imagen para la web).\n"
            'Responde SOLO con JSON: {"tema": "...", "objetos": ["...", "..."], "con_persona": false, "composicion": "primer_plano", "escena": "...", "alt": "..."}')


def _palabras(t: str) -> set:
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"[a-z]{4,}", str(t).lower())}


def parsea(texto: str) -> dict:
    """JSON de la IA -> {"tema", "objetos", "escena", "alt"} limpios y sin texto en la imagen. Lanza ValueError si no sirve (la cadena prueba otro modelo). Exige que la escena MUESTRE
    los objetos propios del tema (≥ 2 de la lista salen en la escena): es lo que impide una imagen genérica."""
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    d = json.loads(t)
    if not isinstance(d, dict):
        raise ValueError("la IA no devolvió un objeto")
    escena = re.sub(r"\s+", " ", str(d.get("escena") or "")).strip()
    alt = re.sub(r"\s+", " ", str(d.get("alt") or "")).strip().strip("\"'«»")[:110]
    tema = re.sub(r"\s+", " ", str(d.get("tema") or "")).strip()[:200]
    objetos = [re.sub(r"\s+", " ", str(o)).strip()[:120] for o in (d.get("objetos") if isinstance(d.get("objetos"), list) else []) if len(str(o).strip()) >= 6][:5]
    if len(escena) < 40 or len(alt) < 8:
        raise ValueError("la escena o el texto alternativo salieron vacíos")
    if len(tema) < 10 or len(objetos) < 3:
        raise ValueError("falta el tema concreto o los 3 objetos propios de ese tema: la imagen saldría genérica")
    palabras_escena = _palabras(escena)
    if sum(1 for o in objetos if _palabras(o) & palabras_escena) < 2:
        raise ValueError("la escena no muestra los objetos propios del tema (saldría genérica)")
    con_persona = d.get("con_persona") is True or str(d.get("con_persona")).strip().lower() == "true"
    comp = str(d.get("composicion") or "").strip().lower()
    comp = comp if comp in COMPOSICIONES else ""
    escena = _mia.sin_texto(escena)
    escena = re.sub(r"\s+", " ", _INFANTIL.sub("", escena)).strip()
    if not con_persona:                                         # sin personas: se quitan las que la IA colara en la escena
        escena = re.sub(r"\s+", " ", _HUMANO.sub("", escena)).strip(" ,")
    for patron, cambio in _TEXTO_EN_PANTALLA:
        escena = patron.sub(cambio, escena)
    if comp:
        escena = f"{COMPOSICIONES[comp]}. {escena}"
    for patron, cambio in _mia._RIESGO:                        # las palabras que hacen saltar el filtro de contenido de Cloudflare («cannibalization»…)
        escena = re.sub(patron, cambio, escena, flags=re.I)
    return {"tema": tema, "objetos": objetos, "escena": escena[:900], "alt": alt, "con_persona": con_persona, "composicion": comp}


def prompt_final(escena: str, refuerzo: bool = False, con_persona: bool = True) -> str:
    """El prompt que va a Cloudflare. `con_persona` por defecto True = como antes (un concepto guardado de antes de este cambio no cambia de estilo); los nuevos lo traen en su concepto."""
    return " ".join(x for x in (estilo(con_persona), escena, _mia.SIN_TEXTO, _mia._REFUERZO_TEXTO if refuerzo else "") if x)


# ------------------------------------------------------------------------------------------------------------------ estado en disco
def estado(jid: str, dir_outbox=None) -> dict:
    try:
        d = json.loads((_dir(jid, dir_outbox) / "hero.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    imgs = [i for i in (d.get("imagenes") or []) if (_dir(jid, dir_outbox) / str(i.get("archivo", ""))).is_file()]
    elegida = d.get("elegida") if any(i["archivo"] == d.get("elegida") for i in imgs) else None
    return {"concepto": d.get("concepto") or None, "imagenes": imgs, "elegida": elegida}


def _guarda(jid: str, d: dict, dir_outbox=None) -> None:
    from .util import reemplaza
    p = _dir(jid, dir_outbox) / "hero.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    reemplaza(tmp, p)


def elegir(jid: str, archivo: str | None, dir_outbox=None) -> bool:
    """Marca (o desmarca, con None) cuál es la imagen destacada. False si esa imagen no existe."""
    e = estado(jid, dir_outbox)
    if archivo is not None and not any(i["archivo"] == archivo for i in e["imagenes"]):
        return False
    _guarda(jid, {**e, "elegida": archivo}, dir_outbox)
    return True


def elegida(jid: str, dir_outbox=None) -> dict | None:
    """La imagen destacada elegida, lista para mandarla a la web: {"ruta": Path, "alt": str} o None."""
    e = estado(jid, dir_outbox)
    if not e["elegida"]:
        return None
    return {"ruta": _dir(jid, dir_outbox) / e["elegida"], "alt": str((e["concepto"] or {}).get("alt") or "")}


# ------------------------------------------------------------------------------------------------------------------ el tope de cada día
def _uso_ruta(dir_work=None) -> Path:
    return Path(dir_work or _mia._rutas()["work"]) / "imagen_articulo_uso.json"


def intentos_hoy(dir_work=None, ahora: float | None = None) -> int:
    try:
        d = json.loads(_uso_ruta(dir_work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return int(d.get("intentos") or 0) if d.get("dia") == _mia._dia_utc(ahora) else 0


def _apunta(dir_work=None, ahora: float | None = None) -> None:
    n = intentos_hoy(dir_work, ahora) + 1
    p = _uso_ruta(dir_work)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"dia": _mia._dia_utc(ahora), "intentos": n}), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------------------------ generar
def _pide_concepto(a: dict, jid: str, cfg, canal: str, _gemini=None, _modelos=None, _texto=None) -> dict:
    """UNA petición a Flash-Lite -> {"escena", "alt", "modelo"}. Lanza si no se pudo. `_texto(prompt)` -> texto crudo: para las pruebas."""
    prompt = arma_prompt(a)
    if _texto is not None:
        return {**parsea(_texto(prompt)), "modelo": "prueba"}
    if _gemini is None:
        from .director import _gemini as _abre
        g = _abre(cfg)
        if not g:
            raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso» (o en «Primeros pasos»).")
        _gemini = g[:2]
    client, types = _gemini
    if _modelos is None:
        from . import modelos as _m
        _modelos = _m.cadena_lite(cfg, client)
    from . import uso as _uso
    from . import util as _util
    _uso.contexto(job=jid)
    try:
        nuevo, modelo, _gasto = _util.call_gemini_cadena(client, types, _modelos, prompt, system=_SYSTEM.format(canal=canal or "tu canal"), valida=parsea,
                                                         reintento_s=20.0, pasadas=1, timeout_s=75.0, esquema=None, etiqueta="articulo_imagen")
    finally:
        _uso.contexto(job=None)
    return {**nuevo, "modelo": modelo}


def genera(jid: str, cfg=None, nueva_idea: bool = False, dir_outbox=None, dir_work=None, progreso=None, canal: str = "", *, _gemini=None, _modelos=None, _texto_concepto=None,
           _llama=None, _revisa=None, _dormir=time.sleep, _ahora=time.time) -> dict:
    """Dibuja UNA imagen más para el artículo de ese vídeo. Con `nueva_idea` (o si todavía no hay concepto) pide una escena nueva (1 petición a Flash-Lite); si no, la MISMA escena con
    otra semilla (cero Gemini). Nunca lanza. -> {"ok": True, "estado": {...}} o {"ok": False, "error": qué pasó y qué hacer}. Los parámetros con «_»: pruebas (sin red)."""
    avisa = progreso or (lambda t: None)
    p = _mia.parametros(cfg)
    a = _art.lee(jid, dir_outbox)
    if not a:
        return {"ok": False, "error": "Este vídeo todavía no tiene artículo: créalo primero."}
    if _llama is None:
        ok, motivo = _mia.disponible(cfg)
        if not ok:
            return {"ok": False, "error": motivo + " (Mira «Primeros pasos» para ponerlas.)"}
    coste = _mia.neuronas(p["ancho"], p["alto"])
    tope = int(p.get("articulo_max_dia", 10))

    def hay_cupo() -> str:
        if intentos_hoy(dir_work, _ahora()) >= tope:
            return (f"Llegaste al tope de hoy: {tope} llamadas a Cloudflare para imágenes de artículos (cada intento cuenta, también los reintentos). "
                    f"Se reinicia a las {_mia.recarga_local(_ahora())}.")
        if _mia.uso_hoy(dir_work, _ahora())["neuronas"] + coste > min(p["tope_dia"], p["neuronas_dia"]):
            return f"Se acabó el cupo gratis de Cloudflare de hoy (lo comparten las miniaturas de tus vídeos). Se recarga a las {_mia.recarga_local(_ahora())}."
        return ""

    if (m := hay_cupo()):
        return {"ok": False, "error": m}
    est = estado(jid, dir_outbox)
    concepto = est["concepto"]
    if nueva_idea or not concepto:
        avisa("Pensando la escena del dibujo (Flash-Lite)…")
        try:
            concepto = _pide_concepto(a, jid, cfg, canal, _gemini, _modelos, _texto_concepto)
        except Exception as exc:  # noqa: BLE001
            log(f"Imagen del artículo de {jid}: el concepto falló ({exc}).")
            from .ideas import _error_claro
            return {"ok": False, "error": _error_claro(exc).replace("las ideas", "la imagen del artículo")}
    llama = _llama or (lambda pr, sd: _mia.llama_cloudflare(pr, sd, cfg))
    revisa = bool(p["revisa_texto"]) and (_revisa is not None or cfg is not None)
    mira = _revisa or (lambda b: _mia.hay_texto(b, cfg, jid))
    n = len(est["imagenes"]) + (0 if not nueva_idea else 1)
    base = int(hashlib.sha1(f"{jid}{concepto['escena']}".encode("utf-8")).hexdigest()[:7], 16)
    resembrado, retextos, esperado, t0 = False, 0, False, _ahora()
    while True:
        if (m := hay_cupo()):
            return {"ok": False, "error": m}
        avisa("Dibujando con Cloudflare (suele tardar 20-60 s)…")
        try:
            bruto = llama(prompt_final(concepto["escena"], bool(retextos), bool(concepto.get("con_persona", True))), base + 7919 * n)
            _mia.apunta(coste, True, dir_work, _ahora())
            _apunta(dir_work, _ahora())
            from PIL import Image
            im = Image.open(io.BytesIO(bruto))
            im.load()
            veo, donde = None, ""
            if revisa:
                avisa("Revisando que la IA no escribiera texto en el dibujo…")
                veo, donde = mira(_mia._chico(im))
                if veo is True and retextos < int(p["reintentos_texto"]):
                    retextos += 1
                    n += 1
                    log(f"Imagen del artículo de {jid}: la IA escribió texto ({donde}); otra semilla ({retextos}).")
                    continue
            gris = im.convert("L").convert("RGB")              # blanco y negro de verdad: si la IA se coló con algún color, se quita
            carpeta = _dir(jid, dir_outbox)
            carpeta.mkdir(parents=True, exist_ok=True)
            k = 1 + max([int(re.search(r"(\d+)", i["archivo"]).group(1)) for i in est["imagenes"]] or [0])
            archivo = f"hero-{k}.jpg"
            (carpeta / archivo).write_bytes(_mia.a_jpg(gris))
            imgs = est["imagenes"] + [{"archivo": archivo, "semilla": base + 7919 * n, "ts": _ahora(), "con_texto": veo is True, "segundos": round(_ahora() - t0, 1)}]
            while len(imgs) > MAX_IMG:                          # se borra la más vieja que no sea la elegida
                viejo = next(i for i in imgs if i["archivo"] != est["elegida"])
                (carpeta / viejo["archivo"]).unlink(missing_ok=True)
                imgs.remove(viejo)
            _guarda(jid, {"concepto": {"tema": concepto.get("tema", ""), "objetos": concepto.get("objetos", []), "escena": concepto["escena"], "alt": concepto["alt"], "modelo": concepto.get("modelo", ""),
                                       "con_persona": bool(concepto.get("con_persona", True)), "composicion": concepto.get("composicion", ""),
                                       "nombre_modelo": nombre_modelo(concepto["modelo"]) if concepto.get("modelo") not in (None, "", "prueba") else ""},
                          "imagenes": imgs, "elegida": est["elegida"]}, dir_outbox)
            log(f"Imagen del artículo de {jid}: {archivo} en {_ahora() - t0:.0f} s.")
            res = estado(jid, dir_outbox)
            res["aviso"] = (f"Puede que la IA haya escrito algo de texto en el dibujo ({donde}): si lo ves, pulsa «↻ Otra»." if veo is True else "")
            return {"ok": True, "estado": res}
        except _mia.Marcada:
            _mia.apunta(coste, False, dir_work, _ahora())
            _apunta(dir_work, _ahora())
            if not resembrado:
                resembrado, n = True, n + 1
                continue
            return {"ok": False, "error": "El filtro de Cloudflare marcó dos dibujos seguidos: pulsa «💡 Otra idea» para probar otra escena."}
        except _mia.ErrorCuenta as exc:
            return {"ok": False, "error": str(exc)}
        except _mia.ErrorServidor as exc:
            _mia.apunta(coste, False, dir_work, _ahora())
            _apunta(dir_work, _ahora())
            if esperado:
                return {"ok": False, "error": f"Cloudflare no respondió ({exc}) ni {p['reintento_min']:g} min después. Prueba otra vez en un rato."}
            esperado = True
            avisa(f"Cloudflare no respondió ({exc}): espero {p['reintento_min']:g} min y reintento una vez…")
            _dormir(float(p["reintento_min"]) * 60)
        except Exception as exc:  # noqa: BLE001 — la imagen no se pudo leer o guardar
            return {"ok": False, "error": f"No se pudo guardar el dibujo ({str(exc)[:160]})."}


# ------------------------------------------------------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    """Cloudflare y Gemini de mentira (nada gasta): el concepto, el estilo fijo, el blanco y negro, el texto, los topes, elegir y el reparto."""
    import tempfile

    from PIL import Image
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:300]})

    def jpeg(color=(200, 40, 40), tam=(1280, 720)):
        b = io.BytesIO()
        Image.new("RGB", tam, color).save(b, "JPEG")
        return b.getvalue()

    escena = ("A huge wall map with pinned web pages drawn as cards, threads joining the pages and converging toward one large computer showing only simple icons, "
              "drawn with fine ink lines and halftone shading.")
    objetos = ["wall map with pinned web pages", "threads joining the pages", "large computer showing simple icons"]
    respuesta = json.dumps({"tema": "How internal links connect pages of a website", "objetos": objetos, "escena": escena, "con_persona": False, "composicion": "diagrama",
                            "alt": "Caricatura en blanco y negro de un imán gigante que atrae personas hacia un ordenador"})
    a = {"h1": "Cómo Dominar el Interlinking", "keyword": "interlinking en SEO", "title_seo": "x", "intro": [{"tipo": "parrafo", "texto": "El **interlinking** conecta páginas."}],
         "secciones": [{"h2": "Qué es", "bloques": []}, {"h2": "Cómo hacerlo", "bloques": []}], "conclusion": "Empieza hoy.", "jid": "x"}
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        out, work = t / "out", t / "work"
        jid = "2026-01-01-12-00-00-aaa--pro"
        (out / jid / "articulo").mkdir(parents=True)
        work.mkdir()
        (out / jid / "articulo" / "articulo.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        prompts, pedidos = [], []

        def llama(pr, sd):
            prompts.append((pr, sd))
            return jpeg()

        def concepto(pr):
            pedidos.append(pr)
            return respuesta

        r = genera(jid, None, dir_outbox=out, dir_work=work, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        e = r.get("estado") or {}
        caso("genera una imagen: 1 petición de concepto (con el título, la introducción y los apartados del artículo), 1 llamada a Cloudflare y un hero-1.jpg",
             r["ok"] and len(pedidos) == 1 and len(prompts) == 1 and "Cómo Dominar el Interlinking" in pedidos[0] and "Cómo hacerlo" in pedidos[0]
             and (out / jid / "articulo/hero-1.jpg").is_file() and len(e["imagenes"]) == 1 and "Caricatura" in e["concepto"]["alt"], r)
        infantil = parsea(json.dumps({"tema": "A robot hands over the key to a marketer", "objetos": ["robot handing a key", "marketer at a desk", "modern office room"],
                                      "escena": "A friendly cheerful cartoon robot, cute and smiling, mascot of the site, hands a cartoonish key to a happy playful marketer at a desk in a modern office", "con_persona": True,
                                      "alt": "Un robot entrega una llave a un profesional"}))["escena"]
        caso("las palabras que vuelven la imagen «de niños» (friendly, cheerful, cute, cartoon, mascot…) se quitan de la escena aunque la IA las escriba, y el ESTILO pide algo profesional y serio",
             not re.search(r"friendly|cheerful|cute|smiling|mascot|cartoon|playful|happy", infantil, re.I) and "robot" in infantil and "marketer" in infantil
             and "professional" in ESTILO.lower() and "never cute, never childish" in ESTILO and "friendly" not in ESTILO.lower() and "cartoon" not in ESTILO.lower(), infantil)
        caso("el ESTILO lo pone el código (blanco y negro, cómic a tinta) y el prompt lleva la regla de «sin texto»",
             prompts[0][0].startswith(estilo(False)) and "strictly black, white and grays only" in prompts[0][0] and _mia.SIN_TEXTO in prompts[0][0] and escena[:40] in prompts[0][0], prompts[0][0][:200])
        caso("POR DEFECTO NO SALE NADIE (el autor, 17-oct: «el 90 % no pondría una persona sino elementos»): el estilo dice «solo objetos… ni personas, ni manos, ni caras» y NO pide personajes",
             "ONLY objects" in prompts[0][0] and "no people" in prompts[0][0] and "realistic adult characters" not in prompts[0][0] and "no hands" in prompts[0][0], prompts[0][0][100:380])
        caso("la composición que elige la IA (diagrama…) abre la escena, y el concepto guarda `con_persona` y `composicion`", prompts[0][0].count(COMPOSICIONES["diagrama"]) == 1 and e["concepto"].get("con_persona") is False and e["concepto"].get("composicion") == "diagrama", e["concepto"])
        guardada = Image.open(out / jid / "articulo/hero-1.jpg").convert("RGB")
        px = guardada.resize((8, 8)).getdata()
        caso("sale en blanco y negro DE VERDAD: aunque la IA se cuele con un color (aquí rojo), la imagen guardada es gris", all(abs(p_[0] - p_[1]) < 6 and abs(p_[1] - p_[2]) < 6 for p_ in px), list(px)[:2])
        r2 = genera(jid, None, dir_outbox=out, dir_work=work, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("«↻ Otra» dibuja la MISMA escena con otra semilla: cero peticiones de concepto y un hero-2.jpg",
             r2["ok"] and len(pedidos) == 1 and len(prompts) == 2 and prompts[1][1] != prompts[0][1] and prompts[1][0] == prompts[0][0] and len(r2["estado"]["imagenes"]) == 2, r2)
        r3 = genera(jid, None, nueva_idea=True, dir_outbox=out, dir_work=work, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("«💡 Otra idea» pide un concepto nuevo (1 petición más)", r3["ok"] and len(pedidos) == 2 and len(r3["estado"]["imagenes"]) == 3, r3)
        caso("elegir la destacada: queda guardada, devuelve ruta y texto alternativo, una que no existe se rechaza y se puede quitar",
             elegir(jid, "hero-2.jpg", out) and (elegida(jid, out) or {}).get("ruta", Path("x")).name == "hero-2.jpg" and "Caricatura" in elegida(jid, out)["alt"]
             and not elegir(jid, "hero-9.jpg", out) and elegir(jid, None, out) and elegida(jid, out) is None)
        elegir(jid, "hero-1.jpg", out)
        for _ in range(3):
            genera(jid, None, dir_outbox=out, dir_work=work, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        e4 = estado(jid, out)
        caso(f"se guardan como mucho {MAX_IMG}; al pasar se borra la más vieja que NO es la elegida (la elegida se queda)",
             len(e4["imagenes"]) == MAX_IMG and any(i["archivo"] == "hero-1.jpg" for i in e4["imagenes"]) and e4["elegida"] == "hero-1.jpg"
             and len(list((out / jid / "articulo").glob("hero-*.jpg"))) == MAX_IMG, e4)
        # el texto escrito por la IA
        texto_ok = {"n": 0}

        def revisa_con_texto(b):
            texto_ok["n"] += 1
            return (True, "una palabra en un cartel") if texto_ok["n"] <= 1 else (False, "")
        n0 = len(prompts)
        r5 = genera(jid, None, dir_outbox=out, dir_work=work, _llama=llama, _texto_concepto=concepto, _revisa=revisa_con_texto)
        caso("si la IA escribió texto, se repite con otra semilla y el refuerzo de «cero texto» (2 llamadas, la imagen buena)",
             r5["ok"] and len(prompts) == n0 + 2 and prompts[-1][0].count("STRICT RULE") == 1 and prompts[-2][0].count("STRICT RULE") == 0 and not r5["estado"]["imagenes"][-1]["con_texto"], r5)
        # topes
        work2 = t / "work2"
        work2.mkdir()
        for _ in range(int(_mia.parametros(None)["articulo_max_dia"])):
            _apunta(work2)
        r6 = genera(jid, None, dir_outbox=out, dir_work=work2, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("el tope de cada día (15 llamadas a Cloudflare para imágenes de artículo) se respeta y dice a qué hora se reinicia", not r6["ok"] and "tope de hoy" in r6["error"], r6)
        work3 = t / "work3"
        work3.mkdir()
        _mia.apunta(9500, True, work3)
        r7 = genera(jid, None, dir_outbox=out, dir_work=work3, _llama=llama, _texto_concepto=concepto, _revisa=lambda b: (False, ""))
        caso("y el cupo general de neuronas (el que comparten las miniaturas de los vídeos) también", not r7["ok"] and "cupo gratis" in r7["error"], r7)
        # servidor caído: 5 min y un reintento
        dormidas, llamadas = [], []

        def cae(pr, sd):
            llamadas.append(1)
            raise _mia.ErrorServidor("HTTP 503")
        r8 = genera(jid, None, dir_outbox=out, dir_work=t / "w8", _llama=cae, _texto_concepto=concepto, _revisa=lambda b: (False, ""), _dormir=dormidas.append)
        caso("servidor caído: espera 5 min, reintenta UNA vez y entonces se rinde diciéndolo (la regla de siempre)", not r8["ok"] and dormidas == [300.0] and len(llamadas) == 2 and "no respondió" in r8["error"], r8)
        # concepto malo
        malos = [json.dumps({"escena": "corta", "alt": "x"}), "esto no es json", json.dumps([1, 2])]
        generico = {"tema": "Structured data for rich results", "objetos": ["search results page with a rich result card", "code block with curly braces", "magnifying glass over the result"],
                    "alt": "Un hombre escribe en un cuaderno", "escena": "An adult man writes thoughtfully in a notebook at a bright wooden desk beside a window, a mug of coffee near his hand, drawn in fine ink lines."}
        sin_objetos = {k: v for k, v in generico.items() if k != "objetos"}
        especifico = {**generico, "escena": "An adult SEO specialist points at a large screen showing a search results page with a rich result card full of stars and a price, next to a code block with curly braces, "
                                           "while a colleague holds a magnifying glass over the result, bright office."}
        caso("IMAGEN ESPECÍFICA DEL TEMA: una escena genérica (un hombre escribiendo en un cuaderno) o sin lista de objetos se RECHAZA; la que muestra los objetos propios del artículo (resultado enriquecido, llaves, lupa) pasa",
             _lanza(parsea, json.dumps(generico)) and _lanza(parsea, json.dumps(sin_objetos)) and not _lanza(parsea, json.dumps(especifico))
             and len(parsea(json.dumps(especifico))["objetos"]) == 3, "")
        dos = {**especifico, "objetos": especifico["objetos"][:2]}
        sin_tema = {**especifico, "tema": ""}
        caso("con menos de 3 objetos propios del tema, o sin tema, se rechaza (la imagen saldría genérica)", _lanza(parsea, json.dumps(dos)) and _lanza(parsea, json.dumps(sin_tema)), "")
        a_ = {"h1": "Guía de Schema Markup", "keyword": "Schema Markup", "meta_descripcion": "Aprende qué son los datos estructurados", "intro": [{"tipo": "parrafo", "texto": "Los **rich snippets** [[A3|no aparecen]] solos."}],
              "secciones": [{"h2": "Validar datos estructurados", "bloques": [{"tipo": "parrafo", "texto": "Usa la prueba de resultados enriquecidos de Google para validar el JSON-LD."}]},
                            {"h2": "Tipos de schema", "bloques": [{"tipo": "parrafo", "texto": "Product, FAQ y Review son los más usados."}]}], "conclusion": "Impleméntalo."}
        pr = arma_prompt(a_)
        caso("el prompt manda TODO lo específico del artículo (título, meta, cada apartado con lo que explica) y exige objetos concretos, cómic profesional y escena luminosa, sin los enlaces [[…]] ni los asteriscos",
             "Validar datos estructurados: Usa la prueba de resultados enriquecidos" in pr and "Aprende qué son los datos estructurados" in pr and "[[" not in pr and "**" not in pr
             and "ESPECÍFICA" in pr and "«objetos»" in pr and "LITERAL" in pr and "PROFESIONAL" in pr and "LUMINOSA" in pr and "comic" in ESTILO.lower() and "comic panel" in ESTILO.lower(), pr[:300])
        caso("un concepto que no sirve (corto, no es JSON, no es un objeto) se rechaza", all(_lanza(parsea, m) for m in malos))
        con_gente = ("An adult SEO specialist points at a huge wall map with pinned web pages drawn as cards while a woman and two colleagues watch, her hands on the keyboard of one large computer showing "
                     "simple icons, threads joining the pages.")
        sin = parsea(json.dumps({"tema": "How internal links connect pages", "objetos": objetos, "escena": con_gente, "con_persona": False, "alt": "Alt de prueba largo"}))
        con = parsea(json.dumps({"tema": "How internal links connect pages", "objetos": objetos, "escena": con_gente, "con_persona": True, "alt": "Alt de prueba largo"}))
        falta = parsea(json.dumps({"tema": "How internal links connect pages", "objetos": objetos, "escena": con_gente, "alt": "Alt de prueba largo"}))
        caso("sin `con_persona` (o en false) las personas, manos y pronombres que la IA cuele en la escena se QUITAN y quedan los objetos; con `con_persona` en true se respetan",
             not re.search(r"\b(?:man|woman|specialist|colleagues?|hands?|her|his)\b", sin["escena"], re.I) and "wall map" in sin["escena"] and "computer" in sin["escena"] and falta["con_persona"] is False
             and "specialist" in con["escena"] and "woman" in con["escena"] and con["con_persona"] is True, (sin["escena"], con["escena"][:80]))
        caso("con persona el estilo vuelve a pedir personajes adultos serios; un concepto guardado de ANTES (sin la clave) mantiene su estilo de siempre",
             "realistic adult characters" in prompt_final("x", False, True) and "ONLY objects" not in prompt_final("x", False, True) and "ONLY objects" in prompt_final("x", False, False) and "realistic adult characters" in prompt_final("x"), prompt_final("x")[:120])
        caso("una composición inventada se ignora y las 6 válidas están", parsea(json.dumps({"tema": "How internal links connect pages", "objetos": objetos, "escena": escena, "composicion": "dibujo_raro", "alt": "Alt de prueba largo"}))["composicion"] == ""
             and len(COMPOSICIONES) == 6)
        garabato = parsea(json.dumps({"tema": "How meta tags work in search results", "objetos": ["browser window with meta tags", "search result card", "large magnifying glass"],
                                      "escena": "A browser window showing meta tags code inside curly braces beside a search result card with lines of text, a title and a description, and a large magnifying glass.", "alt": "Alt de prueba largo"}))["escena"]
        caso("«código», «líneas de texto» y «título y descripción» en una pantalla se cambian por llaves y barras lisas (FLUX los escribiría como garabatos)",
             "code inside" not in garabato.lower() and "lines of text" not in garabato.lower() and "title and a description" not in garabato.lower() and "solid bars" in garabato and "curly braces" in garabato, garabato)
        caso("el estilo del código manda que todo se dibuje con barras, cajas, llaves e iconos, sin letras ni garabatos", "plain solid bars" in estilo(False) and "never any letters" in estilo(False) and "plain solid bars" in estilo(True), estilo(False)[250:520])
        pr_ = arma_prompt({"h1": "Metadata", "keyword": "metadata", "secciones": []})
        caso("el prompt le dice a la IA que NO ponga personas (FALSE en 9 de cada 10), el ejemplo «metadata → código» y las composiciones a elegir",
             "con_persona" in pr_ and "9 de cada 10" in pr_ and "Metadata» → código" in pr_ and "mesa_cenital" in pr_ and "NO pongas a nadie" in pr_, pr_[:200])
        c_ = parsea(json.dumps({"tema": "Internal links between pages", "objetos": objetos, "escena": escena + ' A sign reading "HELLO" hangs above the cannibalization chart.', "alt": "Alt de prueba largo"}))
        caso("el texto entre comillas y las palabras que asustan al filtro se limpian antes de dibujar", "HELLO" not in c_["escena"] and "cannibalization" not in c_["escena"].lower(), c_["escena"][-120:])
        r9 = genera("2026-01-09-no-existe--pro", None, dir_outbox=out, dir_work=work, _llama=llama)
        caso("un vídeo sin artículo avisa en vez de fallar", not r9["ok"] and "artículo" in r9["error"], r9)
    return filas


def _lanza(fn, *a) -> bool:
    try:
        fn(*a)
        return False
    except (ValueError, KeyError):
        return True
