"""Vídeos HORIZONTALES largos por TEMÁTICA.

La diferencia con los Shorts: un Short sale de un PICO de emoción; un vídeo
largo sale de un TEMA DE CONVERSACIÓN. Por eso aquí no se miran los picos de
la curva — se lee el directo ENTERO y se busca de qué se habla.

Flujo:
  1. El transcript se parte en bloques de ~90 s con su marca de tiempo.
  2. Gemini etiqueta cada bloque con un tema corto (2 pasadas de ~40 bloques).
  3. Los bloques del mismo tema se agrupan y se fusionan los que están juntos.
  4. Cada tema con material suficiente = un vídeo. LA TEMÁTICA MANDA: la
     duración sale de cuánto se habló de eso, no de un tope fijo.
"""
from __future__ import annotations

import json
import os
import re

from .util import call_gemini, log, normalize, snap_phrase_end

NL = chr(10)

_SYSTEM = """Eres el editor de un canal de gaming en espanol. Lees la
transcripcion de un directo largo y detectas DE QUE SE HABLA en cada tramo.
Respondes SOLO con JSON valido, sin markdown."""

_PROMPT = """Aqui tienes tramos consecutivos de un directo de {contexto}. Cada
tramo trae su id y lo que se dijo.

Para CADA tramo dime de que TEMA se habla, con una etiqueta corta y consistente
(2-4 palabras, en minusculas). Si en varios tramos se habla de lo mismo, USA
EXACTAMENTE LA MISMA ETIQUETA — es lo mas importante de esta tarea.

Ejemplos de etiquetas buenas: "opinion sobre el arma", "quejas del matchmaking",
"mapas del juego", "comparacion con el anterior", "consejos para novatos",
"anecdota personal", "problemas de rendimiento", "planes del canal".

Usa la etiqueta "relleno" para: saludos, silencios, leer el chat sin mas,
pantallas de carga, buscar partida, problemas tecnicos, o cuando no se habla
de nada concreto.

NO uses etiquetas COMODIN que valgan para medio directo, como "jugando",
"partida", "reaccion a la partida" o "gameplay". Si en un tramo solo esta
jugando sin decir nada que aporte, eso es "relleno". Una etiqueta buena
describe UN TEMA DEL QUE SE HABLA, no lo que se ve en pantalla.

Devuelve SOLO JSON: [{{"id": 0, "tema": "quejas del matchmaking"}}, ...]
Un objeto por CADA tramo, sin saltarte ninguno.

TRAMOS:
{bloques}
"""

_TITULO_SYSTEM = """Eres el editor de un canal de gaming en espanol. Escribes
titulos y descripciones para VIDEOS HORIZONTALES de YouTube (no Shorts).
Respondes SOLO con JSON valido."""

_TITULO_PROMPT = """Este video horizontal junta todos los tramos de un directo
de {contexto} donde se habla de: "{tema}".

Dura {mins} minutos y son {n} tramos. Esto es lo que se dice:

{muestra}

Devuelve SOLO JSON:
{{"titulo": "...", "descripcion": "...", "gancho": "...", "capitulos": ["...", "..."]}}

- "titulo": para YouTube horizontal, en espanol LATINO, maximo 70 caracteres.
  Que invite a hacer clic sin mentir. NADA de emojis al final (eso es de Shorts).
- "descripcion": 2-4 frases en PRIMERA PERSONA, como si la escribiera el
  streamer. Al final, 3-5 hashtags.
- "gancho": una frase de 10-15 palabras que resuma por que merece la pena verlo.
- "capitulos": UNA etiqueta corta (3-6 palabras) por cada uno de los {n} tramos,
  EN ORDEN, describiendo que pasa en ese tramo. Son los capitulos de YouTube.
"""


def _bloques(words: list[dict], seg: float = 90.0) -> list[dict]:
    """Parte el transcript en bloques de ~`seg` segundos con su tiempo."""
    if not words:
        return []
    out: list[dict] = []
    ini = float(words[0].get("start", 0))
    buf: list[str] = []
    for w in words:
        t = float(w.get("start", 0))
        if t - ini >= seg and buf:
            out.append({"id": len(out), "t0": round(ini, 1), "t1": round(t, 1),
                        "texto": " ".join(buf)})
            ini, buf = t, []
        buf.append(str(w.get("word", "")).strip())
    if buf:
        out.append({"id": len(out), "t0": round(ini, 1),
                    "t1": round(float(words[-1].get("end", ini)), 1),
                    "texto": " ".join(buf)})
    return out


