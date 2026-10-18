"""💡 PERFIL DE IDEAS de un canal (el autor, 2026-10-04): «al principio, cuando recién se crea un canal, en el apartado de ideas… la primera
vez solamente… esa configuración de qué es lo que quiere la persona, y ya después, seguir… porque yo subo videos de SEO, pero hay
muchas personas que suben otros videos».

Las ideas (clipper/ideas.py) no saben nada de un tema en concreto: leen el PERFIL del canal:
  · de qué trata y qué quiere lograr su autor (`tema`, `objetivo`: lo que manda al elegir las ideas);
  · a qué públicos quiere llegar: hasta 3 «vertientes» (cada una con su descripción y cuántas ideas lleva) + las ideas «generales»
    (las abiertas: lo que funciona en su nicho). Entre todas suman 10-30 ideas (20 por defecto);
  · qué busca la gente en YouTube sobre su tema (`busquedas`, en español y, si quiere, `busquedas_en`, en inglés);
  · a dónde llevar a la gente (`destinos`: su web, su tienda, su curso…) y si usa los datos de su propio canal de YouTube.
- El perfil del autor (el canal al que se refiere `ideas:` de config.yaml) se arma SOLO desde esa configuración: no ve ningún asistente
  y sus ideas salen igual que antes.
- Un canal nuevo: la primera vez que abre «Ideas» se le piden unas respuestas; UNA petición a Flash-Lite (gratis, una vez por canal)
  propone el perfil, la persona lo ajusta y lo guarda (`work/ideas/perfil_<canal>.json`). Si la IA no responde, hay un perfil de partida
  hecho por código. Después ya no se le vuelve a preguntar (se edita desde «⚙ Configuración de las ideas»).
- La IA nunca escribe una URL: los destinos salen de lo que escribe el autor o de los enlaces de su canal.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from .util import log, normalize, reemplaza

VERSION = 1
ID_GENERAL = "general"
MAX_VERTIENTES = 3
TOTAL_DEF, TOTAL_MIN, TOTAL_MAX = 20, 10, 30
OBJETIVOS = {"clientes": "Conseguir clientes para mi servicio o negocio", "producto": "Vender un curso o un producto",
             "audiencia": "Crecer la audiencia (anuncios y patrocinios)", "marca": "Construir mi marca personal y mi autoridad"}

# ------------------------------------------------------------------ perfil «de config.yaml» (opcional, para quien prefiere editar a mano en vez de usar el asistente)
# Si en config.yaml pones `ideas: {canal: <id de tu canal>, mezcla: {...}, busquedas: [...], semillas: [...]}`, ese canal NO pasa por el asistente: su perfil se arma con
# esto y con los textos de fábrica de aquí abajo (genéricos: cámbialos por los tuyos en tu canal con el asistente «⚙ Configuración de las ideas»).
CASILLA_BASE = {
    "negocio": ("para POSIBLES CLIENTES de lo que ofreces (personas o empresas que podrían contratar tu servicio): hablan de sus problemas y de resultados concretos "
                "de su negocio, sin trucos para principiantes. Llevan a tu servicio (CTA = un servicio [S])."),
    "aprender": ("para PERSONAS que quieren APRENDER lo que tú sabes: tutoriales, rutas paso a paso y errores de principiante, siempre con un resultado concreto. "
                 "Llevan a tu curso o producto (CTA = [S])."),
    "general": ("IDEAS GENERALES DE TU TEMA: es donde tienes más libertad. Salen de lo que se está MOVIENDO ahora en tu nicho (los datos [T] y [O], cada uno con su idioma) y de lo que "
                "sabes del tema. Con este estilo: experimentos y casos reales en primera persona («Creé…», «Hice…», «Analicé…», «Probé…»), «Cómo…» muy concretos que "
                "prometen un resultado, métodos y herramientas gratis, listas de lo que revisas o haces, y errores o mitos que casi nadie ve. Mézclalas con contenido "
                "INFORMATIVO / explicativo (al menos 4): conceptos que la gente busca entender de tu tema."),
}
NOMBRE_BASE = {"negocio": "Para clientes", "aprender": "Para aprender", "general": "Ideas generales"}
RESUMEN_BASE = {"negocio": "personas y empresas que podrían contratar lo que ofreces",
                "aprender": "quien quiere aprender lo que sabes (tu curso o producto)",
                "general": "lo que se mueve en tu nicho, con tu estilo (y explicativos)"}
OBJETIVO_BASE = ("Su prioridad nº 1 es conseguir clientes para su servicio o negocio. La nº 2, vender su curso o producto. "
                 "Crecer la audiencia es el medio, no el fin.")
MEZCLA_BASE = {"negocio": 5, "aprender": 5, "general": 15}
ETIQUETA_OBJETIVO_BASE = "EL NEGOCIO (manda sobre todo lo demás)"
ETIQUETA_DESTINOS_BASE = "SUS SERVICIOS (para la llamada a la acción)"
ETIQUETA_OBJETIVO = "EL OBJETIVO DEL CANAL (manda sobre todo lo demás)"
ETIQUETA_DESTINOS = "SUS DESTINOS (para la llamada a la acción: a dónde llevar a la gente)"


# ------------------------------------------------------------------ utilidades
def _txt(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _entero(v, d: int, minimo: int = 0, maximo: int = 10 ** 6) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return d
    return max(minimo, min(maximo, n))


def _lista(v, n: int, largo: int) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[\n;]+", v)
    out: list[str] = []
    for x in v or []:
        t = _txt(x, largo)
        if len(t) >= 2 and t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out[:n]


def _primera_frase(t: str, n: int) -> str:
    f = re.split(r"(?<=[.!?])\s", _txt(t, 400), maxsplit=1)[0]
    return f[:n].rstrip(" .,;:")


def _slug(nombre: str, usados: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", normalize(nombre)).strip("-")[:20].strip("-") or "publico"
    if base == ID_GENERAL:
        base = "publico"
    s, i = base, 2
    while s in usados:
        s, i = f"{base}-{i}", i + 1
    return s


def _dir(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work) / "ideas"
    from . import config
    return config.paths()["work"] / "ideas"


def _ruta(canal: str, dir_work=None) -> Path:
    return _dir(dir_work) / f"perfil_{re.sub(r'[^A-Za-z0-9_-]', '', str(canal))[:40] or 'canal'}.json"


# ------------------------------------------------------------------ SUS ideas favoritas y SUS herramientas (el autor, 2026-10-05)
def semilla_id(titulo: str) -> str:
    """El id estable de una idea favorita: sale del TÍTULO (no de su posición en config.yaml), así reordenar la lista no descoloca lo ya marcado."""
    import hashlib
    return "semilla-" + hashlib.sha1(normalize(str(titulo)).encode("utf-8")).hexdigest()[:8]


def semillas_de(v) -> list[dict]:
    """`ideas.semillas` de config.yaml -> [{"id", "titulo", "potencial": 1-3}]. Cada una puede ser un texto o {titulo, potencial}; sin repetir, hasta 30."""
    out: list[dict] = []
    vistos: set = set()
    for x in v if isinstance(v, (list, tuple)) else []:
        titulo = _txt(x.get("titulo") if isinstance(x, dict) else x, 120)
        if len(titulo) < 8 or normalize(titulo) in vistos:
            continue
        vistos.add(normalize(titulo))
        out.append({"id": semilla_id(titulo), "titulo": titulo,
                    "potencial": _entero(x.get("potencial") if isinstance(x, dict) else None, 2, 1, 3)})
    return out[:30]


def herramientas_de(v) -> list[dict]:
    """`ideas.herramientas`: las herramientas que el autor YA ha creado -> [{"nombre", "que_hace"}] (para las ideas «Creé una herramienta…»: la IA no
    inventa ninguna otra)."""
    out: list[dict] = []
    for x in v if isinstance(v, (list, tuple)) else []:
        if isinstance(x, dict) and _txt(x.get("nombre"), 60) and _txt(x.get("que_hace"), 220):
            out.append({"nombre": _txt(x.get("nombre"), 60), "que_hace": _txt(x.get("que_hace"), 220)})
    return out[:12]


# ------------------------------------------------------------------ el perfil
def valida(d: dict, canal: str = "") -> tuple[dict, list[str]]:
    """Lo que escribió la persona (o propuso la IA) -> (perfil limpio, errores). Nada se guarda si hay errores."""
    d = d if isinstance(d, dict) else {}
    err: list[str] = []
    tema = _txt(d.get("tema"), 700)
    if len(tema) < 10:
        err.append("Cuéntame de qué trata tu canal (al menos una frase).")
    objetivo = _txt(d.get("objetivo"), 700)
    if len(objetivo) < 10:
        err.append("Dime qué quieres lograr con el canal (al menos una frase).")
    usados = {ID_GENERAL}
    vert: list[dict] = []
    for v in (d.get("vertientes") or [])[:MAX_VERTIENTES]:
        if not isinstance(v, dict) or not _txt(v.get("nombre"), 28):
            continue
        nombre = _txt(v.get("nombre"), 28)
        desc = _txt(v.get("descripcion"), 400)
        if len(desc) < 10:
            err.append(f"Describe en una frase a quién va dirigido «{nombre}».")
        vid = _slug(nombre, usados)
        usados.add(vid)
        vert.append({"id": vid, "nombre": nombre, "resumen": _txt(v.get("resumen"), 90) or _primera_frase(desc, 90),
                     "descripcion": desc, "n": _entero(v.get("n"), 5, 1, 15)})
    g = d.get("general") if isinstance(d.get("general"), dict) else {}
    general = {"id": ID_GENERAL, "nombre": _txt(g.get("nombre"), 28) or "Generales",
               "resumen": _txt(g.get("resumen"), 90) or "lo que funciona en tu tema",
               "descripcion": _txt(g.get("descripcion"), 400) or "Ideas abiertas, con base en lo que más funciona en tu tema ahora mismo.",
               "n": _entero(g.get("n"), 8, 1, 25)}
    total = sum(v["n"] for v in vert) + general["n"]
    if not TOTAL_MIN <= total <= TOTAL_MAX:
        err.append(f"En total salen {total} ideas: tienen que ser entre {TOTAL_MIN} y {TOTAL_MAX}.")
    busq = _lista(d.get("busquedas"), 5, 60)
    if not busq:
        err.append("Pon al menos una búsqueda de tu tema (lo que la gente escribe en YouTube).")
    idiomas = "es+en" if str(d.get("idiomas") or "").lower().replace(" ", "") in ("es+en", "esen", "es-en") else "es"
    busq_en = _lista(d.get("busquedas_en"), 5, 60) if idiomas == "es+en" else []
    if idiomas == "es+en" and not busq_en:
        err.append("Pon al menos una búsqueda en inglés (o elige «solo español»).")
    destinos: list[dict] = []
    for x in (d.get("destinos") or [])[:3]:
        if not isinstance(x, dict):
            continue
        url = _txt(x.get("url"), 300)
        if not url and not _txt(x.get("nombre"), 60):
            continue
        if not re.match(r"^https?://[^\s]+$", url, re.I):
            err.append("Los enlaces de destino tienen que empezar por https://")
            continue
        destinos.append({"nombre": _txt(x.get("nombre"), 60) or "Mi enlace", "url": url})
    perfil = {"version": VERSION, "canal": canal or _txt(d.get("canal"), 40), "origen": _txt(d.get("origen"), 12) or "manual",
              "tema": tema, "objetivo": objetivo, "vertientes": vert, "general": general, "busquedas": busq,
              "busquedas_en": busq_en, "idiomas": idiomas, "destinos": destinos,
              "datos_propios": bool(d.get("datos_propios", True)), "total": total}
    return perfil, err


def lee(canal: str, dir_work=None) -> dict | None:
    """El perfil guardado de un canal, o None (sin archivo o roto)."""
    try:
        d = json.loads(_ruta(canal, dir_work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    perfil, err = valida(d, canal)
    return None if err else perfil


def guarda(canal: str, d: dict, dir_work=None) -> tuple[dict | None, list[str]]:
    perfil, err = valida(d, canal)
    if err:
        return None, err
    perfil["guardado"] = time.time()
    p = _ruta(canal, dir_work)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(perfil, ensure_ascii=False, indent=1), encoding="utf-8")
    reemplaza(tmp, p)
    return perfil, []


def borra(canal: str, dir_work=None) -> bool:
    """No borra: mueve el perfil a `work/ideas/_papelera/` (se puede recuperar a mano)."""
    p = _ruta(canal, dir_work)
    if not p.is_file():
        return False
    dest = p.parent / "_papelera"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest / f"{p.stem}-{time.strftime('%Y%m%d-%H%M%S')}.json"))
    return True


def canal_con_config(cfg) -> str | None:
    """El canal cuyo perfil de ideas se arma a mano en config.yaml (`ideas.canal`); None si no hay ninguno (lo normal: se usa el asistente)."""
    c = cfg.get_path("ideas.canal") if cfg is not None else None
    return str(c) if c else None


def perfil_config(cfg) -> dict:
    """El perfil armado con `ideas:` de config.yaml y los textos de fábrica de arriba (para quien prefiere editar a mano)."""
    c = dict(cfg.get_path("ideas") or {}) if cfg is not None else {}
    pedida = dict(c.get("mezcla") or {})
    if any(k in pedida for k in ("crecimiento", "autoridad")):       # una mezcla de antes (4/4/2): manda la de ahora
        pedida = {}
    n = {t: _entero(pedida.get(t), d, 0) for t, d in MEZCLA_BASE.items()}
    if sum(n.values()) <= 0:
        n = dict(MEZCLA_BASE)

    def lista(k):
        v = c.get(k)
        v = v if isinstance(v, (list, tuple)) else ([v] if v else [])
        return [str(q).strip() for q in v if q and str(q).strip()]

    vert = [{"id": t, "nombre": NOMBRE_BASE[t], "resumen": RESUMEN_BASE[t], "descripcion": CASILLA_BASE[t], "n": n[t]}
            for t in ("negocio", "aprender") if n[t] > 0]
    return {"version": VERSION, "canal": canal_con_config(cfg) or "", "origen": "config", "tema": str(c.get("tema") or ""),
            "objetivo": str(c.get("objetivo") or OBJETIVO_BASE), "etiqueta_objetivo": ETIQUETA_OBJETIVO_BASE,
            "etiqueta_destinos": ETIQUETA_DESTINOS_BASE, "vertientes": vert,
            "general": {"id": ID_GENERAL, "nombre": NOMBRE_BASE["general"], "resumen": RESUMEN_BASE["general"],
                        "descripcion": CASILLA_BASE["general"], "n": n["general"]},
            "busquedas": lista("busquedas"), "busquedas_en": lista("busquedas_en"),
            "idiomas": "es+en" if lista("busquedas_en") else "es", "destinos": [], "datos_propios": True, "total": sum(n.values()),
            "palabras_tema": [str(x) for x in (c.get("palabras_tema") or [])],
            "semillas": semillas_de(c.get("semillas")), "herramientas": herramientas_de(c.get("herramientas"))}


def de_canal(canal: str, cfg, dir_work=None) -> dict | None:
    """El perfil que usa un canal: el que guardó la persona; si no, el del autor (si es su canal); si no, None (hay que
    configurarlo la primera vez)."""
    d = lee(canal, dir_work)
    if d:
        return d
    if canal and canal == canal_con_config(cfg):
        return perfil_config(cfg)
    return None


def base_del_canal(canal: str, cfg) -> dict:
    """Lo que ya se sabe del canal (del asistente de «Nuevo canal») para rellenar las respuestas de la primera vez."""
    info = (cfg.get_path(f"videos.canales.{canal}") or {}) if cfg is not None else {}
    per = info.get("perfil") or {}
    enl = info.get("enlaces") or {}
    destinos = []
    if enl.get("cta_url"):
        destinos.append({"nombre": str(enl.get("cta_texto") or "Mi enlace principal"), "url": str(enl["cta_url"])})
    if enl.get("web"):
        destinos.append({"nombre": "Mi página web", "url": str(enl["web"])})
    vistos: set = set()
    destinos = [d for d in destinos if not (d["url"] in vistos or vistos.add(d["url"]))]
    return {"canal": canal, "nombre": str(info.get("nombre") or canal), "tema": _txt(per.get("tema"), 700),
            "audiencia": _txt(per.get("audiencia"), 300), "tipo": _txt(per.get("tipo"), 200), "idioma": _txt(per.get("idioma"), 80),
            "destinos": destinos}


def sugiere_datos_propios(canal: str, cfg, canal_yt: str | None) -> bool:
    """¿Este canal es el que tiene conectado a YouTube? Solo entonces las ideas pueden usar SUS datos (vídeos, búsquedas que
    llegan a su canal…): con un solo canal, sí; con varios, si el nombre del canal conectado coincide con el de este."""
    from .config import canales_visibles
    canales = canales_visibles(cfg)
    if len(canales) <= 1:
        return True
    if not canal_yt:
        return False
    nombre = str((canales.get(canal) or {}).get("nombre") or canal)
    return normalize(nombre).replace(" ", "") == normalize(canal_yt).replace(" ", "")


# ------------------------------------------------------------------ el asistente de la primera vez (Flash-Lite)
def _respuestas(resp: dict, base: dict) -> dict:
    resp = resp if isinstance(resp, dict) else {}
    return {"tema": _txt(resp.get("tema"), 700) or base.get("tema", ""),
            "objetivo": str(resp.get("objetivo") or "") if str(resp.get("objetivo") or "") in OBJETIVOS else "",
            "objetivo_texto": _txt(resp.get("objetivo_texto"), 500), "publicos": _lista(resp.get("publicos"), 3, 80),
            "idiomas": "es+en" if str(resp.get("idiomas") or "") == "es+en" else "es",
            "destinos": [{"nombre": _txt(x.get("nombre"), 60), "url": _txt(x.get("url"), 300)} for x in (resp.get("destinos") or [])
                         if isinstance(x, dict)][:3] or base.get("destinos") or [],
            "datos_propios": bool(resp.get("datos_propios", True))}


def prompt_propuesta(resp: dict, base: dict) -> str:
    obj = OBJETIVOS.get(str(resp.get("objetivo") or ""), "")
    L = ["Eres estratega de contenido de YouTube. Un autor acaba de crear su canal en un editor de vídeo y vamos a configurar su LISTA DE "
         f"IDEAS: {TOTAL_DEF} ideas por tanda, repartidas entre «vertientes» (públicos a los que quiere llegar) y unas ideas «generales». "
         "Con lo que cuenta, propón la configuración.", "",
         f"CANAL: «{base.get('nombre') or ''}»", f"DE QUÉ TRATA: {resp.get('tema') or '(no lo dijo)'}"]
    for etiqueta, v in (("AUDIENCIA", base.get("audiencia")), ("TIPO DE VÍDEOS", base.get("tipo")),
                        ("IDIOMA DE SUS VÍDEOS", base.get("idioma"))):
        if v:
            L.append(f"{etiqueta}: {v}")
    L.append("LO QUE QUIERE LOGRAR: " + (". ".join(x for x in (obj, resp.get("objetivo_texto")) if x)
                                         or "(no lo dijo: propón lo más razonable para su tema)"))
    L.append("PÚBLICOS A LOS QUE DICE QUERER LLEGAR: " + ("; ".join(resp.get("publicos") or []) or "(no los dijo: propón tú 2)"))
    dest = [d.get("nombre") for d in (resp.get("destinos") or []) if d.get("nombre")]
    if dest:
        L.append("A DÓNDE QUIERE LLEVAR A LA GENTE: " + ", ".join(dest))
    L.append("IDIOMAS DE LA DEMANDA QUE QUIERE MIRAR: " + ("español e inglés" if resp.get("idiomas") == "es+en" else "solo español"))
    L += ["", "Responde SOLO con este JSON:",
          "- \"objetivo\": 1-3 frases en las que el autor se reconozca: qué quiere lograr y qué debe mandar al elegir ideas "
          "(empieza por «Su prioridad…»)",
          "- \"vertientes\": entre 2 y 3 (si el autor nombró públicos, esos). Cada una: \"nombre\" (máx. 28 caracteres), \"resumen\" "
          "(máx. 80 caracteres: a quién va), \"descripcion\" (1-2 frases: para quién son esas ideas, de qué tipo, y a dónde llevan si "
          "encaja), \"n\" (cuántas de las 20 ideas: entre 3 y 6)",
          "- \"general_resumen\": máx. 80 caracteres; \"general_descripcion\": 1-2 frases sobre las ideas abiertas, con base en lo que "
          "más funciona en el nicho" + (" en español y en inglés" if resp.get("idiomas") == "es+en" else ""),
          "- \"busquedas\": 3 búsquedas que la gente escribe en YouTube sobre este tema, en español",
          "- \"busquedas_en\": " + ("3 búsquedas en inglés" if resp.get("idiomas") == "es+en" else "lista vacía (solo español)"),
          "", "Reglas: nada de inventar datos ni cifras; sin enlaces; todo en español; las ideas generales son al menos la mitad."]
    return "\n".join(L)


def _perfil_de_propuesta(d: dict, resp: dict, base: dict, canal: str) -> dict:
    """El JSON de la IA -> un perfil completo (las generales rellenan hasta 20). Lanza si no sale uno válido."""
    vert = []
    for v in (d.get("vertientes") or [])[:MAX_VERTIENTES]:
        if isinstance(v, dict):
            vert.append({"nombre": v.get("nombre"), "resumen": v.get("resumen"), "descripcion": v.get("descripcion"),
                         "n": _entero(v.get("n"), 4, 3, 6)})
    while sum(v["n"] for v in vert) > 12 and any(v["n"] > 3 for v in vert):      # las generales son al menos la mitad
        mayor = max(vert, key=lambda v: v["n"])
        mayor["n"] -= 1
    n_gen = max(1, TOTAL_DEF - sum(v["n"] for v in vert))
    perfil, err = valida({"tema": resp.get("tema") or base.get("tema"), "objetivo": d.get("objetivo"), "vertientes": vert,
                          "general": {"resumen": d.get("general_resumen"), "descripcion": d.get("general_descripcion"), "n": n_gen},
                          "busquedas": d.get("busquedas"), "busquedas_en": d.get("busquedas_en"), "idiomas": resp.get("idiomas"),
                          "destinos": resp.get("destinos"), "datos_propios": resp.get("datos_propios", True), "origen": "asistente"},
                         canal)
    if err:
        raise ValueError("la propuesta de la IA no sirve: " + "; ".join(err))
    return perfil


def parsea_propuesta(texto: str, resp: dict, base: dict, canal: str = "") -> dict:
    from .ideas import _json_de
    d = _json_de(texto)
    if not isinstance(d, dict):
        raise ValueError("la IA no devolvió un objeto")
    return _perfil_de_propuesta(d, resp, base, canal)


def perfil_por_defecto(resp: dict, base: dict, canal: str = "") -> dict:
    """Un perfil de partida hecho por código (si la IA no responde): los públicos que escribió la persona, o uno solo."""
    publicos = (resp.get("publicos") or [])[:MAX_VERTIENTES]
    if not publicos:
        publicos = [_txt(base.get("audiencia"), 80) or "Tu público principal"]
    n_v = 4 if len(publicos) <= 2 else 3
    vert = [{"nombre": p[:28], "resumen": p[:80], "descripcion": f"Ideas pensadas para {p}: lo que más les interesa de {resp.get('tema') or 'tu tema'}.",
             "n": n_v} for p in publicos]
    objetivo = ". ".join(x for x in (OBJETIVOS.get(resp.get("objetivo") or ""), resp.get("objetivo_texto")) if x) or \
        "Hacer crecer el canal con vídeos que la gente busca y quiere ver."
    tema = resp.get("tema") or base.get("tema") or ""
    palabras = [w for w in re.findall(r"[\wáéíóúñü]+", tema.lower()) if len(w) > 3 and w not in ("canal", "sobre", "gaming")][:3]
    busq = [" ".join(palabras)] if palabras else [tema[:50]]
    perfil, err = valida({"tema": tema, "objetivo": objetivo, "vertientes": vert,
                          "general": {"n": max(1, TOTAL_DEF - sum(v["n"] for v in vert))}, "busquedas": busq,
                          "busquedas_en": busq if resp.get("idiomas") == "es+en" else [], "idiomas": resp.get("idiomas"),
                          "destinos": resp.get("destinos"), "datos_propios": resp.get("datos_propios", True), "origen": "partida"},
                         canal)
    return perfil if not err else {**perfil, "errores": err}


def _esquema_propuesta():
    try:
        from pydantic import BaseModel

        class Vertiente(BaseModel):
            nombre: str
            resumen: str
            descripcion: str
            n: int

        class Propuesta(BaseModel):
            objetivo: str
            vertientes: list[Vertiente]
            general_resumen: str
            general_descripcion: str
            busquedas: list[str]
            busquedas_en: list[str]

        return Propuesta
    except Exception:  # noqa: BLE001 — sin pydantic: sin esquema
        return None


def propone(canal: str, resp: dict, cfg, dir_work=None, _llama=None) -> dict:
    """La primera vez: UNA petición a Flash-Lite propone el perfil con lo que contó la persona (no se guarda: lo ajusta y lo guarda
    ella). Si la IA no responde, un perfil de partida por código. Nunca lanza. -> {"ok": True, "perfil", "aviso", "modelo"}"""
    from .util import nombre_modelo
    base = base_del_canal(canal, cfg)
    resp = _respuestas(resp, base)
    modelo, aviso = "", ""
    try:
        from . import ideas as _ideas, uso as _uso
        prompt = prompt_propuesta(resp, base)
        _uso.contexto(job="ideas")
        try:
            llama = _llama or (lambda pr, sy, va: _ideas._llama_gemini(cfg, pr, sy, va, True, "ideas_perfil", esquema=_esquema_propuesta()))
            perfil, modelo = llama(prompt, "Eres estratega de contenido de YouTube. Respondes SOLO con JSON válido.",
                                   lambda t: parsea_propuesta(t, resp, base, canal))
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Perfil de ideas de «{canal}»: {exc}")
        perfil = perfil_por_defecto(resp, base, canal)
        aviso = "La IA no respondió ahora mismo: te dejo un punto de partida para que lo ajustes a mano."
    return {"ok": True, "perfil": perfil, "aviso": aviso, "modelo": nombre_modelo(modelo) if modelo else ""}


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    class Cfg:
        def __init__(self, d):
            self.d = d

        def get_path(self, k, dflt=None):
            cur = self.d
            for parte in k.split("."):
                if not isinstance(cur, dict) or parte not in cur:
                    return dflt
                cur = cur[parte]
            return cur

    canales = {"ejemplo": {"nombre": "Mi canal", "contexto": "Canal de SEO.", "enlaces": {"web": "https://tu-sitio.example"}},
               "gamer": {"nombre": "Canal Gamer", "contexto": "Gaming.", "perfil": {"tema": "Canal Gaming de Mundo Shooter",
                                                                                      "audiencia": "jóvenes de 18 a 35 años", "idioma": "Español latino"},
                          "enlaces": {"youtube": "https://www.youtube.com/@x", "cta_url": "https://tienda.ejemplo.com", "cta_texto": "Mi tienda"}}}
    cfg = Cfg({"ideas": {"canal": "ejemplo", "busquedas": ["tema uno", "tema dos"], "busquedas_en": ["topic one"]}, "videos": {"canales": canales}})

    # ---- el perfil «de config.yaml» (opcional: para quien edita a mano en vez de usar el asistente)
    pj = perfil_config(cfg)
    caso("el perfil de config.yaml: 5 negocio + 5 aprender + 15 generales (español e inglés si hay búsquedas en inglés), con los textos de fábrica",
         [v["id"] for v in pj["vertientes"]] == ["negocio", "aprender"] and [v["n"] for v in pj["vertientes"]] == [5, 5]
         and pj["general"]["n"] == 15 and pj["total"] == 25 and pj["idiomas"] == "es+en" and pj["origen"] == "config"
         and pj["vertientes"][0]["descripcion"] == CASILLA_BASE["negocio"] and pj["busquedas"] == ["tema uno", "tema dos"]
         and pj["busquedas_en"] == ["topic one"] and pj["objetivo"] == OBJETIVO_BASE and pj["datos_propios"], pj["total"])
    cfg_v = Cfg({"ideas": {"mezcla": {"negocio": 4, "crecimiento": 4, "autoridad": 2}, "busquedas": ["x"]}, "videos": {"canales": canales}})
    caso("una mezcla de antes (negocio/crecimiento/autoridad) en config.yaml se ignora; sus búsquedas propias sí valen",
         perfil_config(cfg_v)["total"] == 25 and perfil_config(cfg_v)["busquedas"] == ["x"])
    caso("el canal de `ideas:` es el que diga `ideas.canal`; sin eso, ninguno (todos pasan por el asistente)",
         canal_con_config(cfg) == "ejemplo" and canal_con_config(Cfg({"ideas": {"canal": "gamer"}, "videos": {"canales": canales}})) == "gamer"
         and canal_con_config(Cfg({"videos": {"canales": {"otro": {}}}})) is None and canal_con_config(Cfg({"videos": {"canales": canales}})) is None)

    with tempfile.TemporaryDirectory() as tmp:
        caso("el canal que se arma en config.yaml no necesita asistente; otro canal sin perfil, sí",
             de_canal("ejemplo", cfg, tmp)["origen"] == "config" and de_canal("gamer", cfg, tmp) is None)

        # ---- validar y guardar
        bueno = {"tema": "Canal de gaming de Mundo Shooter", "objetivo": "Hacer crecer el canal y conseguir patrocinios.",
                 "vertientes": [{"nombre": "Tutoriales y guías", "descripcion": "Para jugadores que quieren mejorar: armas, mapas, trucos.", "n": 5},
                                {"nombre": "Momentos épicos", "descripcion": "Para quien quiere entretenerse: partidas y clips.", "n": 5}],
                 "general": {"n": 10}, "busquedas": ["mundo shooter", "ms guía"], "idiomas": "es",
                 "destinos": [{"nombre": "Mi tienda", "url": "https://tienda.ejemplo.com"}], "datos_propios": False}
        pv, err = valida(bueno, "gamer")
        caso("un perfil bueno se valida: ids sacados del nombre, las generales completan, la suma son 20 y no pide inglés si es solo español",
             not err and [v["id"] for v in pv["vertientes"]] == ["tutoriales-y-guias", "momentos-epicos"] and pv["total"] == 20
             and pv["busquedas_en"] == [] and pv["idiomas"] == "es" and pv["datos_propios"] is False and pv["destinos"][0]["url"].startswith("https://"),
             (err, pv["total"]))
        caso("errores claros: sin tema, sin objetivo, sin búsquedas, un público sin descripción, un enlace sin https y una suma fuera de 10-30",
             all(any(k in e for e in valida(m, "x")[1]) for m, k in (
                 ({**bueno, "tema": ""}, "de qué trata"), ({**bueno, "objetivo": "x"}, "qué quieres lograr"),
                 ({**bueno, "busquedas": []}, "al menos una búsqueda"),
                 ({**bueno, "vertientes": [{"nombre": "Solo", "descripcion": "", "n": 5}]}, "a quién va dirigido"),
                 ({**bueno, "destinos": [{"nombre": "x", "url": "tienda.com"}]}, "https://"),
                 ({**bueno, "general": {"n": 25}}, "entre 10 y 30"))))
        caso("como mucho 3 públicos (los demás se descartan) y uno no puede llamarse «general»",
             len(valida({**bueno, "vertientes": [{"nombre": f"Público {i}", "descripcion": "Una descripción larga.", "n": 2} for i in range(5)]}, "x")[0]["vertientes"]) == 3
             and valida({**bueno, "vertientes": [{"nombre": "General", "descripcion": "Una descripción larga.", "n": 2}]}, "x")[0]["vertientes"][0]["id"] != "general")
        caso("con «español e inglés» hace falta al menos una búsqueda en inglés",
             any("inglés" in e for e in valida({**bueno, "idiomas": "es+en"}, "x")[1])
             and not valida({**bueno, "idiomas": "es+en", "busquedas_en": ["ms tips"]}, "x")[1])
        g, err = guarda("gamer", bueno, tmp)
        caso("guardar y leer: el canal ya tiene perfil y no se le vuelve a preguntar; un archivo roto se ignora",
             not err and lee("gamer", tmp)["total"] == 20 and de_canal("gamer", cfg, tmp)["origen"] == "manual"
             and (_ruta("roto", tmp).write_text("{no es json", encoding="utf-8") or lee("roto", tmp) is None), err)
        caso("no se guarda un perfil con errores", guarda("otro", {**bueno, "tema": ""}, tmp)[0] is None and lee("otro", tmp) is None)
        caso("quitar el perfil lo manda a la papelera (no lo borra del todo) y el canal vuelve a pedir el asistente",
             borra("gamer", tmp) and lee("gamer", tmp) is None and len(list((Path(tmp) / "ideas" / "_papelera").glob("perfil_gamer-*.json"))) == 1
             and not borra("gamer", tmp))

        # ---- lo que ya se sabe del canal y los datos propios
        base = base_del_canal("gamer", cfg)
        caso("para la primera vez se rellena con lo del asistente de «Nuevo canal» (tema, audiencia, idioma y a dónde llevar a la gente)",
             base["tema"] == "Canal Gaming de Mundo Shooter" and base["audiencia"].startswith("jóvenes") and base["idioma"] == "Español latino"
             and base["destinos"] == [{"nombre": "Mi tienda", "url": "https://tienda.ejemplo.com"}], base)
        caso("datos propios de YouTube: solo si es el canal conectado (con un solo canal, sí; con varios, si el nombre coincide)",
             sugiere_datos_propios("ejemplo", cfg, "MI CANAL") and not sugiere_datos_propios("gamer", cfg, "MI CANAL")
             and sugiere_datos_propios("gamer", cfg, "Canal Gamer") and not sugiere_datos_propios("gamer", cfg, None)
             and sugiere_datos_propios("solo", Cfg({"videos": {"canales": {"solo": {"nombre": "X"}}}}), None))

        # ---- el asistente
        resp = {"tema": "Canal Gaming de Mundo Shooter", "objetivo": "audiencia", "objetivo_texto": "quiero patrocinios",
                "publicos": ["jugadores nuevos", "jugadores competitivos"], "idiomas": "es", "datos_propios": False}
        r = _respuestas(resp, base)
        pp = prompt_propuesta(r, base)
        caso("el prompt del asistente lleva lo que contó la persona (tema, objetivo, públicos, idioma) y no pide enlaces",
             "Canal Gaming de Mundo Shooter" in pp and "Crecer la audiencia" in pp and "quiero patrocinios" in pp and "jugadores nuevos; jugadores competitivos" in pp
             and "solo español" in pp and "Mi tienda" in pp and "sin enlaces" in pp and "lista vacía" in pp, pp[:300])
        propuesta = json.dumps({"objetivo": "Su prioridad es crecer y conseguir patrocinios.", "vertientes": [
            {"nombre": "Para jugadores nuevos", "resumen": "quienes empiezan en MS", "descripcion": "Guías y consejos para empezar bien en MS.", "n": 6},
            {"nombre": "Para competitivos", "resumen": "quienes quieren subir de rango", "descripcion": "Tácticas avanzadas para ganar partidas.", "n": 6},
            {"nombre": "Entretenimiento", "resumen": "clips y momentos", "descripcion": "Partidas y momentos divertidos para pasar el rato.", "n": 6}],
            "general_resumen": "lo que funciona en MS", "general_descripcion": "Ideas abiertas con base en lo más visto de MS.",
            "busquedas": ["mundo shooter", "ms guía", "ms mejores armas"], "busquedas_en": ["ms tips"], "url": "https://inventada.com"})
        pf = parsea_propuesta(propuesta, r, base, "gamer")
        caso("la propuesta de la IA se vuelve un perfil completo: las generales son al menos la mitad (se rebajan los públicos), suma 20, "
             "solo español (sin búsquedas en inglés) y ninguna URL inventada por la IA",
             pf["total"] == 20 and pf["general"]["n"] >= 8 and sum(v["n"] for v in pf["vertientes"]) <= 12 and pf["busquedas_en"] == []
             and pf["origen"] == "asistente" and pf["datos_propios"] is False and "inventada" not in json.dumps(pf)
             and pf["destinos"] == [{"nombre": "Mi tienda", "url": "https://tienda.ejemplo.com"}], (pf["total"], pf["general"]["n"]))
        for malo, motivo in (("no es json", "JSON roto"), (json.dumps({"objetivo": "x", "vertientes": [], "busquedas": []}), "propuesta vacía")):
            try:
                parsea_propuesta(malo, r, base, "gamer")
                caso(f"una propuesta mala ({motivo}) falla (y se prueba con otro modelo)", False)
            except Exception:  # noqa: BLE001
                caso(f"una propuesta mala ({motivo}) falla (y se prueba con otro modelo)", True)
        visto: list = []

        def llama_ok(pr, sy, va):
            visto.append(pr)
            return va(propuesta), "gemini-3.5-flash-lite"

        rp = propone("gamer", resp, cfg, tmp, llama_ok)
        caso("propone: una petición, con lo que contó la persona, y devuelve el perfil SIN guardarlo (lo ajusta y lo guarda ella)",
             rp["ok"] and rp["perfil"]["origen"] == "asistente" and not rp["aviso"] and len(visto) == 1 and lee("gamer", tmp) is None
             and rp["modelo"], rp.get("aviso"))

        def llama_cae(pr, sy, va):
            raise RuntimeError("503 UNAVAILABLE: high demand")

        rc = propone("gamer", resp, cfg, tmp, llama_cae)
        caso("si la IA no responde: un perfil de partida por código (con los públicos que escribió) y el aviso de que lo ajuste",
             rc["ok"] and rc["perfil"]["origen"] == "partida" and [v["nombre"] for v in rc["perfil"]["vertientes"]] == ["jugadores nuevos", "jugadores competitivos"]
             and rc["perfil"]["total"] == 20 and "ajustes" in rc["aviso"] and not rc["perfil"].get("errores"), rc)
        rs = propone("gamer", {"tema": "Cocina vegana fácil"}, cfg, tmp, llama_cae)
        caso("…y sin escribir ningún público, la partida trae uno solo y una búsqueda sacada del tema",
             rs["perfil"]["vertientes"][0]["nombre"] and rs["perfil"]["busquedas"] and rs["perfil"]["total"] == 20, rs["perfil"])
    return filas
