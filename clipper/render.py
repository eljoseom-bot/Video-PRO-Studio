"""Corte + reencuadre 9:16 + subtítulos quemados, en un solo pase de ffmpeg.

Un solo pase importa: cada re-encode adicional pierde calidad y duplica
el tiempo de render. Con 40 clips por directo eso se nota.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .config import ROOT

NL = chr(10)
from .util import error_de_arranque, filtro_a_archivo, log, run


def _escape_filter_path(p: Path) -> str:
    """ffmpeg usa ':' y '\\' como separadores dentro de los filtros."""
    s = str(p)
    s = s.replace("\\", "/")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    s = s.replace("[", r"\[").replace("]", r"\]")
    s = s.replace(",", r"\,")
    return s


def _even(n: float) -> int:
    """ffmpeg quiere dimensiones pares con yuv420p."""
    return max(2, int(n) // 2 * 2)


def centro_pantalla_zoom(rect_defecto: dict, centro: tuple[float, float] | None,
                         camara_rect: dict | None) -> dict:
    """El rectángulo de "pantalla_zoom" (fracción 0-1 del lienzo) para UN tramo: si `centro`
    trae dónde (x,y) detectó actividad de verdad `clipper/movimiento.py` en ese tramo, recentra
    `rect_defecto` (el de config.yaml, mismo tamaño de siempre — el autor pidió un "perímetro
    amplio", no un recorte ceñido al cursor) ahí; si no hay centro, usa `rect_defecto` tal cual.
    el autor lo pidió (2026-09-23): "pantalla_zoom" enseñaba SIEMPRE el mismo rectángulo fijo — si lo
    que señalaba con el mouse caía fuera de esa zona (p. ej. arriba del todo), se perdía.

    Nunca deja que el rectángulo se meta en el recuadro de la CÁMARA (`camara_rect`, si se da):
    lo aparta por el eje que haga falta (izquierda o arriba de la cámara), y si de plano no cabe
    sin solaparse con ese tamaño, vuelve a `rect_defecto` — mejor el encuadre de siempre que uno
    que enseñe tu cámara por encima de la pantalla."""
    w, h = float(rect_defecto["w"]), float(rect_defecto["h"])
    if not centro:
        return dict(rect_defecto)
    cx, cy = centro
    x = min(max(0.0, cx - w / 2), 1.0 - w)
    y = min(max(0.0, cy - h / 2), 1.0 - h)
    if camara_rect:
        cx0, cy0 = float(camara_rect["x"]), float(camara_rect["y"])
        cx1 = cx0 + float(camara_rect["w"])
        cy1 = cy0 + float(camara_rect["h"])
        solapa = x < cx1 and x + w > cx0 and y < cy1 and y + h > cy0
        if solapa:
            if cx0 - w >= 0:
                x = cx0 - w                    # aparta a la izquierda de la cámara
            elif cy0 - h >= 0:
                y = cy0 - h                    # o arriba de la cámara, si eso sí cabe
            else:
                return dict(rect_defecto)      # no cabe sin solaparse con este tamaño: mejor el de siempre
    out = dict(rect_defecto)
    out["x"], out["y"] = round(x, 4), round(y, 4)
    return out


EXPLICATIVA_ALTO = 0.94            # fracción de la altura de la cámara que se aprovecha en «explicativa»
# 📷 Cámara aparte: cuánto puede salirse un tramo del archivo de la cámara (Source Record arranca unas décimas DESPUÉS
# que la grabación y para unas décimas ANTES: el 28-sep-2026, 0,12 s y 0,29 s) y aun así usarla, con su primer/último
# fotograma quieto ese trocito. Sin esto la despedida entera (el último tramo, que acaba con la grabación) salía del
# recuadro ampliado, como siempre.
_CAM_HOLGURA = 1.0


def explicativa_rect(camara_rect: dict, aspecto: float, src_w: int, src_h: int) -> tuple[float, float, float, float]:
    """El trozo de la grabación (fracciones x, y, w, h) que llena el TERCIO derecho de «explicativa»: tu
    cámara, centrada en ELLA (no en el rectángulo ampliado de «cara», que se aplasta contra el borde del
    lienzo porque la cámara vive en la esquina), con la proporción del tercio (`aspecto` = ancho/alto,
    más alto que ancho). Ocupa el 94 % de la altura de la cámara para dejar fuera el marco rojo de arriba
    y de abajo; si la cámara fuera tan estrecha que esa altura no cabe a lo ancho, manda el ancho. Siempre
    dentro del lienzo. La usan el render (`render_merged`) y los ejemplos de «Ediciones» (`ejemplos.vista`)."""
    cw_f, ch_f = float(camara_rect["w"]), float(camara_rect["h"])
    alto = ch_f * EXPLICATIVA_ALTO
    ancho = alto * src_h * aspecto / src_w              # ancho de la ventana, en fracción del ancho de origen
    if ancho > cw_f * EXPLICATIVA_ALTO:
        ancho = cw_f * EXPLICATIVA_ALTO
        alto = ancho * src_w / (src_h * aspecto)
    ancho, alto = min(1.0, ancho), min(1.0, alto)
    cx = float(camara_rect["x"]) + cw_f / 2
    cy = float(camara_rect["y"]) + ch_f / 2
    return (min(max(0.0, cx - ancho / 2), 1.0 - ancho), min(max(0.0, cy - alto / 2), 1.0 - alto), ancho, alto)


def _ken_burns_params(cfg) -> tuple[float, float]:
    """(zoom, deriva) de Ken Burns, de config.yaml. La deriva NUNCA puede pasar de lo que el zoom deja
    libre a cada lado (con 6% de zoom: ~2.8% del ancho) — más allá la ventana se saldría del fotograma
    y el filtro mostraría el borde vacío; por eso `ken_burns_pan` se recorta a ese tope en vez de fiarse
    de lo que ponga config.yaml. `zoom == 0` = apagado del todo (sin zoom no queda margen para moverse)."""
    zoom = max(0.0, float(cfg.get_path("transiciones.ken_burns_zoom", 0.06)))
    pan = max(0.0, float(cfg.get_path("transiciones.ken_burns_pan", 0.015)))
    return zoom, min(pan, 0.5 * zoom / (1.0 + zoom))


def ken_burns_ventana(p: float, direccion: int, cfg, ancho: float = 1.0,
                      alto: float = 1.0) -> tuple[float, float, float, float] | None:
    """La ventana (x0, y0, x1, y1) del fotograma que se estira a pantalla completa cuando el tramo va
    por `p` (0 = arranque, 1 = final) — LA fórmula de Ken Burns, en números. Al principio ES el
    fotograma entero y va encogiendo hasta `1/(1+ken_burns_zoom)` de él, mientras su centro deriva
    de lado hasta `ken_burns_pan`. `ancho`/`alto` = las medidas del fotograma (1.0 = en fracción).
    La usan tanto el render (`_ken_burns_coords` la escribe como expresiones de ffmpeg) como los
    ejemplos de la pestaña «Ediciones» (`clipper/ejemplos.py`), para que lo que se ve allí sea
    EXACTAMENTE lo que hace el render. None si está apagado."""
    zoom, pan = _ken_burns_params(cfg)
    if zoom <= 0:
        return None
    p = min(1.0, max(0.0, float(p)))
    z = 1.0 + zoom * p
    cx = ancho / 2 + direccion * pan * ancho * p
    return (cx - ancho / (2 * z), alto / 2 - alto / (2 * z), cx + ancho / (2 * z), alto / 2 + alto / (2 * z))


def _ken_burns_coords(dur: float, direccion: int, fps: int, cfg, desde: float = 0.0,
                      total: float | None = None) -> dict | None:
    """Las cuatro expresiones (de `W`, `H` y `on`, el nº de fotograma) de `ken_burns_ventana` para
    ffmpeg. None si el efecto está apagado (config.yaml).

    `total` = lo que dura la ESCENA entera y `desde` = cuánto llevaba ya cuando empieza este trozo:
    una escena con un corte en medio son dos trozos del mismo modo, y el zoom tiene que SEGUIR donde
    iba (no volver a empezar de cero en cada trozo, que se vería como un acercamiento que se
    reinicia). Por defecto un trozo es una escena entera."""
    zoom, pan = _ken_burns_params(cfg)
    if zoom <= 0:
        return None
    n = max(2, int(round(max(0.3, total if total else dur) * fps)))
    off = int(round(max(0.0, desde) * fps))
    p = f"min((on+{off})/{n},1)"
    z = f"(1+{zoom:.4f}*{p})"
    cx = f"(W/2{direccion:+d}*{pan:.4f}*W*{p})"
    return {"x0": f"{cx}-W/(2*{z})", "x1": f"{cx}+W/(2*{z})",
            "y0": f"H/2-H/(2*{z})", "y1": f"H/2+H/(2*{z})"}


def _ken_burns_fx(dur: float, direccion: int, fps: int, cfg, desde: float = 0.0,
                  total: float | None = None) -> str:
    """Pan/zoom sutil ("Ken Burns", el autor, 2026-09-23): un acercamiento lento a lo largo de TODO el
    tramo + una deriva lateral pequeña, para que "cara"/"pantalla"/"pantalla_zoom" no se sientan
    un fotograma congelado. Se encadena DESPUÉS del cam_fx normal de ese modo (mismo recorte de
    siempre — esto no cambia QUÉ se ve, solo que ya no se queda quieto). `direccion` (+1/-1) decide
    hacia qué lado deriva la cámara — variedad entre tramos del mismo vídeo, no siempre el mismo
    lado (ver `pipeline._ken_burns_por_segmento`, quien decide si aplica y hacia dónde).

    Con el filtro `perspective` (interpolación bicúbica con posiciones de SUB-PÍXEL, re-evaluado en
    CADA fotograma), NO con `scale`+`crop`: el primer intento (2026-09-23) escalaba a un tamaño
    entero y par en cada fotograma, y como el zoom total son unos pocos píxeles repartidos en cientos
    de fotogramas, el tamaño solo cambiaba cada ~6 fotogramas (medido: 0.19px de golpe, luego seis
    fotogramas iguales) — el «zoom, zoom, zoom por partes» que el autor vio aunque el vídeo SÍ iba a 60
    fps. `fps=` antes del filtro fija el ritmo (`on` cuenta fotogramas, así el avance es exacto
    aunque la grabación no sea de fps perfectamente constantes) y `format=yuv420p` lo protege de
    una grabación en un formato de píxel que `perspective` no soporta."""
    c = _ken_burns_coords(dur, direccion, fps, cfg, desde, total)
    if not c:
        return ""
    return (f"format=yuv420p,fps={fps},"
            f"perspective=x0='{c['x0']}':y0='{c['y0']}':x1='{c['x1']}':y1='{c['y0']}':"
            f"x2='{c['x0']}':y2='{c['y1']}':x3='{c['x1']}':y3='{c['y1']}':"
            f"interpolation=cubic:sense=source:eval=frame,")


def _lineal(fs: list[int], vs: list[float]) -> str:
    """Expresión de ffmpeg (de `on`, el nº de fotograma) que va de un valor al siguiente en línea recta entre los
    fotogramas `fs` (crecientes, el primero 0) y se queda en el último al acabar."""
    expr = f"{vs[-1]:.2f}"
    for k in range(len(fs) - 2, -1, -1):
        f0, f1, v0, v1 = fs[k], fs[k + 1], vs[k], vs[k + 1]
        if f1 <= f0:
            continue
        expr = f"if(lt(on,{f1}),{v0:.2f}+({v1 - v0:.2f})*(on-{f0})/{f1 - f0},{expr})"
    return expr


def camino_se_mueve(camino: dict | None) -> bool:
    """¿El encuadre de ese «pantalla_zoom» se mueve durante el tramo? (Hoy los de `pipeline.encuadres_zoom` son FIJOS.)"""
    pts = (camino or {}).get("puntos") or []
    return any(abs(p[1] - pts[0][1]) > 1e-4 or abs(p[2] - pts[0][2]) > 1e-4 for p in pts)


def zoom_movil_fx(camino: dict, src_w: int, src_h: int, W_out: int, H_out: int, fps: int) -> str:
    """El encuadre de «pantalla_zoom» calculado con el ratón (`pipeline.encuadres_zoom`, el autor 2026-09-24): una ventana de
    la forma del vídeo (`camino["w"]`, `["h"]`: fracción del lienzo) con su centro en `camino["puntos"]` [(segundo, x, y)].
    Hoy es FIJO (el que enseña todo lo que hace el ratón): un `crop`. Si algún día se mueve, va con `perspective`
    (sub-píxel, re-evaluado en cada fotograma) como el movimiento suave: `crop` solo mueve a píxeles enteros y un paneo
    lento daría tirones."""
    ww, hh = float(camino["w"]) * src_w, float(camino["h"]) * src_h
    pts = camino["puntos"]
    xs = [min(max(0.0, x * src_w - ww / 2), src_w - ww) for _t, x, _y in pts]
    ys = [min(max(0.0, y * src_h - hh / 2), src_h - hh) for _t, _x, y in pts]
    if max(xs) - min(xs) < 0.5 and max(ys) - min(ys) < 0.5:
        return f"crop={_even(ww)}:{_even(hh)}:{_even(xs[0])}:{_even(ys[0])},scale={W_out}:{H_out},"
    fs = [int(round(t * fps)) for t, _x, _y in pts]
    X, Y = _lineal(fs, xs), _lineal(fs, ys)
    return (f"format=yuv420p,fps={fps},"
            f"perspective=x0='{X}':y0='{Y}':x1='{X}+{ww:.2f}':y1='{Y}':x2='{X}':y2='{Y}+{hh:.2f}':"
            f"x3='{X}+{ww:.2f}':y3='{Y}+{hh:.2f}':interpolation=cubic:sense=source:eval=frame,"
            f"scale={W_out}:{H_out},")


def _ken_burns_escenas(durs: list[float], modos: list[str],
                       ken_burns: list[int]) -> tuple[dict[int, float], dict[int, float]]:
    """Por cada trozo con Ken Burns: cuánto llevaba ya su ESCENA (`desde`) y cuánto dura entera
    (`total`), en segundos — una escena = los trozos SEGUIDOS del mismo modo con el mismo Ken Burns
    (con un corte en medio, son dos trozos de la misma escena). Así el zoom continúa donde iba al
    pasar de un trozo al siguiente en vez de reiniciarse en cada uno."""
    desde: dict[int, float] = {}
    total: dict[int, float] = {}
    n = min(len(durs), len(modos), len(ken_burns))
    i = 0
    while i < n:
        kb0 = ken_burns[i]
        if not kb0:
            i += 1
            continue
        j = i
        while j + 1 < n and modos[j + 1] == modos[i] and ken_burns[j + 1] == kb0:
            j += 1
        suma = sum(durs[i:j + 1])
        acum = 0.0
        for k in range(i, j + 1):
            desde[k], total[k] = acum, suma
            acum += durs[k]
        i = j + 1
    return desde, total


def _box(d: dict, W: int, H: int, defx=0, defy=0, defw=None, defh=None) -> tuple[int, int, int, int]:
    """Caja {x,y,w,h} en px del lienzo de salida, redondeada a par y recortada
    para no salirse del 1080x1920."""
    w = _even(d.get("w", defw if defw is not None else W))
    h = _even(d.get("h", defh if defh is not None else H))
    w = min(w, W); h = min(h, H)
    x = int(d.get("x", defx)); y = int(d.get("y", defy))
    x = max(0, min(x, W - w)) // 2 * 2
    y = max(0, min(y, H - h)) // 2 * 2
    return x, y, w, h


def _censor_windows(words, segments, cfg) -> list[tuple[float, float]]:
    """Ventanas (en tiempo del CLIP, base 0) donde hay que silenciar por
    palabrota. `segments` = [(src_start, src_end), ...]."""
    from .util import normalize
    if not cfg.get_path("censura.enabled", True):
        return []
    banned = {normalize(w).strip() for w in (cfg.get_path("censura.palabras") or [])
              if str(w).strip()}
    if not banned or not words:
        return []
    pad = float(cfg.get_path("censura.pad", 0.07))
    wins: list[tuple[float, float]] = []
    offset = 0.0
    for s, e in segments:
        for w in words:
            if w["start"] >= s and w["end"] <= e and normalize(w["word"]).strip() in banned:
                a = offset + (w["start"] - s) - pad
                b = offset + (w["end"] - s) + pad
                wins.append((max(0.0, a), b))
        offset += e - s
    wins.sort()
    merged: list[tuple[float, float]] = []
    for a, b in wins:
        if merged and a <= merged[-1][1] + 0.02:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _censor_prefix(words, segments, cfg) -> str:
    """Prefijo de filtro de audio: silencia las ventanas de palabrotas.
    Vacío si no hay ninguna."""
    wins = _censor_windows(words, segments, cfg)
    if not wins:
        return ""
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in wins)
    log(f"Censura de audio: {len(wins)} tramo(s) silenciado(s).")
    return f"volume=enable='{cond}':volume=0,"


def _audio_filter(cfg, source: Path | None = None,
                  start: float | None = None, dur: float | None = None) -> str:
    """Normalización de loudness EBU R128 (loudnorm), estándar de YouTube/TikTok.

    Si se pasan source/start/dur hace **2 pasadas**: primero mide el clip y
    luego aplica una corrección LINEAL (`linear=true`). Así el volumen es
    correcto DESDE EL PRIMER SEGUNDO — el modo de 1 pasada va "montándose"
    los primeros segundos y se oye raro / con bombeo al principio.
    Si la medición falla, cae a 1 pasada.
    """
    if not cfg.get_path("output.normalizar_audio", True):
        return ""
    i = cfg.get_path("output.loudness_lufs", -14)
    tp = cfg.get_path("output.true_peak_db", -1.5)
    lra = cfg.get_path("output.loudness_range", 11)
    base = f"loudnorm=I={i}:TP={tp}:LRA={lra}"

    if source is not None and start is not None and dur is not None:
        m = _measure_loudness(source, start, dur, i, tp, lra)
        if m:
            return (
                f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
                f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
                f":offset={m['target_offset']}:linear=true,aresample=48000"
            )
    return f"{base},aresample=48000"


def _measure_loudness(source: Path, start: float, dur: float,
                      i, tp, lra) -> dict | None:
    """Pasada de análisis de loudnorm sobre el tramo del clip. Devuelve los
    valores medidos (input_i/tp/lra/thresh, target_offset) o None si algo falla."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats",
             "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
             "-map", "0:a:0?", "-af",
             f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, ValueError):
        return None
    blob = proc.stderr or ""
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", blob)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    try:
        if not -60.0 < float(data["input_i"]) < 0.0:   # silencio / medida rara
            return None
    except (KeyError, ValueError):
        return None
    need = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    return {k: data[k] for k in need} if all(k in data for k in need) else None


