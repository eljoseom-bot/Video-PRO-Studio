"""«↻» junto a cada prompt de miniatura (el autor, 2026-10-03: «botones de refresh al lado de cada miniatura… que refresque ese
concepto… y posterior, refresh en la IA de Cloudflare»): cambia SOLO ese concepto (la escena del prompt, su titular y su cara)
por una idea NUEVA, sin volver a analizar el vídeo ni tocar los otros dos. Luego, «↻ Otra» en la imagen de esa letra dibuja el
concepto nuevo (el dibujo de antes sigue ahí, marcado «de la idea anterior», hasta que se pida el nuevo).

- UNA petición a Flash-Lite por clic (500 al día): no gasta las 20 diarias de los Flash que analizan los vídeos (su regla:
  nada nuevo puede quitarle cuota al modelo más potente). Sale en «Uso» como «Concepto de miniatura nuevo (Flash-Lite)».
- Ve lo mismo que el director: lo que se dice en el vídeo YA montado, el título, el gancho, los capítulos, el contexto del
  canal; los OTROS dos conceptos (para que el A/B/C siga viéndose distinto de un vistazo), el formato de ESA letra y los
  conceptos que esa letra ya tuvo (para no repetir ni hacer una variación del mismo).
- La IA NO escribe texto en la imagen: el concepto se limpia de todo lo que pida texto (`miniaturas_ia.sin_texto`), el titular
  va aparte (lo escribe el programa con Rubik) y las reglas fijas las pone el código (`director._asegura_reglas_miniatura`: la
  zona de la cara de ese formato, la paleta de la marca y «sin texto»), igual que en el análisis.
- No se pierde nada: el concepto sustituido pasa a `miniatura_prompts_anteriores` (el más reciente primero) en el manifest, y
  todo va también a `videos.json` y al plan de esa versión (`work/<base>--pro/director_<slug>.json`), porque «Reintentar»
  rehace el manifest desde el plan.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import util as _util
from .util import log, nombre_modelo

MAX_ANTERIORES = 30          # conceptos sustituidos que se guardan (los más viejos se caen)
MAX_CHARS_TEXTO = 12000      # ≈ 3 mil tokens de transcripción: para elegir una escena no hace falta más
PARECIDO_MAX = 0.6           # un concepto nuevo no puede parecerse a uno ya visto más que esto (palabras en común)
LETRAS = ("A", "B", "C")

# lo que pide cada formato (el mismo reparto que el director: ver `director._PROMPT`, punto 5)
_FORMATOS_ES = (
    "A · CARA A LA DERECHA y versión COMPLETA y cargada: lo importante va a la IZQUIERDA y al centro-izquierda (≈60 % del "
    "ancho); la derecha (≈35-40 %) solo lleva fondo poco importante. Un protagonista GRANDE y 6-10 elementos del tema en "
    "capas alrededor (pantallas o móviles con gráficas y flechas, pictogramas de las herramientas, iconos en círculos, rayos, "
    "insignias, una mascota simpática).",
    "B · CARA EN EL CENTRO: composición SIMÉTRICA — elementos a la IZQUIERDA y a la DERECHA, idealmente un contraste del tema "
    "(antes/después, problema/solución, una herramienta frente a otra), y la franja CENTRAL (≈ un tercio del ancho) sin nada "
    "importante. Un protagonista claro por lado y 3-5 elementos secundarios en tinta/crema con trama. Es la variante que PRUEBA OTROS COLORES: su fondo es de COLOR de la marca "
    "(naranja intenso, dorado o dividido entre los dos), nunca negro ni tinta.",
    "C · MINIMALISTA: UN solo elemento protagonista grande (y, como mucho, 1-2 detalles pequeños), TODO AGRUPADO en un "
    "bloque compacto en los ≈2/3 izquierdos, sobre un fondo liso y tranquilo con mucho aire. A la derecha (≈30 %) queda el "
    "hueco de la cara. El mismo cómic pero LIMPIO: el protagonista naranja y casi nada más.",
)

_SYSTEM = """Eres el director de miniaturas del canal de YouTube "{canal}": propones conceptos de miniatura que se entienden de
un vistazo aunque se vean diminutos. Respondes SOLO con JSON válido, sin markdown."""

_PROMPT = """Contexto del canal: {contexto}

