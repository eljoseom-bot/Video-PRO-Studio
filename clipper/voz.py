"""Dónde suena TU VOZ de verdad —en el audio, no en los tiempos de Whisper—, para que el vídeo ARRANQUE justo cuando
empiezas a hablar (en la toma buena, no en una falsa) y acabe justo después de tu despedida.

Por qué existe (2026-09-24, medido con las dos grabaciones reales del autor, sin gastar nada de Gemini):
- Whisper apunta cada palabra con un error de 0,2 a 0,9 s justo al salir de un silencio. En la del 16-sep pone
  «Hice» en 43,50 y la voz entra en 43,40: el corte le quitaba la «H».
- CORRECCIÓN (2026-09-25): el 24-sep creí que en la del 22-sep «Una» sonaba de 2,99 a 3,97 (Whisper la ponía en
  3,95). NO: ese sonido es un TARAREO («da da da da da da», escuchado solo) y «Una de las cosas que odio» empieza en
  el 5,88. Whisper, con el audio entero, FUNDE lo del arranque (y en el vídeo del 24-sep, «¿Cómo estás?» [2 s]
  «¿Cómo estás apareciendo…» en una sola pregunta): por eso el arranque se vuelve a escuchar frase por frase
  (`transcribe.revisa_arranque`) antes de todo esto, y `tomas_arranque` y el paso 2 de `ajusta_plan` (tarareos,
  murmullos) trabajan sobre ESA transcripción. Regla del autor: «siempre debe arrancar con la toma buena».
- Encima el audio entraba con un fundido de 3 s y salía con otro de 3 s (`videos.fade_in_seg`, heredado del
  modo Generador): la primera frase sonaba lejos y la despedida («…nos vemos en otra, gente, chao») se apagaba.

Lo que hace, todo por código (numpy sobre el `audio.wav` que ya existe: gratis y en milisegundos):
1. `tomas_arranque` — con la transcripción: ¿repites el inicio? («hola qué tal muchachos… [pausa] hola qué tal
   muchachos, hoy…»). Si lo ve CLARO, `aplica_tomas_falsas` corta todo lo anterior a la toma buena; si solo lo
   sospecha, se lo cuenta al director DENTRO de la llamada de siempre (`texto_prompt`: cero peticiones más).
2. `ajusta_plan` — con el audio: el vídeo empieza `preroll` (0,15 s) antes de tu primera sílaba (ni silencio
   delante ni una sílaba comida: si un corte de «silencio» se comió la palabra, se la devuelve), acorta las
   pausas largas de los primeros segundos y quita el silencio largo del final. Lo que añade va en `recortes` con
   `"auto": "arranque" | "final"` (y la palabra devuelta deja el valor original en `fin_ia`), así el plan
   guardado ES lo que se renderiza (el editor de cortes y las mejoras cuentan con los mismos segundos).
   Es idempotente: deshace lo suyo antes de recalcular, así que aplicarlo dos veces da lo mismo.
3. En TODO el vídeo (pedido del autor, 2026-09-24: ediciones más dinámicas desde el primer render, sin quitar sus
   silencios de ÉNFASIS): los bordes de los cortes de la IA se llevan al silencio real más cercano (no se comen
   sílabas) y las pausas de DUDA a media frase (tras «de», «que», «la», «eh»…: `pausas_candidatas`) se dejan en
   0,3 s — salvo las que el director marque como intencionales en `pausas_enfasis`, en la MISMA llamada de siempre.
   Las pausas ENTRE frases no se tocan por código.
"""
from __future__ import annotations

import bisect
import copy
import re
import wave
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

import numpy as np

from .util import normalize

FRAME = 0.01                       # una medida de volumen cada 10 ms
_MIN_TRAMO = 1.0                   # el trozo más corto que el render conserva (`pipeline._aplica_recortes`, `minimo`)

_DEF = {
    "preroll": 0.15,               # aire antes de tu primera sílaba (el fundido de entrada, 0,12 s, cae aquí y no en tu voz)
    "restaura_max": 1.5,           # lo más atrás que se devuelve una palabra que un corte de «silencio» se comió
    "pausa_max": 1.0,              # en los primeros segundos, una pausa más larga que esto se acorta…
    "pausa_antes": 0.12,           # …dejando este aire tras la palabra anterior…
    "pausa_despues": 0.18,         # …y este antes de la siguiente (0,3 s en total)
    "ventana_pausas": 8.0,         # segundos del vídeo YA montado en los que se acortan pausas
    "cola": 0.7,                   # aire después de tu última palabra
    "cola_max": 1.2,               # más silencio que esto al final, se quita
    "recorte_inicio_max": 20.0,    # sin voz tuya pero CON sonido (música, un vídeo en pantalla) más que esto: no se toca
    "activo_db": 18.0,             # hasta aquí por debajo de tu nivel de voz aún es voz (consonantes, finales de palabra)
    "fuerte_db": 10.0,             # hasta aquí por debajo, voz clara (solo informativo: ver `_es_voz`)
    "voz_min": 0.15,               # un sonido de menos que esto sin una palabra encima es un ruido (clic, roce)
    "pausa_media": 0.7,            # A MEDIA FRASE, una pausa más larga que esto se acorta… (`videos.pausas.umbral_seg`)
    "pausa_media_deja": 0.3,       # …a esto: 40 % tras la palabra, 60 % antes de la siguiente (`videos.pausas.deja_seg`)
    "borde_max": 0.7,              # lo más que se mueve el borde de un corte de la IA para no comerse una sílaba
}
_PAUSAS_MAX = 80                   # candidatas que se le enseñan al director (las más largas; las demás no se tocan)

# Palabras tras las que la frase NO puede acabar: una pausa larga justo detrás es una DUDA (buscando la palabra), no
# un silencio de énfasis. Con su tilde: «qué», «cómo», «más», «sí», «él» SÍ pueden cerrar una frase («¿sabes por
# qué?»), y los verbos tampoco están («la clave es… [pausa] el contenido» es suspense a propósito: eso no se toca).
_ABIERTAS = frozenset("""
el la los las un una unos unas lo al del
a ante bajo con contra de desde en entre hacia hasta para por según sin sobre tras
y e o u ni que pero porque aunque sino si como cuando mientras donde
mi mis tu tus su sus nuestro nuestra nuestros nuestras cada muy
este esta estos estas ese esa esos esas aquel aquella aquellos aquellas
me te se nos os le les
eh ehh eeh em emm mm mmm pues entonces digamos
""".split())

# palabras sueltas que, solas al arrancar y seguidas de una pausa, no son el arranque de verdad
_MULETILLAS = {"eh", "ehh", "eeh", "em", "emm", "mm", "mmm", "este", "bueno", "listo", "ok", "okay", "vale", "ya",
               "entonces", "pues", "ah", "uh", "a", "ver", "y", "va", "dale"}
# un saludo como PRIMERA palabra de una toma («qué» o «muy» no: «¿Qué es el SEO?» no es un saludo)
_SALUDO_INICIAL = {"hola", "buenas", "buenos", "bienvenidos", "bienvenidas", "bienvenido", "hey", "saludos"}
_REINICIO = ("otra vez", "de nuevo", "ahora si", "desde el principio", "desde cero", "vuelvo a empezar",
             "empiezo de nuevo", "repito", "corta", "corten", "me equivoque", "me trabe", "me enrede")

# un tarareo o un murmullo antes de arrancar (22-sep: «da da da da da da», que con el vocabulario Whisper escribe
# «¡Tararararara!»): sílabas sueltas de consonante + vocal, o «mmm» / «ehh» / «ahh»
_SILABAS = re.compile(r"(?:(?:ch|[bdlmnprtv])?[aeiou])+n?")
_SILABA_REPETIDA = re.compile(r"((?:ch|[bdlmnprtv])[aeiou])\1")
_MURMULLO = re.compile(r"h?m+|e+h+|a+h+|o+h+|u+h+|h+m+")


def _es_tarareo(toks: list[str]) -> bool:
    """¿Es un tarareo o un murmullo, no una frase? Todo sílabas sueltas y repetidas («da da da», «tarara», «lalala») o
    murmullos («mmm», «ahh»). «Todo.» o «Nada.» solos no cuentan (no repiten sílaba)."""
    if not toks or len(toks) > 14:
        return False
    if all(_MURMULLO.fullmatch(t) for t in toks):
        return True
    if not all(_SILABAS.fullmatch(t) or _MURMULLO.fullmatch(t) for t in toks):
        return False
    return (len(toks) >= 3 and len(set(toks)) <= 2) or any(_SILABA_REPETIDA.search(t) for t in toks)


def _relleno_suelto(toks: list[str], probs: list | None = None) -> str | None:
    """Lo que dices ANTES de arrancar y no es el arranque: una muletilla suelta («Bueno…»), un tarareo o un murmullo,
    o 1-3 palabras de las que Whisper no está nada seguro. Devuelve qué es (para el log) o None."""
    if not toks:
        return None
    if len(toks) <= 2 and all(t in _MULETILLAS for t in toks):
        return f"«{' '.join(toks)}» suelto"
    if _es_tarareo(toks):
        return f"un tarareo o murmullo («{' '.join(toks[:4])}{'…' if len(toks) > 4 else ''}»)"
    ps = [x for x in (probs or []) if x is not None]
    if len(toks) <= 3 and ps and len(ps) >= len(toks) and max(ps) < 0.4:
        return f"«{' '.join(toks)}» (no se entiende)"
    return None


def _es_inicio_de(a: list[str], b: list[str]) -> bool:
    """¿La toma `a` (entera) es el PRINCIPIO de la toma `b`? Es decir, empezaste, te cortaste y volviste a empezar:
    «¿Cómo estás?» / «¿Cómo estás apareciendo hoy día…» (tu vídeo del 24-sep), «Una…» / «Una de las cosas…». La última
    palabra de `a` puede estar cortada a media palabra («a…» de «apareciendo») o mal oída si ya coinciden dos."""
    n = len(a)
    if n == 0 or n > 6 or n >= len(b):
        return False
    if a[: n - 1] != b[: n - 1]:
        return False
    ultima, suya = a[-1], b[n - 1]
    if n == 1:
        return ultima == suya or (len(ultima) >= 3 and suya.startswith(ultima))
    return ultima == suya or suya.startswith(ultima) or n >= 3


def frases(audio: "Audio", t0: float, t1: float, nivel: float, p: dict | None = None,
           hueco: float = 0.6) -> list[tuple[float, float]]:
    """Las FRASES entre `t0` y `t1`: los tramos con sonido (`islas`) unidos cuando los separa menos de `hueco` s de
    silencio. Para volver a escuchar el arranque frase por frase (`transcribe.revisa_arranque`)."""
    out: list[list[float]] = []
    for I in islas(audio, t0, t1, nivel, p):
        if out and I.ini - out[-1][1] < hueco:
            out[-1][1] = I.fin
        else:
            out.append([I.ini, I.fin])
    return [(a, b) for a, b in out]


def _params(cfg=None) -> dict:
    p = dict(_DEF)
    try:
        p.update({k: float(v) for k, v in (cfg.get_path("videos.arranque") or {}).items()
                  if k in _DEF and v is not None})
        pz = cfg.get_path("videos.pausas") or {}
        for k_cfg, k in (("umbral_seg", "pausa_media"), ("deja_seg", "pausa_media_deja")):
            if pz.get(k_cfg) is not None:
                p[k] = float(pz[k_cfg])
    except Exception:  # noqa: BLE001 — sin config (pruebas) o un valor raro: los de fábrica
        pass
    return p


def activo(cfg) -> bool:
    try:
        return bool((cfg.get_path("videos.arranque") or {}).get("activo", True))
    except Exception:  # noqa: BLE001
        return True


def pausas_activo(cfg) -> bool:
    """`videos.pausas.activo`: acortar las pausas de duda a media frase (con el veto del director)."""
    try:
        return activo(cfg) and bool((cfg.get_path("videos.pausas") or {}).get("activo", True))
    except Exception:  # noqa: BLE001
        return True


def bordes_activo(cfg) -> bool:
    """`videos.bordes_cortes`: llevar los bordes de los cortes de la IA al silencio real (sin comerse sílabas)."""
    try:
        return activo(cfg) and bool(cfg.get_path("videos.bordes_cortes", True))
    except Exception:  # noqa: BLE001
        return True


