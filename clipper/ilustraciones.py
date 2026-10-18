"""Ilustraciones animadas de Vídeos PRO — la acción nueva junto a cambios de escena, tarjetas y suscríbete.

Cuando el presentador explica algo MUY VISUAL o conceptual (subir tráfico, buscar, seguridad,
automatizar, una idea…), sale en una esquina un símbolo animado que lo refuerza al instante.

Piezas:
  · `ilus_motor.py`    caja de herramientas de dibujo/animación (Pillow puro) y la "pegatina".
  · `ilus_escenas.py`  la biblioteca: cada escena es una función pura de `t` (CATALOGO).
  · este módulo        fotograma -> secuencia PNG cacheada -> selección por reglas -> overlay para ffmpeg.

El director (Gemini) elige QUÉ escena y CUÁNDO por el significado de lo que se dice (ver
`director.py`, campo "ilustraciones"); aquí se decide, por reglas duras, cuáles sobreviven
(separación, densidad, no pisar imágenes) y se dibujan a los fotogramas exactos del vídeo.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

from . import config
from . import ilus_escenas2  # noqa: F401  (registra la segunda tanda de escenas)
from .ilus_escenas import CATALOGO
from .ilus_motor import (SS, Lienzo, Pal, cinta, clamp, e_back, e_in, e_out, lerp, onda, pegatina,
                         prog, transforma)
from .util import log

VERSION = 3                     # súbelo al cambiar el dibujo: invalida la caché de fotogramas
T_IN, T_OUT = 0.50, 0.36        # entrada con rebote / salida (como el fundido de 0.35 s de las tarjetas)
F_DEFECTO = 440                 # lado del fotograma (px) para un lienzo de 1080 de alto
BORDE = 0.0175                  # grosor del contorno de tinta, en fracción del lienzo


def paleta(marca: dict) -> Pal:
    """`videos.marca` con las rutas de fuente resueltas contra la carpeta del proyecto."""
    m = dict(marca)
    for k in ("fuente_texto", "fuente_titulo", "fuente_numero"):
        v = str(m.get(k, ""))
        if v and not Path(v).is_absolute() and not Path(v).exists():
            m[k] = str(config.ROOT / v)
    return Pal(m)


def catalogo_para_prompt() -> str:
    """El catálogo como lista para el prompt del director: «clave» — qué significa."""
    return "\n".join(f'   - "{c.clave}": {c.significa}' for c in CATALOGO.values())


# ------------------------------------------------------------------ movimiento global (entrada / vaivén / salida)
def _reposo(t: float) -> tuple[float, float, float]:
    """Vaivén suave mientras está en pantalla: (escala, rotación°, desplazamiento vertical en fracción)."""
    u = t - T_IN
    return 1.0 + 0.010 * onda(u, 2.3), 1.1 * onda(u, 3.3), 0.006 * onda(u, 2.7)


def movimiento(t: float, dur: float) -> dict:
    if t < T_IN:
        k = t / T_IN
        return {"esc": lerp(0.08, 1.0, e_back(k, 1.75)), "rot": lerp(-11.0, 0.0, e_out(k)),
                "dy": lerp(0.055, 0.0, e_out(k)), "alpha": clamp(t / 0.11)}
    t_sal = dur - T_OUT
    if t > t_sal:
        e0, r0, d0 = _reposo(t_sal)
        k = clamp((t - t_sal) / (T_OUT - 0.03))         # llega a 0 antes del último fotograma
        return {"esc": lerp(e0, 0.84, e_in(k)), "rot": lerp(r0, 7.0, e_in(k)),
                "dy": lerp(d0, -0.035, e_in(k)), "alpha": clamp(1.0 - k ** 1.6)}
    e, r, d = _reposo(t)
    return {"esc": e, "rot": r, "dy": d, "alpha": 1.0}


# ------------------------------------------------------------------ un fotograma
def fotograma(tipo: str, t: float, dur: float, F: int, pal: Pal, etiqueta: str = "") -> Image.Image:
    """El fotograma RGBA (F×F) de la ilustración `tipo` en el segundo `t` de una de duración `dur`."""
    esc_def = CATALOGO[tipo]
    n = F * SS
    l = Lienzo(n, pal, BORDE)
    esc_def.fn(l, t, dur)
    icono = l.im.reduce(SS)
    icono = pegatina(icono, pal, crema_px=0.030 * F, tinta_px=0.014 * F, sombra_px=0.028 * F)
    g = movimiento(t, dur)
    sube = 0.05 * F if etiqueta else 0.0            # con etiqueta el símbolo sube un poco
    out = transforma(icono, g["esc"], g["rot"], 0.0, g["dy"] * F - sube, g["alpha"])
    if etiqueta:
        alto = int(0.135 * F)
        cin = cinta(etiqueta, pal, alto, int(0.98 * F))
        kk = prog(t, 0.28, 0.62)
        k_sal = clamp((t - (dur - T_OUT)) / T_OUT)
        esc_c = (e_back(kk, 2.2) if kk < 1 else 1.0) * (1.0 - 0.16 * e_in(k_sal))
        alfa_c = clamp(kk * 4.0) * clamp(1.0 - k_sal ** 1.6)
        res = transforma(cin, esc_c, -2.2 + 3.0 * (1 - kk), 0, 0, alfa_c, bbox=True,
                         destino=(F, F), centro_dest=(F / 2.0, F * 0.885))
        if res is not None:
            out.alpha_composite(res[0], res[1])
    return out


def hoja_contacto(tipos: list[str], tiempos: list[float], F: int, pal: Pal, dur: float = 3.4,
                  fondo: tuple = (27, 42, 65, 255), etiqueta: str = "") -> Image.Image:
    """Cuadrícula tipos × tiempos sobre un fondo liso: para MIRAR cómo se ven y se mueven."""
    from PIL import ImageDraw
    W, H = F * len(tiempos), F * len(tipos)
    hoja = Image.new("RGBA", (W, H), fondo)
    d = ImageDraw.Draw(hoja)
    for i, tp in enumerate(tipos):
        for j, t in enumerate(tiempos):
            fr = fotograma(tp, t, dur, F, pal, etiqueta)
            hoja.alpha_composite(fr, (j * F, i * F))
            d.text((j * F + 6, i * F + 4), f"{tp} {t:.2f}s", fill=(255, 255, 255, 200))
    return hoja


# ------------------------------------------------------------------ secuencias de fotogramas (con caché)
def tam_fotograma(alto_canvas: int, cfg=None) -> int:
    base = F_DEFECTO
    if cfg is not None:
        try:
            base = int(cfg.get_path("videos.ilustraciones.tam_px_1080", F_DEFECTO))
        except (TypeError, ValueError):
            base = F_DEFECTO
    return max(64, int(round(base * alto_canvas / 1080.0 / 2.0)) * 2)


def _clave(tipo: str, dur: float, etiqueta: str, F: int, fps: int, pal: Pal) -> str:
    h = hashlib.sha1(f"{VERSION}|{tipo}|{dur:.4f}|{etiqueta}|{F}|{fps}|{pal.clave()}".encode("utf-8")).hexdigest()[:14]
    return f"{tipo}-{h}"


def _dibuja_secuencia(args):
    """Trabajo suelto (nivel de módulo para que lo pueda lanzar un ProcessPoolExecutor en Windows)."""
    tipo, dur, etiqueta, F, fps, marca, carpeta = args
    pal = paleta(marca)
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    n = int(round(dur * fps))
    for k in range(n):
        fotograma(tipo, k / fps, dur, F, pal, etiqueta).save(carpeta / f"f_{k:04d}.png", compress_level=1)
    (carpeta / "listo.json").write_text(json.dumps({"n": n, "fps": fps, "F": F}), encoding="utf-8")
    return str(carpeta), n


def _en_rejilla(x: float, fps: int) -> float:
    return round(x * fps) / fps


def selecciona(items: list[dict], dur_final: float, ocupados: list[tuple[float, float]], cfg,
               fps: int = 60) -> list[dict]:
    """Reglas DURAS sobre lo que propone el director (tiempos ya FINALES, tras los recortes):
    no en los primeros segundos ni pegadas al final, separadas entre sí, con un máximo por minuto,
    sin pisar imágenes ilustrativas (misma esquina) y a la rejilla exacta de fotogramas.
    Devuelve los que sobreviven, ordenados por tiempo."""
    def g(k, d):
        return cfg.get_path(f"videos.ilustraciones.{k}", d) if cfg is not None else d
    sep = float(g("separacion_min_seg", 12.0))
    por_min = float(g("max_por_minuto", 2.0))
    t_min = float(g("min_inicio_seg", 1.5))
    dur_def = float(g("duracion", 3.4))
    tope = max(1, int(dur_final / 60.0 * por_min + 0.999))
    out: list[dict] = []
    for it in sorted(items, key=lambda i: float(i["t"])):
        tipo = it.get("tipo")
        if tipo not in CATALOGO:
            continue
        esc = CATALOGO[tipo]
        dur = max(esc.dur_min, min(6.0, float(it.get("duracion") or dur_def)))
        t0 = float(it["t"])
        if t0 < t_min:
            continue
        if t0 + dur > dur_final - 0.5:
            dur = dur_final - 0.5 - t0
            if dur < esc.dur_min:
                continue
        t0, dur = _en_rejilla(t0, fps), _en_rejilla(dur, fps)
        if out and t0 - out[-1]["t"] < sep:
            continue
        if any(t0 < b and t0 + dur > a for a, b in ocupados):
            continue
        if len(out) >= tope:
            break
        out.append({"t": t0, "tipo": tipo, "duracion": dur, "etiqueta": str(it.get("etiqueta") or "").strip()[:28]})
    return out


def genera_secuencias(items: list[dict], marca: dict, out_dir: Path, alto_canvas: int = 1080,
                      fps: int = 60, cfg=None) -> list[dict]:
    """Dibuja a disco (con caché por contenido) los fotogramas de cada ilustración y devuelve los
    overlays para render.py: una SECUENCIA de PNG por ilustración, a los fps EXACTOS del vídeo,
    con su fundido ya dentro (la propia animación de entrada/salida)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    F = tam_fotograma(alto_canvas, cfg)
    pal = paleta(marca)
    trabajos, listos = [], {}
    for i, it in enumerate(items):
        clave = _clave(it["tipo"], it["duracion"], it["etiqueta"], F, fps, pal)
        carpeta = out_dir / clave
        n = int(round(it["duracion"] * fps))
        ok = False
        try:
            ok = json.loads((carpeta / "listo.json").read_text(encoding="utf-8")).get("n") == n
        except (OSError, ValueError):
            pass
        listos[i] = (carpeta, n)
        if not ok:
            shutil.rmtree(carpeta, ignore_errors=True)
            trabajos.append((i, (it["tipo"], it["duracion"], it["etiqueta"], F, fps, marca, str(carpeta))))
    if trabajos:
        nproc = max(1, min(len(trabajos), (os.cpu_count() or 2) - 1, 8))
        hecho = False
        if nproc > 1:
            try:
                with ProcessPoolExecutor(max_workers=nproc) as ex:
                    list(ex.map(_dibuja_secuencia, [a for _, a in trabajos]))
                hecho = True
            except Exception as exc:  # noqa: BLE001 — sin procesos (o uno cayó): se hace en serie
                log(f"  ilustraciones: no pude dibujar en paralelo ({type(exc).__name__}); lo hago en serie.")
        if not hecho:
            for _, a in trabajos:
                _dibuja_secuencia(a)
    overlays = []
    sonido = bool(cfg.get_path("videos.ilustraciones.sonido", False)) if cfg is not None else False
    for i, it in enumerate(items):
        carpeta, n = listos[i]
        if not (carpeta / "listo.json").is_file():
            continue
        overlays.append({
            "patron": (carpeta / "f_%04d.png").as_posix(), "n": n, "fps": fps,
            "inicio": it["t"], "fin": it["t"] + n / fps, "w": F, "h": F, "ancla": "top-right",
            "dx": int(round(F * 0.06)), "dy": -int(round(F * 0.05)),
            "sin_sonido": not sonido, "tipo": "ilustracion", "ilustracion": it["tipo"],
        })
    return overlays