def _reframe_chain(cfg, meta: dict, srcs: list[str]) -> str:
    """Cadena de filtros para reencuadrar 9:16. `srcs` son las etiquetas de
    entrada (una por cada vez que el layout usa el vídeo). Devuelve la cadena
    que termina en [rf] (vídeo ya reencuadrado, sin subtítulos)."""
    W = int(cfg.get_path("output.width", 1080))
    H = int(cfg.get_path("output.height", 1920))
    src_w, src_h = int(meta["width"]), int(meta["height"])
    layout = cfg.get_path("reframe.layout", "blur")
    focus = max(0.0, min(1.0, float(cfg.get_path("reframe.focus_x", 0.5))))
    a, b, c = (srcs + srcs + srcs)[:3]     # hasta 3 usos

    # OJO: nada de min()/max() de ffmpeg dentro de crop — la coma rompe el parser.
    if layout == "ninguno":
        # VIDEO HORIZONTAL: no se reencuadra nada. El directo ya viene 16:9 y
        # la salida es 16:9, asi que solo se ajusta al lienzo (con barras si la
        # proporcion no cuadrara). Es la cadena mas barata de todas: sin crop,
        # sin desenfoque, sin componer zonas.
        return (f"{a}scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,format=yuv420p[rf]")

    if layout == "center":
        zoom = max(0.6, min(1.0, float(cfg.get_path("reframe.center_zoom", 1.0))))
        crop_w = _even(min(src_w, src_h * (W / H) / zoom))
        crop_x = _even((src_w - crop_w) * focus)
        return (f"{a}crop={crop_w}:{src_h}:{crop_x}:0,"
                f"scale={W}:{H},setsar=1,format=yuv420p[rf]")

    if layout == "facecam_top":
        fc = cfg.get_path("reframe.facecam", {}) or {}
        fw, fh = _even(fc.get("w", 380)), _even(fc.get("h", 280))
        fx = max(0, min(int(fc.get("x", 0)), src_w - fw))
        fy = max(0, min(int(fc.get("y", 0)), src_h - fh))

        ft = cfg.get_path("reframe.facecam_top", {}) or {}
        face_ratio = max(0.20, min(0.45, float(ft.get("face_ratio", 0.34))))
        subs_ratio = max(0.10, min(0.30, float(ft.get("subs_ratio", 0.16))))
        face_h = _even(H * face_ratio)
        subs_h = _even(H * subs_ratio)
        game_h = _even(H - face_h - subs_h)
        return (
            f"{a}crop={fw}:{fh}:{fx}:{fy},"
            f"scale={W}:{face_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{face_h},setsar=1,pad={W}:{H}:0:0:black[base];"
            f"{b}scale={W}:{game_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{game_h},boxblur=26:2,eq=brightness=-0.10,setsar=1[gbg];"
            f"{c}scale={W}:{game_h}:force_original_aspect_ratio=decrease,setsar=1[gfg];"
            f"[base][gbg]overlay=0:{face_h}[m];"
            f"[m][gfg]overlay=(W-w)/2:{face_h}+({game_h}-h)/2,format=yuv420p[rf]"
        )

    if layout == "libre":
        # posición y tamaño LIBRES de cada zona (editor por-clip). Todo en px
        # del lienzo 1080x1920. Fondo = gameplay a pantalla completa y borroso.
        lb = cfg.get_path("reframe.libre", {}) or {}
        fc = cfg.get_path("reframe.facecam", {}) or {}
        fw, fh = _even(fc.get("w", 380)), _even(fc.get("h", 280))
        fx = max(0, min(int(fc.get("x", 0)), src_w - fw))
        fy = max(0, min(int(fc.get("y", 0)), src_h - fh))
        gx, gy, gw, gh = _box(lb.get("game", {}), W, H, 0, int(H * 0.34),
                              W, int(H * 0.50))
        cx, cy, cw, ch = _box(lb.get("cara", {}), W, H, 0, 0, W, int(H * 0.34))
        return (
            f"{a}scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=28:2,eq=brightness=-0.12,setsar=1[bg];"
            f"{b}scale={gw}:{gh}:force_original_aspect_ratio=increase,"
            f"crop={gw}:{gh},setsar=1[game];"
            f"{c}crop={fw}:{fh}:{fx}:{fy},"
            f"scale={cw}:{ch}:force_original_aspect_ratio=increase,"
            f"crop={cw}:{ch},setsar=1[face];"
            f"[bg][game]overlay={gx}:{gy}[m1];"
            f"[m1][face]overlay={cx}:{cy},format=yuv420p[rf]"
        )

    # blur — el más seguro, no pierde nada del encuadre original
    return (
        f"{a}scale={W}:-2,setsar=1[fg];"
        f"{b}scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=30:3,eq=brightness=-0.12,setsar=1[bg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[rf]"
    )


def build_filter(cfg, meta: dict, ass_path: Path | None,
                 src: str = "[0:v]", fontsdir: Path | None = None) -> str:
    """Filtro completo: reencuadre + subtítulos, terminando en [v].
    `src` es la etiqueta de entrada de vídeo. Si es una etiqueta de filtro
    (no el pad [0:v]) hay que hacer split para poder consumirla varias veces.
    `fontsdir`: la carpeta de la letra del .ass (las palabras clave de Vídeos PRO usan la de la marca, en fonts/)."""
    layout = cfg.get_path("reframe.layout", "blur")
    n_uses = {"ninguno": 1, "center": 1, "facecam_top": 3, "libre": 3}.get(layout, 2)

    if src == "[0:v]":
        srcs = ["[0:v]"] * n_uses          # el pad se auto-reparte
        pre = ""
    else:
        srcs = [f"[__m{i}]" for i in range(n_uses)]
        pre = f"{src}split={n_uses}" + "".join(srcs) + ";"

    chain = pre + _reframe_chain(cfg, meta, srcs)   # ...[rf]
    if ass_path is not None:
        fd = f":fontsdir='{_escape_filter_path(fontsdir)}'" if fontsdir else ""
        chain += f";[rf]ass='{_escape_filter_path(ass_path)}'{fd}[v]"
    else:
        chain += ";[rf]null[v]"
    return chain


def _whoosh_path(cfg) -> Path | None:
    """Ruta al 'whoosh' de transición. Si `transiciones.archivo` apunta a un wav
    tuyo, usa ese. Si no, SINTETIZA uno suave (~0.34 s) y lo cachea."""
    custom = str(cfg.get_path("transiciones.archivo", "") or "").strip()
    if custom:
        p = Path(custom)
        if not p.is_absolute():
            p = ROOT / p        # relativa a ESTA carpeta, no a desde dónde se lance el programa
        if p.is_file():
            return p
        log(f"AVISO: no encuentro el whoosh «{custom}»; uso el sintetizado.")
    p = ROOT / "work" / "_whoosh_v3.wav"
    if p.is_file() and p.stat().st_size > 1000:
        return p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # "swoosh" suave: ruido MARRÓN (oscuro, sin sibilancia), banda 250-5000,
        # envolvente en campana (qsin) para que entre y salga sin "click".
        # Nada de tono/barrido -> no suena artificial.
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anoisesrc=d=0.34:c=brown:a=1:r=48000",
            "-af", ("highpass=f=250,lowpass=f=5000,"
                    "afade=t=in:curve=qsin:d=0.14,"
                    "afade=t=out:st=0.13:curve=qsin:d=0.21,"
                    "volume=1.0"),
            "-ac", "2", "-ar", "48000", str(p),
        ])
        return p if p.is_file() else None
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo generar el whoosh de transición ({exc}).")
        return None


def _ethereal_path(cfg) -> Path | None:
    """Sonido SUAVE para tarjetas y para un recorte que NO cambia de cámara
    (solo se quitó una pausa o una toma repetida) — el whoosh se reserva
    para cuando de verdad cambia la escena. Dos tonos afinados (quinta
    justa) con ataques ligeramente desfasados y un toque de eco para que
    no suene ni sintético ni repetitivo, sintetizado y cacheado (mismo
    espíritu que `_whoosh_path`)."""
    p = ROOT / "work" / "_etereo_v1.wav"
    if p.is_file() and p.stat().st_size > 1000:
        return p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=0.55",
            "-f", "lavfi", "-i", "sine=frequency=1318.5:duration=0.55",
            "-filter_complex",
            ("[0:a]afade=t=in:curve=qsin:d=0.10,"
             "afade=t=out:st=0.24:curve=qsin:d=0.30[a];"
             "[1:a]afade=t=in:curve=qsin:d=0.15,"
             "afade=t=out:st=0.22:curve=qsin:d=0.32,volume=0.55[b];"
             "[a][b]amix=inputs=2:normalize=0,"
             "aecho=0.6:0.5:80:0.35,alimiter=limit=0.9"),
            "-ac", "2", "-ar", "48000", str(p),
        ])
        return p if p.is_file() else None
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo generar el sonido etéreo de transición ({exc}).")
        return None


