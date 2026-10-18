"""«Shorts · TikTok» — cortar TODOS los silencios (el autor, 16-oct-2026: «tiene que cortar todos los silencios… importantísimo»).

Se mide en el AUDIO (no con los tiempos de Whisper, que se desvían 0,2-0,9 s al salir de un silencio: ver CLAUDE.md «Arranque y final medidos en el audio»):
dBFS cada 10 ms → «islas» de sonido a menos de `REL_DB` dB del nivel de tu voz → los huecos de ≥ `PAUSA_MIN` s se quitan, dejando un pelo de aire a cada lado
(`PAD_ANTES`/`PAD_DESPUES`) para que no suene a «mordido». Es una COPIA PROPIA de la idea (no importa `voz.py`: si cambia el de Vídeos PRO, los Shorts no cambian).

Todo son funciones puras (+ `prueba()`), con tiempos en segundos.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

PASO = 0.01              # resolución del análisis (s)
REL_DB = -26.0           # «hay voz» = a menos de 26 dB del nivel de la voz (percentil 95)
SUELO_MAS_DB = 9.0       # y al menos 9 dB sobre el ruido de fondo (percentil 10)
HUECO_MAX = 0.16         # huecos de sonido más cortos que esto (entre sílabas, una «t») no parten una isla
ISLA_MIN = 0.07          # un sonido más corto que esto (un clic, una tecla) no es voz
PAUSA_MIN = 0.28         # una pausa de esto o más se corta
PAD_ANTES, PAD_DESPUES = 0.05, 0.11
COLA_FINAL = 0.22        # aire tras la última palabra


def lee_wav(ruta: Path) -> tuple[np.ndarray, int]:
    """El WAV PCM de 16 bits (mono o estéreo → mono) como float32 en [-1, 1] y su frecuencia."""
    with wave.open(str(ruta), "rb") as w:
        sr, n, ch, ancho = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        crudo = w.readframes(n)
    if ancho != 2:
        raise ValueError("el audio tiene que ser PCM de 16 bits")
    x = np.frombuffer(crudo, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def energia_db(x: np.ndarray, sr: int, paso: float = PASO) -> np.ndarray:
    """dBFS (RMS) de cada ventana de `paso` segundos."""
    n = max(1, int(round(sr * paso)))
    m = len(x) // n
    if m == 0:
        return np.zeros(0, dtype=np.float32)
    r = np.sqrt(np.mean(x[: m * n].reshape(m, n) ** 2, axis=1) + 1e-12)
    return (20.0 * np.log10(r + 1e-9)).astype(np.float32)


def islas(db: np.ndarray, paso: float = PASO) -> list[tuple[float, float]]:
    """Tramos con voz: por encima del umbral (relativo al nivel de tu voz y al ruido), uniendo huecos cortos y quitando los clics."""
    if len(db) == 0:
        return []
    nivel = float(np.percentile(db, 95))
    suelo = float(np.percentile(db, 10))
    umbral = max(nivel + REL_DB, suelo + SUELO_MAS_DB)
    voz = db > umbral
    out: list[list[float]] = []
    i, n = 0, len(voz)
    while i < n:
        if not voz[i]:
            i += 1
            continue
        j = i
        while j < n and voz[j]:
            j += 1
        out.append([i * paso, j * paso])
        i = j
    unidas: list[list[float]] = []
    for a, b in out:
        if unidas and a - unidas[-1][1] < HUECO_MAX:
            unidas[-1][1] = b
        else:
            unidas.append([a, b])
    return [(a, b) for a, b in unidas if b - a >= ISLA_MIN]


def segmentos(isl: list[tuple[float, float]], dur: float, pausa_min: float = PAUSA_MIN) -> list[tuple[float, float]]:
    """Lo que se QUEDA: las islas unidas si las separa menos de `pausa_min`, con un poco de aire a cada lado."""
    if not isl:
        return [(0.0, max(0.0, dur))]
    runs: list[list[float]] = []
    for a, b in isl:
        if runs and a - runs[-1][1] < pausa_min:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    segs: list[list[float]] = []
    for k, (a, b) in enumerate(runs):
        a2 = max(0.0, a - PAD_ANTES)
        b2 = min(dur, b + (COLA_FINAL if k == len(runs) - 1 else PAD_DESPUES))
        if segs and a2 <= segs[-1][1]:
            segs[-1][1] = max(segs[-1][1], b2)
        else:
            segs.append([a2, b2])
    return [(round(a, 3), round(b, 3)) for a, b in segs if b - a > 0.05]


def cuadra_a_fotogramas(segs: list[tuple[float, float]], fps: int = 30) -> list[tuple[float, float]]:
    """Cada tramo dura un número ENTERO de fotogramas (así el audio y el vídeo del Short salen clavados)."""
    out = []
    for a, b in segs:
        n = max(1, int(round((b - a) * fps)))
        out.append((a, a + n / fps))
    return out


def mapa(segs: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """(inicio origen, fin origen, inicio en el Short) de cada tramo."""
    out, t = [], 0.0
    for a, b in segs:
        out.append((a, b, t))
        t += b - a
    return out


def duracion(segs: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in segs)


def a_salida(t: float, m: list[tuple[float, float, float]]) -> float | None:
    """El segundo del Short que corresponde a un segundo del original; None si ese instante se cortó."""
    for a, b, o in m:
        if a <= t <= b:
            return o + (t - a)
    return None


def palabras_a_salida(words: list[dict], m: list[tuple[float, float, float]]) -> list[dict]:
    """La transcripción en el reloj del Short. Una palabra cuyo medio cae en un trozo cortado se mueve al tramo más cercano (Whisper se desvía de las pausas);
    cada palabra queda con start < end y en orden."""
    out: list[dict] = []
    for w in words:
        a, b = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        if b < a:
            a, b = b, a
        mid = (a + b) / 2.0
        ts = a_salida(a, m)
        te = a_salida(b, m)
        if ts is None or te is None:
            mejor = None
            for sa, sb, o in m:
                if sa <= mid <= sb:
                    mejor = o + (mid - sa)
                    break
            if mejor is None:
                # el tramo (de origen) más cercano a la mitad de la palabra
                sa, sb, o = min(m, key=lambda z: min(abs(mid - z[0]), abs(mid - z[1])))
                mejor = o if mid < sa else o + (sb - sa)
            ts, te = mejor, mejor + max(0.12, b - a)
        if te <= ts:
            te = ts + 0.12
        out.append({**w, "start": round(ts, 3), "end": round(te, 3)})
    out.sort(key=lambda w: w["start"])
    for k in range(1, len(out)):                         # sin solaparse entre sí
        if out[k]["start"] < out[k - 1]["end"] - 1e-6:
            out[k - 1]["end"] = max(out[k - 1]["start"] + 0.05, out[k]["start"])
    return out


def corta(wav: Path, fps: int = 30) -> dict:
    """Todo junto: -> {"segmentos": [(a, b)], "mapa": [...], "dur_origen", "dur_short", "quitado_s", "n_cortes"}."""
    x, sr = lee_wav(wav)
    dur = len(x) / float(sr)
    segs = cuadra_a_fotogramas(segmentos(islas(energia_db(x, sr)), dur), fps)
    d = duracion(segs)
    return {"segmentos": segs, "mapa": mapa(segs), "dur_origen": round(dur, 3), "dur_short": round(d, 3), "quitado_s": round(dur - d, 3), "n_cortes": max(0, len(segs) - 1)}


# ------------------------------------------------------------------ pruebas (audio sintético: «voz» = un tono con trémolo; sin ffmpeg ni Whisper)
def _sintetico(tramos: list[tuple[float, float]], dur: float, sr: int = 16000, ruido: float = 0.002) -> np.ndarray:
    rng = np.random.default_rng(3)
    t = np.arange(int(dur * sr)) / sr
    x = (rng.standard_normal(len(t)) * ruido).astype(np.float32)
    for a, b in tramos:
        m = (t >= a) & (t < b)
        env = 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t[m])                     # sílabas
        x[m] += (0.25 * env * np.sin(2 * np.pi * 180 * t[m])).astype(np.float32)
    return x


def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    sr = 16000
    habla = [(1.0, 2.5), (2.58, 3.4), (5.2, 6.6), (9.0, 10.0)]            # el 2.5→2.58 es un hueco corto (se queda); 3.4→5.2 y 6.6→9.0 son pausas
    x = _sintetico(habla, 12.0, sr)
    db = energia_db(x, sr)
    isl = islas(db)
    caso("detecta la voz y NO parte una palabra por un hueco corto (3 islas: el 2.5→2.58 se une)", len(isl) == 3 and abs(isl[0][0] - 1.0) < 0.05 and abs(isl[0][1] - 3.4) < 0.08, isl)
    segs = segmentos(isl, 12.0)
    caso("las pausas largas se cortan (3.4→5.2 y 6.6→9.0) y arranca/acaba pegado a la voz", len(segs) == 3 and segs[0][0] > 0.9 and segs[-1][1] < 10.4, segs)
    d = duracion(segs)
    caso("el Short dura mucho menos que el original y nada de la voz se pierde", d < 8.0 and all(any(s[0] <= a + 0.01 and s[1] >= b - 0.01 for s in segs) for a, b in habla), d)
    fq = cuadra_a_fotogramas(segs, 30)
    caso("cada tramo dura un número ENTERO de fotogramas a 30 fps", all(abs((b - a) * 30 - round((b - a) * 30)) < 1e-3 for a, b in fq), fq)
    m = mapa(fq)
    caso("el mapa lleva un segundo del original al del Short (y lo cortado da None)", abs(a_salida(fq[1][0] + 0.2, m) - (m[1][2] + 0.2)) < 1e-6 and a_salida(8.0, m) is None)
    ws = palabras_a_salida([{"word": "hola", "start": 1.1, "end": 1.4}, {"word": "mundo", "start": 5.3, "end": 5.6}, {"word": "fantasma", "start": 7.5, "end": 7.8}], m)
    caso("las palabras pasan al reloj del Short en orden y sin solaparse; la que cayó en lo cortado se queda pegada al tramo más cercano",
         len(ws) == 3 and all(ws[k]["end"] <= ws[k + 1]["start"] + 1e-6 for k in range(2)) and all(w["end"] > w["start"] for w in ws) and ws[2]["start"] < m[-1][2] + (m[-1][1] - m[-1][0]), ws)
    caso("sin ninguna voz no se rompe: un solo tramo con todo el audio", segmentos([], 5.0) == [(0.0, 5.0)])
    caso("un clic suelto (40 ms) no cuenta como voz", islas(energia_db(_sintetico([(1.0, 1.04), (2.0, 3.0)], 4.0, sr), sr))[0][0] > 1.9)
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "a.wav"
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        r = corta(p, 30)
        caso("de un WAV de verdad: quita ≥ 4 s de silencio y deja 3 tramos", r["quitado_s"] > 4.0 and len(r["segmentos"]) == 3 and r["n_cortes"] == 2, {k: r[k] for k in ("dur_origen", "dur_short", "quitado_s", "n_cortes")})
    return filas
