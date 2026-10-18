"""«Shorts · TikTok» — EL MONTAJE (1080×1920 a 30 fps): cámara arriba, pantalla en medio SIN la cámara, subtítulos abajo y, encima, todo lo que dibuja `st_tarjetas`.

Cómo se hace (copia propia de las ideas de `render.py`/`shorts_render.py`; no importa nada de ellos):
  · ffmpeg solo DECODIFICA: un lector por zona (la tira de pantalla —la mayor parte del fotograma que no tapa la cámara—, y la cámara a su resolución real) y solo los tramos que
    se quedan (los silencios no se decodifican; los huecos cortos entre tramos se leen y se tiran para no reabrir el archivo cada vez).
  · cada fotograma se COMPONE en Python (Pillow): recorte con acercamiento animado, tarjetas, escenas, subtítulos… y se manda por tubería a ffmpeg, que solo codifica (GPU si hay).
    Así cada animación es una función del tiempo (sin filtros enormes) y el acercamiento es de sub-píxel (`Image.resize(box=…)`), sin saltos.
  · el audio se corta y se mezcla con numpy: la voz (con 6 ms de fundido en cada empalme) + efectos de sonido sintetizados (whoosh, pop, tick, thud, ding) en cada corte y en cada animación;
    ffmpeg lo nivela a -14 LUFS al empaquetar.
  · la pantalla se centra donde hay ACTIVIDAD (movimiento medido a 4 muestras/s dentro de la tira) y NUNCA enseña la cámara: la tira excluye su recuadro.
Funciones puras donde se puede + `prueba()` con un vídeo sintético (cuesta 0: sin Gemini ni Whisper).
"""
from __future__ import annotations

import math
import random
import subprocess
import time
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import st_plan, st_tarjetas as T, st_voz
from .util import log

FPS = 30
SR = 48000
RATIO_B = T.W / T.B_H                       # proporción de la zona de la pantalla (1080×920)
ZOOM_BASE_CORTE = (1.0, 1.13)               # en cada corte el plano alterna: normal / más cerca («jump cut» con acercamiento)
ZOOM_FACE_CORTE = (1.0, 1.09)
ZOOM_PUNCH = 0.15
ZOOM_MAX = 1.5