Este vídeo YA está editado. Título: «{titulo}».
Gancho de su descripción: {gancho}

Capítulos del vídeo:
{capitulos}

Lo que se dice en el vídeo (transcripción del vídeo YA montado, sin lo recortado):
{texto}

El autor hace un A/B/C test con TRES miniaturas de composiciones distintas. Los conceptos son descripciones EN INGLÉS de la
escena (se le dan a un generador de imágenes). Se QUEDA con estos dos, tal cual (la nueva tiene que verse DISTINTA a ellos
de un vistazo: otra composición, otro objeto protagonista, otra escena):
{otros}

Quiere cambiar SOLO la miniatura {letra}. Su concepto de ahora es:
«{actual}»

Conceptos que esa miniatura ya tuvo (no repitas ninguno ni hagas una variación suya: busca OTRA idea):
{anteriores}

Escribe el concepto NUEVO de la miniatura {letra} con estas reglas:
   - Formato de la {letra}: {formato}
   - "miniatura_prompt": 4-6 frases EN INGLÉS que describen SOLO la escena. No escribas «YouTube thumbnail…», ni colores ni
     códigos hex, ni la frase de la zona de la cara: el sistema los añade. En la B tampoco menciones un fondo oscuro, negro o de
     tinta: su fondo es de color y lo pone el sistema.
   - ENFOCADO en el tema concreto de ESTE vídeo (mira la transcripción): elementos visuales concretos de lo que se habla, el
     resultado o contraste que promete. Nada genérico que valdría para cualquier vídeo del canal.
   - Ilustración tipo CÓMIC/caricatura (no foto realista): trazo cartoon con contornos gruesos de tinta, trama de puntos
     Ben-Day, alto contraste y ráfagas detrás del protagonista. El protagonista en naranja de la marca contra un resto
     sobrio (menciónalo).
   - NINGÚN TEXTO dentro de la imagen: ni rótulo, banner o titular, ni palabras, cifras o siglas, ni el nombre de una
     herramienta junto a su logo; las pantallas, páginas, libros y carteles muestran SOLO formas, iconos y gráficas, sin
     escritura. Las herramientas se representan con un pictograma sencillo (lupa, robot, engranaje, gráfica…), nunca con su
     logo. Deja libre arriba el hueco del titular.
   - "miniatura_titular": el TITULAR corto que el programa escribe encima (el prompt NO lo lleva): 2-4 palabras, TODO EN
     MAYÚSCULAS, en español (salvo términos técnicos que se dicen en inglés), sin comillas, con un gancho distinto del título
     del vídeo y de los de las otras miniaturas: {titulares_otros}.
   - "miniatura_cara": el gesto de la cara del autor que va en esa miniatura, SOLO de esta lista: {caras}. Si no lo tienes
     claro, "feliz".
   - "idea": una frase corta en español para el autor que diga de qué va el concepto (p. ej. «Un cronómetro gigante frente a
     un calendario»).
