"""Orquestador. Cada etapa cachea su resultado en work/<job>/ para que
puedas reintentar sin volver a transcribir un vídeo largo entero.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import anclaje as _anclaje
from . import config, ingest, render, subtitles, thumbs, transcribe, voz
from . import vision as _vision
from .util import hhmmss, log, normalize, safe_slug

_TEMAS_V = 1   # sube esto si cambia la logica de deteccion de tematicas


def job_id(source: str) -> str:
    h = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
    stem = safe_slug(Path(source).stem if not ingest.is_url(source) else source, 28)
    return f"{stem}-{h}"


def _clear_control_flags(work: Path) -> None:
    """Borra banderas de control (PAUSE / STOP) que hubieran quedado de un
    intento anterior sobre esta misma URL. work/ se comparte por URL."""
    for name in ("PAUSE", "STOP"):
        try:
            (work / name).unlink()
        except OSError:
            pass


def _pause_gate(work: Path) -> bool:
    """Pausa cooperativa controlada desde la UI.

    - Si existe work/PAUSE, se queda esperando (sin gastar CPU) hasta que la
      UI lo borre con "Reanudar".
    - Si existe work/STOP, devuelve True para que el bucle corte limpio y
      guarde lo que ya lleva renderizado.
    """
    if (work / "STOP").exists():
        return True
    if not (work / "PAUSE").exists():
        return False
    log("PAUSA — detenido por el usuario. Pulsa 'Reanudar' en la UI para seguir.")
    while (work / "PAUSE").exists():
        if (work / "STOP").exists():
            return True
        time.sleep(2)
    log("Reanudado.")
    return (work / "STOP").exists()


def _con_reintento_auto(analizar, work: Path, cfg, _dormir=None, _ahora=None) -> dict | None:
    """`analizar()` -> plan | None. Si devuelve None porque Gemini está SATURADO en todos los modelos (tras la
    cascada entera: `util.call_gemini_cadena`), no se rinde: lo vuelve a intentar SOLO más tarde, a los minutos de
    `ai.reintento_auto_min` ([10, 20, 30] por defecto: hasta ~1 h), en vez de acabar en «NO ANALIZADO» esperando a
    que el autor pulse «Reintentar» (lo pidió el 2026-09-24: subir el vídeo e irse). Los intentos saturados no gastan
    cuota del día (Google no llega a procesarlos). No reintenta si el fallo no es de saturación (sin clave, la cuota
    DEL DÍA agotada, un error de verdad). Respeta «Pausar» y cancelar mientras espera. `_dormir`/`_ahora`: pruebas."""
    from . import director
    dormir, ahora = _dormir or time.sleep, _ahora or time.time
    esperas = cfg.get_path("ai.reintento_auto_min", None)
    esperas = [10, 20, 30] if esperas is None else [float(x) for x in esperas]
    plan = analizar()
    for k, minutos in enumerate(esperas, 1):
        if plan is not None or not (director.ultimo_fallo or {}).get("transitorio"):
            break
        hora = time.strftime("%H:%M", time.localtime(ahora() + minutos * 60))
        log(f"Gemini sigue saturado en todos los modelos: reintento automático {k} de {len(esperas)} a las {hora} "
            f"(en {minutos:.0f} min). No hace falta que hagas nada: el vídeo sigue solo cuando Gemini responda (los "
            "intentos saturados no gastan tu cuota). Si prefieres no esperar, cancela el trabajo.")
        fin = ahora() + minutos * 60
        while ahora() < fin:
            if _pause_gate(work):
                log("Reintento automático cancelado.")
                return None
            dormir(min(5.0, max(0.0, fin - ahora())))
        log("PROGRESO director_inicio")
        plan = analizar()
    return plan


def _flush_manifest(out_dir: Path, manifest: list[dict]) -> None:
    """Escribe manifest.json después de CADA vídeo para que la UI pueda ir
    mostrando los que ya están listos sin esperar a que termine todo."""
    try:
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _make_thumb(mp4: Path, titulo: str, dur: float, tag: str, layout: str, cfg) -> None:
    """Genera la miniatura JUNTO al .mp4 (mismo nombre, .jpg). Así el vídeo
    ya tiene su captura desde que se renderiza y al subir a YouTube solo se
    reutiliza (nada de generarla al vuelo y que falle). La miniatura se sube
    por la API; NO se quema en el vídeo."""
    if not cfg.get_path("thumbnail.enabled", True):
        return
    try:
        thumbs.make_thumbnail(mp4, max(0.5, dur * 0.42), titulo,
                              mp4.with_suffix(".jpg"), cfg, tag, layout)
    except Exception as exc:  # noqa: BLE001
        log(f"  AVISO: miniatura de {mp4.name} no generada ({exc}).")


def video_out_dir(source: str):
    """Carpeta de salida de los videos largos de este directo."""
    P = config.paths()
    return P["outbox"] / f"{job_id(source)}--videos"


def _limita_recortes(recortes: list[dict], dur_total: float,
                     max_pct: float = 0.5) -> list[dict]:
    """Red de seguridad: si el director propone cortar más de `max_pct` del
    vídeo, descarta los recortes MÁS GRANDES uno a uno hasta quedar debajo
    del tope. Un corte enorme es más probable que sea un error de juicio
    que varios recortes pequeños de relleno real."""
    if dur_total <= 0 or not recortes:
        return recortes
    total = sum(float(r["fin"]) - float(r["inicio"]) for r in recortes)
    tope = dur_total * max_pct
    if total <= tope:
        return recortes
    mantener = sorted(recortes, key=lambda r: float(r["fin"]) - float(r["inicio"]))
    while total > tope and mantener:
        peor = mantener.pop()
        total -= float(peor["fin"]) - float(peor["inicio"])
    log(f"AVISO: el director quería cortar {100 * sum(float(r['fin']) - float(r['inicio']) for r in recortes) / dur_total:.0f}% "
        f"del vídeo — por seguridad me quedo solo con los {len(mantener)} recortes más "
        f"chicos ({100 * total / dur_total:.0f}% del vídeo). Si de verdad hay que cortar "
        f"tanto, revisa el vídeo a mano.")
    return mantener


def _aplica_recortes(segs: list[tuple[float, float]],
                     recortes: list[dict], dur_total: float,
                     minimo: float = 1.0) -> list[tuple[float, float]]:
    """Resta del material los tramos que el director marcó como relleno.
    Los recortes vienen validados (dentro de rango) por director.py, pero
    aquí se vuelve a comprobar: perder 2 s de un vídeo real por un recorte
    mal calculado es peor que dejar 2 s de relleno de más."""
    cortes = sorted((float(r["inicio"]), float(r["fin"])) for r in recortes
                    if float(r["fin"]) - float(r["inicio"]) > 0)
    out: list[tuple[float, float]] = []
    for s0, e0 in segs:
        cursor = s0
        for a, b in cortes:
            a, b = max(a, s0), min(b, e0)
            if b <= cursor or a >= e0:
                continue
            if a > cursor:
                out.append((cursor, a))
            cursor = max(cursor, b)
        if e0 - cursor > 0:
            out.append((cursor, e0))
    # tramos minúsculos (recorte que dejó una miga de 0.3 s) no valen nada
    # y solo añaden un corte de más: se sueldan al vecino si están pegados,
    # si no se descartan
    limpio: list[tuple[float, float]] = []
    for a, b in out:
        if b - a < minimo and limpio and a - limpio[-1][1] < 0.5:
            limpio[-1] = (limpio[-1][0], b)
        elif b - a >= minimo:
            limpio.append((a, b))
    return limpio or segs   # si algo salió mal y no queda nada, mejor sin cortar


def _tiempo_final(t: float, segs: list[tuple[float, float]]) -> float | None:
    """El segundo `t` del vídeo ORIGINAL -> su segundo en el vídeo YA
    RECORTADO (concatenando los `segs` que sobrevivieron a `_aplica_recortes`).
    None si `t` cayó dentro de un tramo que se cortó — esa tarjeta ya no
    tiene dónde ir, mejor perderla que ponerla en el segundo equivocado."""
    acumulado = 0.0
    for a, b in segs:
        if a <= t <= b:
            return acumulado + (t - a)
        acumulado += b - a
    return None


# El código que decide cómo queda el vídeo: si cambia cualquiera, un vídeo ya renderizado deja de valer (`_huella_render`).
# La música no: su orden es al azar en cada corrida, y reintentar no debe repetir el render solo por eso.
_CODIGO_RENDER = ("pipeline.py", "render.py", "tarjetas.py", "imagenes.py", "ilustraciones.py", "ilus_motor.py",
                  "ilus_escenas.py", "ilus_escenas2.py", "voz.py", "movimiento.py", "ritmo.py", "escena_completa.py", "escenas_estilo.py",
                  "camara_aparte.py", "anclaje.py")


def _huella_render(partes: dict, work: Path) -> str:
    """De qué sale el vídeo: los datos del render (tramos, escenas, tarjetas, imágenes, ilustraciones, ajustes de salida),
    los datos del ratón y la actividad de ESA grabación y el código que los dibuja. Si coincide con la del vídeo ya
    renderizado, se puede reusar; si no (otro plan, o el programa mejoró), se renderiza de nuevo."""
    h = hashlib.sha1(json.dumps(partes, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    for nombre in ("actividad_pantalla.json", "cursor.json"):
        try:
            h.update((Path(work) / nombre).read_bytes())
        except OSError:
            h.update(b"-")
    for nombre in _CODIGO_RENDER:
        try:
            h.update((Path(__file__).parent / nombre).read_bytes())
        except OSError:
            h.update(nombre.encode("utf-8"))
    return h.hexdigest()[:20]


def _tiempo_final_item(t: float, segs: list[tuple[float, float]],
                       cortes_programa: list[tuple[float, float]]) -> float | None:
    """`_tiempo_final` para una tarjeta, imagen o ilustración: si `t` cae en algo que cortó el PROGRAMA y no la IA
    (una pausa de duda acortada, el trocito que añadió al borde de un corte de la IA: `voz.cortes_del_programa`), la IA
    la anclaba a lo que dices justo después — sale donde sigue tu voz en vez de perderse. Si cae en un corte de la IA,
    None como siempre."""
    t_final = _tiempo_final(t, segs)
    if t_final is None and any(a - 0.01 <= t <= b + 0.01 for a, b in cortes_programa):
        t_final = _tiempo_final_capitulo(t, segs)
    return t_final


def _fusiona_cortes(cortes: list[tuple[float, float]],
                    junta_bajo: float = 0.02) -> list[tuple[float, float]]:
    """Ordena los cortes y fusiona los que se solapan o quedan pegados
    (< `junta_bajo` s): dos cortes tocándose son en realidad uno."""
    out: list[tuple[float, float]] = []
    for a, b in sorted((min(x, y), max(x, y)) for x, y in cortes if abs(y - x) > 1e-6):
        if out and a <= out[-1][1] + junta_bajo:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def tiempo_original(t_final: float, segs: list[tuple[float, float]],
                    lado: str = "inicio") -> float:
    """Inversa de `_tiempo_final`: el segundo `t_final` del vídeo YA MONTADO ->
    su segundo en el vídeo ORIGINAL (para el editor de cortes: el autor marca
    A/B viendo el vídeo final, y el corte se aplica sobre el original).
    `lado` decide qué pasa si `t_final` cae justo en la frontera entre dos
    tramos: "inicio" (el punto A de un corte) cuenta como el arranque del
    tramo SIGUIENTE, "fin" (el punto B) como el final del tramo ANTERIOR — así
    un corte pegado a una frontera no se come material del vecino."""
    acumulado = 0.0
    for a, b in segs:
        d = b - a
        if lado == "inicio":
            if t_final < acumulado + d - 1e-9:
                return a + max(0.0, t_final - acumulado)
        elif t_final <= acumulado + d + 1e-9:
            return a + max(0.0, t_final - acumulado)
        acumulado += d
    return segs[-1][1] if segs else t_final


def _escribe_analisis(out_dir: Path, datos: dict) -> None:
    """Deja en la carpeta del vídeo si Gemini lo analizó o no (y con qué modelo):
    el panel lo enseña como tarjeta grande. Nunca debe tumbar el render."""
    try:
        (out_dir / "analisis_ia.json").write_text(
            json.dumps({**datos, "fecha": time.strftime("%Y-%m-%d %H:%M:%S")},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log(f"AVISO: no pude guardar el estado del análisis de IA ({exc}).")


def _momentos_suscribete(words: list[dict], separacion: float = 4.0) -> list[float]:
    """Segundos (tiempo ORIGINAL, sin recortar) donde el autor dice "suscríbete"
    o alguna variante ("suscríbanse", "suscripción"...) — detección por
    texto, no por IA (mismo criterio que enlaces.py: nunca inventar que se
    dijo algo). Agrupa menciones a menos de `separacion` en una sola, para
    no duplicar si lo dice dos veces seguidas en la misma frase."""
    momentos: list[float] = []
    for w in words or []:
        if "suscri" not in normalize(str(w.get("word", ""))):
            continue
        t = float(w.get("start", 0) or 0)
        if momentos and t - momentos[-1] < separacion:
            continue
        momentos.append(t)
    return momentos


def _puestos_suscribete(dur_final: float, pct: float, menciones: list[float], tope: int = 3,
                        separacion: float = 120.0) -> list[tuple[float, str]]:
    """Dónde van las tarjetas de «suscríbete» (segundos del vídeo YA montado) y cuál es cuál: "mencion" | "fija".
    - Una por cada vez que DICES «suscríbete» (`menciones`, ya en tiempo final): sale justo cuando lo pides.
    - Nunca dos a menos de `separacion` (2 min: el autor, 2026-09-24 — con 30 s salían dos casi seguidas).
    - La FIJA del `pct` (60 %) del vídeo solo si no hay una tuya a menos de `separacion`: si lo acabas de pedir, sobra.
    - Como mucho `tope` en total (la fija, si va, cuenta como una)."""
    tuyas: list[float] = []
    for t in sorted(menciones):
        if all(abs(t - a) >= separacion for a in tuyas):
            tuyas.append(t)
    fija = dur_final * pct
    cerca = [a for a in tuyas if abs(fija - a) < separacion]
    if not cerca:
        return sorted([(t, "mencion") for t in tuyas[: max(0, tope - 1)]] + [(fija, "fija")])
    elegidas = tuyas[:tope]
    if not any(abs(fija - a) < separacion for a in elegidas):        # (la que tapa a la fija no puede quedarse fuera)
        elegidas = sorted(elegidas[: tope - 1] + [min(cerca, key=lambda a: abs(fija - a))])
    return [(t, "mencion") for t in elegidas]


ZOOM_MARGEN = 0.05      # aire alrededor de TODO lo que hace el ratón (fracción del lienzo): la flecha, lo que subrayas, un menú
ZOOM_MAX = 0.80         # el encuadre crece como mucho hasta aquí (un zoom de 1,25×) para que quepa todo; más grande ya no es zoom
ZOOM_ANTES_S = 30.0     # la última posición del ratón ANTES de un tramo cuenta si es de hace menos de esto (sigue ahí)


def _raton_en(cursor: list[tuple[float, float, float]], t0: float, t1: float,
              antes_s: float = ZOOM_ANTES_S) -> list[tuple[float, float, float]]:
    """Por dónde pasa el ratón en [t0, t1] (segundos del ORIGINAL): [(segundo, x, y)] de cada posición medida ahí, más la
    última de antes, puesta en `t0` (hasta que se mueve, sigue ahí): cuenta si es de hace menos de `antes_s` o si en el
    tramo no se movió nada (entonces es lo único que se sabe)."""
    dentro = [p for p in cursor if t0 <= p[0] <= t1]
    previos = [p for p in cursor if p[0] < t0]
    if previos and (not dentro or t0 - previos[-1][0] <= antes_s):
        dentro.insert(0, (t0, previos[-1][1], previos[-1][2]))
    return dentro


def encuadre_zoom(puntos: list[tuple[float, float]], w0: float, r: float = 1.0, camara: dict | None = None,
                  margen: float = ZOOM_MARGEN, w_max: float = ZOOM_MAX) -> tuple[float, float, float, float] | None:
    """El encuadre FIJO de un «pantalla_zoom» que enseña TODO lo que hace el ratón en ese tramo (el autor, 2026-09-24:
    «cuando se haga un zoom, se debe asegurar al 100 % de que se vea en su totalidad todos los movimientos del mouse en
    ese tiempo»): contiene todos los `puntos` (x, y) con `margen` alrededor, no se sale del lienzo y no enseña tu CÁMARA.
    El más cerrado posible (desde `w0`, el zoom de siempre, hasta `w_max`) y, a igual tamaño, con el ratón lo más
    centrado posible. `r` = alto/ancho del encuadre en fracciones del lienzo (1 con una grabación 16:9).
    Devuelve (x, y del centro, w, h) o None si no existe: el ratón recorre demasiada pantalla (o se mueve pegado a la
    cámara) y ese tramo tiene que ir en «pantalla» entera, donde se ve todo.
    Por qué FIJO y no siguiendo al ratón (lo que se probó antes el mismo día): con el ratón real de su vídeo el encuadre
    que lo seguía llegaba tarde (se le escapaba 4 de 19 veces) y se movía sin parar (36 cambios de rumbo en 46 s); un
    encuadre quieto se lee mejor y la garantía es exacta."""
    if not puntos:
        return None
    xs, ys = [p[0] for p in puntos], [p[1] for p in puntos]
    bx0, bx1 = max(0.0, min(xs) - margen), min(1.0, max(xs) + margen)
    by0, by1 = max(0.0, min(ys) - margen), min(1.0, max(ys) + margen)
    ox, oy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2          # el centro de lo que hace el ratón
    eps = 0.004
    w = w0
    while w <= max(w_max, w0) + 1e-9:
        h = w * r
        # los centros con los que el encuadre contiene todo sin salirse del lienzo
        x_lo, x_hi = max(w / 2, bx1 - w / 2), min(1 - w / 2, bx0 + w / 2)
        y_lo, y_hi = max(h / 2, by1 - h / 2), min(1 - h / 2, by0 + h / 2)
        if h <= 1.0 and x_lo <= x_hi + 1e-9 and y_lo <= y_hi + 1e-9:
            trozos = [(x_lo, x_hi, y_lo, y_hi)]
            if camara:          # sin enseñar la cámara: el encuadre entero a su izquierda, a su derecha, encima o debajo
                cx0 = float(camara["x"]) - w / 2 - eps
                cx1 = float(camara["x"]) + float(camara["w"]) + w / 2 + eps
                cy0 = float(camara["y"]) - h / 2 - eps
                cy1 = float(camara["y"]) + float(camara["h"]) + h / 2 + eps
                trozos = [(x_lo, min(x_hi, cx0), y_lo, y_hi), (max(x_lo, cx1), x_hi, y_lo, y_hi),
                          (x_lo, x_hi, y_lo, min(y_hi, cy0)), (x_lo, x_hi, max(y_lo, cy1), y_hi)]
            buenos = [(min(max(ox, a), b), min(max(oy, c), d)) for a, b, c, d in trozos
                      if a <= b + 1e-9 and c <= d + 1e-9]
            if buenos:
                x, y = min(buenos, key=lambda c: abs(c[0] - ox) + abs(c[1] - oy))
                w, h = round(w, 4), round(h, 4)
                return (round(min(max(x, w / 2), 1 - w / 2), 5), round(min(max(y, h / 2), 1 - h / 2), 5), w, h)
        w += 0.01
    return None


def encuadres_zoom(segs: list[tuple[float, float]], modos: list[str], cursor: list[tuple[float, float, float]],
                   rect: dict | None, camara: dict | None, src_w: int, src_h: int
                   ) -> tuple[list[str], list[dict | None], list[dict]]:
    """«Pantalla con zoom» SOLO donde se ve todo lo que hace el ratón (`encuadre_zoom`). Por cada tramo «pantalla_zoom»
    con datos del ratón: {"w", "h", "puntos": [(0, x, y), (dur, x, y)]} = su encuadre fijo (el render lo usa tal cual:
    `render.zoom_movil_fx`); si ningún encuadre lo enseña todo, el tramo pasa a «pantalla» (la pantalla entera). Devuelve
    (modos corregidos, encuadres, [{"t", "fin" (segundos del vídeo montado), "x", "y" (lo que recorre el ratón)}] de los
    que cambiaron, para el log). Sin datos del ratón en la grabación (o en ese tramo), el zoom de antes (`None`)."""
    modos = list(modos)
    encuadres: list[dict | None] = [None] * len(segs)
    cambios: list[dict] = []
    if not rect or not cursor:
        return modos, encuadres, cambios
    r = (src_w * 9) / (src_h * 16)                          # alto/ancho de un encuadre 16:9, en fracciones del lienzo
    w0 = float(rect["w"])
    if camara and float(camara["y"]) > 0.5:
        # el zoom de siempre, algo menos alto que el hueco que deja la cámara encima: así también cabe por encima de ella
        w0 = min(w0, (float(camara["y"]) - 0.012) / r)
    acum = 0.0
    for i, ((a, b), m) in enumerate(zip(segs, modos)):
        if m == "pantalla_zoom":
            puntos = _raton_en(cursor, a, b)
            e = encuadre_zoom([(x, y) for _t, x, y in puntos], w0, r, camara) if puntos else None
            if e:
                x, y, w, h = e
                encuadres[i] = {"w": w, "h": h, "puntos": [(0.0, x, y), (round(b - a, 3), x, y)]}
            elif puntos:
                modos[i] = "pantalla"
                xs, ys = [p[1] for p in puntos], [p[2] for p in puntos]
                cambios.append({"t": round(acum, 1), "fin": round(acum + b - a, 1),
                                "x": (round(min(xs), 2), round(max(xs), 2)), "y": (round(min(ys), 2), round(max(ys), 2))})
        acum += b - a
    return modos, encuadres, cambios


def _ken_burns_sin_tapar_raton(segs: list[tuple[float, float]], modos: list[str], ken_burns: list[int],
                               cursor: list[tuple[float, float, float]], encuadres: list[dict | None], cfg,
                               margen: float = 0.01) -> tuple[list[int], int]:
    """El movimiento suave (Ken Burns) acerca la imagen hasta un 6 % y la desliza de lado: en una escena de PANTALLA (o
    de zoom a la pantalla) se quita si en algún momento dejaría fuera un punto por donde pasa el ratón — la misma regla
    del 100 % que el zoom (p. ej. el ratón en las pestañas del navegador, arriba del todo). Se mira cada posición del
    ratón con la ventana de ESE instante (`render.ken_burns_ventana`, la fórmula del render), con `margen` para la flecha
    entera (mide ~0,017 del alto; la posición es su centro). Devuelve (Ken Burns corregido, cuántas escenas se quedaron
    sin él)."""
    kb = list(ken_burns)
    if not cursor:
        return kb, 0
    desde, total = render._ken_burns_escenas([max(0.10, b - a) for a, b in segs], modos, kb)
    quitadas, i, n = 0, 0, min(len(segs), len(modos), len(kb))

    def tapa(k: int) -> bool:
        a, b = segs[k]
        enc = encuadres[k] if k < len(encuadres) else None
        if modos[k] == "pantalla_zoom" and not enc:
            return False                                    # zoom de antes (sin saber dónde está el ratón): nada que mirar
        for t, x, y in _raton_en(cursor, a, b):
            v = render.ken_burns_ventana((desde.get(k, 0.0) + (t - a)) / max(0.1, total.get(k, b - a)), kb[k], cfg)
            if not v:
                return False
            if enc:                                         # a coordenadas del encuadre del zoom
                (_t, cx, cy), w, h = enc["puntos"][0], enc["w"], enc["h"]
                x, y = (x - (cx - w / 2)) / w, (y - (cy - h / 2)) / h
            # fuera = lo recorta un borde de la ventana que ya no es el del fotograma (al principio es el fotograma entero)
            if ((v[0] > 1e-6 and x < v[0] + margen) or (v[2] < 1 - 1e-6 and x > v[2] - margen)
                    or (v[1] > 1e-6 and y < v[1] + margen) or (v[3] < 1 - 1e-6 and y > v[3] - margen)):
                return True
        return False

    while i < n:
        if not kb[i] or modos[i] not in ("pantalla", "pantalla_zoom"):
            i += 1
            continue
        j = i
        while j + 1 < n and modos[j + 1] == modos[i] and kb[j + 1] == kb[i]:
            j += 1
        if any(tapa(k) for k in range(i, j + 1)):
            for k in range(i, j + 1):
                kb[k] = 0
            quitadas += 1
        i = j + 1
    return kb, quitadas


def _limites_de_frase(words: list[dict], a: float, b: float, audio=None, nivel: float | None = None,
                      min_pausa: float = 0.3) -> list[tuple[float, int]]:
    """Dónde acaba una frase dentro de [a, b] (segundos del ORIGINAL), con su «fuerza»: 2 = final de frase claro (la
    palabra de antes cierra con . ? ! o la pausa dura ≥ 0,6 s), 1 = pausa corta. Las pausas salen del AUDIO (silencio
    real de al menos `min_pausa` s entre dos tramos con voz): Whisper pega las palabras unas a otras aunque haya una pausa
    (alarga la de antes por encima del silencio: «y[249,66-250,66]»), así que por sus huecos casi no se veía ninguna. Nunca
    tras una palabra que deja la frase abierta («que», «de», «sus», «la»…: `voz._ABIERTAS`) — ahí es una duda, no un
    final. Sin audio, los huecos de Whisper (≥ 0,25 s)."""
    from . import voz as _voz
    ws = sorted((w for w in words if a - 2.0 <= float(w.get("start", 0)) <= b + 1.0 and str(w.get("word", "")).strip()),
                key=lambda w: float(w["start"]))
    huecos: list[tuple[float, float]] = []
    if audio is not None and nivel is not None:
        isl = _voz.islas(audio, a, b, nivel)
        huecos = [(x.fin, y.ini) for x, y in zip(isl, isl[1:]) if y.ini - x.fin >= min_pausa]
    else:
        huecos = [(float(w1["end"]), float(w2["start"])) for w1, w2 in zip(ws, ws[1:])
                  if float(w2["start"]) - float(w1["end"]) >= 0.25]
    out = []
    for h0, h1 in huecos:
        t = (h0 + h1) / 2
        if not a < t < b:
            continue
        previas = [w for w in ws if float(w["start"]) < h0 + 0.05]
        if not previas:
            continue
        crudo = str(previas[-1]["word"]).strip()
        if _voz._frase_abierta(previas[-1]):
            continue                                           # «tienen sus | particularidades»: a media frase
        fuerza = 2 if (crudo[-1:] in ".?!" or h1 - h0 >= 0.6) else 1
        out.append((round(t, 3), fuerza))
    return out


def parte_escenas_largas(segs: list[tuple[float, float]], modos: list[str], encuadres: list[dict | None],
                         words: list[dict], actividad: list[dict], cursor: list[tuple[float, float, float]],
                         rect: dict | None, camara: dict | None, src_w: int, src_h: int, max_s: float = 60.0,
                         audio=None, nivel: float | None = None
                         ) -> tuple[list[tuple[float, float]], list[str], list[dict | None], list[str]]:
    """Ninguna escena de más de `max_s` segundos sin un cambio de plano (el autor, 2026-09-25: 3,4 min seguidos de pantalla
    en su vídeo con la vista IA). Dentro de la escena larga se mete un cambio CORTO (5-12 s) que empieza y acaba ENTRE
    FRASES (`_limites_de_frase`), hacia los ¾ de `max_s`, y se repite mientras quede un trozo largo:
    - escena de «pantalla»: tu CARA si en ese rato no pasa nada en pantalla (ni actividad ni ratón: no tapa nada que
      enseñes); si estás enseñando algo con el ratón, un ZOOM que lo enseña todo (`encuadre_zoom`, la regla del 100 %);
    - escena de «pantalla_zoom»: tu cara si no pasa nada, si no la pantalla entera (alejarse para dar contexto);
    - escena de «cara»: la PANTALLA entera (mejor un rato en que algo se mueve en ella).
    Nunca parte a menos de 2 s de un borde ni cruza un corte. Devuelve (tramos, modos, encuadres, notas para el log)."""
    segs, modos, encuadres = list(segs), list(modos), list(encuadres)
    notas: list[str] = []
    if not max_s or max_s <= 0 or not segs:
        return segs, modos, encuadres, notas
    r = (src_w * 9) / (src_h * 16)
    w0 = float((rect or {}).get("w", 0.66))
    if camara and float(camara["y"]) > 0.5:
        w0 = min(w0, (float(camara["y"]) - 0.012) / r)

    def activo(u: float, v: float) -> bool:
        return (any(float(x["inicio"]) < v + 1.0 and float(x["fin"]) > u - 1.0 for x in actividad or [])
                or any(u - 1.0 <= t <= v + 1.0 for t, _x, _y in cursor or []))

    imposibles: set[tuple[float, str]] = set()
    for _vuelta in range(40):
        # la primera escena (tramos seguidos del mismo modo) que se pasa de largo
        i, hallada = 0, None
        while i < len(segs):
            j = i
            while j + 1 < len(segs) and modos[j + 1] == modos[i]:
                j += 1
            largo = sum(b - a for a, b in segs[i:j + 1])
            if (modos[i] in ("pantalla", "pantalla_zoom", "cara") and largo > max_s
                    and (segs[i][0], modos[i]) not in imposibles):
                hallada = (i, j, largo)
                break
            i = j + 1
        if not hallada:
            break
        i, j, largo = hallada
        modo = modos[i]
        objetivo = 0.75 * max_s                                  # hacia los ¾ del máximo, desde el principio de la escena
        cands = []                                  # (preferencia, fuerza, distancia, k, u, nº, v, modo_nuevo, encuadre)
        acum = 0.0
        for k in range(i, j + 1):
            a, b = segs[k]
            limites = _limites_de_frase(words, a, b, audio, nivel)
            for u, fu in limites:
                pos = acum + (u - a)
                if pos < 15.0 or pos > largo - 15.0 or u - a < 2.0:
                    continue
                fines = [(v, fv) for v, fv in limites if u + 5.0 <= v <= u + 12.0 and b - v >= 2.0]
                if not fines:
                    continue
                v, fv = max(fines, key=lambda x: (x[1], -x[0]))          # el primer final de frase «de verdad»
                mov = activo(u, v)
                nuevo, enc = None, None
                if modo == "cara":
                    nuevo = "pantalla"
                elif not mov and camara:
                    nuevo = "cara"
                elif modo == "pantalla":
                    pts = [(x, y) for t, x, y in (cursor or []) if u - 0.5 <= t <= v + 0.5]
                    e = encuadre_zoom(pts, w0, r, camara) if pts else None
                    if e:
                        nuevo = "pantalla_zoom"
                        enc = {"w": e[2], "h": e[3], "puntos": [(0.0, e[0], e[1]), (round(v - u, 3), e[0], e[1])]}
                else:                                                     # pantalla_zoom con cosas moviéndose
                    nuevo = "pantalla"
                if nuevo:
                    pref = 0 if (modo != "cara" or mov) else 1               # (en «cara», mejor si algo se mueve)
                    lejos = 0 if abs(pos - objetivo) <= 20.0 else 1          # (mejor cerca de los ¾ del máximo)
                    # primero un final de frase CLARO (punto o pausa larga), luego cerca de los ¾, luego lo demás
                    cands.append((-min(fu, fv), lejos, pref, abs(pos - objetivo), k, u, len(cands), v, nuevo, enc))
            acum += b - a
        if not cands:
            imposibles.add((segs[i][0], modo))
            continue
        _f, _l, _p, _d, k, u, _n, v, nuevo, enc = min(cands)
        a, b = segs[k]
        viejo_enc = encuadres[k]

        def trozo(d: float):
            return ({**viejo_enc, "puntos": [(0.0, *viejo_enc["puntos"][0][1:]), (round(d, 3), *viejo_enc["puntos"][0][1:])]}
                    if viejo_enc else None)

        segs[k:k + 1] = [(a, round(u, 3)), (round(u, 3), round(v, 3)), (round(v, 3), b)]
        modos[k:k + 1] = [modo, nuevo, modo]
        encuadres[k:k + 1] = [trozo(u - a), enc, trozo(b - v)]
        que = {"cara": "tu cara", "pantalla": "la pantalla entera", "pantalla_zoom": "un zoom a lo que haces con el ratón"}
        notas.append(f"escena de {modo} de {int(largo) // 60}:{int(largo) % 60:02d} sin cambios: {que[nuevo]} durante "
                     f"{v - u:.0f}s (entre frases)")
    return segs, modos, encuadres, notas


_CTAS = ("suscribete", "like")


def _tramos_de_modo(segs: list[tuple[float, float]], modos: list[str], modo: str) -> list[tuple[float, float]]:
    """Dónde está el modo de cámara `modo` en el vídeo YA MONTADO (segundos finales), p. ej. las escenas «explicativa»."""
    out, acum = [], 0.0
    for (a, b), m in zip(segs, modos):
        if m == modo:
            if out and abs(out[-1][1] - acum) < 1e-6:
                out[-1] = (out[-1][0], acum + (b - a))
            else:
                out.append((acum, acum + (b - a)))
        acum += b - a
    return out


def _explicativas_finales(camara_plan: list[dict], segs: list[tuple[float, float]],
                          modos: list[str]) -> list[dict]:
    """Las escenas «explicativa» en el vídeo YA MONTADO, con su texto: [{"a", "b", "termino", "definicion"}] (segundos
    finales). Mismo reparto que `_tarjetas_explicativa` (cada tramo cae entero dentro de UNA entrada del plan); los trozos
    seguidos de la misma entrada (partidos por un corte de relleno) se juntan en uno."""
    explicativas = [c for c in camara_plan
                    if c.get("modo") == "explicativa" and c.get("termino") and c.get("definicion")]
    out: list[dict] = []
    acum = 0.0
    for (s, e), m in zip(segs, modos):
        if m == "explicativa":
            k = next((k for k, c in enumerate(explicativas)
                      if float(c["inicio"]) - 0.05 <= s and e <= float(c["fin"]) + 0.05), None)
            if k is not None:
                if out and out[-1]["k"] == k and abs(out[-1]["b"] - acum) < 1e-6:
                    out[-1]["b"] = acum + (e - s)
                else:
                    out.append({"k": k, "a": acum, "b": acum + (e - s), "termino": str(explicativas[k]["termino"]),
                                "definicion": str(explicativas[k]["definicion"])})
        acum += e - s
    return [{c: v for c, v in x.items() if c != "k"} for x in out]


# palabras que no dicen de QUÉ trata una definición («ocurre cuando…», «el mismo…»): fuera al comparar (las de unión, como
# «de» o «la», ya las quita `ritmo._tokens`)
_VACIAS_DEF = {"cuando", "como", "donde", "mismo", "misma", "mismos", "mismas", "este", "esta", "estos", "estas", "ese",
               "esa", "esos", "esas", "hay", "son", "ser", "otro", "otra", "otros", "otras", "todo", "toda", "todos", "todas",
               "cada", "entre", "sobre", "sin", "pero", "tambien", "cual", "cuales", "uno", "dos", "tres", "varios",
               "varias", "mucho", "mucha", "muchos", "muchas", "tan", "hace", "hacen", "puede", "pueden", "ocurre",
               "sucede", "pasa", "significa", "consiste", "cuanto", "tus", "nuestro", "nuestra", "no", "si", "ya"}


def _raices(texto) -> set[str]:
    """Lo que DICE un texto, para comparar dos: sus palabras con contenido sin tildes ni mayúsculas, en singular y
    recortadas a su raíz (5 letras: «compiten» y «compitiendo» cuentan como la misma); las cifras, tal cual."""
    from . import ritmo as _ritmo
    out: set[str] = set()
    for c in _ritmo._tokens(texto):
        if c in _ritmo._VACIAS or c in _VACIAS_DEF or not any(ch.isalnum() for ch in c):
            continue
        if c[0].isdigit():
            out.add(c)
            continue
        if len(c) < 2:
            continue
        if len(c) > 3 and c.endswith("s"):
            c = c[:-1]                                      # plural: «URLs» = «URL»
        out.add(c[:5])
    return out


def _repite_explicativa(tarjeta: dict, expl: dict) -> str | None:
    """¿La tarjeta dice lo mismo que la escena «explicativa» que está en pantalla? (el autor, 2026-09-27: «sale la escena de
    definición… pero a la vez sale la tarjeta de definición, que dice prácticamente lo mismo… a menos que añada algo
    distinto»). Repite si es la definición del MISMO término (o de una parte de él: «Canibalización» en la escena de
    «Canibalización SEO»), o si la mayoría de sus palabras ya están en el término o la definición. Añade algo (y se queda)
    si trae una cifra que la escena no dice o si la mitad o más de lo que dice es nuevo. -> el motivo, o None."""
    termino = _raices(expl.get("termino"))
    escena = termino | _raices(expl.get("definicion"))
    titulo = _raices(tarjeta.get("texto"))
    todo = titulo | _raices(tarjeta.get("extra"))
    if not todo or not escena:
        return None                                          # nada que comparar: no se toca
    if any(c[0].isdigit() and c not in escena for c in todo):
        return None                                          # una cifra que la escena no dice: eso es nuevo
    if tarjeta.get("tipo") == "definicion" and titulo and termino and (titulo <= termino or termino <= titulo):
        return "define el mismo término"
    nuevas = todo - escena
    if len(nuevas) / len(todo) < 0.5:
        return "dice lo mismo que la definición"
    return None


def _sin_repetir_explicativa(tarjetas: list[dict], explicativas: list[dict],
                             tras: float = 2.0) -> tuple[list[dict], list[str]]:
    """Fuera las tarjetas del contenido que salen DURANTE una escena «explicativa» (o hasta `tras` s después: se verían «a
    la vez») y repiten lo que ella ya dice (`_repite_explicativa`). Las que añaden algo distinto se quedan. Visto en su
    vídeo de Canibalización SEO (2026-09-27): la escena del 1:51 con «Canibalización SEO» y su definición, y en el 1:52 la
    tarjeta de definición del mismo término. `tarjetas` en tiempo FINAL. -> (tarjetas, notas para el log)"""
    def mmss(t: float) -> str:
        return f"{int(t) // 60}:{int(t) % 60:02d}"

    out: list[dict] = []
    notas: list[str] = []
    for t in tarjetas:
        if t.get("tipo") in _CTAS or not explicativas:
            out.append(t)
            continue
        t0 = float(t["t"])
        t1 = t0 + float(t.get("duracion", 3.5))
        motivo, escena = None, None
        for x in explicativas:
            if min(t1, x["b"] + tras) - max(t0, x["a"]) > 0.25:
                motivo = _repite_explicativa(t, x)
                if motivo:
                    escena = x
                    break
        if motivo and escena is not None:
            notas.append(f"«{str(t.get('texto') or t.get('tipo'))[:40]}» del {mmss(t0)} no sale: {motivo} de la escena "
                         f"«explicativa» de «{escena['termino'][:40]}» ({mmss(escena['a'])}–{mmss(escena['b'])})")
            continue
        out.append(t)
    return out, notas


def _encadena_tarjetas(tarjetas: list[dict], solape: float = 0.25) -> tuple[list[dict], int]:
    """Tarjetas del contenido SEGUIDAS: la nueva REEMPLAZA a la anterior (el autor, 2026-09-26: «versión en Argentina… sale la
    de Estados Unidos encima y la de Argentina todavía sigue conviviendo»). Van en la misma esquina, así que cada una
    termina `solape` s después de que entra la siguiente: su fundido de salida queda debajo del de entrada de la nueva, sin
    que convivan. Las de «suscríbete»/«like» no entran aquí (ya ceden el sitio: `_aparta_ctas`). -> (tarjetas, cuántas
    se acortaron)."""
    contenido = sorted((dict(t) for t in tarjetas if t.get("tipo") not in _CTAS), key=lambda t: float(t["t"]))
    n = 0
    for cur, sig in zip(contenido, contenido[1:]):
        fin = float(cur["t"]) + float(cur.get("duracion", 3.5))
        if float(sig["t"]) < fin:
            cur["duracion"] = round(max(0.3, float(sig["t"]) - float(cur["t"]) + solape), 3)
            n += 1
    return contenido + [dict(t) for t in tarjetas if t.get("tipo") in _CTAS], n


def _marca_estimados(tarjetas: list[dict], words: list[dict]) -> tuple[list[dict], int]:
    """Las tarjetas de DATO cuya cifra es un estimado tuyo («muy posiblemente casi que el 90 %») salen como tal: «≈90%»
    y «Mi estimación» (`tarjetas._dato`). Lo marca el director (`estimado`) o, si no lo marcó, las palabras de duda de esa
    frase (`ritmo.es_estimado`). `tarjetas` en tiempo del ORIGINAL. -> (tarjetas, cuántas son estimado)."""
    from . import ritmo as _ritmo
    out, n = [], 0
    for t in tarjetas or []:
        t = dict(t)
        if t.get("tipo") == "dato" and (t.get("estimado") or _ritmo.es_estimado(t.get("texto"), float(t["t"]), words)):
            t["estimado"] = True
            n += 1
        out.append(t)
    return out, n


def _fuera_de_bordes(items: list[dict], dur_final: float, margen: float, max_retrasar: float | None = None,
                     max_adelantar: float = 2.5) -> tuple[list[dict], list[str]]:
    """Ninguna tarjeta (ni imagen) en los primeros ni en los últimos `margen` segundos del vídeo (el autor, 2026-09-27: «los
    primeros cinco segundos… que no haya ninguna tarjeta porque queda como raro… y los últimos cinco segundos tampoco»).
    La que cae ahí se corre al borde si no pisa a su vecina: al principio, hasta el segundo `margen` (sale justo DESPUÉS de
    decirlo: vale como recordatorio; `max_retrasar`, por defecto todo el margen); al final, adelantándose como mucho
    `max_adelantar` s (antes de decirlo se vería como un adelanto). Si no, no sale. `margen` 0 = sin regla. Visto en sus
    vídeos del 27-sep: la definición de «Vibe Coding» en el 0,3 s y el 0,9 s, y un «suscríbete» al final.
    -> (items, notas para el log)"""
    if margen <= 0:
        return list(items), []
    ini, fin = margen, dur_final - margen
    max_retrasar = margen if max_retrasar is None else max_retrasar
    orden = sorted((dict(x) for x in items), key=lambda x: float(x["t"]))
    out: list[dict] = []
    notas: list[str] = []
    for k, x in enumerate(orden):
        t0, d = float(x["t"]), float(x.get("duracion", 3.5))
        s = min(max(t0, ini), fin - d)
        if abs(s - t0) > 0.05:
            nombre = str(x.get("texto") or x.get("busqueda") or x.get("tipo") or "tarjeta")[:40]
            sig = orden[k + 1] if k + 1 < len(orden) else None
            pisa = bool(out and s < float(out[-1]["t"]) + float(out[-1].get("duracion", 3.5))) or \
                (sig is not None and s + d > float(sig["t"]))
            if s < ini - 1e-6 or (s - t0 > max_retrasar + 1e-6) or (t0 - s > max_adelantar + 1e-6) or pisa:
                notas.append(f"«{nombre}» del {t0:.1f}s no sale: caería en los {margen:g} s del principio o del final")
                continue
            notas.append(f"«{nombre}» del {t0:.1f}s al {s:.1f}s (fuera de los {margen:g} s del principio y del final)")
            x["t"] = round(s, 2)
        out.append(x)
    return out, notas


def _aparta_ctas(tarjetas: list[dict], dur_final: float, ocupados: list[tuple[float, float]] = (),
                 margen: float = 0.6, bordes: float = 0.0) -> tuple[list[dict], list[str]]:
    """Las tarjetas de «suscríbete» y «like» CEDEN el sitio (el autor, 2026-09-24: la del 60 % salió encima de la 3.ª
    tarjeta «punto»): nunca encima de una tarjeta del contenido ni de `ocupados` (las escenas «explicativa», que usan esa
    misma esquina), ni de otra de las suyas. Se mueven al hueco libre MÁS CERCANO (antes o después, con `margen` de aire);
    si no hay ninguno en todo el vídeo, esa no sale. Devuelve (tarjetas, notas para el log)."""
    contenido = [t for t in tarjetas if t.get("tipo") not in _CTAS]
    ocup = [(float(t["t"]), float(t["t"]) + float(t.get("duracion", 3.5))) for t in contenido] + list(ocupados)
    out, notas = list(contenido), []

    def libre(s: float, d: float) -> bool:
        return (s >= bordes and s + d <= dur_final - max(0.2, bordes)
                and all(s + d + margen <= a or s >= b + margen for a, b in ocup))

    for c in sorted((t for t in tarjetas if t.get("tipo") in _CTAS), key=lambda t: float(t["t"])):
        d, t0 = float(c.get("duracion", 4.0)), float(c["t"])
        cands = ([t0] + [b + margen for _a, b in ocup] + [a - margen - d for a, _b in ocup]
                 + [bordes, dur_final - max(0.2, bordes) - d])   # (los bordes de los 5 s también son huecos)
        buenos = [s for s in cands if libre(s, d)]
        if not buenos:
            notas.append(f"«{c.get('tipo')}» del {t0:.0f}s no cabe en ningún hueco libre: no sale")
            continue
        s = min(buenos, key=lambda x: (abs(x - t0), x < t0))           # el más cercano; a igual distancia, después
        if abs(s - t0) > 0.05:
            notas.append(f"«{c.get('tipo')}» del {t0:.1f}s al {s:.1f}s (cede el sitio a una tarjeta del contenido)")
        out.append({**c, "t": round(s, 2)})
        ocup.append((s, s + d))
    return sorted(out, key=lambda t: float(t["t"])), notas


def _segmentos_con_camara(segs: list[tuple[float, float]], camara: list[dict],
                          modo_defecto: str = "pantalla"
                          ) -> tuple[list[tuple[float, float]], list[str]]:
    """Cada tramo de `segs` (lo que sobrevive a los recortes) partido en
    trozos más pequeños donde CAMBIA el modo de cámara, para que
    render.render_merged le aplique a cada trozo su propio encuadre.

    El director no cubre cada segundo (solo marca dónde CAMBIA el encuadre,
    ver director.py) — así que el hueco antes del primer tramo usa
    `modo_defecto` y los huecos entre tramos heredan el modo del tramo
    anterior (no hubo cambio marcado = sigue igual)."""
    cam = sorted((c for c in camara or [] if float(c["fin"]) > float(c["inicio"])),
                key=lambda c: float(c["inicio"]))

    def modo_en(t: float) -> str:
        actual = modo_defecto
        for c in cam:
            if float(c["inicio"]) <= t:
                actual = c["modo"]
            else:
                break
        return actual

    fronteras = sorted({round(float(c["inicio"]), 3) for c in cam}
                       | {round(float(c["fin"]), 3) for c in cam})

    out_segs: list[tuple[float, float]] = []
    out_modos: list[str] = []
    for s0, e0 in segs:
        cortes = [s0] + [f for f in fronteras if s0 < f < e0] + [e0]
        for a, b in zip(cortes, cortes[1:]):
            if b - a < 0.05:
                continue
            modo = modo_en((a + b) / 2)
            # suelda con el trozo anterior si es CONTIGUO (mismo recorte) y
            # tiene el mismo modo — una frontera "inicio"/"fin" del plan no
            # significa que el modo cambió de verdad a los dos lados
            if (out_segs and out_modos[-1] == modo
                    and abs(out_segs[-1][1] - a) < 1e-6):
                out_segs[-1] = (out_segs[-1][0], b)
            else:
                out_segs.append((a, b))
                out_modos.append(modo)
    return out_segs, out_modos


def _en_actividad(t: float, actividad: list[dict]) -> bool:
    return any(float(v["inicio"]) <= t < float(v["fin"]) for v in actividad)


def _fuerza_zoom_en_actividad(camara: list[dict], actividad: list[dict],
                              min_solape_s: float = 3.0) -> list[dict]:
    """Red de seguridad: si el director dejó "cara" en un tramo donde `clipper/movimiento.py`
    detectó actividad SOSTENIDA de verdad en pantalla, esa parte se fuerza a "pantalla_zoom" —
    el autor lo pidió (2026-09-23): quiere la garantía de que un movimiento real en pantalla SIEMPRE
    se vea, sin depender de que el texto lo dejara claro. Parte el tramo "cara" justo donde hace
    falta (no toca los tramos que YA eran pantalla/pantalla_zoom/ambos: esos ya se ven bien).

    `min_solape_s`: solo cuenta una ventana de actividad si se solapa con el tramo "cara" AL
    MENOS esto — el autor lo pidió (2026-09-23), vio cambios de escena "muy rápidos" en un vídeo real
    (zoom→pantalla→zoom→cara en segundos): el zoom es "el último recurso", no algo que entre y
    salga por un roce de actividad de un par de segundos. Una ventana que no llega a este mínimo
    se IGNORA del todo (ese trozo se queda "cara", sin partirlo ni un poco)."""
    if not actividad:
        return camara
    out: list[dict] = []
    for c in camara:
        a0, a1 = float(c["inicio"]), float(c["fin"])
        if c.get("modo") != "cara" or a1 - a0 < 0.1:
            out.append(c)
            continue
        relevante = [v for v in actividad
                    if min(a1, float(v["fin"])) - max(a0, float(v["inicio"])) >= min_solape_s]
        if not relevante:
            out.append(c)
            continue
        fronteras = sorted({a0, a1} | {t for v in relevante
                                       for t in (float(v["inicio"]), float(v["fin"])) if a0 < t < a1})
        for x0, x1 in zip(fronteras, fronteras[1:]):
            nuevo = dict(c)
            nuevo["inicio"], nuevo["fin"] = round(x0, 2), round(x1, 2)
            if _en_actividad((x0 + x1) / 2, relevante):
                nuevo["modo"] = "pantalla_zoom"
            out.append(nuevo)
    return out


def _centros_pantalla_zoom(segs: list[tuple[float, float]], modos: list[str],
                          actividad: list[dict]) -> list[tuple[float, float] | None]:
    """Por cada tramo en modo "pantalla_zoom": el (x,y) de la ventana de actividad
    (`clipper/movimiento.py`) que MÁS se solapa con él — None si ninguna se solapa o si ninguna
    trae posición (recorte fijo de config.yaml para ese tramo). el autor lo pidió (2026-09-23):
    "pantalla_zoom" enseñaba SIEMPRE el mismo rectángulo — si lo que señalaba con el mouse caía
    fuera de esa zona, se perdía. Ver `render.centro_pantalla_zoom` (quien de verdad recorta)."""
    if not actividad:
        return [None] * len(segs)
    out: list[tuple[float, float] | None] = []
    for (s, e), modo in zip(segs, modos):
        if modo != "pantalla_zoom":
            out.append(None)
            continue
        mejor, mejor_solape = None, 0.0
        for v in actividad:
            if "x" not in v or "y" not in v:
                continue
            solape = min(e, float(v["fin"])) - max(s, float(v["inicio"]))
            if solape > mejor_solape:
                mejor, mejor_solape = (float(v["x"]), float(v["y"])), solape
        out.append(mejor)
    return out


def _tarjetas_explicativa(camara_plan: list[dict], segs: list[tuple[float, float]], modos: list[str],
                          marca: dict, ancho_out: int, alto_out: int,
                          out_dir: Path) -> list[Path | None]:
    """Por cada tramo de `segs` en modo "explicativa": el PNG de su tarjeta de término+definición
    (`tarjetas.dibuja_explicativa`), o None (los demás tramos, o uno cuya tarjeta no se pudo armar).
    `render.render_merged(explicativa_cards=…)` la pega DENTRO del propio tramo, así que no hace falta
    ningún tiempo: solo saber a qué entrada del plan pertenece cada tramo (los tramos salen de partir el
    plan por cada cambio de cámara y por cada corte, así que un tramo cae entero dentro de UNA entrada,
    y varios tramos de la misma escena comparten tarjeta). MISMO reparto 2/3+1/3 que render.py y
    `ejemplos._dos_tercios` (si cambia uno, tienen que cambiar los tres). Entradas sin término o definición
    ya llegan degradadas a "cara" por `director._valida` — no debería pasar aquí, pero se filtran igual."""
    out: list[Path | None] = [None] * len(segs)
    explicativas = [c for c in camara_plan
                    if c.get("modo") == "explicativa" and c.get("termino") and c.get("definicion")]
    if not explicativas or "explicativa" not in modos:
        return out
    from . import tarjetas as tarjetas_mod
    ancho_tarjeta = max(2, int(ancho_out * 2 / 3) // 2 * 2)
    out_dir.mkdir(parents=True, exist_ok=True)
    hechas: dict[int, Path] = {}
    for i, (s, e) in enumerate(segs):
        if i >= len(modos) or modos[i] != "explicativa":
            continue
        for k, c in enumerate(explicativas):
            if float(c["inicio"]) - 0.05 <= s and e <= float(c["fin"]) + 0.05:
                if k not in hechas:
                    p = out_dir / f"explicativa_{k:02d}.png"
                    tarjetas_mod.dibuja_explicativa(c["termino"], c["definicion"], marca,
                                                    ancho_tarjeta, alto_out).save(p)
                    hechas[k] = p
                out[i] = hechas[k]
                break
    return out


def _ken_burns_por_segmento(modos: list[str]) -> list[int]:
    """Por cada tramo "cara"/"pantalla"/"pantalla_zoom": si le toca Ken Burns (el pan/zoom sutil
    de render.py) y en qué dirección — decidido por CÓDIGO, nunca por Gemini (el autor, 2026-09-23):
    "cara" y "pantalla"/"pantalla_zoom" cuentan como dos familias APARTE, y dentro de cada una se
    alterna 1 de cada 2 en el orden en que aparecen (mitad con Ken Burns, mitad estático) — ni azar
    (no sería reproducible entre renders del mismo plan) ni "todo o nada" (el autor pidió variantes de
    verdad dentro del MISMO vídeo, no un interruptor global). "explicativa"/"ambos" nunca lo llevan
    (no se pidió, y en "explicativa" competiría visualmente con la tarjeta de texto).

    0 = sin Ken Burns; +1/-1 = con Ken Burns, deriva a la derecha/izquierda (también alterna, para
    que los tramos que sí lo llevan no se sientan todos iguales).

    Se reparte por ESCENA (los trozos SEGUIDOS del mismo modo), no por trozo suelto: una escena
    partida por un corte de relleno son dos trozos del mismo modo, y tienen que llevar los dos el
    mismo Ken Burns (`render._ken_burns_escenas` los une para que el zoom continúe donde iba) — si no,
    el movimiento aparecería y desaparecería en cada corte de una misma escena."""
    out = [0] * len(modos)
    contadores = {"cara": 0, "pantalla": 0}
    i = 0
    while i < len(modos):
        j = i
        while j + 1 < len(modos) and modos[j + 1] == modos[i]:
            j += 1
        m = modos[i]
        familia = "cara" if m == "cara" else "pantalla" if m in ("pantalla", "pantalla_zoom") else None
        if familia is not None:
            n = contadores[familia]
            contadores[familia] += 1
            if n % 2 == 1:                          # la 2ª, 4ª, 6ª... escena de su familia
                for k in range(i, j + 1):
                    out[k] = 1 if (n // 2) % 2 == 0 else -1
        i = j + 1
    return out


def _tiempo_final_capitulo(t: float, segs: list[tuple[float, float]]) -> float | None:
    """Como `_tiempo_final`, pero para CAPÍTULOS: si `t` cae justo en un
    hueco que se cortó, no se pierde el capítulo entero — se adelanta al
    inicio del siguiente tramo que sobrevivió (el tema seguía ahí, solo se
    cortó el relleno de en medio). None solo si ya no queda nada después."""
    directo = _tiempo_final(t, segs)
    if directo is not None:
        return directo
    acumulado = 0.0
    for a, b in segs:
        if a > t:
            return acumulado
        acumulado += b - a
    return None


def _capitulos_texto(capitulos: list[dict], segs: list[tuple[float, float]]) -> str:
    """Los capítulos del plan del director -> texto listo para la
    descripción de YouTube. Reubica cada uno según lo que sobrevive a los
    recortes y aplica las reglas de YouTube: el primero es 0:00, hace falta
    al menos 3, y 10 s de separación mínima entre cada uno (si no, YouTube
    ni los muestra)."""
    remapeados = []
    for c in sorted(capitulos or [], key=lambda c: c["t"]):
        t = _tiempo_final_capitulo(float(c["t"]), segs)
        titulo = str(c.get("titulo", "")).strip()
        if t is None or not titulo:
            continue
        if remapeados and t - remapeados[-1][0] < 10.0:
            continue
        remapeados.append((t, titulo))
    if len(remapeados) < 3:
        return ""
    lineas = []
    for i, (t, titulo) in enumerate(remapeados):
        if i == 0:
            lineas.append(f"0:00 {titulo}")
            continue
        m, sg = divmod(int(t), 60)
        h, m = divmod(m, 60)
        sello = f"{h}:{m:02d}:{sg:02d}" if h else f"{m}:{sg:02d}"
        lineas.append(f"{sello} {titulo}")
    return "\n".join(lineas)


def titulo_del_plan(plan: dict) -> str:
    """El título del vídeo: el que ELEGISTE en el panel (`titulo_elegido`, lo guarda `webapp.job_set_titulo` en el plan de
    esa versión) o, si no elegiste ninguno, el 🥇 de la IA."""
    sugeridos = [str(t).strip() for t in (plan.get("titulos_sugeridos") or []) if str(t or "").strip()]
    return str(plan.get("titulo_elegido") or "").strip() or (sugeridos[0] if sugeridos else "")


_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def nombre_video(fecha, tags=None, titulo: str = "") -> str:
    """El NOMBRE de un vídeo en el panel y al descargarlo (el autor, 2026-09-26: el nombre del archivo de OBS, «2026-09-24_20-33-39-3»,
    trae la fecha de GRABACIÓN y no dice de qué va): la fecha en que se RENDERIZÓ + dos palabras clave que lo definen — sus
    dos primeras etiquetas SEO (la primera es la más importante; la segunda, la siguiente que no repite a la primera). Sin
    etiquetas, el título. `fecha`: date/datetime, «2026-09-26» o un timestamp. Ej.: «26-sep-2026 · Search Console · IAs en
    inglés». '' si no hay fecha."""
    import datetime as _dt
    try:
        if isinstance(fecha, (int, float)):
            d = _dt.date.fromtimestamp(float(fecha))
        elif isinstance(fecha, str):
            d = _dt.date.fromisoformat(fecha[:10])
        else:
            d = fecha.date() if isinstance(fecha, _dt.datetime) else fecha
        dia = f"{d.day:02d}-{_MESES[d.month - 1]}-{d.year}"
    except (TypeError, ValueError, AttributeError, OSError):
        return ""
    elegidas: list[str] = []
    for t in tags or []:
        t = re.sub(r"\s+", " ", str(t or "")).strip()
        palabras = set(normalize(t).split())
        if not t or any(len(palabras & set(normalize(x).split())) * 2 >= max(1, len(palabras)) for x in elegidas):
            continue                                  # «Search Console» tras «Google Search Console»: repite
        elegidas.append(_corta(t, 32))
        if len(elegidas) == 2:
            break
    if not elegidas and str(titulo or "").strip():
        elegidas = [_corta(str(titulo).strip(), 44)]
    return " · ".join([dia] + elegidas)


def _contexto_director(contexto: str, work, reglas=None, revision: bool = False, nombres: str = "") -> str:
    """Lo que el DIRECTOR lee como contexto: el del canal (la idea de la que salió el vídeo YA NO se le pasa: el autor, 12-oct-2026, «confío más en la transcripción») + los NOMBRES
    PROPIOS del autor (`nombres`, de clipper/nombres.py: sus herramientas y marcas, para que las escriba SIEMPRE bien) + las
    REGLAS PERMANENTES del autor (clipper/reglas.py: su feedback general de vídeos anteriores, «no cortes mis risas»). Los Shorts
    siguen recibiendo el contexto de siempre. En una `revision` las reglas valen solo en lo que el comentario hace tocar."""
    from . import reglas as _reglas
    base = contexto
    if nombres:
        base = f"{base}\n\n{nombres}".strip()
    bloque = _reglas.texto_prompt(reglas, revision=revision)
    return f"{base}\n\n{bloque}".strip() if bloque else base


def _revisa_nombres(plan: dict, words: list, cfg, canal: str, dir_work, comentario: str = "", jid: str = "") -> None:
    """🛡 Los NOMBRES de todo lo que sale en pantalla (tarjetas, palabras clave, escenas, términos) se confirman ANTES de dibujar
    nada (clipper/nombres.py; el autor, 2026-10-03): la errata clara se corrige, el nombre mal oído que la IA reconoce se escribe
    bien, y lo que no se puede confirmar NO sale. Modifica `plan` y apunta el resultado en `plan["nombres"]`. Nunca rompe un vídeo."""
    try:
        from . import nombres as _nombres
        _nombres.revisa(plan, words, cfg, canal, dir_work, comentario=comentario, jid=jid)
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudieron revisar los nombres de lo que sale en pantalla ({exc}); sigo sin revisarlos.")


def campana_utm(base: str, tags=None, titulo: str = "") -> str:
    """El `utm_campaign` de los enlaces a tu web en la descripción (el autor, 2026-09-26: «para yo desde Google Analytics saber
    de dónde viene ese tráfico»): QUÉ vídeo, legible — la fecha de GRABACIÓN (sale del nombre de la grabación: la misma en
    todas las versiones del vídeo) + sus dos palabras clave, las mismas del NOMBRE del vídeo (`nombre_video`).
    Ej.: «2026-09-26-google-search-console-ias-en-ingles»."""
    from .enlaces import slug_utm
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(base or ""))
    fecha = m.group(1) if m else time.strftime("%Y-%m-%d")
    claves = (nombre_video(fecha, tags, titulo) or "").split(" · ")[1:]
    return slug_utm(" ".join([fecha] + claves))


def _corta(texto: str, n: int) -> str:
    """`texto` en ≤ `n` caracteres cortando en una palabra entera, sin acabar en una palabra de unión («…con»), con «…»."""
    if len(texto) <= n:
        return texto
    palabras = texto[:n].rsplit(" ", 1)[0].split()
    while len(palabras) > 1 and normalize(palabras[-1]).strip() in {"de", "del", "la", "las", "el", "los", "en", "con", "y",
                                                                     "o", "a", "al", "para", "por", "tu", "su", "un", "una"}:
        palabras.pop()
    return " ".join(palabras).rstrip(" ,.;:¿?¡!-") + "…"


def _es_fijo(r: dict) -> bool:
    """Un recorte que NO pasa por el tope de seguridad del % (`_limita_recortes`, que es para lo que propone la IA):
    los que hiciste tú a mano, los que pone el programa midiendo el audio (`clipper/voz.py`: silencio del arranque,
    tomas falsas, pausas, silencio del final) y los de la IA en los que no suena ni una palabra (`sin_voz`: quitar
    silencio no quita contenido; el tope tiraba primero los cortes MÁS GRANDES, justo un silencio de 20 min)."""
    return bool(r.get("manual") or r.get("auto") or r.get("sin_voz"))


def _plan_visto(plan: dict, dur: float, cfg) -> dict:
    """Copia del plan con SOLO los recortes que de verdad se aplicaron al renderizar esa versión: la red de seguridad
    del % máximo pudo descartar alguno de la IA, y eso NO lo vio el autor. Una mejora parte de lo que vio; si no, un
    corte que el render descartó «reaparecería» en la versión nueva (el tope de una mejora es más alto) sin que nadie lo pidiera."""
    import copy
    out = copy.deepcopy(plan)
    max_pct = float(plan.get("max_pct") or cfg.get_path("videos.recortes_max_pct", 0.5))
    ia = [r for r in (plan.get("recortes") or []) if not _es_fijo(r)]
    fijos = [r for r in (plan.get("recortes") or []) if _es_fijo(r)]
    out["recortes"] = sorted(_limita_recortes(ia, dur, max_pct) + fijos, key=lambda r: float(r["inicio"]))
    return out


def _huella_plan(plan: dict, dur: float | None = None, cfg=None) -> str:
    """Lo que hace que el vídeo (o lo que se publica con él) salga DISTINTO: los tramos que se ven, la cámara, las tarjetas,
    las imágenes, las ilustraciones y los textos de YouTube. Dos planes con la misma huella dan lo mismo. Sirve para saber si
    una mejora cambió algo: no cuenta el orden, ni redondeos de una décima, ni recortes solapados que dejan los mismos tramos."""
    def norm(x):
        if isinstance(x, float):
            return round(x, 1)
        if isinstance(x, dict):
            return {k: norm(v) for k, v in sorted(x.items()) if k != "cita"}      # (la cita solo sirve para anclar: no se ve)
        if isinstance(x, (list, tuple)):
            return sorted((norm(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False, default=str))
        return x
    if dur and cfg is not None:
        cortes = [[round(a, 1), round(b, 1)] for a, b in _segs_de_plan(plan, dur, cfg)]          # lo que de verdad se ve
    else:
        cortes = norm([[float(x["inicio"]), float(x["fin"]), bool(x.get("manual"))] for x in plan.get("recortes") or []])
    otros = {k: norm(plan.get(k)) for k in ("camara", "tarjetas", "imagenes", "ilustraciones", "palabras_clave", "escenas",
                                             "titulos_sugeridos", "miniatura_prompts", "miniatura_titulares", "capitulos",
                                             "gancho_descripcion",
                                             "articulo_relacionado", "hashtag")}
    return json.dumps({"tramos": cortes, **otros}, sort_keys=True, ensure_ascii=False, default=str)


def _segs_de_plan(plan: dict, dur: float, cfg) -> list[tuple[float, float]]:
    """Los tramos del ORIGINAL que se ven en el vídeo de ese plan: lo mismo que calcula el render (recortes de la
    IA con su tope de seguridad + los cortes manuales del autor). Sirve para que la IA de una revisión sepa qué
    ve exactamente el autor."""
    max_pct = float(plan.get("max_pct") or cfg.get_path("videos.recortes_max_pct", 0.5))
    ia = [r for r in (plan.get("recortes") or []) if not _es_fijo(r)]
    fijos = [r for r in (plan.get("recortes") or []) if _es_fijo(r)]
    return _aplica_recortes([(0.0, dur)], _limita_recortes(ia, dur, max_pct) + fijos, dur)


def process_video_pro(source: str, cfg=None, canal: str = "",
                      only_analyze: bool = False, version: int = 1,
                      plan_de: str | None = None,
                      cortes: list[tuple[float, float]] | None = None,
                      mejora: str | None = None,
                      camara_aparte: bool | None = None,
                      camara_cara: str | None = None) -> dict:
    """UN archivo de vídeo ya grabado (subido, no un directo) -> UN vídeo
    editado por el director: recorta relleno, quema las tarjetas del plan
    (ver `clipper/tarjetas.py`) y aplica el zoom dinámico cara/pantalla
    (ver `clipper/render.py`, `camara_rect`).

    `version` — "Reshuffle" desde el panel: >1 fuerza un análisis NUEVO del
    director (Gemini puede decidir distinto la segunda vez) y un render
    aparte que NO pisa al anterior, para poder comparar. Reusa la
    transcripción (`work` es el mismo para todas las versiones de un mismo
    archivo — eso no cambia entre corridas) pero cachea el plan del
    director y el vídeo de salida con su propio nombre por versión.

    `plan_de` + `cortes` — EDITOR DE CORTES del panel: en vez de llamar a
    Gemini, parte del plan YA analizado de otra versión (`plan_de` = su slug,
    "pro" o "pro-vN") y le suma los `cortes` manuales del autor, en segundos del
    vídeo ORIGINAL [(inicio, fin), ...]. Sale una versión nueva y aparte."""
    cfg = cfg or config.load()
    P = config.paths()
    from . import director, enlaces

    canales = (cfg.get("videos") or {}).get("canales") or {}
    info = canales.get(canal) or {}
    contexto = str(info.get("contexto", "")).strip()
    nombre_canal = str(info.get("nombre") or canal or "vídeo")
    from . import reglas as _reglas
    try:
        reglas_autor = _reglas.activas(canal)              # 📌 el feedback general del autor (se apunta en cada versión)
    except Exception as exc:  # noqa: BLE001 — sin reglas el director trabaja como siempre
        log(f"AVISO: no se pudieron leer las reglas permanentes ({exc}).")
        reglas_autor = []
    try:
        from . import nombres as _nombres_mod
        nombres_autor = _nombres_mod.texto_para_director(cfg, canal)     # 🛡 sus herramientas y marcas, escritas como van
    except Exception as exc:  # noqa: BLE001 — sin la lista el director trabaja como siempre
        log(f"AVISO: no se pudo leer la lista de nombres propios ({exc}).")
        nombres_autor = ""
    nombres_revisados = False                 # ¿ya se revisaron los nombres del plan en ESTA corrida?
    enlaces_canal = dict(info.get("enlaces") or {})
    reglas_enlaces = info.get("enlaces_por_tema") or []
    hashtags_canal = [str(h) for h in (info.get("hashtags") or [])]
    articulos = enlaces.articulos_del_canal(info)
    if info.get("marca"):
        # un canal con su propia marca (colores y letras): la usa TODO lo que se dibuja (tarjetas, ilustraciones,
        # imágenes) y los colores que se le piden a la IA para las miniaturas
        m = {**(cfg.get_path("videos.marca") or {}), **{k: v for k, v in info["marca"].items() if v}}
        for k in ("fuente_texto", "fuente_titulo", "fuente_numero"):
            if m.get(k) and not Path(str(m[k])).is_absolute():
                m[k] = str(config.ROOT / m[k])
        cfg["videos"]["marca"] = m
    # 🗣️ la jerga y los nombres de ESTE canal: Whisper los escribe bien («Términos: …») y la censura es la suya
    terminos = list(dict.fromkeys([str(t) for t in (info.get("vocabulario") or [])] + [str(k) for k in (info.get("nombres_propios") or {})]))
    if terminos:
        base_prompt = str(cfg.get_path("transcribe.initial_prompt", "") or "").strip()
        cfg.setdefault("transcribe", {})["initial_prompt"] = f"{base_prompt} Términos: {', '.join(terminos)}.".strip()
    if isinstance(info.get("censura"), dict):
        cfg["censura"] = {"enabled": bool(info["censura"].get("enabled")), "palabras": list(info["censura"].get("palabras") or []), "pad": 0.07, "modo": "silencio"}
        cfg.setdefault("videos", {})["censura"] = bool(info["censura"].get("enabled"))
    # 🎛️ las ediciones que ESTE canal eligió (clipper/ediciones_canal.py): interruptores de la corrida y filtros del plan
    from . import ediciones_canal as _edic
    edicion_canal = _edic.valida(info.get("edicion"))
    if edicion_canal:
        apagadas_ed = _edic.aplica(cfg, edicion_canal)
        if apagadas_ed:
            log("Ediciones apagadas para este canal: " + ", ".join(apagadas_ed) + ".")

    base = job_id(source)
    version = max(1, int(version))
    slug_director = "pro" if version == 1 else f"pro-v{version}"
    jid = f"{base}--pro" if version == 1 else f"{base}--pro-v{version}"
    from . import uso as _uso
    _uso.contexto(job=jid)                       # cada llamada a Gemini se apunta con su trabajo (pestaña «Uso»)
    work = P["work"] / f"{base}--pro"     # SIEMPRE el mismo: comparte transcripción
    work.mkdir(parents=True, exist_ok=True)
    out_dir = P["outbox"] / jid
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_control_flags(work)

    t0 = time.time()
    log("=" * 62)
    log(f"VÍDEO PRO — {jid}  (canal: {nombre_canal})")
    log("=" * 62)

    video = ingest.fetch(source, work)
    if not ingest.is_url(source):
        # la MISMA grabación subida otra vez (pasaba mucho: 12 copias de 2 grabaciones = 53 GB): no ocupa espacio de
        # nuevo y reusa la transcripción y el audio del trabajo que ya existía (clipper/duplicados.py)
        from . import duplicados
        duplicados.al_subir(video, P["inbox"], work, lambda p: P["work"] / f"{job_id(str(p))}--pro",
                            P["work"] / "huellas_inbox.json")
    meta = ingest.probe(video)
    log(f"Vídeo: {meta['width']}x{meta['height']} @ {meta['fps']:.0f}fps "
        f"— duración {hhmmss(meta['duration'])}")
    wav = ingest.extract_audio(video, work)
    words = transcribe.transcribe(wav, cfg, work)
    try:                                        # 🛡 «dije EEAT y salió EAT» (el autor, 11-oct-2026): los alias de tus nombres, también en lo que oyó Whisper
        from . import nombres as _nombres_alias
        cambios_alias = _nombres_alias.alias_transcripcion(words, _nombres_alias.glosario(cfg, canal))
        if cambios_alias:
            log("🛡 Nombres en la transcripción: " + ", ".join(f"«{a}» → «{b}»" for a, b in cambios_alias[:6])
                + (f" y {len(cambios_alias) - 6} más" if len(cambios_alias) > 6 else "") + " (tus alias de config.yaml).")
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no pude aplicar los alias de nombres a la transcripción ({exc}).")
    dur = float(meta["duration"])
    segs_completo = [(0.0, dur)]

    # TU VOZ, medida en el audio (clipper/voz.py, sin Gemini): dónde arrancas de verdad, si repites el arranque
    # (tomas falsas) y dónde acabas. Los tiempos de Whisper fallan hasta ~1 s justo al salir de un silencio y el vídeo
    # arrancaba con silencio o con la primera palabra comida (el autor, 2026-09-24).
    audio_voz, tomas_info, pausas_cand = None, None, None
    if voz.activo(cfg):
        try:
            audio_voz = voz.Audio(wav)
        except Exception as exc:  # noqa: BLE001 — sin esto el vídeo sale igual, con el arranque que marque la IA
            log(f"AVISO: no pude medir tu voz en el audio ({exc}); el arranque queda como lo marque el director.")
        # el ARRANQUE vuelto a escuchar frase por frase: Whisper, con el audio entero, funde las tomas («¿Cómo estás?»
        # [2 s] «¿Cómo estás apareciendo…» salía como UNA sola pregunta) y ni Gemini ni `voz.tomas_arranque` podían
        # ver la toma falsa (el autor, 2026-09-25). Una vez por grabación (`arranque_reoido.json`); words.json no se toca.
        if audio_voz is not None and cfg.get_path("transcribe.backend", "local") != "groq":
            try:
                words = transcribe.revisa_arranque(wav, words, cfg, work, audio=audio_voz)
            except Exception as exc:  # noqa: BLE001 — sin esto, el arranque como lo transcribió Whisper
                log(f"AVISO: no pude volver a escuchar el arranque ({exc}); sigo con la transcripción de Whisper.")
        tomas_info = voz.tomas_arranque(words)
    texto_arranque = voz.texto_prompt(tomas_info) if tomas_info else None

    # (lo que hay DENTRO de los cortes de «silencio / muletilla» de la IA se vuelve a escuchar con Whisper: una vez por
    # trozo y grabación, `cortes_reoidos.json` — ver `voz._protege_contenido`)
    oye_cortes = (transcribe.oido_con_cache(wav, cfg, work / "cortes_reoidos.json")
                  if cfg.get_path("transcribe.backend", "local") != "groq" else None)

    def _ajusta_voz(pl: dict | None) -> tuple[dict | None, dict]:
        if pl is None or audio_voz is None:
            return pl, {}
        return voz.ajusta_plan(pl, words, audio_voz, dur, lambda x: _segs_de_plan(x, dur, cfg), cfg, pausas=pausas_cand,
                               oye=oye_cortes)

    def _ancla(pl: dict | None, avisa: bool = True) -> int:
        """🎯 ANCLAJE (el autor, 2026-10-11): tarjetas, ilustraciones, imágenes, palabras clave y escenas salen cuando DICES lo que
        muestran (clipper/anclaje.py: por la cita del director y, si no, por su texto). Se hace nada más tener el plan, ANTES de
        revisar los nombres (que miran lo que se oye justo en ese segundo) y de todo lo demás; es idempotente y nunca rompe un
        vídeo. -> cuántos elementos movió."""
        if pl is None:
            return 0
        try:
            info = _anclaje.ancla_plan(pl, words, _segs_de_plan(pl, dur, cfg), cfg)
            if avisa:
                for linea in _anclaje.resumen(info):
                    log(linea)
            if info.get("activo"):
                pl["anclaje"] = {"movidos": len(info["movidos"]), "iguales": info["iguales"], "sin_ancla": len(info["sin_ancla"])}
            return len(info["movidos"])
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: no pude anclar lo que sale en pantalla a tus palabras ({exc}); sale donde lo puso la IA.")
            return 0

    from . import movimiento as _mov
    actividad_pantalla = _mov.detecta(video, int(meta["width"]), int(meta["height"]), dur,
                                      camara_rect=info.get("camara") or None,
                                      cache=work / "actividad_pantalla.json")
    if actividad_pantalla:
        log(f"  actividad en pantalla: {len(actividad_pantalla)} ventana(s) sostenida(s) detectada(s) "
            f"({sum(v['fin'] - v['inicio'] for v in actividad_pantalla):.0f}s en total).")

    # PAUSAS DE DUDA a media frase («la clave de… [1,4 s] todo esto»), medidas en el audio: el director las ve en la
    # MISMA llamada y marca en "pausas_enfasis" las que son a propósito; `voz.ajusta_plan` deja las demás en 0,3 s
    if audio_voz is not None and voz.pausas_activo(cfg):
        try:
            pausas_cand = voz.pausas_candidatas(words, audio_voz, actividad_pantalla, cfg)
            log(f"  pausas de duda a media frase: {len(pausas_cand)} de más de "
                f"{voz._params(cfg)['pausa_media']:.1f}s ({sum(c['dur'] for c in pausas_cand):.0f}s en total).")
        except Exception as exc:  # noqa: BLE001 — sin esto el vídeo sale igual, con esas pausas enteras
            log(f"AVISO: no pude medir las pausas de duda ({exc}); se quedan como estén.")
            pausas_cand = None
    texto_pausas = voz.texto_pausas(pausas_cand) if pausas_cand is not None else None
    ids_pausas = [c["id"] for c in pausas_cand] if pausas_cand is not None else None

    # ---- 1) el director: recortes + cámara + tarjetas + imágenes + título ----
    plan_cache = work / f"director_{slug_director}.json"
    if plan_de:
        # EDICIÓN MANUAL: nada de Gemini — el plan de la versión de origen +
        # los cortes del autor. Se guarda como el plan de ESTA versión, así una
        # edición posterior puede seguir encadenando cortes sobre esta.
        import copy
        base_plan = director.cargar(work, plan_de)
        if base_plan is None:
            log(f"ERROR: no encuentro el plan del director de «{plan_de}» "
                f"(work/{work.name}/director_{plan_de}.json), no puedo editar a partir de él.")
            return {"job": jid, "videos": 0, "out": str(out_dir)}
        if mejora:
            # MEJORA: el autor vio esa versión y comenta qué cambiar. Gemini recibe SU comentario, el plan de esa
            # versión y la transcripción con los dos relojes (vídeo editado / original), y devuelve el plan revisado.
            mv = re.search(r"-v(\d+)$", plan_de)
            previo = director.cargar(work, slug_director)
            if previo is not None and (previo.get("ia") or {}).get("mejora_texto") == mejora:
                # «Reintentar» tras un fallo del render: esta misma mejora ya la resolvió la IA y su plan está guardado
                log(f"MEJORA sobre «{plan_de}» ya resuelta antes: reuso ese plan, sin volver a llamar a Gemini.")
                plan = previo
            else:
                log("PROGRESO director_inicio")
                log(f"MEJORA sobre «{plan_de}»: el autor comenta: «{mejora[:200]}»")
                # las pausas de duda solo si esa versión ya las acortaba (un vídeo de antes de esto sigue igual: una
                # mejora cambia lo que pide el autor, no el estilo de edición)
                con_pausas = "pausas_vistas" in base_plan and ids_pausas is not None
                # si la IA VIO el vídeo para esa versión, la mejora recibe lo mismo que vio (guardado: no se vuelve a
                # mandar el vídeo); si no lo vio, tampoco ahora
                vista_base = _vision.lee(work) if (base_plan.get("vision") or {}).get("ok") else None
                try:
                    visto = _plan_visto(base_plan, dur, cfg)      # el plan tal como lo vio el autor (sin lo que el render descartó)
                    plan = _con_reintento_auto(lambda: director.analiza(
                        words, segs_completo, cfg, tema=nombre_canal,
                        contexto=_contexto_director(contexto, work, reglas_autor, revision=True, nombres=nombres_autor),
                        canal=nombre_canal,
                        es_completo=True, articulos=[a["titulo"] for a in articulos], hashtags=hashtags_canal,
                        actividad_pantalla=actividad_pantalla, arranque=texto_arranque,
                        pausas=texto_pausas if con_pausas else None,
                        vision=_vision.texto_prompt(vista_base) if vista_base else None,
                        revision={"plan": visto, "texto": mejora, "version": int(mv.group(1)) if mv else 1,
                                  "segs_vistos": _segs_de_plan(visto, dur, cfg)}), work, cfg)
                except Exception as exc:  # noqa: BLE001
                    log(f"AVISO: el director no pudo aplicar tu comentario ({exc}).")
                    director.ultimo_fallo = {"motivo": director._motivo_corto(str(exc)), "modelos": []}
                    plan = None
                if plan is not None and con_pausas:
                    plan["pausas_vistas"] = list(ids_pausas)       # las que la IA acaba de ver en esta llamada
                if plan is not None and base_plan.get("vision"):
                    plan["vision"] = dict(base_plan["vision"])
                if plan is not None:
                    plan["reglas"] = _reglas.resumen_plan(reglas_autor)    # las reglas permanentes que leyó el director
                    # los nombres, ANTES de comparar con lo que vio el autor (lo que él vio ya está revisado): los que
                    # escribió en su comentario cuentan como confirmados (y antes, cada cosa en el segundo en que la dices)
                    _ancla(plan)
                    _revisa_nombres(plan, words, cfg, canal, P["work"], comentario=mejora or "", jid=jid)
                    nombres_revisados = True
                log("PROGRESO director_fin")
                # se compara lo que SALDRÍA en pantalla (con el arranque y el final medidos en el audio en los dos:
                # si la IA solo «olvidó» esos cortes del programa, el vídeo sería el mismo)
                visto_cmp = copy.deepcopy(visto)               # (una versión de antes del anclaje se compara ya anclada)
                _ancla(visto_cmp, avisa=False)
                if plan is not None and (_huella_plan(_ajusta_voz(plan)[0], dur, cfg)
                                         == _huella_plan(_ajusta_voz(visto_cmp)[0], dur, cfg)):
                    # la IA devolvió el mismo plan: renderizar daría el MISMO vídeo. Se avisa y no se gasta render ni una mejora
                    nota = plan.get("nota_cambios") or ""
                    ia = plan.get("ia") or {}
                    _escribe_analisis(out_dir, {
                        "ok": True, "modelo": ia.get("modelo"), "nombre": ia.get("nombre") or "Gemini",
                        "principal": bool(ia.get("principal", True)),
                        "tokens_entrada": ia.get("tokens_entrada"), "tokens_salida": ia.get("tokens_salida"),
                        "en_cache": False, "manual": None,
                        "mejora": {"texto": mejora, "nota": nota, "de": plan_de, "sin_cambios": True}})
                    log("=" * 62)
                    log("SIN CAMBIOS — la IA no encontró nada que cambiar con tu comentario, así que NO se crea una versión nueva")
                    log("(saldría igual que la anterior). No se te descuenta ninguna mejora.")
                    if nota:
                        log(f"La IA dice: «{nota}»")
                    log("Prueba a explicarlo con otras palabras o con el tiempo exacto que ves en el reproductor.")
                    log("=" * 62)
                    return {"job": jid, "videos": 0, "out": str(out_dir), "sin_cambios": True}
                if plan is not None:
                    director.guardar(work, slug_director, plan)
            if plan is not None:
                log(f"  cambios: {plan.get('nota_cambios') or '(la IA no dejó nota)'}")
        else:
            plan = copy.deepcopy(base_plan)
            nuevos = _fusiona_cortes([(float(a), float(b)) for a, b in (cortes or [])])
            recortes_previos = list(plan.get("recortes") or [])
            plan["recortes"] = recortes_previos + [
                {"inicio": round(a, 4), "fin": round(b, 4), "motivo": "corte manual (editor)",
                 "manual": True} for a, b in nuevos]
            director.guardar(work, slug_director, plan)
            log(f"EDICIÓN MANUAL sobre «{plan_de}»: {len(nuevos)} corte(s) nuevo(s) del autor "
                f"({sum(b - a for a, b in nuevos):.1f}s) — sin llamar a Gemini.")
    else:
        # SIEMPRE se intenta la caché, sea la versión que sea: un Reshuffle genuino cae en None
        # solo porque a esa versión NUEVA nunca se le ha guardado plan todavía (normal), pero un
        # REINTENTO tras un fallo del RENDER (Gemini ya respondió bien, solo falló ffmpeg después)
        # SÍ tiene un plan válido esperando — sin esto, «Reintentar» repetía toda la negociación
        # con Gemini de cero (el autor vio un caso real: ~7 min reintentando modelos saturados, éxito,
        # y luego el render falló por un «No se encontró ffmpeg» transitorio — un simple reintento
        # hubiera vuelto a gastar esos 7 minutos por nada).
        plan = director.cargar(work, slug_director)
    if plan is None and not plan_de:          # (una mejora que falló NO se re-analiza de cero: se avisa y ya)
        # «La IA ve el vídeo» (⚙️ Opciones; experimento del autor, 2026-09-24): Gemini Flash-Lite mira la grabación UNA
        # vez (work/…/vision.json) y el director recibe lo que vio, en texto, dentro de su llamada de siempre
        vista = _vision.obten(video, words, dur, cfg, work, canal=nombre_canal) if _vision.activo(cfg) else None
        if not _vision.activo(cfg):
            # (el autor, 2026-09-25: comprobar que «sin vista IA» es de verdad sin vista) — dicho en el registro, y el director
            # recibe «no disponible» aunque exista un vision.json (p. ej. copiado de una subida anterior de la misma grabación)
            log("🙈 La IA NO ve el vídeo en este análisis (opción «La IA ve el vídeo» apagada): el director decide solo con "
                "la transcripción.")
        log("PROGRESO director_inicio")
        try:
            plan = _con_reintento_auto(lambda: director.analiza(words, segs_completo, cfg,
                                                                tema=nombre_canal,
                                                                contexto=_contexto_director(contexto, work, reglas_autor,
                                                                                            nombres=nombres_autor),
                                                                canal=nombre_canal,
                                                                es_completo=True,
                                                                articulos=[a["titulo"] for a in articulos],
                                                                hashtags=hashtags_canal,
                                                                actividad_pantalla=actividad_pantalla,
                                                                arranque=texto_arranque, pausas=texto_pausas,
                                                                vision=(_vision.texto_prompt(vista)
                                                                        if vista and vista.get("ok") else None)),
                                       work, cfg)
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: el director no pudo analizar el vídeo ({exc}).")
            director.ultimo_fallo = {"motivo": director._motivo_corto(str(exc)), "modelos": []}
            plan = None
        log("PROGRESO director_fin")
        if plan is not None:
            # si la IA VIO el vídeo en este análisis (para comparar versiones con y sin la pasada)
            plan["vision"] = _vision.resumen(vista) if vista else {"apagada": True}
            plan["reglas"] = _reglas.resumen_plan(reglas_autor)            # las reglas permanentes que leyó el director
            if reglas_autor:
                log(f"📌 El director leyó {len(reglas_autor)} regla(s) permanente(s) tuyas.")
            _ancla(plan)                                                     # 🎯 cada cosa en el segundo en que la dices
            _revisa_nombres(plan, words, cfg, canal, P["work"], jid=jid)    # 🛡 los nombres, antes de guardar el plan
            nombres_revisados = True
            if ids_pausas is not None:
                # qué pausas de duda VIO la IA (y pudo vetar): solo esas se acortan — lo apunta el programa, no ella
                plan["pausas_vistas"] = list(ids_pausas)
                if plan.get("pausas_enfasis"):
                    log(f"  el director conserva {len(plan['pausas_enfasis'])} pausa(s) por énfasis: "
                        + ", ".join(plan["pausas_enfasis"][:12]))
            # (las tomas falsas del arranque ya no se cortan aquí: las corta `voz.ajusta_plan` en CADA plan —también en
            # «Reintentar» y en las mejoras—, el autor 2026-09-25: «siempre debe arrancar con la toma buena»)
            director.guardar(work, slug_director, plan)
    elif not plan_de:
        log("Plan del director en caché, lo reuso.")

    # Sin plan de Gemini NO se renderiza: saldría un vídeo SIN editar (0 recortes,
    # 0 cambios de cámara, solo las tarjetas de suscríbete) y, encima, marcado
    # "Terminado" tras ~10 minutos de GPU. Antes seguía adelante con un plan
    # vacío (así salió v9 cuando Gemini estaba saturado). Ahora se corta aquí y
    # el panel enseña la tarjeta roja "NO analizado por Gemini" + Reshuffle.
    if plan is None:
        fallo = director.ultimo_fallo or {"motivo": "el director no devolvió ningún plan", "modelos": []}
        _escribe_analisis(out_dir, {"ok": False, **fallo})
        log("=" * 62)
        log("NO ANALIZADO POR GEMINI — no se renderiza (saldría el vídeo SIN editar).")
        log(f"Motivo: {fallo['motivo']}")
        log("Vuelve a intentarlo en unos minutos con «Reshuffle» (o «Reintentar»).")
        log("=" * 62)
        return {"job": jid, "videos": 0, "out": str(out_dir)}

    # 🎯 ANCLAJE para los planes que no pasaron por arriba (caché, «Reintentar», corte a mano, un plan de antes del anclaje):
    # idempotente, así que en los demás casos no mueve nada
    movidos_ancla = _ancla(plan, avisa=False)
    if movidos_ancla:
        log(f"🎯 Anclaje a lo que dices: {movidos_ancla} elemento(s) de este plan (de caché o de antes del anclaje) pasan al segundo en que "
            "dices lo que muestran.")
        director.guardar(work, slug_director, plan)

    # 🛡 NOMBRES de lo que sale en pantalla: un plan de ANTES de esta revisión (caché, «Reintentar») o uno que la IA no pudo
    # revisar la última vez se revisa aquí, una vez, y se guarda (así «Reintentar» no vuelve a preguntar)
    if not nombres_revisados and not (plan.get("nombres") or {}).get("revisado"):
        _revisa_nombres(plan, words, cfg, canal, P["work"], comentario=mejora or "", jid=jid)
        director.guardar(work, slug_director, plan)

    # ARRANQUE y FINAL medidos en el audio, sea cual sea el origen del plan (análisis nuevo, caché, mejora o corte a
    # mano): el vídeo empieza 0,15 s antes de tu primera sílaba, sin pausas largas en los primeros segundos, y acaba
    # 0,7 s después de tu última palabra como mucho. Se guarda en el plan (así el editor de cortes y las mejoras ven
    # los mismos segundos que el vídeo) y es idempotente: recalcularlo en un «Reintentar» da lo mismo.
    # (y en el resto del vídeo: los bordes de los cortes de la IA en silencio y las pausas de duda a 0,3 s)
    info_voz: dict = {}
    if audio_voz is not None:
        plan, info_voz = _ajusta_voz(plan)
        director.guardar(work, slug_director, plan)
        if info_voz.get("voz") is not None or info_voz.get("notas"):
            log("Tu voz medida en el audio"
                + (f": entra en el segundo {info_voz['voz']:.2f}" if info_voz.get("voz") is not None else "")
                + (f" — {'; '.join(info_voz['notas'])}." if info_voz.get("notas") else " (ya estaba bien)."))

    max_pct = float(plan.get("max_pct") or cfg.get_path("videos.recortes_max_pct", 0.5))
    # La red de seguridad del % máximo es para lo que propone la IA; un corte
    # que el autor hizo A MANO en el editor se respeta siempre (si no, uno grande
    # se ignoraría en silencio), y los que pone el programa midiendo tu voz
    # también (`_es_fijo`).
    recortes_ia = [r for r in (plan.get("recortes") or []) if not _es_fijo(r)]
    recortes_manuales = [r for r in (plan.get("recortes") or []) if r.get("manual")]
    recortes_auto = [r for r in (plan.get("recortes") or []) if _es_fijo(r) and not r.get("manual")]  # (+ los sin_voz)
    recortes_ia_seguros = _limita_recortes(recortes_ia, dur, max_pct)
    recortes_seguros = recortes_ia_seguros + recortes_auto + recortes_manuales
    segs = _aplica_recortes(segs_completo, recortes_seguros, dur)

    # Cuánto quitan DE VERDAD los cortes manuales: lo que marcó el autor en la línea
    # del tiempo, sin contar lo que la IA ya había recortado en ese mismo tramo
    # (así el panel dice "10 s quitados" y no "11.2 s" por solaparse con un
    # muletilla que la IA ya cortaba dentro).
    quita_manual = 0.0
    if recortes_manuales:
        sin_manual = _aplica_recortes(segs_completo, recortes_ia_seguros + recortes_auto, dur)
        quita_manual = sum(e - a for a, e in sin_manual) - sum(e - a for a, e in segs)

    ia = plan.get("ia") or {}
    _escribe_analisis(out_dir, {
        "ok": True, "modelo": ia.get("modelo"), "nombre": ia.get("nombre") or "Gemini",
        "principal": bool(ia.get("principal", True)),
        "tokens_entrada": ia.get("tokens_entrada"), "tokens_salida": ia.get("tokens_salida"),
        "en_cache": bool(not ia),   # plan de una corrida anterior que no anotó el modelo
        # cortes hechos a mano en el editor (vacío si esta versión no tiene)
        "manual": ({"cortes": len(recortes_manuales), "segundos": round(quita_manual, 1),
                    "de": plan_de} if recortes_manuales else None),
        # comentario del autor sobre la versión anterior y lo que la IA entendió/cambió (lo enseña la página de versiones)
        "mejora": ({"texto": mejora, "nota": plan.get("nota_cambios") or "", "de": plan_de} if mejora else None),
        # si la IA VIO el vídeo para este análisis (opción «La IA ve el vídeo»): para comparar versiones con y sin
        "vision": plan.get("vision"),
        # las reglas permanentes del autor que leyó el director en ESTE análisis (lo enseña /video/<base>: «📌 N reglas»)
        "reglas": plan.get("reglas"),
    })

    quitado = dur - sum(e - a for a, e in segs)
    if quitado > 0.5:
        log(f"Recortes: fuera {quitado:.0f}s de relleno "
            f"({len(recortes_seguros)} tramo(s)), quedan "
            f"{sum(e - a for a, e in segs) / 60:.1f} min de {dur / 60:.1f} min.")

    # Vuelve a partir lo que sobrevivió, esta vez en cada cambio de cámara
    # del plan — no cambia NADA de lo que ya sobrevivió (mismos tramos,
    # solo más subdivididos), así que _tiempo_final/_tiempo_final_capitulo
    # siguen dando los mismos segundos de siempre.
    camara_plan = _fuerza_zoom_en_actividad(_edic.filtra_camara(plan.get("camara") or [], edicion_canal), actividad_pantalla)
    segs, modos_camara = _segmentos_con_camara(segs, camara_plan)
    # «Pantalla con zoom» SOLO donde se ve TODO lo que hace el ratón (el autor, 2026-09-24: «se debe asegurar al 100 %»): un
    # encuadre fijo que lo contiene todo sin enseñar tu cámara; si no existe, ese tramo va con la pantalla entera. Antes de
    # todo lo demás (tarjetas, «Ritmo del vídeo», movimiento suave) para que todos vean ya el modo definitivo.
    camara_rect = info.get("camara") or None
    # zoom a una zona de la PANTALLA (no tu cara): sin medir nada tuyo, por defecto un recorte centrado moderado que ya
    # deja fuera la esquina de la cámara — se puede afinar con videos.canales.<canal>.pantalla_zoom en config.yaml.
    pantalla_zoom_rect = info.get("pantalla_zoom") or {"x": 0.08, "y": 0.08, "w": 0.66, "h": 0.78}
    cursor_raton = _mov.lee_cursor(work / "actividad_pantalla.json")
    modos_camara, encuadres_pz, sin_zoom = encuadres_zoom(segs, modos_camara, cursor_raton, pantalla_zoom_rect,
                                                          camara_rect, int(meta["width"]), int(meta["height"]))
    for c in sin_zoom:
        log(f"«Pantalla con zoom» del {int(c['t']) // 60}:{int(c['t']) % 60:02d} al {int(c['fin']) // 60}:"
            f"{int(c['fin']) % 60:02d} pasa a pantalla entera: ahí el ratón va de x {c['x'][0]:.2f} a {c['x'][1]:.2f} y de "
            f"y {c['y'][0]:.2f} a {c['y'][1]:.2f}, y ningún zoom lo enseña todo sin tapar nada.")
    # y ninguna escena de más de `videos.escena_max_seg` sin un cambio de plano (el autor, 2026-09-25: 3,4 min de pantalla
    # seguidos): un cambio corto ENTRE FRASES que no tapa lo que enseñas (ver `parte_escenas_largas`)
    segs, modos_camara, encuadres_pz, notas_escenas = parte_escenas_largas(
        segs, modos_camara, encuadres_pz, words, actividad_pantalla, cursor_raton, pantalla_zoom_rect, camara_rect,
        int(meta["width"]), int(meta["height"]), float(cfg.get_path("videos.escena_max_seg", 60) or 0),
        audio=audio_voz, nivel=voz.nivel_voz(audio_voz, words) if audio_voz is not None else None)
    for n in notas_escenas:
        log(f"  {n}")

    capitulos_texto = _capitulos_texto(plan.get("capitulos") or [], segs)
    # las 5 ETIQUETAS SEO de este vídeo (el autor, 2026-09-25: «todos están repitiendo el mismo tag»): las pide el director en
    # su llamada de siempre; un plan de antes de eso las pide UNA vez a Flash-Lite y se guardan en el plan
    tags_video = director.limpia_tags(plan.get("tags"))
    if not tags_video and cfg.get_path("videos.tags_activo", True):
        from . import titulos as _titulos_mod
        tags_video = _titulos_mod.tags_para(titulo_del_plan(plan), capitulos_texto, words, segs, contexto, nombre_canal,
                                            cfg, jid=jid)
        if tags_video:
            plan["tags"] = tags_video
            director.guardar(work, slug_director, plan)
    if tags_video:
        log("Etiquetas del vídeo: " + ", ".join(tags_video))
    hashtag_linea = enlaces.hashtags_de(tags_video) or plan.get("hashtag", "")
    articulo = enlaces.articulo_por_titulo(plan.get("articulo_relacionado", ""), articulos)
    texto_completo = " ".join(str(w.get("word", "")) for w in words)
    extra_enlaces = enlaces.enlaces_mencionados(texto_completo, reglas_enlaces, enlaces_canal)
    herramientas_tabla = cfg.get_path("videos.herramientas_conocidas") or {}
    materiales = enlaces.materiales_de_tarjetas(plan.get("tarjetas") or [], herramientas_tabla)
    # los enlaces a TU web llevan UTM para ver en Google Analytics qué vídeo te trae cada visita (el autor, 2026-09-26)
    utm = enlaces.utm_para(enlaces_canal, info.get("utm"), campana_utm(base, tags_video, titulo_del_plan(plan)))
    if utm:
        log(f"UTM en los enlaces a {', '.join(utm['dominios'])}: "
            + "&".join(f"utm_{k}={v}" for k, v in (("source", utm["source"]), ("medium", utm["medium"]),
                                                    ("campaign", utm["campaign"])) if v))
    descripcion = enlaces.arma_descripcion(
        plan.get("gancho_descripcion", ""), articulo, hashtag_linea,
        enlaces_canal, extra_enlaces, capitulos_texto, materiales=materiales, utm=utm)

    report = {
        "source": source, "jid": jid, "canal": canal, "duration": dur,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "titulos_sugeridos": plan.get("titulos_sugeridos") or [],
        "miniatura_prompts": plan.get("miniatura_prompts") or [],
        "miniatura_titulares": plan.get("miniatura_titulares") or [],
        "recortes": plan.get("recortes") or [],
        "tarjetas_pendientes": len(plan.get("tarjetas") or []),
        "imagenes_pendientes": len(plan.get("imagenes") or []),
        "capitulos": capitulos_texto,
        "descripcion": descripcion,
    }
    (out_dir / "videos.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if only_analyze:
        log(f"Solo análisis. Plan en {plan_cache}")
        return {"job": jid, "videos": 0, "out": str(out_dir)}

    # ---- 2) render: recortes de relleno + fades + tarjetas + zoom cámara ----
    ch = _cfg_horizontal(cfg)
    vpro = ch.setdefault("videos", {})
    vpro["intro"] = False              # las cartelas genéricas ("salió de un
    vpro["outro"] = False              # directo") no pegan con un tutorial subido
    if "subtitulos" in info:
        ch.setdefault("subtitles", {})["enabled"] = bool(info["subtitulos"])

    name = "pro"
    out_file = out_dir / f"{name}.mp4"
    jpg = out_file.with_suffix(".jpg")
    manifest: list[dict] = []
    _titulos = plan.get("titulos_sugeridos") or []
    # el título que ELEGISTE en el panel («usar este título» o editándolo): vive en el plan, así «Reintentar» no lo
    # cambia por el 🥇 (el autor lo perdía: 2026-09-25). Sin elegir, el 🥇 de la IA como siempre.
    _titulo = titulo_del_plan(plan)

    marca = dict(cfg.get_path("videos.marca") or {})
    alto_out = int(ch.get_path("output.height", 1080))
    ancho_out = int(ch.get_path("output.width", 1920))

    cortes_programa = voz.cortes_del_programa(plan)

    def _remapea(items: list[dict]) -> list[dict]:
        out_items = []
        for it in items:
            t_final = _tiempo_final_item(float(it["t"]), segs, cortes_programa)
            if t_final is None:
                continue
            it2 = dict(it)
            it2["t"] = t_final
            out_items.append(it2)
        if len(out_items) < len(items):
            log(f"  {len(items) - len(out_items)} caían en un tramo recortado, "
                f"no salen en el vídeo.")
        return out_items

    dur_final_pre = sum(e - a for a, e in segs)
    overlays: list[dict] = []
    creditos_imagenes: list[str] = []
    imagenes_eventos: list[dict] = []
    imagenes_info: list[dict] = []        # qué se eligió (o por qué no) en cada hueco de imagen: lo rellena imagenes.genera_pngs
    tarjetas_finales: list[dict] = []
    ilus_finales: list[dict] = []
    # fps de la SALIDA (los del config, sin pasar de los de la grabación): las ilustraciones se
    # dibujan a esos fps exactos para que cada fotograma suyo caiga en uno del vídeo
    _fps_src = int(round(float(meta.get("fps") or 0)))
    fps_salida = min(int(ch.get_path("output.fps", 30)), _fps_src) if _fps_src > 0 else int(ch.get_path("output.fps", 30))
    if not marca:
        log("AVISO: no hay videos.marca en config.yaml — sin eso no hay "
            "tarjetas ni imágenes (colores/fuentes de la marca).")
    else:
        # ninguna tarjeta ni imagen en los primeros ni en los últimos 5 s (el autor, 2026-09-27); en un clip de menos de 60 s
        # (el render de prueba de «Probar el sistema») no se aplica: no le quedaría sitio para nada
        margen_tj = float(cfg.get_path("videos.tarjetas_margen_seg", 5.0) or 0) if dur_final_pre >= 60 else 0.0
        if cfg.get_path("videos.tarjetas_activo", True):
            from . import tarjetas as tarjetas_mod
            log("Tarjetas:")
            tarjetas_plan, n_estim = _marca_estimados(_edic.filtra_tarjetas(plan.get("tarjetas") or [], edicion_canal), words)
            if n_estim:
                log(f"  {n_estim} dato(s) que son un estimado tuyo: salen como «≈» y «Mi estimación».")
            tarjetas_finales = _remapea(tarjetas_plan)
            # la que repite la escena «explicativa» que está en pantalla no sale (el autor, 2026-09-27). Solo si esa escena
            # sale de verdad con su término y definición: sin la cámara medida se queda en «cara» (ver más abajo)
            if info.get("camara"):
                tarjetas_finales, notas_ex = _sin_repetir_explicativa(
                    tarjetas_finales, _explicativas_finales(camara_plan, segs, modos_camara))
                for nota in notas_ex:
                    log(f"  {nota}")
            # seguidas en la misma esquina: la nueva reemplaza a la anterior (el autor, 2026-09-26)
            tarjetas_finales, n_enc = _encadena_tarjetas(tarjetas_finales)
            if n_enc:
                log(f"  {n_enc} tarjeta(s) seguidas: cada una se quita cuando entra la siguiente (no conviven).")
            tarjetas_finales, notas_b = _fuera_de_bordes(tarjetas_finales, dur_final_pre, margen_tj)
            for nota in notas_b:
                log(f"  {nota}")

            # CTA de suscripción: SIEMPRE al X% del vídeo YA montado — no
            # depende de que el director la pida, el autor la quiere en todos
            # los vídeos. Se calcula directo en tiempo FINAL (no pasa por
            # _remapea: no viene de un instante del original, es una
            # fracción del resultado). ADEMÁS, una por cada vez que el autor
            # dice "suscríbete" de verdad en la transcripción — detección
            # por texto (normalize + substring), no por IA: mismo criterio
            # anti-alucinación que enlaces.py. Nunca dos a menos de 2 min, y
            # si lo pides tú cerca del X%, gana la tuya (`_puestos_suscribete`).
            if cfg.get_path("videos.suscribete_activo", True) and dur_final_pre > 10:
                pct = float(cfg.get_path("videos.suscribete_pct", 0.6))
                cta = dict(info.get("cta_suscribete") or {})
                cta_texto = cta.get("texto", "¡SUSCRÍBETE!")
                cta_extra = cta.get("extra", "Si te está gustando el video, no te pierdas el próximo")
                cta_dur = float(cta.get("duracion", 4.0))
                menciones = [t for t in (_tiempo_final(t_o, segs) for t_o in _momentos_suscribete(words)) if t is not None]
                puestos = _puestos_suscribete(dur_final_pre, pct, menciones,
                                              tope=int(cfg.get_path("videos.suscribete_max", 3)),
                                              separacion=float(cfg.get_path("videos.suscribete_separacion_seg", 120.0)))
                for t_final, _por in puestos:
                    tarjetas_finales.append({
                        "t": t_final, "tipo": "suscribete",
                        "texto": cta_texto, "extra": cta_extra, "duracion": cta_dur,
                    })
                n_tuyas = sum(1 for _t, por in puestos if por == "mencion")
                if n_tuyas:
                    log(f"  suscríbete: {len(puestos)} tarjeta(s) — {n_tuyas} donde tú lo pides"
                        + (f" + 1 al {pct:.0%} del vídeo." if any(por == "fija" for _t, por in puestos) else
                           f"; la del {pct:.0%} no sale porque lo pediste a menos de 2 min de ahí."))

            # CTA de «dale like»: SIEMPRE al 80% del vídeo YA montado, en TODOS los vídeos — pedido
            # del autor (2026-09-23), igual que el suscríbete de arriba (no depende de que el
            # director la pida ni de nada dicho en la transcripción: una sola, siempre en el mismo
            # sitio relativo). Con su propio icono (clipper/tarjetas._like) y fondo dorado, para
            # no confundirla con la de suscribirse.
            if cfg.get_path("videos.like_activo", True) and dur_final_pre > 10:
                like_pct = float(cfg.get_path("videos.like_pct", 0.8))
                like = dict(info.get("cta_like") or {})
                tarjetas_finales.append({
                    "t": dur_final_pre * like_pct, "tipo": "like",
                    "texto": like.get("texto", "¡DALE LIKE!"),
                    "extra": like.get("extra", "Me ayuda muchísimo a que más gente lo encuentre"),
                    "duracion": float(like.get("duracion", 4.0)),
                })
                log(f"  dale like: 1 tarjeta al {like_pct:.0%} del vídeo.")

            # «suscríbete» y «like» ceden el sitio: nunca encima de una tarjeta del contenido ni de una escena
            # «explicativa» (misma esquina) — el autor vio la del 60 % tapando la 3.ª tarjeta «punto» (2026-09-24)
            tarjetas_finales, notas_cta = _aparta_ctas(tarjetas_finales, dur_final_pre,
                                                       _tramos_de_modo(segs, modos_camara, "explicativa"),
                                                       bordes=margen_tj)
            for nota in notas_cta:
                log(f"  {nota}")

            tj_overlays = tarjetas_mod.genera_pngs(
                tarjetas_finales, marca, work / "tarjetas", alto_canvas=alto_out)
            overlays += tj_overlays
            log(f"  {len(tj_overlays)} lista(s) para quemar en el vídeo.")

        if cfg.get_path("videos.imagenes_activo", True) and plan.get("imagenes"):
            from . import imagenes as imagenes_mod
            from .nombres import contexto_oido
            log("Imágenes ilustrativas:")
            # lo que se OYE en el momento de cada imagen: el curador (Flash-Lite mira las fotos candidatas, clipper/imagenes.py) lo lee
            # para elegir la que de verdad lo ilustra o ninguna (el autor, 2026-10-05: «que sea más accurate»)
            con_contexto = [dict(im, contexto=contexto_oido(words, float(im["t"]), antes=4.0, despues=7.0, max_chars=220))
                            for im in plan["imagenes"]]
            imagenes_plan, notas_im = _fuera_de_bordes(_remapea(con_contexto), dur_final_pre, margen_tj)
            for nota in notas_im:
                log(f"  {nota}")
            im_overlays, creditos_imagenes, imagenes_eventos = imagenes_mod.genera_pngs(
                imagenes_plan, marca, work / "imagenes", alto_canvas=alto_out, cfg=cfg, info=imagenes_info)
            overlays += im_overlays
            log(f"  {len(im_overlays)} lista(s) para quemar en el vídeo.")

        # ILUSTRACIONES ANIMADAS (clipper/ilustraciones.py): símbolos con movimiento que salen
        # en la esquina cuando la explicación es muy visual. El director las propone por
        # significado; aquí se aplican reglas duras (separación, densidad, no pisar imágenes).
        if cfg.get_path("videos.ilustraciones_activo", True) and plan.get("ilustraciones"):
            from . import ilustraciones as ilus_mod
            log("Ilustraciones animadas:")
            propuestas = _remapea(plan["ilustraciones"])
            ocupados = [(float(o["inicio"]), float(o["fin"])) for o in overlays if o.get("ancla") == "top-right"]
            ilus_finales = ilus_mod.selecciona(propuestas, dur_final_pre, ocupados, cfg, fps_salida)
            if len(ilus_finales) < len(propuestas):
                log(f"  {len(propuestas) - len(ilus_finales)} descartada(s) por las reglas "
                    f"(separación, máximo por minuto, imágenes en la misma esquina).")
            il_overlays = ilus_mod.genera_secuencias(ilus_finales, marca, work / "ilustraciones",
                                                     alto_canvas=alto_out, fps=fps_salida, cfg=cfg)
            overlays += il_overlays
            log(f"  {len(il_overlays)} lista(s) para quemar en el vídeo "
                f"({', '.join(i['tipo'] for i in ilus_finales) or '—'}).")

    # ESCENAS A PANTALLA COMPLETA (el autor, 2026-09-26: «me encanta… implementalo»): la pantalla entera con una ilustración grande
    # y la frase clave, tu voz debajo. Al menos una por vídeo, pocas; fondos naranja/crema alternados (clipper/escena_completa.py)
    escenas_finales: list[dict] = []
    if marca and cfg.get_path("videos.escenas.activo", True) and dur_final_pre > 30:
        try:
            from . import escena_completa as _esc
            from . import ritmo as _ritmo_esc
            p_esc = _esc.parametros(cfg)
            # las del director y, como plan B (plan de antes, o ninguna de las suyas cabe), sus ilustraciones de la esquina
            descartes_est: list[str] = []
            cands = (_esc.candidatas(plan, words, lambda t_o: _tiempo_final_item(t_o, segs, cortes_programa))
                     + _esc.candidatas_estilo(plan, words, lambda t_o: _tiempo_final_item(t_o, segs, cortes_programa), descartes_est)
                     + _esc.de_ilustraciones(ilus_finales))
            if descartes_est:
                log("  escenas de estilo descartadas: " + "; ".join(descartes_est))
            ocup_esc = [(float(o["inicio"]), float(o["fin"])) for o in overlays if o.get("tipo") != "ilustracion"]
            ocup_esc += _tramos_de_modo(segs, modos_camara, "explicativa")
            escenas_finales = _esc.selecciona(cands, dur_final_pre, ocup_esc,
                                              _ritmo_esc.vivos(segs, modos_camara, actividad_pantalla), p_esc, fps_salida)
            if escenas_finales:
                # la ilustración de la esquina que caería DEBAJO de una escena (o la que se volvió escena) sobra
                def _tapada(o):
                    return o.get("tipo") == "ilustracion" and any(
                        float(o["inicio"]) < e["t"] + e["duracion"] and float(o["fin"]) > e["t"] for e in escenas_finales)
                quitadas = [o for o in overlays if _tapada(o)]
                if quitadas:
                    overlays = [o for o in overlays if not _tapada(o)]
                    ilus_finales = [i for i in ilus_finales if not any(
                        i["t"] < e["t"] + e["duracion"] and i["t"] + i["duracion"] > e["t"] for e in escenas_finales)]
                overlays += _esc.genera(escenas_finales, marca, work / "escenas", ancho_out, alto_out, fps_salida, cfg)
                log("Escenas a pantalla completa: " + "; ".join(
                    f"{int(e['t']) // 60}:{int(e['t']) % 60:02d} {e['tipo']} «{e['frase']}» ({e['tono']}"
                    f"{', de una ilustración' if e['fuente'] == 'ilustracion' else (', de estilo' if e['fuente'] == 'estilo' else '')})"
                    for e in escenas_finales))
            else:
                log("Escenas a pantalla completa: ninguna cabe (tarjetas, demostraciones o sin candidatas).")
        except Exception as exc:  # noqa: BLE001 — un extra: nunca tumba el vídeo
            log(f"AVISO: las escenas a pantalla completa fallaron ({str(exc)[:200]}); el vídeo sale sin ellas.")
            escenas_finales = []

    # ESCENAS "explicativa": la tarjeta de término+definición va DENTRO del propio tramo (ver
    # `render.render_merged(explicativa_cards=…)`), no como un overlay con fundido — así entra y sale en el
    # fotograma exacto del corte de cámara y no pasa por negro. Independiente de `tarjetas_activo` (es una
    # escena de cámara, no una tarjeta flotante) pero SÍ necesita la marca (colores y letra) y la cámara
    # medida: si falta algo, ese tramo se queda en "cara" (la cara entera) en vez de dejar un hueco negro.
    cards_explicativa = (_tarjetas_explicativa(camara_plan, segs, modos_camara, marca, ancho_out, alto_out,
                                               work / "explicativa")
                         if marca and info.get("camara") else [None] * len(segs))
    n_expl = sum(1 for m in modos_camara if m == "explicativa")
    if n_expl:
        sin_tarjeta = [i for i, m in enumerate(modos_camara) if m == "explicativa" and cards_explicativa[i] is None]
        for i in sin_tarjeta:
            modos_camara[i] = "cara"
        if sin_tarjeta:
            log(f"AVISO: {len(sin_tarjeta)} tramo(s) «explicativa» sin tarjeta (falta videos.marca o la cámara medida "
                f"en config.yaml) — se quedan en «cara».")
        if n_expl > len(sin_tarjeta):
            log(f"Escenas «explicativa»: {n_expl - len(sin_tarjeta)} tramo(s) con su tarjeta de término+definición.")

    # MÚSICA DE FONDO (experimental, apagada por defecto —
    # videos.musica_activo). Primero TU carpeta local (videos.musica_carpeta
    # — canciones que ya descargaste tú, p. ej. de la Librería de Audio de
    # YouTube); solo si esa carpeta no existe o está vacía, cae a Openverse
    # como respaldo automático. Se arma a la duración exacta de ESTE vídeo
    # antes de renderizar porque el .mp4 final necesita el archivo ya listo
    # para mezclarlo (ver clipper/render.py, segundo pase aparte).
    musica_path = None
    creditos_musica: list[str] = []
    if cfg.get_path("videos.musica_activo", False) and dur_final_pre > 10:
        from . import musica as musica_mod
        log("Música de fondo:")
        carpeta_cfg = str(cfg.get_path("videos.musica_carpeta", "musica_fondo"))
        carpeta_musica = Path(carpeta_cfg)
        if not carpeta_musica.is_absolute():
            carpeta_musica = config.ROOT / carpeta_musica
        musica_path, creditos_musica = musica_mod.arma_pista_local(
            dur_final_pre, carpeta_musica, work / "musica",
            # solo canciones ESTABLES de fondo: sin «drops» ni subidones (el autor, 2026-09-24; medidas una vez, en caché)
            solo_estables=bool(cfg.get_path("videos.musica_solo_estables", True)),
            cache_perfiles=P["work"] / "musica_perfil.json")
        if musica_path is None:
            terminos = cfg.get_path("videos.musica_terminos") or [
                "upbeat corporate background", "energetic ambient instrumental"]
            musica_path, creditos_musica = musica_mod.arma_pista(
                dur_final_pre, terminos, work / "musica",
                volumen_rel_db=float(cfg.get_path("transiciones.musica_rel_voz_db", -18.0)))

    # RITMO (el autor, 2026-09-25, tras medir un vídeo de Alex Hormozi): PALABRAS CLAVE en grande que salen justo cuando las
    # dices y el «metrónomo» que las reparte para que el vídeo no pase más de videos.ritmo.max_sin_cambio_seg sin un cambio
    # visual (clipper/ritmo.py). Van en un .ass que el render quema en el mismo pase: no añaden tramos ni tocan el audio.
    ritmo_res: dict = {"palabras": [], "ass": None, "fontsdir": None}
    if marca and cfg.get_path("videos.ritmo.activo", True):
        try:
            from . import ritmo as _ritmo
            # un plan de ANTES de las palabras clave (sin el campo): UNA petición a Flash-Lite, guardada en el plan — así
            # «Reintentar» también las pone en un vídeo ya hecho sin cambiar su edición (como las etiquetas SEO)
            if ("palabras_clave" not in plan and cfg.get_path("videos.ritmo.palabras_activo", True)
                    and cfg.get_path("videos.ritmo.pedir_para_planes_viejos", True)):
                pc = _ritmo.palabras_para(words, segs, dur, contexto, nombre_canal, cfg)
                if pc is not None:
                    plan["palabras_clave"] = pc
                    director.guardar(work, slug_director, plan)
                    log(f"Palabras clave: {len(pc)} propuestas por Flash-Lite para este plan de antes (se guardan en el plan).")
            ritmo_res = _ritmo.aplica(
                plan, words, segs, modos_camara, overlays, tarjetas_finales, actividad_pantalla,
                lambda t_o: _tiempo_final_item(t_o, segs, cortes_programa), dur_final_pre, marca, ancho_out, alto_out,
                work / "palabras_clave.ass", cfg, explicativas=_tramos_de_modo(segs, modos_camara, "explicativa"),
                audio=audio_voz, nivel=voz.nivel_voz(audio_voz, words) if audio_voz is not None else None)
            for nota in ritmo_res.get("notas") or []:
                log(f"  {nota}")
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: las palabras clave fallaron ({str(exc)[:200]}); el vídeo sale sin ellas.")
            ritmo_res = {"palabras": [], "ass": None, "fontsdir": None}

    # EVENTOS VISUALES: cada tarjeta y cada cambio de cámara de verdad, en el
    # tiempo del vídeo YA MONTADO — para que puedas revisar el ritmo (cuánto
    # dinamismo hay por minuto) sin tener que ver el vídeo entero de nuevo.
    eventos_visuales: list[dict] = list(imagenes_eventos)   # ya vienen listos de imagenes.genera_pngs (uno por imagen que SÍ se metió)
    for pc in ritmo_res.get("palabras") or []:
        eventos_visuales.append({"t": round(float(pc["t"]), 1), "tipo": "palabra", "detalle": pc["texto"]})
    for e in escenas_finales:
        eventos_visuales.append({"t": round(float(e["t"]), 1), "tipo": f"escena:{e['tipo']}",
                                 "detalle": f"{e['frase'] or e['tipo']} ({e['tono']})"})
    for tj in tarjetas_finales:
        etiqueta = tj.get("texto") or tj.get("tipo", "")
        eventos_visuales.append({"t": round(float(tj["t"]), 1),
                                 "tipo": f"tarjeta:{tj.get('tipo', '')}",
                                 "detalle": str(etiqueta)[:80]})
    if ilus_finales:
        from .ilus_escenas import CATALOGO as _CAT_ILUS
        for il in ilus_finales:
            eventos_visuales.append({"t": round(float(il["t"]), 1), "tipo": f"ilustracion:{il['tipo']}",
                                     "detalle": il["etiqueta"] or _CAT_ILUS[il["tipo"]].nombre})
    acumulado_ev = 0.0
    for i in range(len(segs) - 1):
        acumulado_ev += max(0.10, segs[i][1] - segs[i][0])
        if i + 1 < len(modos_camara) and modos_camara[i] != modos_camara[i + 1]:
            eventos_visuales.append({
                "t": round(acumulado_ev, 1), "tipo": "camara",
                "detalle": modos_camara[i + 1]})
    eventos_visuales.sort(key=lambda e: e["t"])
    if eventos_visuales and dur_final_pre > 0:
        ratio = len(eventos_visuales) / (dur_final_pre / 60)
        log(f"  eventos visuales: {len(eventos_visuales)} en "
            f"{dur_final_pre / 60:.1f} min ({ratio:.1f} por minuto).")

    # descripción final (con créditos de imágenes si hubo) + el reporte
    # completo — SIEMPRE se reescribe aquí, tarjetas/eventos ya están listos.
    descripcion = enlaces.arma_descripcion(
        plan.get("gancho_descripcion", ""), articulo, hashtag_linea,
        enlaces_canal, extra_enlaces, capitulos_texto,
        creditos_imagenes or None, materiales=materiales,
        creditos_musica=creditos_musica or None, utm=utm)
    report["descripcion"] = descripcion
    report["eventos_visuales"] = eventos_visuales
    (out_dir / "videos.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ¿Se puede reusar el vídeo ya renderizado («Reintentar» tras un fallo DESPUÉS del render)? Solo si sale de exactamente
    # lo mismo: mismos tramos, escenas, tarjetas, imágenes e ilustraciones, mismos datos del ratón y el mismo código del
    # render (`_huella_render`). Antes bastaba con que existiera el .mp4: el autor (2026-09-24) le dio «Reintentar» para ver el
    # zoom nuevo y se reusó el vídeo viejo — encima con el «Ritmo del vídeo» reescrito para un vídeo que no era ese.
    render_reusado = False
    # 📷 CÁMARA APARTE (clipper/camara_aparte.py, el autor 2026-09-28): los tramos «cara» y «explicativa» con la cámara que
    # graba OBS aparte, a su resolución, si está y sincroniza con confianza. Apagada por defecto hasta que el autor compare:
    # `camara_aparte=True` (run_video_pro.py --camara-aparte) o `videos.camara_aparte.activo: true`.
    cam_aparte = None
    if camara_rect and any(m in ("cara", "explicativa") for m in modos_camara):
        from . import camara_aparte as _cam_ap
        if camara_aparte is True or (camara_aparte is None and _cam_ap.parametros(ch)["activo"]):
            cam_aparte = _cam_ap.prepara(Path(video), meta, camara_rect, ch, work, cara=camara_cara)
            if cam_aparte and cam_aparte.get("cara") == "marco" and marca:
                # «marco» (el autor, 28-sep noche): el fondo naranja con el recuadro, al tamaño del vídeo
                cam_aparte.update(_cam_ap.dibuja_marco(cam_aparte, marca, ancho_out, alto_out, work / "camara_marco"))
    huella_render = _huella_render({
        "camara_aparte": ({k: cam_aparte.get(k) for k in ("ruta", "desfase", "caja", "espejo", "zoom_cara", "cara",
                                                          "centro_x", "marco_png", "marco_caja")}
                          if cam_aparte else None),
        "segs": segs, "modos": modos_camara, "overlays": overlays, "explicativa": cards_explicativa,
        "palabras": ritmo_res.get("palabras") or [],
        "cortes_silenciosos": voz.cortes_silenciosos(plan), "canal": info.get("camara"), "zoom": info.get("pantalla_zoom"),
        "salida": [ch.get_path(k) for k in ("output.width", "output.height", "output.fps", "output.crf", "output.motor",
                                             "videos.pro_fade_in_seg", "videos.pro_fade_out_seg")],
        "transiciones": ch.get_path("transiciones")}, work)
    marca_render = out_dir / "render_huella.txt"
    try:
        huella_previa = marca_render.read_text(encoding="utf-8").strip()
    except OSError:
        huella_previa = ""
    if (out_file.is_file() and out_file.stat().st_size > 1_000_000 and jpg.is_file()
            and huella_previa == huella_render):
        render_reusado = True
        log("Ya estaba renderizado con esto mismo, lo reuso.")
    else:
        if out_file.is_file():
            log("El vídeo que había no sale de esto mismo (cambió el programa o el plan): lo vuelvo a renderizar.")
        # Fundidos del AUDIO en Vídeos PRO: cortos y FUERA de tu voz. Antes eran los del modo Generador (3 s de
        # subida y 3 de bajada): la primera frase —el gancho— sonaba lejos y la despedida se apagaba. La entrada
        # (0,12 s) cae en el aire que deja `voz.ajusta_plan` antes de tu primera sílaba (0,15 s); la salida, en el
        # que queda tras tu última palabra (nunca encima de ella).
        fi = float(ch.get_path("videos.pro_fade_in_seg", 0.12))
        fo_max = float(ch.get_path("videos.pro_fade_out_seg", 0.6))
        cola = info_voz.get("cola")
        fo = fo_max if cola is None else max(0.1, min(fo_max, float(cola) - 0.05))
        try:
            _ultimo_pct = -1

            def _reporta(pct: int) -> None:
                nonlocal _ultimo_pct
                if pct != _ultimo_pct:            # 1 línea por punto, no por fotograma
                    _ultimo_pct = pct
                    log(f"PROGRESO render_pct={pct}")

            if any(m in ("cara", "explicativa") for m in modos_camara) and not camara_rect:
                log("AVISO: el director quiere zoom a tu cara pero no hay "
                    "videos.canales.<canal>.camara en config.yaml — se renderiza "
                    "sin zoom (pantalla completa) en esos tramos.")
            # un tramo "pantalla_zoom" SIN datos del ratón se recentra donde hubo actividad (si se detectó); el que no
            # tenga una ventana clara usa `pantalla_zoom_rect` tal cual. Con datos del ratón manda su encuadre fijo
            # (`encuadres_pz`, calculado arriba: se ve TODO lo que hace el ratón).
            centros_zoom = _centros_pantalla_zoom(segs, modos_camara, actividad_pantalla)
            if any(encuadres_pz):
                log(f"  zoom a la pantalla: {sum(1 for c in encuadres_pz if c)} tramo(s) encuadrados donde se ve TODO "
                    f"lo que hace el ratón.")
            # el movimiento suave, tampoco si recortaría el ratón en alguna escena de pantalla (la misma regla)
            ken_burns, kb_quitados = _ken_burns_sin_tapar_raton(segs, modos_camara, _ken_burns_por_segmento(modos_camara),
                                                                cursor_raton, encuadres_pz, ch)
            if kb_quitados:
                log(f"  movimiento suave quitado en {kb_quitados} escena(s) de pantalla: habría recortado el ratón.")
            render.render_merged(video, segs, ch, meta, ritmo_res.get("ass"), out_file,
                                 fontsdir=ritmo_res.get("fontsdir"), camara_aparte=cam_aparte,
                                 words=words, fade_in=fi, fade_out=fo,
                                 tarjetas_overlays=overlays, progress_cb=_reporta,
                                 modos=modos_camara, camara_rect=camara_rect,
                                 pantalla_zoom_rect=pantalla_zoom_rect,
                                 pantalla_zoom_centros=centros_zoom,
                                 pantalla_zoom_caminos=encuadres_pz,
                                 ken_burns=ken_burns,
                                 explicativa_cards=cards_explicativa,
                                 cortes_silenciosos=voz.cortes_silenciosos(plan),
                                 musica_path=musica_path)
            try:
                marca_render.write_text(huella_render, encoding="utf-8")        # de qué salió este vídeo (ver arriba)
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001
            log(f"FALLÓ el render: {exc}")
            return {"job": jid, "videos": 0, "out": str(out_dir)}
        _make_thumb(out_file, _titulo or nombre_canal,
                    sum(e - a for a, e in segs), "video", "ninguno", ch)

    dur_final = sum(e - a for a, e in segs)
    # el día en que se RENDERIZÓ (para el nombre del vídeo): hoy, o el del render que se reusa
    import datetime as _dt
    fecha_render = _dt.date.today().isoformat()
    if render_reusado:
        try:
            fecha_render = (json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))[0].get("renderizado")
                            or _dt.date.fromtimestamp(out_file.stat().st_mtime).isoformat())
        except (OSError, ValueError, IndexError, AttributeError):
            fecha_render = _dt.date.fromtimestamp(out_file.stat().st_mtime).isoformat()
    manifest.append({
        "archivo": out_file.name, "miniatura": jpg.name,
        # «26-sep-2026 · Search Console · IAs en inglés»: lo que enseña el panel en vez del nombre del archivo de OBS
        "renderizado": fecha_render, "nombre": nombre_video(fecha_render, tags_video, _titulo),
        "kind": "video", "video": True, "pro": True, "score": None,
        "ai_pick": True, "top": None, "layout": "ninguno", "tema": canal,
        "tematica": nombre_canal,
        "titulo": _titulo or out_file.stem,
        "descripcion": descripcion, "gancho": plan.get("gancho_descripcion", ""),
        "tag": "video-pro",
        "razon": (f"vídeo PRO: recortado por el director de {nombre_canal}"
                  + (f" + {len(recortes_manuales)} corte(s) manual(es) tuyos" if recortes_manuales else "")),
        "titulos_sugeridos": _titulos,
        "tags": tags_video,                  # las 5 etiquetas SEO (el campo «tags» al publicar; en la descripción, hashtags)
        # los sets que se pidieron antes con «↻ Otros 3 títulos» (clipper/titulos.py): viven en el plan, y así
        # «Reintentar» no los pierde
        "titulos_anteriores": plan.get("titulos_anteriores") or [],
        "miniatura_prompts": plan.get("miniatura_prompts") or [],
        # el titular de cada miniatura (lo escribe el programa con Rubik, no la IA) y los conceptos que ya tuvo cada una
        # (clipper/conceptos_miniatura.py): viven en el plan, y así «Reintentar» no los pierde
        "miniatura_titulares": plan.get("miniatura_titulares") or [],
        "miniatura_prompts_anteriores": plan.get("miniatura_prompts_anteriores") or [],
        # los nombres de lo que sale en pantalla que se corrigieron o se quitaron (clipper/nombres.py): lo enseña /video/<base>
        "nombres": plan.get("nombres"),
        # cómo se eligió cada imagen ilustrativa (clipper/imagenes.py): elegida u omitida, por quién (la IA que mira las fotos, las reglas o lo
        # recordado de una pasada anterior), qué foto y por qué
        "imagenes_ia": imagenes_info or None,
        "capitulos": capitulos_texto,
        "eventos_visuales": eventos_visuales,
        # cómo queda el ritmo (clipper/ritmo.py): cambios por minuto y el tramo más largo sin ningún cambio, antes y después
        # de las palabras clave — el panel lo enseña junto al «Ritmo del vídeo»
        "ritmo": {**(ritmo_res.get("despues") or {}), "antes": ritmo_res.get("antes"),
                  "palabras": len(ritmo_res.get("palabras") or [])} if ritmo_res.get("despues") else None,
        "segments": [[round(a, 4), round(b, 4)] for a, b in segs],
        "origen": " + ".join(hhmmss(a) for a, _ in segs),
        "duracion": round(dur_final, 1),
    })
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    from . import historial
    historial.registra_render(jid, out_dir)          # copia ligera + miniatura en work/: sobrevive aunque borres videos/<jid>/
    # El vídeo largo y su manifest (con los prompts de miniatura) ya están: el panel puede empezar YA las miniaturas con IA (Cloudflare) en
    # paralelo con los Shorts, que dependen de Gemini y pueden tardar minutos si está saturado (8-oct-2026: el vídeo ya estaba al 100 % y
    # las miniaturas no salían hasta terminar los Shorts).
    log("PROGRESO miniaturas_listas")

    # SHORTS del vídeo YA editado (el autor, 2026-09-25: «ponlo de una vez en el renderizado general… en la parte de abajo»).
    # Van DESPUÉS del vídeo largo y nunca lo frenan ni lo rompen: cualquier fallo = sin Shorts, con el motivo.
    if cfg.get_path("videos.shorts_activo", True):
        log("PROGRESO shorts_inicio")
        try:
            from . import shorts as _shorts
            res_sh = _shorts.crea(video, words, segs, plan, cfg, work, slug_director, out_dir, meta,
                                  info.get("camara") or None, cursor_raton, actividad_pantalla, marca,
                                  canal=nombre_canal, contexto=contexto, capitulos=capitulos_texto, audio=audio_voz,
                                  nivel=voz.nivel_voz(audio_voz, words) if audio_voz is not None else None,
                                  forzar=not render_reusado)
            manifest[-1]["shorts"] = res_sh["shorts"]
            manifest[-1]["shorts_motivo"] = res_sh["motivo"]
            _mf = out_dir / "manifest.json"          # atómico: las miniaturas con IA leen este archivo en paralelo y no pueden verlo a medio escribir
            _tmp = _mf.with_name("manifest.json.tmp")
            _tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            from .util import reemplaza
            reemplaza(_tmp, _mf)
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: no se pudieron crear los Shorts ({str(exc)[:200]}); el vídeo largo está bien.")

    mins = (time.time() - t0) / 60
    log("=" * 62)
    log(f"LISTO — vídeo PRO en {mins:.1f} min ({dur_final / 60:.1f} min de salida)")
    if _titulos:
        log("Títulos sugeridos:")
        for i, t in enumerate(_titulos, 1):
            log(f"  {i}. {t}")
    log(f"Carpeta: {out_dir}")
    log("=" * 62)
    return {"job": jid, "videos": 1, "out": str(out_dir)}


def _cfg_horizontal(cfg):
    """Config PROPIA del generador de vídeos, sobre una COPIA (no toca la tuya).

    Todo se controla desde el bloque `videos:` de config.yaml.
    """
    import copy
    c = copy.deepcopy(cfg)
    v = cfg.get("videos") or {}

    # --- formato ---
    c["output"]["width"] = int(v.get("ancho", 1920))
    c["output"]["height"] = int(v.get("alto", 1080))
    c["output"]["crf"] = int(v.get("crf", cfg.get_path("output.crf", 20)))
    c.setdefault("reframe", {})["layout"] = "ninguno"     # 16:9 -> 16:9, sin recortes

    # --- subtítulos: si los quieres, en horizontal van CALMADOS, no karaoke ---
    sub = c.setdefault("subtitles", {})
    sub["enabled"] = bool(v.get("subtitulos", False))
    sub["preset"] = str(v.get("subs_preset", "limpio"))
    sub["font_size"] = int(v.get("subs_font_size", 54))
    sub["margin_v"] = int(v.get("subs_margin_v", 70))
    sub["words_per_line"] = int(v.get("subs_words_per_line", 6))
    sub["uppercase"] = bool(v.get("subs_uppercase", False))

    # --- transición entre tramos: sí ayuda (avisa del salto), pero MÁS BAJA ---
    tr = c.setdefault("transiciones", {})
    tr["sonido"] = bool(v.get("transicion", True))
    tr["rel_voz_db"] = float(v.get("transicion_db", -16.0))
    # los videos largos son otro producto: el whoosh sigue midiendose contra la
    # voz, sin agachar el audio y sin destello entre tramos
    tr["modo"] = "voz"
    tr["visual"] = str(v.get("transicion_visual", "no"))

    # --- censura de palabrotas: decisión tuya, aquí es un ajuste aparte ---
    cen = c.setdefault("censura", {})
    cen["enabled"] = bool(v.get("censura", cfg.get_path("censura.enabled", True)))

    # --- miniatura: menos gritona que la de Shorts ---
    th = c.setdefault("thumbnail", {})
    th["saturacion"] = float(v.get("thumb_saturacion", 1.18))
    th["borde_px"] = int(v.get("thumb_borde_px", 6))
    th["uppercase"] = bool(v.get("thumb_uppercase", True))
    return c


def process_video(source: str, cfg=None, contexto: str = "",
                  only_analyze: bool = False) -> dict:
    """Saca VIDEOS HORIZONTALES por tematica de un directo."""
    cfg = cfg or config.load()
    P = config.paths()
    from . import taste_videos, temas

    contexto = (contexto or "").strip()[:200]
    # slug corto del contexto para el modelo de aprendizaje propio (otros programas
    # tenía aquí una tabla de juegos; Mi canal no tiene "temas" fijos conocidos
    # de antemano, así que se deriva directo del texto que escribas).
    # OJO: safe_slug("") devuelve "clip" (su propio valor por defecto, pensado
    # para nombrar archivos de Shorts) — aquí el vacío tiene que ser "general".
    tema_juego = safe_slug(contexto, 24) if contexto else "general"
    cfg["contexto"] = contexto
    cfg["_tema"] = tema_juego

    jid = job_id(source)
    work = P["work"] / jid
    work.mkdir(parents=True, exist_ok=True)
    out_dir = video_out_dir(source)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    log("=" * 62)
    log(f"VIDEOS LARGOS — {jid}")
    log("=" * 62)

    video = ingest.fetch(source, work)
    meta = ingest.probe(video)
    log(f"Video: {meta['width']}x{meta['height']} @ {meta['fps']:.0f}fps "
        f"— duración {hhmmss(meta['duration'])}")
    wav = ingest.extract_audio(video, work)
    words = transcribe.transcribe(wav, cfg, work)      # reusa la caché

    # ---- 1) de qué se habla en este directo ----
    cache = work / "tematicas.json"
    vids = None
    if cache.is_file():
        try:
            blob = json.loads(cache.read_text(encoding="utf-8"))
            if blob.get("_v") == _TEMAS_V:
                vids = blob.get("videos")
                log("Temáticas en caché, las reuso.")
        except (OSError, ValueError):
            vids = None
    if vids is None:
        vids = temas.detectar(words, meta, cfg, contexto)
        cache.write_text(json.dumps({"_v": _TEMAS_V, "videos": vids},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    # el aprendizaje PROPIO de videos pone delante las tematicas que rinden
    try:
        vids = taste_videos.ordena(vids, cfg, tema_juego)
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: aprendizaje de videos no aplicado ({exc}).")
    if not vids:
        log("No salió ninguna temática con material suficiente.")
        return {"job": jid, "videos": 0, "out": str(out_dir)}

    report = {"source": source, "jid": jid, "tema": tema_juego,
              "contexto": contexto, "duration": meta["duration"],
              "generated": time.strftime("%Y-%m-%d %H:%M:%S"), "videos": vids}
    (out_dir / "videos.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if only_analyze:
        log(f"Solo análisis. {len(vids)} vídeo(s) propuestos en {out_dir}/videos.json")
        return {"job": jid, "videos": len(vids), "out": str(out_dir)}

    # ---- 2) render horizontal ----
    ch = _cfg_horizontal(cfg)
    log(f"Renderizando {len(vids)} vídeos... (horizontal "
        f"{ch.get_path('output.width')}x{ch.get_path('output.height')}, sin reencuadre)")
    log(f"PROGRESO videos_total={len(vids)}")
    manifest: list[dict] = []
    for k, v in enumerate(vids, 1):
        if _pause_gate(work):
            log("DETENIDO por el usuario.")
            break
        segs = [(float(a), float(b)) for a, b in v["segments"]]
        name = f"vid{k:02d}_{safe_slug(v.get('tema', 'tema'), 44)}"
        out_file = out_dir / f"{name}.mp4"

        # EL DIRECTOR (clipper/director.py) — recortes/camara/tarjetas/imagenes.
        # Apagado por defecto (`videos.director_activo`): el generador por
        # temática parte un directo en varios vídeos y el plan del director
        # está pensado para editar UN vídeo de una pieza (Vídeos PRO). Se deja
        # aquí por si algún día se quiere el mismo tratamiento por temática.
        if ch.get_path("videos.director_activo", False):
            try:
                from . import director as _dir
                plan = _dir.cargar(work, name)
                if plan is None:
                    plan = _dir.analiza(words, segs, ch, tema=v.get("tema", ""),
                                        contexto=contexto)
                    if plan is not None:
                        _dir.guardar(work, name, plan)
                else:
                    log(f"  [vid {k}] plan del director en caché, lo reuso.")
            except Exception as exc:  # noqa: BLE001
                log(f"  [vid {k}] director no disponible ({exc}); sigo sin él.")
        jpg = out_file.with_suffix(".jpg")
        if out_file.is_file() and out_file.stat().st_size > 4_000_000 and jpg.is_file():
            log(f"  [vid {k}] ya estaba renderizado, lo reuso.")
        else:
            ass = None
            if ch.get_path("subtitles.enabled", False):
                ass = subtitles.build_ass_segments(words, segs, ch, work / f"{name}.ass")

            # 1) el vídeo, con el audio entrando y saliendo suave
            fi = float(ch.get_path("videos.fade_in_seg", 6.0))
            fo = float(ch.get_path("videos.fade_out_seg", 3.0))
            crudo = out_dir / f"_{name}_crudo.mp4"
            try:
                render.render_merged(video, segs, ch, meta, ass, crudo, words=words,
                                     fade_in=fi, fade_out=fo)
            except Exception as exc:  # noqa: BLE001
                log(f"  [vid {k}] FALLÓ: {exc}")
                continue

            # 2) cartelas de intro y outro (animadas, sacadas del propio directo)
            intro = outro = None
            if ch.get_path("videos.intro", True):
                try:
                    intro = render.make_cartela(
                        video, segs[0][0] + 8, ch, out_dir / f"_{name}_intro.mp4", "intro")
                except Exception as exc:  # noqa: BLE001
                    log(f"  [vid {k}] intro no generada ({exc}), sigo sin ella.")
            if ch.get_path("videos.outro", True):
                try:
                    outro = render.make_cartela(
                        video, max(0.0, segs[-1][1] - 12), ch,
                        out_dir / f"_{name}_outro.mp4", "outro")
                except Exception as exc:  # noqa: BLE001
                    log(f"  [vid {k}] outro no generado ({exc}), sigo sin él.")

            # 3) pegado SIN recodificar
            try:
                res = render.pega_cartelas(crudo, intro, outro, out_file)
                if Path(res) != out_file:            # no habia cartelas que pegar
                    crudo.replace(out_file)
            except Exception as exc:  # noqa: BLE001
                log(f"  [vid {k}] no se pudieron pegar las cartelas ({exc}); "
                    f"queda el vídeo sin ellas.")
                crudo.replace(out_file)
            for tmp in (crudo, intro, outro):
                if tmp and Path(tmp).is_file() and Path(tmp) != out_file:
                    try:
                        Path(tmp).unlink()
                    except OSError:
                        pass
            if intro or outro:
                extra = (ch.get_path("videos.intro_seg", 5.0) if intro else 0) + \
                        (ch.get_path("videos.outro_seg", 6.0) if outro else 0)
                v["duracion"] = round(float(v["duracion"]) + float(extra), 1)
            _make_thumb(out_file, v.get("titulo") or v.get("tema", ""),
                        v["duracion"], "video", "ninguno", ch)
            log(f"  [vid {k}] {v['duracion'] / 60:.1f} min | {v['n_tramos']} tramos "
                f"| {out_file.stat().st_size / 1e6:.0f} MB | {v.get('titulo', '')}")
        manifest.append({
            "archivo": out_file.name, "miniatura": jpg.name,
            "kind": "video", "video": True, "score": None, "ai_pick": True,
            "top": None, "layout": "ninguno", "tema": tema_juego,
            "tematica": v.get("tema", ""),
            "titulo": v.get("titulo", ""), "descripcion": v.get("descripcion", ""),
            "gancho": v.get("gancho", ""), "tag": "video",
            "razon": f"todo lo que se habló de «{v.get('tema', '')}» "
                     f"({v['n_tramos']} tramos)",
            "segments": [[round(a, 2), round(b, 2)] for a, b in segs],
            "origen": " + ".join(hhmmss(a) for a, _ in segs),
            "duracion": round(v["duracion"], 1),
        })
        _flush_manifest(out_dir, manifest)

    _clear_control_flags(work)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    mins = (time.time() - t0) / 60
    log("=" * 62)
    log(f"LISTO — {len(manifest)} vídeos en {mins:.1f} min")
    log(f"Carpeta: {out_dir}")
    log("=" * 62)
    return {"job": jid, "videos": len(manifest), "out": str(out_dir)}


def prueba() -> list[dict]:
    """Primera prueba de este módulo (pura, sin ffmpeg ni Gemini): `_centros_pantalla_zoom` —
    cada tramo "pantalla_zoom" debe emparejarse con la ventana de actividad (`movimiento.py`) que
    MÁS se solapa con él — y `_fuerza_zoom_en_actividad` — solo fuerza "cara"->"pantalla_zoom"
    con un solape SUSTANCIAL, para no entrar y salir del zoom por un roce fugaz de actividad."""
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": detalle})

    # 🎯 «SIN CAMBIOS»: la `cita` (solo sirve para anclar) no cuenta como un cambio del plan
    pa = {"recortes": [], "camara": [], "tarjetas": [{"t": 10.0, "tipo": "herramienta", "texto": "Astro"}]}
    pb = {"recortes": [], "camara": [], "tarjetas": [{"t": 10.0, "tipo": "herramienta", "texto": "Astro", "cita": "abrimos Astro"}]}
    pc = {"recortes": [], "camara": [], "tarjetas": [{"t": 10.0, "tipo": "herramienta", "texto": "Vercel"}]}
    r("la huella de un plan no cambia por la cita, y sí por un cambio de verdad", _huella_plan(pa) == _huella_plan(pb) != _huella_plan(pc))

    segs = [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]
    modos = ["cara", "pantalla_zoom", "pantalla_zoom"]
    actividad = [
        {"inicio": 9.0, "fin": 14.0, "x": 0.2, "y": 0.3},     # solapa 1s con el tramo 0 (cara, se ignora) y 4s con el 1
        {"inicio": 21.0, "fin": 29.0, "x": 0.6, "y": 0.7},    # solapa 8s con el tramo 2: gana por mucho
        {"inicio": 22.0, "fin": 23.0, "x": 0.9, "y": 0.9},    # solapa solo 1s con el tramo 2: no debería ganar
    ]
    centros = _centros_pantalla_zoom(segs, modos, actividad)
    r("un tramo que NO es pantalla_zoom nunca lleva centro (aunque haya actividad ahí)",
      centros[0] is None, centros)
    r("el tramo pantalla_zoom usa la ventana con MÁS solape (4s, no una que no llega)",
      centros[1] == (0.2, 0.3), centros[1])
    r("con dos ventanas solapando, gana la de más solape (8s > 1s), no la última de la lista",
      centros[2] == (0.6, 0.7), centros[2])
    r("sin actividad detectada, ningún tramo lleva centro (ni siquiera revienta)",
      _centros_pantalla_zoom(segs, modos, []) == [None, None, None])

    # tarjetas de «suscríbete»: nunca dos a menos de 2 min, y si lo pides tú cerca del 60 %, gana la tuya (el autor, 2026-09-24)
    r("sin menciones: solo la fija del 60 %", _puestos_suscribete(600, 0.6, []) == [(360.0, "fija")])
    r("lo pides 20 s antes del 60 %: sale la tuya y la fija NO",
      _puestos_suscribete(600, 0.6, [340.0]) == [(340.0, "mencion")], _puestos_suscribete(600, 0.6, [340.0]))
    r("lo pides dos veces a 1 min de distancia: solo la primera (mínimo 2 min entre tarjetas)",
      _puestos_suscribete(900, 0.6, [60.0, 120.0]) == [(60.0, "mencion"), (540.0, "fija")],
      _puestos_suscribete(900, 0.6, [60.0, 120.0]))
    p4 = _puestos_suscribete(1200, 0.6, [60.0, 300.0, 1000.0])
    r("menciones lejos de la fija: tuyas + la fija, con el tope de 3 en total y todas a ≥ 2 min",
      p4 == [(60.0, "mencion"), (300.0, "mencion"), (720.0, "fija")]
      and all(b[0] - a[0] >= 120 for a, b in zip(p4, p4[1:])), p4)
    p5 = _puestos_suscribete(1200, 0.6, [60.0, 300.0, 500.0, 700.0])
    r("si la que tapa a la fija quedaría fuera por el tope, entra ella (nunca sin tarjeta cerca del 60 %)",
      len(p5) == 3 and any(abs(t - 720) < 120 for t, _p in p5) and all(p == "mencion" for _t, p in p5), p5)

    # «pantalla con zoom» SOLO donde se ve TODO lo que hace el ratón (el autor, 2026-09-24: «asegurar al 100 %»). La cámara de
    # el autor abajo a la derecha y su zoom de la config (el encuadre de siempre: 0,646 del lienzo)
    cam = {"x": 0.7672, "y": 0.6583, "w": 0.2172, "h": 0.2847}
    rect = {"x": 0.08, "y": 0.08, "w": 0.66, "h": 0.78}
    w0 = 0.6583 - 0.012

    def ve_todo(e, pts, margen=ZOOM_MARGEN - 1e-6):
        """¿El encuadre (x, y, w, h) enseña cada punto con su margen (o hasta el borde del lienzo)?"""
        x, y, w, h = e
        return all(max(0.0, px - margen) >= x - w / 2 - 1e-4 and min(1.0, px + margen) <= x + w / 2 + 1e-4
                   and max(0.0, py - margen) >= y - h / 2 - 1e-4 and min(1.0, py + margen) <= y + h / 2 + 1e-4
                   for px, py in pts)                      # (1e-4 = redondeo a 4-5 decimales: < 1 píxel)

    def ve_camara(e):
        x, y, w, h = e
        return (x + w / 2 > cam["x"] and x - w / 2 < cam["x"] + cam["w"]
                and y + h / 2 > cam["y"] and y - h / 2 < cam["y"] + cam["h"])

    # 1) el caso REAL (2:46-3:32 de la versión con vista IA, ratón medido): subraya a la derecha pegado a la cámara, va a las
    #    pestañas arriba a la izquierda, vuelve al centro y acaba a la derecha → ningún zoom lo enseña todo: pantalla entera
    real = [(165.0, 0.4365, 0.3741), (167.0, 0.3937, 0.5778), (168.5, 0.4469, 0.5963), (169.0, 0.4927, 0.6167),
            (174.0, 0.6979, 0.6611), (174.5, 0.7469, 0.6111), (175.5, 0.7354, 0.5537), (176.0, 0.6854, 0.5315),
            (177.0, 0.7167, 0.5241), (177.5, 0.7615, 0.5278), (178.0, 0.7792, 0.4981), (178.5, 0.7552, 0.5019),
            (179.0, 0.7073, 0.5259), (180.0, 0.3979, 0.5648), (181.5, 0.2156, 0.0611), (182.0, 0.125, 0.0074),
            (182.5, 0.1187, 0.0074), (183.5, 0.0865, 0.1167), (184.5, 0.5844, 0.5815), (187.0, 0.5073, 0.5778),
            (189.0, 0.8469, 0.5185)]
    m1, e1, c1 = encuadres_zoom([(100.0, 168.0), (168.0, 214.0), (214.0, 320.0)], ["cara", "pantalla_zoom", "pantalla"],
                                real, rect, cam, 2560, 1440)
    r("el caso real del 24-sep: el ratón recorre casi toda la pantalla → ese zoom va con la pantalla entera (lo demás "
      "no se toca)", m1 == ["cara", "pantalla", "pantalla"] and e1 == [None, None, None] and len(c1) == 1
      and c1[0]["t"] == 68.0 and c1[0]["fin"] == 114.0, (m1, e1, c1))
    # 2) su primer trocito (el ratón por el centro, abajo): cabe → un encuadre fijo que lo enseña todo, sin la cámara
    trozo = [(x, y) for t, x, y in real if t <= 169.0]
    e2 = encuadre_zoom(trozo, w0, 1.0, cam)
    r("si todo lo que hace el ratón cabe, un encuadre FIJO que lo enseña entero (con aire) y sin tu cámara, con el zoom "
      "de siempre", e2 is not None and ve_todo(e2, trozo) and not ve_camara(e2) and abs(e2[2] - w0) < 1e-3, e2)
    # 3) arriba a la derecha (encima de la cámara): el encuadre sube por encima de ella
    e3 = encuadre_zoom([(0.88, 0.15), (0.84, 0.20)], w0, 1.0, cam)
    r("el ratón arriba a la derecha: el encuadre va por encima de la cámara y lo enseña",
      e3 is not None and ve_todo(e3, [(0.88, 0.15), (0.84, 0.20)]) and not ve_camara(e3) and e3[1] < 0.34, e3)
    # 4) pegado a la esquina de la cámara: imposible sin enseñarla → None (pantalla entera)
    r("el ratón pegado a la esquina de tu cámara: ningún zoom (lo enseñaría a medias o con tu cámara recortada)",
      encuadre_zoom([(0.75, 0.64), (0.70, 0.60)], w0, 1.0, cam) is None)
    # 5) un poco más ancho que el zoom de siempre: el encuadre se abre lo justo (sigue siendo zoom)
    anchos = [(0.05, 0.40), (0.66, 0.42), (0.30, 0.45)]
    e5 = encuadre_zoom(anchos, w0, 1.0, cam)
    r("si no cabe por poco, el encuadre se abre lo justo (menos zoom, pero lo enseña todo)",
      e5 is not None and w0 < e5[2] <= ZOOM_MAX and ve_todo(e5, anchos) and not ve_camara(e5), e5)
    r("…y si recorre casi todo el ancho, ninguno (más abierto ya no sería zoom)",
      encuadre_zoom([(0.02, 0.3), (0.95, 0.3)], w0, 1.0, cam) is None)
    # 6) garantía con 400 recorridos al azar: si hay encuadre, SIEMPRE enseña todo y nunca la cámara
    import random as _rnd
    azar = _rnd.Random(7)
    malos = []
    for _ in range(400):
        pts = [(azar.uniform(0, 1), azar.uniform(0, 1)) for _k in range(azar.randint(1, 8))]
        pts = [p for p in pts if not (cam["x"] <= p[0] <= cam["x"] + cam["w"] and cam["y"] <= p[1] <= cam["y"] + cam["h"])]
        if not pts:
            continue
        e = encuadre_zoom(pts, w0, 1.0, cam)
        if e and (not ve_todo(e, pts) or ve_camara(e) or not w0 - 1e-6 <= e[2] <= ZOOM_MAX + 1e-6
                  or e[0] - e[2] / 2 < -1e-4 or e[0] + e[2] / 2 > 1 + 1e-4 or e[1] - e[3] / 2 < -1e-4
                  or e[1] + e[3] / 2 > 1 + 1e-4):
            malos.append((pts, e))
    r("400 recorridos al azar: cada encuadre que sale enseña TODO el recorrido, dentro del lienzo y sin tu cámara",
      not malos, malos[:2])
    # 7) sin datos del ratón: nada cambia (el zoom de antes); la última posición de ANTES del tramo cuenta
    r("sin datos del ratón no se toca nada", encuadres_zoom([(0.0, 10.0)], ["pantalla_zoom"], [], rect, cam, 2560, 1440)
      == (["pantalla_zoom"], [None], []))
    m7, e7, _ = encuadres_zoom([(100.0, 110.0)], ["pantalla_zoom"], [(95.0, 0.9, 0.2)], rect, cam, 2560, 1440)
    r("el ratón quieto desde antes del tramo (se vio a los 95 s arriba a la derecha): el zoom lo enseña ahí",
      m7 == ["pantalla_zoom"] and e7[0] and ve_todo((*e7[0]["puntos"][0][1:], e7[0]["w"], e7[0]["h"]), [(0.9, 0.2)]), e7)
    m8, e8, _ = encuadres_zoom([(100.0, 110.0)], ["pantalla_zoom"], [(10.0, 0.9, 0.2), (101.0, 0.3, 0.4), (105.0, 0.35, 0.45)],
                               rect, cam, 2560, 1440)
    r("una posición de hace más de 30 s no cuenta si en el tramo el ratón sí se movió (ya no está ahí)",
      e8[0] and e8[0]["puntos"][0][1] < 0.5, e8)
    # 8) el movimiento suave (hasta 6 % de zoom) tampoco puede recortar el ratón
    from . import config as _config
    cfg_kb = _config.load()
    kb1, q1 = _ken_burns_sin_tapar_raton([(0.0, 60.0)], ["pantalla"], [1], [(50.0, 0.5, 0.01)], [None], cfg_kb)
    kb2, q2 = _ken_burns_sin_tapar_raton([(0.0, 60.0)], ["pantalla"], [1], [(50.0, 0.5, 0.5)], [None], cfg_kb)
    r("movimiento suave: fuera si recortaría el ratón (en las pestañas, arriba del todo); si no, se queda",
      (kb1, q1, kb2, q2) == ([0], 1, [1], 0), (kb1, q1, kb2, q2))
    mz, ez, _ = encuadres_zoom([(0.0, 40.0)], ["pantalla_zoom"], [(10.0, 0.3, 0.3), (20.0, 0.35, 0.35)], rect, cam, 2560, 1440)
    kb3, _ = _ken_burns_sin_tapar_raton([(0.0, 40.0)], mz, [1], [(10.0, 0.3, 0.3), (20.0, 0.35, 0.35)], ez, cfg_kb)
    mt, et, _ = encuadres_zoom([(0.0, 40.0)], ["pantalla_zoom"], [(10.0, 0.5, 0.01)], rect, cam, 2560, 1440)
    kb4, _ = _ken_burns_sin_tapar_raton([(0.0, 40.0)], mt, [1], [(10.0, 0.5, 0.01)], et, cfg_kb)
    r("en un zoom: con el ratón dentro del encuadre (con aire) el movimiento suave se queda; con el ratón en el borde del "
      "lienzo (las pestañas), se quita", kb3 == [1] and kb4 == [0], (kb3, kb4, ez, et))

    # «Reintentar» solo reusa el vídeo ya renderizado si sale de exactamente lo mismo (el autor, 2026-09-24)
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmp_h:
        base_h = {"segs": [(0.0, 10.0), (12.0, 30.0)], "modos": ["cara", "pantalla_zoom"], "overlays": []}
        h1, h2 = _huella_render(base_h, Path(tmp_h)), _huella_render(dict(base_h), Path(tmp_h))
        h3 = _huella_render({**base_h, "segs": [(0.0, 10.0), (12.5, 30.0)]}, Path(tmp_h))
        (Path(tmp_h) / "cursor.json").write_text("[[1.0, 0.5, 0.5]]", encoding="utf-8")
        h4 = _huella_render(base_h, Path(tmp_h))
    r("mismo plan, mismos datos y mismo programa: misma huella (se puede reusar el vídeo); otro corte o datos nuevos del "
      "ratón: huella distinta (se vuelve a renderizar)", h1 == h2 and h1 != h3 and h1 != h4, (h1, h2, h3, h4))

    # «suscríbete» y «like» ceden el sitio a las tarjetas del contenido (el autor, 2026-09-24: la del 60 % tapaba una «punto»)
    tj = [{"t": 206.5, "tipo": "punto", "duracion": 3.5}, {"t": 210.0, "tipo": "punto", "duracion": 3.5},
          {"t": 215.0, "tipo": "punto", "duracion": 3.5}, {"t": 216.7, "tipo": "suscribete", "duracion": 4.0},
          {"t": 289.0, "tipo": "like", "duracion": 4.0}]
    ap, notas = _aparta_ctas(tj, 361.0)
    sus = [t for t in ap if t["tipo"] == "suscribete"][0]
    r("el caso real del 24-sep: «suscríbete» del 216,7 s (encima de la 3.ª «punto», 215-218,5) se aparta al hueco más "
      "cercano sin pisar ninguna; el «like» libre no se mueve",
      sus["t"] >= 218.5 + 0.6 - 1e-6 and sus["t"] <= 219.2 and [t for t in ap if t["tipo"] == "like"][0]["t"] == 289.0
      and len(notas) == 1, (ap, notas))
    ap2, _ = _aparta_ctas([{"t": 50.0, "tipo": "definicion", "duracion": 3.5}, {"t": 51.0, "tipo": "suscribete", "duracion": 4.0}],
                          600.0, ocupados=[(54.0, 70.0)])
    r("si justo detrás hay una escena «explicativa», se va antes (al hueco más cercano) y nunca encima",
      [t for t in ap2 if t["tipo"] == "suscribete"][0]["t"] == round(50.0 - 0.6 - 4.0, 2), ap2)
    r("en un vídeo sin sitio para ella, no sale (antes que tapar algo)",
      not [t for t in _aparta_ctas([{"t": 0.0, "tipo": "dato", "duracion": 12.0}, {"t": 2.0, "tipo": "like", "duracion": 4.0}],
                                   12.0)[0] if t["tipo"] == "like"])
    r("las escenas «explicativa» en el vídeo montado", _tramos_de_modo([(0, 10), (20, 30), (30, 35), (50, 60)],
                                                                     ["cara", "explicativa", "explicativa", "pantalla"],
                                                                     "explicativa") == [(10.0, 25.0)])

    # el NOMBRE del vídeo (el autor, 2026-09-26): fecha del render + dos palabras clave (sus dos primeras etiquetas que no se repiten)
    import datetime as _dt
    r("nombre: fecha del render + las dos primeras etiquetas SEO («Search Console» tras «Google Search Console» se salta)",
      nombre_video("2026-09-26", ["Google Search Console", "Search Console", "IAs en inglés", "SEO"])
      == "26-sep-2026 · Google Search Console · IAs en inglés",
      nombre_video("2026-09-26", ["Google Search Console", "Search Console", "IAs en inglés"]))
    r("nombre: sin etiquetas, el título; la fecha vale como texto, date o timestamp; sin fecha, nada",
      nombre_video(_dt.date(2026, 1, 5), [], "Cómo aparecer en las IAs") == "05-ene-2026 · Cómo aparecer en las IAs"
      and nombre_video(_dt.datetime(2026, 12, 31, 23, 0).timestamp(), ["A"]).startswith("31-dic-2026 · A")
      and nombre_video(None, ["A"]) == "")
    r("nombre: un título largo se corta en una palabra entera, con «…»",
      nombre_video("2026-09-25", [], "Mide el trafico de la IA en tu web con Search Console")
      == "25-sep-2026 · Mide el trafico de la IA en tu web…",
      nombre_video("2026-09-25", [], "Mide el trafico de la IA en tu web con Search Console"))
    # la campaña UTM de los enlaces a tu web (el autor, 2026-09-26): la fecha de GRABACIÓN + las dos palabras clave del nombre
    r("UTM: la campaña es la fecha de la grabación + las dos palabras clave del vídeo, sin tildes (legible en Analytics)",
      campana_utm("2026-09-26-13-20-17-9970b018e9", ["Google Search Console", "Search Console", "IAs en inglés"])
      == "2026-09-26-google-search-console-ias-en-ingles",
      campana_utm("2026-09-26-13-20-17-9970b018e9", ["Google Search Console", "Search Console", "IAs en inglés"]))
    import tempfile as _tf
    # 📌 las reglas permanentes del autor (el autor, 2026-10-03): al director, dentro de su contexto
    with _tf.TemporaryDirectory() as _tmp2:
        regla = [{"id": "r1", "texto": "No cortes mis risas"}, {"id": "r2", "texto": "Tarjetas más cortas"}]
        c0 = _contexto_director("Canal de SEO.", _tmp2)
        c1 = _contexto_director("Canal de SEO.", _tmp2, regla)
        c2 = _contexto_director("Canal de SEO.", _tmp2, regla, revision=True)
        (Path(_tmp2) / "idea.json").write_text(json.dumps({"titulo": "SEO para restaurantes", "de_que_va": "x", "puntos": ["a"]}),
                                               encoding="utf-8")
        c3 = _contexto_director("Canal de SEO.", _tmp2, regla)
    r("📌 sin reglas el director recibe EXACTAMENTE lo de siempre; con reglas, el contexto + el bloque numerado (y en una "
      "revisión, avisa de que solo valen en lo que el comentario toca)",
      c0 == "Canal de SEO." and c1.startswith("Canal de SEO.\n\nREGLAS PERMANENTES DEL AUTOR") and "1. No cortes mis risas" in c1
      and "2. Tarjetas más cortas" in c1 and "REVISIÓN" not in c1 and "En esta REVISIÓN" in c2, c1)
    cn = _contexto_director("Canal de SEO.", _tmp2, regla, nombres="NOMBRES PROPIOS DEL AUTOR (…):\n- Medidor 5000: su herramienta")
    r("🛡 los nombres propios del autor llegan al director entre el contexto y las reglas (y sin ellos, nada cambia)",
      cn.index("Canal de SEO.") < cn.index("Medidor 5000") < cn.index("REGLAS PERMANENTES")
      and _contexto_director("Canal de SEO.", _tmp2, nombres="") == "Canal de SEO.", cn)
    r("💡 el director YA NO recibe la idea de la que salió el vídeo (el autor, 12-oct-2026: «confío más en la transcripción»): aunque exista un idea.json viejo, solo llega el contexto + las reglas",
      "SEO para restaurantes" not in c3 and c3.startswith("Canal de SEO.\n\nREGLAS PERMANENTES") and "_contexto_con_idea" not in globals(), c3)
    r("UTM: sin etiquetas, el título; una grabación sin fecha en el nombre usa la de hoy",
      campana_utm("2026-09-26-13-20-17-x", [], "Creé mi propia herramienta SEO con IA").startswith("2026-09-26-cree-mi-propia")
      and re.fullmatch(r"\d{4}-\d{2}-\d{2}-seo-local", campana_utm("mi-grabacion", ["SEO local"])) is not None,
      campana_utm("mi-grabacion", ["SEO local"]))

    # la tarjeta que repite la escena «explicativa» no sale (el autor, 2026-09-27: su vídeo de Canibalización SEO, 1:52)
    ex_plan = [{"inicio": 20.0, "fin": 48.0, "modo": "cara"},
               {"inicio": 132.0, "fin": 160.0, "modo": "explicativa", "termino": "Canibalización SEO",
                "definicion": "Ocurre cuando dos o más URLs de un mismo dominio compiten y se posicionan en Google por la "
                              "misma palabra clave."}]
    ex_segs = [(20.0, 48.0), (132.0, 140.0), (141.0, 160.0)]           # un corte de relleno parte la escena en dos
    ex = _explicativas_finales(ex_plan, ex_segs, ["cara", "explicativa", "explicativa"])
    r("escena «explicativa» en el vídeo montado, con su texto (los trozos partidos por un corte se juntan)",
      [(x["a"], x["b"], x["termino"]) for x in ex] == [(28.0, 55.0, "Canibalización SEO")], ex)
    canib = {"t": 29.0, "tipo": "definicion", "texto": "Canibalización SEO", "duracion": 3.5,
             "extra": "Múltiples URLs de tu web compitiendo por la misma búsqueda"}
    sin_rep, notas_rep = _sin_repetir_explicativa([canib], ex)
    r("la tarjeta de definición del MISMO término que la escena, encima de ella, no sale (su vídeo del 27-sep)",
      sin_rep == [] and len(notas_rep) == 1 and "Canibalización SEO" in notas_rep[0], notas_rep)
    otra_forma = dict(canib, texto="Canibalización", extra="Varias páginas de tu sitio peleando por una keyword")
    r("dicha con otras palabras, o solo una parte del término («Canibalización»), también se reconoce",
      _sin_repetir_explicativa([otra_forma], ex)[0] == [])
    punto = {"t": 40.0, "tipo": "punto", "texto": "Dos URLs compitiendo por la misma palabra clave", "duracion": 3.5}
    r("una tarjeta de otro tipo que solo repite la definición tampoco sale", _sin_repetir_explicativa([punto], ex)[0] == [])
    nuevas = [{"t": 30.0, "tipo": "dato", "texto": "50% / 50%", "duracion": 3.5,
               "extra": "Reparto de impresiones entre las dos URLs competidoras"},
              {"t": 36.0, "tipo": "punto", "texto": "Alertas rojas: actuar de inmediato", "duracion": 3.5},
              {"t": 42.0, "tipo": "herramienta", "texto": "Google Search Console", "duracion": 3.5},
              {"t": 48.0, "tipo": "definicion", "texto": "Palabra clave", "duracion": 3.5,
               "extra": "El término que la gente escribe en el buscador"}]
    r("las que añaden algo distinto se quedan: una cifra, un punto nuevo, una herramienta, OTRO término",
      _sin_repetir_explicativa(nuevas, ex)[0] == nuevas, _sin_repetir_explicativa(nuevas, ex))
    fuera = [dict(canib, t=10.0), dict(canib, t=58.0), {"t": 45.0, "tipo": "suscribete", "texto": "¡SUSCRÍBETE!"}]
    r("fuera de la escena (antes, o más de 2 s después) no se toca, ni «suscríbete»/«like»",
      _sin_repetir_explicativa(fuera, ex)[0] == fuera)
    r("hasta 2 s después de la escena todavía cuenta como «a la vez»", _sin_repetir_explicativa([dict(canib, t=56.0)], ex)[0] == [])
    r("sin escenas «explicativa» no se toca nada", _sin_repetir_explicativa([canib, punto], [])[0] == [canib, punto])

    # ninguna tarjeta en los primeros ni en los últimos 5 s (el autor, 2026-09-27)
    tj = [{"t": 1.0, "texto": "A"}, {"t": 3.5, "texto": "B"}, {"t": 50.0, "texto": "C"}, {"t": 93.0, "texto": "D"},
          {"t": 97.0, "texto": "E"}]
    fb, notas_fb = _fuera_de_bordes(tj, 100.0, 5.0)
    r("bordes: la del principio se corre al segundo 5 (B; A no, porque pisaría a B); la del final se adelanta si está "
      "cerca (D) y, si tendría que adelantarse mucho, no sale (E)",
      [(x["texto"], x["t"]) for x in fb] == [("B", 5.0), ("C", 50.0), ("D", 91.5)] and len(notas_fb) == 4,
      [(x["texto"], x["t"]) for x in fb])
    fb3, _n3 = _fuera_de_bordes([{"t": 0.3, "texto": "Vibe Coding", "duracion": 3.5}, {"t": 20.0, "texto": "X"}], 806.0, 5.0)
    r("bordes: la definición del segundo 0,3 (su vídeo del 27-sep) sale en el 5, justo después de decirlo",
      [(x["texto"], x["t"]) for x in fb3] == [("Vibe Coding", 5.0), ("X", 20.0)], fb3)
    fb2, _n2 = _fuera_de_bordes([{"t": 4.0, "texto": "A"}, {"t": 6.0, "texto": "B"}], 100.0, 5.0)
    r("bordes: una que al correrse pisaría a la siguiente no sale (no conviven)", [x["texto"] for x in fb2] == ["B"], fb2)
    r("bordes: con margen 0 (clip de menos de 60 s) no se toca nada", _fuera_de_bordes(tj, 100.0, 0.0)[0] == tj)
    ap_b, _nb = _aparta_ctas([{"t": 50.0, "tipo": "punto", "duracion": 3.5}, {"t": 98.0, "tipo": "suscribete", "duracion": 4.0}],
                             100.0, bordes=5.0)
    r("bordes: un «suscríbete» que cae al final se corre hasta acabar 5 s antes del final",
      [t["t"] for t in ap_b if t["tipo"] == "suscribete"] == [91.0], ap_b)

    # tarjetas SEGUIDAS: la nueva reemplaza a la anterior (el autor, 2026-09-26: Argentina / Estados Unidos / México a 2,7 y 2,3 s)
    seguidas = [{"t": 267.7, "tipo": "punto", "texto": "Versión en Argentina", "duracion": 3.5},
                {"t": 270.4, "tipo": "punto", "texto": "Versión en Estados Unidos", "duracion": 3.5},
                {"t": 272.7, "tipo": "punto", "texto": "Versión en México", "duracion": 3.5},
                {"t": 300.0, "tipo": "dato", "texto": "73%", "duracion": 3.5},
                {"t": 301.0, "tipo": "suscribete", "texto": "¡SUSCRÍBETE!", "duracion": 4.0}]
    enc, n_enc = _encadena_tarjetas(seguidas)
    por_t = {t["t"]: t for t in enc}
    r("tres tarjetas seguidas: cada una se quita 0,25 s después de que entra la siguiente (no conviven); la última dura lo "
      "suyo; «suscríbete» no se toca aquí (ya cede el sitio aparte)",
      n_enc == 2 and abs(por_t[267.7]["duracion"] - 2.95) < 1e-6 and abs(por_t[270.4]["duracion"] - 2.55) < 1e-6
      and por_t[272.7]["duracion"] == 3.5 and por_t[300.0]["duracion"] == 3.5 and por_t[301.0]["duracion"] == 4.0,
      [(t["t"], t["duracion"]) for t in enc])
    W_ = lambda w, a, b: {"word": w, "start": a, "end": b}                                                    # noqa: E731
    dichas = [W_("muy", 36.0, 36.2), W_("posiblemente", 36.2, 36.8), W_("casi", 36.9, 37.1), W_("que", 37.1, 37.2),
              W_("el", 37.3, 37.4), W_("90", 37.5, 37.8), W_("%", 37.8, 37.9), W_("vaya.", 38.0, 38.3),
              W_("El", 50.0, 50.1), W_("73", 50.2, 50.5), W_("%", 50.5, 50.6), W_("de", 50.6, 50.7), W_("los", 50.7, 50.8),
              W_("clics.", 50.8, 51.2)]
    est, n_est = _marca_estimados([{"t": 37.6, "tipo": "dato", "texto": "90%", "extra": "de la búsqueda"},
                                   {"t": 50.1, "tipo": "dato", "texto": "73%", "extra": "de los clics"},
                                   {"t": 60.0, "tipo": "dato", "texto": "2,5 M", "extra": "", "estimado": True},
                                   {"t": 70.0, "tipo": "punto", "texto": "Casi todo"}], dichas)
    r("un dato que es un estimado tuyo («muy posiblemente casi que el 90 %») o que el director marcó sale como estimado; "
      "«El 73 % de los clics», no",
      n_est == 2 and est[0].get("estimado") and not est[1].get("estimado") and est[2].get("estimado")
      and not est[3].get("estimado"), est)

    # escenas demasiado largas sin cambio de plano (el autor, 2026-09-25: 3,4 min seguidos de pantalla): un cambio corto ENTRE
    # FRASES (en una pausa REAL del audio), que no tapa lo que enseñas
    from . import voz as _voz
    import tempfile as _tf2
    with _tf2.TemporaryDirectory() as tmp_e:
        wav_e = Path(tmp_e) / "e.wav"
        voces = [(1.0 + 8 * i, 8.3 + 8 * i) for i in range(18)]                  # frases de 7,3 s con 0,7 s de pausa
        _voz._wav_sintetico(wav_e, 150.0, voces)
        au_e = _voz.Audio(wav_e)
        pal_e = []
        for a, b in voces:
            n = int(b - a)
            for j in range(n):
                pal_e.append({"word": f"p{len(pal_e)}" + ("." if j == n - 1 else ""), "start": a + j, "end": a + j + 0.9})
        niv = _voz.nivel_voz(au_e, pal_e)
        rect_e = {"x": 0.08, "y": 0.08, "w": 0.66, "h": 0.78}

        def en_pausa(t):
            return not any(a - 0.05 < t < b + 0.05 for a, b in voces)

        s1, m1, e1, n1 = parte_escenas_largas([(0.0, 150.0)], ["pantalla"], [None], pal_e, [], [], rect_e, cam, 2560, 1440,
                                              60.0, audio=au_e, nivel=niv)
        cortes = [a for a, _b in s1[1:]]
        r("escena de pantalla de 150 s sin nada moviéndose: se mete tu CARA un rato, entre frases (en una pausa real), "
          "y no queda ningún trozo de más de 60 s",
          "cara" in m1 and all(en_pausa(t) for t in cortes) and max(b - a for a, b in s1) <= 60.0
          and abs(sum(b - a for a, b in s1) - 150.0) < 1e-6 and all(x[1] == y[0] for x, y in zip(s1, s1[1:])),
          (list(zip(s1, m1)), n1))
        todo = [{"inicio": 0.0, "fin": 150.0}]
        raton = [(t / 2, 0.30 + 0.02 * ((t // 2) % 3), 0.30) for t in range(0, 300)]
        s2, m2, e2, _ = parte_escenas_largas([(0.0, 150.0)], ["pantalla"], [None], pal_e, todo, raton, rect_e, cam,
                                             2560, 1440, 60.0, audio=au_e, nivel=niv)
        zooms = [(seg, e) for seg, m, e in zip(s2, m2, e2) if m == "pantalla_zoom"]
        r("…si estás enseñando algo con el ratón, NO tu cara (taparía lo que enseñas): un zoom que enseña todo lo que hace "
          "el ratón ahí", zooms and "cara" not in m2 and all(
              e and abs(0.32 - e["puntos"][0][1]) < e["w"] / 2 and abs(0.30 - e["puntos"][0][2]) < e["h"] / 2
              for _s, e in zooms), list(zip(s2, m2)))
        s3, m3, _e3, _ = parte_escenas_largas([(0.0, 150.0)], ["pantalla"], [None], pal_e, todo, [], rect_e, cam, 2560, 1440,
                                              60.0, audio=au_e, nivel=niv)
        r("…y si la pantalla cambia todo el rato sin ratón (bajando por la página), se deja: ya se mueve y tu cara taparía",
          m3 == ["pantalla"], list(zip(s3, m3)))
        s4, m4, _e4, _ = parte_escenas_largas([(0.0, 95.0)], ["cara"], [None], pal_e, [], [], rect_e, cam, 2560, 1440,
                                              60.0, audio=au_e, nivel=niv)
        r("escena de tu cara de 95 s: un rato de pantalla entera, entre frases",
          "pantalla" in m4 and all(en_pausa(a) for a, _b in s4[1:]), list(zip(s4, m4)))
        s5, m5, _e5, _ = parte_escenas_largas([(0.0, 150.0)], ["pantalla"], [None], pal_e, [], [], rect_e, cam, 2560, 1440,
                                              0.0, audio=au_e, nivel=niv)
        r("con videos.escena_max_seg = 0 no se toca nada", s5 == [(0.0, 150.0)] and m5 == ["pantalla"])
        s6, m6, _e6, _ = parte_escenas_largas([(0.0, 40.0), (45.0, 150.0)], ["pantalla", "pantalla"], [None, None], pal_e, [],
                                              [], rect_e, cam, 2560, 1440, 60.0, audio=au_e, nivel=niv)
        r("nunca cruza un corte ni deja un trozo de menos de 2 s junto a sus bordes",
          all(b - a >= 2.0 for a, b in s6) and not any(a < 42.5 < b for a, b in s6), list(zip(s6, m6)))

    r("el título que elegiste manda sobre el 🥇 de la IA (y sin elegir, el 🥇)",
      titulo_del_plan({"titulos_sugeridos": ["A", "B"], "titulo_elegido": "El mío"}) == "El mío"
      and titulo_del_plan({"titulos_sugeridos": ["A", "B"]}) == "A" and titulo_del_plan({}) == "")

    # una tarjeta que la IA anclaba en una pausa que luego acortó el PROGRAMA (clipper/voz.py) sale donde sigue la voz
    tramos = [(0.0, 10.12), (10.82, 20.0), (25.0, 40.0)]                   # pausa acortada 10,12-10,82; la IA cortó 20-25
    prog = [(10.12, 10.82)]
    r("tarjeta dentro de una pausa acortada por el programa: sale justo donde sigue tu voz (no se pierde)",
      abs((_tiempo_final_item(10.5, tramos, prog) or -1) - 10.12) < 1e-6, _tiempo_final_item(10.5, tramos, prog))
    r("tarjeta dentro de un corte de la IA: se pierde como siempre (la IA la puso en algo que ella misma quitó)",
      _tiempo_final_item(22.0, tramos, prog) is None)
    r("tarjeta en un tramo que se ve: el mismo segundo de siempre",
      abs(_tiempo_final_item(30.0, tramos, prog) - (10.12 + 9.18 + 5.0)) < 1e-6)
    sin_pos = [{"inicio": 10.0, "fin": 20.0}]                 # ventana real pero sin x/y (no se pudo ubicar)
    r("una ventana sin x/y (no se pudo ubicar dónde) no cuenta como centro válido",
      _centros_pantalla_zoom(segs, modos, sin_pos) == [None, None, None], sin_pos)

    # --- _fuerza_zoom_en_actividad: el zoom es "el último recurso", no cualquier roce ---
    cara_larga = [{"inicio": 0.0, "fin": 60.0, "modo": "cara"}]
    fugaz = [{"inicio": 20.0, "fin": 22.0, "x": 0.5, "y": 0.5}]         # solo 2s: por debajo del mínimo
    r("una actividad FUGAZ (2s, bajo el mínimo) no parte el tramo — sigue TODO en cara",
      _fuerza_zoom_en_actividad(cara_larga, fugaz) == cara_larga, _fuerza_zoom_en_actividad(cara_larga, fugaz))
    sostenida = [{"inicio": 20.0, "fin": 26.0, "x": 0.5, "y": 0.5}]     # 6s: por encima del mínimo (3s)
    res = _fuerza_zoom_en_actividad(cara_larga, sostenida)
    modos_res = [(r_["inicio"], r_["fin"], r_["modo"]) for r_ in res]
    r("una actividad SOSTENIDA (6s, sobre el mínimo) SÍ fuerza pantalla_zoom justo en ese tramo",
      any(m == "pantalla_zoom" and abs(ini - 20.0) < 0.1 and abs(fin - 26.0) < 0.1 for ini, fin, m in modos_res)
      and any(m == "cara" for _, _, m in modos_res), modos_res)
    # dos ráfagas cortas (2s cada una, aunque estén separadas): ninguna por sí sola llega al
    # mínimo, así que NINGUNA debería forzar el cambio (el fundido de huecos de movimiento.py es
    # quien decide si cuentan como una sola ráfaga larga o no — esta función no las une por su cuenta)
    dos_fugaces = [{"inicio": 10.0, "fin": 12.0, "x": 0.2, "y": 0.2},
                  {"inicio": 15.0, "fin": 17.0, "x": 0.2, "y": 0.2}]
    r("varias ráfagas cortas, cada una bajo el mínimo por separado, no fuerzan nada",
      _fuerza_zoom_en_actividad(cara_larga, dos_fugaces) == cara_larga)

    # --- _tarjetas_explicativa: la tarjeta de término+definición, una por tramo "explicativa" ---
    import shutil
    import tempfile
    marca = dict(config.load().get_path("videos.marca") or {})
    tmp = Path(tempfile.mkdtemp(prefix="zz_explicativa_"))
    try:
        # tramos tal como los deja `_segmentos_con_camara`: la 2ª explicativa (40-50) está partida por un
        # corte de relleno (45-46) en dos trozos de la MISMA escena
        segs_e = [(0.0, 5.0), (5.0, 15.0), (15.0, 40.0), (40.0, 45.0), (46.0, 50.0), (50.0, 80.0)]
        modos_e = ["pantalla", "explicativa", "cara", "explicativa", "explicativa", "cara"]
        plan_ex = [{"inicio": 5.0, "fin": 15.0, "modo": "explicativa",
                    "termino": "SEO", "definicion": "Optimizar tu web para Google."},
                   {"inicio": 40.0, "fin": 50.0, "modo": "explicativa",
                    "termino": "CMS", "definicion": "Sistema para gestionar contenido."},
                   {"inicio": 60.0, "fin": 70.0, "modo": "cara"}]           # no es explicativa: se ignora
        r("sin tramos 'explicativa' en el plan, no genera ninguna tarjeta",
          _tarjetas_explicativa([], segs_e, modos_e, marca, 1920, 1080, tmp / "a") == [None] * 6)
        r("con un plan de explicativa pero SIN tramos en ese modo, tampoco (nada que tapar)",
          _tarjetas_explicativa(plan_ex, segs_e, ["cara"] * 6, marca, 1920, 1080, tmp / "a2") == [None] * 6)

        cards = _tarjetas_explicativa(plan_ex, segs_e, modos_e, marca, 1920, 1080, tmp / "b")
        r("cada tramo 'explicativa' recibe su tarjeta y los demás (pantalla, cara) ninguna",
          [c is not None for c in cards] == [False, True, False, True, True, False], cards)
        r("los dos trozos de una escena partida por un corte COMPARTEN la misma tarjeta",
          cards[3] is not None and cards[3] == cards[4], (cards[3], cards[4]))
        r("dos escenas distintas tienen tarjetas distintas (cada una su término)",
          cards[1] is not None and cards[1] != cards[3], (cards[1], cards[3]))
        if cards[1] is not None:
            from PIL import Image as _Im
            with _Im.open(cards[1]) as im_c:
                tam = im_c.size
            r("la tarjeta mide EXACTO 2/3 del ancho (par, la misma fórmula que render.py) x el alto de salida",
              tam == (1280, 1080), tam)

        plan_sin = [{"inicio": 5.0, "fin": 15.0, "modo": "explicativa"}]      # sin término ni definición
        r("una entrada sin término/definición no da tarjeta (quien llama degrada ese tramo a 'cara')",
          _tarjetas_explicativa(plan_sin, segs_e, modos_e, marca, 1920, 1080, tmp / "c") == [None] * 6)
        modos_raros = ["pantalla", "pantalla", "explicativa", "pantalla", "pantalla", "pantalla"]     # el 3er tramo (15-40) no está en ninguna entrada
        r("un tramo que dice 'explicativa' pero no cae en ninguna entrada del plan no revienta ni inventa tarjeta",
          _tarjetas_explicativa(plan_ex, segs_e, modos_raros, marca, 1920, 1080, tmp / "d") == [None] * 6)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- _ken_burns_por_segmento: mitad con pan/zoom, mitad estático, por FAMILIA (decidido por código) ---
    modos_alt = ["cara", "pantalla"] * 6                   # 6 escenas de cara y 6 de pantalla, alternadas
    kb_alt = _ken_burns_por_segmento(modos_alt)
    kb_cara = [kb_alt[i] for i, m in enumerate(modos_alt) if m == "cara"]
    r("la mitad EXACTA de las escenas 'cara' llevan Ken Burns (6 escenas -> 3)",
      sum(1 for k in kb_cara if k != 0) == 3, kb_cara)
    r("los que llevan Ken Burns alternan de dirección (no siempre el mismo lado)",
      len({k for k in kb_cara if k != 0}) == 2, kb_cara)
    r("la PRIMERA escena de una familia nunca lleva Ken Burns (siempre arranca estática)",
      kb_cara[0] == 0, kb_cara)
    r("una escena partida en varios trozos seguidos del mismo modo cuenta UNA sola vez (mismo valor en todos sus trozos)",
      _ken_burns_por_segmento(["cara", "pantalla", "pantalla", "cara", "cara", "pantalla"]) == [0, 0, 0, 1, 1, 1],
      _ken_burns_por_segmento(["cara", "pantalla", "pantalla", "cara", "cara", "pantalla"]))
    r("seis trozos seguidos del mismo modo son UNA escena: sin movimiento (es la primera de su familia)",
      _ken_burns_por_segmento(["cara"] * 6) == [0] * 6)

    modos_mixto = ["cara", "pantalla", "cara", "pantalla_zoom", "cara", "pantalla"]
    kb_mixto = _ken_burns_por_segmento(modos_mixto)
    idx_cara = [i for i, m in enumerate(modos_mixto) if m == "cara"]
    idx_pantalla = [i for i, m in enumerate(modos_mixto) if m in ("pantalla", "pantalla_zoom")]
    r("'pantalla' y 'pantalla_zoom' cuentan como la MISMA familia para el reparto",
      sum(1 for i in idx_pantalla if kb_mixto[i] != 0) == 1, [kb_mixto[i] for i in idx_pantalla])
    r("'cara' es una familia APARTE de 'pantalla' (su propio reparto, no se mezclan)",
      sum(1 for i in idx_cara if kb_mixto[i] != 0) == 1, [kb_mixto[i] for i in idx_cara])

    r("'explicativa' y 'ambos' nunca llevan Ken Burns (no se pidió)",
      _ken_burns_por_segmento(["explicativa", "ambos"]) == [0, 0])
    r("una lista vacía no revienta", _ken_burns_por_segmento([]) == [])

    # --- _con_reintento_auto: Gemini saturado en TODOS los modelos -> lo reintenta solo más tarde (reloj falso) ---
    import tempfile as _tf
    from . import director as _dir

    class _Cfg:
        def __init__(self, esperas=None):
            self.esperas = esperas

        def get_path(self, k, d=None):
            return self.esperas if k == "ai.reintento_auto_min" else d

    def _falla_luego(n_fallos, transitorio=True):
        llamadas = []

        def analizar():
            llamadas.append(1)
            if len(llamadas) <= n_fallos:
                _dir.ultimo_fallo = {"motivo": "503 UNAVAILABLE: high demand", "modelos": ["m"], "transitorio": transitorio}
                return None
            return {"recortes": []}
        return analizar, llamadas

    with _tf.TemporaryDirectory() as tmp_r:
        wk = Path(tmp_r)
        reloj = [1000.0]

        def dormir(s):
            reloj[0] += s
        an, ll = _falla_luego(2)
        plan_r = _con_reintento_auto(an, wk, _Cfg(), _dormir=dormir, _ahora=lambda: reloj[0])
        r("saturado 2 veces y a la 3.ª responde: el plan sale solo, sin que nadie pulse «Reintentar» (esperó 10 + 20 min)",
          plan_r == {"recortes": []} and len(ll) == 3 and abs(reloj[0] - 1000.0 - 30 * 60) < 1, (len(ll), reloj[0] - 1000))
        an, ll = _falla_luego(9)
        r("si sigue saturado tras los 3 reintentos (~1 h), se rinde (NO ANALIZADO, como antes)",
          _con_reintento_auto(an, wk, _Cfg(), _dormir=dormir, _ahora=lambda: reloj[0]) is None and len(ll) == 4, len(ll))
        an, ll = _falla_luego(9, transitorio=False)
        r("un fallo que NO es de saturación (sin clave, cuota del día agotada…) no se reintenta: esperar no lo arregla",
          _con_reintento_auto(an, wk, _Cfg(), _dormir=dormir, _ahora=lambda: reloj[0]) is None and len(ll) == 1)
        an, ll = _falla_luego(9)
        r("con `ai.reintento_auto_min: []` en el config, apagado (como antes)",
          _con_reintento_auto(an, wk, _Cfg([]), _dormir=dormir, _ahora=lambda: reloj[0]) is None and len(ll) == 1)
        an, ll = _falla_luego(9)
        (wk / "STOP").write_text("x")
        r("si se cancela mientras espera, para ahí (no vuelve a llamar a Gemini)",
          _con_reintento_auto(an, wk, _Cfg(), _dormir=dormir, _ahora=lambda: reloj[0]) is None and len(ll) == 1)
    _dir.ultimo_fallo = None
    r("«quota exceeded» (el límite del DÍA) no cuenta como saturación; un 503 «high demand» sí",
      not _dir.es_saturacion("429 RESOURCE_EXHAUSTED: You exceeded your current quota ... limit: 20")
      and _dir.es_saturacion("503 UNAVAILABLE: This model is currently experiencing high demand"))
    return out
