"""📝 ARTÍCULO PARA TU WEB a partir de un vídeo ya editado (el autor, 2026-09-26: «cada video convertirlo en un borrador de
artículo para tu web nueva… una estructura optimizada orgánicamente… bullet points, tablas si es necesario… imágenes que
sean screenshots de ese video hablando sobre esas partes… que sugiera el title y la metadescripción… que no consuma tanto»).

- UNA petición a Flash-Lite (500 al día): no toca las 20 de Flash que editan los vídeos. Ve la transcripción del vídeo YA
  MONTADO con su reloj (marcado 🖥 donde se ve tu pantalla), sus capítulos, el título y las etiquetas.
- El artículo llega por BLOQUES (párrafo, lista, tabla, subtítulo, imagen): el Markdown y el HTML salen del mismo sitio,
  sin librerías nuevas y sin que se cuele HTML raro.
- Las CAPTURAS no gastan IA: la IA dice en qué segundo del vídeo hay algo que enseñar (una línea 🖥) y el programa saca ese
  fotograma de pro.mp4 con ffmpeg, en tu PC (apartándose de las tarjetas y textos que haya encima).
- Enlaces REALES solo: los artículos del catálogo que la IA cite por código ([[A3|texto]]) y el servicio de la llamada a la
  acción (del config). La IA nunca escribe una URL.
- Title (≤ 60), meta descripción (≤ 155), slug y palabra clave: sugeridos, con su número de caracteres.
Se guarda en `videos/<vídeo>/articulo/`: articulo.json, articulo.md, articulo.html (vista previa) y las capturas.
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

from .util import log

VERSION = 2
FIDELIDAD_MIN = 0.15            # (4 palabras seguidas del artículo que están en lo que dijo: por debajo, la IA lo reescribió con otras palabras y se vuelve a pedir una vez)
MAX_TITLE, MAX_META = 60, 155
_MODOS_PANTALLA = ("pantalla", "pantalla_zoom", "ambos")
_CUBRE = {"escena": 8.0}                  # lo que tapa el cuadro tras un evento visual (s); el resto, 4 s
_SERVICIOS = {"consultor_seo": "¿Quieres que te ayude con el SEO de tu web?", "reserva_consultoria": "Reserva una consultoría",
              "auditoria_gratis": "Pide tu auditoría SEO gratis", "curso_seo": "Aprende SEO paso a paso con mi curso",
              "paginas_web": "¿Necesitas una página web?", "extension_seo": "Prueba mi extensión de SEO"}
_ENLACE_INT = re.compile(r"\[\[\s*(A\d+)\s*\|\s*([^\]]+?)\s*\]\]")
_NEGRITA = re.compile(r"\*\*(.+?)\*\*")


# ------------------------------------------------------------------ el vídeo
def _mmss(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _tramos_pantalla(eventos: list[dict], dur: float) -> list[tuple[float, float]]:
    """Del «Ritmo del vídeo» (eventos «camara» en tiempo FINAL), los tramos en que se ve tu pantalla."""
    cambios = sorted((float(e["t"]), str(e.get("detalle") or "")) for e in eventos or [] if e.get("tipo") == "camara")
    out, modo, desde = [], "cara", 0.0
    for t, nuevo in cambios + [(dur, "")]:
        if modo in _MODOS_PANTALLA and t > desde:
            out.append((desde, t))
        modo, desde = nuevo, t
    return out


def _tapado(eventos: list[dict]) -> list[tuple[float, float]]:
    """Ventanas en que algo va ENCIMA del cuadro (tarjetas, palabras clave, ilustraciones, escenas): la captura las evita."""
    out = []
    for e in eventos or []:
        tipo = str(e.get("tipo") or "")
        if tipo == "camara":
            continue
        t = float(e.get("t") or 0)
        out.append((t, t + _CUBRE.get(tipo.split(":")[0], 4.0)))
    return out


def transcripcion_final(words: list[dict], segs: list, pantalla: list[tuple[float, float]]) -> list[dict]:
    """Las frases del vídeo YA montado, con su segundo en el vídeo final y si en ese momento se ve la pantalla."""
    from .pipeline import _tiempo_final
    segs = [(float(a), float(b)) for a, b in segs or []]
    frases, actual, t0 = [], [], None
    for w in words or []:
        tf = _tiempo_final(float(w.get("start", 0)), segs)
        if tf is None:
            continue
        palabra = str(w.get("word") or "").strip()
        if not palabra:
            continue
        if t0 is None:
            t0 = tf
        actual.append(palabra)
        if palabra[-1:] in ".?!" or len(actual) >= 35:
            frases.append((t0, " ".join(actual)))
            actual, t0 = [], None
    if actual:
        frases.append((t0 or 0.0, " ".join(actual)))
    return [{"t": round(t, 1), "texto": x, "pantalla": any(a <= t < b for a, b in pantalla)} for t, x in frases]


def datos_del_video(jid: str, dir_outbox=None, dir_work=None) -> dict:
    """Todo lo que hace falta de un vídeo terminado (manifest, transcripción, tramos y reloj). Lanza si falta algo."""
    from . import config
    P = config.paths()
    out_dir = Path(dir_outbox or P["outbox"]) / jid
    man = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    e = man[0] if isinstance(man, list) else man
    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    words = json.loads((Path(dir_work or P["work"]) / f"{base}--pro" / "words.json").read_text(encoding="utf-8"))
    video = out_dir / str(e.get("archivo") or "pro.mp4")
    if not video.is_file():
        raise FileNotFoundError(f"no encuentro el vídeo ({video.name})")
    dur = float(e.get("duracion") or 0)
    eventos = e.get("eventos_visuales") or []
    pantalla = _tramos_pantalla(eventos, dur)
    return {"jid": jid, "base": base, "out_dir": out_dir, "video": video, "titulo": str(e.get("titulo") or ""),
            "tags": list(e.get("tags") or []), "capitulos": str(e.get("capitulos") or ""), "duracion": dur,
            "canal": str(e.get("tema") or ""), "eventos": eventos, "pantalla": pantalla,
            "frases": transcripcion_final(words, e.get("segments") or [], pantalla)}


# ------------------------------------------------------------------ la petición
_SYSTEM = """Eres redactor SEO del sitio de «{canal}»: conviertes sus vídeos en artículos claros, útiles y bien
estructurados para Google y para que las IAs los citen. Respondes SOLO con JSON válido, sin markdown."""


def arma_prompt(d: dict, arts: list[dict], servicios: list[tuple[str, str, str]], canal: str, web: str, objetivo: str,
                max_img: int = 6) -> str:
    palabras = sum(len(f["texto"].split()) for f in d["frases"])
    objetivo_pal = max(900, min(3200, int(palabras * 0.8)))
    L = [f"Convierte este vídeo de YouTube de «{canal}» en un ARTÍCULO para su web ({web}), optimizado para Google y para "
         "que lo citen las IAs. Es un BORRADOR que él revisará antes de publicar.", "",
         f"Objetivo del negocio: {objetivo} El artículo lleva al lector, con naturalidad, hacia su servicio.", "",
         "LA VOZ DEL AUTOR ES LO MÁS IMPORTANTE (si algo de lo demás choca con esto, gana esto):",
         "- Es SU artículo, escrito con SUS palabras. Tu trabajo es ORDENAR y ESTRUCTURAR lo que dijo (títulos, listas, tablas, negritas), NO reescribirlo. "
         "Cada párrafo se arma con las frases que él dijo, casi literales y en el orden en que las dijo; como mínimo el 60 % de las palabras de cada párrafo "
         "salen directamente de la transcripción.",
         "- Solo se arregla: la ortografía, la puntuación, las muletillas y los titubeos («o sea», «pues», «este», «eh», repeticiones, frases a medias) y se une lo "
         "que dijo partido en dos. Se mantiene en PRIMERA persona, con sus expresiones y su forma de hablar (si dice «chévere» o «la clave es esta», así queda), sus "
         "ejemplos, analogías, anécdotas, experiencias y opiniones, con la misma fuerza con la que las dijo (no las suavices ni las vuelvas neutras).",
         "- PROHIBIDO: cambiar sus frases por frases genéricas de manual de SEO, explicar con otras palabras lo que él ya explicó, añadir consejos, pasos o datos que "
         "no dijo, o convertirlo en tercera persona («el experto recomienda»). Si algo no lo dijo, no va. La parte de expertise (su criterio, su experiencia, su "
         "forma de decidir) se conserva a toda costa.",
         "- Incluye de 3 a 6 bloques «cita» repartidos por el artículo: frases SUYAS contundentes (una idea, una opinión, un consejo) copiadas LITERALMENTE de la "
         "transcripción, solo con la ortografía corregida; van en un bloque aparte, sin cambiarles ni una palabra.",
         "- La introducción, las preguntas frecuentes y la conclusión también salen de lo que él dijo, con sus palabras.", "",
         "REGLAS:",
         "- Solo lo que se dice en el vídeo: no inventes datos, cifras, herramientas ni experiencias. Una cifra que él da "
         "como estimación va como aproximada.",
         "- La transcripción es automática y a veces escribe mal un nombre (p. ej. «Pulso Webs» por «Pulso Web»): "
         "usa SIEMPRE cómo sale en el título, los capítulos o las etiquetas.",
         "- Estructura: un H1 con la palabra clave principal; una introducción que responde de entrada (2-3 párrafos "
         "cortos); secciones H2 en el orden de los capítulos del vídeo; H3 donde ayude; listas con viñetas (o numeradas "
         "para pasos); una TABLA cuando compares cosas (métricas, herramientas, antes y después), solo si aporta; "
         "párrafos de 2-4 frases; lo clave en **negrita**.",
         f"- Imágenes: en cada sección donde el vídeo ENSEÑA algo en pantalla (líneas con 🖥), un bloque «imagen» con el "
         f"segundo (t) de UNA de esas líneas, un texto alternativo descriptivo (alt) y un pie corto. Como mucho 1 por sección "
         f"y {max_img} en total.",
         "- Enlaces internos: si un artículo de su web encaja de verdad, enlázalo DENTRO de un párrafo así: [[A3|texto del "
         "enlace]] (con su código). Como mucho 3. Nunca escribas una URL.",
         "- Preguntas frecuentes: 3-5 que se haría un lector, respondidas en 2-3 frases con lo que dice el vídeo.",
         "- Una conclusión corta y, en «cta», el código [S] del servicio al que llevar al lector.",
         f"- «title_seo»: máx. {MAX_TITLE} caracteres, con la palabra clave al principio. «meta_descripcion»: 140-"
         f"{MAX_META} caracteres, con la palabra clave y un beneficio claro. «slug»: corto, minúsculas, guiones, sin tildes. "
         "«keyword»: la palabra clave principal.",
         f"- Unas {objetivo_pal} palabras en total (que no sobren por rellenar: si dijo menos, es más corto). En español, tuteando, con SU tono y SU forma de hablar.", "",
         "BLOQUES (cada sección es una lista de bloques):",
         '{"tipo": "parrafo", "texto": "..."} · {"tipo": "lista", "items": ["...", "..."], "numerada": false} · '
         '{"tipo": "tabla", "cabecera": "Columna 1 | Columna 2", "filas": ["a | b", "c | d"]} · {"tipo": "h3", "texto": '
         '"..."} · {"tipo": "imagen", "t": 83.5, "alt": "...", "pie": "..."} · {"tipo": "cita", "texto": "frase literal suya"}', "",
         f"DATOS DEL VÍDEO: «{d['titulo']}» · {_mmss(d['duracion'])} · etiquetas: {', '.join(d['tags']) or '—'}",
         "Capítulos:", d["capitulos"] or "(sin capítulos)", "",
         "[A] Artículos de su web:"]
    L += [f"A{n}: {a['titulo']}" for n, a in enumerate(arts[:60], 1)] or ["(ninguno)"]
    L += ["", "[S] Sus servicios:"]
    L += [f"S{n}: {nombre}" for n, (_k, nombre, _u) in enumerate(servicios, 1)] or ["(ninguno)"]
    L += ["", "TRANSCRIPCIÓN del vídeo ya editado ([m:ss] = reloj del vídeo; 🖥 = en ese momento se ve su pantalla):"]
    L += [f"[{_mmss(f['t'])}]{' 🖥' if f['pantalla'] else ''} {f['texto']}" for f in d["frases"]]
    L += ["", 'Responde SOLO con JSON: {"title_seo", "meta_descripcion", "slug", "keyword", "h1", "intro": [bloques], '
              '"secciones": [{"h2", "bloques": [...]}], "faq": [{"pregunta", "respuesta"}], "conclusion", "cta"}']
    return "\n".join(L)


_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from pydantic import BaseModel

            class Bloque(BaseModel):
                tipo: str
                texto: str = ""
                items: list[str] = []
                numerada: bool = False
                cabecera: str = ""
                filas: list[str] = []
                t: float = 0.0
                alt: str = ""
                pie: str = ""

            class Seccion(BaseModel):
                h2: str
                bloques: list[Bloque]

            class Faq(BaseModel):
                pregunta: str
                respuesta: str

            class Articulo(BaseModel):
                title_seo: str
                meta_descripcion: str
                slug: str
                keyword: str
                h1: str
                intro: list[Bloque]
                secciones: list[Seccion]
                faq: list[Faq]
                conclusion: str
                cta: str

            _ESQUEMA = Articulo
        except Exception:  # noqa: BLE001
            _ESQUEMA = False
    return _ESQUEMA or None


def _json_de(texto: str):
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1] if i >= 0 and j > i else t)


def _txt(v, tope: int = 4000) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:tope]


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _sin_tildes(str(s).lower()))


def _esta_dicho(frase: str, ref: list[str]) -> bool:
    """¿La frase está (casi literal) en lo que dijo? Sus palabras, en orden, dentro de una ventana de la transcripción (se admiten muletillas de por medio)."""
    t = _tokens(frase)
    if len(t) < 4:
        return False
    for i, w in enumerate(ref):
        if w != t[0]:
            continue
        k, j = 0, i
        while j < min(len(ref), i + len(t) + 6) and k < len(t):
            if ref[j] == t[k]:
                k += 1
            j += 1
        if k / len(t) >= 0.85:
            return True
    return False


def fidelidad(a: dict, ref: list[str]) -> float:
    """Qué parte del artículo es SU voz: la fracción de secuencias de 4 palabras de lo que escribió la IA (párrafos, listas, intro, respuestas) que están en lo que dijo (0-1).
    Un artículo reescrito con otras palabras sale ~0,04; uno que conserva sus frases, 0,3 o más."""
    textos: list[str] = []
    for b in list(a.get("intro") or []) + [b for s in a.get("secciones") or [] for b in s["bloques"]]:
        if b["tipo"] in ("parrafo", "cita"):
            textos.append(b["texto"])
        elif b["tipo"] == "lista":
            textos += b["items"]
    textos += [f["respuesta"] for f in a.get("faq") or []] + ([a["conclusion"]] if a.get("conclusion") else [])
    mios = _tokens(" ".join(textos))
    if len(mios) < 8:
        return 1.0
    suyo = {tuple(ref[i:i + 4]) for i in range(len(ref) - 3)}
    seqs = [tuple(mios[i:i + 4]) for i in range(len(mios) - 3)]
    return round(sum(1 for q in seqs if q in suyo) / max(1, len(seqs)), 3)


def _bloques(lista, dur: float, imgs: list, ref: list[str] | None = None) -> list[dict]:
    out = []
    for b in lista if isinstance(lista, list) else []:
        if not isinstance(b, dict):
            continue
        tipo = str(b.get("tipo") or "").lower()
        if tipo == "cita":                                    # una cita SUYA solo si de verdad la dijo; si no, queda como párrafo normal (no se pierde el contenido)
            txt = _txt(b.get("texto"), 500)
            if txt:
                out.append({"tipo": "cita", "texto": txt} if (ref is None or _esta_dicho(txt, ref)) else {"tipo": "parrafo", "texto": txt})
        elif tipo == "parrafo" and _txt(b.get("texto")):
            out.append({"tipo": "parrafo", "texto": _txt(b["texto"])})
        elif tipo == "h3" and _txt(b.get("texto"), 150):
            out.append({"tipo": "h3", "texto": _txt(b["texto"], 150)})
        elif tipo == "lista":
            items = [_txt(x, 600) for x in b.get("items") or [] if _txt(x, 600)]
            if items:
                out.append({"tipo": "lista", "items": items[:15], "numerada": bool(b.get("numerada"))})
        elif tipo == "tabla":
            cab = [c.strip() for c in _txt(b.get("cabecera"), 600).split("|") if c.strip()]
            filas = [[c.strip() for c in _txt(f, 800).split("|")] for f in b.get("filas") or [] if _txt(f, 800)]
            if len(cab) >= 2 and filas:
                out.append({"tipo": "tabla", "cabecera": cab, "filas": [(f + [""] * len(cab))[:len(cab)] for f in filas][:20]})
        elif tipo == "imagen":
            try:
                t = float(b.get("t"))
            except (TypeError, ValueError):
                continue
            if 0 < t < dur and not any(abs(t - x) < 5 for x in imgs):   # dentro del vídeo y no dos capturas casi iguales
                imgs.append(t)
                out.append({"tipo": "imagen", "t": round(t, 2), "alt": _txt(b.get("alt"), 160), "pie": _txt(b.get("pie"), 200)})
    return out


def parsea(texto: str, d: dict, max_img: int = 6) -> dict:
    """El JSON de la IA -> el artículo limpio (bloques válidos, como mucho 1 imagen por sección y `max_img` en total, la
    llamada a la acción solo si es un servicio de la lista). Lanza si queda algo inservible (se reintenta)."""
    x = _json_de(texto)
    if not isinstance(x, dict):
        raise ValueError("la respuesta no es un artículo")
    imgs: list[float] = []
    secciones = []
    ref = d.get("_ref")
    if ref is None:
        ref = d["_ref"] = _tokens(" ".join(f["texto"] for f in d.get("frases") or []))
    for s in x.get("secciones") or []:
        if not isinstance(s, dict) or not _txt(s.get("h2"), 150):
            continue
        bl = _bloques(s.get("bloques"), d["duracion"], imgs, ref)
        vistas = 0
        limpios = []
        for b in bl:                                   # 1 imagen por sección, y el total
            if b["tipo"] == "imagen":
                vistas += 1
                if vistas > 1 or sum(1 for s2 in secciones for b2 in s2["bloques"] if b2["tipo"] == "imagen") >= max_img:
                    continue
            limpios.append(b)
        if limpios:
            secciones.append({"h2": _txt(s["h2"], 150), "bloques": limpios})
    faq = [{"pregunta": _txt(f.get("pregunta"), 200), "respuesta": _txt(f.get("respuesta"), 900)}
           for f in x.get("faq") or [] if isinstance(f, dict) and _txt(f.get("pregunta")) and _txt(f.get("respuesta"))][:6]
    h1 = _txt(x.get("h1"), 150)
    if not h1 or len(secciones) < 2:
        raise ValueError("el artículo salió sin título o con menos de 2 secciones")
    slug = re.sub(r"[^a-z0-9]+", "-", _sin_tildes(_txt(x.get("slug") or h1, 120).lower())).strip("-")[:80]
    return {"title_seo": _txt(x.get("title_seo"), 120), "meta_descripcion": _txt(x.get("meta_descripcion"), 300),
            "slug": slug, "keyword": _txt(x.get("keyword"), 80), "h1": h1,
            "intro": [b for b in _bloques(x.get("intro"), d["duracion"], [], ref) if b["tipo"] not in ("imagen", "cita")],
            "secciones": secciones, "faq": faq, "conclusion": _txt(x.get("conclusion"), 1500),
            "cta": str(x.get("cta") or "").strip().upper()}


def _sin_tildes(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


# ------------------------------------------------------------------ capturas (ffmpeg, en tu PC: sin IA)
def momento_captura(t: float, d: dict) -> float:
    """El segundo de la captura: si cae con algo ENCIMA (tarjeta, palabra clave, ilustración, escena), justo después; y
    si la pantalla se ve cerca (±8 s) pero no ahí, en la pantalla."""
    pant, tap = d["pantalla"], _tapado(d["eventos"])
    if pant and not any(a <= t < b for a, b in pant):
        cerca = [(min(abs(t - a), abs(t - b)), a, b) for a, b in pant if min(abs(t - a), abs(t - b)) <= 8]
        if cerca:
            _dist, a, b = min(cerca)
            t = min(max(t, a + 0.5), b - 0.5)
    for _ in range(4):
        dentro = [(a, b) for a, b in tap if a <= t < b]
        if not dentro:
            break
        t = max(b for _a, b in dentro) + 0.3
    return round(min(max(0.5, t), max(0.5, d["duracion"] - 0.5)), 2)


def _captura(video: Path, t: float, destino: Path) -> bool:
    from .util import run
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", "scale=1280:-2", "-q:v", "3", str(destino)])
        return destino.is_file() and destino.stat().st_size > 1000
    except Exception as exc:                                   # noqa: BLE001
        log(f"AVISO: no se pudo sacar la captura del segundo {t:.1f} ({exc}).")
        return False


# ------------------------------------------------------------------ Markdown y HTML (del mismo sitio)
def _inline_md(s: str, arts: list[dict]) -> str:
    def enlace(m):
        n = int(m.group(1)[1:])
        return f"[{m.group(2)}]({arts[n - 1]['url']})" if 0 < n <= len(arts) else m.group(2)
    # sin HTML crudo de la IA: el Markdown lo pintaría tal cual al publicarlo
    return _ENLACE_INT.sub(enlace, str(s).replace("<", "&lt;").replace(">", "&gt;"))


def _inline_html(s: str, arts: list[dict]) -> str:
    partes, ultimo = [], 0
    for m in _ENLACE_INT.finditer(s):
        partes.append(_NEGRITA.sub(r"<strong>\1</strong>", html.escape(s[ultimo:m.start()])))
        n = int(m.group(1)[1:])
        texto = _NEGRITA.sub(r"<strong>\1</strong>", html.escape(m.group(2)))
        partes.append(f'<a href="{html.escape(arts[n - 1]["url"])}">{texto}</a>' if 0 < n <= len(arts) else texto)
        ultimo = m.end()
    partes.append(_NEGRITA.sub(r"<strong>\1</strong>", html.escape(s[ultimo:])))
    return "".join(partes)


def _md_bloques(bl: list[dict], arts: list[dict]) -> list[str]:
    L = []
    for b in bl:
        if b["tipo"] == "parrafo":
            L += [_inline_md(b["texto"], arts), ""]
        elif b["tipo"] == "cita":
            L += [f"> {_inline_md(b['texto'], arts)}", ""]
        elif b["tipo"] == "h3":
            L += [f"### {b['texto']}", ""]
        elif b["tipo"] == "lista":
            L += [f"{k}. {_inline_md(i, arts)}" if b["numerada"] else f"- {_inline_md(i, arts)}"
                  for k, i in enumerate(b["items"], 1)] + [""]
        elif b["tipo"] == "tabla":
            fila = lambda cs: "| " + " | ".join(c.replace("|", "/") for c in cs) + " |"     # noqa: E731
            L += [fila(b["cabecera"]), "|" + "|".join(" --- " for _ in b["cabecera"]) + "|"]
            L += [fila(f) for f in b["filas"]] + [""]
        elif b["tipo"] == "imagen" and b.get("archivo"):
            L += [f"![{b['alt']}]({b['archivo']})"] + ([f"*{b['pie']}*"] if b.get("pie") else []) + [""]
    return L


def _html_bloques(bl: list[dict], arts: list[dict]) -> list[str]:
    L = []
    for b in bl:
        if b["tipo"] == "parrafo":
            L.append(f"<p>{_inline_html(b['texto'], arts)}</p>")
        elif b["tipo"] == "cita":
            L.append(f"<blockquote><p>{_inline_html(b['texto'], arts)}</p></blockquote>")
        elif b["tipo"] == "h3":
            L.append(f"<h3>{html.escape(b['texto'])}</h3>")
        elif b["tipo"] == "lista":
            tag = "ol" if b["numerada"] else "ul"
            L.append(f"<{tag}>" + "".join(f"<li>{_inline_html(i, arts)}</li>" for i in b["items"]) + f"</{tag}>")
        elif b["tipo"] == "tabla":
            cab = "".join(f"<th>{html.escape(c)}</th>" for c in b["cabecera"])
            filas = "".join("<tr>" + "".join(f"<td>{_inline_html(c, arts)}</td>" for c in f) + "</tr>" for f in b["filas"])
            L.append(f"<table><thead><tr>{cab}</tr></thead><tbody>{filas}</tbody></table>")
        elif b["tipo"] == "imagen" and b.get("archivo"):
            pie = f"<figcaption>{html.escape(b['pie'])}</figcaption>" if b.get("pie") else ""
            L.append(f'<figure><img src="{html.escape(b["archivo"])}" alt="{html.escape(b["alt"])}" loading="lazy">{pie}</figure>')
    return L


def a_markdown(a: dict, arts: list[dict]) -> str:
    L = [f"# {a['h1']}", ""] + _md_bloques(a["intro"], arts)
    L += [a["video_html"], ""] if a.get("video_html") else ["<!-- Pega aquí el vídeo de YouTube cuando esté publicado -->", ""]
    for s in a["secciones"]:
        L += [f"## {s['h2']}", ""] + _md_bloques(s["bloques"], arts)
    if a["faq"]:
        L += ["## Preguntas frecuentes", ""]
        for f in a["faq"]:
            L += [f"### {f['pregunta']}", "", _inline_md(f["respuesta"], arts), ""]
    if a["conclusion"]:
        L += ["## Conclusión", "", _inline_md(a["conclusion"], arts), ""]
    if a.get("cta_url"):
        L += [f"**👉 [{a['cta_texto']}]({a['cta_url']})**", ""]
    return "\n".join(L).strip() + "\n"


def a_html(a: dict, arts: list[dict]) -> str:
    L = [f"<h1>{html.escape(a['h1'])}</h1>"] + _html_bloques(a["intro"], arts)
    L.append(a.get("video_html") or "<!-- Pega aquí el vídeo de YouTube cuando esté publicado -->")
    for s in a["secciones"]:
        L += [f"<h2>{html.escape(s['h2'])}</h2>"] + _html_bloques(s["bloques"], arts)
    if a["faq"]:
        L.append("<h2>Preguntas frecuentes</h2>")
        for f in a["faq"]:
            L += [f"<h3>{html.escape(f['pregunta'])}</h3>", f"<p>{_inline_html(f['respuesta'], arts)}</p>"]
    if a["conclusion"]:
        L += ["<h2>Conclusión</h2>", f"<p>{_inline_html(a['conclusion'], arts)}</p>"]
    if a.get("cta_url"):
        L.append(f'<p><strong>👉 <a href="{html.escape(a["cta_url"])}">{html.escape(a["cta_texto"])}</a></strong></p>')
    return "\n".join(L) + "\n"


def _pagina(a: dict) -> str:
    """Vista previa suelta (se abre desde el panel): el artículo con una hoja de estilo sencilla y su title/meta."""
    return ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(a['title_seo'])}</title><meta name=\"description\" content=\"{html.escape(a['meta_descripcion'])}\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><style>"
            "body{font:17px/1.65 Georgia,serif;max-width:760px;margin:40px auto;padding:0 18px;color:#1d1a17;background:#fffdf8}"
            "h1,h2,h3{font-family:system-ui,sans-serif;line-height:1.25}h2{margin-top:2em}img{max-width:100%;border-radius:8px}"
            "figure{margin:1.5em 0}figcaption{font-size:14px;color:#6b625a;margin-top:6px}table{border-collapse:collapse;"
            "width:100%;margin:1em 0;font-size:15px}th,td{border:1px solid #d9cdb7;padding:8px 10px;text-align:left}"
            "th{background:#f7efe0}iframe{width:100%;aspect-ratio:16/9;border:0;border-radius:8px}"
            ".seo{font:13px/1.5 system-ui,sans-serif;background:#f7efe0;padding:12px 14px;border-radius:8px;margin-bottom:28px}"
            "</style></head><body>"
            f"<div class=\"seo\"><b>Title:</b> {html.escape(a['title_seo'])} ({len(a['title_seo'])})<br><b>Meta:</b> "
            f"{html.escape(a['meta_descripcion'])} ({len(a['meta_descripcion'])})<br><b>Slug:</b> /{html.escape(a['slug'])}/ · "
            f"<b>Palabra clave:</b> {html.escape(a['keyword'])}</div>" + a["html"] + "</body></html>")


# ------------------------------------------------------------------ crear / leer
def ruta(jid: str, dir_outbox=None) -> Path:
    from . import config
    return Path(dir_outbox or config.paths()["outbox"]) / jid / "articulo"


def lee(jid: str, dir_outbox=None) -> dict | None:
    try:
        d = json.loads((ruta(jid, dir_outbox) / "articulo.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _llama_gemini(cfg, prompt: str, valida):
    from . import modelos as _m
    from . import util as _util
    from .director import _gemini as _abre
    g = _abre(cfg)
    if not g:
        raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso».")
    client, types = g[:2]
    res, modelo, _uso = _util.call_gemini_cadena(client, types, _m.cadena_lite(cfg, client), prompt,
                                                 system=_SYSTEM.format(canal="Mi canal"), valida=valida, reintento_s=60.0,
                                                 pasadas=1, timeout_s=180.0, esquema=_esquema(), etiqueta="articulo")
    return res, modelo


DESFASES_ARTICULO = (0.0, 1.5, -1.5, 3.0, -3.0, 5.0, -5.0, 8.0, -8.0)     # dónde buscar una captura de PANTALLA cerca del segundo que dijo la IA
ANCHO_CAPTURA = 1280


def _capturas_de_pantalla(a: dict, d: dict, carpeta: Path) -> list[dict]:
    """Las capturas del artículo con la lógica del carrusel (el autor, 11-oct-2026: «que sí salgan mis pantallazos, pero que no muestre mi cara»): solo momentos en que se ve tu PANTALLA
    (nunca el plano de tu cara), sin tarjetas encima, sin fotogramas vacíos y a ≥ 4 s de las otras, y con tu cámara RECORTADA cuando va pegada en una esquina. Si cerca de ese segundo
    no hay ninguna pantalla buena, esa imagen no sale. -> los «pasos» (uno por imagen) para revisar que encajen."""
    from . import carrusel as K
    pasos, ocupados = [], []
    n = 0
    for s in a["secciones"]:
        previo = next((b["texto"] for b in s["bloques"] if b["tipo"] == "parrafo"), "")
        for b in s["bloques"]:
            if b["tipo"] != "imagen":
                continue
            nombre = f"img-{n + 1:02d}.jpg"
            tc = K.busca_captura(d, b["t"], carpeta / nombre, ocupados, DESFASES_ARTICULO, ANCHO_CAPTURA)
            if tc is None:
                continue
            n += 1
            ocupados.append(tc)
            b.update(archivo=nombre, t_captura=tc)
            pasos.append({"id": f"i{n}", "titulo": s["h2"], "texto": " ".join(x for x in (b.get("alt"), b.get("pie"), previo[:240]) if x), "t": b["t"],
                          "captura": nombre, "t_captura": tc, "_bloque": b})
    return pasos


def _revisa_capturas(a: dict, pasos: list[dict], d: dict, carpeta: Path, cfg, _verifica, progreso) -> list[str]:
    """Una IA MIRA que cada captura enseñe lo que dice su apartado (la misma revisión del carrusel): las que no encajan se cambian por otra pantalla cercana o se quitan."""
    from . import carrusel_coherencia as coh
    if not pasos:
        return []
    c = {"pasos": [{k: v for k, v in p.items() if k != "_bloque"} for p in pasos], "jid": a.get("jid", "")}
    avisos = coh.verifica(c, d, carpeta, cfg, _verifica, progreso, subcarpeta="", tipo="artículo", ancho_max=ANCHO_CAPTURA)
    for p, r in zip(pasos, c["pasos"]):
        b = p["_bloque"]
        if not r.get("captura"):
            b.pop("archivo", None)                         # no encajaba y no hay otra mejor: esa imagen se va
            b.pop("t_captura", None)
        else:
            b["t_captura"] = r.get("t_captura") or b.get("t_captura")
    return avisos


def _renumera(a: dict, carpeta: Path) -> int:
    """img-01, img-02… seguidas (si se quitó alguna, sin huecos). -> cuántas imágenes quedan."""
    n = 0
    for s in a["secciones"]:
        for b in s["bloques"]:
            if b["tipo"] == "imagen" and b.get("archivo"):
                n += 1
                nuevo = f"img-{n:02d}.jpg"
                if b["archivo"] != nuevo:
                    (carpeta / b["archivo"]).replace(carpeta / nuevo)
                    b["archivo"] = nuevo
    return n


def crea(jid: str, cfg, dir_outbox=None, dir_work=None, _llama=None, progreso=None, _verifica=None) -> dict:
    """El artículo de un vídeo terminado: datos -> UNA petición a Flash-Lite -> capturas -> Markdown + HTML. Nunca lanza.
    -> {"ok": True, "articulo": {...}} o {"ok": False, "error": qué pasó y qué hacer}. `_llama`: pruebas (sin red)."""
    from . import enlaces as enl
    from .util import nombre_modelo
    try:
        d = datos_del_video(jid, dir_outbox, dir_work)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": f"No encuentro todo lo de este vídeo en el PC ({str(exc)[:160]})."}
    if len(d["frases"]) < 5:
        return {"ok": False, "error": "Este vídeo casi no tiene voz transcrita: no da para un artículo."}
    info = (cfg.get_path(f"videos.canales.{d['canal']}") or {}) if cfg is not None and d["canal"] else {}
    arts = enl.articulos_del_canal(info) if info else []
    enl_c = dict(info.get("enlaces") or {})
    servicios = [(k, _SERVICIOS[k], enl_c[k]) for k in _SERVICIOS if enl_c.get(k)]
    objetivo = str((cfg.get_path("ideas.objetivo") if cfg is not None else "") or
                   "Su prioridad nº 1 es conseguir clientes para sus servicios de SEO.")
    max_img = 6
    prompt = arma_prompt(d, arts, servicios, str(info.get("nombre") or d["canal"]), str(enl_c.get("web") or ""), objetivo,
                         max_img)
    if progreso:
        progreso("Escribiendo el artículo (Flash-Lite)…")
    try:
        from . import uso as _uso
        _uso.contexto(job=jid)
        try:
            llama = _llama or (lambda pr, va: _llama_gemini(cfg, pr, va))
            a, modelo = llama(prompt, lambda t: parsea(t, d, max_img))
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Artículo de {jid}: {exc}")
        from .ideas import _error_claro
        return {"ok": False, "error": _error_claro(exc).replace("las ideas", "el artículo")}

    ref = d.get("_ref") or _tokens(" ".join(f["texto"] for f in d["frases"]))
    fid = fidelidad(a, ref)
    if fid < FIDELIDAD_MIN and cfg is not None and cfg.get_path("articulo.reintenta_voz", True):
        if progreso:
            progreso("Cuidando que sea tu voz…")
        log(f"Artículo de {jid}: solo {fid:.0%} del texto sale de lo que dijiste; se vuelve a pedir conservando tus frases.")
        try:
            extra = (f"\n\nAVISO IMPORTANTE: tu versión anterior reescribió casi todo con otras palabras (solo el {fid:.0%} del texto estaba en lo que él dijo). "
                     "Hazlo otra vez COPIANDO sus frases de la transcripción (solo corrige ortografía y quita muletillas), en el orden en que las dijo; no expliques con tus palabras lo que él ya explicó.")
            from . import uso as _uso2
            _uso2.contexto(job=jid)
            try:
                a2, modelo2 = llama(prompt + extra, lambda t: parsea(t, d, max_img))
            finally:
                _uso2.contexto(job=None)
            fid2 = fidelidad(a2, ref)
            if fid2 > fid:
                a, modelo, fid = a2, modelo2, fid2
        except Exception as exc:                                # noqa: BLE001 — se queda la primera versión
            log(f"Artículo de {jid}: no se pudo repetir conservando tu voz ({str(exc)[:120]}).")

    carpeta = ruta(jid, dir_outbox)
    carpeta.mkdir(parents=True, exist_ok=True)
    for viejo in carpeta.glob("img-*.jpg"):                    # «↻ Rehacer»: sin capturas de la vez anterior
        viejo.unlink(missing_ok=True)
    if progreso:
        progreso("Sacando las capturas del vídeo…")
    pasos = _capturas_de_pantalla(a, d, carpeta)
    avisos_cap: list[str] = []
    if pasos and (_verifica is not None or (cfg is not None and cfg.get_path("articulo.verifica_coherencia", True))):
        avisos_cap = _revisa_capturas(a, pasos, d, carpeta, cfg, _verifica, progreso)
    for s in a["secciones"]:
        s["bloques"] = [b for b in s["bloques"] if b["tipo"] != "imagen" or b.get("archivo")]
    n = _renumera(a, carpeta)
    from . import historial
    vid = historial.video_id_de(d["base"])
    a["video_html"] = (f'<iframe src="https://www.youtube.com/embed/{vid}" title="{html.escape(d["titulo"])}" '
                       'allowfullscreen></iframe>') if vid else ""
    k = int(a["cta"][1:]) if re.fullmatch(r"S\d+", a["cta"] or "") else 0
    if 0 < k <= len(servicios):
        a["cta_texto"], a["cta_url"] = servicios[k - 1][1], servicios[k - 1][2]
    elif servicios:
        a["cta_texto"], a["cta_url"] = servicios[0][1], servicios[0][2]
    a["markdown"] = a_markdown(a, arts)
    a["html"] = a_html(a, arts)
    a.update(version=VERSION, jid=jid, ts=time.time(), modelo=modelo, nombre_modelo=nombre_modelo(modelo),
             imagenes=n, palabras=len(re.findall(r"\w+", a["markdown"])), fidelidad=fid,
             avisos=avisos_cap + ([f"Solo el {fid:.0%} del texto conserva tus frases (lo normal al ordenar bien es 25 % o más): léelo y devuélvele tu voz donde suene a manual."] if fid < FIDELIDAD_MIN else []) + [x for x in (f"El title tiene {len(a['title_seo'])} caracteres (mejor ≤ {MAX_TITLE})."
                                 if len(a["title_seo"]) > MAX_TITLE else "",
                                 f"La meta descripción tiene {len(a['meta_descripcion'])} caracteres (mejor ≤ {MAX_META})."
                                 if len(a["meta_descripcion"]) > MAX_META else "") if x])
    (carpeta / "articulo.md").write_text(a["markdown"], encoding="utf-8")
    (carpeta / "articulo.html").write_text(_pagina(a), encoding="utf-8")
    (carpeta / "articulo.json").write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Artículo de {jid}: {a['palabras']} palabras, {n} captura(s) ({nombre_modelo(modelo)}).")
    return {"ok": True, "articulo": a}


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    from .util import run
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    eventos = [{"t": 2.0, "tipo": "camara", "detalle": "pantalla"}, {"t": 3.0, "tipo": "tarjeta:dato", "detalle": "x"},
               {"t": 8.0, "tipo": "camara", "detalle": "cara"}]
    pant = _tramos_pantalla(eventos, 10.0)
    caso("los tramos con tu pantalla salen del «Ritmo del vídeo» (antes del primer cambio, tu cara)", pant == [(2.0, 8.0)], pant)
    words = [{"word": w, "start": s, "end": s + 0.3} for w, s in
             (("Hola.", 0.5), ("Esto", 5.2), ("se", 5.6), ("corta.", 5.9), ("Mira", 11.0), ("la", 11.4), ("pantalla.", 11.8))]
    fr = transcripcion_final(words, [(0.0, 3.0), (10.0, 20.0)], [(3.5, 8.0)])
    caso("la transcripción va en el reloj del vídeo YA montado (lo cortado no sale) y marca 🖥 donde se ve la pantalla",
         [(f["t"], f["texto"], f["pantalla"]) for f in fr] == [(0.5, "Hola.", False), (4.0, "Mira la pantalla.", True)], fr)
    d = {"duracion": 10.0, "pantalla": pant, "eventos": eventos, "frases": fr, "titulo": "T", "tags": [], "capitulos": ""}
    caso("la captura se aparta de una tarjeta que tapa el cuadro, y se va a la pantalla si estaba cerca",
         momento_captura(3.5, d) == 7.3 and momento_captura(1.5, d) == 2.5, (momento_captura(3.5, d), momento_captura(1.5, d)))

    respuesta = {"title_seo": "SEO local: guía", "meta_descripcion": "m" * 150, "slug": "SEO Local Guía!", "keyword": "seo local",
                 "h1": "SEO local", "intro": [{"tipo": "parrafo", "texto": "Hola **clave** <script>x</script>"},
                                              {"tipo": "imagen", "t": 3.0}],
                 "secciones": [{"h2": "Uno", "bloques": [
                     {"tipo": "parrafo", "texto": "Lee [[A1|mi guía]] y [[A9|otra]]."},
                     {"tipo": "imagen", "t": 3.1, "alt": "panel", "pie": "el panel"},
                     {"tipo": "imagen", "t": 6.0, "alt": "segunda en la misma sección"},
                     {"tipo": "tabla", "cabecera": "Métrica | Valor", "filas": ["Clics | 10", "CTR"]}]},
                     {"h2": "Dos", "bloques": [{"tipo": "lista", "items": ["a", "b"], "numerada": True},
                                               {"tipo": "imagen", "t": 99.0}]}],
                 "faq": [{"pregunta": "¿Qué?", "respuesta": "Esto."}], "conclusion": "Fin.", "cta": "S1"}
    a = parsea(json.dumps(respuesta), d)
    caso("parser: 1 captura por sección como mucho, ninguna fuera del vídeo ni en la intro; tablas cuadradas; slug limpio",
         [b["tipo"] for b in a["secciones"][0]["bloques"]] == ["parrafo", "imagen", "tabla"]
         and [b["tipo"] for b in a["secciones"][1]["bloques"]] == ["lista"] and a["intro"][-1]["tipo"] == "parrafo"
         and a["secciones"][0]["bloques"][2]["filas"][1] == ["CTR", ""] and a["slug"] == "seo-local-guia", a)
    arts = [{"titulo": "Guía", "url": "https://ejemplo.com/guia/"}]
    a["secciones"][0]["bloques"][1]["archivo"] = "img-01.jpg"
    a.update(cta_texto="Pide tu auditoría", cta_url="https://ejemplo.com/auditoria/", video_html="")
    md, h = a_markdown(a, arts), a_html(a, arts)
    caso("Markdown: enlaces SOLO a artículos reales (el código que no existe queda como texto), tabla, lista numerada, "
         "captura con su pie y la llamada a la acción",
         "[mi guía](https://ejemplo.com/guia/)" in md and "[[A9" not in md and "otra" in md and "| Métrica | Valor |" in md
         and "1. a" in md and "![panel](img-01.jpg)" in md and "*el panel*" in md and "(https://ejemplo.com/auditoria/)" in md, md)
    caso("HTML: el mismo contenido, con lo peligroso escapado (nada de <script>) y la negrita como <strong>",
         "<strong>clave</strong>" in h and "&lt;script&gt;" in h and "<script>" not in h and '<a href="https://ejemplo.com/guia/">' in h
         and "<ol><li>a</li><li>b</li></ol>" in h and '<img src="img-01.jpg" alt="panel"' in h, h[:400])
    try:
        parsea(json.dumps({"h1": "x", "secciones": [{"h2": "solo una", "bloques": [{"tipo": "parrafo", "texto": "y"}]}]}), d)
        caso("un artículo con menos de 2 secciones se rechaza (y se reintenta)", False)
    except ValueError:
        caso("un artículo con menos de 2 secciones se rechaza (y se reintenta)", True)

    # de punta a punta, con un vídeo de verdad (sintético) y la IA de mentira
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        jid = "2026-01-01-art--pro"
        out = t / "videos" / jid
        out.mkdir(parents=True)
        (t / "work" / jid).mkdir(parents=True)
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
             "-t", "10", "-pix_fmt", "yuv420p", str(out / "pro.mp4")])
        frases = [{"word": f"palabra{k}.", "start": 0.5 + k, "end": 0.9 + k} for k in range(9)]
        (t / "work" / jid / "words.json").write_text(json.dumps(frases), encoding="utf-8")
        (out / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "titulo": "Prueba", "duracion": 10.0,
                                                        "segments": [[0.0, 10.0]], "eventos_visuales": eventos,
                                                        "tema": "", "tags": ["seo"]}]), encoding="utf-8")
        pedidos: list = []

        def llama(pr, va):
            pedidos.append(pr)
            return va(json.dumps(respuesta)), "gemini-3.5-flash-lite"

        r = crea(jid, None, t / "videos", t / "work", llama)
        carpeta = out / "articulo"
        caso("de punta a punta: 1 petición, la captura sale de pro.mp4 (apartada de la tarjeta), y se guardan el .md, el "
             ".html de vista previa y el .json",
             r["ok"] and len(pedidos) == 1 and (carpeta / "img-01.jpg").is_file() and (carpeta / "articulo.md").is_file()
             and (carpeta / "articulo.html").is_file() and lee(jid, t / "videos")["imagenes"] == 1
             and lee(jid, t / "videos")["secciones"][0]["bloques"][1]["t_captura"] == 7.3
             and "🖥" in pedidos[0] and "[0:02] 🖥" in pedidos[0], r.get("error") or lee(jid, t / "videos"))

        # --- si la IA lo reescribió con otras palabras, se vuelve a pedir UNA vez conservando sus frases (y se queda la versión más fiel)
        class _Cfg:
            def get_path(self, clave, defecto=None):
                return False if clave == "articulo.verifica_coherencia" else (True if clave == "articulo.reintenta_voz" else defecto)
        fiel_resp = json.loads(json.dumps(respuesta))
        fiel_resp["intro"] = [{"tipo": "parrafo", "texto": "palabra0 palabra1 palabra2 palabra3 palabra4 palabra5 palabra6 palabra7 palabra8"}]
        for sec in fiel_resp["secciones"]:
            sec["bloques"] = [bq for bq in sec["bloques"] if bq["tipo"] != "parrafo"] + [{"tipo": "parrafo", "texto": "palabra1 palabra2 palabra3 palabra4 palabra5 palabra6 palabra7"}]
        pedidos2: list = []

        def llama2(pr, va):
            pedidos2.append(pr)
            return va(json.dumps(respuesta if len(pedidos2) == 1 else fiel_resp)), "gemini-3.5-flash-lite"
        r2 = crea(jid, _Cfg(), t / "videos", t / "work", llama2)
        caso("artículo reescrito con otras palabras: se vuelve a pedir una vez con el aviso de conservar sus frases y se queda la versión más fiel a su voz",
             r2["ok"] and len(pedidos2) == 2 and "AVISO IMPORTANTE" in pedidos2[1] and "AVISO IMPORTANTE" not in pedidos2[0] and r2["articulo"]["fidelidad"] >= FIDELIDAD_MIN, (len(pedidos2), r2.get("articulo", {}).get("fidelidad")))

        # --- la lógica del carrusel en el artículo (el autor, 11-oct-2026): solo pantalla, sin tu cámara, y una IA mira que encaje
        def verif(coincide: bool, elegida: int = 0):
            llamadas: list = []

            def f(partes, va):
                llamadas.append(len(partes))
                if any(isinstance(x, str) and "Alternativa" in x for x in partes):
                    return va(json.dumps({"pasos": [{"id": "i1", "elegida": elegida}]})), "prueba"
                return va(json.dumps({"pasos": [{"id": "i1", "coincide": coincide, "ve": "x"}]})), "prueba"
            return f, llamadas

        f_ok, ll_ok = verif(True)
        r2 = crea(jid, None, t / "videos", t / "work", llama, _verifica=f_ok)
        a2 = lee(jid, t / "videos") or {}
        caso("las capturas pasan por la revisión de coherencia (una petición con todas) y, si encajan, se quedan",
             r2["ok"] and a2.get("imagenes") == 1 and (carpeta / "img-01.jpg").is_file() and len(ll_ok) == 1, (r2.get("error"), a2.get("avisos"), ll_ok))
        f_mal, ll_mal = verif(False, 0)
        r3 = crea(jid, None, t / "videos", t / "work", llama, _verifica=f_mal)
        a3 = lee(jid, t / "videos") or {}
        caso("una captura que NO encaja con su apartado y sin mejor alternativa SE QUITA del artículo (archivo, Markdown y HTML) y se avisa",
             r3["ok"] and a3.get("imagenes") == 0 and not list(carpeta.glob("img-*.jpg")) and "img-01" not in a3["markdown"] and "img-01" not in a3["html"]
             and any("se quitaron del artículo" in x for x in a3.get("avisos", [])), (r3.get("error"), a3.get("avisos"), a3.get("imagenes")))
        f_alt, _l = verif(False, 1)
        r4 = crea(jid, None, t / "videos", t / "work", llama, _verifica=f_alt)
        a4 = lee(jid, t / "videos") or {}
        caso("si hay otra pantalla cercana que sí encaja, se CAMBIA por esa (la imagen sigue, numerada sin huecos)",
             r4["ok"] and a4.get("imagenes") == 1 and (carpeta / "img-01.jpg").is_file() and "img-01.jpg" in a4["markdown"]
             and any("se cambiaron" in x for x in a4.get("avisos", [])), (r4.get("error"), a4.get("avisos")))

    with tempfile.TemporaryDirectory() as tmp3:
        t3 = Path(tmp3)
        for nom, contenido in (("img-02.jpg", b"dos"), ("img-04.jpg", b"cuatro")):
            (t3 / nom).write_bytes(contenido)
        a3_ = {"secciones": [{"h2": "A", "bloques": [{"tipo": "imagen", "archivo": "img-02.jpg"}]}, {"h2": "B", "bloques": [{"tipo": "imagen", "archivo": "img-04.jpg"}]}]}
        n3 = _renumera(a3_, t3)
        caso("si se quitó alguna captura, las que quedan se renumeran sin huecos (img-01, img-02) con su contenido",
             n3 == 2 and sorted(p.name for p in t3.glob("img-*.jpg")) == ["img-01.jpg", "img-02.jpg"] and (t3 / "img-01.jpg").read_bytes() == b"dos"
             and (t3 / "img-02.jpg").read_bytes() == b"cuatro" and a3_["secciones"][1]["bloques"][0]["archivo"] == "img-02.jpg", sorted(p.name for p in t3.iterdir()))

    # --- solo se ve tu PANTALLA: sin ninguna pantalla cerca, esa imagen no sale (nunca un plano de tu cara)
    from . import carrusel as K
    with tempfile.TemporaryDirectory() as tmp2:
        t2 = Path(tmp2)
        video = t2 / "pro.mp4"
        cam = K._camara_de({"canal": "ejemplo"})
        caja = (f"drawbox=x={cam['x'] * 640:.0f}:y={cam['y'] * 360:.0f}:w={cam['w'] * 640:.0f}:h={cam['h'] * 360:.0f}:color=0xFF8000:t=fill" if cam else "null")
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", "-vf", caja, "-t", "40", "-pix_fmt", "yuv420p", str(video)])
        ev = [{"t": 0.0, "tipo": "camara", "detalle": "pantalla"}, {"t": 4.0, "tipo": "camara", "detalle": "cara"}]
        d2 = {"duracion": 40.0, "pantalla": _tramos_pantalla(ev, 40.0), "eventos": ev, "frases": [], "canal": "ejemplo", "video": video}
        a_ = {"jid": "x", "secciones": [{"h2": "Cerca", "bloques": [{"tipo": "imagen", "t": 2.0, "alt": "uno"}]},
                                        {"h2": "En tu cara", "bloques": [{"tipo": "imagen", "t": 30.0, "alt": "dos"}]}]}
        pasos = _capturas_de_pantalla(a_, d2, t2)
        caso("solo salen capturas donde se ve tu PANTALLA: la imagen pedida en pleno plano de tu cara (sin pantalla a ±8 s) se descarta en vez de enseñar tu cara",
             len(pasos) == 1 and a_["secciones"][0]["bloques"][0].get("archivo") == "img-01.jpg" and not a_["secciones"][1]["bloques"][0].get("archivo")
             and 0 <= a_["secciones"][0]["bloques"][0]["t_captura"] < 4.0, (len(pasos), a_))
        if cam:
            from PIL import Image
            with Image.open(t2 / "img-01.jpg") as im:
                rojo = sum(1 for px in im.convert("RGB").resize((160, 90)).getdata() if px[0] > 230 and 100 < px[1] < 160 and px[2] < 40)
                caso("en pantalla entera tu CÁMARA queda recortada (ni un píxel del recuadro naranja) y la captura es la zona grande de al lado, con más zoom a lo que enseñas",
                     rojo == 0 and im.width < 640, (rojo, im.size))
        else:
            caso("(sin cámara medida en config.yaml no se puede probar el recorte)", True)

    # --- la VOZ del autor (el autor, 17-oct-2026: «que guarde mis dichos, mi forma de decirlo, mi expertise a toda costa»)
    dicho = ("o sea mira lo que yo hago siempre es poner la canonical en la pagina principal porque si no google elige una por su cuenta "
             "y casi siempre elige la peor entonces yo prefiero decirle cual es la buena desde el principio y ya").split()
    caso("una frase que sí dijo (con una muletilla de por medio) se reconoce como suya; una parafraseada, no",
         _esta_dicho("Yo prefiero decirle cuál es la buena desde el principio", dicho) and _esta_dicho("Lo que yo hago siempre es poner la canonical en la página principal", dicho)
         and not _esta_dicho("Se recomienda designar una URL canónica para evitar contenido duplicado", dicho))
    fiel = {"intro": [{"tipo": "parrafo", "texto": "Lo que yo hago siempre es poner la canonical en la página principal porque si no Google elige una por su cuenta."}],
            "secciones": [{"h2": "x", "bloques": [{"tipo": "parrafo", "texto": "Casi siempre elige la peor, entonces yo prefiero decirle cuál es la buena desde el principio."}]}], "faq": [], "conclusion": ""}
    manual = {"intro": [{"tipo": "parrafo", "texto": "Designar una URL canónica permite indicar a los buscadores cuál es la versión preferida de una página y evita problemas de contenido duplicado."}],
              "secciones": [{"h2": "x", "bloques": [{"tipo": "parrafo", "texto": "Implementar la etiqueta correctamente contribuye a consolidar las señales de posicionamiento de cada recurso."}]}], "faq": [], "conclusion": ""}
    f1, f2 = fidelidad(fiel, dicho), fidelidad(manual, dicho)
    caso("la fidelidad mide cuánto del artículo es SU voz: alta si conserva sus frases, ~0 si lo reescribió como manual",
         f1 >= 0.5 and f2 <= 0.05 and FIDELIDAD_MIN < f1, (f1, f2))
    refd = {"duracion": 10.0, "frases": [{"t": 0, "texto": " ".join(dicho), "pantalla": False}]}
    ap = parsea(json.dumps({"h1": "H", "secciones": [{"h2": "A", "bloques": [{"tipo": "cita", "texto": "Yo prefiero decirle cuál es la buena desde el principio"}, {"tipo": "cita", "texto": "Conviene designar una canónica en cada plantilla del sitio"}]}, {"h2": "B", "bloques": [{"tipo": "parrafo", "texto": "x"}]}]}), refd)
    caso("un bloque «cita» solo se queda como cita si de verdad lo dijo; una «cita» inventada pasa a párrafo normal (no se pierde el contenido)",
         [b["tipo"] for b in ap["secciones"][0]["bloques"]] == ["cita", "parrafo"], ap["secciones"][0]["bloques"])
    art_c = {"h1": "H", "intro": [], "video_html": "", "secciones": [{"h2": "A", "bloques": [{"tipo": "cita", "texto": "Yo prefiero decirle cuál es la buena"}]}], "faq": [], "conclusion": "", "title_seo": "", "meta_descripcion": ""}
    caso("la cita sale como cita en el Markdown («> …») y en el HTML (<blockquote>)", "> Yo prefiero decirle cuál es la buena" in a_markdown(art_c, []) and "<blockquote><p>Yo prefiero" in a_html(art_c, []))
    caso("el prompt exige SU voz: conservar sus frases, ordenar sin reescribir, primera persona, citas literales, nada de tono de manual",
         all(x in arma_prompt({"titulo": "T", "duracion": 60.0, "tags": [], "capitulos": "", "frases": [{"t": 0, "texto": "hola a todos aquí", "pantalla": False}]}, [], [], "Mi canal", "x.com", "obj")
             for x in ("LA VOZ DEL AUTOR", "NO reescribirlo", "PRIMERA persona", "LITERALMENTE", "PROHIBIDO")))
    return filas
