"""🎬 Título, descripción y tags de cada Short, para YouTube y para TikTok (el autor, 2026-10-09: «cuando salgan shorts… que salga un título con sus tags para TikTok y un título con sus tags para
YouTube… la idea es subirlos a YouTube de una con ese título que proponga… manténlo dentro del flujo»).

- Se escriben SOLOS al terminar de montar los Shorts de una versión (`shorts.crea`): UNA petición a Flash-Lite (500 al día: no toca las 20 de los Flash) para TODOS los Shorts de ese vídeo.
  Si Gemini no responde, salen igual por CÓDIGO (el título del Short, su idea y las etiquetas del vídeo): el flujo nunca se detiene.
- YouTube: título corto con gancho (≤ 70 caracteres), descripción de 2-3 líneas, 5-8 tags. TikTok: texto corto de tono más directo (≤ 90) y 3-5 hashtags. Nada lleva URLs (el enlace al vídeo completo lo
  pone el CÓDIGO al subir, con el de tu vídeo ya publicado) y la IA nunca escribe «#Shorts»: lo añade el código.
- Se guardan en `videos/<jid>/shorts_meta.json` ({archivo: meta}); lo cacheado de la IA vive en `work/shorts_meta_<slug>.json` (un «Reintentar» no gasta otra petición).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import util as _util
from .util import log, nombre_modelo

VERSION = 1
MAX_TITULO_YT = 70                 # YouTube admite 100; en un Short, corto se lee mejor
MAX_TITULO_TT = 90
MAX_TAGS_YT = 8
MAX_HASHTAGS_TT = 5
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_HASHTAG_FINAL = re.compile(r"(?:\s*#[\w]+)+\s*$")
_NO_HASHTAG_TT = {"fyp", "parati", "foryou", "foryoupage", "viral", "shorts", "short"}     # relleno que no aporta

_SYSTEM = """Eres el community manager del canal de YouTube «{canal}»: escribes los títulos, descripciones y etiquetas de sus Shorts para YouTube y para TikTok. Respondes SOLO con JSON válido, sin markdown."""

_PROMPT = """Contexto del canal: {contexto}

Vídeo largo del que salen: «{titulo}» · etiquetas del vídeo: {tags}

Estos son los Shorts (vídeos verticales cortos) que salieron de él. Cada uno ya tiene un título provisional:
{lista}

Para CADA Short escribe, en español con tildes y ¿¡:
- "yt_titulo": el título para YouTube. 25-70 caracteres, con gancho (una promesa, una pregunta o un dato concreto que se cumple en el Short), sin comillas, sin hashtags, sin emojis (uno como mucho) y sin
  prometer lo que el Short no dice. Que no repita al pie de la letra el provisional si se puede decir mejor.