# ------------------------------------------------------------------ vista previa animada (galería del panel)
def previa(tipo: str, etiqueta: str = "", F: int = 220, fps: int = 20, dur: float = 3.4) -> Path:
    """WebP animado (con transparencia) de una escena, cacheado en work/ilustraciones_previas/."""
    if tipo not in CATALOGO:
        raise KeyError(tipo)
    carpeta = config.ROOT / "work" / "ilustraciones_previas"
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = config.load().get_path("videos.marca")
    pal = paleta(marca)
    h = hashlib.sha1(f"{VERSION}|{tipo}|{etiqueta}|{F}|{fps}|{dur}|{pal.clave()}".encode("utf-8")).hexdigest()[:10]
    destino = carpeta / f"{tipo}-{h}.webp"
    if not destino.is_file():
        frames = [fotograma(tipo, k / fps, dur, F, pal, etiqueta) for k in range(int(round(dur * fps)))]
        tmp = destino.with_suffix(".tmp")
        frames[0].save(tmp, format="WEBP", save_all=True, append_images=frames[1:],
                       duration=int(1000 / fps), loop=0, lossless=False, quality=88, method=4)
        os.replace(tmp, destino)
    return destino


# ------------------------------------------------------------------ pruebas automáticas de la biblioteca
def prueba(F: int = 160, rapido: bool = False, dur: float = 3.4) -> list[dict]:
    """Comprueba CADA escena: dibuja sin errores, entra y sale invisible, ocupa un tamaño razonable,
    no se sale del lienzo, se MUEVE durante toda su vida y es determinista. Devuelve una fila por
    escena: {escena, ok, problemas[], ms}."""
    import time
    import numpy as np
    try:
        pal = paleta(config.load().get_path("videos.marca"))
    except Exception as exc:  # noqa: BLE001
        return [{"escena": "(config)", "ok": False, "problemas": [f"no pude leer videos.marca: {exc}"], "ms": 0}]
    ts = (0.0, 0.25, 0.7, 1.3, 1.7, 2.3, 2.7, 3.0, dur - 1 / 60) if rapido else \
         (0.0, 0.1, 0.25, 0.5, 0.7, 1.0, 1.3, 1.7, 2.0, 2.3, 2.7, 3.0, dur - 1 / 60)
    filas = []
    for clave, esc in CATALOGO.items():
        probs: list[str] = []
        t0 = time.time()
        try:
            frs = {t: fotograma(clave, t, dur, F, pal) for t in ts}
            a = {t: np.asarray(fr.getchannel("A"), dtype=np.uint8) for t, fr in frs.items()}
            if a[0.0].max() > 20:
                probs.append("empieza visible (debería entrar desde nada)")
            if a[dur - 1 / 60].max() > 20:
                probs.append("termina visible (debería salir del todo antes del último fotograma)")
            for t in ts:
                if 0.8 < t < dur - 0.5:
                    op = float((a[t] > 128).mean())
                    if op < 0.06:
                        probs.append(f"casi vacía en t={t:.1f} ({op:.0%})")
                    if op > 0.62:
                        probs.append(f"ocupa demasiado en t={t:.1f} ({op:.0%})")
                    ys, xs = np.where(a[t] > 20)
                    if len(xs) and (xs.min() < 1 or ys.min() < 1 or xs.max() > F - 2 or ys.max() > F - 2):
                        probs.append(f"se sale del lienzo en t={t:.1f}")
            for t1, t2 in ((1.3, 1.7), (2.3, 2.7)):
                d = np.abs(np.asarray(frs[t1], dtype=np.int16) - np.asarray(frs[t2], dtype=np.int16)).mean()
                if d < 0.35:
                    probs.append(f"casi no se mueve entre {t1}s y {t2}s (diferencia {d:.2f})")
            r1, r2 = fotograma(clave, 1.5, dur, F, pal).tobytes(), fotograma(clave, 1.5, dur, F, pal).tobytes()
            if r1 != r2:
                probs.append("no es determinista (mismo t, distinto fotograma)")
            con_e = fotograma(clave, 1.5, dur, F, pal, "Prueba")
            if (np.asarray(con_e.getchannel("A"))[int(F * 0.80):] > 128).mean() < 0.02:
                probs.append("la etiqueta no se ve")
        except Exception as exc:  # noqa: BLE001
            probs.append(f"error al dibujar: {type(exc).__name__}: {exc}")
        filas.append({"escena": clave, "ok": not probs, "problemas": probs,
                      "ms": round((time.time() - t0) * 1000 / (len(ts) + 3), 1)})
    return filas


