"""🛡 Nombres en lo que sale en pantalla (el autor, 2026-10-03: «siempre debe estar, cien por ciento se debe confirmar lo que está en las
tarjetas… "BaseNator 5000" [su Medidor 5000]… una validación súper rápida y sencilla: si tiene sentido, se pone; si no tiene
sentido, no se pone esa tarjeta»).

Whisper oye mal los NOMBRES PROPIOS (herramientas, marcas, productos) y el director los copia a las tarjetas tal como salen de la
transcripción. Antes de dibujar nada, cada texto del plan que sale en pantalla se REVISA:
  1. por código, contra los nombres que ya se conocen (`videos.canales.<canal>.nombres_propios`, el vocabulario de Whisper y
     `videos.herramientas_conocidas`): una errata clara («Pulso Wev») se corrige a su escritura exacta;
  2. lo que no está en esa lista lo mira UNA petición a Flash-Lite por vídeo (no toca las 20 diarias de los Flash), con lo que se oye
     alrededor y la lista como glosario: «ok» (no hay nombre dudoso), «corrige» (era un nombre mal oído y sabe cuál es) o «quita»
     (hay un nombre que no se puede confirmar). Las respuestas se guardan (`work/nombres_validados.json`): un texto ya revisado no
     se vuelve a preguntar;
  3. por código se aplica: la corrección entra si se parece a lo que había y la tarjeta que no se pudo confirmar NO sale.
Lo que salga de este paso queda apuntado en `plan["nombres"]` (qué se corrigió y qué se quitó) y el panel lo enseña. Si la IA no
responde, el plan sigue SIN revisar (se reintenta en el siguiente «Reintentar») y solo se quitan, por código, las tarjetas de
herramienta con un nombre claramente sospechoso (mayúsculas en medio de la palabra o letras pegadas a números que no están en la
lista: «BaseNator 5000»).
Los nombres que el autor ESCRIBE en el comentario de una mejora cuentan como confirmados (si pide una tarjeta, sale).
"""
from __future__ import annotations

import json
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

from . import util as _util
from .util import log, nombre_modelo, normalize

VERSION = 1
MAX_LOTE = 60               # textos por petición a Flash-Lite
UMBRAL_ERRATA = 0.88        # una errata clara contra la lista se corrige por código
PARECIDO_MIN = 0.5          # una corrección de la IA tiene que parecerse a lo que había (si no, es otra cosa: se quita)
# lo que cabe en cada sitio (los mismos topes que `director._valida`): una corrección más larga no entra
LIMITES = {("tarjetas", "texto"): 80, ("tarjetas", "extra"): 150, ("palabras_clave", "texto"): 36, ("escenas", "frase"): 36,
           ("ilustraciones", "etiqueta"): 36, ("camara", "termino"): 40, ("camara", "definicion"): 160}
_LOCK = threading.Lock()


def _norma(t: str) -> str:
    return " ".join(normalize(str(t or "")).split())


# ============================================================================================ el glosario
def glosario(cfg, canal: str = "") -> list[dict]:
    """[{nombre, que, origen}]: los nombres CONFIRMADOS. Los propios del canal (con su descripción, de
    `videos.canales.<canal>.nombres_propios`), las `videos.herramientas_conocidas` y el vocabulario de Whisper."""
    out: list[dict] = []
    vistos: set[str] = set()

    def add(nombre, que="", origen=""):
        n = _norma(nombre)
        if n and n not in vistos:
            vistos.add(n)
            out.append({"nombre": str(nombre).strip(), "que": str(que or "").strip(), "origen": origen})

    info = ((cfg.get("videos") or {}).get("canales") or {}).get(canal) or {}
    propios = info.get("nombres_propios") or {}
    if isinstance(propios, dict):
        for k, v in propios.items():
            add(k, v, "propio")
    elif isinstance(propios, list):
        for k in propios:
            add(k, "", "propio")
    # formas que salen MAL en pantalla → su escritura buena (`nombres_alias: {"E-A-T": "E-E-A-T"}`; el autor, 11-oct-2026)
    alias = info.get("nombres_alias") or {}
    if isinstance(alias, dict):
        for malo, bueno in alias.items():
            if not str(malo).strip() or not str(bueno).strip():
                continue
            g = next((x for x in out if _norma(x["nombre"]) == _norma(bueno)), None)
            if g is None:
                add(bueno, "", "propio")
                g = next(x for x in out if _norma(x["nombre"]) == _norma(bueno))
            g.setdefault("alias", []).append(str(malo).strip())
    for k in (cfg.get_path("videos.herramientas_conocidas") or {}):
        add(k, "", "herramienta")
    try:
        from . import transcribe
        for t in transcribe.vocabulario(cfg):
            add(t, "", "vocabulario")
    except Exception:  # noqa: BLE001 — sin el vocabulario de Whisper, la lista de arriba basta
        pass
    return out


def texto_para_director(cfg, canal: str = "") -> str:
    """El bloque con los nombres PROPIOS del autor para el contexto del director ('' si no tiene): que los escriba SIEMPRE así
    (tarjetas, títulos, capítulos, descripción) aunque la transcripción los traiga mal oídos."""
    propios = [g for g in glosario(cfg, canal) if g["origen"] == "propio"]
    if not propios:
        return ""
    lineas = [f"- {g['nombre']}" + (f": {g['que']}" if g["que"] else "")
              + (" (a veces mal oído o escrito como " + ", ".join(f"«{a}»" for a in g["alias"]) + ")" if g.get("alias") else "")
              for g in propios]
    return ("NOMBRES PROPIOS DEL AUTOR (sus herramientas y marcas). Escríbelos SIEMPRE exactamente así —en tarjetas, palabras clave, "
            "títulos, capítulos y descripción—, aunque la transcripción los traiga mal oídos (si algo suena como uno de ellos, es ese):\n"
            + "\n".join(lineas))