- "yt_descripcion": 2 o 3 líneas cortas (300 caracteres como mucho): la 1.ª con la promesa o la curiosidad, la 2.ª con lo que se aprende. SIN URLs ni hashtags (el programa los añade).
- "yt_tags": de 5 a 8 etiquetas de YouTube (1 a 3 palabras cada una, minúsculas, con tildes, sin «#» y sin «shorts»): el tema concreto del Short y el tema general (SEO, Google…).
- "tt_titulo": el texto para TikTok. 40-90 caracteres, tono más directo y cercano que el de YouTube, una frase que haga querer ver el vídeo. Sin hashtags ni URLs.
- "tt_hashtags": de 3 a 5 hashtags de TikTok sin «#» (una palabra o varias pegadas: «SEOenEspañol»): uno amplio del tema y el resto concretos. Sin «fyp», «parati» ni «viral».
Responde SOLO con este JSON: {{"shorts": [{{"id": 1, "yt_titulo": "...", "yt_descripcion": "...", "yt_tags": ["..."], "tt_titulo": "...", "tt_hashtags": ["..."]}}]}} (un elemento por Short, con el mismo «id»)."""


# ------------------------------------------------------------------ limpieza (todo lo que escribe la IA pasa por aquí)
def _sin_urls(s) -> str:
    return re.sub(r"\s{2,}", " ", _URL_RE.sub("", str(s or ""))).strip()


def limpia_titulo(t, tope: int) -> str:
    """Una línea, sin URLs, comillas ni hashtags al final, cortada en palabra entera si pasa del tope."""
    s = " ".join(_sin_urls(t).split())
    s = _HASHTAG_FINAL.sub("", s).strip().strip("\"'“”«»‘’`").strip()
    if len(s) > tope:
        s = s[:tope].rsplit(" ", 1)[0].rstrip(",;:-–— ") if " " in s[:tope] else s[:tope]
    return s


def limpia_tags(v, tope: int = MAX_TAGS_YT) -> list[str]:
    """Etiquetas de YouTube: sin «#», sin «shorts», de 1 a 3 palabras y ≤ 30 caracteres, sin repetir (da igual mayúsculas) y en total ≤ 400 caracteres (el límite de YouTube es 500)."""
    out: list[str] = []
    vistos: set[str] = set()
    total = 0
    for x in (v if isinstance(v, (list, tuple)) else str(v or "").split(",")):
        s = " ".join(str(x or "").replace("#", " ").split()).strip(" ,;.")
        if not s or len(s) > 30 or len(s.split()) > 3 or s.lower() in ("short", "shorts") or s.lower() in vistos:
            continue
        if total + len(s) + 1 > 400:
            break
        vistos.add(s.lower())
        out.append(s)
        total += len(s) + 1
        if len(out) >= tope:
            break
    return out


def limpia_hashtags(v, tope: int = MAX_HASHTAGS_TT) -> list[str]:
    """Hashtags de TikTok, con su «#», cada uno en una sola palabra con mayúsculas para leerse (`enlaces.hashtag_de`), sin relleno («fyp») y sin repetir."""
    from . import enlaces
    out: list[str] = []
    for x in (v if isinstance(v, (list, tuple)) else str(v or "").replace("#", " ").split()):
        t = " ".join(str(x or "").replace("#", " ").split())
        h = enlaces.hashtag_de(t) if (" " in t or not t.isupper()) and not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", t) else "#" + t
        if len(h) < 3 or len(h) > 31 or h[1:].lower() in _NO_HASHTAG_TT or h.lower() in {o.lower() for o in out}:
            continue
        out.append(h)
        if len(out) >= tope:
            break
    return out


def _frases(texto: str, n: int = 2, tope: int = 300) -> str:
    """Las `n` primeras frases (una por línea) de un texto, sin URLs y cortado a `tope`."""
    s = " ".join(_sin_urls(texto).split())
    partes = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", s) if p.strip()]
    out = "\n".join(partes[:n])
    return out[:tope].rsplit(" ", 1)[0] if len(out) > tope and " " in out[:tope] else out[:tope]


# ------------------------------------------------------------------ por código (la red de seguridad: sin Gemini)
def por_codigo(sh: dict, tags_video: list[str] | None = None) -> dict:
    """El título, la descripción y las etiquetas de un Short sacados de lo que YA se sabe de él (su título, su idea, lo que se aprende) y de las etiquetas del vídeo."""
    tags = limpia_tags(tags_video or [])
    idea = str(sh.get("idea") or "").strip()
    aprende = str(sh.get("que_aprende") or sh.get("por_que") or "").strip()
    desc = "\n".join(x for x in (_frases(idea, 1, 200), _frases(aprende, 1, 200)) if x)
    return {"yt": {"titulo": limpia_titulo(sh.get("titulo") or "", MAX_TITULO_YT), "descripcion": desc, "tags": tags},
            "tiktok": {"titulo": limpia_titulo(sh.get("gancho") or sh.get("titulo") or "", MAX_TITULO_TT), "hashtags": limpia_hashtags(tags[:MAX_HASHTAGS_TT])},
            "origen": "codigo"}


def arma_descripcion(meta: dict, enlace: str = "") -> str:
    """La descripción que se sube a YouTube: la de la IA (o la de código), el enlace al vídeo completo si ya se conoce, y «#Shorts» con los 3 primeros tags como hashtags. ≤ 4.900 caracteres."""
    yt = (meta or {}).get("yt") or {}
    partes = [str(yt.get("descripcion") or "").strip()]
    if enlace:
        partes.append(f"🎬 Vídeo completo: {enlace}")
    from . import enlaces
    hashtags = ["#Shorts"] + [h for h in (enlaces.hashtag_de(t) for t in (yt.get("tags") or [])[:3]) if h]
    partes.append(" ".join(hashtags))
    return "\n\n".join(p for p in partes if p)[:4900]


# ------------------------------------------------------------------ la IA (UNA petición para todos los Shorts del vídeo)
_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from pydantic import BaseModel

            class Meta(BaseModel):
                id: int
                yt_titulo: str
                yt_descripcion: str
                yt_tags: list[str]
                tt_titulo: str
                tt_hashtags: list[str]

            class Respuesta(BaseModel):
                shorts: list[Meta]

            _ESQUEMA = Respuesta
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQUEMA = False
    return _ESQUEMA or None


def arma_prompt(shorts: list[dict], entrada: dict) -> str:
    lista = "\n".join(f"{i}. «{s.get('titulo', '')}» — gancho: «{s.get('gancho', '')}» — idea: {s.get('idea', '')} — qué se aprende: {s.get('que_aprende') or s.get('por_que') or ''} "
                      f"({s.get('tipo', 'otro')}, {round(float(s.get('duracion') or 0))} s)" for i, s in enumerate(shorts, 1))
    return _PROMPT.format(contexto=str(entrada.get("contexto") or "canal de YouTube"), titulo=str(entrada.get("titulo") or "(sin título)"),
                          tags=", ".join(entrada.get("tags") or []) or "—", lista=lista)


def _json_de(texto: str):
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    return json.loads(t)


def parsea(texto: str, shorts: list[dict], tags_video: list[str] | None = None) -> list[dict]:
    """La respuesta de la IA -> una meta por Short, LIMPIA. Cada campo que la IA no dé bien (vacío, demasiado corto, sin tags suficientes) sale por código; si no sirve NINGUNO, lanza (así la cadena prueba
    con otro modelo). Nunca devuelve URLs ni hashtags dentro de un título."""
    d = _json_de(texto)
    filas = d.get("shorts") if isinstance(d, dict) else d
    if not isinstance(filas, list) or not filas:
        raise ValueError("la respuesta no trae Shorts")
    por_id = {}
    for f in filas:
        try:
            por_id[int(f.get("id"))] = f
        except (TypeError, ValueError, AttributeError):
            continue
    out: list[dict] = []
    usos_ia = 0
    for i, s in enumerate(shorts, 1):
        base = por_codigo(s, tags_video)
        f = por_id.get(i) or {}
        yt_t = limpia_titulo(f.get("yt_titulo"), MAX_TITULO_YT)
        yt_d = _frases(f.get("yt_descripcion"), 3, 300)
        yt_tags = limpia_tags(f.get("yt_tags"))
        tt_t = limpia_titulo(f.get("tt_titulo"), MAX_TITULO_TT)
        tt_h = limpia_hashtags(f.get("tt_hashtags"))
        meta = {"yt": {"titulo": yt_t if len(yt_t) >= 12 else base["yt"]["titulo"], "descripcion": yt_d if len(yt_d) >= 20 else base["yt"]["descripcion"],
                       "tags": yt_tags if len(yt_tags) >= 3 else (yt_tags + [t for t in base["yt"]["tags"] if t.lower() not in {x.lower() for x in yt_tags}])[:MAX_TAGS_YT]},
                "tiktok": {"titulo": tt_t if len(tt_t) >= 12 else base["tiktok"]["titulo"], "hashtags": tt_h if len(tt_h) >= 2 else base["tiktok"]["hashtags"]},
                "origen": "ia"}
        usos_ia += int(len(yt_t) >= 12) + int(len(tt_t) >= 12)
        out.append(meta)
    if not usos_ia:
        raise ValueError("la IA no escribió ningún título utilizable")
    return out


def _firma(shorts: list[dict]) -> str:
    return hashlib.sha1(json.dumps([[s.get("archivo"), s.get("titulo"), s.get("idea")] for s in shorts], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def genera(shorts: list[dict], entrada: dict, cfg=None, work=None, slug: str = "", forzar: bool = False, _gemini=None, _modelos=None) -> dict:
    """Las metas de TODOS los Shorts de una versión con UNA petición a Flash-Lite. `entrada` = {titulo, tags, canal, contexto}. -> {"metas": [una por Short], "origen": "ia"|"codigo", "modelo", "nombre_modelo"}.
    De caché (`work/shorts_meta_<slug>.json`) si los Shorts son los mismos (un «Reintentar» no gasta); si Gemini no responde, por código (no se cachea: se reintenta la próxima vez). Nunca lanza."""
    tags_video = list(entrada.get("tags") or [])
    firma = _firma(shorts)
    cache = Path(work) / f"shorts_meta_{slug}.json" if work and slug else None
    if cache is not None and not forzar:
        try:
            previo = json.loads(cache.read_text(encoding="utf-8"))
            if previo.get("v") == VERSION and previo.get("firma") == firma and len(previo.get("metas") or []) == len(shorts):
                return {**previo, "de_cache": True}
        except (OSError, ValueError):
            pass
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                raise RuntimeError("no hay clave de Gemini")
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from . import modelos as _m
            _modelos = _m.cadena_lite(cfg, client)
        metas, modelo, _uso = _util.call_gemini_cadena(
            client, types, _modelos, arma_prompt(shorts, entrada), system=_SYSTEM.format(canal=str(entrada.get("canal") or "de YouTube")),
            valida=lambda t: parsea(t, shorts, tags_video), reintento_s=30.0, pasadas=1, timeout_s=90.0, esquema=_esquema(), etiqueta="shorts_meta")
    except Exception as exc:  # noqa: BLE001 — sin la IA, por código: los Shorts salen igual con título y tags
        log(f"  Shorts: el título y los tags salen por código (Gemini: {str(exc)[:120]}).")
        return {"v": VERSION, "firma": firma, "metas": [por_codigo(s, tags_video) for s in shorts], "origen": "codigo", "modelo": "", "nombre_modelo": ""}
    out = {"v": VERSION, "firma": firma, "metas": metas, "origen": "ia", "modelo": modelo, "nombre_modelo": nombre_modelo(modelo)}
    if cache is not None:
        try:
            _util.reemplaza_texto(cache, json.dumps(out, ensure_ascii=False, indent=1)) if hasattr(_util, "reemplaza_texto") else cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    log(f"  Shorts: título y tags de {len(shorts)} Short(s) escritos ({out['nombre_modelo']}).")
    return out


# ------------------------------------------------------------------ guardar y leer (junto a los Shorts de esa versión)
def _ruta(out_dir) -> Path:
    return Path(out_dir) / "shorts_meta.json"


def lee(out_dir) -> dict:
    try:
        d = json.loads(_ruta(out_dir).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def guarda(out_dir, por_archivo: dict) -> None:
    """{archivo del Short: meta}. Atómico (el panel lo lee mientras se escribe)."""
    p = _ruta(out_dir)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps(por_archivo, ensure_ascii=False, indent=1), encoding="utf-8")
    _util.reemplaza(tmp, p)


def para_version(shorts: list[dict], entrada: dict, out_dir, cfg=None, work=None, slug: str = "", forzar: bool = False, _gemini=None, _modelos=None) -> dict:
    """`genera` + guardar en la carpeta de la versión. -> el resultado de `genera`."""
    r = genera(shorts, entrada, cfg, work, slug, forzar, _gemini, _modelos)
    guarda(out_dir, {s["archivo"]: m for s, m in zip(shorts, r["metas"])})
    return r


# ------------------------------------------------------------------ pruebas (sin red: Gemini de mentira; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    shorts = [{"archivo": "short_1.mp4", "titulo": "El error que te saca de Google", "gancho": "Tu página no aparece y no sabes por qué", "idea": "Una etiqueta noindex olvidada saca tu página del buscador. Se revisa en 10 segundos.",
               "que_aprende": "A comprobar el noindex con la inspección de URL.", "tipo": "error_comun", "duracion": 41.0},
              {"archivo": "short_2.mp4", "titulo": "Sitemap en un minuto", "gancho": "", "idea": "Cómo enviar tu sitemap a Search Console.", "tipo": "truco", "duracion": 33.0}]
    entrada = {"titulo": "Indexación SEO", "tags": ["SEO para Google", "indexación", "Search Console", "SEO técnico", "Google"], "canal": "Mi canal", "contexto": "Canal de SEO"}

    # limpieza
    caso("un título sale en una línea, sin URL, sin comillas ni hashtags al final y cortado en palabra entera",
         limpia_titulo('«Revisa esto https://x.com   ahora»  #SEO #Google', 70) == "Revisa esto ahora" and limpia_titulo("palabra " * 20, 30).endswith("palabra") and len(limpia_titulo("palabra " * 20, 30)) <= 30
         and "\n" not in limpia_titulo("a\nb", 20), limpia_titulo('«Revisa esto https://x.com   ahora»  #SEO #Google', 70))
    caso("los tags salen sin «#», sin «shorts», sin repetir, de 1 a 3 palabras, y como mucho 8",
         limpia_tags(["#SEO", "seo", "Shorts", "tag con demasiadas palabras largas", "x" * 40, "Search Console"] + [f"t{i}" for i in range(10)]) == ["SEO", "Search Console", "t0", "t1", "t2", "t3", "t4", "t5"],
         limpia_tags(["#SEO", "seo", "Shorts", "tag con demasiadas palabras largas", "x" * 40, "Search Console"] + [f"t{i}" for i in range(10)]))
    h = limpia_hashtags(["SEO para Google", "#fyp", "indexación", "SEOenEspañol", "viral", "seo"])
    caso("los hashtags de TikTok salen con «#», frases en una palabra con mayúsculas, sin relleno («fyp», «viral») y sin repetir", h[0] == "#SEOparaGoogle" and all(x.startswith("#") and " " not in x for x in h)
         and not {"#fyp", "#viral"} & {x.lower() for x in h} and len({x.lower() for x in h}) == len(h) and "#SEOenEspañol" in h, h)

    # por código
    pc = por_codigo(shorts[0], entrada["tags"])
    caso("por código: el título del Short, su idea y las etiquetas del vídeo; TikTok con el gancho y hashtags", pc["yt"]["titulo"] == "El error que te saca de Google" and "noindex" in pc["yt"]["descripcion"]
         and len(pc["yt"]["tags"]) == 5 and pc["tiktok"]["titulo"].startswith("Tu página no aparece") and len(pc["tiktok"]["hashtags"]) >= 3 and pc["origen"] == "codigo", pc)
    caso("la descripción que se sube lleva el enlace al vídeo completo (si se conoce) y «#Shorts» con 3 hashtags; sin enlace, sin esa línea",
         "🎬 Vídeo completo: https://youtu.be/abc" in arma_descripcion(pc, "https://youtu.be/abc") and "#Shorts #SEOparaGoogle #Indexación #SearchConsole" in arma_descripcion(pc, "")
         and "Vídeo completo" not in arma_descripcion(pc, ""), arma_descripcion(pc, ""))

    # el parser de la IA
    buena = {"shorts": [{"id": 1, "yt_titulo": "Este error te saca de Google y no lo ves", "yt_descripcion": "¿Tu página no aparece? A veces es una etiqueta olvidada.\nSe revisa en 10 segundos. https://x.com", "yt_tags": ["#noindex", "SEO técnico", "indexación", "Google", "Shorts"],
                        "tt_titulo": "Tu página no sale en Google y la culpa es de UNA etiqueta #SEO", "tt_hashtags": ["SEO", "noindex", "fyp", "SEOenEspañol"]},
                       {"id": 2, "yt_titulo": "x", "yt_descripcion": "", "yt_tags": [], "tt_titulo": "", "tt_hashtags": []}]}
    ms = parsea(json.dumps(buena), shorts, entrada["tags"])
    caso("el parser limpia lo de la IA (sin URLs, hashtags, «shorts» ni «fyp») y lo que falla o es muy corto sale por código, Short por Short",
         ms[0]["origen"] == "ia" and ms[0]["yt"]["titulo"].startswith("Este error") and "https" not in ms[0]["yt"]["descripcion"] and "Shorts" not in ms[0]["yt"]["tags"] and "#fyp" not in ms[0]["tiktok"]["hashtags"]
         and "#" not in ms[0]["tiktok"]["titulo"] and ms[1]["yt"]["titulo"] == "Sitemap en un minuto" and len(ms[1]["yt"]["tags"]) >= 3, ms)
    try:
        parsea(json.dumps({"shorts": [{"id": 1, "yt_titulo": "", "tt_titulo": ""}]}), shorts, entrada["tags"])
        caso("si la IA no da ningún título utilizable se rechaza (para probar con otro modelo)", False)
    except ValueError:
        caso("si la IA no da ningún título utilizable se rechaza (para probar con otro modelo)", True)

    # genera: una sola petición, caché y red de seguridad
    pedidos: list = []
    real = _util.call_gemini_cadena

    def falso(client, types, modelos, prompt, system=None, valida=None, **k):
        pedidos.append(prompt)
        return valida(json.dumps(buena)), "gemini-3.5-flash-lite", {}

    _util.call_gemini_cadena = falso
    try:
        with tempfile.TemporaryDirectory() as tmp:
            work, out = Path(tmp) / "work", Path(tmp) / "out"
            work.mkdir()
            out.mkdir()
            kw = dict(_gemini=(object(), object()), _modelos=["m"])
            r = para_version(shorts, entrada, out, None, work, "pro", **kw)
            guardado = lee(out)
            caso("de punta a punta: UNA petición para los 2 Shorts, y las metas quedan guardadas por archivo junto a la versión",
                 len(pedidos) == 1 and r["origen"] == "ia" and set(guardado) == {"short_1.mp4", "short_2.mp4"} and "El error que te saca" in pedidos[0] and "Sitemap en un minuto" in pedidos[0], (len(pedidos), list(guardado)))
            r2 = para_version(shorts, entrada, out, None, work, "pro", **kw)
            caso("un «Reintentar» (mismos Shorts) sale de la caché: 0 peticiones más", len(pedidos) == 1 and r2.get("de_cache") is True, len(pedidos))
            r3 = para_version(shorts, entrada, out, None, work, "pro", forzar=True, **kw)
            caso("«forzar» (el botón ↻) sí vuelve a preguntar", len(pedidos) == 2 and r3["origen"] == "ia", len(pedidos))

            def roto(client, types, modelos, prompt, system=None, valida=None, **k):
                raise RuntimeError("503 UNAVAILABLE: high demand")

            _util.call_gemini_cadena = roto
            r4 = genera(shorts, entrada, None, None, "", **kw)
            caso("si Gemini no responde, salen por código (título del Short + etiquetas del vídeo): el flujo no se detiene y no se cachea",
                 r4["origen"] == "codigo" and len(r4["metas"]) == 2 and r4["metas"][0]["yt"]["titulo"] == "El error que te saca de Google" and len(r4["metas"][0]["yt"]["tags"]) == 5, r4["origen"])
    finally:
        _util.call_gemini_cadena = real
    return filas