def prueba_reglas() -> list[dict]:
    """Casos fijos de `selecciona` (las reglas duras que cortan lo que propone el director)."""
    class _Cfg:
        _v = {"max_por_minuto": 2.0, "separacion_min_seg": 12, "min_inicio_seg": 1.5, "duracion": 3.4}

        def get_path(self, k, d=None):
            return self._v.get(k.rsplit(".", 1)[-1], d)

    c, filas = _Cfg(), []

    def caso(nombre, items, dur_final, ocupados, esperado, fps=60):
        got = [round(x["t"], 3) for x in selecciona(items, dur_final, ocupados, c, fps)]
        esp = [round(x, 3) for x in esperado]
        filas.append({"caso": nombre, "ok": got == esp, "detalle": f"salió {got}, esperado {esp}"})

    def it(t, tipo="lupa", d=3.4):
        return {"t": t, "tipo": tipo, "duracion": d, "etiqueta": ""}

    caso("separación mínima entre dos", [it(5), it(10), it(20)], 120, [], [5, 20])
    caso("tope por minuto", [it(5), it(20), it(40)], 60, [], [5, 20])
    caso("ninguna en el arranque", [it(0.5), it(3)], 120, [], [3])
    caso("ninguna pegada al final", [it(30), it(59)], 60, [], [30])
    caso("clave desconocida", [it(5, "unicornio"), it(20)], 120, [], [20])
    caso("no pisa una imagen en la misma esquina", [it(20), it(40)], 120, [(19.0, 23.0)], [40])
    caso("a la rejilla de fotogramas", [it(10.013)], 120, [], [round(10.013 * 60) / 60])
    caso("duración máxima 6 s", [it(10, d=20.0)], 120, [], [10])
    out = selecciona([it(10, d=20.0)], 120, [], c, 60)
    filas.append({"caso": "duración topada a 6 s", "ok": bool(out) and abs(out[0]["duracion"] - 6.0) < 1e-6,
                  "detalle": f"duracion={out[0]['duracion'] if out else None}"})
    return filas