# ============================================================================================ errata clara, por código
def canoniza(texto: str, glos: list[dict], umbral: float = UMBRAL_ERRATA) -> tuple[str, list[tuple[str, str]]]:
    """Cambia, dentro de `texto`, cada trozo de 1-4 palabras que se parece MUCHO a un nombre de la lista sin ser él por su
    escritura exacta (y si el texto ENTERO es ese nombre con otra mayúscula o tilde, también). Solo contra los nombres CURADOS
    (los propios del canal y `herramientas_conocidas`: su escritura es la buena); los del vocabulario de Whisper («robots.txt»,
    «sitemap») sirven para confirmar, no para reescribir. -> (texto, [(antes, después)])."""
    toks = str(texto or "").split()
    if not toks:
        return str(texto or ""), []
    nombres = [(g["nombre"], _norma(g["nombre"])) for g in glos
               if g.get("origen") in ("propio", "herramienta") and len(_norma(g["nombre"]).replace(" ", "")) >= 5]
    if len(toks) <= 4 and _norma(texto) in {n for _, n in nombres}:                 # el texto entero ES un nombre de la lista
        exacto = next(c for c, n in nombres if n == _norma(texto))
        return (exacto, [(str(texto), exacto)] if exacto != str(texto).strip() else [])
    alias = {_norma(a): g["nombre"] for g in glos for a in (g.get("alias") or []) if _norma(a) and _norma(a) != _norma(g["nombre"])}
    cambios: list[tuple[str, str]] = []
    i = 0
    salida: list[str] = []
    while i < len(toks):
        mejor = None
        for n in range(min(4, len(toks) - i), 0, -1):
            grupo = " ".join(toks[i:i + n])
            ng = _norma(grupo)
            if not ng:
                continue
            if ng in alias and (grupo.strip(".,;:¿?¡!()\"'«»").isupper() or "-" in grupo):   # un alias solo vale en MAYÚSCULAS o con guion («EAT», no «eat»)
                mejor = (2.0, n, alias[ng])
                break
            for canon, nc in nombres:
                if len(canon.split()) != n or ng == nc:                       # (palabras CONTADAS como se escriben: «Robots.txt» es 1)
                    continue
                r = SequenceMatcher(None, ng, nc).ratio()
                if r >= umbral and (mejor is None or r > mejor[0]):
                    mejor = (r, n, canon)
            if mejor:
                break
        if mejor:
            _r, n, canon = mejor
            ultimo = toks[i + n - 1]
            cola = ultimo[-1] if ultimo and ultimo[-1] in ".,;:?!" else ""
            cambios.append((" ".join(toks[i:i + n]), canon))
            salida.append(canon + cola)
            i += n
        else:
            salida.append(toks[i])
            i += 1
    return " ".join(salida), cambios


def alias_transcripcion(words: list[dict], glos: list[dict]) -> list[tuple[str, str]]:
    """El mismo alias (`nombres_alias`), en la TRANSCRIPCIÓN: Whisper junta «E-E-A-T» en «EAT» (o lo parte en «ea t») y todo lo demás
    —el director, las palabras clave— lo copia (el autor, 11-oct-2026: «dije EEAT y salió EAT»). Volver a escuchar no basta: Whisper a veces
    oye «E-A-T» al repetirlo. Cambia, EN `words`, una palabra en MAYÚSCULAS o con guion («EAT», «E-A-T») o el par partido («ea», «t»)
    por la escritura buena; una palabra normal («eat», «Eat») nunca. -> [(antes, después)]."""
    alias: dict[str, str] = {}
    for g in glos:
        for a in g.get("alias") or []:
            clave = _norma(a).replace(" ", "")
            if clave and str(a).strip() != g["nombre"]:
                alias[clave] = g["nombre"]
    if not alias or not words:
        return []
    cambios: list[tuple[str, str]] = []
    i = 0
    while i < len(words):
        for n in (2, 1):
            if i + n > len(words):
                continue
            crudo = [str(w.get("word", "")).strip() for w in words[i:i + n]]
            limpio = [c.strip(".,;:¿?¡!()\"'«»") for c in crudo]
            clave = _norma(" ".join(limpio)).replace(" ", "")
            canon = alias.get(clave)
            if not canon or not all(limpio):
                continue
            if n == 1 and not (limpio[0].isupper() or "-" in limpio[0]):
                continue                                   # «eat» / «Eat»: una palabra normal
            if limpio == [canon]:
                continue                                   # ya está bien
            cola = crudo[-1][-1] if crudo[-1] and crudo[-1][-1] in ".,;:?!" else ""
            cambios.append((" ".join(crudo), canon + cola))
            words[i:i + n] = [{**words[i], "word": canon + cola, "end": words[i + n - 1].get("end", words[i].get("end"))}]
            break
        i += 1
    return cambios


_CAMEL = re.compile(r"[a-záéíóúñ][A-ZÁÉÍÓÚÑ]")


def sospechoso(texto: str, glos: list[dict]) -> bool:
    """Sin IA: ¿el texto tiene un nombre que PARECE mal oído? Mayúscula en medio de la palabra («BaseNator») CON un número pegado
    o al lado («BaseNator 5000», «BaseNator5000»), y que no sea (ni se parezca mucho a) un nombre de la lista. «YouTube Data API»,
    «iPhone», «H1» o «4K» no: son marcas y términos normales (sin la IA no se puede saber más)."""
    conocidos = [_norma(g["nombre"]) for g in glos]
    nt = _norma(texto)
    if any(c and c in nt for c in conocidos):
        return False
    toks = [tok.strip(".,;:¿?¡!()\"'«»") for tok in str(texto or "").split()]
    for i, t in enumerate(toks):
        if not t or not (_CAMEL.search(t) and not t.isupper()) or re.match(r"^[a-z][A-Z]", t):
            continue                                              # (iPhone, eBay: una minúscula y ya)
        vecinos = [t] + toks[max(0, i - 1):i] + toks[i + 1:i + 2]
        if any(re.search(r"\d", v) for v in vecinos) and not any(
                SequenceMatcher(None, _norma(t), c).ratio() >= 0.8 for c in conocidos if c):
            return True
    return False


# ============================================================================================ qué textos se revisan
def _vale(txt: str) -> bool:
    return len(txt) >= 3 and len(re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]", txt)) >= 2