def _par(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _pos(v: float) -> int:
    return max(0, int(v) // 2 * 2)


def _rect(x: float, y: float, w: float, h: float, W: int, Hh: int) -> tuple[int, int, int, int]:
    """Un rectángulo con medidas PARES (lo pide el codificador) y siempre dentro del fotograma."""
    x, y = _pos(x), _pos(y)
    w, h = min(_par(w), (W - x) // 2 * 2), min(_par(h), (Hh - y) // 2 * 2)
    return x, y, max(2, w), max(2, h)


@lru_cache(maxsize=1)
def _nvenc() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30)
        return "h264_nvenc" in (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


@lru_cache(maxsize=1)
def _cuda_dec() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-hwaccels"], capture_output=True, text=True, timeout=30)
        return "cuda" in (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------ geometría
def geometria(w: int, h: int, camara: dict | None) -> dict:
    """Dónde sale cada cosa en el vídeo de origen. `modo`: «camara» (pantalla + cámara en un recuadro), «solo» (apaisado sin cámara) o «vertical» (ya es vertical)."""
    if h > w * 1.15:
        return {"modo": "vertical", "tira": _rect(0, 0, w, h, w, h), "camara": None}
    cam = None
    tira = _rect(0, 0, w, h, w, h)
    if camara:
        try:
            cx0, cy0 = float(camara["x"]) * w, float(camara["y"]) * h
            cx1, cy1 = cx0 + float(camara["w"]) * w, cy0 + float(camara["h"]) * h
            cam = _rect(max(0, cx0), max(0, cy0), min(w, cx1) - max(0, cx0), min(h, cy1) - max(0, cy0), w, h)
            margen = 4                                                         # (unos píxeles de más: el borde de la cámara nunca se cuela)
            cands = {"izq": (0, 0, int(cx0) - margen, h), "der": (int(cx1) + margen, 0, w - int(cx1) - margen, h), "arriba": (0, 0, w, int(cy0) - margen), "abajo": (0, int(cy1) + margen, w, h - int(cy1) - margen)}

            def util(r):
                if r[2] < 160 or r[3] < 160:
                    return 0.0
                aw = min(r[2], r[3] * RATIO_B)
                return aw * aw / RATIO_B
            mejor = max(cands.values(), key=util)
            if util(mejor) > 0:
                tira = _rect(mejor[0], mejor[1], mejor[2], mejor[3], w, h)
        except (KeyError, TypeError, ValueError):
            cam = None
    return {"modo": "camara" if cam else "solo", "tira": tira, "camara": cam}


def tamano_tira(geo: dict) -> tuple[int, int]:
    """El tamaño al que se lee la tira de pantalla: lo justo para que el recorte base se vea a ~1,4× el ancho de salida (aguanta un acercamiento sin emborronar)."""
    x, y, tw, th = geo["tira"]
    objetivo_h = T.B_H + (T.FACE_H if geo["modo"] == "vertical" else 0)
    ratio = T.W / objetivo_h
    bw = min(tw, th * ratio)
    s = min(1.0, (T.W * 1.4) / max(1.0, bw))
    return _par(tw * s), _par(th * s)


# ------------------------------------------------------------------ lectores de fotogramas
class Lector:
    """Fotogramas RGB en bruto de un tramo del vídeo, uno detrás de otro (ffmpeg solo decodifica, recorta y escala)."""

    def __init__(self, src: Path, a: float, n: int, vf: str, w: int, h: int, cuda: bool = False):
        self.w, self.h, self.n = w, h, n
        self.tam = w * h * 3
        self.ultimo: np.ndarray | None = None
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + (["-hwaccel", "cuda"] if cuda else []) + ["-ss", f"{a:.3f}", "-i", str(src), "-an", "-vf", vf, "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self.tam * 3)

    def lee(self) -> np.ndarray:
        buf = self.p.stdout.read(self.tam) if self.p.stdout else b""
        if len(buf) < self.tam:
            if self.ultimo is None:
                self.ultimo = np.zeros((self.h, self.w, 3), np.uint8)
            return self.ultimo                                              # (se acabó el archivo: se repite el último)
        self.ultimo = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
        return self.ultimo

    def salta(self, k: int) -> None:
        for _ in range(max(0, k)):
            if self.p.stdout:
                self.p.stdout.read(self.tam)

    def cierra(self) -> None:
        try:
            if self.p.stdout:
                self.p.stdout.close()
            self.p.kill()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def corridas(segs: list[tuple[float, float]], hueco_max: float = 2.0) -> list[list[int]]:
    """Agrupa tramos CONSECUTIVOS separados por menos de `hueco_max` s: se decodifican de una vez (el hueco se lee y se tira)."""
    out: list[list[int]] = []
    for k, (a, b) in enumerate(segs):
        if out and a - segs[out[-1][-1]][1] < hueco_max:
            out[-1].append(k)
        else:
            out.append([k])
    return out


# ------------------------------------------------------------------ dónde mirar: actividad en la tira
def movimiento(src: Path, geo: dict, dur: float, cuda: bool | None = None, desde: float = 0.0) -> list[tuple[float, float, float]]:
    """(t, cx, cy) cada 0,25 s: el centro de lo que cambia dentro de la tira (el cursor, una página que carga), en fracción 0-1 de la tira. Sin cambios, repite el anterior."""
    x, y, tw, th = geo["tira"]
    ancho = 320
    alto = _par(th * ancho / tw)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + (["-hwaccel", "cuda"] if (_cuda_dec() if cuda is None else cuda) else []) + (["-ss", f"{desde:.3f}", "-t", f"{dur:.3f}"] if desde > 0 or dur else []) + ["-i", str(src), "-an",
           "-vf", f"crop={tw}:{th}:{x}:{y},scale={ancho}:{alto},fps=4,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    tam = ancho * alto
    out: list[tuple[float, float, float]] = []
    prev = None
    cx, cy = 0.5, 0.5
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=tam * 4)
        i = 0
        while True:
            buf = p.stdout.read(tam) if p.stdout else b""
            if len(buf) < tam:
                break
            f = np.frombuffer(buf, np.uint8).reshape(alto, ancho).astype(np.int16)
            if prev is not None:
                m = np.abs(f - prev) > 28
                n = int(m.sum())
                if n >= 6:
                    ys, xs = np.nonzero(m)
                    nx, ny = float(xs.mean()) / ancho, float(ys.mean()) / alto
                    cx, cy = cx + (nx - cx) * 0.55, cy + (ny - cy) * 0.55            # (se mueve suave hacia lo nuevo)
            out.append((i * 0.25, cx, cy))
            prev = f
            i += 1
        p.wait(timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log(f"AVISO: no pude medir la actividad de la pantalla ({exc}); centro el recorte.")
    return out


def centro_en(mov: list[tuple[float, float, float]], t: float) -> tuple[float, float]:
    if not mov:
        return 0.5, 0.5
    k = min(len(mov) - 1, max(0, int(t / 0.25)))
    k2 = min(len(mov) - 1, k + 1)
    a, b = mov[k], mov[k2]
    u = min(1.0, max(0.0, (t - a[0]) / 0.25)) if k2 != k else 0.0
    return a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u


def caja_recorte(sw: int, sh: int, cx: float, cy: float, zoom: float, objetivo: float) -> tuple[float, float, float, float]:
    """El recuadro (x0, y0, x1, y1) dentro de la tira, de proporción `objetivo`, centrado en (cx, cy) fracción, con `zoom` ≥ 1 y siempre DENTRO de la tira."""
    zoom = max(1.0, min(ZOOM_MAX, zoom))
    bw = min(float(sw), sh * objetivo)
    bh = bw / objetivo
    w, h = bw / zoom, bh / zoom
    x0 = min(max(cx * sw - w / 2, 0.0), sw - w)
    y0 = min(max(cy * sh - h / 2, 0.0), sh - h)
    return x0, y0, x0 + w, y0 + h


def caja_cara(cw: int, ch: int, zoom: float, alto: int | None = None) -> tuple[float, float, float, float]:
    """El recuadro de la cara dentro del recuadro de la cámara (proporción 1080:alto de la cara del layout), con la cabeza algo por encima del centro."""
    objetivo = T.W / (alto or T.Zona.face_h)
    bw = float(cw)
    bh = bw / objetivo
    if bh > ch:
        bh, bw = float(ch), ch * objetivo
    zoom = max(1.0, min(1.5, zoom))
    w, h = bw / zoom, bh / zoom
    x0 = min(max(cw / 2 - w / 2, 0.0), cw - w)
    y0 = min(max(ch * 0.44 - h / 2, 0.0), ch - h)
    return x0, y0, x0 + w, y0 + h


# ------------------------------------------------------------------ zoom y efectos en el tiempo
def bump(t: float, t0: float, dur: float) -> float:
    """0→1 en 0,10 s y vuelve a 0 con suavidad hasta `dur`."""
    u = t - t0
    if u < 0 or u > dur:
        return 0.0
    if u < 0.10:
        return u / 0.10
    return (1.0 - (u - 0.10) / max(0.05, dur - 0.10)) ** 2


def zooms_en(t: float, eventos: list[dict], cortes: list[float]) -> tuple[float, float, float]:
    """(zoom de la pantalla, zoom de la cara, destello 0-1) en el segundo `t` del Short: el plano de base de cada tramo (alterna en cada corte) + un golpe en cada corte + los acercamientos del plan."""
    k = 0
    ult = 0.0
    for tc in cortes:
        if tc <= t + 1e-9:
            k += 1
            ult = tc
    zs, zf = ZOOM_BASE_CORTE[k % 2], ZOOM_FACE_CORTE[k % 2]
    golpe = 0.05 * bump(t, ult, 0.3) if k else 0.0
    zs += golpe
    zf += golpe
    fl = 0.0
    for e in eventos:
        if e["tipo"] == "zoom_pantalla":
            zs += ZOOM_PUNCH * bump(t, e["t"], e["dur"])
        elif e["tipo"] == "zoom_cara":
            zf += ZOOM_PUNCH * bump(t, e["t"], e["dur"])
        elif e["tipo"] == "flash":
            fl = max(fl, 0.4 * bump(t, e["t"], e["dur"]))
        elif e["tipo"] == "escena" and e["t"] <= t < e["t"] + 0.14:
            fl = max(fl, 0.0)
    return zs, zf, fl


# ------------------------------------------------------------------ sonidos (sintetizados: nada que bajar, nada con derechos)
@lru_cache(maxsize=16)
def sfx(nombre: str) -> np.ndarray:
    rng = np.random.default_rng(11)
    t = lambda d: np.arange(int(SR * d)) / SR  # noqa: E731
    if nombre == "whoosh":
        d = 0.42
        x = t(d)
        ruido = rng.standard_normal(len(x))
        y = np.zeros_like(ruido)
        est = 0.0
        for i in range(len(x)):
            f = 300 + 5200 * math.sin(math.pi * i / len(x)) ** 1.5           # barrido del filtro: sube y baja
            a = min(0.98, 2 * math.pi * f / SR)
            est += a * (ruido[i] - est)
            y[i] = est
        env = np.sin(np.pi * np.clip(x / d, 0, 1)) ** 1.4
        out = y * env
    elif nombre == "swish":
        d = 0.2
        x = t(d)
        ruido = rng.standard_normal(len(x))
        est, y = 0.0, np.zeros_like(ruido)
        for i in range(len(x)):
            f = 800 + 4200 * (i / len(x))
            a = min(0.98, 2 * math.pi * f / SR)
            est += a * (ruido[i] - est)
            y[i] = est
        out = y * np.sin(np.pi * np.clip(x / d, 0, 1)) ** 1.2
    elif nombre == "pop":
        d = 0.14
        x = t(d)
        f = 420 + 900 * np.minimum(1.0, x / 0.07)
        out = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 30)
        out[:40] *= np.linspace(0, 1, 40)
    elif nombre == "tick":
        d = 0.05
        x = t(d)
        out = np.sin(2 * np.pi * 1900 * x) * np.exp(-x * 90)
    elif nombre == "thud":
        d = 0.24
        x = t(d)
        out = np.sin(2 * np.pi * (95 - 35 * x / d) * x) * np.exp(-x * 18)
    elif nombre == "ding":
        d = 0.6
        x = t(d)
        out = (np.sin(2 * np.pi * 1320 * x) + 0.5 * np.sin(2 * np.pi * 1980 * x)) * np.exp(-x * 7)
        out[:30] *= np.linspace(0, 1, 30)
    else:
        return np.zeros(10, np.float32)
    pico = float(np.max(np.abs(out))) or 1.0
    return (out / pico).astype(np.float32)


GANANCIA = {"whoosh": 0.24, "swish": 0.08, "pop": 0.22, "tick": 0.0, "thud": 0.26, "ding": 0.18}     # (16-oct: algunos sonidos le molestaban: el «tick» se apaga y el resto baja)


def efectos(eventos: list[dict], cortes: list[float], dur: float, hay_gancho: bool, hay_cta: bool, t_cta: float, t_like: float | None, cambios: list[float] | None = None) -> list[tuple[float, str]]:
    """(segundo, sonido) de lo que suena. el autor, 16-oct: «quítale el sonido a los cambios de escena… el whoosh en el 50 % nada más… y todos los sonidos de piu, piu cuando sale algo»:
    NADA suena en los cortes, en los cambios de layout ni cuando sale una palabra, tarjeta, ilustración, gancho o «like» (el «pop»); el «whoosh» solo en una de cada dos escenas a
    pantalla completa (la 1.ª sí, la 2.ª no…); se queda el golpe de una cifra y el «ding» del final."""
    out: list[tuple[float, str]] = []
    k = 0
    for e in sorted(eventos, key=lambda z: z["t"]):
        t = e["t"]
        if e["tipo"] == "escena":
            if k % 2 == 0:
                out.append((max(0.0, t - 0.06), "whoosh"))
            k += 1
            if e.get("kind") == "dato":
                out.append((t + 0.9, "thud"))
        elif e["tipo"] == "tarjeta" and e.get("kind") == "dato":
            out.append((t + 0.05, "thud"))
    if hay_cta:
        out.append((t_cta + 0.05, "ding"))
    return sorted(out)


def lee_audio(src: Path, sr: int = SR, desde: float = 0.0, dur: float = 0.0) -> np.ndarray:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + (["-ss", f"{desde:.3f}"] if desde > 0 else []) + (["-t", f"{dur:.3f}"] if dur > 0 else []) + ["-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    return np.frombuffer(r.stdout, np.float32).copy() if r.returncode == 0 else np.zeros(0, np.float32)


def corta_audio(x: np.ndarray, segs: list[tuple[float, float]], sr: int = SR, fade_ms: float = 6.0) -> np.ndarray:
    """La voz de los tramos que se quedan, pegados, con un fundido de 6 ms en cada empalme (sin «clics»)."""
    f = max(1, int(sr * fade_ms / 1000))
    partes = []
    for a, b in segs:
        i0, i1 = int(round(a * sr)), int(round(b * sr))
        p = np.zeros(max(0, i1 - i0), np.float32)
        tro = x[i0:i1]
        p[: len(tro)] = tro
        if len(p) > 2 * f:
            p[:f] *= np.linspace(0, 1, f)
            p[-f:] *= np.linspace(1, 0, f)
        partes.append(p)
    return np.concatenate(partes) if partes else np.zeros(0, np.float32)


DB_BAJO_VOZ = 18.0       # el autor (16-oct): «mínimo 8 dB por debajo de mi voz» y, al oírlo, «sigue muy alto: bájale ~18 dB más»: los efectos van 18 dB por debajo del RMS de la voz


def rms_voz(x: np.ndarray, sr: int = SR) -> float:
    """El nivel (RMS) de la voz SOLO donde suena (bloques de 20 ms a menos de 35 dB de su pico), para que las pausas no lo bajen."""
    if len(x) < sr // 10:
        return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    n = sr // 50
    m = len(x) // n
    b = x[: m * n].reshape(m, n)
    r = np.sqrt(np.mean(b ** 2, axis=1))
    activo = r > max(1e-6, float(r.max()) * 10 ** (-35 / 20))
    return float(np.sqrt(np.mean(r[activo] ** 2))) if activo.any() else float(r.max())


def mezcla(voz: np.ndarray, fx: list[tuple[float, str]], sr: int = SR, db_bajo: float = DB_BAJO_VOZ) -> np.ndarray:
    """La voz + los efectos, cada efecto con su RMS `db_bajo` dB POR DEBAJO del RMS de la voz (y su pico nunca por encima de la voz − 8 dB)."""
    out = voz.astype(np.float32).copy()
    pico = float(np.max(np.abs(out))) if len(out) else 0.0
    if pico > 0.01:
        out *= min(3.0, 0.8 / pico)
    nivel = rms_voz(out, sr) or 0.1
    for t, nombre in fx:
        s = sfx(nombre)
        if not len(s):
            continue
        g = GANANCIA.get(nombre, 0.2)
        if g <= 0:
            continue
        rms_s = float(np.sqrt(np.mean(s ** 2))) or 1.0
        k = nivel * 10 ** (-db_bajo / 20) / rms_s * min(1.0, g / 0.24)
        k = min(k, float(np.max(np.abs(out))) * 10 ** (-8 / 20) / (float(np.max(np.abs(s))) or 1.0)) if pico > 0.01 else k
        s = s * k
        i = int(round(t * sr))
        if i >= len(out):
            continue
        n = min(len(s), len(out) - i)
        out[i:i + n] += s[:n]
    pico = float(np.max(np.abs(out))) if len(out) else 0.0
    if pico > 0.98:
        out *= 0.98 / pico
    return out


def escribe_wav(x: np.ndarray, ruta: Path, sr: int = SR) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ el montaje
def _cabecera(est: T.Est, alto: int) -> Image.Image:
    """Sin cámara (o sin sitio para ella): la zona de arriba lleva la marca del canal en vez de tu cara."""
    im = est.fondo("naranja", alto).copy()
    if est.canal:
        sp = T.sprite_texto(est, est.canal.upper(), est.pal.fuente_numero, 900, 220, 150, 60, 2, est.crema, trazo=8, sombra=7, inter=1.0)
        im.paste(sp, ((T.W - sp.width) // 2, (alto - sp.height) // 2 - 20), sp)
    return im


def renderiza(src: Path, salida: Path, segs: list[tuple[float, float]], words: list[dict], eventos: list[dict], meta: dict, est: T.Est, geo: dict, mov: list[tuple[float, float, float]],
              progreso=None, miniatura: Path | None = None, t_like: float | None = None, desfase: float = 0.0, dur_origen: float = 0.0) -> dict:
    """Monta el Short. `words` ya en el reloj del Short, `eventos` ya validados y rellenados. -> {"dur", "frames", "tiempo_s"}."""
    t_ini = time.time()
    n_seg = [max(1, int(round((b - a) * FPS))) for a, b in segs]
    N = sum(n_seg)
    dur = N / FPS
    inicios_seg = [0]
    for n in n_seg[:-1]:
        inicios_seg.append(inicios_seg[-1] + n)
    cortes = [i / FPS for i in inicios_seg[1:]]
    # --- audio
    voz = corta_audio(lee_audio(src, SR, desfase, dur_origen), segs)
    t_cta = max(0.0, dur - st_plan.T_CTA_FINAL)
    lays = st_plan.layouts(dur, cortes, eventos)
    fx = efectos([e for e in eventos if e["tipo"] != "flash"] + [e for e in eventos if e["tipo"] == "flash"], cortes, dur, bool(meta.get("gancho")), bool(meta.get("cta_final")), t_cta, t_like, [x["t"] for x in lays[1:]])
    audio = mezcla(voz, fx)
    if len(audio) < N * SR // FPS:
        audio = np.concatenate([audio, np.zeros(N * SR // FPS - len(audio), np.float32)])
    wav = salida.with_suffix(".audio.wav")
    escribe_wav(audio[: N * SR // FPS], wav)
    # --- capas
    capas = []
    for e in eventos:
        c = T.crea_capa(est, e)
        if c is not None:
            capas.append((e, c))
    gancho = T.Gancho(est, meta["gancho"], st_plan.T_GANCHO) if meta.get("gancho") else None
    cta = T.Cta(est, "sigueme", meta["cta_final"], st_plan.T_CTA_FINAL) if meta.get("cta_final") else None
    like = T.Cta(est, "like", "DALE LIKE", 2.2) if t_like is not None else None
    subs = T.Subtitulos(est, words)
    vertical = geo["modo"] == "vertical"
    sw, sh = tamano_tira(geo)
    tx, ty, tw, th = geo["tira"]
    cam = geo["camara"]
    cabeceras = {n: _cabecera(est, T.medidas(n)[0]) for n in T.LAYOUTS} if not cam and not vertical else {}
    cuda = _cuda_dec()
    # --- el codificador
    enc = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{T.W}x{T.H}", "-r", str(FPS), "-i", "-", "-i", str(wav)]
    enc += (["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "23", "-b:v", "0", "-maxrate", "10M", "-bufsize", "16M"] if _nvenc() else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
    enc += ["-pix_fmt", "yuv420p", "-g", "60", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(salida)]
    pe = subprocess.Popen(enc, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    miniatura_hecha = False
    idx = 0
    try:
        for run in corridas(segs):
            a0 = segs[run[0]][0]
            b1 = segs[run[-1]][1]
            n_run = int(round((b1 - a0) * FPS)) + 2
            vf_s = f"crop={tw}:{th}:{tx}:{ty},scale={sw}:{sh}:flags=bicubic,fps={FPS}"
            ls = Lector(src, a0 + desfase, n_run, vf_s, sw, sh, cuda)
            lc = Lector(src, a0 + desfase, n_run, f"crop={cam[2]}:{cam[3]}:{cam[0]}:{cam[1]},fps={FPS}", cam[2], cam[3], cuda) if cam else None
            pos = 0
            try:
                for k in run:
                    off = int(round((segs[k][0] - a0) * FPS))
                    ls.salta(off - pos)
                    if lc:
                        lc.salta(off - pos)
                    pos = off
                    for j in range(n_seg[k]):
                        fs = ls.lee()
                        fc = lc.lee() if lc else None
                        pos += 1
                        t = idx / FPS
                        t_src = segs[k][0] + j / FPS
                        # la zona B (pantalla) y la A (cara)
                        zs, zf, fl = zooms_en(t, eventos, cortes)
                        T.Zona.pon("base" if vertical else st_plan.layout_en(lays, t))
                        Z = T.Zona
                        alto_b = Z.b_h + (Z.face_h if vertical else 0)
                        y_b = 0 if vertical else Z.b_y0
                        cx, cy = centro_en(mov, t_src)
                        x0, y0, x1, y1 = caja_recorte(sw, sh, cx, cy, zs, T.W / alto_b)
                        canvas = Image.new("RGB", (T.W, T.H), est.tinta)
                        canvas.paste(Image.fromarray(fs).resize((T.W, alto_b), Image.BICUBIC, box=(x0, y0, x1, y1)), (0, y_b))
                        if fc is not None:
                            fx0, fy0, fx1, fy1 = caja_cara(cam[2], cam[3], zf, Z.face_h)
                            canvas.paste(Image.fromarray(fc).resize((T.W, Z.face_h), Image.BICUBIC, box=(fx0, fy0, fx1, fy1)), (0, 0))
                        elif cabeceras:
                            canvas.paste(cabeceras[Z.nombre], (0, 0))
                        # lo que dibuja encima: ilustración / palabra / tarjeta, luego la escena a pantalla completa
                        escena_activa = False
                        for e, capa in capas:
                            if e["t"] <= t < e["t"] + e["dur"]:
                                if e["tipo"] == "escena":
                                    escena_activa = True
                                    capa.pinta(canvas, t - e["t"])
                                else:
                                    capa.pinta(canvas, t - e["t"])
                        if like is not None and t_like <= t < t_like + like.dur:
                            like.pinta(canvas, t - t_like)
                        if cta is not None and t >= t_cta:
                            cta.pinta(canvas, t - t_cta)
                        if gancho is not None and t < gancho.dur:
                            gancho.pinta(canvas, t)
                        # banda de subtítulos (en una escena a pantalla completa, los subtítulos van encima y sin banda)
                        if not escena_activa:
                            ImageDraw.Draw(canvas).rectangle((0, Z.sub_y0, T.W, Z.sub_y0 + Z.sub_h), fill=est.tinta)
                            subs.pinta(canvas, t)
                        else:
                            subs.pinta(canvas, t, T.SUB_ESCENA_Y)
                        T.barra_progreso(canvas, est, (idx + 1) / N)
                        if fl > 0.01 and not escena_activa:
                            canvas = Image.blend(canvas, Image.new("RGB", (T.W, T.H), (255, 255, 255)), fl)
                        if miniatura is not None and not miniatura_hecha and t >= min(1.4, dur * 0.4):
                            canvas.save(miniatura, "JPEG", quality=88)
                            miniatura_hecha = True
                        pe.stdin.write(canvas.tobytes())
                        idx += 1
                        if progreso and idx % 15 == 0:
                            progreso(idx / N, f"Montando el Short… {idx * 100 // N} %")
            finally:
                ls.cierra()
                if lc:
                    lc.cierra()
    finally:
        try:
            pe.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        pe.wait(timeout=600)
        try:
            wav.unlink()
        except OSError:
            pass
    if pe.returncode != 0 or not salida.is_file():
        raise RuntimeError("ffmpeg no pudo codificar el Short (mira work/panel.log).")
    return {"dur": round(dur, 2), "frames": idx, "tiempo_s": round(time.time() - t_ini, 1)}


# ------------------------------------------------------------------ pruebas
def _fuente_sintetica(ruta: Path, dur: float = 12.0) -> list[tuple[float, float]]:
    """Un vídeo de 1280×720 a 30 fps: página con líneas, un cursor que se mueve y una CÁMARA magenta abajo a la derecha; con «voz» en tres ráfagas y silencios entre ellas."""
    habla = [(0.5, 3.0), (3.9, 6.0), (8.0, 10.5)]
    x = st_voz._sintetico(habla, dur, SR)
    wav = ruta.with_suffix(".wav")
    escribe_wav(x, wav)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x720", "-r", "30", "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
           "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest", str(ruta)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    for i in range(int(dur * 30)):
        t = i / 30.0
        im = Image.new("RGB", (1280, 720), (238, 240, 246))
        d = ImageDraw.Draw(im)
        for k in range(14):
            d.rectangle((40, 40 + k * 44, 700 - (k % 5) * 70, 64 + k * 44), fill=(190, 195, 210))
        cx = 120 + (t / dur) * 760
        cy = 120 + 300 * abs(math.sin(t * 0.9))
        d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=(0, 0, 0))
        d.rectangle((int(1280 * 0.70), int(720 * 0.62), int(1280 * 0.98), int(720 * 0.96)), fill=(255, 0, 255))
        d.ellipse((int(1280 * 0.79), int(720 * 0.68), int(1280 * 0.89), int(720 * 0.90)), fill=(210, 160, 130))
        p.stdin.write(im.tobytes())
    p.stdin.close()
    p.wait(timeout=120)
    try:
        wav.unlink()
    except OSError:
        pass
    return habla


def prueba() -> list[dict]:
    import tempfile

    from . import config as _config
    from .ingest import probe
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    camara = {"x": 0.70, "y": 0.62, "w": 0.28, "h": 0.34}
    g = geometria(1280, 720, camara)
    tx, ty, tw, th = g["tira"]
    cx, cy, cw, ch = g["camara"]
    caso("geometría: con cámara abajo a la derecha, la tira de pantalla queda a su IZQUIERDA y no la toca", g["modo"] == "camara" and tx + tw <= cx and ty == 0 and th == 720, g)
    caso("sin cámara: el modo es «solo» y la tira es todo el fotograma; un vídeo vertical se detecta", geometria(1280, 720, None)["modo"] == "solo" and geometria(1080, 1920, None)["modo"] == "vertical")
    x0, y0, x1, y1 = caja_recorte(900, 720, 0.99, 0.99, 1.3, RATIO_B)
    caso("el recorte nunca se sale de la tira (ni pegado a una esquina, ni con acercamiento) y mantiene la proporción", 0 <= x0 and x1 <= 900 and 0 <= y0 and y1 <= 720 and abs((x1 - x0) / (y1 - y0) - RATIO_B) < 1e-6)
    a = caja_recorte(900, 720, 0.5, 0.5, 1.0, RATIO_B)
    b = caja_recorte(900, 720, 0.5, 0.5, 1.4, RATIO_B)
    caso("más zoom = recuadro más pequeño y centrado en lo mismo", (b[2] - b[0]) < (a[2] - a[0]) * 0.8 and abs((a[0] + a[2]) - (b[0] + b[2])) < 1.0)
    cortes = [3.0, 6.0]
    z0 = zooms_en(1.0, [], cortes)
    z1 = zooms_en(3.4, [], cortes)
    z2 = zooms_en(6.5, [], cortes)
    caso("el plano alterna en cada corte (normal / más cerca / normal) y hay golpe justo tras el corte", z0[0] == 1.0 and z1[0] > 1.1 and z2[0] < z1[0] and zooms_en(3.05, [], cortes)[0] > zooms_en(4.5, [], cortes)[0])
    zp = zooms_en(2.1, [{"tipo": "zoom_pantalla", "t": 2.0, "dur": 0.7}], [])
    caso("un acercamiento del plan sube el zoom de la pantalla y no el de la cara", zp[0] > 1.1 and zp[1] == 1.0)
    caso("corridas: los tramos separados por poco se leen de una vez y los separados por mucho no", corridas([(0, 2), (2.5, 4), (9, 11), (11.3, 12)]) == [[0, 1], [2, 3]])
    fx = efectos([{"tipo": "escena", "kind": "dato", "t": 5.0, "dur": 2.6}, {"tipo": "palabra", "t": 1.0, "dur": 1.2, "texto": "A"}, {"tipo": "zoom_cara", "t": 2.0, "dur": 0.7}], [3.0], 12.0, True, True, 9.3, 7.0)
    nombres = [n for _, n in fx]
    fx = efectos([{"tipo": "escena", "kind": "dato", "t": 5.0, "dur": 2.6}, {"tipo": "escena", "kind": "cita", "t": 8.0, "dur": 2.6}, {"tipo": "escena", "kind": "cita", "t": 11.0, "dur": 2.6}, {"tipo": "palabra", "t": 1.0, "dur": 1.2, "texto": "A"},
                  {"tipo": "tarjeta", "kind": "punto", "t": 3.5, "dur": 2.0}, {"tipo": "ilus", "t": 4.0, "dur": 2.0}, {"tipo": "zoom_cara", "t": 2.0, "dur": 0.7}], [3.0], 14.0, True, True, 11.3, 7.0, [6.0])
    nombres = [n for _, n in fx]
    caso("sonidos: NADA en cortes, layouts, palabras, tarjetas, ilustraciones, gancho ni like (sin pop/swish/tick); el whoosh solo en 1 de cada 2 escenas; quedan el golpe de la cifra y el ding",
         nombres.count("whoosh") == 2 and "pop" not in nombres and "swish" not in nombres and "tick" not in nombres and "thud" in nombres and "ding" in nombres and [t for t, _ in fx] == sorted(t for t, _ in fx), nombres)
    caso("cada sonido sintetizado dura algo, no tiene NaN y no pasa de 1", all(0.04 < len(sfx(n)) / SR < 0.7 and np.all(np.isfinite(sfx(n))) and float(np.max(np.abs(sfx(n)))) <= 1.0001 for n in GANANCIA))
    voz = np.sin(np.arange(SR * 3) / 30.0).astype(np.float32) * 0.5
    m = mezcla(voz, [(0.5, "whoosh"), (1.0, "pop"), (2.9, "ding")])
    caso("la mezcla no recorta (pico ≤ 1) y mantiene la duración", len(m) == len(voz) and float(np.max(np.abs(m))) <= 1.0)
    rng_ = np.random.default_rng(2)
    habla = (np.sin(np.arange(SR * 4) / 9.0) * 0.45 * (np.sin(np.arange(SR * 4) / 3000.0) > -0.2)).astype(np.float32) + rng_.normal(0, 0.01, SR * 4).astype(np.float32)
    m2 = mezcla(habla, [(1.0, "whoosh"), (2.0, "thud"), (3.0, "ding")])
    base_ = mezcla(habla, [])
    niv = rms_voz(base_)
    peor = -999.0
    for t0, dd in ((1.0, 0.7), (2.0, 0.3), (3.0, 0.7)):
        a_, b_ = int(t0 * SR), int((t0 + dd) * SR)
        esc_ = float(np.sqrt(np.mean(m2[:SR // 2] ** 2))) / (float(np.sqrt(np.mean(base_[:SR // 2] ** 2))) or 1.0)       # (el limitador final puede bajar todo un poco)
        sf = m2[a_:b_] - base_[a_:b_] * esc_
        peor = max(peor, 20 * np.log10(float(np.sqrt(np.mean(sf ** 2))) / niv + 1e-9))
    caso("los efectos van MÍNIMO 8 dB por debajo de la voz (su RMS frente al de la voz donde suena)", peor <= -8.0, round(peor, 1))
    cc = corta_audio(np.ones(SR * 4, np.float32), [(0.5, 1.5), (2.5, 3.5)])
    caso("el audio cortado dura la suma de los tramos y tiene fundido en el empalme (sin clic)", abs(len(cc) / SR - 2.0) < 0.002 and cc[0] < 0.2 and float(cc[SR - 3]) < 0.6)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        src = t / "origen.mp4"
        _fuente_sintetica(src)
        wav16 = t / "a16.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav16)], check=True)
        cut = st_voz.corta(wav16, FPS)
        segs = cut["segmentos"]
        caso("de verdad (ffmpeg): el vídeo de origen de 12 s con 2 pausas largas se queda en menos de 8 s y con 3 tramos", len(segs) == 3 and cut["dur_short"] < 8.0, cut["dur_short"])
        words = []
        for a0, b0 in [(0.5, 3.0), (3.9, 6.0), (8.0, 10.5)]:
            pals = "Google elige solo y a veces se equivoca con tu pagina principal".split()
            n = max(2, int((b0 - a0) / 0.35))
            for k in range(n):
                words.append({"word": pals[k % len(pals)], "start": round(a0 + k * (b0 - a0) / n, 2), "end": round(a0 + (k + 0.8) * (b0 - a0) / n, 2)})
        w_out = st_voz.palabras_a_salida(words, cut["mapa"])
        dur = cut["dur_short"]
        geo = geometria(1280, 720, camara)
        mov = movimiento(src, geo, 12.0, cuda=False)
        caso("la actividad: se mide 4 veces por segundo y el centro sigue al cursor (de izquierda a derecha)", len(mov) >= 40 and mov[-1][1] > mov[4][1] + 0.1, (len(mov), mov[4][1] if len(mov) > 4 else None, mov[-1][1] if mov else None))
        ev = [{"tipo": "palabra", "t": 1.9, "texto": "GOOGLE ELIGE", "dur": 0.3, "origen": "ia"}, {"tipo": "tarjeta", "kind": "punto", "t": 4.6, "texto": "Elige la URL principal", "n": 1, "dur": 2.4, "origen": "ia"},
              {"tipo": "ilus", "ilus": "lupa", "texto": "BUSCAR", "t": 0.0, "dur": 0.4, "origen": "ia"}, {"tipo": "escena", "kind": "cita", "texto": "Google elige solo y a veces se equivoca con tu pagina", "t": 2.3, "dur": 2.3, "tono": "naranja", "origen": "ia"}]
        ev, _ = st_plan.convivencia(ev, dur)
        cortes_t = [b - a for a, b in segs]
        cortes_t = [float(v) for v in np.cumsum(cortes_t)[:-1]]
        ev = st_plan.rellena(ev, cortes_t, dur, w_out)
        cfg = _config.load()
        est = T.Est(dict(cfg.get_path("videos.marca") or {}), "Mi canal", "tu-sitio.example")
        out = t / "short.mp4"
        prog: list = []
        renderiza(src, out, segs, w_out, ev, {"gancho": "TU PAGINA PRINCIPAL", "cta_final": "SIGUEME PARA MAS SEO"}, est, geo, mov, progreso=lambda f, s: prog.append(f), miniatura=t / "mini.jpg", t_like=dur * 0.45)
        info = probe(out)
        caso("el Short sale en 1080×1920 a 30 fps, con audio, y dura lo que los tramos (±0,15 s)", (info["width"], info["height"]) == (1080, 1920) and abs(info["fps"] - 30) < 0.5 and info["has_audio"] and abs(info["duration"] - dur) < 0.15, (info, dur))
        caso("hay miniatura (un fotograma del Short) y el progreso avanzó", (t / "mini.jpg").is_file() and len(prog) >= 2 and prog[-1] > 0.5, len(prog))
        fr = t / "f"
        fr.mkdir()
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(out), "-vf", "fps=2", str(fr / "f_%03d.png")], check=True)
        imgs = sorted(fr.glob("f_*.png"))
        arrs = [np.asarray(Image.open(p).convert("RGB")).astype(np.int16) for p in imgs]

        def magenta(a):
            return int(((np.abs(a[..., 0] - 255) < 70) & (a[..., 1] < 70) & (np.abs(a[..., 2] - 255) < 70)).sum())
        lays = st_plan.layouts(dur, cortes_t, ev)
        en_escena = lambda tt: any(e["tipo"] == "escena" and e["t"] <= tt < e["t"] + e["dur"] for e in ev)  # noqa: E731
        info_fr = [(st_plan.layout_en(lays, i / 2 + 0.25), en_escena(i / 2 + 0.25)) for i in range(len(arrs))]
        largo = st_plan.layouts(45.0, [5.0 * k for k in range(1, 9)], [])
        caso("el calendario de layouts (45 s) usa los tres, empieza y acaba en «base», no deja huecos y ninguno dura menos de 2 s", {x["nombre"] for x in largo} == set(T.LAYOUTS) and largo[0]["nombre"] == "base" and largo[-1]["nombre"] == "base"
             and abs(sum(x["dur"] for x in largo) - 45.0) < 0.1 and min(x["dur"] for x in largo) >= 2.0 and abs(sum(x["dur"] for x in lays) - dur) < 0.02, [(x["nombre"], x["dur"]) for x in largo])
        zonaB = [a[T.medidas(n)[3]:T.H - T.SEGURO] for a, (n, esc) in zip(arrs, info_fr) if not esc]
        caso("la CÁMARA no sale en la zona de la pantalla de NINGÚN layout (ni un píxel magenta) y SÍ sale arriba (su recuadro)", sum(magenta(z) for z in zonaB) == 0 and any(magenta(a[:T.medidas(n)[0]]) > 3000 for a, (n, esc) in zip(arrs, info_fr) if not esc), [magenta(z) for z in zonaB][:6])
        banda = [a[T.medidas(n)[1]:T.medidas(n)[1] + T.medidas(n)[2]] for a, (n, esc) in zip(arrs, info_fr) if not esc]
        caso("los subtítulos salen en su banda pequeña (hay texto claro en algunos fotogramas) y NO en el borde de abajo (zona segura de TikTok)", sum(1 for b in banda if int((b.sum(axis=2) > 600).sum()) > 1200) >= 2
             and all(int((a[T.H - T.SEGURO + 60:T.H - 20].sum(axis=2) > 600).sum()) < 200 for a in arrs), len(banda))
        caso("el vídeo está vivo: dos fotogramas seguidos de la zona de la pantalla casi nunca son iguales", sum(1 for x, y in zip(zonaB, zonaB[1:]) if x.shape == y.shape and np.abs(x - y).mean() > 0.3) >= len(zonaB) * 0.6)
        naranja = est.naranja
        escena_fr = [a for i, a in enumerate(arrs) if 2.7 <= i / 2 + 0.25 <= 4.3]
        caso("la escena a pantalla completa (naranja) tapa la cara y la pantalla mientras dura", any(float(np.abs(a[10:70, 10:70].reshape(-1, 3).mean(axis=0) - np.array(naranja[:3])).max()) < 60 and float(np.abs(a[1500:1560, 10:70].reshape(-1, 3).mean(axis=0) - np.array(naranja[:3])).max()) < 60 for a in escena_fr), [(a[10:70, 10:70].reshape(-1, 3).mean(axis=0).round().tolist(), a[1500:1560, 10:70].reshape(-1, 3).mean(axis=0).round().tolist(), list(naranja[:3])) for a in escena_fr])
    return filas
