"""Detecta ventanas de ACTIVIDAD SOSTENIDA en la pantalla (cursor moviéndose, scroll, algo
cambiando) analizando la grabación fotograma a fotograma — nada de Gemini, puro ffmpeg + numpy,
gratis y local.

Por qué existe: `director.py` decide el modo de cámara (cara/pantalla/pantalla_zoom) SOLO a
partir de lo que se dice en la transcripción — nunca ve la pantalla. Si el presentador señala
algo con el mouse sin decir "miren aquí" con esas palabras, no hay ninguna señal para que Gemini
lo sepa. el autor lo pidió (2026-09-23): quiere que "pantalla_zoom" se ancle a cuando de verdad hay
movimiento en pantalla, no solo a cuando se menciona con palabras — y que no cuente una simple
vibración (ruido de compresión, un tembleque de un fotograma), solo movimiento SOSTENIDO.

`pipeline.py` llama a `detecta()` una vez por vídeo (cachea el resultado, igual que
`transcribe.py` con `words.json`: el vídeo no cambia entre Reshuffle/mejoras) y usa la lista de
ventanas de dos formas: (1) se la pasa a `director.py` como una pista más en el mismo prompt de
siempre (mismo costo, cero llamadas nuevas a Gemini) y (2) como red de seguridad, fuerza
"pantalla_zoom" si Gemini aun así dejó "cara" en un tramo con actividad detectada.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from .util import log

FPS_MUESTREO = 2.0          # fotogramas/segundo analizados — de sobra para "sostenido", barato
ANCHO_ANALISIS = 480        # se reduce a este ancho antes de comparar: más rápido y el ruido fino importa menos
UMBRAL_PIXEL = 20           # diferencia 0-255 para contar un píxel como "cambió" (ruido de compresión < esto)
UMBRAL_FRACCION = 0.003     # fracción mínima de píxeles-pantalla cambiados para contar el fotograma como "actividad" —
                            # medido sobre grabación real: el ruido de fondo (compresión, un reloj parpadeando) se
                            # queda por debajo de ~0.0013; un cursor moviéndose de verdad ya da 0.01+, así que 0.003
                            # deja aire de sobra sin perder movimiento genuino pero modesto
MIN_SOSTENIDO_S = 1.5       # cuánto tiene que durar SEGUIDO para no ser "una simple vibración"
CURSOR_MAX_FRAC = 0.02      # el RATÓN (2026-09-24): un cambio pequeño (≤ 2 % de la pantalla)…
CURSOR_MAX_CAJA = 0.30      # …y concentrado (su caja ≤ 30 % del lienzo en cada eje). Medido en el vídeo del 24-sep: mover
                            # el ratón cambia 0,01-0,2 % de la pantalla en una caja pequeña (x 0,67-0,80 y 0,46-0,72), mientras
                            # que cargar una página o hacer scroll cambia 30-83 % en TODA la pantalla (su centro no dice nada del
                            # ratón). Los cambios del ratón quedan por debajo de UMBRAL_FRACCION: no cuentan como «actividad».
FUNDE_HUECOS_S = 4.0        # dos ráfagas separadas por menos de esto se funden en una ventana — el autor lo pidió
                            # (2026-09-23): al EXPLICAR algo es normal que el mouse se quede quieto un momento
                            # mientras sigue hablando de lo mismo; con 1.0s esas pausas partían la actividad en
                            # ráfagas sueltas y el zoom entraba y salía todo el rato (visto en un vídeo real:
                            # zoom→pantalla→zoom→cara en segundos). Con 4.0s el zoom dura lo que dura la
                            # explicación, no lo que dura cada movimiento suelto del cursor


def _alto_analisis(ancho_video: int, alto_video: int, ancho: int = ANCHO_ANALISIS) -> int:
    """El alto correspondiente a `ancho` manteniendo la proporción real del vídeo, redondeado a
    par (lo pide el pixel format gris de ffmpeg)."""
    return max(2, round(ancho * alto_video / max(1, ancho_video) / 2) * 2)


def _mascara_pantalla(alto: int, ancho: int, camara_rect: dict | None) -> np.ndarray | None:
    """`True` = mirar ese píxel (es pantalla); `False` = el rectángulo de la CÁMARA (fracción 0-1
    del lienzo, igual que `videos.canales.<canal>.camara` en config.yaml) — para que el autor
    moviéndose delante de su webcam no cuente como "actividad en pantalla". None (mira el
    fotograma entero) si no hay rectángulo medido para este canal."""
    if not camara_rect:
        return None
    m = np.ones((alto, ancho), dtype=bool)
    x0 = round(float(camara_rect["x"]) * ancho)
    y0 = round(float(camara_rect["y"]) * alto)
    x1 = round((float(camara_rect["x"]) + float(camara_rect["w"])) * ancho)
    y1 = round((float(camara_rect["y"]) + float(camara_rect["h"])) * alto)
    m[max(0, y0):min(alto, y1), max(0, x0):min(ancho, x1)] = False
    return m


_CUDA_CACHE: dict = {}


def _cuda_disponible() -> bool:
    """¿ffmpeg puede decodificar con la GPU NVIDIA (hwaccel cuda)? Decodificar TODO el vídeo
    fotograma a fotograma (para poder bajarlo a `fps` muestras/s) es lo que de verdad tarda aquí
    — medido: ~2.2x más rápido por GPU que por CPU en esta máquina. Se pregunta UNA vez (cachea)."""
    if "ok" not in _CUDA_CACHE:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-hwaccels"], capture_output=True,
                               text=True, timeout=15, errors="replace")
            _CUDA_CACHE["ok"] = "cuda" in (r.stdout or "")
        except Exception:                                  # noqa: BLE001 — sin ffmpeg en el PATH o lo que sea: se cae a CPU sin reventar
            _CUDA_CACHE["ok"] = False
    return _CUDA_CACHE["ok"]


def _energia_por_fotograma(video: Path, ancho: int, alto: int, fps: float,
                           mascara: np.ndarray | None, timeout_s: float,
                           usa_gpu: bool = True) -> list[tuple[float, float | None, float | None]]:
    """Por cada muestra (cada 1/fps segundos): fracción de píxeles-pantalla que cambiaron respecto
    al fotograma anterior, y DÓNDE (el centroide de esos píxeles, en fracción 0-1 del lienzo —
    x,y = None si no cambió nada). El centroide es lo que deja luego centrar el zoom donde de
    verdad está pasando algo (el autor lo pidió, 2026-09-23), no solo detectar que algo pasa.
    Decodifica EN STREAMING —nunca carga el vídeo entero en memoria: los directos del autor pueden
    durar más de una hora— comparando cada fotograma solo contra el de antes."""
    tam_frame = ancho * alto
    hwaccel = ["-hwaccel", "cuda"] if usa_gpu and _cuda_disponible() else []
    cmd = ["ffmpeg", "-v", "error", *hwaccel, "-i", str(video), "-vf", f"fps={fps},scale={ancho}:{alto}",
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    energia: list[tuple[float, float | None, float | None]] = []
    anterior: np.ndarray | None = None
    total = float(mascara.sum()) if mascara is not None else float(tam_frame)
    try:
        while True:
            crudo = proc.stdout.read(tam_frame)
            if len(crudo) < tam_frame:
                break
            actual = np.frombuffer(crudo, dtype=np.uint8).reshape(alto, ancho)
            if anterior is not None:
                dif = np.abs(actual.astype(np.int16) - anterior.astype(np.int16)) > UMBRAL_PIXEL
                if mascara is not None:
                    dif &= mascara
                frac = float(dif.sum()) / total if total else 0.0
                cx = cy = px = py = None
                if frac > 0:
                    ys, xs = np.nonzero(dif)
                    cx, cy = float(xs.mean()) / ancho, float(ys.mean()) / alto
                    x0, x1, y0, y1 = xs.min() / ancho, xs.max() / ancho, ys.min() / alto, ys.max() / alto
                    if frac <= CURSOR_MAX_FRAC and x1 - x0 <= CURSOR_MAX_CAJA and y1 - y0 <= CURSOR_MAX_CAJA:
                        px, py = float(x0 + x1) / 2, float(y0 + y1) / 2          # el ratón (o lo que abre junto a él)
                energia.append((frac, cx, cy, px, py))
            anterior = actual
    finally:
        proc.stdout.close()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("ffmpeg no terminó a tiempo analizando actividad en pantalla")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg salió con código {proc.returncode} analizando actividad en pantalla")
    return energia


def _ventanas(energia: list[tuple[float, float | None, float | None]], fps: float,
             umbral: float = UMBRAL_FRACCION, min_sostenido_s: float = MIN_SOSTENIDO_S,
             funde_huecos_s: float = FUNDE_HUECOS_S) -> list[dict]:
    """La serie de "fracción de píxeles que cambiaron + dónde" -> ventanas [{"inicio","fin","x","y"}]
    con actividad SOSTENIDA. "x","y" (fracción 0-1 del lienzo) es el PUNTO MEDIO de dónde se movió
    el cursor en esa ventana (percentil 10-90 de los centroides, no min/max: una lectura suelta
    rara no debe estirar la caja) — el autor fue explícito (2026-09-23): "siempre se tiene que ver el
    mouse en ese zoom" — no solo el punto más TÍPICO (eso sería la mediana), sino uno que cubra
    todo el RECORRIDO real, para que un recorte centrado ahí no deje el cursor fuera de cuadro a
    mitad de la ventana. Nunca un solo pico: hace falta que dure `min_sostenido_s` seguidos para
    no confundir una vibración de compresión con un movimiento real."""
    paso = 1.0 / fps
    crudas: list[list] = []       # [inicio, fin, [cx,...], [cy,...]]
    for i, muestra in enumerate(energia):
        e, cx, cy = muestra[0], muestra[1], muestra[2]          # (las muestras nuevas traen también dónde está el ratón)
        if e <= umbral:
            continue
        t = i * paso
        if crudas and t - crudas[-1][1] <= paso + 1e-6:
            crudas[-1][1] = t
        else:
            crudas.append([t, t, [], []])
        if cx is not None:
            crudas[-1][2].append(cx)
            crudas[-1][3].append(cy)
    fundidas: list[list] = []
    for a, b, cxs, cys in crudas:
        if fundidas and a - fundidas[-1][1] <= funde_huecos_s:
            fundidas[-1][1] = b
            fundidas[-1][2] += cxs
            fundidas[-1][3] += cys
        else:
            fundidas.append([a, b, cxs, cys])
    out = []
    for a, b, cxs, cys in fundidas:
        if (b - a) < min_sostenido_s:
            continue
        v = {"inicio": round(a, 1), "fin": round(b + paso, 1)}
        if cxs:
            x_lo, x_hi = np.percentile(cxs, 10), np.percentile(cxs, 90)
            y_lo, y_hi = np.percentile(cys, 10), np.percentile(cys, 90)
            v["x"], v["y"] = round(float(x_lo + x_hi) / 2, 4), round(float(y_lo + y_hi) / 2, 4)
        out.append(v)
    return out


def detecta(video: Path, ancho_video: int, alto_video: int, dur: float,
           camara_rect: dict | None = None, cache: Path | None = None) -> list[dict]:
    """Ventanas [{"inicio","fin","x","y"}] con actividad sostenida en PANTALLA (excluye el
    rectángulo de la cámara si se da) — "x","y" (fracción 0-1 del lienzo, ausentes si por lo que
    sea no se pudo ubicar) son DÓNDE, para poder centrar el zoom ahí en vez de un rectángulo fijo
    (el autor lo pidió, 2026-09-23; ver `render.centro_pantalla_zoom`). Cachea en `cache` — el
    análisis no cambia entre Reshuffle/mejoras del mismo archivo. Nunca revienta el pipeline: si
    algo falla (vídeo raro, ffmpeg sin ese filtro, lo que sea), avisa y sigue con lista vacía —
    es una pista extra, no un requisito."""
    # el recorrido del RATÓN va en `cursor.json`, al lado (lo usa el zoom a la pantalla: `pipeline.encuadres_zoom`); una
    # grabación analizada antes de eso se vuelve a analizar UNA vez para sacarlo (los dos salen de la misma pasada)
    cache_cursor = cache.with_name("cursor.json") if cache else None
    if cache and cache.is_file() and cache_cursor.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    energia = []
    try:
        alto = _alto_analisis(ancho_video, alto_video)
        mascara = _mascara_pantalla(alto, ANCHO_ANALISIS, camara_rect)
        timeout_s = max(60.0, dur * 1.5)
        try:
            energia = _energia_por_fotograma(video, ANCHO_ANALISIS, alto, FPS_MUESTREO, mascara,
                                             timeout_s=timeout_s, usa_gpu=True)
        except Exception as exc_gpu:                                  # noqa: BLE001 — GPU rara/driver raro: se reintenta por CPU antes de rendirse
            log(f"AVISO: decodificar por GPU falló analizando actividad en pantalla ({exc_gpu}); reintento por CPU.")
            energia = _energia_por_fotograma(video, ANCHO_ANALISIS, alto, FPS_MUESTREO, mascara,
                                             timeout_s=timeout_s, usa_gpu=False)
        ventanas = _ventanas(energia, FPS_MUESTREO)
    except Exception as exc:                                        # noqa: BLE001
        log(f"AVISO: no se pudo analizar actividad en pantalla ({exc}); sigo sin esa pista.")
        ventanas = []
    if cache:
        try:
            cache.write_text(json.dumps(ventanas), encoding="utf-8")
            cache_cursor.write_text(json.dumps(recorrido_cursor(energia, FPS_MUESTREO)), encoding="utf-8")
        except OSError:
            pass
    return ventanas


def recorrido_cursor(energia: list[tuple], fps: float) -> list[list[float]]:
    """Dónde está el RATÓN a lo largo del vídeo: [[segundo, x, y], ...] (fracción 0-1 del lienzo), una entrada por
    muestra en la que se movió (o se abrió algo pequeño junto a él); cuando no se mueve, no hay entrada: sigue donde
    estaba. Las muestras de antes de esto no traen la posición del ratón: lista vacía."""
    paso = 1.0 / fps
    return [[round(i * paso, 2), round(m[3], 4), round(m[4], 4)] for i, m in enumerate(energia)
            if len(m) >= 5 and m[3] is not None]


def quita_fijos(cursor: list[tuple[float, float, float]], radio: float = 0.01, veces: int = 3,
               suelto_s: float = 1.0) -> list[tuple[float, float, float]]:
    """Quita lo que parece el ratón y NO lo es: un cambio pequeño que vuelve una y otra vez al MISMO sitio, suelto (sin
    nada que se mueva alrededor). El reloj de la barra de tareas de Windows cambia cada minuto: en el vídeo del 24-sep
    salía como «ratón» en (0,975; 0,97) en los segundos 103, 223, 283 y 343 — metido en un zoom, lo obligaba a enseñar
    la esquina de la cámara. El ratón de verdad casi nunca da `veces` muestras sueltas en el mismo punto (para llegar a
    un sitio se mueve y deja muestras por el camino)."""
    ts = [p[0] for p in cursor]
    sueltas = [i for i, t in enumerate(ts)
               if (i == 0 or t - ts[i - 1] > suelto_s) and (i == len(ts) - 1 or ts[i + 1] - t > suelto_s)]
    fuera = set()
    for i in sueltas:
        iguales = [j for j in sueltas
                   if abs(cursor[j][1] - cursor[i][1]) <= radio and abs(cursor[j][2] - cursor[i][2]) <= radio]
        if len(iguales) >= veces:
            fuera.update(iguales)
    return [p for k, p in enumerate(cursor) if k not in fuera]


def lee_cursor(cache: Path | None) -> list[tuple[float, float, float]]:
    """El recorrido del ratón guardado junto a `actividad_pantalla.json` (vacío si no hay), sin lo que no es el ratón
    (`quita_fijos`: el reloj de la barra de tareas)."""
    try:
        return quita_fijos([tuple(p) for p in json.loads(Path(cache).with_name("cursor.json").read_text(encoding="utf-8"))])
    except (OSError, ValueError, TypeError):
        return []


def texto_prompt(ventanas: list[dict] | None) -> str:
    """Las ventanas -> el texto que se le pega al prompt del director."""
    if not ventanas:
        return "(no se detectó actividad sostenida en pantalla, o no se pudo analizar)"
    return ", ".join(f"{v['inicio']:.1f}s-{v['fin']:.1f}s" for v in ventanas)


def prueba() -> list[dict]:
    """Sin red, sin Gemini: fabrica un clip sintético con una zona QUIETA y una zona con
    movimiento real y verifica que `detecta()` las distingue — y que un umbral absurdamente alto
    (nada cruza nunca) da ventanas vacías sin reventar."""
    import shutil
    import tempfile
    out = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": detalle})

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-movimiento-"))
    try:
        clip = tmp / "clip.mp4"
        dur = 8
        # mitad de ARRIBA: un cuadro que se mueve todo el clip. Mitad de ABAJO: liso, quieto.
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", f"color=c=0x202020:s=320x240:r=10:d={dur}",
               "-f", "lavfi", "-i", f"color=c=0xffffff:s=40x40:r=10:d={dur}",
               "-filter_complex", "[0:v][1:v]overlay=x='mod(t*60,280)':y=20[v]",
               "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
               "-pix_fmt", "yuv420p", str(clip)]
        p = subprocess.run(cmd, capture_output=True, timeout=60)
        if p.returncode != 0 or not clip.is_file():
            r("fabricar clip", False, (p.stderr or b"").decode("utf-8", "replace")[-200:])
            return out
        r("fabricar clip", True, f"{clip.stat().st_size} bytes")

        ventanas = detecta(clip, 320, 240, float(dur))
        hay_ventana = any(v["fin"] - v["inicio"] >= MIN_SOSTENIDO_S for v in ventanas)
        r("detecta actividad real y sostenida", hay_ventana, str(ventanas))
        # el cuadro se mueve TODO el clip (menos el arranque, que se pierde en el primer diff):
        # la ventana detectada debería cubrir casi todo el clip, no un instante suelto
        cubierto = sum(v["fin"] - v["inicio"] for v in ventanas)
        r("la ventana cubre la mayor parte del clip (no un instante)", cubierto >= dur * 0.5,
          f"cubierto={cubierto:.1f}s de {dur}s")
        # el cuadro se mueve SIEMPRE a la misma altura (y=20 de 240, cuadro de 40): el centro Y
        # detectado debe caer ahí (con margen) sin importar por dónde ande en X ese instante
        v0 = ventanas[0]
        r("la ventana trae DÓNDE (x,y) además de CUÁNDO", "x" in v0 and "y" in v0, v0)
        r("la altura (y) detectada coincide con la del cuadro (cerca de 0.17, no en cualquier lado)",
          "y" in v0 and 0.08 <= v0["y"] <= 0.30, v0.get("y"))
        r("la x detectada cae dentro del lienzo (0-1): se movió mucho, la mediana no puede salirse",
          "x" in v0 and 0.0 <= v0["x"] <= 1.0, v0.get("x"))

        # con la máscara tapando TODO el fotograma (como si la cámara fuera el lienzo entero),
        # no debería quedar nada que mirar -> sin ventanas, sin reventar
        sin_nada = detecta(clip, 320, 240, float(dur),
                           camara_rect={"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
        r("con todo el lienzo tapado no detecta nada (y no revienta)", sin_nada == [],
          str(sin_nada))

        # cacheado: segunda llamada con el mismo cache no debería volver a invocar ffmpeg —
        # se comprueba borrando el clip fuente y viendo que igual responde bien
        cache = tmp / "cache.json"
        primero = detecta(clip, 320, 240, float(dur), cache=cache)
        clip.unlink()
        segundo = detecta(clip, 320, 240, float(dur), cache=cache)
        r("la caché evita reanalizar (el archivo fuente ya no existe y aun así responde)",
          segundo == primero, str(segundo))

        r("texto_prompt() maneja vacío sin reventar", "no se detectó" in texto_prompt([]))
        r("texto_prompt() lista una ventana real", "-" in texto_prompt([{"inicio": 1.0, "fin": 2.0}]))

        # el RATÓN: un puntito de 8 px que cruza la pantalla de izquierda a derecha a media altura (como el cursor)
        clip2 = tmp / "raton.mp4"
        cmd2 = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=0x202020:s=640x360:r=10:d=8",
                "-f", "lavfi", "-i", "color=c=0xffffff:s=8x8:r=10:d=8",
                "-filter_complex", "[0:v][1:v]overlay=x='40+t*70':y=176[v]",
                "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", str(clip2)]
        subprocess.run(cmd2, capture_output=True, timeout=60)
        cache2 = tmp / "raton" / "actividad_pantalla.json"
        cache2.parent.mkdir()
        v2 = detecta(clip2, 640, 360, 8.0, cache=cache2)
        cur = lee_cursor(cache2)
        xs = [p[1] for p in cur]
        r("el ratón (cambio pequeño y concentrado) se sigue: muchas posiciones, a media altura y de izquierda a derecha, "
          "sin contar como «actividad»", len(cur) >= 8 and all(0.40 <= p[2] <= 0.60 for p in cur)
          and xs[0] < 0.25 and xs[-1] > 0.70 and all(b >= a - 0.02 for a, b in zip(xs, xs[1:])) and v2 == [],
          (len(cur), cur[:3], cur[-3:], v2))
        r("una página que cambia entera (el cuadro grande de antes) no es el ratón", lee_cursor(cache) == [],
          lee_cursor(cache)[:3])
        # el reloj de la barra de tareas (el caso real del 24-sep): suelto, cada minuto, siempre en el mismo sitio
        reloj = [(103.0, 0.975, 0.9704), (223.0, 0.976, 0.9685), (283.0, 0.975, 0.9704), (343.0, 0.975, 0.9704)]
        raton = [(174.0, 0.6979, 0.6611), (174.5, 0.7469, 0.6111), (175.5, 0.7354, 0.5537), (176.0, 0.6854, 0.5315)]
        limpio = quita_fijos(sorted(reloj + raton + [(50.0, 0.5, 0.5), (90.0, 0.5, 0.5)]))
        r("el reloj de la barra de tareas (cambia cada minuto en el mismo punto) no es el ratón; el ratón de verdad y un "
          "punto repetido solo 2 veces sí se quedan", limpio == sorted(raton + [(50.0, 0.5, 0.5), (90.0, 0.5, 0.5)]), limpio)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