def items_del_plan(plan: dict) -> list[dict]:
    """Los textos del plan que SALEN en pantalla y pueden llevar un nombre: tarjetas (texto y, en definición/dato, su frase),
    palabras clave, escenas a pantalla completa, la etiqueta de una ilustración (es la frase de su escena de respaldo) y el
    término/definición de una escena «explicativa». Cada uno:
    {id, lista, i, campo, texto, t, tipo}. Las tarjetas de suscríbete/like (texto fijo) no se miran."""
    items: list[dict] = []

    def add(id_, lista, i, campo, txt, t, tipo):
        txt = str(txt or "").strip()
        if _vale(txt):
            items.append({"id": id_, "lista": lista, "i": i, "campo": campo, "texto": txt, "t": float(t or 0), "tipo": tipo})

    for i, c in enumerate(plan.get("tarjetas") or []):
        if str(c.get("tipo")) in ("suscribete", "like"):
            continue
        for campo in ("texto", "extra"):
            add(f"t{i}.{campo}", "tarjetas", i, campo, c.get(campo), c.get("t"), f"tarjeta · {c.get('tipo')}")
    for i, p in enumerate(plan.get("palabras_clave") or []):
        add(f"p{i}.texto", "palabras_clave", i, "texto", p.get("texto"), p.get("t"), "palabra clave")
    for i, e in enumerate(plan.get("escenas") or []):
        add(f"e{i}.frase", "escenas", i, "frase", e.get("frase"), e.get("t"), "escena a pantalla completa")
    for i, il in enumerate(plan.get("ilustraciones") or []):
        add(f"i{i}.etiqueta", "ilustraciones", i, "etiqueta", il.get("etiqueta"), il.get("t"), "ilustración")
    for i, c in enumerate(plan.get("camara") or []):
        if c.get("modo") == "explicativa":
            for campo in ("termino", "definicion"):
                add(f"c{i}.{campo}", "camara", i, campo, c.get(campo), c.get("inicio"), "escena explicativa")
    return items


def contexto_oido(words: list[dict], t: float, antes: float = 5.0, despues: float = 8.0, max_chars: int = 200) -> str:
    """Lo que se OYE en el vídeo alrededor del segundo `t` (para que el verificador vea cómo sonaba el nombre)."""
    cerca = []
    for w in words or []:
        try:
            s = float(w.get("start", 0))
        except (TypeError, ValueError):
            continue
        if t - antes <= s <= t + despues:
            cerca.append(str(w.get("word", "")).strip())
    txt = re.sub(r"\s+", " ", " ".join(cerca)).strip()
    return txt[:max_chars] + ("…" if len(txt) > max_chars else "")


# ============================================================================================ la caché
def _ruta_cache(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work) / "nombres_validados.json"
    from . import config
    return config.paths()["work"] / "nombres_validados.json"


def _huella(glos: list[dict]) -> str:
    import hashlib
    return hashlib.sha1("|".join(sorted(_norma(g["nombre"]) for g in glos)).encode("utf-8")).hexdigest()[:12]