Responde SOLO con este JSON: {{"miniatura_prompt": "...", "miniatura_titular": "...", "miniatura_cara": "feliz", "idea": "..."}}"""


# ============================================================================================ lo que ve la IA
def _escena(prompt: str) -> str:
    """El concepto de un prompt SIN lo que añade el código (apertura, zona de la cara, «sin texto», paleta): lo que de verdad
    describe la escena. Es lo que se le enseña a la IA de los otros conceptos y lo que se compara para no repetir."""
    from . import director as _d
    from . import miniaturas_ia as _mia
    p = str(prompt or "")
    p = _d._BLOQUE_PALETA_RE.sub("", p)
    p = _d._FONDO_B_RE.sub("", p)
    p = _d._FRASE_ZONA_CARA_RE.sub(" ", p)
    p = p.replace(_mia.SIN_TEXTO, "").replace(_d._APERTURA_MINIATURA, "")
    return re.sub(r"\s{2,}", " ", p).strip()


def _palabras(texto: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", _util.normalize(texto)) if w}


def parecido(a: str, b: str) -> float:
    """0-1: cuántas palabras (de 4+ letras) tienen en común dos conceptos, sobre el total de las dos."""
    x, y = _palabras(_escena(a)), _palabras(_escena(b))
    return len(x & y) / len(x | y) if x and y else 0.0


def vistos_de(entrada: dict, letra_i: int) -> list[str]:
    """Los conceptos que esa letra ya tuvo, el más reciente primero (de `miniatura_prompts_anteriores`; cada fila lleva su
    letra: «A» o «B»…, y se leen también las filas sueltas de una lista plana)."""
    out: list[str] = []
    for x in entrada.get("miniatura_prompts_anteriores") or []:
        if isinstance(x, dict):
            if x.get("letra") in (None, LETRAS[letra_i]) and str(x.get("prompt") or "").strip():
                out.append(str(x["prompt"]))
        elif str(x or "").strip():
            out.append(str(x))
    return out


def arma_prompt(entrada: dict, words: list[dict], contexto: str, indice: int, caras: str) -> str:
    from .titulos import texto_montado
    prompts = [str(p or "") for p in (entrada.get("miniatura_prompts") or [])]
    titulares = [str(t or "").strip() for t in (entrada.get("miniatura_titulares") or [])]
    titulares += [""] * (3 - len(titulares))
    otros = "\n".join(f"- Miniatura {LETRAS[j]}: {_escena(p)}" for j, p in enumerate(prompts) if j != indice and p.strip()) \
        or "- (ninguno)"
    vistos = vistos_de(entrada, indice)
    anteriores = "\n".join(f"- {_escena(v)}" for v in vistos[:8]) or "- (ninguno)"
    ya = [t for j, t in enumerate(titulares) if j != indice and t]
    return _PROMPT.format(
        contexto=contexto or "canal de YouTube", titulo=entrada.get("titulo") or "(sin título)",
        gancho=entrada.get("gancho") or "(sin gancho)",
        capitulos=str(entrada.get("capitulos") or "").strip() or "(sin capítulos)",
        texto=(texto_montado(words, entrada.get("segments")) or "(sin transcripción)")[:MAX_CHARS_TEXTO],
        otros=otros, letra=LETRAS[indice], actual=_escena(prompts[indice]) if indice < len(prompts) else "(vacío)",
        anteriores=anteriores, formato=_FORMATOS_ES[indice], caras=caras or '"feliz"',
        titulares_otros=" · ".join(f"«{t}»" for t in ya) or "(ninguno)")


def parsea(texto: str, indice: int, marca: dict, previos: list[str], caras_ok: list[str] | None = None) -> dict:
    """El JSON de la IA -> {"prompt", "titular", "cara", "idea"} ya LIMPIOS: el concepto sin texto y con las reglas fijas del
    código (zona de la cara de esa letra, paleta, «sin texto»), el titular corto. Lanza si no vale (sin concepto, o casi
    igual a uno que ya salió): así la cadena prueba con el siguiente Flash-Lite."""
    from . import director as _d
    from . import miniaturas_ia as _mia
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    d = json.loads(t)
    if isinstance(d, list) and d:
        d = d[0]
    if not isinstance(d, dict):
        raise ValueError("la IA no devolvió un concepto")
    crudo = re.sub(r"\s+", " ", str(d.get("miniatura_prompt") or d.get("prompt") or "")).strip()[:1600]
    if len(crudo) < 60:
        raise ValueError("la IA no escribió un concepto de miniatura")
    for v in previos:
        if parecido(crudo, v) >= PARECIDO_MAX:
            raise ValueError("la IA solo devolvió un concepto casi igual a uno que ya habías visto")
    titular = _d.limpia_titular(d.get("miniatura_titular") or d.get("titular") or _mia.titular_de(crudo))
    cara = re.sub(r"\s+", " ", str(d.get("miniatura_cara") or d.get("cara") or "")).strip()[:40]
    if caras_ok and cara and cara.lower() not in {c.lower() for c in caras_ok}:
        cara = ""                                          # un gesto que el autor no tiene: se queda la cara que había
    limpio = _mia.sin_texto(crudo, siglas=False)
    if len(limpio) < 40:
        raise ValueError("el concepto de la IA era casi todo texto")
    return {"prompt": _d._asegura_reglas_miniatura(limpio, marca, indice), "titular": titular, "cara": cara,
            "idea": re.sub(r"\s+", " ", str(d.get("idea") or "")).strip()[:160]}


_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from pydantic import BaseModel

            class Concepto(BaseModel):
                miniatura_prompt: str
                miniatura_titular: str
                miniatura_cara: str = ""
                idea: str = ""

            _ESQUEMA = Concepto
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQUEMA = False
    return _ESQUEMA or None


def _lee_json(p: Path, defecto=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _escribe_json(p: Path, d) -> None:
    """Atómico: nunca deja un manifest a medias (si se corta la luz, queda el de antes)."""
    tmp = Path(p).with_suffix(Path(p).suffix + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _error_claro(exc: Exception) -> str:
    from .director import _motivo_corto, es_saturacion
    msg = str(exc).lower()
    if es_saturacion(exc):
        return ("Gemini (Flash-Lite) está saturado ahora mismo. Prueba otra vez en un minuto: los intentos saturados no "
                "gastan tu cuota.")
    if "exceeded your current quota" in msg or "quota exceeded" in msg:
        return ("Se acabaron por hoy las peticiones gratis de Flash-Lite (500 al día). Vuelve a intentarlo mañana: la "
                "cuenta se reinicia a las 2:00 a. m.")
    if "ya habías visto" in msg:
        return "La IA solo devolvió ideas que ya habías visto. Dale otra vez al ↻."
    return f"No se pudo pedir un concepto nuevo ({_motivo_corto(str(exc))}). Prueba otra vez en un momento."


def renueva(out_dir: Path, work: Path, slug: str, cfg, indice: int, jid: str = "", _gemini=None, _modelos=None,
            _caras=None) -> dict:
    """Cambia SOLO el concepto número `indice` (0 = A, 1 = B, 2 = C) de la versión de `out_dir` (videos/<jid>/): su prompt,
    su titular y su cara, con UNA petición a Flash-Lite. Lo guarda en el manifest, en videos.json y en el plan `slug` de
    `work`; el concepto sustituido pasa a `miniatura_prompts_anteriores`. La imagen que ya había NO se toca (queda marcada
    «de la idea anterior» hasta que se pida otra). Devuelve {"ok": True, "letra", "indice", "prompts", "titulares", "idea",
    "titular", "modelo", "nombre"} o {"ok": False, "error": qué pasó y qué hacer}. Nunca lanza. `_gemini` = (client, types)
    y `_modelos` = la cadena, `_caras` = (texto de gestos para el prompt, lista de gestos), para las pruebas."""
    out_dir, work = Path(out_dir), Path(work)
    man = _lee_json(out_dir / "manifest.json")
    if not isinstance(man, list) or not man or not isinstance(man[0], dict):
        return {"ok": False, "error": "No encuentro el manifest.json de este vídeo (¿lo borraste del PC?)."}
    entrada = man[0]
    prompts = [str(p or "").strip() for p in (entrada.get("miniatura_prompts") or [])]
    if isinstance(indice, bool) or not isinstance(indice, int) or not 0 <= indice < len(prompts):
        return {"ok": False, "error": "Esa miniatura ya no está en la lista: recarga la página."}
    actual = prompts[indice]
    canal_id = str(entrada.get("tema") or "")
    info = ((cfg.get("videos") or {}).get("canales") or {}).get(canal_id) or {}
    nombre_canal = str(info.get("nombre") or entrada.get("tematica") or canal_id or "de YouTube")
    marca = {**dict(cfg.get_path("videos.marca") or {}), **dict(info.get("marca") or {})}
    words = _lee_json(work / "words.json", []) or []
    previos = [actual] + vistos_de(entrada, indice)
    if _caras is not None:
        caras_txt, caras_ok = _caras
    else:
        try:
            from . import miniaturas_ia as _mia
            from . import director as _d
            caras_txt = _d._caras_para_prompt(cfg)
            caras_ok = _mia.gestos(_mia.caras(cfg=cfg), _mia.parametros(cfg)["cara_defecto"])
        except Exception:  # noqa: BLE001 — sin fotos: igual vale «feliz»
            caras_txt, caras_ok = '"feliz"', []
    prompt = arma_prompt(entrada, words if isinstance(words, list) else [], str(info.get("contexto") or ""), indice, caras_txt)
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                return {"ok": False, "error": "No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso»."}
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from . import modelos as _m
            _modelos = _m.cadena_lite(cfg, client)
        from . import uso as _uso
        _uso.contexto(job=jid or out_dir.name)
        try:
            nuevo, modelo, _gasto = _util.call_gemini_cadena(
                client, types, _modelos, prompt, system=_SYSTEM.format(canal=nombre_canal),
                valida=lambda t: parsea(t, indice, marca, previos, caras_ok), reintento_s=20.0, pasadas=1,
                timeout_s=75.0, esquema=_esquema(), etiqueta="miniatura_concepto")
        finally:
            _uso.contexto(job=None)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de Gemini: se dice qué pasó, el vídeo no se toca
        log(f"«Otro concepto de miniatura» ({out_dir.name}, {LETRAS[indice]}): {exc}")
        return {"ok": False, "error": _error_claro(exc)}

    try:                       # las imágenes de antes de «cada letra sola» apuntan su prompt AHORA, que aún es el que dibujaron
        from . import miniaturas_ia as _mia_sello
        _mia_sello.sella_vigentes(out_dir.name, out_dir.parent, work.parent)
    except Exception as exc:  # noqa: BLE001 — sin sello, lo peor es que las otras letras salgan como «de la idea anterior»
        log(f"AVISO: no se pudieron sellar las miniaturas de {out_dir.name} ({exc}).")
    titulares = [str(t or "").strip() for t in (entrada.get("miniatura_titulares") or [])][:len(prompts)]
    titulares += [""] * (len(prompts) - len(titulares))
    caras_plan: list[str] | None = None
    prompts[indice], titulares[indice] = nuevo["prompt"], nuevo["titular"]
    anteriores = [{"letra": LETRAS[indice], "prompt": actual}] + [
        x for x in (entrada.get("miniatura_prompts_anteriores") or [])
        if not (isinstance(x, dict) and x.get("letra") == LETRAS[indice] and x.get("prompt") == actual)]
    anteriores = anteriores[:MAX_ANTERIORES]
    try:
        entrada["miniatura_prompts"], entrada["miniatura_titulares"] = prompts, titulares
        entrada["miniatura_prompts_anteriores"] = anteriores
        _escribe_json(out_dir / "manifest.json", man)
        rep = _lee_json(out_dir / "videos.json")
        if isinstance(rep, dict):
            rep["miniatura_prompts"], rep["miniatura_titulares"] = prompts, titulares
            _escribe_json(out_dir / "videos.json", rep)
        from . import director
        plan = director.cargar(work, slug)
        if plan is not None:              # «Reintentar» rehace el manifest desde el plan: que conserve el concepto nuevo
            plan["miniatura_prompts"], plan["miniatura_titulares"] = list(prompts), list(titulares)
            plan["miniatura_prompts_anteriores"] = anteriores
            caras_plan = [str(c or "") for c in (plan.get("miniatura_caras") or [])][:len(prompts)]
            caras_plan += [""] * (len(prompts) - len(caras_plan))
            if nuevo["cara"]:
                caras_plan[indice] = nuevo["cara"]
            plan["miniatura_caras"] = caras_plan
            director.guardar(work, slug, plan)
    except OSError as exc:
        return {"ok": False, "error": f"El concepto salió pero no se pudo guardar ({exc})."}
    log(f"«Otro concepto de miniatura» ({out_dir.name}, {LETRAS[indice]}, {nombre_modelo(modelo)}): {nuevo['idea'] or nuevo['titular']}")
    return {"ok": True, "letra": LETRAS[indice], "indice": indice, "prompts": prompts, "titulares": titulares,
            "titular": nuevo["titular"], "idea": nuevo["idea"], "cara": nuevo["cara"], "anteriores": anteriores,
            "modelo": modelo, "nombre": nombre_modelo(modelo)}


def prueba() -> list[dict]:
    """Sin red ni Gemini (uno falso): el prompt lleva lo del vídeo montado, el formato de ESA letra, los otros dos conceptos
    (sin lo que añade el código) y los anteriores; lo que devuelve la IA se limpia (sin texto, con las reglas fijas del
    código) y se rechaza lo que repite; SOLO cambia esa letra y el sustituido pasa a «anteriores» en el manifest, videos.json
    y el plan; un fallo no toca nada."""
    import shutil
    import tempfile
    from . import config, director
    from . import miniaturas_ia as mia
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": detalle})

    marca = {"naranja": "#FE330A", "crema": "#FFFBF2", "beige": "#F1E9DA", "dorado": "#FFC93C", "tinta": "#14100F"}
    pa = director._asegura_reglas_miniatura("A giant glowing stopwatch jumping forward on a timeline, surrounded by floating "
                                            "graph icons. Background of dark ink with orange speed lines.", marca, 0)
    pb = director._asegura_reglas_miniatura("A flatlined chart inside a calendar on the left and a rising arrow on the right.",
                                            marca, 1)
    pc = director._asegura_reglas_miniatura("A huge hourglass filled with gold stars on a flat dark background.", marca, 2)
    e = _escena(pa)
    r("«la escena» de un prompt es SIN lo que añade el código (apertura, zona de la cara, «sin texto», paleta, y el fondo de color de la B)",
      "stopwatch" in e and "YouTube thumbnail" not in e and "palette" not in e and "face cutout" not in e
      and "No text of any kind" not in e and "whole background" not in _escena(pb) and "whole background" in pb and "calendar" in _escena(pb), (e, _escena(pb)))
    r("dos conceptos con las mismas palabras se parecen casi del todo; dos distintos, casi nada",
      parecido(pa, pa) == 1.0 and parecido(pa, pc) < 0.2, (parecido(pa, pa), parecido(pa, pc)))

    words = [{"word": "Hola", "start": 0.5, "end": 0.8}, {"word": "silencioCORTADO", "start": 12.0, "end": 12.5},
             {"word": "Search", "start": 21.0, "end": 21.4}, {"word": "Console", "start": 21.4, "end": 21.9}]
    entrada = {"archivo": "pro.mp4", "tema": "ejemplo", "titulo": "Título de ahora", "gancho": "Un gancho",
               "capitulos": "0:00 Intro\n1:00 Lo nuevo", "segments": [[0.0, 10.0], [20.0, 30.0]],
               "miniatura_prompts": [pa, pb, pc], "miniatura_titulares": ["¿CUÁNTO TARDA?", "LA VERDAD", "TIEMPO REAL"]}
    p = arma_prompt(entrada, words, "SEO en español", 1, '"feliz", "sorpresa"')
    r("el prompt lleva lo que se oye en el vídeo MONTADO, el formato de la B, los OTROS dos conceptos y los titulares ya usados",
      "Hola Search Console" in p and "silencioCORTADO" not in p and "CARA EN EL CENTRO" in p and "Miniatura A: A giant" in p
      and "Miniatura C: A huge hourglass" in p and "Miniatura B:" not in p and "«¿CUÁNTO TARDA?» · «TIEMPO REAL»" in p
      and "flatlined chart" in p and '"feliz", "sorpresa"' in p, p[-1700:])
    bueno = {"miniatura_prompt": "On the left a sad cartoon website page with a broken chain link, on the right a happy glowing "
                                 "website page with a golden trophy, bold ink outlines and Ben-Day dots. Bold text banner "
                                 "reading 'EL ERROR' on the top left. A Google Search Console logo floats between them.",
             "miniatura_titular": "«¿Y LA INDEXACIÓN?»", "miniatura_cara": "sorpresa", "idea": "Una página triste frente a una feliz"}
    n = parsea(json.dumps(bueno), 1, marca, [pb], caras_ok=["feliz", "sorpresa"])
    r("lo que devuelve la IA se limpia: sin rótulo ni «reading 'EL ERROR'», el logo pasa a pictograma, y lleva las reglas "
      "FIJAS del código de la B (zona de la cara en el centro, paleta, sin texto)",
      "banner" not in n["prompt"].split("YouTube")[0] and "EL ERROR" not in n["prompt"] and "Google" not in n["prompt"]
      and "pictogram" in n["prompt"] and n["prompt"].startswith("YouTube thumbnail")
      and director._FORMATOS_MINIATURA[1][1] in n["prompt"] and "Use ONLY this exact color palette" in n["prompt"]
      and "whole background is a bold, saturated colour field" in n["prompt"]
      and mia.SIN_TEXTO in n["prompt"] and n["titular"] == "¿Y LA INDEXACIÓN?" and n["cara"] == "sorpresa", n)
    r("un gesto que el autor no tiene se descarta (se queda la cara que había)",
      parsea(json.dumps({**bueno, "miniatura_cara": "enfadadísimo"}), 1, marca, [], caras_ok=["feliz"])["cara"] == "")
    for caso, malo in (("un concepto casi igual a uno ya visto cuenta como fallo (prueba el siguiente modelo)",
                        {"miniatura_prompt": pb.replace("flatlined", "flat")}),
                       ("un concepto vacío o muy corto cuenta como fallo", {"miniatura_prompt": "a chart"})):
        try:
            parsea(json.dumps(malo), 1, marca, [pb])
            r(caso, False)
        except ValueError:
            r(caso, True)

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-conceptos-"))
    real = _util.call_gemini_cadena
    try:
        out_dir, work = tmp / "videos" / "x--pro-v2", tmp / "work" / "x--pro"
        out_dir.mkdir(parents=True)
        work.mkdir(parents=True)
        (out_dir / "manifest.json").write_text(json.dumps([entrada], ensure_ascii=False), encoding="utf-8")
        (out_dir / "videos.json").write_text(json.dumps({"miniatura_prompts": [pa, pb, pc]}), encoding="utf-8")
        (work / "words.json").write_text(json.dumps(words), encoding="utf-8")
        director.guardar(work, "pro-v2", {"miniatura_prompts": [pa, pb, pc], "miniatura_caras": ["feliz", "feliz", "feliz"],
                                          "miniatura_titulares": ["¿CUÁNTO TARDA?", "LA VERDAD", "TIEMPO REAL"], "recortes": []})
        cfg = config.load()
        visto: dict = {}
        otro = {"miniatura_prompt": "A huge cartoon robot detective with a magnifying glass inspecting a long crooked sitemap "
                                    "scroll, orange hero against a calm ink background, Ben-Day dots.",
                "miniatura_titular": "INDEXA YA", "miniatura_cara": "pensando", "idea": "Un robot detective"}
        respuestas = [json.dumps(bueno), json.dumps({**bueno, "miniatura_prompt": pb}), json.dumps(otro)]

        def falso(client, types, modelos, prompt, system=None, valida=None, etiqueta="", **kw):
            visto.update(prompt=prompt, system=system, modelos=modelos, etiqueta=etiqueta)
            while respuestas:                                     # (como la cadena real: lo que no vale, prueba el siguiente)
                crudo = respuestas.pop(0)
                try:
                    return valida(crudo), modelos[0], {"entrada": 100, "salida": 20}
                except ValueError:
                    visto["rechazadas"] = visto.get("rechazadas", 0) + 1
            raise RuntimeError("ningún modelo respondió")

        _util.call_gemini_cadena = falso
        caras = ('"feliz", "sorpresa", "pensando"', ["feliz", "sorpresa", "pensando"])
        # unas miniaturas hechas ANTES de «cada letra por sí sola» (solo la firma de las tres juntas): al cambiar la B, la A y la
        # C no deben parecer «de la idea anterior»
        (out_dir / "miniaturas_ia").mkdir()
        mia._guarda(out_dir.name, {"firma": mia._firma([pa, pb, pc]), "estado": "listo",
                                   "items": {l: {"ok": True, "n": 0, "hecha": 1.0} for l in "ABC"}}, tmp / "videos")
        r1 = renueva(out_dir, work, "pro-v2", cfg, 1, _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"],
                     _caras=caras)
        r("la B cambió: su imagen (hecha antes) queda «de la idea anterior» y la A y la C siguen al día",
          mia.vigentes(out_dir.name, tmp / "videos", tmp / "work") == {"A": True, "B": False, "C": True},
          mia.vigentes(out_dir.name, tmp / "videos", tmp / "work"))
        man = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))[0]
        plan = director.cargar(work, "pro-v2")
        rep = json.loads((out_dir / "videos.json").read_text(encoding="utf-8"))
        r("↻ en la B: SOLO cambia esa (su prompt y su titular) y la A y la C quedan IGUAL — en el manifest, en videos.json y "
          "en el plan de ESA versión",
          r1.get("ok") and man["miniatura_prompts"][0] == pa and man["miniatura_prompts"][2] == pc
          and "sad cartoon website page" in man["miniatura_prompts"][1] and man["miniatura_titulares"] ==
          ["¿CUÁNTO TARDA?", "¿Y LA INDEXACIÓN?", "TIEMPO REAL"] and rep["miniatura_prompts"] == man["miniatura_prompts"]
          and plan["miniatura_prompts"] == man["miniatura_prompts"] and plan["miniatura_titulares"] == man["miniatura_titulares"]
          and plan["recortes"] == [], (r1, man["miniatura_titulares"]))
        r("la cara de esa letra cambia en el plan (y solo la suya)", plan["miniatura_caras"] == ["feliz", "sorpresa", "feliz"],
          plan["miniatura_caras"])
        r("el concepto sustituido pasa a «anteriores» con su letra, y nada más se pierde",
          man["miniatura_prompts_anteriores"] == [{"letra": "B", "prompt": pb}] and plan["miniatura_prompts_anteriores"] ==
          man["miniatura_prompts_anteriores"], man.get("miniatura_prompts_anteriores"))
        r("va a Flash-Lite y se apunta en «Uso» como «miniatura_concepto»",
          visto.get("modelos") == ["gemini-3.5-flash-lite"] and visto.get("etiqueta") == "miniatura_concepto", visto.get("etiqueta"))
        r2 = renueva(out_dir, work, "pro-v2", cfg, 1, _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"],
                     _caras=caras)
        man = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))[0]
        r("otro ↻ en la B: la IA ve los conceptos que esa letra ya tuvo (para no repetirlos), uno que repite el de antes se "
          "rechaza y se prueba otro, y los sustituidos se acumulan (el más reciente primero)",
          r2.get("ok") and visto.get("rechazadas") == 1 and "robot detective" in man["miniatura_prompts"][1]
          and "sad cartoon website page" in visto["prompt"] and "flatlined chart" in visto["prompt"]
          and [x["letra"] for x in man["miniatura_prompts_anteriores"]] == ["B", "B"]
          and "sad cartoon website page" in man["miniatura_prompts_anteriores"][0]["prompt"]
          and man["miniatura_prompts_anteriores"][1]["prompt"] == pb, (r2, visto.get("rechazadas")))
        r("un número de miniatura que no existe: lo dice y no toca nada",
          not renueva(out_dir, work, "pro-v2", cfg, 7, _gemini=(object(), object()), _modelos=["x"]).get("ok"))

        def saturado(*a, **kw):
            raise RuntimeError("503 UNAVAILABLE: high demand")

        _util.call_gemini_cadena = saturado
        antes = (out_dir / "manifest.json").read_text(encoding="utf-8")
        r3 = renueva(out_dir, work, "pro-v2", cfg, 2, _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"],
                     _caras=caras)
        r("Gemini saturado: dice qué pasa y qué hacer, y no toca nada",
          not r3.get("ok") and "saturado" in r3.get("error", "") and (out_dir / "manifest.json").read_text(encoding="utf-8") == antes, r3)
        r("sin manifest: lo dice (no revienta)", not renueva(tmp / "no-existe", work, "pro", cfg, 0).get("ok"))
    finally:
        _util.call_gemini_cadena = real
        shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    import sys
    filas_ = prueba()
    for f_ in filas_:
        print(("OK   " if f_["ok"] else "FALLA") + " " + f_["caso"] + ("" if f_["ok"] else f"  -> {f_['detalle']}"))
    sys.exit(1 if any(not f_["ok"] for f_ in filas_) else 0)