# ------------------------------------------------------------------ el audio
class Audio:
    """El volumen del `audio.wav` (el de `ingest.extract_audio`: 16 kHz, mono, 16 bits) cada 10 ms, en dBFS."""

    def __init__(self, wav: Path):
        partes: list[np.ndarray] = []
        with wave.open(str(wav), "rb") as w:
            sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
            if sw != 2:
                raise ValueError(f"el audio es de {8 * sw} bits; se esperaba PCM de 16 bits")
            hop = max(1, int(round(sr * FRAME)))
            bloque = hop * 6000                         # de 60 s en 60 s: un directo de 2 h no carga 230 MB de golpe
            leidos = 0
            while leidos < n:
                raw = w.readframes(min(bloque, n - leidos))
                if not raw:
                    break
                a = np.frombuffer(raw, dtype=np.int16)
                if ch > 1:
                    a = a[: len(a) // ch * ch].reshape(-1, ch)[:, 0]
                leidos += len(a)
                m = len(a) // hop
                if m:
                    x = a[: m * hop].astype(np.float32).reshape(m, hop) / 32768.0
                    partes.append((10.0 * np.log10((x * x).mean(axis=1) + 1e-12)).astype(np.float32))
        self.db = np.concatenate(partes) if partes else np.zeros(0, dtype=np.float32)
        self.dur = leidos / float(sr)


class Isla(NamedTuple):
    """Un tramo con sonido de voz: [ini, fin] en segundos, cuánto de él es voz CLARA y su pico (dBFS)."""
    ini: float
    fin: float
    fuerte: float
    pico: float


def nivel_voz(audio: Audio, words: list[dict]) -> float:
    """Tu nivel de voz (dBFS): la mediana del pico de cada palabra, con una muestra repartida por todo el vídeo.
    Todo lo demás se mide RELATIVO a esto, así sirve igual si grabas más alto o más bajo."""
    ws = [w for w in words if float(w.get("end", 0)) > float(w.get("start", 0))]
    picos = []
    for w in ws[:: max(1, len(ws) // 400)]:
        i0 = max(0, int(float(w["start"]) / FRAME) - 20)
        i1 = int(float(w["end"]) / FRAME) + 20
        seg = audio.db[i0:i1]
        if len(seg):
            picos.append(float(seg.max()))
    if picos:
        return float(np.median(picos))
    return float(np.percentile(audio.db, 95)) if len(audio.db) else -20.0


def islas(audio: Audio, t0: float, t1: float, nivel: float, p: dict | None = None) -> list[Isla]:
    """Los tramos con sonido entre `t0` y `t1`: a menos de `activo_db` de tu nivel de voz, con los huecos de menos de
    0,15 s unidos (una oclusiva o un respiro dentro de la frase no parten la palabra). Incluye ruidos: el volumen NO
    los distingue de una palabra suave — medido en las grabaciones reales, el clic del 4,75 s (22-sep) y tu «Hice»
    del 43,4 s (16-sep) tienen el MISMO pico, 11,6 dB por debajo de tu voz. Lo que los separa es que Whisper oyó una
    palabra ahí (`_isla_de`) y la duración (`_es_voz`: el clic dura 90 ms, una palabra 150 ms o más)."""
    p = p or _DEF
    a0 = max(0, int((t0 - 10.0) / FRAME))              # 10 s de margen: una isla que ya venía sonando no se trunca
    a1 = min(len(audio.db), int(np.ceil((t1 + 10.0) / FRAME)))
    if a1 <= a0:
        return []
    db = audio.db[a0:a1]
    act = db > nivel - p["activo_db"]
    fuerte = db > nivel - p["fuerte_db"]
    corridas: list[list[int]] = []
    i, n = 0, len(db)
    while i < n:
        if not act[i]:
            i += 1
            continue
        j = i
        while j < n and act[j]:
            j += 1
        if corridas and i - corridas[-1][1] <= int(round(0.15 / FRAME)):
            corridas[-1][1] = j
        else:
            corridas.append([i, j])
        i = j
    out = []
    for a, b in corridas:
        ini, fin = (a0 + a) * FRAME, (a0 + b) * FRAME
        if b - a >= 3 and fin > t0 and ini < t1:
            out.append(Isla(round(ini, 3), round(fin, 3), round(int(fuerte[a:b].sum()) * FRAME, 3),
                            float(db[a:b].max())))
    return out


def _es_voz(I: Isla, p: dict | None = None) -> bool:
    """¿Dura lo que una palabra? (el clic del 4,75 s de la grabación del 22-sep dura 90 ms; «Hice», 200 ms)."""
    return I.fin - I.ini >= (p or _DEF)["voz_min"]


def _isla_de(w: dict, isl: list[Isla], tolerancia: float = 0.8) -> int | None:
    """La isla donde EMPIEZA a sonar la palabra `w` según lo que dice Whisper (cualquier isla: si Whisper oyó una
    palabra ahí, es voz). Si su intervalo pisa varias, gana la PRIMERA que pisa de verdad (≥ 30 % de la más corta de
    las dos): el 16-sep Whisper da a «Hice» 43,50–44,04, que pisa «Hice» (43,42–43,64) y el principio de «una página
    web…» (desde 43,88) — por solape mayor ganaba la segunda y el arranque se saltaba «Hice». Si no pisa ninguna, la
    isla con duración de palabra más cercana a menos de `tolerancia` s (Whisper se equivoca hasta ~0,9 s al salir de
    un silencio; un clic no «roba» la palabra)."""
    a, b = float(w["start"]), float(w["end"])
    mejor, mejor_sol = None, 0.0
    for k, s in enumerate(isl):
        sol = min(b, s.fin) - max(a, s.ini)
        if sol <= 0:
            continue
        if sol >= max(0.05, 0.3 * min(s.fin - s.ini, max(0.05, b - a))):
            return k
        if sol > mejor_sol:
            mejor, mejor_sol = k, sol
    if mejor is not None:
        return mejor
    mejor_d = tolerancia
    for k, s in enumerate(isl):
        if not _es_voz(s):
            continue
        d = s.ini - b if s.ini >= b else a - s.fin
        if d < mejor_d:
            mejor, mejor_d = k, d
    return mejor


def _mapa(ws: list[dict], isl: list[Isla]) -> list[int | None]:
    """`_isla_de` para TODAS las palabras de una vez (la isla de cada una, o None), mirando solo las islas de
    alrededor: con 4.600 palabras y 2.000 islas (un vídeo de 23 min) recorrerlas todas por palabra tarda segundos."""
    inis = [I.ini for I in isl]
    fines = [I.fin for I in isl]
    out: list[int | None] = []
    for w in ws:
        a, b = float(w["start"]), float(w["end"])
        i0 = bisect.bisect_left(fines, a - 1.0)            # (las islas no se pisan: sus finales también van en orden)
        i1 = bisect.bisect_right(inis, b + 1.0)
        k = _isla_de(w, isl[i0:i1]) if i1 > i0 else None
        out.append(None if k is None else i0 + k)
    return out


def _visto(segs: list[tuple[float, float]], x: float, y: float) -> float:
    """Cuánto de [x, y] del original se ve en el vídeo montado."""
    return sum(max(0.0, min(e, y) - max(s, x)) for s, e in segs)


def _solo_cambia(antes: list[tuple[float, float]], despues: list[tuple[float, float]],
                 ventanas: list[tuple[float, float]]) -> bool:
    """¿El vídeo cambia SOLO dentro de `ventanas`? El render tira los trozos de menos de 1 s (y suelda las migas al
    de al lado): un corte o un borde nuevo podría llevarse de rebote una palabra vecina sin que nadie lo pida."""
    fin = max([e for _s, e in antes + despues] or [0.0]) + 1.0
    fuera, cur = [], 0.0
    for a, b in sorted(ventanas):
        if a > cur:
            fuera.append((cur, a))
        cur = max(cur, b)
    fuera.append((cur, fin))
    return all(abs(_visto(antes, x, y) - _visto(despues, x, y)) < 0.02 for x, y in fuera)


def _es_silencio(r: dict) -> bool:
    m = normalize(str(r.get("motivo", "")))
    return any(k in m for k in ("silencio", "silence", "pausa", "vacio", "sin voz", "nada"))


# ------------------------------------------------------------------ pausas de duda a media frase
def _frase_abierta(w: dict) -> str | None:
    """La palabra (en minúsculas) si tras ella la frase NO puede acabar (`_ABIERTAS`); None si sí puede, o si Whisper
    le puso un punto, una interrogación o dos puntos (ahí acaba algo: «¿sabes por qué?» no es una duda)."""
    crudo = str(w.get("word", "")).strip()
    if not crudo or crudo[-1] in ".?!…:;":
        return None
    t = crudo.lower().strip("¿¡\"'«»()[],")
    return t if t in _ABIERTAS else None


def pausas_candidatas(words: list[dict], audio: Audio, actividad: list[dict] | None = None, cfg=None) -> list[dict]:
    """Las pausas de DUDA a media frase: > `pausa_media` (0,7 s) de silencio medido en el audio justo después de una
    palabra tras la que la frase no puede acabar («la clave de… [1,4 s] todo esto», «que… eh… [0,9 s] Google»).
    Casi siempre es buscar la palabra; las que sean a propósito (suspense antes de un dato) las marca el director en
    `pausas_enfasis` al ver esta lista (`texto_pausas`, dentro de la llamada de siempre). Las pausas ENTRE frases no
    salen aquí: esas pueden ser énfasis y el código no las toca (el autor, 2026-09-24). Tampoco las que caen mientras hay
    actividad en pantalla (`movimiento.py`): ahí el silencio es el tiempo de un clic o de que cargue algo.
    Devuelve [{"id": "p123.4", "ini", "fin" (el silencio medido), "dur", "palabra", "antes", "despues"}] en orden,
    como mucho `_PAUSAS_MAX` (las más largas)."""
    p = _params(cfg)
    ws = sorted((w for w in words if float(w.get("end", 0)) > float(w.get("start", 0)) and str(w.get("word", "")).strip()),
                key=lambda w: float(w["start"]))
    if not ws or not len(audio.db):
        return []
    isl = [I for I in islas(audio, 0.0, audio.dur, nivel_voz(audio, ws), p) if _es_voz(I, p)]
    mapa = _mapa(ws, isl)
    de_isla: dict[int, list[int]] = {}
    for j, k in enumerate(mapa):
        if k is not None:
            de_isla.setdefault(k, []).append(j)
    act = [(float(v["inicio"]), float(v["fin"])) for v in actividad or [] if "inicio" in v and "fin" in v]
    out = []
    for k in range(len(isl) - 1):
        A, B = isl[k], isl[k + 1]
        if B.ini - A.fin <= p["pausa_media"] or k not in de_isla or k + 1 not in de_isla:
            continue                              # (sin palabra a los dos lados: un respiro, un «eh» que Whisper no escribió…)
        ia, ib = de_isla[k][-1], de_isla[k + 1][0]
        if ib != ia + 1 or float(ws[ia]["start"]) >= A.fin - 0.02:
            continue                              # Whisper oyó algo en medio (un «eh» flojito): no es un silencio limpio
        palabra = _frase_abierta(ws[ia])
        if not palabra or any(x < B.ini and y > A.fin for x, y in act):
            continue
        antes = " ".join(str(w["word"]).strip() for w in ws[max(0, ia - 5): ia + 1])
        despues = " ".join(str(w["word"]).strip() for w in ws[ib: ib + 5])
        out.append({"id": f"p{A.fin:.1f}", "ini": round(A.fin, 3), "fin": round(B.ini, 3),
                    "dur": round(B.ini - A.fin, 2), "palabra": palabra, "antes": antes, "despues": despues})
    if len(out) > _PAUSAS_MAX:
        out = sorted(sorted(out, key=lambda c: -c["dur"])[:_PAUSAS_MAX], key=lambda c: c["ini"])
    return out


def texto_pausas(cands: list[dict] | None) -> str:
    """La lista de `pausas_candidatas` para el prompt del director (una por línea, con su id)."""
    if cands is None:
        return '(el programa no midió pausas en este vídeo: deja "pausas_enfasis" vacío)'
    if not cands:
        return '(ninguna en este vídeo: deja "pausas_enfasis" vacío)'
    return "\n   ".join(f"- {c['id']} ({c['dur']:.1f}s): «…{c['antes']}» [PAUSA] «{c['despues']}…»" for c in cands)


# ------------------------------------------------------------------ ajustar el plan (arranque, pausas, final)
_AUTO = ("arranque", "final", "pausa", "tomas_falsas")   # los cortes que pone `ajusta_plan` (y quita `revierte`)


def revierte(plan: dict) -> dict:
    """El plan SIN lo que añadió `ajusta_plan` (para recalcular desde cero: por eso es idempotente): fuera sus cortes
    y los de la IA vuelven a sus segundos originales (`inicio_ia` / `fin_ia`)."""
    out = copy.deepcopy(plan)
    recs = []
    vistos_ia: set[str] = set()
    for r in out.get("recortes") or []:
        if r.get("auto") in _AUTO:
            continue
        if isinstance(r.get("de_ia"), dict):
            # un corte de la IA que `_protege_contenido` partió (o anuló): vuelve a ser EL corte original, una sola vez
            clave = repr(sorted(r["de_ia"].items()))
            if clave not in vistos_ia:
                vistos_ia.add(clave)
                recs.append(dict(r["de_ia"]))
            continue
        if "fin_ia" in r:
            r["fin"] = r.pop("fin_ia")
        if "inicio_ia" in r:
            r["inicio"] = r.pop("inicio_ia")
        r.pop("sin_voz", None)
        recs.append(r)
    out["recortes"] = recs
    return out


def _marca_sin_voz(recs: list[dict], ws: list[dict], audio: Audio, nivel: float, p: dict, min_s: float = 20.0) -> float:
    """Marca `sin_voz` los cortes GRANDES de la IA (≥ `min_s`) en los que NO suena ninguna palabra: solo silencio, o
    ruidos sin palabra (teclear, clics). Así no cuentan para el tope de seguridad del % (`pipeline._es_fijo`): ese tope
    quita los cortes MÁS GRANDES primero, y en un vídeo largo con un silencio enorme («hago el keyword research y vuelvo
    en 20 min», el autor 2026-09-24) habría tirado justo ese corte y dejado 20 min de silencio. Quitar silencio no puede
    quitar contenido. Una palabra de Whisper sin sonido debajo (se las inventa en los silencios largos) no cuenta como
    voz. Solo los grandes: en los cortes cortos del arranque Whisper apunta la primera palabra fuera de su sitio (22-sep:
    «Una» suena dentro del corte de 0-4 s y Whisper la pone después), y para ellos el tope no importa. Devuelve los
    segundos marcados."""
    total = 0.0
    for r in recs:
        if r.get("manual") or r.get("auto"):
            continue
        a, b = float(r["inicio"]), float(r["fin"])
        if b - a < min_s:
            continue
        isl = [I for I in islas(audio, a, b, nivel, p) if _es_voz(I, p) and I.fin > a + 0.3 and I.ini < b - 0.3]
        dentro = [w for w in ws if float(w["start"]) >= a + 0.3 and float(w["end"]) <= b - 0.3]
        if not any(_isla_de(w, isl, tolerancia=0.2) is not None for w in dentro):
            r["sin_voz"] = True
            total += b - a
    return total


# cortes de la IA que DICEN quitar solo silencio, ruido o muletillas (no contenido): los que se comprueban
_MOTIVO_SUAVE = ("silencio", "pausa", "muletilla", "tropiezo", "titubeo", "duda", "ruido", "golpe", "respir", "carraspe",
                 "vacio", "espera", "eh ")
_MOTIVO_CONTENIDO = ("divag", "repet", "toma", "desvi", "tangente", "aporta", "irrelevante", "off topic", "fuera de tema")


def _es_corte_suave(r: dict) -> bool:
    m = f" {normalize(str(r.get('motivo', '')))} "
    return any(k in m for k in _MOTIVO_SUAVE) and not any(k in m for k in _MOTIVO_CONTENIDO)


def _protege_contenido(recs: list[dict], ws: list[dict], audio: Audio, nivel: float, p: dict,
                       max_s: float = 8.0, oye=None) -> list[str]:
    """Un corte de la IA que DICE quitar silencio, ruido o una muletilla no puede llevarse palabras de verdad (el autor,
    2026-09-25: «me cortó la idea»). Gemini elige los cortes con los tiempos de Whisper, que fallan justo en las pausas:
    en su vídeo del 24-sep, «silencio y tropiezo» [189,5-192,1] se llevaba «los pasé a inglés» (el final de la idea) y
    «silencio y muletilla» [358,2-360,3] se llevaba «muchas ver-» de «muchas versiones» (en el audio casi no había pausa).
    Se escucha qué suena DENTRO del corte (tramos de voz del audio + qué palabras de Whisper caen en cada uno): se pueden
    quitar los silencios, los clics (< `voz_min`) y los tramos que son SOLO muletillas o palabras sueltas de unión («eh»,
    «¿listo?», «la…»), o palabras que repites justo después (un reinicio). Si dentro hay otra cosa (palabras de
    contenido, o un sonido con duración de palabra que Whisper no ubicó), el corte se rehace: solo los silencios de
    ≥ `pausa_media` s entre esas palabras, dejando 0,3 s (como las pausas de duda). El original queda en `de_ia` y
    `revierte` lo recupera. Solo cortes de ≤ `max_s` s (los largos son otra cosa: una intro, un silencio de trabajo) y
    no los del arranque (< 1 s: eso lo decide el paso del ARRANQUE). `oye(inicio, fin)` -> [(texto, inicio, fin)] vuelve
    a escuchar el trozo con Whisper (`transcribe.oido_con_cache`): las palabras guardadas vienen desplazadas justo en las
    pausas y Whisper casi nunca escribe los «eh» — sin volver a escuchar, un «Bueno, eh…» que SÍ había que cortar
    parecía un sonido sin palabra y se protegía. Devuelve notas para el log."""
    notas: list[str] = []
    nuevos: list[dict] = []
    for r in list(recs):
        if r.get("manual") or r.get("auto") or r.get("de_ia") or not _es_corte_suave(r):
            continue
        a, b = float(r["inicio"]), float(r["fin"])
        if not 0 < b - a <= max_s or a < 1.0:
            continue
        # solo los tramos de voz que están MAYORMENTE dentro del corte: uno que el corte solo roza en su borde es cosa
        # de `_ajusta_bordes` (lleva el borde al silencio)
        isl = [I for I in islas(audio, max(0.0, a - 0.5), b + 0.5, nivel, p)
               if min(I.fin, b) - max(I.ini, a) >= 0.5 * (I.fin - I.ini)]
        if not isl:
            continue
        palabras, despues = ws, None
        if oye is not None:
            try:
                oidas = [{"word": t, "start": s0, "end": e0} for t, s0, e0 in oye(max(0.0, a - 0.3), b + 2.5)]
                palabras = [w for w in oidas if float(w["start"]) < b]
                # lo que dices JUSTO después del corte, oído de verdad (en lo guardado, Whisper pone a veces la MISMA
                # palabra detrás del corte: «versiones» en 360,28 cuando suena en 359,6 — y parecería que la repites)
                despues = [t for w in oidas if float(w["start"]) >= b + 0.05 for t in _toks(str(w["word"]))][:6]
            except Exception:  # noqa: BLE001 — sin volver a escuchar, con lo de Whisper (más prudente con lo que no ubica)
                palabras, despues = ws, None
        if despues is None:
            despues = [t for w in ws if float(w["start"]) >= b + 0.5 for t in _toks(str(w["word"]))][:6]
        pal: dict[int, list[str]] = {}
        for w in palabras:
            if float(w["end"]) < a - 1.0 or float(w["start"]) > b:
                continue
            k = _isla_de(w, isl, tolerancia=0.2)
            if k is not None:
                pal.setdefault(k, []).extend(_toks(str(w["word"])))
        guarda = []
        for k, I in enumerate(isl):
            toks = pal.get(k, [])
            if not toks:
                if I.fin - I.ini >= p["voz_min"]:
                    guarda.append((I, "(un sonido con duración de palabra)"))
                continue
            contenido = [t for t in toks if t not in _MULETILLAS and t not in _ABIERTAS and len(t) > 1]
            if contenido and not all(t in despues for t in contenido) and not _es_tarareo(toks):
                guarda.append((I, " ".join(toks)))
        if not guarda:
            continue                                           # la IA tenía razón: dentro solo hay silencio o relleno
        # el corte rehecho: solo los silencios entre lo que se queda (y los bordes del corte original)
        partes, cur = [], a
        for I, _t in guarda:
            partes.append((cur, max(cur, I.ini), cur == a))
            cur = max(cur, I.fin)
        partes.append((cur, b, False))
        original = {k: v for k, v in r.items() if k not in ("inicio_ia", "fin_ia", "sin_voz")}
        recs.remove(r)
        hechos = 0
        for x, y, desde_borde in partes:
            x2 = x if desde_borde else x + p["pausa_media_deja"] * 0.4
            y2 = y if y >= b - 1e-6 else y - p["pausa_media_deja"] * 0.6
            if y - x >= p["pausa_media"] and y2 - x2 > 0.05:
                nuevos.append({**original, "inicio": round(x2, 3), "fin": round(y2, 3), "de_ia": original})
                hechos += 1
        if not hechos:                                          # ni un silencio que quitar: el corte se anula
            nuevos.append({**original, "inicio": round(a, 3), "fin": round(a, 3), "de_ia": original})
        dichas = "; ".join(t for _I, t in guarda)[:70]
        notas.append(f"el corte de la IA «{r.get('motivo', '')}» ({a:.1f}-{b:.1f}s) se llevaba «{dichas}»: solo se "
                     f"quita {'el silencio' if hechos else 'nada'}")
    recs.extend(nuevos)
    return notas


def _puede_devolver(recs: list[dict], nuevo: float, s0: float, ext: float, p: dict, solo_suya: bool) -> bool:
    """¿Se puede volver a dejar ver [nuevo, s0]? Nunca si lo cortaste tú a mano. Sí si en ese sonido solo suenan
    palabras que el plan QUERÍA dejar (`solo_suya`: la IA cortó por los tiempos de Whisper, no por lo que se dice —
    da igual cómo llamara al corte), si lo tapan cortes de «silencio» (o de tomas falsas del propio programa), o si
    es solo una sílaba (≤ 0,35 s) de cualquier corte."""
    if ext > p["restaura_max"]:
        return False
    tapan = [r for r in recs if float(r["inicio"]) < s0 and float(r["fin"]) > nuevo]
    if any(r.get("manual") for r in tapan):
        return False
    return (ext <= 0.35 or solo_suya
            or all(_es_silencio(r) or r.get("auto") == "tomas_falsas" for r in tapan))


def _dentro(w: dict, a: float, b: float) -> float:
    """Qué parte de la palabra (en los tiempos de Whisper, con los que la IA eligió el corte) cae dentro de [a, b]."""
    s, e = float(w["start"]), float(w["end"])
    return max(0.0, min(e, b) - max(s, a)) / max(0.01, e - s)


def _borde_nuevo(t: float, a: float, b: float, lado: str, isl: list[Isla], pal: dict[int, list[int]],
                 ws: list[dict], p: dict) -> float | None:
    """Adónde llevar el borde `t` (= `a` si `lado` es "inicio", = `b` si es "fin") del corte [a, b] de la IA para que
    no caiga DENTRO de un sonido. Whisper apunta los finales de palabra hasta ~0,4 s antes de tiempo (medido: 19 de 26
    cortes de «silencio» del 22-sep empezaban con la última sílaba aún sonando), así que el corte se la comía.
    - En silencio: se queda (None).
    - Dentro de palabras que el plan quería DEJAR (la mayor parte de cada una fuera del corte): el borde sale del
      sonido y la palabra se oye entera.
    - Dentro de palabras que el plan quería QUITAR: el corte se lleva el sonido entero (sin medio «eh» colgando).
    - Dentro de un sonido sin palabra (un respiro, un «eh» que Whisper no escribió): al silencio más cercano.
    - Habla seguida con palabras de las dos clases: no hay silencio donde llevarlo, se queda.
    Nunca más de `borde_max` (0,7 s)."""
    i = bisect.bisect_right([I.ini for I in isl], t) - 1
    if i < 0 or not (isl[i].ini + 0.02 < t < isl[i].fin - 0.02):
        return None
    I = isl[i]
    quitadas = [_dentro(ws[j], a, b) >= 0.5 for j in pal.get(i, [])]
    if not quitadas:
        nuevo = I.ini - 0.04 if t - I.ini <= I.fin - t else I.fin + 0.04
    elif all(quitadas):
        nuevo = I.ini - 0.04 if lado == "inicio" else I.fin + 0.04
    elif not any(quitadas):
        nuevo = I.fin + 0.06 if lado == "inicio" else I.ini - 0.08
    else:
        return None
    return round(nuevo, 3) if abs(nuevo - t) <= p["borde_max"] else None


def _ajusta_bordes(out: dict, ws: list[dict], audio: Audio, nivel: float, segs_de, p: dict, dur: float) -> int:
    """Los bordes de los cortes de la IA (no los tuyos a mano ni los del programa) al silencio real más cercano:
    `_borde_nuevo`. El segundo original queda en `inicio_ia` / `fin_ia` (`revierte` lo devuelve). Solo los bordes
    que de verdad se ven (dos cortes pegados no tienen borde entre ellos) y solo si el vídeo cambia ahí y en ningún
    otro sitio (`_solo_cambia`). Los cortes del arranque y del final ya los mide `ajusta_plan`. Devuelve cuántos
    cortes cambió."""
    recs = out.get("recortes") or []
    segs = segs_de(out)
    if not segs:
        return 0
    v0 = segs[0][0]
    isl = islas(audio, 0.0, audio.dur, nivel, p)
    pal: dict[int, list[int]] = {}
    for j, k in enumerate(_mapa(ws, isl)):
        if k is not None:
            pal.setdefault(k, []).append(j)
    cambiados = 0
    for r in recs:
        if r.get("manual") or r.get("auto"):
            continue
        a, b = float(r["inicio"]), float(r["fin"])
        if a < v0 + 0.5 or b > dur - 0.05 or b - a < 0.1:
            continue
        na = _borde_nuevo(a, a, b, "inicio", isl, pal, ws, p) if any(abs(e - a) < 0.02 for _s, e in segs) else None
        nb = _borde_nuevo(b, a, b, "fin", isl, pal, ws, p) if any(abs(s - b) < 0.02 for s, _e in segs) else None
        a2, b2 = (a if na is None else na), (b if nb is None else nb)
        if (na is None and nb is None) or b2 - a2 < 0.1:
            continue
        antes = dict(r)
        r["inicio"], r["fin"] = a2, b2
        if na is not None:
            r["inicio_ia"] = a
        if nb is not None:
            r["fin_ia"] = b
        nuevos = segs_de(out)
        if not _solo_cambia(segs, nuevos, [(min(a, a2) - 0.01, max(a, a2) + 0.01), (min(b, b2) - 0.01, max(b, b2) + 0.01)]):
            r.clear()
            r.update(antes)                              # se llevaba algo más de rebote (un trozo de < 1 s): como estaba
            continue
        segs = nuevos
        cambiados += 1
    return cambiados


def _acorta_pausas(out: dict, pausas: list[dict], segs_de, p: dict) -> tuple[int, float]:
    """Las pausas de `pausas_candidatas` que el director VIO (`pausas_vistas`) y no vetó (`pausas_enfasis`), a
    `pausa_media_deja` (0,3 s): un corte `"auto": "pausa"` que no suena. No toca una pausa que ya corta otro (la IA, tú
    a mano, el arranque), ni deja un trozo de < 1 s (el render lo tiraría con la palabra), ni cambia nada fuera de la
    pausa (`_solo_cambia`). Un plan de ANTES de esto no trae `pausas_vistas`: se queda como estaba. Devuelve
    (cuántas, segundos ganados)."""
    if not pausas or out.get("pausas_vistas") is None:
        return 0, 0.0
    vistas, veto = set(out.get("pausas_vistas") or []), set(out.get("pausas_enfasis") or [])
    recs = out.setdefault("recortes", [])
    segs = segs_de(out)
    n, ganado = 0, 0.0
    for c in pausas:
        if c["id"] not in vistas or c["id"] in veto:
            continue
        x, y = float(c["ini"]), float(c["fin"])
        if any(float(r["inicio"]) < y + 0.05 and float(r["fin"]) > x - 0.05 for r in recs):
            continue
        trozo = next(((s, e) for s, e in segs if s <= x + 1e-6 and e >= y - 1e-6), None)
        if trozo is None:
            continue                                     # no se ve entera
        a = max(x + 0.4 * p["pausa_media_deja"], trozo[0] + _MIN_TRAMO)
        b = min(y - 0.6 * p["pausa_media_deja"], trozo[1] - _MIN_TRAMO)
        if b - a < 0.2:
            continue
        corte = {"inicio": round(a, 3), "fin": round(b, 3), "auto": "pausa", "id": c["id"],
                 "motivo": f"pausa de duda de {y - x:.1f}s a media frase (tras «{c['palabra']}»)"}
        recs.append(corte)
        nuevos = segs_de(out)
        if (not _solo_cambia(segs, nuevos, [(a - 0.01, b + 0.01)])
                or _visto(nuevos, x, y) > _visto(segs, x, y) - (b - a) + 0.02):
            recs.remove(corte)
            continue
        segs = nuevos
        n += 1
        ganado += b - a
    return n, ganado


def _devuelve(recs: list[dict], nuevo: float, s0: float) -> None:
    """Vuelve a dejar ver [nuevo, s0]: acorta SOLO los cortes que lo tapan (guardando su final original en `fin_ia`);
    los de más adelante en el vídeo ni se miran — un primer intento los acortaba todos y anulaba, por ejemplo, la
    toma repetida del 1:45 del vídeo «-6» (lo destapó la medición de los cortes reales, 2026-09-24)."""
    for r in recs:
        if r.get("manual") or float(r["fin"]) <= nuevo or float(r["inicio"]) >= s0:
            continue
        r.setdefault("fin_ia", r["fin"])
        r["fin"] = round(max(float(r["inicio"]), nuevo), 3)     # si empezaba después, queda vacío (no corta nada)


def ajusta_plan(plan: dict, words: list[dict], audio: Audio, dur: float, segs_de, cfg=None,
                pausas: list[dict] | None = None, oye=None) -> tuple[dict, dict]:
    """El plan con el ARRANQUE y el FINAL medidos en el audio, los bordes de los cortes de la IA en silencio y las
    pausas de duda a media frase acortadas (`pausas` = `pausas_candidatas`; solo las que el director vio y no vetó).
    `segs_de(plan)` = los tramos que se verán (la misma cuenta que el render, `pipeline._segs_de_plan`). Devuelve
    (plan, info) — info = {"notas": [...] para el log, "voz": segundo del original donde entra tu voz, "cola":
    segundos que quedan tras tu última palabra, "bordes": cortes de la IA ajustados, "pausas" / "pausas_seg":
    pausas acortadas y segundos ganados}."""
    p = _params(cfg)
    out = revierte(plan)
    recs = out.setdefault("recortes", [])
    info: dict = {"notas": [], "voz": None, "cola": None, "bordes": 0, "pausas": 0, "pausas_seg": 0.0}
    ws = sorted((w for w in words if float(w.get("end", 0)) > float(w.get("start", 0))
                 and str(w.get("word", "")).strip()), key=lambda w: float(w["start"]))
    segs = segs_de(out)
    if not ws or not segs:
        return out, info
    nivel = nivel_voz(audio, ws)

    # 0) los cortes de la IA que son SOLO silencio: fuera del tope del % (ver `_marca_sin_voz`)
    info["sin_voz_s"] = round(_marca_sin_voz(recs, ws, audio, nivel, p), 1)
    if info["sin_voz_s"] >= 60:
        info["notas"].append(f"{info['sin_voz_s'] / 60:.1f} min de cortes de la IA sin ninguna palabra: van siempre, "
                             "no cuentan para el tope de seguridad del %")

    # 0b) TOMAS FALSAS al arrancar (el autor, 2026-09-25: «siempre debe arrancar con la toma buena»): en CADA plan —análisis
    #     nuevo, «Reintentar», mejora, corte a mano—, no solo al analizar de cero como antes (así un vídeo ya hecho se
    #     arregla con «Reintentar»). Salvo que TÚ hayas cortado a mano el principio: eso manda.
    ti = tomas_arranque(ws)
    if ti.get("reinicio") and not any(r.get("manual") and float(r["inicio"]) <= 1.0 for r in recs):
        con_tomas, nota = aplica_tomas_falsas(out, ti, ws, audio, dur, cfg)
        if nota:
            out, recs = con_tomas, con_tomas["recortes"]
            info["notas"].append(nota)
            info["tomas"] = ti["reinicio"]["t"]

    # 0c) un corte de la IA de «silencio», «muletilla» o «tropiezo» que se lleva PALABRAS DE VERDAD (el autor, 2026-09-25:
    #     «me cortó la idea»): solo se quita el silencio que haya entre ellas
    if bordes_activo(cfg):
        protegidos = _protege_contenido(recs, ws, audio, nivel, p, oye=oye)
        if protegidos:
            info["notas"] += protegidos
            info["protegidos"] = len(protegidos)
    segs = segs_de(out)

    # 1) ARRANQUE: la primera palabra que el plan quiere enseñar, y dónde suena DE VERDAD
    s0 = segs[0][0]
    siguientes = [w for w in ws if float(w["end"]) > s0 + 0.02]
    hasta = (float(siguientes[0]["start"]) if siguientes else s0) + 40.0
    isl = islas(audio, max(0.0, s0 - 3.0), min(audio.dur, hasta), nivel, p)
    inicio, k_ini = None, None
    for w in ws:
        if float(w["end"]) <= s0 + 0.02:
            continue                                            # ya estaba fuera en el plan
        if float(w["start"]) > hasta - 10.0:
            break
        k = _isla_de(w, isl)
        if k is None:
            continue                                            # Whisper la oyó pero el audio no: no sirve de ancla
        I = isl[k]
        if I.ini < s0 - 0.01:
            # su voz empieza ANTES de donde arranca el vídeo: el corte de delante se la come (entera o una sílaba)
            nuevo = max(0.0, I.ini - p["preroll"])
            ajenas = sum(1 for x in ws if I.ini - 1.0 < float(x["end"]) <= s0 + 0.02 and _isla_de(x, isl) == k)
            if _puede_devolver(recs, nuevo, s0, s0 - I.ini, p, solo_suya=ajenas == 0):
                prueba_pl = copy.deepcopy(out)
                _devuelve(prueba_pl["recortes"], nuevo, s0)
                s_p = segs_de(prueba_pl)
                if s_p and abs(s_p[0][0] - nuevo) < 0.02:       # (un trozo de < 1 s el render lo tiraría: no vale)
                    texto = " ".join(str(x["word"]).strip() for x in ws if _isla_de(x, isl) == k)[:40]
                    out, recs = prueba_pl, prueba_pl["recortes"]
                    info["notas"].append(f"se devuelve «{texto}», que el corte de delante se comía "
                                         f"({s0 - I.ini:.2f}s de voz: Whisper la apuntaba tarde)")
                    inicio, k_ini = nuevo, k
                    break
            if I.fin > s0 + 0.1:
                inicio, k_ini = s0, k      # el corte cae a mitad de ese sonido y no se puede deshacer (a mano, o suena
                break                      # ahí algo que el plan quería quitar): se respeta tal cual
            continue                                           # entera dentro del corte: esa palabra se quita
        nuevo = I.ini - p["preroll"]
        if nuevo - s0 > 0.03:
            # entre el inicio y tu voz: ¿solo silencio/ruiditos, o sonido de verdad (música, un vídeo en pantalla)?
            sonido = sum(x.fin - x.ini for x in isl if _es_voz(x, p) and x.fin <= I.ini and x.ini >= s0 - 0.01)
            if nuevo - s0 > p["recorte_inicio_max"] and sonido > 8.0:
                info["notas"].append(f"AVISO: {nuevo - s0:.0f}s con sonido pero sin tu voz al principio — no los quito "
                                     "(¿una intro con música o una demo?)")
            else:
                recs.append({"inicio": round(s0, 3), "fin": round(nuevo, 3), "auto": "arranque",
                             "motivo": "silencio antes de tu primera palabra (medido en el audio)"})
                info["notas"].append(f"fuera {nuevo - s0:.2f}s de silencio antes de tu primera palabra")
        inicio, k_ini = max(s0, nuevo), k
        break

    # 1b) LOS BORDES de los cortes de la IA en el resto del vídeo: al silencio real, sin comerse sílabas
    if bordes_activo(cfg):
        info["bordes"] = _ajusta_bordes(out, ws, audio, nivel, segs_de, p, dur)
        recs = out["recortes"]
        if info["bordes"]:
            info["notas"].append(f"{info['bordes']} corte(s) de la IA con el borde llevado al silencio real "
                                 "(no se comen sílabas)")

    if inicio is not None and k_ini is not None:
        info["voz"] = isl[k_ini].ini

        # 2) LOS PRIMEROS SEGUNDOS: sin una palabra suelta de relleno delante («Bueno… [pausa] Hoy les…») y sin
        #    pausas largas («Una… [1,9 s] de las cosas que odio»): el gancho tiene que sonar de corrido
        segs = segs_de(out)
        isl2 = islas(audio, inicio, min(audio.dur, inicio + 60.0), nivel, p)
        de_voz: dict[int, list[str]] = {}
        probs: dict[int, list] = {}
        for w in ws:
            if float(w["end"]) <= inicio or float(w["start"]) > inicio + 62.0:
                continue
            k = _isla_de(w, isl2)
            if k is not None and _visto(segs, isl2[k].ini, isl2[k].fin) > 0.05:
                de_voz.setdefault(k, []).append(normalize(str(w["word"])).strip())
                probs.setdefault(k, []).append(w.get("p"))       # (las palabras vueltas a escuchar traen su probabilidad)
        orden = sorted(de_voz)
        # lo que dices ANTES de arrancar de verdad: una muletilla suelta («Bueno…»), un tarareo («da da da», 22-sep) o
        # un murmullo («oh… mmm») seguido de una pausa: fuera (hasta 3 seguidos)
        for _vuelta in range(3):
            if len(orden) < 2:
                break
            a0, a1 = isl2[orden[0]], isl2[orden[1]]
            toks = [t for x in de_voz[orden[0]] for t in x.split()]
            que = _relleno_suelto(toks, probs.get(orden[0]))
            if not que or _visto(segs, a0.fin, a1.ini) <= 0.6:
                break
            corte_fin = a1.ini - p["preroll"]
            recs.append({"inicio": round(inicio, 3), "fin": round(corte_fin, 3), "auto": "arranque",
                         "motivo": f"{que} antes de arrancar"})
            info["notas"].append(f"fuera {que} antes de arrancar")
            inicio = corte_fin
            info["voz"] = a1.ini
            orden = orden[1:]
            segs = segs_de(out)
        for k_a, k_b in zip(orden, orden[1:]):
            A, B = isl2[k_a], isl2[k_b]
            if _visto(segs, inicio, B.ini) > p["ventana_pausas"]:
                break
            hueco = _visto(segs, A.fin, B.ini)
            if hueco > p["pausa_max"]:
                # el trozo que queda delante del corte no puede bajar de 1 s: el render tira los trozos más cortos
                # (`pipeline._aplica_recortes`, pensado para las migas de la IA) y se llevaría la palabra con él
                ini_trozo = max((s for s, _e in segs if s <= A.fin + 1e-6), default=inicio)
                a = max(A.fin + p["pausa_antes"], ini_trozo + _MIN_TRAMO)
                b = B.ini - p["pausa_despues"]
                if b - a > 0.05:
                    corte = {"inicio": round(a, 3), "fin": round(b, 3), "auto": "arranque",
                             "motivo": f"pausa de {hueco:.1f}s al arrancar (medida en el audio)"}
                    recs.append(corte)
                    nuevos = segs_de(out)
                    if (_visto(nuevos, A.ini, A.fin) + 0.02 < _visto(segs, A.ini, A.fin)
                            or _visto(nuevos, B.ini, B.fin) + 0.02 < _visto(segs, B.ini, B.fin)):
                        recs.remove(corte)                     # se habría perdido una palabra: mejor la pausa entera
                        continue
                    segs = nuevos
                    info["notas"].append(f"pausa de {hueco:.1f}s al arrancar -> {_visto(segs, A.fin, B.ini):.1f}s")

    # 2b) LAS PAUSAS DE DUDA a media frase en todo el vídeo (las que el director vio y no marcó como de énfasis)
    if pausas and pausas_activo(cfg):
        n, ganado = _acorta_pausas(out, pausas, segs_de, p)
        if n:
            info["pausas"], info["pausas_seg"] = n, round(ganado, 1)
            info["notas"].append(f"{n} pausa(s) de duda a media frase dejadas en {p['pausa_media_deja']:.1f}s "
                                 f"(−{ganado:.1f}s)")

    # 3) EL FINAL: tu última palabra y el silencio que sobra detrás
    segs = segs_de(out)
    e = segs[-1][1]
    isl3 = islas(audio, max(0.0, e - 30.0), e, nivel, p)
    k_ult = None
    for w in reversed(ws):
        if float(w["start"]) >= e:
            continue
        if float(w["end"]) < e - 30.0:
            break
        k = _isla_de(w, isl3)
        if k is not None and isl3[k].ini < e:
            k_ult = k
            break
    if k_ult is not None:
        fin_voz = isl3[k_ult].fin
        for x in isl3[k_ult + 1:]:
            # Whisper apunta las últimas palabras hasta ~1 s ANTES de tiempo (16-sep: «…de Mi canal» suena hasta el
            # 795,4 y Whisper lo acaba en 794,1): lo que suena pegado detrás, con duración de palabra, también es tu voz
            if x.ini >= e:
                break
            if _es_voz(x, p) and x.ini - fin_voz <= 1.0:
                fin_voz = x.fin
        fin_voz = min(fin_voz, e)
        cola = e - fin_voz
        despues = [x for x in isl3 if _es_voz(x, p) and fin_voz + 0.05 < x.ini < e]     # risa, música… detrás
        if cola > p["cola_max"] and not despues:
            corte = fin_voz + p["cola"]
            recs.append({"inicio": round(corte, 3), "fin": round(e, 3), "auto": "final",
                         "motivo": "silencio tras tu última palabra (medido en el audio)"})
            info["notas"].append(f"fuera {e - corte:.1f}s de silencio al final")
            cola = p["cola"]
        info["cola"] = round(cola, 3)
    return out, info


def cortes_silenciosos(plan: dict) -> list[tuple[float, float]]:
    """Los cortes de `ajusta_plan` que NO deben sonar (el tono etéreo de un corte, a 1 s del arranque, entre «Una» y
    «de las cosas que odio», o a media frase tras acortar una pausa, sonaría a fallo)."""
    return [(float(r["inicio"]), float(r["fin"])) for r in plan.get("recortes") or []
            if r.get("auto") in _AUTO and float(r["fin"]) > float(r["inicio"])]


def cortes_del_programa(plan: dict) -> list[tuple[float, float]]:
    """Lo que cortó el PROGRAMA (no la IA) en medio del vídeo: pausas acortadas, silencios del arranque y el trocito
    que añadió a un corte de la IA al llevar su borde al silencio. Una tarjeta, imagen o ilustración que la IA puso ahí
    no debe perderse: sale donde sigue tu voz (`pipeline._remapea`)."""
    out = []
    for r in plan.get("recortes") or []:
        a, b = float(r["inicio"]), float(r["fin"])
        if r.get("auto") in ("arranque", "pausa", "tomas_falsas"):
            out.append((a, b))
        if "inicio_ia" in r and a < float(r["inicio_ia"]):
            out.append((a, float(r["inicio_ia"])))
        if "fin_ia" in r and b > float(r["fin_ia"]):
            out.append((float(r["fin_ia"]), b))
    return out


# ------------------------------------------------------------------ tomas falsas al arrancar
def _toks(texto: str) -> list[str]:
    return normalize(texto).split()


def _firma(ws: list[dict], i: int, n: int = 6, pausa: float | None = None) -> tuple[list[str], int]:
    """Las primeras `n` palabras con contenido a partir de la palabra `i` (sin «eh», «bueno», «listo» delante),
    sin pasar de la siguiente pausa de `pausa` s o más (una toma no se mezcla con la siguiente), y el índice de la
    palabra que sigue a las usadas (para no comparar una firma consigo misma corrida una palabra)."""
    out: list[str] = []
    j = i
    while j < len(ws):
        if pausa is not None and j > i and float(ws[j]["start"]) - float(ws[j - 1]["end"]) >= pausa:
            break
        for t in _toks(str(ws[j].get("word", ""))):
            if not out and t in _MULETILLAS:
                continue
            out.append(t)
        j += 1
        if len(out) >= n:
            break
    return out[:n], j


def _coinciden(a: list[str], b: list[str]) -> tuple[int, float, int]:
    """(palabras iguales en orden, parecido 0-1, iguales desde el principio)."""
    if not a or not b:
        return 0, 0.0, 0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    iguales = sum(m.size for m in sm.get_matching_blocks())
    prefijo = 0
    for x, y in zip(a, b):
        if x != y:
            break
        prefijo += 1
    return iguales, sm.ratio(), prefijo


def tomas_arranque(words: list[dict], ventana: float = 240.0, pausa: float = 0.9) -> dict:
    """Las tomas del ARRANQUE (habla separada por pausas) y si repites el inicio. Solo con la transcripción (lo
    fino del corte lo pone luego el audio). Devuelve {"tomas": [{ini, fin, texto, pausa}], "reinicio": el último
    reinicio CLARO o None, "sospecha": uno dudoso posterior o None} — cada reinicio = {"idx", "t", "confianza":
    "alta"|"media", "motivo", "palabra"}. el autor suele hacer unas tres tomas falsas antes de la buena (2026-09-24):
    «hola qué tal muchachos… [se traba]», «hola qué tal muchachos, hoy…», y a la tercera sigue.

    Reglas (conservadoras: cortar tu arranque de verdad es peor que dejar una toma falsa, que luego se quita con
    una mejora). Reinicio CLARO si, dentro de los primeros minutos: (0) la toma entera (≤ 6 palabras, ≤ 12 s) es el
    PRINCIPIO de la siguiente, tras ≥ 0,6 s de pausa (`_es_inicio_de`: «¿Cómo estás?» … «¿Cómo estás apareciendo hoy
    día…», su vídeo del 24-sep — solo se ve con la transcripción vuelta a escuchar frase por frase,
    `transcribe.revisa_arranque`: Whisper con el audio entero funde las dos tomas en una); (1) tras una pausa de 1,5 s o más, una toma de
    ≤ 45 s va seguida de otra que empieza IGUAL (las 2 primeras palabras, ≥ 3 en orden y parecido ≥ 0,5 — una
    pausa normal de 1 s entre dos frases que empiezan parecido, «En este vídeo les voy… En este vídeo también…»,
    NO basta); (2) sin pausa, si se repite casi literal (≥ 5 palabras desde el principio) tras ≤ 20 s; (3) un
    saludo de ≤ 12 s seguido de otro saludo («Hola.» … «Hola muchachos…»); (4) «ahora sí», «de nuevo», «otra vez»…
    al volver a empezar tras una toma de ≤ 20 s. Un parecido más flojo es solo una SOSPECHA (se le cuenta al
    director, no se corta). Una toma que ya lleva 90 s sin reinicio ES la buena: después no se busca más. La
    toma buena tiene que seguir al menos 30 s (si no, no se corta nada: vídeo corto o grabación rara)."""
    ws = [w for w in words if str(w.get("word", "")).strip() and float(w.get("end", 0)) >= float(w.get("start", 0))]
    ws.sort(key=lambda w: float(w["start"]))
    if not ws:
        return {"tomas": [], "reinicio": None}
    t_ini = float(ws[0]["start"])
    win = [w for w in ws if float(w["start"]) <= t_ini + ventana]

    # las tomas separadas por pausas (para contárselo al director)
    tomas, a = [], 0
    for i in range(1, len(win) + 1):
        corte = i == len(win) or float(win[i]["start"]) - float(win[i - 1]["end"]) >= pausa
        if corte:
            tx = " ".join(str(w["word"]).strip() for w in win[a:i])
            pz = (float(win[i]["start"]) - float(win[i - 1]["end"])) if i < len(win) else None
            tomas.append({"ini": round(float(win[a]["start"]), 1), "fin": round(float(win[i - 1]["end"]), 1),
                          "texto": tx[:80] + ("…" if len(tx) > 80 else ""), "pausa": round(pz, 1) if pz else None})
            a = i
            if len(tomas) >= 8:
                break

    c, reinicio, sospecha = 0, None, None
    for i in range(1, len(win)):
        t_i = float(win[i]["start"])
        dura = float(win[i - 1]["end"]) - float(win[c]["start"])
        if t_i - float(win[c]["start"]) > 90.0:
            break
        pz = t_i - float(win[i - 1]["end"])
        con_pausa = pz >= pausa
        (fa, fin_a), (fb, _fin_b) = _firma(win, c, 6, pausa), _firma(win, i, 6, pausa)
        iguales, parecido, prefijo = _coinciden(fa, fb)
        motivo, conf = None, "alta"
        toma_a = _toks_toma(win, c, i) if pz >= 0.6 and dura <= 12.0 else []
        if toma_a and _es_inicio_de(toma_a, _firma(win, i, 8, pausa)[0]):
            # (2026-09-25) la toma entera era solo el PRINCIPIO de la siguiente: «¿Cómo estás?» [2 s] «¿Cómo estás
            # apareciendo hoy día…». Con 2 palabras las reglas de abajo (≥ 3 iguales) no lo veían
            motivo = f"«{' '.join(fb[:5])}…» vuelve a empezar lo que decías («{' '.join(toma_a)}…»)"
        elif pz >= 1.5 and dura <= 45.0 and prefijo >= 2 and iguales >= 3 and parecido >= 0.5:
            motivo = f"«{' '.join(fb[:5])}…» repite cómo empezaba la toma anterior"
        elif not con_pausa and i >= fin_a and dura <= 20.0 and prefijo >= 5:
            motivo = f"«{' '.join(fb[:5])}…» se repite casi igual sin pausa"
        elif (con_pausa and dura <= 12.0 and fa and fb and fa[0] in _SALUDO_INICIAL
              and fb[0] in _SALUDO_INICIAL):
            motivo = "otro saludo tras una toma corta"
        elif con_pausa and dura <= 30.0 and _marca_reinicio(win, c, i):
            motivo = f"dices «{_marca_reinicio(win, c, i)}» y vuelves a empezar"
            conf = "alta" if dura <= 20.0 else "media"
        elif con_pausa and dura <= 60.0 and iguales >= 2 and parecido >= 0.4:
            motivo, conf = f"«{' '.join(fb[:5])}…» se parece a cómo empezaba la toma anterior", "media"
        if not motivo:
            continue
        hallado = {"idx": i, "t": round(t_i, 2), "confianza": conf, "motivo": motivo, "palabra": dict(win[i])}
        if conf == "alta":
            c, reinicio, sospecha = i, hallado, None           # la toma buena empieza aquí (hasta que se vea otra)
        else:
            sospecha = hallado                                  # una sospecha no mueve el arranque: solo se cuenta
    fin_voz = float(ws[-1]["end"])
    if reinicio and fin_voz - reinicio["t"] < 30.0:
        reinicio = None
    if sospecha and fin_voz - sospecha["t"] < 30.0:
        sospecha = None
    return {"tomas": tomas, "reinicio": reinicio, "sospecha": sospecha}


def _toks_toma(win: list[dict], c: int, i: int) -> list[str]:
    """Las palabras de la toma [c, i) sin las muletillas del principio («bueno, ¿cómo estás?» -> «como estas»)."""
    out: list[str] = []
    for w in win[c:i]:
        for t in _toks(str(w.get("word", ""))):
            if not out and t in _MULETILLAS:
                continue
            out.append(t)
    return out


def _marca_reinicio(win: list[dict], c: int, i: int) -> str | None:
    """«ahora sí», «de nuevo», «otra vez»… al principio de la toma nueva o al final de la abandonada."""
    inicio_txt = " ".join(t for w in win[i:i + 4] for t in _toks(str(w["word"])))
    fin_txt = " ".join(t for w in win[max(c, i - 4):i] for t in _toks(str(w["word"])))
    return next((m for m in _REINICIO if re.search(rf"\b{m}\b", inicio_txt) or re.search(rf"\b{m}\b", fin_txt)), None)


def texto_prompt(info: dict | None) -> str:
    """Lo que ve el programa del arranque, en una línea, para el prompt del director (sin llaves: va en un .format)."""
    if not info or not info.get("tomas"):
        return "(no hay voz transcrita al principio)"
    partes = [f"[{t['ini']:.1f}s-{t['fin']:.1f}s] «{t['texto']}»" + (f" (luego {t['pausa']:.1f}s de pausa)"
                                                                     if t.get("pausa") else "")
              for t in info["tomas"][:6]]
    r, s = info.get("reinicio"), info.get("sospecha")
    if r:
        veredicto = (f"El programa VE que reinicias el arranque en el segundo {r['t']:.1f} ({r['motivo']}): lo de antes "
                     "son tomas falsas y el programa YA las corta midiendo el audio — NO las pongas tú en recortes (tus "
                     "segundos, sacados de la transcripción, podrían comerse la primera palabra de la toma buena).")
    else:
        veredicto = "El programa no ve ningún reinicio claro"
    if s:
        veredicto += (f"{' Además' if r else '; pero'} SOSPECHA otro reinicio en el segundo {s['t']:.1f} ({s['motivo']}), "
                      "sin estar seguro: decide tú con la regla de ARRANQUE CON VARIAS TOMAS.")
    elif not r:
        veredicto += "; si tú lo ves (regla de ARRANQUE CON VARIAS TOMAS), córtalo."
    return "; ".join(partes) + ". " + veredicto


def aplica_tomas_falsas(plan: dict, info: dict | None, words: list[dict], audio: Audio | None, dur: float,
                        cfg=None) -> tuple[dict, str | None]:
    """Si `tomas_arranque` vio CLARO que repites el inicio, corta todo lo anterior a la toma buena: hasta
    `preroll` antes de que suene su primera palabra (medido en el audio si lo hay). Solo al analizar de cero:
    así, si una mejora pide recuperar ese arranque, la IA quita este corte y ya no vuelve (queda en el plan como
    un recorte más, con `"auto": "tomas_falsas"`). Devuelve (plan, nota para el log o None)."""
    r = (info or {}).get("reinicio")                      # solo el reinicio CLARO; una sospecha la decide el director
    if not r:
        return plan, None
    p = _params(cfg)
    w = r["palabra"]
    corte = float(w["start"]) - 0.3
    if audio is not None:
        isl = islas(audio, max(0.0, float(w["start"]) - 3.0), float(w["end"]) + 3.0, nivel_voz(audio, words), p)
        k = _isla_de(w, isl)
        if k is not None:
            corte = isl[k].ini - p["preroll"]
            if k > 0 and isl[k - 1].fin > corte:                # la pausa entre tomas es más corta que el aire: no
                corte = max(corte, isl[k - 1].fin + 0.03)      # dejes el final de la toma falsa
    if corte <= 0.5 or corte > min(300.0, 0.35 * dur):
        return plan, None
    out = copy.deepcopy(plan)
    out.setdefault("recortes", []).append({"inicio": 0.0, "fin": round(corte, 3), "auto": "tomas_falsas",
                                           "motivo": f"tomas falsas del arranque: {r['motivo']}"})
    return out, f"tomas falsas al arrancar: el vídeo empieza en tu toma buena ({corte:.1f}s; {r['motivo']})"


# ------------------------------------------------------------------ diagnóstico a mano
def diagnostico(work_dir: Path) -> None:
    """`python -m clipper.voz work/<vídeo>--pro`: lo que haría con cada plan guardado de ese vídeo (no cambia nada)."""
    import json
    from . import config, director, pipeline
    cfg = config.load()
    words = json.loads((work_dir / "words.json").read_text(encoding="utf-8"))
    audio = Audio(work_dir / "audio.wav")
    ws = [w for w in words if str(w.get("word", "")).strip()]
    print(f"audio {audio.dur:.1f}s · tu nivel de voz {nivel_voz(audio, ws):.1f} dBFS")
    info = tomas_arranque(words)
    print("arranque:", texto_prompt(info))
    act = []
    if (work_dir / "actividad_pantalla.json").is_file():
        act = json.loads((work_dir / "actividad_pantalla.json").read_text(encoding="utf-8"))
        act = act.get("ventanas", act) if isinstance(act, dict) else act
    cands = pausas_candidatas(words, audio, act, cfg)
    print(f"pausas de duda a media frase: {len(cands)} ({sum(c['dur'] for c in cands):.1f}s) — lo que vería el director:")
    print("   " + texto_pausas(cands))
    for pp in sorted(work_dir.glob("director_*.json")):
        slug = pp.stem.replace("director_", "", 1)
        plan = director.cargar(work_dir, slug)
        if not plan:
            continue
        simulado = "pausas_vistas" not in plan
        if simulado:                               # un plan de antes: como si el director las viera todas y no vetara ninguna
            plan = {**plan, "pausas_vistas": [c["id"] for c in cands]}
        nuevo, inf = ajusta_plan(plan, words, audio, audio.dur, lambda pl: pipeline._segs_de_plan(pl, audio.dur, cfg), cfg,
                                 pausas=cands)
        antes = pipeline._segs_de_plan(revierte(plan), audio.dur, cfg)
        despues = pipeline._segs_de_plan(nuevo, audio.dur, cfg)
        print(f"- {slug}: empezaba en {antes[0][0]:.2f}s -> ahora {despues[0][0]:.2f}s (voz en {inf['voz']}); "
              f"acababa en {antes[-1][1]:.2f}s -> {despues[-1][1]:.2f}s (cola {inf['cola']}); "
              f"dura {sum(e - s for s, e in antes):.1f}s -> {sum(e - s for s, e in despues):.1f}s"
              + (" [pausas simuladas: plan de antes]" if simulado else "") + "; " + "; ".join(inf["notas"]))
        for r in nuevo.get("recortes") or []:
            if "inicio_ia" in r or ("fin_ia" in r and float(r["fin"]) > float(r["fin_ia"])):
                print(f"     borde: {r.get('inicio_ia', r['inicio'])}-{r.get('fin_ia', r['fin'])} -> "
                      f"{r['inicio']}-{r['fin']}  ({r.get('motivo', '')[:50]})")


# ------------------------------------------------------------------ pruebas (sin Gemini, sin red)
def _wav_sintetico(ruta: Path, dur: float, voz: list[tuple[float, float]], ruidos: list[tuple[float, float, float]] = (),
                   sr: int = 16000) -> None:
    """Un audio de prueba: silencio (−80 dBFS), «voz» (armónicos a −20 dBFS con ataque de 10 ms) en los tramos de
    `voz` y ruiditos (inicio, fin, dBFS) — como el clic de la grabación real."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(7)
    x = rng.normal(0, 10 ** (-80 / 20), n)
    for a, b in voz:
        i0, i1 = int(a * sr), int(b * sr)
        tt = t[i0:i1] - a
        env = np.minimum(1.0, np.minimum(tt, (b - a) - tt) / 0.01)
        s = 0.5 * np.sin(2 * np.pi * 180 * tt) + 0.3 * np.sin(2 * np.pi * 360 * tt) + 0.2 * np.sin(2 * np.pi * 540 * tt)
        x[i0:i1] += s * env * (10 ** (-20 / 20)) / 0.44
    for a, b, lvl in ruidos:
        i0, i1 = int(a * sr), int(b * sr)
        x[i0:i1] += rng.normal(0, 10 ** (lvl / 20), i1 - i0)
    pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _palabras_de(pares: list[tuple[str, float, float]]) -> list[dict]:
    return [{"word": p, "start": a, "end": b} for p, a, b in pares]


def prueba() -> list[dict]:
    """Casos sacados de las grabaciones REALES (22-sep y 16-sep) con un audio sintético equivalente, más las tomas
    falsas que describe el autor. Devuelve [{caso, ok, detalle}]."""
    import tempfile
    filas: list[dict] = []

    def r(caso, ok, detalle=""):
        filas.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    from . import pipeline                      # (aquí dentro: pipeline importa este módulo)

    def segs_de_simple(dur):
        # la MISMA cuenta que el render (con su regla de tirar los trozos de menos de 1 s)
        return lambda pl: pipeline._aplica_recortes([(0.0, dur)], pl.get("recortes") or [], dur)

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-voz-"))
    try:
        # --- A: una palabra que Whisper apunta TARDE y el corte de «silencio» de Gemini se come (sintético; se armó con
        #        la del 22-sep creyendo que ese sonido era «Una» — era un tarareo: el caso real de verdad está en M).
        wa = tmp / "a.wav"
        _wav_sintetico(wa, 40.0, [(2.99, 3.97), (5.85, 9.0), (9.25, 14.0), (14.3, 38.8)],
                       ruidos=[(1.6, 1.9, -48), (4.75, 4.85, -35), (5.25, 5.35, -41)])
        au = Audio(wa)
        words_a = _palabras_de([("Una", 3.95, 4.47), ("de", 5.49, 6.01), ("las", 6.01, 6.17), ("cosas", 6.17, 6.6),
                                ("que", 6.6, 6.8), ("odio", 6.8, 7.3)] +
                               [(f"p{i}", 9.3 + i * 0.5, 9.7 + i * 0.5) for i in range(9)] +
                               [(f"q{i}", 14.4 + i * 0.5, 14.8 + i * 0.5) for i in range(48)])
        plan_a = {"recortes": [{"inicio": 0.0, "fin": 4.0, "motivo": "silencio al arranque"},
                               {"inicio": 20.0, "fin": 22.0, "motivo": "toma repetida o titubeo descartado"}]}
        sd = segs_de_simple(au.dur)
        nuevo, info = ajusta_plan(plan_a, words_a, au, au.dur, sd)
        s = sd(nuevo)
        r("palabra apuntada tarde: el vídeo vuelve a empezar con ella (0,15 s antes de su voz), no 1,85 s de silencio después",
          abs(s[0][0] - (2.99 - 0.15)) < 0.03, f"empieza en {s[0][0]:.2f}s · {info['notas']}")
        r("palabra apuntada tarde: el corte de Gemini queda acortado y guarda su final original (fin_ia)",
          nuevo["recortes"][0].get("fin_ia") == 4.0, nuevo["recortes"][0])
        r("los cortes de MÁS ADELANTE no se tocan al devolver la primera palabra (el 1:45 del vídeo -6 se anulaba)",
          nuevo["recortes"][1] == plan_a["recortes"][1] and _visto(s, 20.0, 22.0) == 0.0, nuevo["recortes"][1])
        pausa = [x for x in nuevo["recortes"] if x.get("auto") == "arranque" and x["inicio"] > 3.5]
        hueco = _visto(s, 3.97, 5.85)
        r("palabra apuntada tarde: la pausa de 1,9 s de detrás se queda en 0,3 s (y se van el clic y el ruidito)",
          len(pausa) == 1 and abs(hueco - 0.30) < 0.03, f"{pausa} · hueco {hueco:.2f}s")
        r("palabra apuntada tarde: ninguna voz se corta (todo lo que suena de 2,99 a 3,97 y desde 5,85 se ve)",
          _visto(s, 2.99, 3.97) > 0.97 and _visto(s, 5.85, 9.0) > 3.14, f"{s[:3]}")
        otra, _ = ajusta_plan(nuevo, words_a, au, au.dur, sd)
        r("es idempotente: aplicarlo otra vez da exactamente el mismo vídeo", sd(otra) == s, f"{sd(otra)[:3]}")
        r("`revierte` devuelve el plan original de Gemini", revierte(nuevo)["recortes"] == plan_a["recortes"],
          revierte(nuevo)["recortes"])
        r("los cortes del programa no suenan (sin tono etéreo a 1 s del arranque)",
          any(abs(a - 4.09) < 0.03 for a, _b in cortes_silenciosos(nuevo))
          and all(x.get("auto") for x in nuevo["recortes"] if (float(x["inicio"]), float(x["fin"]))
                  in cortes_silenciosos(nuevo)), cortes_silenciosos(nuevo))

        # --- A2: si ese corte lo hubieras hecho TÚ a mano, no se toca (solo se quita el silencio de detrás)
        plan_m = {"recortes": [{"inicio": 0.0, "fin": 4.0, "motivo": "silencio"},
                               {"inicio": 4.0, "fin": 5.32, "motivo": "corte manual (editor)", "manual": True}]}
        nuevo_m, _ = ajusta_plan(plan_m, words_a, au, au.dur, sd)
        sm_ = sd(nuevo_m)
        r("con tu corte a mano (4,0-5,32, como el de la versión 2 real): no se devuelve «Una», y se quita el "
          "silencio hasta tu voz (5,85)", abs(sm_[0][0] - (5.85 - 0.15)) < 0.03, f"empieza en {sm_[0][0]:.2f}s")

        # --- A3: da igual cómo llame la IA al corte («toma falsa»): si en ese sonido SOLO suena la palabra que el plan
        #          quería dejar («Una»: Whisper la pone después del corte), se devuelve
        plan_t = {"recortes": [{"inicio": 0.0, "fin": 4.0, "motivo": "toma falsa del arranque"}]}
        nuevo_t, _ = ajusta_plan(plan_t, words_a, au, au.dur, sd)
        r("un corte de la IA con otro nombre («toma falsa») que se come una palabra que ella quería dejar: se devuelve",
          abs(sd(nuevo_t)[0][0] - (2.99 - 0.15)) < 0.03, f"empieza en {sd(nuevo_t)[0][0]:.2f}s")
        # --- A4: …pero si en ese mismo sonido suena también algo que la IA quería quitar («eh» de la toma falsa, pegado
        #          a «Una»), el corte se respeta y el vídeo empieza en la voz siguiente
        wa4 = tmp / "a4.wav"
        _wav_sintetico(wa4, 40.0, [(3.1, 3.97), (5.85, 38.8)])
        a4u = Audio(wa4)
        words_a4 = _palabras_de([("eh", 3.1, 3.3), ("Una", 3.95, 4.47), ("de", 5.49, 6.01)] +
                                [(f"q{i}", 6.1 + i * 0.5, 6.5 + i * 0.5) for i in range(64)])
        sd4 = segs_de_simple(a4u.dur)
        n4, _ = ajusta_plan({"recortes": [{"inicio": 0.0, "fin": 4.0, "motivo": "toma falsa del arranque"}]}, words_a4,
                            a4u, a4u.dur, sd4)
        r("…pero si en ese sonido suena también algo que la IA quería quitar, el corte se respeta (empieza en la voz "
          "siguiente, sin trozos a medias)", abs(sd4(n4)[0][0] - (5.85 - 0.15)) < 0.03, f"empieza en {sd4(n4)[0][0]:.2f}s")

        # --- B: la grabación del 16-sep: «Hice» suena desde 43,40 y Gemini cortó hasta 43,50 (le quitaba la «H»)
        wb = tmp / "b.wav"
        _wav_sintetico(wb, 80.0, [(30.5, 31.5), (43.40, 43.78), (43.85, 78.0)], ruidos=[(3.5, 7.0, -40)])
        bu = Audio(wb)
        # (Whisper da a «Hice» 43,50–44,04: pisa su isla Y el principio de la siguiente, como en el real)
        words_b = _palabras_de([("Hice", 43.5, 44.04), ("una", 44.04, 44.44), ("página", 44.44, 44.62)] +
                               [(f"x{i}", 44.6 + i * 0.5, 45.0 + i * 0.5) for i in range(66)])
        plan_b = {"recortes": [{"inicio": 0.0, "fin": 43.5, "motivo": "silencio puro al inicio del directo"}]}
        sdb = segs_de_simple(bu.dur)
        nb, ib = ajusta_plan(plan_b, words_b, bu, bu.dur, sdb)
        r("16-sep: se devuelve la «H» de «Hice» (empieza 0,15 s antes de 43,40, no en 43,50)",
          abs(sdb(nb)[0][0] - (43.40 - 0.15)) < 0.03, f"empieza en {sdb(nb)[0][0]:.2f}s · {ib['notas']}")
        r("16-sep: el golpe de 30,5 s (sin palabras) no cuenta como tu arranque", sdb(nb)[0][0] > 43.0)

        # --- C: sin ningún corte al principio: 5 s de silencio y luego hablas -> fuera el silencio
        wc = tmp / "c.wav"
        _wav_sintetico(wc, 60.0, [(5.0, 52.0)])
        cu = Audio(wc)
        words_c = _palabras_de([(f"w{i}", 5.05 + i * 0.5, 5.45 + i * 0.5) for i in range(93)])
        sdc = segs_de_simple(cu.dur)
        nc, ic = ajusta_plan({"recortes": []}, words_c, cu, cu.dur, sdc)
        r("sin cortes al principio: el silencio de delante se quita solo (empieza 0,15 s antes de la voz)",
          abs(sdc(nc)[0][0] - 4.85) < 0.03, f"empieza en {sdc(nc)[0][0]:.2f}s")
        r("final: 8 s de silencio tras tu última palabra se quedan en 0,7 s", abs(sdc(nc)[-1][1] - 52.7) < 0.03
          and abs(ic["cola"] - 0.7) < 0.01, f"acaba en {sdc(nc)[-1][1]:.2f}s · cola {ic['cola']}")

        # --- D: «Bueno… [1,2 s] Hoy les voy a enseñar…»: la muletilla suelta se va
        wd = tmp / "d.wav"
        _wav_sintetico(wd, 50.0, [(2.0, 2.4), (3.6, 48.0)])
        du = Audio(wd)
        words_d = _palabras_de([("Bueno.", 2.0, 2.4)] + [(f"h{i}", 3.65 + i * 0.5, 4.05 + i * 0.5) for i in range(88)])
        sdd = segs_de_simple(du.dur)
        nd, idd = ajusta_plan({"recortes": [{"inicio": 0.0, "fin": 1.9, "motivo": "silencio"}]}, words_d, du, du.dur, sdd)
        r("«Bueno…» suelto con pausa delante de la frase: fuera, el vídeo empieza en la frase",
          abs(sdd(nd)[0][0] - (3.6 - 0.15)) < 0.03, f"empieza en {sdd(nd)[0][0]:.2f}s · {idd['notas']}")

        # --- D2: «Mira… [1,5 s] esto es lo que pasa…»: «Mira» es corto (0,3 s); el trozo que queda antes del corte de la
        #          pausa no puede bajar de 1 s (el render lo tiraría, y con él la palabra): la pausa se acorta menos
        wd2 = tmp / "d2.wav"
        _wav_sintetico(wd2, 50.0, [(2.0, 2.3), (3.8, 48.0)])
        d2u = Audio(wd2)
        words_d2 = _palabras_de([("Mira", 2.0, 2.3)] + [(f"g{i}", 3.85 + i * 0.5, 4.25 + i * 0.5) for i in range(88)])
        sdd2 = segs_de_simple(d2u.dur)
        nd2, _ = ajusta_plan({"recortes": [{"inicio": 0.0, "fin": 1.9, "motivo": "silencio"}]}, words_d2, d2u, d2u.dur,
                             sdd2)
        s2 = sdd2(nd2)
        r("una palabra corta al arrancar («Mira») no se pierde por acortar la pausa de detrás (ni queda un trozo de "
          "< 1 s)", _visto(s2, 2.0, 2.3) > 0.29 and all(e - s >= 1.0 for s, e in s2) and _visto(s2, 2.3, 3.8) < 1.5,
          f"{s2[:3]}")

        # --- E: una intro con 30 s de MÚSICA (sonido fuerte sin tu voz) no se recorta sola
        we = tmp / "e.wav"
        _wav_sintetico(we, 90.0, [(0.5, 30.0), (31.0, 88.0)])
        eu = Audio(we)
        words_e = _palabras_de([(f"m{i}", 31.05 + i * 0.5, 31.45 + i * 0.5) for i in range(110)])
        sde = segs_de_simple(eu.dur)
        ne, ie = ajusta_plan({"recortes": []}, words_e, eu, eu.dur, sde)
        r("30 s de música/sonido sin tu voz al principio: no se tocan (avisa en el log)",
          sde(ne)[0][0] == 0.0 and any("AVISO" in n for n in ie["notas"]), ie["notas"])

        # --- F: tomas falsas (lo que describe el autor) -----------------------------------------------------------
        def ws_(*tomas):
            out = []
            for t0, texto in tomas:
                for j, pal in enumerate(texto.split()):
                    out.append({"word": pal, "start": t0 + 0.4 * j, "end": t0 + 0.4 * j + 0.3})
            return out
        largo = " ".join(f"contenido{i}" for i in range(120))
        tres = ws_((2.0, "hola qué tal muchachos hoy les"), (8.0, "hola qué tal muchachos bienvenidos a"),
                   (15.0, "eh bueno hola qué tal"), (22.0, "hola qué tal muchachos bienvenidos a un nuevo video " + largo))
        ti = tomas_arranque(tres)
        r("tres tomas falsas que empiezan igual: el arranque bueno es la cuarta (22 s), con confianza alta",
          ti["reinicio"] and ti["reinicio"]["t"] == 22.0 and ti["reinicio"]["confianza"] == "alta", ti["reinicio"])
        normal = ws_((1.0, "una de las cosas que odio de generar contenido " + largo))
        r("un arranque normal (sin repetir nada) no se toca", tomas_arranque(normal)["reinicio"] is None)
        # la grabación del 16-sep: «100 mil pesos aproximadamente… ¿Listo? Unos 100 mil pesos…» NO es un reinicio
        real16 = ws_((43.5, "hice una página web 100 % con ia la nueva página web de mi canal " + largo[:400]),
                     (88.9, "100 mil pesos aproximadamente listo unos 100 mil pesos aproximadamente " + largo))
        r("repetir una cifra a mitad de explicación (16-sep) no es un reinicio",
          tomas_arranque(real16)["reinicio"] is None, tomas_arranque(real16)["reinicio"])
        sin_pausa = [{"word": p_, "start": 1.0 + 0.3 * j, "end": 1.25 + 0.3 * j} for j, p_ in enumerate(
            ("hola muchachos hoy vamos a ver no espera hola muchachos hoy vamos a ver cómo " + largo).split())]
        rs = tomas_arranque(sin_pausa)["reinicio"]
        r("reinicio SIN pausa («…no, espera. Hola muchachos, hoy vamos a ver…»): lo ve", rs and abs(rs["t"] - 3.4) < 0.01,
          rs)
        paralelo = ws_((1.0, "en este video les voy a mostrar el seo"),
                       (5.5, "en este video también les voy a mostrar la web " + largo))
        pr = tomas_arranque(paralelo)
        r("dos frases que empiezan parecido tras una pausa normal (1 s) NO se cortan: como mucho es una sospecha",
          pr["reinicio"] is None and pr["sospecha"] is not None, pr)
        saludos = ws_((1.0, "hola"), (4.0, "hola muchachos"), (8.0, "hola muchachos cómo están hoy " + largo))
        rs2 = tomas_arranque(saludos)["reinicio"]
        r("«Hola.» [pausa] «Hola muchachos.» [pausa] «Hola muchachos, cómo están…»: arranca en el tercero",
          rs2 and rs2["t"] == 8.0, rs2)
        muy_largo = " ".join(f"tema{i}" for i in range(180))            # ≈ 72 s de toma
        largo_ok = ws_((1.0, "hola muchachos " + muy_largo), (80.0, "hola muchachos otra cosa " + largo))
        lo = tomas_arranque(largo_ok)
        r("una toma de más de 45 s no se considera falsa aunque luego vuelvas a saludar igual",
          lo["reinicio"] is None and lo["sospecha"] is None, lo)
        ahora = ws_((1.0, "bueno pues hoy vamos a hablar de"), (6.0, "listo ahora sí hoy les quiero contar " + largo))
        rs3 = tomas_arranque(ahora)["reinicio"]
        r("«…listo, ahora sí, hoy les quiero contar…» tras una toma corta: reinicio claro", rs3 and rs3["t"] == 6.0, rs3)
        corto = ws_((1.0, "hola muchachos"), (5.0, "hola muchachos esto es todo"))
        r("si tras el reinicio no hay ni 30 s de vídeo, no se corta nada", tomas_arranque(corto)["reinicio"] is None)
        r("el texto para el director no lleva llaves (va dentro de un .format)",
          "{" not in texto_prompt(ti) and "}" not in texto_prompt(ti) and "22.0" in texto_prompt(ti), texto_prompt(ti))

        # --- G: aplica_tomas_falsas corta justo antes de la voz de la toma buena (medida en el audio)
        wg = tmp / "g.wav"
        _wav_sintetico(wg, 90.0, [(2.0, 4.4), (8.0, 10.8), (15.0, 16.9), (22.35, 88.0)])
        gu = Audio(wg)
        tres_g = [dict(w) for w in tres]
        tig = tomas_arranque(tres_g)
        ng, nota = aplica_tomas_falsas({"recortes": []}, tig, tres_g, gu, gu.dur)
        cg = [x for x in ng["recortes"] if x.get("auto") == "tomas_falsas"]
        r("con las tomas falsas vistas claras: un corte de 0 hasta 0,15 s antes de que suene la toma buena (22,35)",
          len(cg) == 1 and abs(cg[0]["fin"] - (22.35 - 0.15)) < 0.03, f"{cg} · {nota}")
        nada, _ = aplica_tomas_falsas({"recortes": []}, tomas_arranque(normal), normal, gu, gu.dur)
        r("sin reinicio claro no se añade ningún corte", not nada["recortes"])

        # --- H: PAUSAS DE DUDA A MEDIA FRASE (el autor, 2026-09-24: dinámico, pero sin quitar sus silencios de énfasis)
        def habla(tramos):
            """[(ini, fin, "texto")] -> las palabras repartidas por cada tramo con voz, como las da Whisper."""
            out = []
            for s_, e_, texto in tramos:
                pals = texto.split()
                paso = (e_ - s_) / len(pals)
                for j, pal_ in enumerate(pals):
                    out.append({"word": pal_, "start": round(s_ + j * paso, 3), "end": round(s_ + (j + 0.85) * paso, 3)})
            return out

        largo_h = " ".join(f"cosa{i}" for i in range(60))
        tramos_h = [(1.0, 10.0, "hola a todos hoy les quiero contar algo que me pasó con el seo de mi web"),
                    (10.4, 13.0, "esta es la clave de"),                # 1,4 s tras «de»: DUDA
                    (14.4, 17.0, "todo esto es muy importante."),      # tras un punto: entre frases, no se toca
                    (18.2, 21.0, "y el resultado es"),                 # tras «es»: puede ser suspense, no se toca
                    (22.0, 25.0, "el triple de visitas que"),          # 1,0 s tras «que»: DUDA
                    (26.0, 29.0, "Google nos manda cada"),             # 0,9 s tras «cada», con el ratón moviéndose: no
                    (29.9, 33.0, "mes y eso es genial pero"),          # 1,5 s tras «pero»: DUDA (la vetará el director)
                    (34.5, 37.0, "no todo es bueno sabes por qué?"),   # tras «qué?»: acaba una pregunta, no
                    (38.0, 40.0, "porque la"),                         # 1,6 s tras «la», pero con un «eh» oído en medio
                    (41.6, 70.0, largo_h)]
        wh = tmp / "h.wav"
        _wav_sintetico(wh, 72.0, [(a_, b_) for a_, b_, _t in tramos_h])
        hu = Audio(wh)
        words_h = sorted(habla(tramos_h) + [{"word": "eh", "start": 40.75, "end": 40.85}], key=lambda w: w["start"])
        cands = pausas_candidatas(words_h, hu, [{"inicio": 28.5, "fin": 30.5}])
        ids = [c["id"] for c in cands]
        r("pausas de duda: salen las de tras «de», «que» y «pero»; NO las de tras un punto, tras «es», tras «qué?», con "
          "el ratón moviéndose ni con algo oído en medio", ids == ["p13.0", "p25.0", "p33.0"], texto_pausas(cands))
        sdh = segs_de_simple(hu.dur)
        nh, ih = ajusta_plan({"recortes": [], "pausas_vistas": ids, "pausas_enfasis": ["p33.0"]}, words_h, hu, hu.dur,
                             sdh, pausas=cands)
        sh = sdh(nh)
        r("las dudas se quedan en 0,3 s y la que el director marcó como énfasis («…genial pero [pausa] no todo es "
          "bueno») sigue entera", abs(_visto(sh, 13.0, 14.4) - 0.3) < 0.03 and abs(_visto(sh, 25.0, 26.0) - 0.3) < 0.03
          and abs(_visto(sh, 33.0, 34.5) - 1.5) < 0.03 and ih["pausas"] == 2, f"{ih['notas']} · {sh[:6]}")
        r("acortar pausas no se lleva ni un trozo de voz", all(abs(_visto(sh, a_, b_) - (b_ - a_)) < 0.02
                                                              for a_, b_, _t in tramos_h[1:]), sh)
        pz = [(float(x["inicio"]), float(x["fin"])) for x in nh["recortes"] if x.get("auto") == "pausa"]
        r("las pausas acortadas no suenan (sin tono etéreo a media frase) y cuentan como cortes del programa (una "
          "tarjeta que caía ahí no se pierde)", len(pz) == 2 and all(c in cortes_silenciosos(nh) for c in pz)
          and all(c in cortes_del_programa(nh) for c in pz), pz)
        otra_h, _ = ajusta_plan(nh, words_h, hu, hu.dur, sdh, pausas=cands)
        r("pausas: es idempotente y `revierte` las quita", sdh(otra_h) == sh
          and not any(x.get("auto") == "pausa" for x in revierte(nh)["recortes"]))
        vieja, iv = ajusta_plan({"recortes": []}, words_h, hu, hu.dur, sdh, pausas=cands)
        r("un plan de ANTES (sin `pausas_vistas`: el director no vio la lista) se queda con sus pausas enteras",
          abs(_visto(sdh(vieja), 13.0, 14.4) - 1.4) < 0.03 and iv["pausas"] == 0)
        ya, _ = ajusta_plan({"recortes": [{"inicio": 13.1, "fin": 14.2, "motivo": "silencio"}], "pausas_vistas": ids},
                            words_h, hu, hu.dur, sdh, pausas=cands)
        r("una pausa que ya corta la IA no se vuelve a cortar",
          not any(x.get("auto") == "pausa" and x["inicio"] < 14.4 and x["fin"] > 13.0 for x in ya["recortes"]))

        # --- H2: el trozo que queda delante de la pausa no puede bajar de 1 s (el render lo tiraría, con «de la»)
        wh2 = tmp / "h2.wav"
        tramos_h2 = [(1.0, 10.0, "hola a todos hoy les quiero contar algo que me pasó con el seo"),
                     (12.0, 12.5, "de la"), (13.5, 40.0, largo_h)]
        _wav_sintetico(wh2, 42.0, [(a_, b_) for a_, b_, _t in tramos_h2])
        h2u = Audio(wh2)
        words_h2 = habla(tramos_h2)
        c2 = pausas_candidatas(words_h2, h2u)
        sdh2 = segs_de_simple(h2u.dur)
        n2, _ = ajusta_plan({"recortes": [{"inicio": 10.1, "fin": 11.95, "motivo": "silencio"}],
                             "pausas_vistas": [c["id"] for c in c2]}, words_h2, h2u, h2u.dur, sdh2, pausas=c2)
        s2h = sdh2(n2)
        r("tras un corte de la IA, la pausa se acorta menos para que el trozo de «de la» no baje de 1 s (y no se pierde)",
          [c["id"] for c in c2] == ["p12.5"] and _visto(s2h, 12.0, 12.5) > 0.48 and all(e - s >= 0.99 for s, e in s2h)
          and _visto(s2h, 12.5, 13.5) < 0.8, s2h[:4])

        # --- K: los BORDES de los cortes de la IA (Whisper acaba las palabras antes de tiempo y las empieza tarde)
        wk = tmp / "k.wav"
        _wav_sintetico(wk, 72.0, [(1.0, 10.0), (12.0, 12.6), (13.5, 25.0), (26.0, 27.5), (28.5, 50.0), (52.0, 56.0),
                                  (58.0, 70.0)])
        ku = Audio(wk)
        words_k = (habla([(1.0, 9.6, "hoy les voy a enseñar cómo posicionar una web en google y esto es bueno")])
                   + habla([(12.25, 12.55, "eh este")])
                   + habla([(13.75, 25.0, " ".join(f"k{i}" for i in range(22)))])
                   + habla([(26.0, 27.1, "o sea que")])
                   + habla([(28.5, 50.0, " ".join(f"m{i}" for i in range(43)))])
                   + habla([(52.0, 54.8, "w0 w1 w2 w3 w4 w5")])
                   + habla([(58.0, 70.0, " ".join(f"z{i}" for i in range(24)))]))
        plan_k = {"recortes": [{"inicio": 9.8, "fin": 13.7, "motivo": "silencio y muletilla"},
                               {"inicio": 25.3, "fin": 27.2, "motivo": "reinicio de frase"},
                               {"inicio": 35.0, "fin": 36.0, "motivo": "relleno"},
                               {"inicio": 40.0, "fin": 41.0, "motivo": "corte manual (editor)", "manual": True},
                               {"inicio": 55.0, "fin": 57.5, "motivo": "silencio"}]}
        sdk = segs_de_simple(ku.dur)
        nk, ik = ajusta_plan(plan_k, words_k, ku, ku.dur, sdk)
        rk = nk["recortes"]
        r("borde en la cola de tu palabra y en el arranque de la siguiente (las dos se quedaban): el corte sale del "
          "sonido de las dos y se lleva el «eh este» entero", abs(rk[0]["inicio"] - 10.06) < 0.02
          and abs(rk[0]["fin"] - 13.42) < 0.02 and rk[0].get("inicio_ia") == 9.8 and rk[0].get("fin_ia") == 13.7, rk[0])
        r("borde a mitad de palabras que la IA quería QUITAR («o sea que» de un reinicio): el corte se lleva el sonido "
          "entero, sin medio «que» colgando", abs(rk[1]["fin"] - 27.54) < 0.02 and rk[1].get("fin_ia") == 27.2
          and (27.2, rk[1]["fin"]) in cortes_del_programa(nk), rk[1])
        r("en habla seguida (palabras que se quedan y que se van en el mismo sonido), en un corte a mano y si habría que "
          "moverlo más de 0,7 s: el borde no se toca", rk[2] == plan_k["recortes"][2] and rk[3] == plan_k["recortes"][3]
          and rk[4] == plan_k["recortes"][4] and ik["bordes"] == 2, rk[2:])
        sk = sdk(nk)
        r("bordes: ninguna voz que el plan quería dejar se pierde y el «eh este» ya no se oye",
          abs(_visto(sk, 1.0, 10.0) - 9.0) < 0.02 and abs(_visto(sk, 13.5, 25.0) - 11.5) < 0.02
          and _visto(sk, 12.0, 12.6) == 0.0 and _visto(sk, 26.0, 27.5) == 0.0, sk[:4])
        otra_k, _ = ajusta_plan(nk, words_k, ku, ku.dur, sdk)
        r("bordes: es idempotente y `revierte` devuelve los segundos de la IA",
          sdk(otra_k) == sk and revierte(nk)["recortes"] == plan_k["recortes"], revierte(nk)["recortes"])

        # --- L: un silencio ENORME en medio («hago el keyword research y vuelvo en 20 min»): el tope de seguridad del
        #        50 % quita primero los cortes más grandes y habría dejado justo ese silencio en el vídeo
        wl = tmp / "l.wav"
        _wav_sintetico(wl, 160.0, [(1.0, 20.0), (140.0, 158.0)],
                       ruidos=[(40.0 + 3 * k, 40.06 + 3 * k, -30) for k in range(20)])      # teclear: clics sueltos
        lu = Audio(wl)
        words_l = (habla([(1.0, 20.0, " ".join(f"a{i}" for i in range(38)))])
                   + [{"word": "Gracias.", "start": 80.0, "end": 80.6}]                    # Whisper se lo inventa: sin sonido
                   + habla([(140.0, 158.0, " ".join(f"b{i}" for i in range(36)))]))

        class _Cfg:                                                                         # (el tope de verdad: 50 %)
            def get_path(self, k, d=None):
                return d
        sdl = lambda pl: pipeline._segs_de_plan(pl, lu.dur, _Cfg())                          # noqa: E731
        plan_l = {"recortes": [{"inicio": 20.5, "fin": 139.5, "motivo": "silencio largo mientras trabaja"}]}
        nl, il = ajusta_plan(plan_l, words_l, lu, lu.dur, sdl)
        r("un silencio de 2 min que es el 74 % del vídeo (más que el tope del 50 %) se corta igual: sin una palabra dentro "
          "no cuenta para el tope (los clics de teclear y un «Gracias» inventado por Whisper no son voz)",
          nl["recortes"][0].get("sin_voz") is True and _visto(sdl(nl), 21.0, 139.0) == 0.0
          and il["sin_voz_s"] > 100, (nl["recortes"], sdl(nl)))
        r("…y sin la marca, el tope lo habría tirado (lo que pasaba antes)",
          _visto(sdl(revierte(nl)), 21.0, 139.0) > 100, sdl(revierte(nl)))
        wl2 = tmp / "l2.wav"
        _wav_sintetico(wl2, 160.0, [(1.0, 20.0), (79.8, 81.0), (140.0, 158.0)])            # aquí SÍ dice algo en medio
        l2u = Audio(wl2)
        nl2, _ = ajusta_plan(plan_l, words_l, l2u, l2u.dur, lambda pl: pipeline._segs_de_plan(pl, l2u.dur, _Cfg()))
        r("si dentro de ese corte suena una palabra de verdad, no se marca: el tope de seguridad sigue valiendo",
          not nl2["recortes"][0].get("sin_voz"), nl2["recortes"])

        # --- M: SIEMPRE en la toma buena (el autor, 2026-09-25), con sus dos grabaciones reales y la transcripción VUELTA A
        #        ESCUCHAR frase por frase (`transcribe.revisa_arranque`)
        wm = tmp / "m.wav"
        _wav_sintetico(wm, 60.0, [(0.87, 2.64), (4.66, 58.0)], ruidos=[(3.0, 3.05, -32)])
        mu = Audio(wm)
        sdm = segs_de_simple(mu.dur)
        resto = habla([(5.44, 58.0, " ".join(f"r{i}" for i in range(100)))])
        reoidas = (_palabras_de([("¿Cómo", 0.72, 1.14), ("estás?", 1.14, 1.54), ("¿Cómo", 4.51, 4.75), ("estás", 4.75, 4.99),
                                 ("apareciendo", 4.99, 5.44)]) + resto)
        tm = tomas_arranque(reoidas)
        r("24-sep: «¿Cómo estás?» [2 s] «¿Cómo estás apareciendo hoy día…»: la primera es una toma falsa (confianza alta)",
          tm["reinicio"] and abs(tm["reinicio"]["t"] - 4.51) < 0.01 and tm["reinicio"]["confianza"] == "alta", tm["reinicio"])
        nm, im = ajusta_plan({"recortes": []}, reoidas, mu, mu.dur, sdm)
        sm = sdm(nm)
        r("24-sep: el vídeo empieza en la toma buena (0,15 s antes de su voz) y de la toma falsa no queda nada — ni pegada "
          "a la buena con la pausa acortada (sonaba a tartamudeo)",
          abs(sm[0][0] - (4.66 - 0.15)) < 0.03 and _visto(sm, 0.0, 4.5) == 0.0, (sm[:2], im["notas"]))
        otra_m, _ = ajusta_plan(nm, reoidas, mu, mu.dur, sdm)
        r("…es idempotente (el corte de las tomas falsas se recalcula igual en cada «Reintentar») y `revierte` lo quita",
          sdm(otra_m) == sm and not [x for x in revierte(nm)["recortes"] if x.get("auto")], sdm(otra_m)[:2])
        fundidas = _palabras_de([("¿Cómo", 0.50, 1.04), ("estás", 1.04, 1.50), ("apareciendo", 1.50, 4.32)]) + resto
        r("control: con la transcripción de Whisper SIN volver a escuchar (funde las dos tomas en una pregunta con un "
          "«apareciendo» de 2,8 s) no se ve ninguna toma falsa — por eso fallaba", tomas_arranque(fundidas)["reinicio"] is None)
        nman, _ = ajusta_plan({"recortes": [{"inicio": 0.0, "fin": 2.0, "motivo": "corte manual", "manual": True}]},
                              reoidas, mu, mu.dur, sdm)
        r("si TÚ cortaste a mano el principio, eso manda: no se añade el corte automático de tomas",
          not [x for x in nman["recortes"] if x.get("auto") == "tomas_falsas"], nman["recortes"])

        wt = tmp / "t.wav"
        _wav_sintetico(wt, 60.0, [(3.02, 4.01), (5.88, 58.0)], ruidos=[(4.78, 4.82, -35)])
        tu = Audio(wt)
        sdt = segs_de_simple(tu.dur)
        tarareo = (_palabras_de([("da", 2.87 + 0.17 * j, 3.0 + 0.17 * j) for j in range(6)]
                                + [("Una", 5.73, 5.95), ("de", 5.95, 6.01)])
                   + habla([(6.01, 58.0, " ".join(f"s{i}" for i in range(100)))]))
        nt1, _ = ajusta_plan({"recortes": [{"inicio": 0.0, "fin": 4.0, "motivo": "silencio al arranque"}]}, tarareo, tu,
                             tu.dur, sdt)
        r("22-sep: con el corte de Gemini (0-4,0) el vídeo ya NO devuelve el tarareo como si fuera «Una»: empieza en tu "
          "«Una de las cosas…» (5,73)", abs(sdt(nt1)[0][0] - (5.88 - 0.15)) < 0.03, sdt(nt1)[:2])
        nt2, it2 = ajusta_plan({"recortes": []}, tarareo, tu, tu.dur, sdt)
        r("22-sep sin ningún corte de la IA: el tarareo («da da da…») y su pausa se quitan solos",
          abs(sdt(nt2)[0][0] - (5.88 - 0.15)) < 0.03 and any("tarareo" in n for n in it2["notas"]), (sdt(nt2)[:2], it2["notas"]))
        una = _palabras_de([("Una", 1.0, 1.3)]) + _palabras_de([("Una", 2.5, 2.7), ("de", 2.7, 2.8)]) + habla(
            [(2.8, 50.0, " ".join(f"u{i}" for i in range(90)))])
        ru = tomas_arranque(una)["reinicio"]
        r("«Una…» [1,2 s] «Una de las cosas…»: una sola palabra que vuelves a empezar también es toma falsa",
          ru and abs(ru["t"] - 2.5) < 0.01, ru)
        distinta = _palabras_de([("¿Cómo", 1.0, 1.3), ("estás?", 1.3, 1.7)]) + habla(
            [(3.2, 50.0, "hoy vamos a ver " + " ".join(f"v{i}" for i in range(90)))])
        r("«¿Cómo estás?» [1,5 s] «Hoy vamos a ver…» (NO vuelve a empezar lo mismo): no se corta nada",
          tomas_arranque(distinta)["reinicio"] is None, tomas_arranque(distinta)["reinicio"])
        # --- N: un corte de la IA de «silencio / muletilla / tropiezo» no puede llevarse palabras de verdad (el autor, 2026-09-25:
        #        «me cortó la idea» — su vídeo del 24-sep, 3:03 y 5:50). Audio con sus tramos de voz REALES y un «oído» falso
        #        que devuelve lo que Whisper oye de verdad al volver a escuchar el trozo
        wn = tmp / "n.wav"
        _wav_sintetico(wn, 400.0, [(150.0, 189.05), (189.59, 190.82), (191.33, 191.60), (191.84, 192.51), (192.96, 240.0),
                                   (300.0, 357.50), (357.80, 359.26), (359.63, 360.02), (360.26, 395.0)])
        nu = Audio(wn)
        sdn = segs_de_simple(nu.dur)
        palabras_n = (habla([(150.0, 189.05, " ".join(f"a{i}" for i in range(60)) + " español")])
                      + _palabras_de([("Los", 189.48, 189.84), ("pasé", 189.84, 190.2), ("a", 190.2, 190.3),
                                      ("inglés", 190.3, 192.06), ("listo", 192.06, 192.68)])
                      + habla([(192.96, 240.0, "perdón por ese golpe " + " ".join(f"b{i}" for i in range(70)))])
                      + habla([(300.0, 357.5, " ".join(f"c{i}" for i in range(90)))])
                      + _palabras_de([("pero", 357.8, 357.96), ("si", 357.96, 358.0), ("tienen", 358.0, 358.22),
                                      ("muchas", 358.22, 360.28)])
                      + habla([(360.28, 395.0, "versiones de su sitio web " + " ".join(f"d{i}" for i in range(50)))]))
        oido_real = {(189.2, 194.6): [("Los", 189.6, 189.9), ("pasé", 189.9, 190.2), ("a", 190.2, 190.3),
                                      ("inglés.", 190.3, 190.8), ("¿Listo?", 191.35, 191.6), ("Perdón", 192.96, 193.2),
                                      ("por", 193.2, 193.3), ("ese", 193.3, 193.4), ("golpe", 193.4, 193.7)],
                     (357.9, 362.8): [("Tienen", 358.1, 358.4), ("muchas", 358.4, 359.2), ("versiones", 359.6, 360.5),
                                      ("de", 360.8, 360.9), ("su", 360.9, 361.0), ("sitio", 361.0, 361.2),
                                      ("web", 361.2, 361.5)]}

        def oido_n(a, b):
            return next((v for (x, y), v in oido_real.items() if abs(x - a) < 0.05 and abs(y - b) < 0.05), [])
        plan_n = {"recortes": [{"inicio": 189.5, "fin": 192.1, "motivo": "silencio y tropiezo"},
                               {"inicio": 358.2, "fin": 360.3, "motivo": "silencio y muletilla"},
                               {"inicio": 250.0, "fin": 290.0, "motivo": "divagación sobre otro tema"}]}
        nn, inn = ajusta_plan(plan_n, palabras_n, nu, nu.dur, sdn, oye=oido_n)
        sn = sdn(nn)
        r("24-sep (3:03): el corte «silencio y tropiezo» ya NO se lleva «los pasé a inglés» (el final de la idea): solo "
          "sale el «¿Listo?» y su silencio", _visto(sn, 189.59, 190.82) > 1.2 and _visto(sn, 190.9, 191.6) < 0.35,
          (sn[:4], inn["notas"]))
        r("24-sep (5:50): el corte «silencio y muletilla» ya NO se lleva «muchas versiones» (en el audio casi no había "
          "pausa: se anula)", _visto(sn, 357.8, 360.4) > 2.5, sn[:6])
        r("un corte de contenido de verdad («divagación») no se toca", _visto(sn, 251.0, 289.0) == 0.0)
        otra_n, _ = ajusta_plan(nn, palabras_n, nu, nu.dur, sdn, oye=oido_n)
        r("es idempotente y `revierte` devuelve los cortes originales de la IA, una vez cada uno",
          sdn(otra_n) == sn and sorted((x["inicio"], x["fin"]) for x in revierte(nn)["recortes"])
          == sorted((x["inicio"], x["fin"]) for x in plan_n["recortes"]), revierte(nn)["recortes"])
        wf = tmp / "f.wav"
        _wav_sintetico(wf, 120.0, [(10.0, 50.0), (50.6, 51.4), (52.2, 110.0)])
        fu = Audio(wf)
        pal_f = (habla([(10.0, 50.0, " ".join(f"e{i}" for i in range(70)))]) + _palabras_de([("Bueno,", 50.6, 51.0)])
                 + habla([(52.2, 110.0, " ".join(f"g{i}" for i in range(100)))]))
        nf, _ = ajusta_plan({"recortes": [{"inicio": 50.3, "fin": 52.0, "motivo": "muletilla y pausa"}]}, pal_f, fu, fu.dur,
                            segs_de_simple(fu.dur), oye=lambda a, b: [("Bueno,", 50.6, 51.0), ("eh...", 51.0, 51.4)])
        r("…pero un «Bueno, eh…» que la IA corta como muletilla SÍ se corta (volviendo a escuchar: Whisper casi nunca "
          "escribe los «eh»)", _visto(segs_de_simple(fu.dur)(nf), 50.6, 51.4) < 0.05, nf["recortes"])

        r("tarareos y murmullos sí; palabras de verdad no («Todo.», «Nada.», «¿Cómo estás?»)",
          _es_tarareo(["da"] * 6) and _es_tarareo(["tararararara"]) and _es_tarareo(["oh", "oh", "mmm", "mmm"])
          and not _es_tarareo(["todo"]) and not _es_tarareo(["nada"]) and not _es_tarareo(["como", "estas"]))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return filas


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] != "--prueba":
        diagnostico(Path(sys.argv[1]))
    else:
        filas_ = prueba()
        for f in filas_:
            print(("OK   " if f["ok"] else "FALLA") + " " + f["caso"] + ("" if f["ok"] else f"  -> {f['detalle']}"))
        sys.exit(1 if any(not f["ok"] for f in filas_) else 0)