def _lee_cache(glos: list[dict], dir_work=None) -> dict:
    """Los veredictos de antes: valen mientras la lista de nombres confirmados no cambie (si cambia, se vuelve a preguntar)."""
    try:
        d = json.loads(_ruta_cache(dir_work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if d.get("version") != VERSION or d.get("lista") != _huella(glos):
        return {}
    return d.get("items") or {}


def _guarda_cache(items: dict, glos: list[dict], dir_work=None) -> None:
    p = _ruta_cache(dir_work)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": VERSION, "lista": _huella(glos), "items": items}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        log(f"AVISO: no se pudo guardar la caché de nombres ({exc}).")


# ============================================================================================ la petición a Flash-Lite
_SYSTEM = """Eres el verificador de NOMBRES del canal de YouTube "{canal}": cuidas que ningún nombre dudoso salga en pantalla.
Respondes SOLO con JSON válido, sin markdown."""

_PROMPT = """Contexto del canal: {contexto}

Este vídeo ya tiene su plan de textos en pantalla (tarjetas, palabras clave, escenas…). Salen de una transcripción automática que a
veces OYE MAL los nombres propios: herramientas, productos, marcas, empresas, personas, funciones o términos técnicos en inglés
(«BaseNator» por «Medidor», «Lobable» por «Lovable», «Shares Console» por «Search Console»). Si un nombre mal oído llega a la
pantalla, el vídeo pierde credibilidad: NINGÚN nombre dudoso puede salir.

NOMBRES CONFIRMADOS (escríbelos EXACTAMENTE así; si un texto SUENA como uno de ellos, aunque venga mal escrito, es ese):
{glosario}

TEXTOS A REVISAR, cada uno con lo que se oye en el vídeo justo en ese momento (así ves cómo sonaba):
{items}

Para CADA texto devuelve un veredicto:
- "ok": no hay ningún nombre propio dudoso (los conceptos, frases y palabras normales son SIEMPRE "ok": «Tráfico cualificado»,
  «Intención de compra», «Core Web Vitals»), o hay un nombre que conoces con seguridad y está bien escrito.
- "corrige": hay un nombre mal oído o mal escrito y sabes con seguridad cuál es (porque está en la lista de confirmados o porque es
  una herramienta, marca o término real que conoces): "texto" = el MISMO texto cambiando SOLO el nombre. Respeta las mayúsculas del
  original (si está todo en mayúsculas, todo en mayúsculas).
- "quita": hay un nombre que NO puedes confirmar: no está en la lista, no lo conoces o parece un error de transcripción sin solución
  segura. Esa tarjeta NO saldrá.
Reglas: no cambies nada que no sea el nombre (ni gramática ni estilo); ante la duda entre «ok» y «quita» con un nombre propio,
«quita»; no inventes herramientas ni confirmes un nombre solo porque suena creíble.
Responde SOLO con este JSON, con UN veredicto por cada id y en el mismo orden:
{{"veredictos": [{{"id": "t3.texto", "veredicto": "ok", "texto": "", "motivo": "una frase corta"}}]}}"""


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def arma_prompt(items: list[dict], words: list[dict], glos: list[dict], canal: str, contexto: str) -> str:
    lista = "\n".join(f"- {g['nombre']}" + (f": {g['que']}" if g["que"] else "") for g in glos) or "- (ninguno)"
    filas = "\n".join(f"[{it['id']} · {it['tipo']} · {_mmss(it['t'])}] «{it['texto']}» — se oye: «{contexto_oido(words, it['t'])}»"
                      for it in items)
    return _PROMPT.format(contexto=contexto or "canal de YouTube", glosario=lista, items=filas)


def parsea(texto: str, ids: list[str]) -> dict[str, dict]:
    """El JSON de la IA -> {id: {"v": ok|corrige|quita, "texto", "motivo"}}. Lanza si falta alguno de `ids` o un veredicto no vale:
    así la cadena prueba con el siguiente Flash-Lite."""
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    d = json.loads(t)
    lista = d.get("veredictos") if isinstance(d, dict) else d
    if not isinstance(lista, list):
        raise ValueError("la IA no devolvió veredictos")
    out: dict[str, dict] = {}
    for x in lista:
        if not isinstance(x, dict) or x.get("id") not in ids:
            continue
        v = str(x.get("veredicto") or "").strip().lower()
        if v not in ("ok", "corrige", "quita"):
            raise ValueError(f"veredicto no válido para {x.get('id')}")
        out[x["id"]] = {"v": v, "texto": re.sub(r"\s+", " ", str(x.get("texto") or "")).strip(),
                        "motivo": re.sub(r"\s+", " ", str(x.get("motivo") or "")).strip()[:140]}
    faltan = [i for i in ids if i not in out]
    if faltan:
        raise ValueError(f"la IA no dio veredicto para: {', '.join(faltan[:5])}")
    return out


_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from typing import Literal

            from pydantic import BaseModel

            class Veredicto(BaseModel):
                id: str
                veredicto: Literal["ok", "corrige", "quita"]
                texto: str = ""
                motivo: str = ""

            class Revision(BaseModel):
                veredictos: list[Veredicto]

            _ESQUEMA = Revision
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQUEMA = False
    return _ESQUEMA or None


def _pregunta(items: list[dict], words: list[dict], glos: list[dict], canal: str, contexto: str, cfg, jid: str,
              _gemini=None, _modelos=None) -> dict[str, dict]:
    """UNA petición a Flash-Lite (por lote de `MAX_LOTE` textos). Lanza si no hay clave o nadie responde."""
    if _gemini is None:
        from .director import _gemini as _abre
        g = _abre(cfg)
        if not g:
            raise RuntimeError("no hay clave de Gemini (o la IA está apagada)")
        _gemini = g[:2]
    client, types = _gemini
    if _modelos is None:
        from . import modelos as _m
        _modelos = _m.cadena_lite(cfg, client)
    from . import uso as _uso
    out: dict[str, dict] = {}
    for k in range(0, len(items), MAX_LOTE):
        lote = items[k:k + MAX_LOTE]
        ids = [it["id"] for it in lote]
        prompt = arma_prompt(lote, words, glos, canal, contexto)
        _uso.contexto(job=jid or None)
        try:
            res, _modelo, _gasto = _util.call_gemini_cadena(
                client, types, _modelos, prompt, system=_SYSTEM.format(canal=canal or "de YouTube"),
                valida=lambda t, ids=ids: parsea(t, ids), reintento_s=20.0, pasadas=2, timeout_s=75.0, esquema=_esquema(),
                etiqueta="nombres")
        finally:
            _uso.contexto(job=None)
        out.update(res)
    return out


# ============================================================================================ aplicar
def _igual_caso(original: str, nuevo: str) -> str:
    """El texto corregido con las mayúsculas del original: si estaba TODO en mayúsculas, el nuevo también."""
    return nuevo.upper() if original and original == original.upper() and any(c.isalpha() for c in original) else nuevo


def revisa(plan: dict, words: list[dict], cfg, canal: str = "", dir_work=None, comentario: str = "", jid: str = "",
           _gemini=None, _modelos=None) -> dict:
    """Revisa los nombres de todo lo que sale en pantalla (ver arriba), CORRIGE y QUITA sobre `plan` y deja el resumen en
    `plan["nombres"]` = {"revisado": bool, "corregidas": [...], "quitadas": [...], "motivo": ""}. Nunca lanza."""
    glos = glosario(cfg, canal)
    res: dict = {"revisado": False, "corregidas": [], "quitadas": [], "motivo": "", "version": VERSION}
    items = items_del_plan(plan)
    try:
        info = ((cfg.get("videos") or {}).get("canales") or {}).get(canal) or {}
        nombre_canal = str(info.get("nombre") or canal or "de YouTube")
        contexto = str(info.get("contexto") or "").strip()
    except Exception:  # noqa: BLE001
        nombre_canal, contexto = canal or "de YouTube", ""
    if not items:
        res["revisado"] = True
        plan["nombres"] = res
        return res

    veredictos: dict[str, dict] = {}          # id -> {"v", "texto", "motivo", "origen"}
    pendientes: list[dict] = []
    cache = _lee_cache(glos, dir_work)
    comentario_n = _norma(comentario)
    for it in items:
        txt, n = it["texto"], _norma(it["texto"])
        nuevo, cambios = canoniza(txt, glos)
        if cambios:                                                            # errata clara contra la lista
            veredictos[it["id"]] = {"v": "corrige", "texto": _igual_caso(txt, nuevo), "motivo": "estaba en tu lista de nombres",
                                    "origen": "lista"}
        elif any(n == _norma(g["nombre"]) for g in glos):                      # es un nombre de la lista, bien escrito
            veredictos[it["id"]] = {"v": "ok", "texto": "", "motivo": "", "origen": "lista"}
        elif comentario_n and n and n in comentario_n:                         # lo escribió el autor en su comentario
            veredictos[it["id"]] = {"v": "ok", "texto": "", "motivo": "lo escribiste tú", "origen": "comentario"}
        elif n in cache:
            veredictos[it["id"]] = {**cache[n], "origen": "caché"}
        else:
            pendientes.append(it)

    if pendientes:
        try:
            nuevos = _pregunta(pendientes, words, glos, nombre_canal, contexto, cfg, jid, _gemini=_gemini, _modelos=_modelos)
            guardar = dict(cache)
            for it in pendientes:
                v = {**nuevos[it["id"]], "origen": "IA"}
                veredictos[it["id"]] = v
                guardar[_norma(it["texto"])] = {k: v[k] for k in ("v", "texto", "motivo")}
            with _LOCK:
                _guarda_cache(guardar, glos, dir_work)
            res["revisado"] = True
        except Exception as exc:  # noqa: BLE001 — sin IA: solo lo claramente sospechoso (en tarjetas de herramienta)
            res["motivo"] = str(exc).replace("\n", " ")[:160]
            for it in pendientes:
                if it["tipo"] == "tarjeta · herramienta" and it["campo"] == "texto" and sospechoso(it["texto"], glos):
                    veredictos[it["id"]] = {"v": "quita", "texto": "", "origen": "código",
                                            "motivo": "no se pudo confirmar (la IA no respondió) y parece un nombre mal oído"}
            log(f"AVISO: no se pudieron revisar los nombres con la IA ({res['motivo']}): se reintenta en el próximo «Reintentar».")
    else:
        res["revisado"] = True

    # ---- aplicar: correcciones primero (no mueven índices), luego lo que se quita
    quitar: dict[str, set[int]] = {"tarjetas": set(), "palabras_clave": set(), "escenas": set(), "ilustraciones": set()}
    for it in items:
        v = veredictos.get(it["id"])
        if not v or v["v"] == "ok":
            continue
        lista, i, campo = it["lista"], it["i"], it["campo"]
        elem = plan[lista][i]
        nuevo = _igual_caso(it["texto"], v.get("texto") or "")
        if v["v"] == "corrige":
            if nuevo and nuevo == it["texto"]:
                continue                                          # «corrige» sin cambiar nada = está bien
            parecido = SequenceMatcher(None, _norma(it["texto"]), _norma(nuevo)).ratio() if nuevo else 0.0
            if (not nuevo or len(nuevo) > LIMITES.get((lista, campo), 150)
                    or (parecido < PARECIDO_MIN and v.get("origen") != "lista")):
                v = {**v, "v": "quita", "motivo": v.get("motivo") or "la corrección no cuadra con lo que se oyó"}
            else:
                elem[campo] = nuevo
                res["corregidas"].append({"antes": it["texto"], "despues": nuevo, "tipo": it["tipo"], "t": round(it["t"], 1),
                                          "origen": v.get("origen", "")})
                continue
        # quita
        if lista == "camara":
            elem["modo"] = "cara"                                            # una escena «explicativa» sin su término sale como «cara»
            elem.pop("termino", None)
            elem.pop("definicion", None)
        else:
            quitar[lista].add(i)
        res["quitadas"].append({"texto": it["texto"], "tipo": it["tipo"], "t": round(it["t"], 1),
                                "motivo": v.get("motivo") or "no se pudo confirmar el nombre", "origen": v.get("origen", "")})
    for lista, indices in quitar.items():
        if indices:
            plan[lista] = [x for j, x in enumerate(plan.get(lista) or []) if j not in indices]
    # una tarjeta cuyo `texto` o `extra` se quitó pudo contarse dos veces: sin repetir
    vistos: set[tuple] = set()
    res["quitadas"] = [q for q in res["quitadas"] if not ((q["texto"], q["t"]) in vistos or vistos.add((q["texto"], q["t"])))]
    plan["nombres"] = res
    if res["corregidas"] or res["quitadas"] or not res["revisado"]:
        log("🛡 Nombres de lo que sale en pantalla"
            + (f": {len(res['corregidas'])} corregido(s) " + ", ".join(f"«{c['antes']}» → «{c['despues']}»" for c in res["corregidas"][:6])
               if res["corregidas"] else "")
            + (f"{';' if res['corregidas'] else ':'} {len(res['quitadas'])} QUITADO(s) por no poder confirmarlos "
               + ", ".join(f"«{q['texto']}»" for q in res["quitadas"][:6]) if res["quitadas"] else "")
            + ("" if res["revisado"] else " (SIN revisar con la IA)") + ".")
    else:
        log(f"🛡 Nombres de lo que sale en pantalla: {len(items)} texto(s) revisados, todos bien.")
    return res


# ============================================================================================ pruebas
def prueba() -> list[dict]:
    """Sin red ni Gemini (uno falso): el glosario, las erratas claras por código, qué textos se revisan, el prompt (con lo que se
    oye y la lista), el parseo estricto, aplicar (corregir, quitar, degradar una escena «explicativa»), la caché, lo que escribe el
    autor en su comentario y qué pasa si la IA no responde."""
    import shutil
    import tempfile
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    class _Cfg:
        def __init__(self, extra=None):
            self.d = {"videos": {"canales": {"ejemplo": {"nombre": "Mi canal", "contexto": "Canal de SEO.", "nombres_propios": {
                "Medidor 5000": "su herramienta que compara una página con la competencia",
                "Pulso Web": "su panel de Search Console y Analytics", "Velocimetro Web": "velocidad web",
                "E-E-A-T": "los factores de calidad de Google (Experiencia, Pericia, Autoridad, Confianza)"},
                "nombres_alias": {"E-A-T": "E-E-A-T", "EAT": "E-E-A-T", "EEAT": "E-E-A-T"}}}}}

        def get(self, k, d=None):
            return self.d.get(k, d)

        def get_path(self, k, d=None):
            return {"videos.herramientas_conocidas": {"Claude": "https://claude.ai", "Google Search Console": "x"},
                    "transcribe.initial_prompt": "Canal de SEO. Términos: SEO, Vibe Coding, Cursor, Vercel."}.get(k, d)

    cfg = _Cfg()
    glos = glosario(cfg, "ejemplo")
    nombres = [g["nombre"] for g in glos]
    r("el glosario junta los nombres propios del canal (con su descripción), las herramientas conocidas y el vocabulario de Whisper",
      "Medidor 5000" in nombres and "Claude" in nombres and "Vibe Coding" in nombres
      and next(g for g in glos if g["nombre"] == "Medidor 5000")["que"].startswith("su herramienta")
      and next(g for g in glos if g["nombre"] == "Medidor 5000")["origen"] == "propio", nombres)
    r("para el director: solo los nombres PROPIOS del autor, con lo que son, y la orden de escribirlos siempre así",
      "Medidor 5000: su herramienta" in texto_para_director(cfg, "ejemplo") and "Claude" not in texto_para_director(cfg, "ejemplo")
      and texto_para_director(cfg, "otro-canal") == "")
    n1, c1 = canoniza("Pulso Wev nuevo", glos)
    n2, c2 = canoniza("medidor 5000", glos)
    n3, c3 = canoniza("Mide con Velocimetro Web. Y luego Index.", glos)
    r("una errata CLARA contra la lista se corrige por código («Pulso Wev» -> «Pulso Web»), un texto que ya ES el nombre con "
      "otra mayúscula se deja con la exacta, y lo que está bien no se toca",
      n1 == "Pulso Web nuevo" and c1 == [("Pulso Wev", "Pulso Web")] and n2 == "Medidor 5000" and c2
      and n3 == "Mide con Velocimetro Web. Y luego Index." and c3 == [], (n1, n2, n3))
    n4, c4 = canoniza("Basenator 5000", glos)
    r("lo que se parece pero NO claramente («Basenator 5000» ~ «Medidor 5000») no se toca por código: lo decide la IA",
      c4 == [] and n4 == "Basenator 5000", (n4, c4))
    r("«sospechoso» (sin IA): mayúscula en medio de la palabra CON un número al lado y que no está en la lista; una marca de la "
      "lista, «YouTube Data API», «iPhone 15», «H1» o una frase normal, no (salió de sus planes reales: YouTube salía «sospechoso»)",
      sospechoso("BaseNator 5000", glos) and sospechoso("BaseNator5000", glos) and not sospechoso("Search Console", glos)
      and not sospechoso("Tráfico cualificado", glos) and not sospechoso("Medidor 5000", glos)
      and not sospechoso("YouTube Data API", glos) and not sospechoso("YouTube", glos) and not sospechoso("iPhone 15", glos)
      and not sospechoso("H1", glos) and not sospechoso("WordPress", [{"nombre": "WordPress", "que": "", "origen": "herramienta"}]))
    n5, c5 = canoniza("Sitemap y Robots.txt", glos)
    n6, c6 = canoniza("Robots.txt", glos)
    r("lo curado se corrige, lo del vocabulario de Whisper no se reescribe: «Sitemap y Robots.txt» queda igual (antes perdía la «y») y "
      "«Robots.txt» no cambia de mayúscula", c5 == [] and n5 == "Sitemap y Robots.txt" and c6 == [] and n6 == "Robots.txt", (n5, n6))

    # alias (el autor, 11-oct-2026: «dije EEAT y salió E-A-T»): lo que sale en pantalla con la forma mala se cambia por la buena
    na1, ca1 = canoniza("PARÁMETROS DE E-A-T", glos)
    na2, ca2 = canoniza("Fase 2: Evaluación E-A-T", glos)
    na3, ca3 = canoniza("EAT", glos)
    na4, ca4 = canoniza("PARÁMETROS DE E-E-A-T", glos)
    na5, ca5 = canoniza("Eat healthy y great", glos)
    r("un alias corrige «E-A-T» y «EAT» a «E-E-A-T» en lo que sale en pantalla (aunque sean 3 letras: la errata por parecido no llega ahí)",
      na1 == "PARÁMETROS DE E-E-A-T" and ca1 == [("E-A-T", "E-E-A-T")] and na2 == "Fase 2: Evaluación E-E-A-T" and na3 == "E-E-A-T", (na1, na2, na3))
    r("…lo que ya está bien no se toca, y una palabra normal en minúsculas o en medio de otra («Eat», «great») tampoco",
      na4 == "PARÁMETROS DE E-E-A-T" and ca4 == [] and na5 == "Eat healthy y great" and ca5 == [], (na4, na5, ca5))
    r("el director lee el alias («a veces mal oído o escrito como «E-A-T»…») para escribirlo siempre bien",
      "E-E-A-T: los factores de calidad de Google" in texto_para_director(cfg, "ejemplo") and "«E-A-T»" in texto_para_director(cfg, "ejemplo"),
      texto_para_director(cfg, "ejemplo"))
    pal = [{"word": "parámetros", "start": 194.2, "end": 194.9}, {"word": "de", "start": 194.9, "end": 195.2}, {"word": "EAT", "start": 195.2, "end": 196.3},
           {"word": "que", "start": 196.5, "end": 196.7}, {"word": "generales", "start": 310.9, "end": 311.5}, {"word": "de", "start": 311.6, "end": 311.7},
           {"word": "ea", "start": 311.7, "end": 312.0}, {"word": "t,", "start": 312.0, "end": 312.2}, {"word": "como", "start": 312.4, "end": 312.6},
           {"word": "Eat", "start": 400.0, "end": 400.2}, {"word": "eat", "start": 401.0, "end": 401.2}, {"word": "E-E-A-T", "start": 402.0, "end": 402.8}]
    camb = alias_transcripcion(pal, glos)
    textos = [w["word"] for w in pal]
    r("la transcripción: «EAT» (195,2) y el par partido «ea t,» (311,7) pasan a «E-E-A-T» y «E-E-A-T,» (con su coma); «Eat», «eat» y el que ya está bien, no",
      textos == ["parámetros", "de", "E-E-A-T", "que", "generales", "de", "E-E-A-T,", "como", "Eat", "eat", "E-E-A-T"]
      and camb == [("EAT", "E-E-A-T"), ("ea t,", "E-E-A-T,")] and pal[2]["start"] == 195.2 and pal[2]["end"] == 196.3 and pal[6]["end"] == 312.2, (textos, camb))
    r("…y sin alias en el canal no toca nada", alias_transcripcion([{"word": "EAT", "start": 1.0, "end": 1.2}], glosario(cfg, "otro-canal")) == [])

    plan_a = {"tarjetas": [{"t": 10.0, "tipo": "punto", "texto": "Evaluación E-A-T", "numero": 2}],
              "palabras_clave": [{"t": 20.0, "texto": "PARÁMETROS DE E-A-T", "importancia": 3}], "escenas": [], "ilustraciones": [], "camara": []}
    res_a = revisa(plan_a, [], cfg, "ejemplo", None, _gemini=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no debería preguntar")))
    r("en el plan: «Evaluación E-A-T» y «PARÁMETROS DE E-A-T» se corrigen por la lista, sin preguntarle nada a la IA",
      plan_a["tarjetas"][0]["texto"] == "Evaluación E-E-A-T" and plan_a["palabras_clave"][0]["texto"] == "PARÁMETROS DE E-E-A-T"
      and len(res_a["corregidas"]) == 2 and res_a["revisado"], (plan_a, res_a))

    plan = {"tarjetas": [{"t": 10.0, "tipo": "punto", "texto": "Tráfico cualificado", "numero": 1},
                         {"t": 20.0, "tipo": "herramienta", "texto": "BaseNator 5000"},
                         {"t": 30.0, "tipo": "herramienta", "texto": "Google Search Console"},
                         {"t": 40.0, "tipo": "dato", "texto": "73%", "extra": "de los clics van a los primeros resultados"},
                         {"t": 50.0, "tipo": "suscribete", "texto": "Suscríbete"},
                         {"t": 60.0, "tipo": "herramienta", "texto": "Lobable"}],
            "palabras_clave": [{"t": 21.0, "texto": "USA BASENATOR 5000", "importancia": 3}],
            "escenas": [{"t": 70.0, "tipo": "reloj", "frase": "EL SEO TARDA", "duracion": 5.0}],
            "ilustraciones": [{"t": 75.0, "tipo": "lupa", "etiqueta": "Mide con Zorbatron", "duracion": 3.5}],
            "camara": [{"inicio": 0.0, "fin": 15.0, "modo": "cara"},
                       {"inicio": 80.0, "fin": 100.0, "modo": "explicativa", "termino": "Crawl Budjet", "definicion": "Lo que Google rastrea."}]}
    items = items_del_plan(plan)
    ids = [it["id"] for it in items]
    r("qué se revisa: textos de tarjetas (y la frase de definición/dato), palabras clave, escenas, la etiqueta de una ilustración y el "
      "término de una «explicativa»; no el suscríbete ni una cifra suelta",
      "t1.texto" in ids and "t3.extra" in ids and "p0.texto" in ids and "e0.frase" in ids and "c1.termino" in ids and "c1.definicion" in ids
      and "i0.etiqueta" in ids and not any(i.startswith("t4") for i in ids) and "t3.texto" not in ids, ids)
    words = [{"word": "la", "start": 18.0, "end": 18.2}, {"word": "herramienta", "start": 19.0, "end": 19.5},
             {"word": "medidor", "start": 20.1, "end": 20.6}, {"word": "cinco", "start": 20.6, "end": 20.8},
             {"word": "mil", "start": 20.8, "end": 21.0}, {"word": "lejano", "start": 99.0, "end": 99.5}]
    p = arma_prompt(items[:3], words, glos, "Mi canal", "Canal de SEO.")
    r("el prompt lleva la lista confirmada con lo que es cada nombre, cada texto con lo que se OYE a su alrededor y las 3 salidas",
      "- Medidor 5000: su herramienta que compara" in p and "[t1.texto · tarjeta · herramienta · 0:20] «BaseNator 5000» — se oye: «la herramienta "
      "medidor cinco mil»" in p and '"quita"' in p and "lejano" not in p, p[-1300:])
    bien = json.dumps({"veredictos": [{"id": i, "veredicto": "ok"} for i in ids]})
    r("parsear: un veredicto por texto; si falta alguno o uno no vale, lanza (la cadena prueba otro modelo)",
      len(parsea(bien, ids)) == len(ids) and all(v["v"] == "ok" for v in parsea(bien, ids).values()), "")
    for caso, malo in (("falta un veredicto", json.dumps({"veredictos": [{"id": ids[0], "veredicto": "ok"}]})),
                       ("un veredicto inventado", json.dumps({"veredictos": [{"id": i, "veredicto": "quizá"} for i in ids]}))):
        try:
            parsea(malo, ids)
            r(f"parsear: {caso} lanza", False)
        except ValueError:
            r(f"parsear: {caso} lanza", True)

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-nombres-"))
    try:
        llamadas: list = []
        vered = {"t1.texto": {"veredicto": "corrige", "texto": "Medidor 5000", "motivo": "suena a tu herramienta"},
                 "t5.texto": {"veredicto": "corrige", "texto": "Lovable", "motivo": "herramienta real"},
                 "p0.texto": {"veredicto": "corrige", "texto": "Usa Medidor 5000"},
                 "c1.termino": {"veredicto": "quita", "motivo": "no lo conozco"},
                 "i0.etiqueta": {"veredicto": "quita", "motivo": "no existe esa herramienta"}}

        def falso(client, types, modelos, prompt, system=None, valida=None, etiqueta="", **kw):
            llamadas.append((prompt, etiqueta, modelos))
            idsq = re.findall(r"\[([tpeci]\d+\.\w+) ·", prompt)
            return valida(json.dumps({"veredictos": [{"id": i, **vered.get(i, {"veredicto": "ok"})} for i in idsq]})), modelos[0], {}

        real = _util.call_gemini_cadena
        _util.call_gemini_cadena = falso
        try:
            import copy
            p1 = copy.deepcopy(plan)
            res = revisa(p1, words, cfg, "ejemplo", tmp, jid="x--pro", _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"])
        finally:
            _util.call_gemini_cadena = real
        textos = [t["texto"] for t in p1["tarjetas"]]
        r("aplicar: «BaseNator 5000» sale como «Medidor 5000» (por la IA, sabiendo tu lista) y la palabra clave en mayúsculas "
          "conserva sus mayúsculas",
          "Medidor 5000" in textos and "BaseNator 5000" not in textos and p1["palabras_clave"][0]["texto"] == "USA MEDIDOR 5000", textos)
        r("aplicar: «Lobable» -> «Lovable» (herramienta real que la IA conoce)", "Lovable" in textos and "Lobable" not in textos)
        r("aplicar: lo que la IA no puede confirmar («Crawl Budjet» en una escena «explicativa») se quita y esa escena sale como «cara»",
          p1["camara"][1]["modo"] == "cara" and "termino" not in p1["camara"][1] and "definicion" not in p1["camara"][1]
          and any(q["texto"] == "Crawl Budjet" for q in res["quitadas"]), res["quitadas"])
        r("aplicar: la etiqueta de una ilustración con un nombre que no se confirma («Zorbatron») quita esa ilustración",
          p1["ilustraciones"] == [] and any(q["texto"] == "Mide con Zorbatron" for q in res["quitadas"]), (p1["ilustraciones"], res["quitadas"]))
        r("va a Flash-Lite, UNA sola petición para todo el vídeo, apuntada en «Uso» como «nombres»",
          len(llamadas) == 1 and llamadas[0][1] == "nombres" and llamadas[0][2] == ["gemini-3.5-flash-lite"], [(c[1], c[2]) for c in llamadas])
        r("el resumen apunta qué se corrigió y qué se quitó (y que SÍ se revisó con la IA)",
          res["revisado"] is True and plan_ok(p1) and len(res["corregidas"]) == 3 and len(res["quitadas"]) == 2
          and p1["nombres"] is res, res)
        p1b = copy.deepcopy(plan)
        antes_llamadas = len(llamadas)
        _util.call_gemini_cadena = falso
        try:
            res2 = revisa(p1b, words, cfg, "ejemplo", tmp, jid="x--pro", _gemini=(object(), object()), _modelos=["m"])
        finally:
            _util.call_gemini_cadena = real
        r("la caché: el mismo texto ya revisado NO se vuelve a preguntar (y da el mismo resultado)",
          len(llamadas) == antes_llamadas and [t["texto"] for t in p1b["tarjetas"]] == textos and res2["revisado"], len(llamadas))
        # la lista cambia (p. ej. el autor añade un nombre): la caché vieja ya no vale
        cfg2 = _Cfg()
        cfg2.d["videos"]["canales"]["ejemplo"]["nombres_propios"]["Mapa Local"] = "mapa de calor"
        _util.call_gemini_cadena = falso
        try:
            revisa(copy.deepcopy(plan), words, cfg2, "ejemplo", tmp, _gemini=(object(), object()), _modelos=["m"])
        finally:
            _util.call_gemini_cadena = real
        r("si cambia la lista de nombres confirmados, se vuelve a preguntar (la caché vieja ya no vale)", len(llamadas) == antes_llamadas + 1)

        # sin IA: no se revisa, y solo se quita lo CLARAMENTE sospechoso de las tarjetas de herramienta
        def cae(*a, **k):
            raise RuntimeError("503 UNAVAILABLE: high demand")

        p2 = copy.deepcopy(plan)
        _util.call_gemini_cadena = cae
        try:
            res3 = revisa(p2, words, cfg, "ejemplo", tempfile.mkdtemp(dir=tmp), _gemini=(object(), object()), _modelos=["m"])
        finally:
            _util.call_gemini_cadena = real
        t2 = [t["texto"] for t in p2["tarjetas"]]
        r("sin IA: queda marcado «sin revisar» (se reintenta), «BaseNator 5000» (claramente sospechoso) se quita, y lo demás se queda",
          res3["revisado"] is False and "503" in res3["motivo"] and "BaseNator 5000" not in t2 and "Lobable" in t2
          and "Google Search Console" in t2 and len(res3["quitadas"]) == 1, (res3, t2))

        # lo que el autor escribe en el comentario de una mejora cuenta como confirmado
        p3 = {"tarjetas": [{"t": 5.0, "tipo": "herramienta", "texto": "Windsurf"}], "palabras_clave": [], "escenas": [], "camara": []}
        vered["t0.texto"] = {"veredicto": "quita", "motivo": "no lo conozco"}
        _util.call_gemini_cadena = falso
        try:
            revisa(p3, words, cfg, "ejemplo", tempfile.mkdtemp(dir=tmp), comentario="pon la tarjeta de Windsurf cuando lo nombro",
                   _gemini=(object(), object()), _modelos=["m"])
        finally:
            _util.call_gemini_cadena = real
        r("un nombre que el autor ESCRIBE en su comentario cuenta como confirmado (si pide la tarjeta, sale)",
          p3["tarjetas"] and p3["nombres"]["quitadas"] == [], p3)
        # «corrige» sin cambiar nada = está bien; y una corrección que no CABE donde va (36 letras en una palabra clave) se descarta
        vered.clear()
        vered.update({"t0.texto": {"veredicto": "corrige", "texto": "Windsurf"},
                      "p0.texto": {"veredicto": "corrige", "texto": "USA WINDSURF PARA TODO LO QUE HAGAS EN TU WEB HOY"}})
        p4 = {"tarjetas": [{"t": 5.0, "tipo": "herramienta", "texto": "Windsurf"}],
              "palabras_clave": [{"t": 6.0, "texto": "USA WINDSURF", "importancia": 2}], "escenas": [], "camara": []}
        _util.call_gemini_cadena = falso
        try:
            res4 = revisa(p4, words, cfg, "ejemplo", tempfile.mkdtemp(dir=tmp), _gemini=(object(), object()), _modelos=["m"])
        finally:
            _util.call_gemini_cadena = real
        r("«corrige» sin cambiar nada es «ok» (la tarjeta se queda) y una corrección que no CABE donde va se descarta quitando el texto",
          [t["texto"] for t in p4["tarjetas"]] == ["Windsurf"] and p4["palabras_clave"] == [] and len(res4["quitadas"]) == 1
          and not res4["corregidas"], (p4, res4))
        r("un plan sin textos que revisar no pregunta nada y queda «revisado»",
          revisa({"tarjetas": []}, [], cfg, "ejemplo", tmp)["revisado"] is True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def plan_ok(plan: dict) -> bool:
    """(prueba) el plan sigue siendo válido tras revisar: listas de siempre y sin tarjetas vacías."""
    return all(isinstance(plan.get(k), list) for k in ("tarjetas", "palabras_clave", "escenas", "camara")) \
        and all(str(t.get("texto") or "").strip() for t in plan["tarjetas"])


if __name__ == "__main__":
    import sys
    filas_ = prueba()
    for f_ in filas_:
        print(("OK   " if f_["ok"] else "FALLA") + " " + f_["caso"] + ("" if f_["ok"] else f"  -> {f_['detalle']}"))
    sys.exit(1 if any(not f_["ok"] for f_ in filas_) else 0)