# ------------------------------------------------------------------ CLI de trabajo: python -m clipper.ilustraciones
def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Ilustraciones animadas")
    ap.add_argument("--hoja", help="tipos separados por coma (o 'todas')")
    ap.add_argument("--t", default="0.15,0.3,0.5,0.9,1.5,2.2,3.2", help="segundos a mostrar")
    ap.add_argument("--tam", type=int, default=260)
    ap.add_argument("--dur", type=float, default=3.4)
    ap.add_argument("--etiqueta", default="")
    ap.add_argument("--claro", action="store_true", help="fondo claro en vez de oscuro")
    ap.add_argument("--out", default="hoja.png")
    ap.add_argument("--prueba", action="store_true", help="comprueba todas las escenas")
    a = ap.parse_args()
    if a.prueba:
        filas = prueba()
        for f in filas:
            print(("OK   " if f["ok"] else "MAL  ") + f"{f['escena']:<14} {f['ms']:>6} ms/fotograma  " + "; ".join(f["problemas"]))
        reglas = prueba_reglas()
        for f in reglas:
            print(("OK   " if f["ok"] else "MAL  ") + f"regla: {f['caso']:<40} {'' if f['ok'] else f['detalle']}")
        print(f"\n{sum(f['ok'] for f in filas)} de {len(filas)} escenas y {sum(f['ok'] for f in reglas)} de {len(reglas)} reglas correctas")
        raise SystemExit(0 if all(f["ok"] for f in filas + reglas) else 1)
    pal = paleta(config.load().get_path("videos.marca"))
    tipos = list(CATALOGO) if a.hoja in (None, "todas") else [x.strip() for x in a.hoja.split(",")]
    fondo = (245, 240, 230, 255) if a.claro else (27, 42, 65, 255)
    hoja_contacto(tipos, [float(x) for x in a.t.split(",")], a.tam, pal, a.dur, fondo, a.etiqueta).save(a.out)
    print("guardada", a.out)


if __name__ == "__main__":
    _cli()