_NOMBRE_SONIDO = {"whoosh": "whoosh (cambio de escena)", "etereo": "etéreo (tarjetas y cortes)",
                  "pop": "pop (suscríbete/like)"}


def _pop_path(cfg) -> Path | None:
    """El «pop» de las alertas de SUSCRÍBETE y LIKE (el autor, 2026-09-26: «el sonido que acompaña las alertas de suscribirse
    y like, que sea distinto al de las tarjetas, un pop»): un tono que sube rápido de 380 Hz a ~1,2 kHz en 75 ms (una
    burbuja que revienta), ataque instantáneo, caída corta y un eco muy leve para que no quede seco al lado del etéreo de
    las tarjetas. Sintetizado y cacheado, como el whoosh y el etéreo (`transiciones.pop_archivo` = uno tuyo)."""
    custom = str(cfg.get_path("transiciones.pop_archivo", "") or "").strip()
    if custom:
        p = Path(custom) if Path(custom).is_absolute() else ROOT / custom
        if p.is_file():
            return p
        log(f"AVISO: no encuentro el pop «{custom}»; uso el sintetizado.")
    p = ROOT / "work" / "_pop_v1.wav"
    if p.is_file() and p.stat().st_size > 1000:
        return p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fase = "380*(exp(15*min(t\\,0.075))-1)/15+380*exp(15*0.075)*max(t-0.075\\,0)"     # barrido 380 Hz -> ~1,2 kHz
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"aevalsrc='sin(2*PI*({fase}))*(1-exp(-t*600))*exp(-t*24)':s=48000:d=0.32",
            "-af", ("highpass=f=160,lowpass=f=6000,aecho=0.5:0.35:55:0.22,"
                    "afade=t=out:st=0.22:d=0.10,alimiter=limit=0.9"),
            "-ac", "2", "-ar", "48000", str(p),
        ])
        return p if p.is_file() else None
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo generar el «pop» de suscríbete/like ({exc}).")
        return None


def _peak_offset_s(path: Path) -> float:
    """Segundo, dentro del propio clip de transición, donde cae su AMPLITUD
    MÁXIMA — para retrasar el sonido lo justo y que ese golpe caiga EXACTO
    en el instante del corte, no el arranque del clip (que puede traer un
    pelín de ataque/aire antes del pico real). Medido de verdad sobre el
    audio (soporta el whoosh sintetizado y también uno tuyo en
    `transiciones.archivo`, que puede venir en otro formato) — si no se
    puede leer, 0.0 (mismo comportamiento que antes: el clip empieza justo
    en el corte)."""
    try:
        import array
        import wave
        with wave.open(str(path), "rb") as w:
            n_frames = w.getnframes()
            rate = w.getframerate()
            sampwidth = w.getsampwidth()
            nchan = w.getnchannels()
            raw = w.readframes(n_frames)
        if sampwidth != 2 or not raw:
            return 0.0
        samples = array.array("h")
        samples.frombytes(raw)
        canal = samples[::nchan] if nchan > 1 else samples
        if not canal:
            return 0.0
        peak_i = max(range(len(canal)), key=lambda i: abs(canal[i]))
        return peak_i / float(rate)
    except Exception:  # noqa: BLE001
        return 0.0