def _hhmm(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _gemini(cfg):
    """(client, types, model) o None si no se puede usar la IA."""
    if not cfg.get_path("ai.enabled", True):
        return None
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        modelo = cfg.get_path("ai.model", "gemini-3.1-flash-lite")
        try:                                       # el mejor Flash-Lite de hoy, sin nombres a mano (ver clipper/modelos.py); si falla, `ai.model`
            from . import modelos
            modelo = modelos.barato(cfg, client)
        except Exception:                          # noqa: BLE001
            pass
        return client, types, modelo
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo abrir Gemini ({exc}).")
        return None


def _etiquetar(bloques: list[dict], cfg, contexto: str) -> dict[int, str]:
    """Pide a Gemini el tema de cada bloque. Devuelve {id_bloque: tema}."""
    g = _gemini(cfg)
    if not g:
        log("AVISO: sin IA no se pueden detectar temáticas.")
        return {}
    client, types, model = g

    lote = int(cfg.get_path("videos.bloques_por_llamada", 40))
    chars = int(cfg.get_path("videos.chars_por_bloque", 700))
    fuera: dict[int, str] = {}
    for i in range(0, len(bloques), lote):
        trozo = bloques[i:i + lote]
        listado = "\n\n".join(
            f'id {b["id"]} | {_hhmm(b["t0"])}\n{b["texto"][:chars]}'
            for b in trozo)
        prompt = _PROMPT.format(contexto=contexto or "el vídeo", bloques=listado)
        try:
            raw = call_gemini(client, types, model, prompt, system=_SYSTEM)
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
            for it in json.loads(raw):
                bid = int(it.get("id", -1))
                tema = normalize(str(it.get("tema", ""))).strip()
                if bid >= 0 and tema:
                    fuera[bid] = tema
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: fallo etiquetando bloques {i}-{i + lote} ({exc}).")
        log(f"  temáticas: {min(i + lote, len(bloques))}/{len(bloques)} tramos leídos.")
    return fuera


def _titular(tema: str, segs: list, words: list[dict], cfg, contexto: str) -> dict:
    """Título/descripción del vídeo. Si no hay IA, algo digno pero simple."""
    dur = sum(e - s for s, e in segs)
    base = {"titulo": tema.capitalize()[:70],
            "descripcion": f"Todo lo que hablé sobre {tema} en el directo. "
                           f"#{normalize(contexto).replace(' ', '')[:18] or 'video'}",
            "gancho": "", "capitulos": []}
    g = _gemini(cfg)
    if not g:
        return base
    client, types, model = g
    trozos = []
    for s, e in segs[:6]:
        dicho = " ".join(w["word"] for w in words
                         if s <= float(w.get("start", 0)) < min(e, s + 45))
        if dicho.strip():
            trozos.append(f"[{_hhmm(s)}] {dicho.strip()[:400]}")
    try:
        raw = call_gemini(client, types, model, _TITULO_PROMPT.format(
            contexto=contexto or "el vídeo", tema=tema, mins=round(dur / 60),
            n=len(segs), muestra="\n\n".join(trozos)), system=_TITULO_SYSTEM)
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        d = json.loads(raw)
        if isinstance(d, list):
            d = d[0] if d else {}
        caps = [str(x).strip()[:60] for x in (d.get("capitulos") or []) if str(x).strip()]
        return {
            "titulo": (str(d.get("titulo") or base["titulo"])).strip()[:70],
            "descripcion": (str(d.get("descripcion") or base["descripcion"])).strip()[:2000],
            "gancho": (str(d.get("gancho") or "")).strip()[:200],
            "capitulos": caps,
        }
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo titular «{tema}» ({exc}).")
        return base


def _capitulos(segs: list, etiquetas: list[str], tema: str) -> str:
    """Capítulos de YouTube a partir de los tramos ya montados.

    Solo tienen sentido en VÍDEO LARGO (en un Short no existen). Los tiempos
    son los del vídeo FINAL, no los del directo: como se pegan tramos sueltos,
    hay que ir acumulando. YouTube exige que el primero sea 0:00 y que haya
    al menos 3; si no llegan, se devuelve vacío.
    """
    if len(segs) < 3:
        return ""
    lineas, acc = [], 0.0
    for i, (a, b) in enumerate(segs):
        m, sg = divmod(int(acc), 60)
        h, m = divmod(m, 60)
        sello = f"{h}:{m:02d}:{sg:02d}" if h else f"{m}:{sg:02d}"
        et = (etiquetas[i] if i < len(etiquetas) else "") or f"{tema} · parte {i + 1}"
        lineas.append(f"{'0:00' if i == 0 else sello} {et}")
        acc += b - a
    return NL.join(lineas)


def detectar(words: list[dict], meta: dict, cfg, contexto: str = "") -> list[dict]:
    """Devuelve los vídeos por temática que salen de este directo:
    [{tema, titulo, descripcion, gancho, segments:[(s,e)], duracion, n_tramos}]

    LA TEMÁTICA MANDA LA DURACIÓN: cada vídeo dura lo que se hablo de ese tema
    (dentro de unos límites de cordura configurables).
    """
    if not words:
        return []
    seg = float(cfg.get_path("videos.bloque_seg", 90))
    bloques = _bloques(words, seg)
    log(f"Temáticas: el directo se parte en {len(bloques)} tramos de ~{seg:.0f}s.")

    etiquetas = _etiquetar(bloques, cfg, contexto)
    if not etiquetas:
        return []

    # --- agrupar bloques por tema y fusionar los que van seguidos ---
    hueco = float(cfg.get_path("videos.hueco_max_seg", 180))
    por_tema: dict[str, list[list[float]]] = {}
    for b in bloques:
        tema = etiquetas.get(b["id"], "")
        if not tema or tema == "relleno":
            continue
        tr = por_tema.setdefault(tema, [])
        if tr and b["t0"] - tr[-1][1] <= hueco:
            tr[-1][1] = b["t1"]                     # sigue el mismo tramo
        else:
            tr.append([b["t0"], b["t1"]])

    dur_min = float(cfg.get_path("videos.duracion_min_seg", 300))
    dur_max = float(cfg.get_path("videos.duracion_max_seg", 1200))
    tramo_min = float(cfg.get_path("videos.tramo_min_seg", 40))
    max_vids = int(cfg.get_path("videos.max_por_directo", 5))
    cobertura_max = float(cfg.get_path("videos.cobertura_max", 0.40))
    total = float(meta.get("duration", 0) or 0)

    cand: list[dict] = []
    for tema, tramos in por_tema.items():
        tramos = [[max(0.0, a), min(total or b, b)] for a, b in tramos
                  if b - a >= tramo_min]
        if not tramos:
            continue
        dur = sum(b - a for a, b in tramos)
        if dur < dur_min:
            continue
        # Un tema que cubre casi todo el directo NO es un tema: es la etiqueta
        # comodin que la IA le puso a "esta jugando". Se descarta.
        if total and dur / total > cobertura_max:
            log(f"  tema «{tema}» descartado: cubre el "
                f"{100 * dur / total:.0f}% del directo (es un comodín, no un tema).")
            continue
        # si se pasa del tope, se quedan los tramos MÁS LARGOS (donde más se
        # profundizó). Nunca se corta un tramo por la mitad de una frase: si
        # uno solo ya se pasa (p. ej. una hora seguida), se recorta cerrando
        # la frase con `snap_phrase_end`.
        if dur > dur_max:
            tramos.sort(key=lambda x: -(x[1] - x[0]))
            keep, acc = [], 0.0
            for a, b in tramos:
                room = dur_max - acc
                if room < tramo_min:
                    break
                if b - a <= room:
                    keep.append([a, b])
                    acc += b - a
                else:
                    corte = min(b, snap_phrase_end(a + room, words, 8.0))
                    if corte - a >= tramo_min:
                        keep.append([a, round(corte, 2)])
                        acc += corte - a
                    break
            tramos = sorted(keep)
            dur = sum(b - a for a, b in tramos)
        cand.append({"tema": tema, "segments": [(round(a, 2), round(b, 2))
                                                for a, b in sorted(tramos)],
                     "duracion": round(dur, 1), "n_tramos": len(tramos)})

    # Ordena pensando en el ESPECTADOR DE VÍDEO LARGO, no en el de Shorts:
    # no gana el tema con más minutos sueltos, gana el que se sostiene mejor.
    # Un vídeo de 12 min en 3 tramos largos se ve mucho mejor que uno de 14 en
    # 9 saltos. `coherencia` premia los tramos largos y penaliza el picoteo.
    pref = float(cfg.get_path("videos.peso_coherencia", 0.35))
    for c in cand:
        medio = c["duracion"] / max(1, c["n_tramos"])          # duración media del tramo
        c["_coh"] = min(1.0, medio / 240.0)                    # 4 min por tramo = ideal
        c["_pts"] = (1 - pref) * (c["duracion"] / dur_max) + pref * c["_coh"]
    cand.sort(key=lambda c: -c["_pts"])
    cand = cand[:max_vids]
    for c in cand:
        c.pop("_pts", None)
        c.pop("_coh", None)
    if not cand:
        log("Temáticas: ningún tema llega al mínimo de duración.")
        return []

    for c in cand:
        c.update(_titular(c["tema"], c["segments"], words, cfg, contexto))
        if cfg.get_path("videos.capitulos", True):
            caps = _capitulos(c["segments"], c.get("capitulos") or [], c["tema"])
            if caps:
                c["descripcion"] = (c.get("descripcion", "").strip()
                                    + NL + NL + "CAPÍTULOS" + NL + caps)[:4900]
                c["n_capitulos"] = len(caps.splitlines())
        log(f"  tema «{c['tema']}» -> {c['duracion'] / 60:.1f} min "
            f"en {c['n_tramos']} tramo(s): {c['titulo']}")
    return cand