def _mean_dbfs(cmd_tail: list[str]) -> float | None:
    """Corre `ffmpeg <cmd_tail> -af volumedetect -f null -` y devuelve el
    mean_volume (RMS en dBFS). None si falla."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", *cmd_tail,
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", r.stderr)
        return float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _speech_dbfs(source: Path, segments, words, cfg) -> float | None:
    """Nivel RMS (dBFS) de la VOZ del streamer dentro de este clip: mide el
    audio SOLO en los tramos donde hay palabras. Así el whoosh se puede poner
    N dB por debajo de tu voz, no del pico de disparos."""
    if not words:
        return None
    segs = [(float(a), float(b)) for a, b in segments]
    wins: list[tuple[float, float]] = []
    for w in words:
        t0 = w.get("start", w.get("t"))
        t1 = w.get("end", (t0 + 0.3) if t0 is not None else None)
        if t0 is None or t1 is None:
            continue
        t0, t1 = float(t0), float(t1)
        if any(a <= t0 < b for a, b in segs):        # la palabra cae en el clip
            wins.append((max(0.0, t0 - 0.05), t1 + 0.05))
    if not wins:
        return None
    # fusiona y limita a ~25 s de voz (suficiente para medir, rápido)
    wins.sort()
    merged: list[list] = []
    for a, b in wins:
        if merged and a <= merged[-1][1] + 0.15:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    tail, tot = [], 0.0
    for a, b in merged:
        d = min(b - a, 3.0)
        tail += ["-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", str(source)]
        tot += d
        if tot >= 25.0:
            break
    n = len(tail) // 6
    if n < 1:
        return None
    fc = ("".join(f"[{i}:a]" for i in range(n))
          + f"concat=n={n}:v=0:a=1,volumedetect")
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", *tail,
             "-filter_complex", fc, "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        mm = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", r.stderr)
        return float(mm.group(1)) if mm else None
    except Exception:  # noqa: BLE001
        return None


def render_merged(source: Path, segments: list[tuple[float, float]], cfg,
                  meta: dict, ass_path: Path | None, out_file: Path,
                  words: list | None = None, progress_cb=None,
                  fade_in: float = 0.0, fade_out: float = 0.0,
                  tarjetas_overlays: list[dict] | None = None,
                  modos: list[str] | None = None,
                  camara_rect: dict | None = None,
                  pantalla_zoom_rect: dict | None = None,
                  pantalla_zoom_centros: list[tuple[float, float] | None] | None = None,
                  pantalla_zoom_caminos: list[dict | None] | None = None,
                  ken_burns: list[int] | None = None,
                  explicativa_cards: list[Path | None] | None = None,
                  cortes_silenciosos: list[tuple[float, float]] | None = None,
                  musica_path: Path | None = None,
                  fontsdir: Path | None = None,
                  camara_aparte: dict | None = None) -> Path:
    """Clip combinado: junta varios trozos del directo (con seek rápido) y les
    aplica el mismo reencuadre 9:16 + subtítulos, todo en UN pase. El audio de
    cada trozo se concatena con el vídeo.

    `modos`/`camara_rect`/`pantalla_zoom_rect` — Vídeo PRO, zoom cara/pantalla:
    si vienen, cada `segments[i]` con `modos[i] == "cara"` se recorta a
    `camara_rect` y con `modos[i] == "pantalla_zoom"` se recorta a
    `pantalla_zoom_rect` (los dos: fracción 0-1 del lienzo x/y/w/h), cada uno
    escalado para llenar el cuadro; `modos[i] == "explicativa"` recorta la
    cara igual que "cara" pero solo llena el TERCIO derecho del lienzo, y
    `explicativa_cards[i]` (el PNG de `tarjetas.dibuja_explicativa`, de
    `2/3 del ancho x alto`) se pega encima en el 0,0 DENTRO del propio tramo
    (antes de unir los tramos), no como un overlay con fundido sobre el
    vídeo ya unido: así entra y sale en el fotograma EXACTO del corte de
    cámara, sin pasar por negro (el hueco de la cara es negro por debajo) ni
    desfasarse un par de fotogramas del tramo; el resto pasa igual que
    "ninguno". Nada de esto se activa si `modos` es None — Shorts y los demás
    layouts no lo pasan nunca.

    `pantalla_zoom_centros` (opcional, paralelo a `segments`): por tramo, dónde
    (x,y fracción 0-1) detectó actividad real `clipper/movimiento.py` — si viene,
    ESE tramo de "pantalla_zoom" se recentra ahí (ver `centro_pantalla_zoom`) en
    vez de usar siempre el mismo `pantalla_zoom_rect` fijo. `None` en un tramo
    (o toda la lista) usa el rectángulo fijo de siempre para ese tramo.

    `ken_burns` (opcional, paralelo a `segments`): por tramo, 0 (sin efecto) o
    +1/-1 (pan/zoom sutil, ver `_ken_burns_fx`, hacia la derecha/izquierda) —
    lo decide `pipeline._ken_burns_por_segmento` (código, nunca Gemini). Solo
    se aplica en "cara"/"pantalla"/"pantalla_zoom".

    `cortes_silenciosos` (opcional): tramos del ORIGINAL [(inicio, fin)] cuyo corte NO suena (ni tono etéreo):
    los que pone `clipper/voz.py` para acortar una pausa al arrancar — un tono entre «Una» y «de las cosas que
    odio», al segundo de empezar el vídeo, sonaría a fallo. El micro-fundido de audio del empalme sí se queda."""
    n = len(segments)
    inputs: list[str] = []
    for s, e in segments:
        d = max(0.10, e - s)
        inputs += ["-ss", f"{s:.3f}", "-t", f"{d:.3f}", "-i", str(source)]

    # TRANSICION VISUAL entre trozos. Antes no habia NINGUNA: los trozos se
    # pegaban a secas con concat. Se hace con un fundido corto al final de cada
    # trozo y otro al principio del siguiente (destello blanco por defecto).
    # No se usa xfade a proposito: xfade solapa los trozos y ACORTA el clip,
    # lo que desincroniza los subtitulos un poco mas en cada corte.
    vis = str(cfg.get_path("transiciones.visual", "flash")).lower()
    vseg = max(0.04, float(cfg.get_path("transiciones.visual_seg", 0.12)))
    vcolor = "white" if vis == "flash" else "black"
    usar_vis = vis in ("flash", "negro") and n >= 2

    # Con `modos`, `segments` ya viene partido también en cada cambio de
    # cámara (no solo en cada recorte) — un destello igual de fuerte en ESE
    # borde se ve mal: es un simple cambio de encuadre, no un salto en el
    # tiempo. Un borde es "puro cambio de cámara" cuando el tramo siguiente
    # empieza justo donde acaba éste (nada se cortó entre medias).
    def _es_corte_de_verdad(i: int) -> bool:
        if not modos:
            return True
        return abs(segments[i][1] - segments[i + 1][0]) > 0.02

    W_out = int(cfg.get_path("output.width", 1920))
    H_out = int(cfg.get_path("output.height", 1080))
    src_w, src_h = int(meta["width"]), int(meta["height"])

    # FPS de salida: los del config (60), pero NUNCA más de los que trae la
    # grabación — subir una grabación de 30 fps a 60 solo duplicaría frames
    # (más peso y más tiempo de render para el mismo movimiento).
    fps_cfg = int(cfg.get_path("output.fps", 30))
    try:
        fps_src = int(round(float(meta.get("fps") or 0)))
    except (TypeError, ValueError):
        fps_src = 0
    fps_out = min(fps_cfg, fps_src) if fps_src > 0 else fps_cfg

    # Tarjetas de «explicativa»: una entrada MÁS de ffmpeg por tramo (el PNG en bucle, del tamaño del tramo),
    # justo detrás de las de los tramos (índices n, n+1…) y ANTES de las de audio/tarjetas de más abajo.
    # Solo si de verdad se recorta la cara al tercio (hace falta `camara_rect`): sin él ese tramo cae al plano
    # completo y una tarjeta encima taparía la pantalla.
    card_idx: dict[int, int] = {}
    if explicativa_cards and modos and camara_rect:
        for i, png in enumerate(explicativa_cards[:n]):
            if png and i < len(modos) and modos[i] == "explicativa" and Path(png).is_file():
                d_c = max(0.10, segments[i][1] - segments[i][0])
                inputs += ["-framerate", str(fps_out), "-loop", "1", "-t", f"{d_c:.3f}", "-i", str(png)]
                card_idx[i] = n + len(card_idx)

    # 📷 CÁMARA APARTE (clipper/camara_aparte.py, el autor 2026-09-28): en los tramos «cara» y «explicativa» la imagen sale
    # del archivo de la cámara que graba OBS aparte (a su resolución, sincronizado: `desfase`) en vez de ampliar ~3× el
    # recuadro de la grabación; el SONIDO sigue siendo el de la grabación. Una entrada más por tramo, detrás de las
    # tarjetas de «explicativa». Un tramo que se sale del archivo por poco (≤ `_CAM_HOLGURA`: el primero o el último)
    # la usa igual, con su primer/último fotograma quieto ese trocito (`cam_pad`, y recortado a la duración EXACTA del
    # tramo para que la imagen no se adelante al sonido); uno que se sale más sale como siempre, del recuadro.
    cam_idx: dict[int, int] = {}
    cam_pad: dict[int, str] = {}
    # «marco» (el autor, 28-sep noche): en los tramos «cara», el fondo naranja de la marca con el contorno y la sombra del
    # recuadro (`camara_aparte.dibuja_marco`, un PNG) y tu cámara pegada dentro — una entrada más por tramo, como las
    # tarjetas de «explicativa». Sin ese PNG, «cara» sale a pantalla completa («pantalla»).
    marco_idx: dict[int, int] = {}
    n_extra = 0                          # entradas añadidas aquí (cámara y fondos de «marco»), detrás de las tarjetas
    if camara_aparte and modos and camara_rect:
        off = float(camara_aparte.get("desfase") or 0.0)
        dur_cam = float(camara_aparte.get("duracion") or 0.0)
        marco_png = camara_aparte.get("marco_png") if camara_aparte.get("cara") == "marco" else None
        if marco_png and not (camara_aparte.get("marco_caja") and Path(marco_png).is_file()):
            marco_png = None
        n_caras = 0
        for i, (s_i, e_i) in enumerate(segments):
            if i >= len(modos) or modos[i] not in ("cara", "explicativa"):
                continue
            n_caras += 1
            d_i = max(0.10, e_i - s_i)
            ini = s_i + off
            falta_ini = max(0.0, -ini)
            falta_fin = max(0.0, ini + d_i - dur_cam) if dur_cam else 0.0
            if falta_ini > _CAM_HOLGURA or falta_fin > _CAM_HOLGURA or d_i - falta_ini - falta_fin < 0.10:
                continue
            inputs += ["-ss", f"{ini + falta_ini:.3f}", "-t", f"{d_i - falta_ini - falta_fin:.3f}",
                       "-i", str(camara_aparte["ruta"])]
            cam_idx[i] = n + len(card_idx) + n_extra
            n_extra += 1
            if marco_png and modos[i] == "cara":
                # (un pelín más largo que el tramo: lo corta la cámara, que es la base)
                inputs += ["-framerate", str(fps_out), "-loop", "1", "-t", f"{d_i + 0.5:.3f}", "-i", str(marco_png)]
                marco_idx[i] = n + len(card_idx) + n_extra
                n_extra += 1
            if falta_ini > 0.0005 or falta_fin > 0.0005:
                cam_pad[i] = ("tpad=" + (f"start_mode=clone:start_duration={falta_ini:.3f}:" if falta_ini > 0.0005 else "")
                              + f"stop_mode=clone:stop_duration={falta_fin + 0.5:.3f},trim=duration={d_i:.3f},")
        if n_caras:
            fuera = n_caras - len(cam_idx)
            log(f"  cámara aparte: {len(cam_idx)} de {n_caras} tramo(s) «cara»/«explicativa»"
                + (f"; {len(marco_idx)} «cara» en un recuadro sobre tu naranja («marco»)" if marco_idx else "")
                + (f" ({len(cam_pad)} con su primer o último fotograma quieto unas décimas: el archivo de la cámara "
                   f"empieza o acaba un poco antes que la grabación)" if cam_pad else "")
                + (f"; {fuera} salen del recuadro ampliado, como siempre: no están en el archivo de la cámara"
                   if fuera else "") + ".")

    # Recorte para "cara": no el rectángulo medido tal cual (hay que
    # ampliarlo ~3.5x para llenar el lienzo y se pixela) — uno más grande,
    # mismo centro, con `zoom_cara` como margen. Se calcula UNA vez, fuera
    # del bucle: es el mismo recorte para todos los tramos en modo cara.
    cx = cy = cw = ch = 0
    if camara_rect:
        zoom = max(1.0, float(camara_rect.get("zoom_cara", 1.0)))
        w_frac = min(1.0, float(camara_rect["w"]) * zoom)
        h_frac = min(1.0, float(camara_rect["h"]) * zoom)
        cx_c = float(camara_rect["x"]) + float(camara_rect["w"]) / 2
        cy_c = float(camara_rect["y"]) + float(camara_rect["h"]) / 2
        x_frac = min(max(0.0, cx_c - w_frac / 2), 1.0 - w_frac)
        y_frac = min(max(0.0, cy_c - h_frac / 2), 1.0 - h_frac)
        cx, cy = _even(x_frac * src_w), _even(y_frac * src_h)
        cw, ch = _even(w_frac * src_w), _even(h_frac * src_h)

    # Modo "explicativa": SOLO llena el tercio DERECHO del lienzo — el resto lo cubre por completo la
    # tarjeta de término + definición (`tarjetas.dibuja_explicativa`, pegada dentro del propio tramo,
    # ver `explicativa_cards`). Mismo reparto 2/3 + 1/3 que usa pipeline.py para dibujar esa tarjeta
    # — si cambia aquí, tiene que cambiar allá también (ambos parten del mismo W_out/H_out, así que
    # el reparto siempre coincide). El recorte de la cara NO es el de "cara" (`zoom_cara`): ese
    # rectángulo se aplasta contra el borde del lienzo (la cámara está en la esquina) y, recortado a
    # la proporción VERTICAL del tercio, dejaba la cara pegada al borde y cortada (visto en una grabación
    # real) — ver `explicativa_rect`, centrado en la propia cámara.
    ancho_tercio = W_out - _even(W_out * 2 / 3)
    ex_cx = ex_cy = ex_cw = ex_ch = 0
    if camara_rect:
        fx, fy, fw, fh = explicativa_rect(camara_rect, ancho_tercio / H_out, src_w, src_h)
        ex_cx, ex_cy, ex_cw, ex_ch = _even(fx * src_w), _even(fy * src_h), _even(fw * src_w), _even(fh * src_h)

    # Recorte para "pantalla_zoom": el rectángulo YA es la ventana que se
    # quiere ver (no un punto pequeño que haya que agrandar como la cara) —
    # se usa tal cual, solo escalado para llenar el lienzo. Por tramo (no una
    # vez para todo el vídeo): con `pantalla_zoom_centros` cada tramo puede
    # recentrarse donde de verdad hubo actividad (ver `centro_pantalla_zoom`).
    def _pz_pixel_crop(rect: dict) -> tuple[int, int, int, int]:
        pw = min(1.0, max(0.05, float(rect["w"])))
        ph = min(1.0, max(0.05, float(rect["h"])))
        px = min(max(0.0, float(rect["x"])), 1.0 - pw)
        py = min(max(0.0, float(rect["y"])), 1.0 - ph)
        return _even(px * src_w), _even(py * src_h), _even(pw * src_w), _even(ph * src_h)

    # MICRO-FUNDIDO de audio en cada empalme de un corte de verdad (solo Vídeos
    # PRO, o sea con `modos`). Al pegar dos puntos cualesquiera de la onda queda un
    # escalón que suena a "clic": medido sobre la voz del autor, un corte en plena voz
    # lo deja audible (>-26 dBFS) en ~8 de cada 10 empalmes; en una pausa, en ninguno.
    # Un fundido de ~10 ms a cada lado lo quita sin acortar nada (un crossfade sí
    # solaparía los trozos y desplazaría el audio respecto al vídeo).
    micro = 0.0
    if modos is not None and n >= 2:
        micro = max(0.0, float(cfg.get_path("transiciones.micro_fade_audio_ms", 10))) / 1000.0

    # Ken Burns por ESCENA, no por trozo: una escena con un corte en medio son varios trozos seguidos
    # del mismo modo con el mismo Ken Burns — el zoom tiene que continuar donde iba al pasar de un
    # trozo al siguiente (`desde`/`total`), no reiniciarse en cada uno.
    kb_desde, kb_total = _ken_burns_escenas([max(0.10, e - s) for s, e in segments], modos or [], ken_burns or [])

    norm = ""
    for i, (s_i, e_i) in enumerate(segments):
        d_i = max(0.10, e_i - s_i)
        afx = ""
        if micro > 0:
            mf = min(micro, d_i / 3)
            if i > 0 and _es_corte_de_verdad(i - 1):
                afx += f",afade=t=in:st=0:d={mf:.4f}:curve=qsin"
            if i < n - 1 and _es_corte_de_verdad(i):
                afx += f",afade=t=out:st={max(0.0, d_i - mf):.4f}:d={mf:.4f}:curve=qsin"
        fx = []
        if usar_vis:
            vseg_i = vseg if (i == 0 or _es_corte_de_verdad(i - 1)) else vseg * 0.35
            vseg_o = vseg if (i == n - 1 or _es_corte_de_verdad(i)) else vseg * 0.35
            if i > 0:
                fx.append(f"fade=t=in:st=0:d={vseg_i:.3f}:color={vcolor}")
            if i < n - 1:
                fo_d = vseg_o * 0.6
                fx.append(f"fade=t=out:st={max(0.0, d_i - fo_d):.3f}:d={fo_d:.3f}:color={vcolor}")
        extra = ("," + ",".join(fx)) if fx else ""

        cam_fx = ""
        if modos is not None:
            modo_i = modos[i] if i < len(modos) else None
            if modo_i == "cara" and camara_rect and i in marco_idx:
                # «marco»: el cuadrado de la cámara al tamaño del recuadro (su fondo va ENCIMA, con un hueco: abajo)
                from . import camara_aparte as _cam_ap
                ax, ay, aw, ah = _cam_ap.recorte_marco(camara_aparte)
                bw_m, bh_m = camara_aparte["marco_caja"][2:4]
                cam_fx = (f"crop={aw}:{ah}:{ax}:{ay}," + ("hflip," if camara_aparte.get("espejo") else "")
                          + f"scale={bw_m}:{bh_m}:flags=lanczos,")
            elif modo_i == "cara" and camara_rect and i in cam_idx:
                from . import camara_aparte as _cam_ap
                ax, ay, aw, ah = _cam_ap.recorte_cara(camara_aparte, W_out, H_out)
                cam_fx = (f"crop={aw}:{ah}:{ax}:{ay}," + ("hflip," if camara_aparte.get("espejo") else "")
                          + f"scale={W_out}:{H_out}:flags=lanczos,")
            elif modo_i == "cara" and camara_rect:
                cam_fx = (f"crop={cw}:{ch}:{cx}:{cy},"
                         f"scale={W_out}:{H_out}:force_original_aspect_ratio=increase,"
                         f"crop={W_out}:{H_out},")
            elif (modo_i == "pantalla_zoom" and pantalla_zoom_caminos and i < len(pantalla_zoom_caminos)
                  and pantalla_zoom_caminos[i]):
                # el encuadre que enseña todo lo que hace el ratón (pipeline.encuadres_zoom); sin él, el de abajo
                cam_fx = zoom_movil_fx(pantalla_zoom_caminos[i], src_w, src_h, W_out, H_out, fps_out)
            elif modo_i == "pantalla_zoom" and pantalla_zoom_rect:
                centro_i = pantalla_zoom_centros[i] if pantalla_zoom_centros and i < len(pantalla_zoom_centros) else None
                pzx, pzy, pzw, pzh = _pz_pixel_crop(centro_pantalla_zoom(pantalla_zoom_rect, centro_i, camara_rect))
                cam_fx = (f"crop={pzw}:{pzh}:{pzx}:{pzy},"
                         f"scale={W_out}:{H_out}:force_original_aspect_ratio=increase,"
                         f"crop={W_out}:{H_out},")
            elif modo_i == "explicativa" and camara_rect and i in cam_idx:
                from . import camara_aparte as _cam_ap
                ax, ay, aw, ah = _cam_ap.recorte_explicativa(camara_aparte, ancho_tercio, H_out)
                cam_fx = (f"crop={aw}:{ah}:{ax}:{ay}," + ("hflip," if camara_aparte.get("espejo") else "")
                          + f"scale={ancho_tercio}:{H_out}:flags=lanczos,"
                          f"pad={W_out}:{H_out}:{W_out - ancho_tercio}:0:black,")
            elif modo_i == "explicativa" and camara_rect:
                cam_fx = (f"crop={ex_cw}:{ex_ch}:{ex_cx}:{ex_cy},"
                         f"scale={ancho_tercio}:{H_out}:force_original_aspect_ratio=increase,"
                         f"crop={ancho_tercio}:{H_out},"
                         f"pad={W_out}:{H_out}:{W_out - ancho_tercio}:0:black,")
            else:
                cam_fx = (f"scale={W_out}:{H_out}:force_original_aspect_ratio=decrease,"
                         f"pad={W_out}:{H_out}:(ow-iw)/2:(oh-ih)/2:black,")
            # Ken Burns: pan/zoom sutil, decidido por pipeline._ken_burns_por_segmento (código, no
            # Gemini) — encima de LO QUE SEA que ya haya elegido cam_fx para este modo, nunca en
            # "explicativa" (competiría con la tarjeta de texto) ni en "ambos".
            kb_i = ken_burns[i] if ken_burns and i < len(ken_burns) else 0
            se_mueve = bool(modo_i == "pantalla_zoom" and pantalla_zoom_caminos and i < len(pantalla_zoom_caminos)
                            and camino_se_mueve(pantalla_zoom_caminos[i]))
            if kb_i and modo_i in ("cara", "pantalla", "pantalla_zoom") and not se_mueve:   # (un encuadre que ya se mueve, no)
                cam_fx += _ken_burns_fx(d_i, kb_i, fps_out, cfg, kb_desde.get(i, 0.0), kb_total.get(i))

        vin = f"[{cam_idx[i]}:v]{cam_pad.get(i, '')}" if i in cam_idx else f"[{i}:v]"   # 📷 la cámara aparte, si la hay
        if i in card_idx:
            # el tramo «explicativa»: la cara al tercio derecho + su tarjeta pegada encima EN el 0,0, todo dentro
            # del tramo (los fundidos de transición, si los hay, van sobre el conjunto). `shortest=1`: el PNG en
            # bucle dura lo mismo que el tramo, así que acaban en el mismo fotograma.
            video_i = (f"{vin}{cam_fx}setsar=1,format=yuv420p,setpts=PTS-STARTPTS[bs{i}];"
                       f"[{card_idx[i]}:v]format=yuv420p,setsar=1,setpts=PTS-STARTPTS[cd{i}];"
                       f"[bs{i}][cd{i}]overlay=0:0:shortest=1{extra}[cv{i}];")
        elif i in marco_idx:
            # «marco»: la CÁMARA es la base (con su movimiento suave, si lo lleva), llevada a su sitio con `pad`, y el
            # fondo naranja va ENCIMA con su hueco transparente. Al revés (la cámara pegada sobre el PNG en bucle),
            # `overlay` elegía cada fotograma de la cámara por marca de tiempo y las del .mkv van al milisegundo:
            # salían fotogramas repetidos y saltados. Así su ritmo queda tal cual (como en «pantalla»); el PNG es una
            # imagen fija, un pelín más larga que el tramo (`shortest=1`: manda la cámara). Los fundidos, sobre el conjunto.
            bx_m, by_m = camara_aparte["marco_caja"][:2]
            video_i = (f"{vin}{cam_fx}pad={W_out}:{H_out}:{bx_m}:{by_m}:black,"
                       f"setsar=1,format=yuv420p,setpts=PTS-STARTPTS[fm{i}];"
                       f"[{marco_idx[i]}:v]format=rgba,setpts=PTS-STARTPTS[mb{i}];"
                       f"[fm{i}][mb{i}]overlay=0:0:shortest=1{extra}[cv{i}];")
        else:
            video_i = f"{vin}{cam_fx}setsar=1,format=yuv420p,setpts=PTS-STARTPTS{extra}[cv{i}];"
        norm += (video_i
                 + f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                   f"asetpts=PTS-STARTPTS{afx}[ca{i}];")
    concat_in = "".join(f"[cv{i}][ca{i}]" for i in range(n))
    # (en Vídeos PRO, `ass_path` = las PALABRAS CLAVE de clipper/ritmo.py: van debajo de las tarjetas, que se pegan después)
    reframe = build_filter(cfg, meta, ass_path, src="[gv]", fontsdir=fontsdir)   # consume [gv] -> [v]

    # El sonido de transición se mezcla MÁS ABAJO, después del fade y del
    # filtro de audio — un único sistema para cortes, cambios de cámara y
    # tarjetas (ver el bloque "SONIDO de transición" tras armar `[ga]`).
    tr_fc, a_src = "", "[gac]"

    # `fps` justo después del concat: los frames que salen de un .mkv traen
    # timestamps en milisegundos (16.667 ms -> 17, 33.333 -> 33...) y `overlay`
    # los compara con los de las tarjetas (múltiplos exactos de 1/fps): cada
    # 3er frame la tarjeta llegaba "tarde" y repetía el frame anterior, así
    # que su fundido avanzaba a tirones irregulares. Con `fps` los frames
    # caen en la rejilla exacta 1/fps y coinciden con los de las tarjetas.
    fc = (f"{norm}{concat_in}concat=n={n}:v=1:a=1[gv0][gac];"
          f"[gv0]fps={fps_out}[gv];{tr_fc}{reframe}")
    af = _censor_prefix(words or [], segments, cfg) + _audio_filter(cfg)

    # ENTRADA/SALIDA suaves. Sin esto el vídeo entra de golpe y a todo volumen,
    # que en horizontal (donde el espectador YA le dio a reproducir) suena a
    # bofetada. El vídeo también funde desde/hacia negro, para que el corte con
    # las cartelas de intro y outro no se note.
    dur_total = sum(max(0.1, e - s) for s, e in segments)
    fi = max(0.0, float(fade_in or 0))
    fo = max(0.0, float(fade_out or 0))
    if fi > 0:
        af = (af + "," if af else "") + f"afade=t=in:st=0:d={fi:.2f}"
    if fo > 0 and dur_total > fo + 1:
        af = (af + "," if af else "") + f"afade=t=out:st={dur_total - fo:.2f}:d={fo:.2f}"
    fc += f";{a_src}{af}[ga]" if af else f";{a_src}anull[ga]"

    # SONIDO de transición: UN whoosh cuando la cámara cambia de encuadre DE
    # VERDAD (escena nueva) y un sonido más SUAVE ("etéreo") cuando se quitó
    # una pausa/toma repetida sin cambiar de cámara, y en cada tarjeta.
    # Antes un corte de silencio sonaba exactamente igual que un cambio real
    # de escena (el autor lo notó: "hay un sonido pero no hay ningún cambio") —
    # ahora solo el cambio de cámara de verdad suena a whoosh; lo demás usa
    # el sonido etéreo, más discreto. Antes también había DOS sistemas que en
    # un cambio de cámara sonaban los dos A LA VEZ; ahora es uno solo.
    eventos: list[tuple[float, str]] = []      # (segundo, "whoosh" | "etereo" | "pop")
    acumulado = 0.0

    def _silencioso(a: float, b: float) -> bool:
        return any(x - 0.02 <= a and b <= y + 0.02 for x, y in cortes_silenciosos or [])

    for i in range(n - 1):
        acumulado += max(0.10, segments[i][1] - segments[i][0])
        cambia_modo = bool(modos) and i + 1 < len(modos) and modos[i] != modos[i + 1]
        if cambia_modo:
            eventos.append((acumulado, "whoosh"))
        elif _es_corte_de_verdad(i) and not _silencioso(segments[i][1], segments[i + 1][0]):
            eventos.append((acumulado, "etereo"))
        # si no cambia el modo NI es un corte de verdad, no hay nada que
        # anunciar (no debería pasar: los tramos contiguos con el mismo
        # modo ya llegan soldados en uno solo)
    if tarjetas_overlays:
        # cada tarjeta suena al entrar (etéreo); las alertas de suscríbete y like, con su «pop» (`sonido: "pop"`,
        # tarjetas.TIPOS_CON_POP); una ESCENA a pantalla completa es un cambio de escena: «whoosh» al entrar Y al
        # volver a tu vídeo (`sonido`/`sonido_fin`, clipper/escena_completa.py)
        for ov in tarjetas_overlays:
            if ov.get("sin_sonido"):
                continue
            eventos.append((float(ov["inicio"]), ov.get("sonido") or "etereo"))
            if ov.get("sonido_fin"):
                eventos.append((float(ov["fin"]), ov.get("sonido") or "etereo"))

    next_idx = n + len(card_idx) + n_extra   # (los índices n.. son las tarjetas de «explicativa», la cámara aparte y «marco»)
    if eventos and cfg.get_path("transiciones.sonido", True):
        whoosh = _whoosh_path(cfg)
        etereo = _ethereal_path(cfg)
        pop = _pop_path(cfg) if any(k == "pop" for _t, k in eventos) else None      # solo si hay suscríbete/like
        modo_vol = str(cfg.get_path("transiciones.modo", "corte")).lower()
        rel_db = float(cfg.get_path("transiciones.rel_voz_db", -8.0))
        vf_fb = float(cfg.get_path("transiciones.volumen_fallback", 0.3))
        pop_db = float(cfg.get_path("transiciones.pop_db", -3.0))
        voz = _speech_dbfs(source, segments, words or [], cfg) if modo_vol == "voz" else None
        if voz is not None:
            log(f"  transiciones: voz {voz:.1f} dBFS -> {voz + rel_db:.1f} dBFS.")

        def _ganancia(asset: Path, extra_db: float = 0.0, ref: Path | None = None) -> str:
            a_db = _mean_dbfs(["-t", "0.6", "-i", str(asset)])
            if voz is not None and a_db is not None:
                g = max(-40.0, min(6.0, voz + rel_db - a_db + extra_db))
                return f"volume={g:.1f}dB"
            if not extra_db and ref is None:
                return f"volume={vf_fb:.2f}"
            # sin tu voz medida: el nivel fijo de siempre; el «pop» antes se iguala al etéreo (a igual volumen fijo sonaría
            # ~9 dB más fuerte: es un golpe corto, el etéreo un tono suave)
            r_db = _mean_dbfs(["-t", "0.6", "-i", str(ref)]) if ref is not None and ref.is_file() else None
            ajuste = (r_db - a_db) if (r_db is not None and a_db is not None) else 0.0
            return f"volume={vf_fb * 10 ** ((ajuste + extra_db) / 20):.3f}"

        legs_fc, mix_labels, mezclados = "", [], []
        # el «pop» va `pop_db` por debajo: a igual volumen MEDIO que el etéreo, su golpe pica ~3,4 dB más (medido)
        for kind, asset, extra, ref in (("whoosh", whoosh, 0.0, None), ("etereo", etereo, 0.0, None),
                                        ("pop", pop, pop_db, etereo)):
            tiempos = [t for t, k in eventos if k == kind]
            if not asset or not asset.is_file() or not tiempos:
                continue
            mezclados.append(f"{len(tiempos)} {_NOMBRE_SONIDO[kind]}")
            inputs += ["-i", str(asset)]
            idx, next_idx = next_idx, next_idx + 1
            peak = _peak_offset_s(asset)          # dónde cae el golpe real del clip
            gan = _ganancia(asset, extra, ref)
            m = len(tiempos)
            legs_fc += (f"[{idx}:a]asplit={m}"
                       + "".join(f"[s{kind}{k}]" for k in range(m)) + ";")
            for k, t in enumerate(tiempos):
                offs = max(0.0, t - peak)         # retrasa lo justo para que el
                lbl = f"d{kind}{k}"                # PICO caiga exacto en `t`, no el inicio
                legs_fc += (f"[s{kind}{k}]atrim=0:0.6,asetpts=PTS-STARTPTS,{gan},"
                           f"adelay={int(offs * 1000)}|{int(offs * 1000)}[{lbl}];")
                mix_labels.append(f"[{lbl}]")
        if mix_labels:
            log(f"  sonidos: {', '.join(mezclados)}.")        # («Probar el sistema» comprueba aquí que suena el pop)
            fc = fc.replace("[ga]", "[ga_precue]", 1)
            fc += (f";{legs_fc}[ga_precue]" + "".join(mix_labels)
                  + f"amix=inputs={1 + len(mix_labels)}:normalize=0:dropout_transition=0,"
                    f"{_limite_pico(cfg)}[ga]")

    # MÚSICA DE FONDO: se mezcla aparte, DESPUÉS del render principal (ver
    # `_mezcla_musica`) — no aquí. Meterla en este mismo filter_complex
    # (que ya carga 9 tarjetas en bucle a duración completa + los cues)
    # reventaba ffmpeg con "Cannot allocate memory" en vídeos largos.

    # TARJETAS de Mi canal: PNGs ya dibujados por clipper/tarjetas.py, cada
    # uno pegado encima con `overlay` en su ventana [inicio,fin]. Nada de
    # esto se activa si `tarjetas_overlays` viene vacío/None (Shorts y el
    # generador por temática nunca lo pasan) — mismo criterio que en
    # webapp.py: lo nuevo no debe tocar lo que ya funciona si no se pide.
    if tarjetas_overlays:
        W = int(cfg.get_path("output.width", 1920))
        H = int(cfg.get_path("output.height", 1080))
        # las tarjetas entran/salen con un fundido de opacidad: el PNG en
        # bucle tiene que ir AL MISMO fps que el vídeo, si no (p. ej. a 30
        # sobre un vídeo de 60) el fundido avanza a saltos de 33 ms en vez de
        # de 16 ms y se nota menos fluido.
        fps = str(fps_out)
        mx, my_ = round(W * 0.05), round(H * 0.06)
        base_idx = next_idx
        fc = fc.replace("[v]", "[vprecards0]", 1)
        cur = "[vprecards0]"
        last = len(tarjetas_overlays) - 1
        eslabones = []
        for i, ov in enumerate(tarjetas_overlays):
            if ov.get("patron"):
                # ILUSTRACIÓN ANIMADA: una secuencia de PNG (f_0000.png, f_0001.png…) a los fps
                # EXACTOS del vídeo, con su entrada/salida ya dibujada dentro.
                inputs += ["-framerate", str(int(ov.get("fps") or fps_out)), "-start_number", "0",
                           "-i", str(ov["patron"])]
            else:
                inputs += ["-framerate", fps, "-loop", "1", "-t", f"{dur_total:.3f}", "-i", ov["path"]]
            w_ov, h_ov = int(ov["w"]), int(ov["h"])
            # "ancla": las tarjetas van abajo-izquierda (por defecto); las
            # imágenes ilustrativas (clipper/imagenes.py) van arriba-derecha
            # para que nunca se pisen si coinciden en el tiempo.
            if ov.get("ancla") == "completa":
                x, y = 0, 0                     # la escena a pantalla completa tapa el cuadro entero
            elif ov.get("ancla") == "top-right":
                x, y = max(0, W - mx - w_ov), my_
            else:
                x, y = mx, max(0, H - my_ - h_ov)
            x, y = max(0, x + int(ov.get("dx", 0))), max(0, y + int(ov.get("dy", 0)))
            if ov.get("patron"):
                # Sus PTS empiezan en `inicio` (múltiplo exacto de 1/fps): cada fotograma de la
                # ilustración cae en un fotograma del vídeo, sin repetidos ni saltos.
                eslabones.append(
                    f"[{base_idx + i}:v]format=rgba,"
                    f"setpts=PTS-STARTPTS+{ov['inicio']:.6f}/TB[imgf{i}]")
            else:
                # Entra/sale con un fundido de ALPHA (no de color: es un PNG
                # transparente, fundir a negro se vería como un parpadeo oscuro
                # detrás). Antes aparecía/desaparecía de golpe.
                fdur = max(0.12, min(0.35, (ov["fin"] - ov["inicio"]) / 4))
                eslabones.append(
                    f"[{base_idx + i}:v]format=rgba,"
                    f"fade=t=in:st={ov['inicio']:.3f}:d={fdur:.3f}:alpha=1,"
                    f"fade=t=out:st={ov['fin'] - fdur:.3f}:d={fdur:.3f}:alpha=1[imgf{i}]")
            nxt = "[v]" if i == last else f"[vprecards{i + 1}]"
            eslabones.append(
                f"{cur}[imgf{i}]overlay={x}:{y}:eof_action=pass:"
                f"enable='between(t,{ov['inicio']:.3f},{ov['fin']:.3f})'{nxt}")
            cur = nxt
        fc += ";" + ";".join(eslabones)

    if fi > 0 or fo > 0:
        vf = []
        if fi > 0:
            vf.append("fade=t=in:st=0:d=0.5")
        if fo > 0 and dur_total > 1.5:
            vf.append(f"fade=t=out:st={dur_total - 0.6:.2f}:d=0.6")
        if vf:
            fc = fc.replace("[v]", "[vpre]", 1) + f";[vpre]{','.join(vf)}[v]"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *(["-progress", "pipe:1", "-nostats"] if progress_cb else []),
        *inputs,
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[ga]",
        *_codec_args(cfg),
        *_delivery_args(cfg, fps_out),
        "-profile:v", "high", "-level", _h264_level(fps_out),
        "-r", str(fps_out),
        # si después se mezcla música (segundo pase, que vuelve a codificar el
        # audio), este primer audio va a 320k: así la doble codificación AAC
        # no deja pérdida audible; sin música, el bitrate normal del config.
        "-c:a", "aac", "-b:a", ("320k" if musica_path and musica_path.is_file()
                                else str(cfg.get_path("output.audio_bitrate", "192k"))),
        "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out_file),
    ]
    if progress_cb:
        _run_with_progress(cmd, sum(max(0.1, e - s) for s, e in segments), progress_cb)
    else:
        run(cmd)

    if musica_path and musica_path.is_file():
        _mezcla_musica(out_file, musica_path, cfg, dur_total, source, segments, words)
    return out_file


def _mezcla_musica(out_file: Path, musica_path: Path, cfg, dur_total: float,
                   source: Path, segments: list[tuple[float, float]],
                   words: list | None) -> None:
    """Mezcla la música de fondo en un SEGUNDO pase aparte, ligero: el vídeo
    ya renderizado se copia tal cual (`-c:v copy`, sin recodificar — no
    cuenta como otro pase "de verdad", nada de calidad se pierde) y solo se
    decodifican dos pistas de audio (la mezcla ya hecha + la música) para
    combinarlas. Se calibra contra TU VOZ igual que el whoosh/etéreo.

    El fundido NO usa `afade` (rampa lineal en AMPLITUD): eso suena lento al
    principio y da un salto grande cerca del final, porque los dB son una
    escala logarítmica — un cambio de amplitud parejo NO es un cambio de
    volumen parejo al oído. el autor lo notó ("se sube mucho al principio") y
    pidió algo más preciso: una rampa lineal EN DECIBELES, 1dB por segundo,
    con `volume=` y una expresión que depende de `t` — empieza
    `musica_fade_seg` dB por debajo del nivel ya calibrado (`gan_m`, que es
    el nivel de régimen: `rel_voz_db` bajo tu voz), sube 1dB por segundo
    hasta llegarle, se queda plana, y baja igual de simétrica al final.
    Si algo falla aquí, el vídeo SIN música (ya renderizado bien) se queda
    tal cual — la música nunca debe tumbar un render que por lo demás ya
    salió bien."""
    rel_db_m = float(cfg.get_path("transiciones.musica_rel_voz_db", -18.0))
    voz_m = _speech_dbfs(source, segments, words or [], cfg)
    m_db = _mean_dbfs(["-i", str(musica_path)])
    if voz_m is not None and m_db is not None:
        gan_m = max(-50.0, min(0.0, voz_m + rel_db_m - m_db))
    else:
        gan_m = -24.0        # sin datos para calibrar: nivel bajo y seguro de fábrica
    fdur = max(1.0, min(float(cfg.get_path("transiciones.musica_fade_seg", 8.0)), dur_total / 3))
    fin_ini = dur_total - fdur
    expr = (f"if(lt(t,{fdur:.2f}),pow(10,({gan_m:.2f}-{fdur:.2f}+t)/20),"
           f"if(gt(t,{fin_ini:.2f}),pow(10,({gan_m:.2f}-(t-{fin_ini:.2f}))/20),"
           f"pow(10,{gan_m:.2f}/20)))")
    fc = (f"[1:a]volume='{expr}':eval=frame[gmusic];"
         f"[0:a][gmusic]amix=inputs=2:normalize=0:duration=first,"
         f"{_limite_pico(cfg)}[a]")
    tmp = out_file.with_name(out_file.stem + "_conmusica" + out_file.suffix)
    try:
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(out_file), "-i", str(musica_path),
            "-filter_complex", fc,
            "-map", "0:v", "-map", "[a]",
            "-shortest",        # el relleno del AAC (2 codificaciones) dejaba ~110 ms de audio de más
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", str(cfg.get_path("output.audio_bitrate", "192k")),
            "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(tmp),
        ])
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo mezclar la música de fondo, vídeo sin música ({exc}).")
        tmp.unlink(missing_ok=True)
        return
    tmp.replace(out_file)
    log(f"  música de fondo: {gan_m:+.1f} dB sobre el nivel de la pista.")



def _cartela_lineas(cfg, tipo: str) -> list[str]:
    v = cfg.get("videos") or {}
    if tipo == "intro":
        return [str(x) for x in (v.get("intro_texto") or [
            "Este vídeo salió de un DIRECTO",
            "Suscríbete para verlos completos"]) if str(x).strip()]
    return [str(x) for x in (v.get("outro_texto") or [
        "¡Gracias por verlo!",
        "Suscríbete y dale like",
        "Nos vemos en el próximo"]) if str(x).strip()]


def make_cartela(source: Path, at_seconds: float, cfg, out_file: Path,
                 tipo: str = "intro") -> Path:
    """Cartela ANIMADA de intro/outro para los vídeos horizontales.

    Todo con ffmpeg, sin descargar nada: se coge un fotograma del propio
    directo, se desenfoca y se oscurece (así pega con el vídeo en vez de ser
    una pantalla negra genérica), se le hace un zoom lento, y el texto entra
    con fundido. El audio va en silencio: el vídeo de verdad arranca después
    subiendo el volumen desde cero.
    """
    W = int(cfg.get_path("output.width", 1920))
    H = int(cfg.get_path("output.height", 1080))
    fps = int(cfg.get_path("output.fps", 30))
    v = cfg.get("videos") or {}
    dur = float(v.get("intro_seg", 5.0) if tipo == "intro" else v.get("outro_seg", 6.0))
    accent = str(cfg.get_path("thumbnail.accent", "#ff2d2d"))
    font = _esc_path(_font_cartela(cfg))
    lineas = _cartela_lineas(cfg, tipo)
    nf = max(1, int(dur * fps))

    # fondo: fotograma del directo, desenfocado, oscurecido y con zoom lento
    cad = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
           f"crop={W}:{H},boxblur=22:2,eq=brightness=-0.14:saturation=0.85:contrast=1.05,"
           f"vignette=PI/4.2,"
           f"zoompan=z='min(zoom+0.0006,1.10)':d={nf}:s={W}x{H}:fps={fps},"
           f"setsar=1,format=yuv420p[bg]")

    fs0 = int(H * 0.085)
    cur = "[bg]"
    tmpdir = Path(tempfile.mkdtemp(prefix="cartela_"))
    ficheros: list[Path] = []

    def _tf(txt: str) -> str:
        f = tmpdir / f"c{len(ficheros)}.txt"
        f.write_text(txt, encoding="utf-8")
        ficheros.append(f)
        return _esc_path(str(f))

    # el interlineado sigue al TAMAÑO REAL de cada línea (la primera es mayor),
    # si no la segunda queda pegada a la primera y la tercera muy suelta
    tam = [fs0 if i == 0 else int(fs0 * 0.62) for i in range(len(lineas))]
    altos = [int(f * 1.55) for f in tam]
    y0 = int((H - sum(altos)) / 2)
    for i, ln in enumerate(lineas):
        fs = tam[i]
        y = y0 + sum(altos[:i])
        ap = 0.35 + i * 0.35                       # cada línea entra un poco después
        nxt = f"[c{i}]"
        cad += (f";{cur}drawtext=fontfile='{font}':textfile='{_tf(ln)}':expansion=none:"
                f"fontcolor=white:fontsize={fs}:borderw={max(3, int(fs * 0.06))}:"
                f"bordercolor=black@0.85:x=(w-text_w)/2:y={y}:"
                f"alpha='if(lt(t,{ap:.2f}),0,min(1,(t-{ap:.2f})/0.7))'{nxt}")
        cur = nxt
    cad += (f";{cur}drawbox=x=0:y={H - int(H * 0.012)}:w={W}:h={int(H * 0.012)}:"
            f"color={accent}:t=fill,"
            f"fade=t=in:st=0:d=0.4,fade=t=out:st={dur - 0.5:.2f}:d=0.5[v]")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(source),
        "-f", "lavfi", "-t", f"{dur:.2f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", cad,
        "-map", "[v]", "-map", "1:a",
        "-t", f"{dur:.2f}",
        *_codec_args(cfg),
        *_delivery_args(cfg),
        "-profile:v", "high", "-level", _h264_level(fps), "-r", str(fps),
        "-c:a", "aac", "-b:a", str(cfg.get_path("output.audio_bitrate", "192k")),
        "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
        str(out_file),
    ]
    run(cmd)
    for f in ficheros:
        try:
            f.unlink()
        except OSError:
            pass
    return out_file


def _font_cartela(cfg) -> str:
    """Fuente para las cartelas (reusa la de las miniaturas)."""
    from . import thumbs
    return thumbs._font(cfg)


def _esc_path(ruta: str) -> str:
    """Escapa una ruta para meterla DENTRO de un filtro de ffmpeg."""
    from . import thumbs
    return thumbs._esc_path(ruta)


def pega_cartelas(principal: Path, intro: Path | None, outro: Path | None,
                  out_file: Path) -> Path:
    """Une intro + vídeo + outro SIN recodificar (concat demuxer, -c copy).
    Todos se generan con los mismos parámetros, así que el pegado es
    instantáneo: no vuelve a comprimir 20 minutos de vídeo."""
    partes = [p for p in (intro, principal, outro) if p and Path(p).is_file()]
    if len(partes) < 2:
        return principal
    lista = out_file.with_suffix(".concat.txt")
    lista.write_text(
        NL.join("file '" + str(Path(p).resolve()).replace("\\", "/") + "'" for p in partes),
        encoding="utf-8")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(lista),
         "-c", "copy", "-movflags", "+faststart", str(out_file)])
    try:
        lista.unlink()
    except OSError:
        pass
    return out_file

def _h264_level(fps: float) -> str:
    """Nivel H.264 según el fps. El 4.1 solo llega a 1080p ~30 fps; 1080p60
    exige el 4.2 — declarar 4.1 con 60 fps excede el nivel y algunos
    televisores/decodificadores por hardware rechazan el archivo."""
    return "4.2" if fps > 30 else "4.1"


def _limite_pico(cfg) -> str:
    """Parámetros de `alimiter` para la mezcla final: techo en el pico
    verdadero configurado (`output.true_peak_db`, -1.5 dB) y `level=0`.
    OJO con el valor por defecto de alimiter (level=1): tras limitar le
    aplica una ganancia compensatoria de 1/límite, o sea devuelve los picos a
    0 dBFS — con el 0.98 de antes anulaba el margen que loudnorm había dejado
    y los picos del audio final quedaban en -0.4 dBFS, sin holgura para la
    recompresión AAC. Con level=0 el limitador solo protege: no sube nada."""
    tp = float(cfg.get_path("output.true_peak_db", -1.5))
    # latency=1: alimiter mira 5 ms por adelantado y, sin compensarlo, retrasa
    # TODO el audio esos 5 ms respecto al vídeo (10 ms con el segundo pase de
    # la música) — los sonidos de transición caían ~4.5 ms tarde.
    return f"alimiter=limit={10 ** (tp / 20):.3f}:level=0:latency=1"


_NVENC_CACHE: dict = {}


def _nvenc_disponible() -> bool:
    """¿ffmpeg sabe codificar con la GPU NVIDIA (h264_nvenc)? Se pregunta UNA vez (cachea el
    resultado): correr `ffmpeg -encoders` de más en cada vídeo no aporta nada."""
    if "ok" not in _NVENC_CACHE:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                               text=True, timeout=15, errors="replace")
            _NVENC_CACHE["ok"] = "h264_nvenc" in (r.stdout or "")
        except Exception:                                  # noqa: BLE001 — sin ffmpeg en el PATH o lo que sea: se cae a CPU sin reventar
            _NVENC_CACHE["ok"] = False
    return _NVENC_CACHE["ok"]


def motor_activo(cfg, _gpu_disponible=None) -> str:
    """'gpu' o 'cpu': lo que de verdad se va a usar, según `output.motor` (auto|gpu|cpu — el autor lo
    elige en «Opciones» del panel, pedido 2026-09-22 pensando en abrir esto a gente sin GPU) y si
    esta máquina tiene una NVIDIA que ffmpeg sepa usar. Nunca revienta: si pide gpu y no hay, se
    cae a cpu con un aviso en vez de romper el render. `_gpu_disponible` es solo para las pruebas
    (sin tocar ffmpeg de verdad)."""
    pedido = str((cfg.get_path("output.motor", "auto") if cfg is not None else "auto") or "auto").lower()
    if pedido == "cpu":
        return "cpu"
    hay_gpu = (_gpu_disponible or _nvenc_disponible)()
    if pedido == "gpu" and not hay_gpu:
        log("AVISO: pediste render por GPU pero no encuentro una NVIDIA que ffmpeg sepa usar (h264_nvenc) — uso la CPU.")
        return "cpu"
    return "gpu" if hay_gpu else "cpu"


def _codec_args(cfg) -> list[str]:
    """`-c:v ...` y el control de calidad: por GPU (NVENC — mucho más rápido: la tarjeta hace el
    trabajo que hoy hace la CPU) o por CPU (libx264, como hasta ahora). `output.crf` afina la
    calidad en los dos casos (en GPU se traduce al `-cq` equivalente de NVENC)."""
    crf = str(cfg.get_path("output.crf", 20))
    if motor_activo(cfg) == "gpu":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", crf, "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", str(cfg.get_path("output.preset", "medium")), "-crf", crf]


# ------------------------------------------------------------------ pruebas (sin ffmpeg; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    """Solo la LÓGICA de elegir CPU o GPU (el render de verdad, con ffmpeg real, lo mide
    `selftest._c_render` — esto es aparte y no toca ffmpeg para nada)."""
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    class Cfg:
        def __init__(self, **d):
            self.d = d

        def get_path(self, k, dflt=None):
            return self.d.get(k, dflt)

    caso("con 'auto' usa la GPU si hay una NVIDIA que ffmpeg sepa aprovechar",
         motor_activo(Cfg(**{"output.motor": "auto"}), _gpu_disponible=lambda: True) == "gpu")
    caso("con 'auto' y SIN GPU cae a CPU sola, sin avisar (es lo normal, no un fallo)",
         motor_activo(Cfg(**{"output.motor": "auto"}), _gpu_disponible=lambda: False) == "cpu")
    caso("con 'cpu' usa CPU aunque SÍ haya una GPU (el autor puede forzarlo)",
         motor_activo(Cfg(**{"output.motor": "cpu"}), _gpu_disponible=lambda: True) == "cpu")
    caso("con 'gpu' pedido a propósito pero SIN GPU de verdad, se cae a CPU en vez de romper el render",
         motor_activo(Cfg(**{"output.motor": "gpu"}), _gpu_disponible=lambda: False) == "cpu")
    caso("sin config (None) o con la clave vacía, se comporta como 'auto'",
         motor_activo(None, _gpu_disponible=lambda: True) == "gpu" and motor_activo(Cfg(), _gpu_disponible=lambda: True) == "gpu")

    antes = dict(_NVENC_CACHE)
    try:
        _NVENC_CACHE.clear()
        _NVENC_CACHE["ok"] = True
        args_gpu = _codec_args(Cfg(**{"output.motor": "auto", "output.crf": 22}))
        caso("por GPU pide h264_nvenc con la calidad (crf->cq) que puso el autor, sin -preset de CPU",
             args_gpu[:2] == ["-c:v", "h264_nvenc"] and "22" in args_gpu and "-preset" in args_gpu and "medium" not in args_gpu, args_gpu)
        _NVENC_CACHE["ok"] = False
        args_cpu = _codec_args(Cfg(**{"output.motor": "auto", "output.crf": 22, "output.preset": "slow"}))
        caso("por CPU sigue como siempre: libx264 con su preset y crf",
             args_cpu == ["-c:v", "libx264", "-preset", "slow", "-crf", "22"], args_cpu)
    finally:
        _NVENC_CACHE.clear()
        _NVENC_CACHE.update(antes)

    # --- centro_pantalla_zoom: recentra "pantalla_zoom" donde de verdad hubo actividad, sin
    # meterse jamás en el recuadro de la cámara — el autor lo pidió (2026-09-23): el rectángulo fijo
    # de siempre a veces perdía justo lo que señalaba con el mouse si caía fuera de esa zona.
    defecto = {"x": 0.08, "y": 0.08, "w": 0.66, "h": 0.78}
    cam = {"x": 0.7672, "y": 0.6583, "w": 0.2172, "h": 0.2847}   # la medida real de Mi canal
    caso("sin centro detectado, usa el rectángulo de config.yaml tal cual",
         centro_pantalla_zoom(defecto, None, cam) == defecto)
    r1 = centro_pantalla_zoom(defecto, (0.3, 0.3), cam)          # centrado a la izquierda, lejos de la cámara
    caso("con un centro que no choca con la cámara, recentra ahí (mismo tamaño)",
         r1["w"] == defecto["w"] and r1["h"] == defecto["h"]
         and abs((r1["x"] + r1["w"] / 2) - 0.3) < 0.17 and abs((r1["y"] + r1["h"] / 2) - 0.3) < 0.2, r1)
    r2 = centro_pantalla_zoom(defecto, (0.9, 0.9), cam)          # justo donde está la cámara
    x0, y0, x1, y1 = r2["x"], r2["y"], r2["x"] + r2["w"], r2["y"] + r2["h"]
    cx0, cy0, cx1, cy1 = cam["x"], cam["y"], cam["x"] + cam["w"], cam["y"] + cam["h"]
    solapa = x0 < cx1 and x1 > cx0 and y0 < cy1 and y1 > cy0
    caso("un centro que SÍ chocaría con la cámara se aparta — nunca enseña la cámara encima de la pantalla",
         not solapa, r2)
    caso("el tamaño del rectángulo nunca cambia (el autor pidió un perímetro amplio, no ceñido al cursor)",
         r2["w"] == defecto["w"] and r2["h"] == defecto["h"], r2)
    caso("sin recuadro de cámara medido, recentra sin más restricción que el lienzo",
         centro_pantalla_zoom(defecto, (0.95, 0.95), None)["x"] <= 1.0 - defecto["w"] + 1e-9)

    import tempfile
    from . import config as _cfgmod
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "config.yaml"
        copia.write_bytes(b"output:\r\n  fps: 60\r\n  motor: auto     # comentario que no se debe perder\r\n  crf: 20\r\n")
        ok = _cfgmod.guarda_motor("gpu", ruta=copia)
        crudo = copia.read_bytes()                      # en bytes: para comprobar que sigue siendo CRLF de verdad, no lo que read_text() normaliza solo
        caso("elegir GPU/CPU en «Opciones» escribe SOLO esa línea de config.yaml, conserva el CRLF y no pierde el comentario ni el resto",
             ok and b"\r\n  motor: gpu     # comentario que no se debe perder\r\n" in crudo and b"fps: 60" in crudo and b"crf: 20" in crudo, crudo)
        caso("un valor que no es auto/gpu/cpu se rechaza (no escribe nada raro)", _cfgmod.guarda_motor("nvidia-please", ruta=copia) is False)
        caso("`config.load` recoge el cambio al vuelo, sin reiniciar el panel", _cfgmod.load(copia).get_path("output.motor") == "gpu")

    # --- _popen_con_reintento: el arranque de ffmpeg no debe pedir un clic en "Reintentar" a mano
    # por un tropiezo pasajero del sistema (antivirus escaneando el .exe, lo más común) — el autor lo
    # pidió (2026-09-23) tras verlo fallar así dos veces seguidas. subprocess.Popen interceptado:
    # nada de esto arranca un proceso de verdad ni tarda de verdad.
    real_popen, real_sleep = subprocess.Popen, time.sleep
    dormidas: list[float] = []
    time.sleep = lambda s: dormidas.append(s)
    llamadas = []

    class _ProcFalso:
        pass

    try:
        def falla_dos_veces(cmd, **kw):
            llamadas.append(cmd)
            if len(llamadas) <= 2:
                raise FileNotFoundError(2, "No such file or directory")
            return _ProcFalso()

        subprocess.Popen = falla_dos_veces
        p = _popen_con_reintento(["ffmpeg", "-x"])
        caso("si el sistema no arranca ffmpeg (antivirus, lo que sea), reintenta solo hasta que sí",
             isinstance(p, _ProcFalso) and len(llamadas) == 3, f"llamadas={len(llamadas)}")
        caso("la espera entre reintentos crece (3s, luego 6s)", dormidas == [3.0, 6.0], str(dormidas))

        llamadas.clear()
        dormidas.clear()

        def siempre_falla(cmd, **kw):
            llamadas.append(cmd)
            raise FileNotFoundError(2, "No such file or directory")

        subprocess.Popen = siempre_falla
        try:
            _popen_con_reintento(["ffmpeg", "-x"], reintentos=3)
            caso("si NUNCA arranca, se rinde tras agotar los reintentos (no se queda colgado para siempre)", False)
        except RuntimeError as exc:
            caso("si NUNCA arranca, se rinde tras agotar los reintentos (no se queda colgado para siempre)",
                 len(llamadas) == 3 and "ffmpeg" in str(exc), f"llamadas={len(llamadas)} · {exc}")
    finally:
        subprocess.Popen, time.sleep = real_popen, real_sleep

    # --- _ken_burns_fx: el pan/zoom sutil, apagable por config.yaml, con dirección de verdad, y
    # SUAVE de verdad (sub-píxel en cada fotograma, no un salto cada ~6) — el autor lo notó (2026-09-23) ---
    cfg_kb = Cfg(**{"transiciones.ken_burns_zoom": 0.06, "transiciones.ken_burns_pan": 0.015})
    fx_der = _ken_burns_fx(10.0, 1, 60, cfg_kb)
    fx_izq = _ken_burns_fx(10.0, -1, 60, cfg_kb)
    caso("con zoom en 0 (apagado por config), no añade nada al filtro",
         _ken_burns_fx(10.0, 1, 60, Cfg(**{"transiciones.ken_burns_zoom": 0,
                                           "transiciones.ken_burns_pan": 0})) == "")
    caso("la deriva sin zoom no existe (sin zoom no hay margen libre para moverse): también apagado",
         _ken_burns_fx(10.0, 1, 60, Cfg(**{"transiciones.ken_burns_zoom": 0,
                                           "transiciones.ken_burns_pan": 0.02})) == "")
    caso("con zoom activo arma `perspective` con interpolación bicúbica, evaluada en CADA fotograma",
         "perspective=" in fx_der and "interpolation=cubic" in fx_der and "eval=frame" in fx_der
         and "fps=60" in fx_der and "format=yuv420p" in fx_der, fx_der)
    caso("la dirección +1/-1 cambia el signo de la deriva (no es el mismo filtro)",
         fx_der != fx_izq and "W/2+1*0.0150" in fx_der and "W/2-1*0.0150" in fx_izq, (fx_der, fx_izq))
    caso("un tramo brevísimo no revienta (el piso de 0.3s evita dividir entre casi-cero)",
         "(on+0)/18," in _ken_burns_fx(0.05, 1, 60, cfg_kb))
    caso("una deriva absurda de config.yaml se recorta a lo que el zoom deja libre (nunca sale del fotograma)",
         "0.0283" in _ken_burns_fx(10.0, 1, 60, Cfg(**{"transiciones.ken_burns_zoom": 0.06,
                                                       "transiciones.ken_burns_pan": 0.5})))

    # La GEOMETRÍA de verdad: se evalúan las propias expresiones de ffmpeg (son aritmética
    # simple, sirven tal cual en Python) fotograma a fotograma — la ventana que se estira a
    # pantalla completa tiene que arrancar en el fotograma ENTERO, ir encogiendo sin retroceder
    # NUNCA, y no salirse jamás del fotograma (ni con la deriva a cada lado).
    W_t, H_t, fps_t, dur_t = 1920, 1080, 60, 10.0
    n_t = int(dur_t * fps_t)
    for direccion in (1, -1):
        c = _ken_burns_coords(dur_t, direccion, fps_t, cfg_kb)
        ventana = []
        for on in range(0, n_t + 30):                            # incluso pasado el final (se queda quieto)
            ns = {"W": W_t, "H": H_t, "on": on, "min": min}
            ventana.append(tuple(float(eval(c[k], {"__builtins__": {}}, ns)) for k in ("x0", "x1", "y0", "y1")))
        x0s, x1s, y0s, y1s = zip(*ventana)
        anchos = [b - a for a, b in zip(x0s, x1s)]
        caso(f"(dirección {direccion:+d}) la ventana arranca siendo el fotograma ENTERO",
             abs(anchos[0] - W_t) < 1e-6 and abs(y1s[0] - y0s[0] - H_t) < 1e-6, (anchos[0], y1s[0] - y0s[0]))
        caso(f"(dirección {direccion:+d}) la ventana nunca se sale del fotograma, en ningún fotograma",
             min(x0s) >= -1e-6 and max(x1s) <= W_t + 1e-6 and min(y0s) >= -1e-6 and max(y1s) <= H_t + 1e-6,
             (min(x0s), max(x1s), min(y0s), max(y1s)))
        caso(f"(dirección {direccion:+d}) el zoom solo va hacia dentro: la ventana nunca vuelve a crecer",
             all(b <= a + 1e-9 for a, b in zip(anchos, anchos[1:])))
        caso(f"(dirección {direccion:+d}) termina exactamente en 1/(1+zoom) del fotograma (6% -> 94.3%) y se queda ahí",
             abs(anchos[n_t] - W_t / 1.06) < 1e-3 and abs(anchos[-1] - anchos[n_t]) < 1e-9, (anchos[n_t], anchos[-1]))
        pasos = [a - b for a, b in zip(anchos, anchos[1:n_t + 1])]
        caso(f"(dirección {direccion:+d}) CADA fotograma encoge un poco (ni un solo fotograma igual al anterior): sin «por partes»",
             min(pasos) > 0.05 and max(pasos) < 0.5, (min(pasos), max(pasos)))

    # UNA sola fórmula: los ejemplos de «Ediciones» usan `ken_burns_ventana` (números) y el render usa
    # las expresiones de ffmpeg — tienen que dar lo mismo, o la documentación enseñaría otra cosa.
    def _eval_ventana(c, on):
        ns = {"W": 1.0, "H": 1.0, "on": on, "min": min}
        return tuple(float(eval(c[k], {"__builtins__": {}}, ns)) for k in ("x0", "y0", "x1", "y1"))

    for direccion in (1, -1):
        c = _ken_burns_coords(10.0, direccion, 60, cfg_kb)
        difs = [max(abs(a - b) for a, b in zip(_eval_ventana(c, on), ken_burns_ventana(on / 600, direccion, cfg_kb)))
                for on in (0, 1, 137, 300, 599, 600, 700)]
        caso(f"(dirección {direccion:+d}) la fórmula en números (ejemplos de «Ediciones») y las expresiones de ffmpeg (render) coinciden",
             max(difs) < 1e-9, max(difs))
    caso("con Ken Burns apagado, la fórmula en números también lo dice (None)",
         ken_burns_ventana(0.5, 1, Cfg(**{"transiciones.ken_burns_zoom": 0})) is None)

    # --- explicativa_rect: el tercio derecho enseña TU CÁMARA centrada (no el rectángulo ampliado de
    # «cara», que en una grabación real dejaba la cara pegada al borde y cortada) ---
    cam_real = {"x": 0.7672, "y": 0.6583, "w": 0.2172, "h": 0.2847, "zoom_cara": 1.7}   # la medida real de Mi canal
    asp = 640 / 1080
    fx, fy, fw, fh = explicativa_rect(cam_real, asp, 2560, 1440)
    caso("«explicativa»: la ventana cabe DENTRO de la caja de la cámara (sin enseñar nada de fuera)",
         fx >= cam_real["x"] - 1e-9 and fx + fw <= cam_real["x"] + cam_real["w"] + 1e-9
         and fy >= cam_real["y"] - 1e-9 and fy + fh <= cam_real["y"] + cam_real["h"] + 1e-9, (fx, fy, fw, fh))
    caso("«explicativa»: tiene la proporción VERTICAL del tercio (ancho/alto = 640/1080)",
         abs((fw * 2560) / (fh * 1440) - asp) < 1e-6, (fw * 2560) / (fh * 1440))
    caso("«explicativa»: está centrada en la cámara (la cara queda en medio del tercio, no pegada al borde)",
         abs((fx + fw / 2) - (cam_real["x"] + cam_real["w"] / 2)) < 1e-9
         and abs((fy + fh / 2) - (cam_real["y"] + cam_real["h"] / 2)) < 1e-9)
    caso("«explicativa»: deja fuera el marco rojo de arriba y de abajo (usa el 94 % de la altura)",
         abs(fh / cam_real["h"] - EXPLICATIVA_ALTO) < 1e-9, fh / cam_real["h"])
    gx, gy, gw, gh = explicativa_rect({"x": 0.5, "y": 0.5, "w": 0.03, "h": 0.5}, asp, 2560, 1440)
    caso("«explicativa»: con una cámara muy estrecha manda el ANCHO (no se sale de la caja) y mantiene la proporción",
         gw <= 0.03 * EXPLICATIVA_ALTO + 1e-9 and abs((gw * 2560) / (gh * 1440) - asp) < 1e-6, (gw, gh))
    hx, hy, hw, hh = explicativa_rect({"x": 0.9, "y": 0.9, "w": 0.1, "h": 0.1}, asp, 2560, 1440)
    caso("«explicativa»: con la cámara pegada a la esquina de la grabación, la ventana sigue dentro del lienzo",
         hx >= 0 and hy >= 0 and hx + hw <= 1.0 + 1e-9 and hy + hh <= 1.0 + 1e-9, (hx, hy, hw, hh))

    # Una ESCENA con un corte en medio son dos trozos del mismo modo: el zoom debe SEGUIR donde iba
    # (no reiniciarse en el 2º trozo) — el fotograma siguiente al último del trozo 1 es el 1º del 2º.
    desde, total = _ken_burns_escenas([4.0, 6.0, 3.0, 5.0], ["cara", "cara", "pantalla", "pantalla"], [1, 1, 0, -1])
    caso("una escena partida por un corte comparte su duración total y el 2º trozo sabe cuánto llevaba el 1º",
         total == {0: 10.0, 1: 10.0, 3: 5.0} and desde == {0: 0.0, 1: 4.0, 3: 0.0}, (desde, total))
    caso("un trozo sin Ken Burns no entra en ninguna escena", 2 not in desde and 2 not in total)
    caso("dos escenas seguidas del mismo modo pero con distinta dirección NO se mezclan",
         _ken_burns_escenas([3.0, 3.0], ["cara", "cara"], [1, -1]) == ({0: 0.0, 1: 0.0}, {0: 3.0, 1: 3.0}))
    c1 = _ken_burns_coords(4.0, 1, 60, cfg_kb, 0.0, 10.0)
    c2 = _ken_burns_coords(6.0, 1, 60, cfg_kb, 4.0, 10.0)
    fin1 = _eval_ventana(c1, 4 * 60 - 1)
    ini2 = _eval_ventana(c2, 0)
    fin2 = _eval_ventana(c2, 6 * 60 - 1)
    ultimo = ken_burns_ventana(1.0, 1, cfg_kb)
    caso("al pasar de un trozo al siguiente de la misma escena el zoom continúa (salto < 1 fotograma normal)",
         max(abs(a - b) for a, b in zip(fin1, ini2)) < 0.0004, (fin1, ini2))
    caso("y el último trozo de la escena termina en el zoom completo",
         max(abs(a - b) for a, b in zip(fin2, ultimo)) < 0.0004, (fin2, ultimo))

    # --- el zoom que SIGUE AL RATÓN (el autor, 2026-09-24): la expresión de ffmpeg recorre el camino fotograma a fotograma
    def _ev(expr: str, on: int) -> float:
        ns = {"on": on, "lt": lambda a, b: a < b, "iff": lambda c, a, b: a if c else b}
        return float(eval(expr.replace("if(", "iff("), {"__builtins__": {}}, ns))
    ex = _lineal([0, 30, 90], [100.0, 400.0, 400.0])
    caso("la posición va en línea recta entre los puntos del camino y se queda en el último al acabar",
         [_ev(ex, f) for f in (0, 15, 30, 60, 90, 500)] == [100.0, 250.0, 400.0, 400.0, 400.0, 400.0],
         [_ev(ex, f) for f in (0, 15, 30, 60, 90, 500)])
    cm = {"w": 0.65, "h": 0.65, "puntos": [(0.0, 0.40, 0.45), (2.0, 0.60, 0.325), (6.0, 0.675, 0.325)]}
    fxm = zoom_movil_fx(cm, 2560, 1440, 1920, 1080, 60)
    xs_ = re.search(r"x0='([^']+)'", fxm).group(1)
    ys_ = re.search(r"y0='([^']+)'", fxm).group(1)
    esperado = [(0.40 * 2560 - 832, 0.45 * 1440 - 468), (0.60 * 2560 - 832, 0.325 * 1440 - 468),
                (0.675 * 2560 - 832, 0.325 * 1440 - 468)]
    obtenido = [(_ev(xs_, f), _ev(ys_, f)) for f in (0, 120, 360)]
    caso("zoom que sigue al ratón: la esquina del encuadre pasa por cada punto del camino (en el fotograma exacto), con "
         "`perspective` (sub-píxel) y escalado a 1920×1080", "perspective=" in fxm and "scale=1920:1080" in fxm
         and all(abs(a - b) < 0.02 and abs(c - d) < 0.02 for (a, c), (b, d) in zip(obtenido, esperado)), (obtenido, esperado))
    xs_all = [_ev(xs_, f) for f in range(0, 400, 7)]
    ys_all = [_ev(ys_, f) for f in range(0, 400, 7)]
    caso("…y nunca se sale del fotograma", min(xs_all) >= 0 and max(xs_all) + 1664 <= 2560 + 0.5
         and min(ys_all) >= 0 and max(ys_all) + 936 <= 1440 + 0.5, (min(xs_all), max(xs_all), min(ys_all), max(ys_all)))
    fxq = zoom_movil_fx({"w": 0.65, "h": 0.65, "puntos": [(0.0, 0.4, 0.4), (8.0, 0.4, 0.4)]}, 2560, 1440, 1920, 1080, 60)
    caso("si el encuadre no se mueve en todo el tramo, un recorte fijo (más barato), del mismo tamaño",
         fxq.startswith("crop=1664:936:") and "perspective" not in fxq, fxq)
    caso("un encuadre fijo no cuenta como «se mueve» (así puede llevar el movimiento suave, como los demás); uno que se "
         "mueve, sí", not camino_se_mueve({"w": 0.65, "h": 0.65, "puntos": [(0.0, 0.4, 0.4), (8.0, 0.4, 0.4)]})
         and camino_se_mueve(cm) and not camino_se_mueve(None))
    return filas


def _delivery_args(cfg, fps: int | None = None) -> list[str]:
    """GOP corto y regular + tope de bitrate. Un GOP largo (por defecto x264
    mete un keyframe cada ~8 s) hace que el reproductor y YouTube tarden en
    'engancharse' al principio del clip; con uno cada 2 s arranca fino.
    `fps`: el fps REAL de salida (Vídeos PRO lo baja al de la grabación si
    fuera menor que el configurado); por defecto, el del config."""
    fps = int(fps or cfg.get_path("output.fps", 30))
    g = max(2, int(cfg.get_path("output.gop_seconds", 2))) * fps
    maxrate = str(cfg.get_path("output.maxrate", "12M"))
    bufsize = str(cfg.get_path("output.bufsize", "16M"))
    return ["-g", str(g), "-keyint_min", str(max(1, g // 2)),
            "-maxrate", maxrate, "-bufsize", bufsize]


def _popen_con_reintento(cmd: list[str], reintentos: int = 4, **kw) -> subprocess.Popen:
    """`subprocess.Popen` que reintenta solo si el SISTEMA no logra ni arrancar el proceso
    (`FileNotFoundError`/`PermissionError` — un antivirus escaneando el .exe justo en ese
    instante es el sospechoso más común) — el autor lo pidió (2026-09-23), vio justo este fallo dos
    veces seguidas y quiere que el proceso entero corra sin pedirle un clic manual. Con espera
    CORTA y creciente (a diferencia del reintento de Gemini: esto es un tropiezo del sistema
    operativo, no una cola de servidor — se resuelve en segundos, no minutos)."""
    ultimo: Exception | None = None
    for intento in range(reintentos):
        try:
            return subprocess.Popen(cmd, **kw)
        except (FileNotFoundError, PermissionError) as exc:
            largo = error_de_arranque(cmd, exc)
            if largo:
                # comando demasiado largo (WinError 206): ESTO era el «No se encontró ffmpeg» de los vídeos -3 y -6 de
                # el autor (2026-09-22/23), no un antivirus — reintentar no lo arregla, así que ni se intenta
                raise RuntimeError(largo) from exc
            ultimo = exc
            if intento + 1 >= reintentos:
                break
            espera = min(20.0, 3.0 * (2 ** intento))       # 3, 6, 12, 20s...
            log(f"  {cmd[0]} no arrancó (¿algo lo bloqueó un instante?); "
                f"reintento {intento + 2}/{reintentos} en {espera:.0f}s")
            time.sleep(espera)
    raise RuntimeError(
        f"No se encontró '{cmd[0]}' tras {reintentos} intentos. ¿ffmpeg está en el PATH?") from ultimo


def _run_with_progress(cmd: list[str], total_s: float, cb) -> None:
    """Como run() pero parsea el `-progress` de ffmpeg y llama cb(pct 0-100). El `-filter_complex` va SIEMPRE por
    archivo (`util.filtro_a_archivo`): el de un vídeo largo pasa de los 32.767 caracteres que admite Windows en una
    línea de comandos, y así el render de prueba de «Probar el sistema» usa exactamente el mismo camino."""
    cmd, tmp_filtro = filtro_a_archivo(cmd, siempre=True)
    try:
        proc = _popen_con_reintento(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time=") and total_s > 0:
                try:
                    h, m, s = line.split("=", 1)[1].split(":")
                    cur = int(h) * 3600 + int(m) * 60 + float(s)
                    cb(max(1, min(99, int(cur / total_s * 100))))
                except (ValueError, ZeroDivisionError):
                    pass
            elif line == "progress=end":
                cb(100)
        proc.wait()
        if proc.returncode != 0:
            tail = (proc.stderr.read() if proc.stderr else "")[-2000:]
            raise RuntimeError(f"Falló ffmpeg:\n{tail}")
    finally:
        if tmp_filtro is not None:
            tmp_filtro.unlink(missing_ok=True)


def render_clip(source: Path, clip: dict, cfg, meta: dict,
                ass_path: Path | None, out_file: Path, progress_cb=None,
                words: list | None = None) -> Path:
    start = float(clip["start"])
    dur = float(clip["end"]) - start

    filt = build_filter(cfg, meta, ass_path)
    censor = _censor_prefix(words or [], [(start, start + dur)], cfg)
    af = censor + _audio_filter(cfg, source, start, dur)   # silencia palabrotas + loudnorm
    if af and meta.get("has_audio", True):
        filt = filt + f";[0:a]{af}[a]"
        amap = ["-map", "[a]"]
    else:
        amap = ["-map", "0:a?"]

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *(["-progress", "pipe:1", "-nostats"] if progress_cb else []),
        # -ss antes de -i = seek rápido; -ss de nuevo después sería exacto
        # pero lento. Con re-encode el seek rápido ya es preciso al frame.
        "-ss", f"{start:.3f}",
        "-t", f"{dur:.3f}",
        "-i", str(source),
        "-filter_complex", filt,
        "-map", "[v]", *amap,
        *_codec_args(cfg),
        *_delivery_args(cfg),
        "-profile:v", "high", "-level", _h264_level(int(cfg.get_path("output.fps", 30))),
        "-r", str(cfg.get_path("output.fps", 30)),
        "-c:a", "aac", "-b:a", str(cfg.get_path("output.audio_bitrate", "192k")),
        "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out_file),
    ]
    if progress_cb:
        _run_with_progress(cmd, dur, progress_cb)
    else:
        run(cmd)
    return out_file
