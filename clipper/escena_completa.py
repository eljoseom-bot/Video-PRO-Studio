"""ESCENA A PANTALLA COMPLETA (el autor, 2026-09-26, idea sacada del vídeo de Alex Hormozi): un cambio de escena SIN tu cara —
la pantalla entera con un fondo de la marca y UNA ilustración grande que representa lo que dices, con tu voz debajo y la
frase clave escrita. Reusa las 32 ilustraciones de `ilus_escenas.CATALOGO` (dibujo vectorial: sirve a cualquier tamaño)
a cámara lenta (`lento`: su animación tarda el doble en desplegarse).

el autor vio la muestra y: «me encanta… implementalo». Sus reglas: especial («casos particulares»), pero AL MENOS UNA por vídeo;
los dos fondos, alternados 50/50 en orden — la 1.ª NARANJA («tiene más esencia de tu canal»), la 2.ª crema, la 3.ª naranja…

En el vídeo: el director la marca en su llamada de siempre (`escenas` del plan: segundo, ilustración, frase); un plan de antes
(o si no marcó ninguna) usa por código la mejor de sus ilustraciones de la esquina. `selecciona` aplica las reglas (nunca
encima de una tarjeta, una imagen, una escena «explicativa» ni de una demostración en pantalla; separadas; pocas) y `genera`
dibuja sus fotogramas (caché por contenido) como una CAPA a pantalla completa para `render.render_merged` (sin tramos nuevos:
la RAM del render no sube), con «whoosh» al entrar y al volver.

Muestra suelta sobre un vídeo ya editado (para probar un momento concreto):
    python -m clipper.escena_completa --video videos/<jid>/pro.mp4 --t 30.9 --tipo grafica_sube --frase "ESTO VA A EVOLUCIONAR"
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from .ilus_escenas import CATALOGO
from .ilus_motor import SS, Lienzo, Pal, clamp, e_back, e_out, fuente, onda, prog, transforma
from .ilustraciones import BORDE, paleta
from .util import log

VERSION = 1
_ESTILOS = ("capitulo", "grafica", "chat", "veredicto", "cita")     # = escenas_estilo.ESTILOS (aquí para no importar al cargar)
LENTO = 2.0                  # la animación de la ilustración tarda el doble en desplegarse que en la esquina
T_POP = 0.55                 # la ilustración entra con un rebote (el corte de plano ya es seco)


# ------------------------------------------------------------------ fondo
@lru_cache(maxsize=8)
def _fondo(W: int, H: int, tono: str, pal_clave: str, colores: tuple) -> Image.Image:
    """El fondo liso de la marca con una TRAMA de puntos (la estética de cómic de sus miniaturas): puntos pequeños en el
    centro que crecen hacia los bordes, para que no se vea vacío ni compita con la ilustración."""
    base, punto = colores
    im = Image.new("RGB", (W, H), base)
    d = ImageDraw.Draw(im)
    paso = max(12, round(H / 40))
    cx, cy = W / 2.0, H / 2.0
    rmax = math.hypot(cx, cy)
    for j, y in enumerate(range(paso // 2, H + paso, paso)):
        for x in range((paso // 2) if j % 2 == 0 else paso, W + paso, paso):
            k = math.hypot(x - cx, y - cy) / rmax             # 0 en el centro, 1 en las esquinas
            r = paso * (0.06 + 0.16 * k ** 1.6)
            if r >= 0.6:
                d.ellipse((x - r, y - r, x + r, y + r), fill=punto)
    return im


def fondo(W: int, H: int, pal: Pal, tono: str = "crema") -> Image.Image:
    # la trama, SUAVE: un tono apenas más oscuro que el fondo (con el beige oscuro, a pantalla completa, distraía)
    if tono == "naranja":
        colores = (pal.naranja[:3], _mezcla3(pal.naranja, pal.naranja_osc, 0.55))
    else:
        colores = (pal.crema[:3], _mezcla3(pal.crema, pal.beige_osc, 0.45))
    return _fondo(W, H, tono, pal.clave(), colores)


def _mezcla3(c1, c2, k: float) -> tuple:
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * k)) for i in range(3))


def _dilata_nitida(alfa: Image.Image, px: float) -> Image.Image:
    """Silueta agrandada `px` píxeles con el borde DURO (desenfoque + un solo umbral). La de `ilus_motor.dilata` deja una
    rampa suave que en la esquina (400 px) no se nota, pero a pantalla completa emborronaba el contorno y la sombra."""
    from PIL import ImageFilter
    if px < 0.5:
        return alfa
    b = alfa.filter(ImageFilter.GaussianBlur(max(0.6, px / 1.48)))
    return b.point(lambda v: 255 if v > 20 else 0)


def _pegatina_nitida(icono: Image.Image, pal: Pal, crema_px: float, tinta_px: float, sombra_px: float) -> Image.Image:
    """Como `ilus_motor.pegatina` (halo crema, contorno de tinta y sombra dura abajo-derecha) pero con bordes duros:
    se aplica al dibujo a tamaño doble (SS) y se reduce después, así queda nítido y sin dientes."""
    W, H = icono.size
    a = icono.getchannel("A")
    a_crema = _dilata_nitida(a, crema_px)
    a_tinta = _dilata_nitida(a, crema_px + tinta_px)
    s = int(round(sombra_px))
    ink = pal.tinta[:3]
    out = Image.new("RGBA", (W, H), ink + (0,))
    if s > 0:
        sombra_a = Image.new("L", (W, H), 0)
        sombra_a.paste(a_tinta, (s, s))
        cap = Image.new("RGBA", (W, H), ink + (255,))
        cap.putalpha(sombra_a)
        out.alpha_composite(cap)
    for alfa, color in ((a_tinta, ink), (a_crema, pal.crema[:3])):
        cap = Image.new("RGBA", (W, H), color + (255,))
        cap.putalpha(alfa)
        out.alpha_composite(cap)
    out.alpha_composite(icono)
    return out


# ------------------------------------------------------------------ la frase
def _lineas(palabras: list[str], ancho_max: float, f, d) -> list[list[str]]:
    """Una línea si cabe; si no, dos, partidas por la palabra que deja las dos más parecidas."""
    if d.textlength(" ".join(palabras), font=f) <= ancho_max or len(palabras) < 2:
        return [palabras]
    k = min(range(1, len(palabras)),
            key=lambda i: max(d.textlength(" ".join(palabras[:i]), font=f), d.textlength(" ".join(palabras[i:]), font=f)))
    return [palabras[:k], palabras[k:]]


def frase_img(frase: str, pal: Pal, tono: str, W: int, H: int) -> Image.Image | None:
    """La frase clave en MAYÚSCULAS con la letra de la marca: en tinta con la última palabra (o la cifra) en naranja
    sobre el fondo crema; en crema con contorno de tinta sobre el naranja. Sombra dura de tinta, como las tarjetas."""
    palabras = str(frase or "").upper().split()
    if not palabras:
        return None
    px = round(0.088 * H)
    ancho_max = 0.84 * W
    tmp = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    f = fuente(pal.fuente_titulo, px)
    lineas = _lineas(palabras, ancho_max, f, tmp)
    while px > 30 and max(tmp.textlength(" ".join(l), font=f) for l in lineas) > ancho_max:
        px -= 4
        f = fuente(pal.fuente_titulo, px)
        lineas = _lineas(palabras, ancho_max, f, tmp)
    con_cifra = [i for i, x in enumerate(palabras) if any(c.isdigit() for c in x)]
    realce = set(con_cifra) if con_cifra else {len(palabras) - 1}
    if tono == "naranja":
        color, color_realce, contorno = pal.crema, pal.dorado, max(3, round(px * 0.075))
    else:
        color, color_realce, contorno = pal.tinta, pal.naranja, 0
    sombra = max(3, round(px * 0.07))
    alto_l = round(px * 1.12)
    anchos = [tmp.textlength(" ".join(l), font=f) for l in lineas]
    w = int(max(anchos) + 2 * contorno + sombra + 4)
    h = int(alto_l * len(lineas) + 2 * contorno + sombra + round(px * 0.25))
    img = Image.new("RGBA", (w, h), pal.tinta[:3] + (0,))
    d = ImageDraw.Draw(img)
    idx = 0
    for n, (linea, ancho) in enumerate(zip(lineas, anchos)):
        x = (w - sombra - ancho) / 2.0
        y = contorno + n * alto_l
        for palabra in linea:
            c = color_realce if idx in realce else color
            ancho_p = d.textlength(palabra, font=f)
            # sombra dura (abajo-derecha) y, sobre el naranja, contorno de tinta
            d.text((x + sombra, y + sombra), palabra, font=f, fill=pal.tinta,
                   stroke_width=contorno, stroke_fill=pal.tinta)
            d.text((x, y), palabra, font=f, fill=c, stroke_width=contorno, stroke_fill=pal.tinta)
            x += ancho_p + d.textlength(" ", font=f)
            idx += 1
    return img


# ------------------------------------------------------------------ un fotograma
def fotograma(tipo: str, t: float, dur: float, W: int, H: int, pal: Pal, frase: str = "", tono: str = "crema",
              lento: float = LENTO, datos: dict | None = None) -> Image.Image:
    """El fotograma RGB (W×H) de la escena en el segundo `t` (de `dur`): el fondo, la ilustración `tipo` grande (su
    animación a `lento`× menos velocidad) con un rebote al entrar, y la frase debajo, que entra con un «pop».
    Si `tipo` es un ESTILO (capitulo, grafica, chat, veredicto, cita: clipper/escenas_estilo.py) se dibuja ese con sus `datos`."""
    if tipo in _ESTILOS:
        from . import escenas_estilo as _est
        return _est.fotograma(tipo, t, dur, W, H, pal, datos or {}, tono)
    out = fondo(W, H, pal, tono).copy().convert("RGBA")
    esc_def = CATALOGO[tipo]
    F = round(0.84 * H) if frase else round(0.98 * H)          # el dibujo ocupa ~57 % del alto: protagonista
    ts, dur_s = t / max(1.0, lento), dur / max(1.0, lento)
    l = Lienzo(F * SS, pal, BORDE)
    esc_def.fn(l, ts, dur_s)
    icono = _pegatina_nitida(l.im, pal, crema_px=0.030 * F * SS, tinta_px=0.014 * F * SS,
                             sombra_px=0.028 * F * SS).reduce(SS)
    # entra con rebote y luego un vaivén muy suave; NO sale: el corte de vuelta a tu vídeo es seco
    k = prog(t, 0.0, T_POP)
    esc = (0.30 + 0.70 * e_back(k, 1.6)) * (1.0 + 0.008 * onda(t, 3.1)) if k < 1 else 1.0 + 0.008 * onda(t, 3.1)
    rot = (-6.0 * (1 - e_out(k))) + 0.8 * onda(t, 4.3)
    cy = (0.40 if frase else 0.50) * H
    res = transforma(icono, esc, rot, 0, 0, clamp(t / 0.10), bbox=True, destino=(W, H), centro_dest=(W / 2.0, cy))
    if res is not None:
        out.alpha_composite(res[0], res[1])
    if frase:
        img = _frase_cache(frase, pal.clave(), tono, W, H, pal)
        if img is not None:
            kf = prog(t, 0.30, 0.65)
            if kf > 0:
                ef = e_back(kf, 2.0) if kf < 1 else 1.0
                res = transforma(img, 0.55 + 0.45 * ef, 0, 0, 0, clamp(kf * 3), bbox=True, destino=(W, H),
                                 centro_dest=(W / 2.0, 0.835 * H))
                if res is not None:
                    out.alpha_composite(res[0], res[1])
    return out.convert("RGB")


_FRASES: dict = {}


def _frase_cache(frase, clave, tono, W, H, pal):
    k = (frase, clave, tono, W, H)
    if k not in _FRASES:
        _FRASES[k] = frase_img(frase, pal, tono, W, H)
    return _FRASES[k]


# ------------------------------------------------------------------ secuencia de fotogramas
def _trozo(args):
    """Trabajo suelto (a nivel de módulo: ProcessPoolExecutor en Windows lo necesita así)."""
    tipo, dur, fps, W, H, marca, frase, tono, lento, carpeta, desde, hasta, datos = args
    pal = paleta(marca)
    carpeta = Path(carpeta)
    for k in range(desde, hasta):
        fotograma(tipo, k / fps, dur, W, H, pal, frase, tono, lento, datos).save(carpeta / f"f_{k:04d}.png", compress_level=1)
    return hasta - desde


def secuencia(tipo: str, dur: float, fps: int, W: int, H: int, marca: dict, frase: str, tono: str, carpeta: Path,
              lento: float = LENTO, datos: dict | None = None) -> int:
    """Los fotogramas PNG de la escena (`f_0000.png`…) a los fps EXACTOS del vídeo, repartidos entre procesos."""
    carpeta = Path(carpeta)
    shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True)
    n = int(round(dur * fps))
    partes = max(1, min(8, (__import__("os").cpu_count() or 2) - 1))
    cortes = [round(i * n / partes) for i in range(partes + 1)]
    trabajos = [(tipo, dur, fps, W, H, marca, frase, tono, lento, str(carpeta), a, b, datos)
                for a, b in zip(cortes, cortes[1:]) if b > a]
    try:
        with ProcessPoolExecutor(max_workers=len(trabajos)) as ex:
            list(ex.map(_trozo, trabajos))
    except Exception as exc:  # noqa: BLE001 — sin procesos (o uno cayó): en serie
        log(f"  escena: no pude dibujar en paralelo ({type(exc).__name__}); lo hago en serie.")
        for t in trabajos:
            _trozo(t)
    (carpeta / "listo.json").write_text(json.dumps({"n": n, "fps": fps, "v": VERSION}), encoding="utf-8")
    return n


# ------------------------------------------------------------------ en el vídeo: elegir, colorear y dibujar
_DEF = {"activo": True, "minimo": 1, "cada_seg": 150.0, "duracion": 6.0, "duracion_min": 4.5, "duracion_max": 7.5,
        "inicio_min_seg": 5.0, "final_libre_seg": 2.0, "margen_seg": 0.3, "lento": LENTO,
        # las escenas de ESTILO (capítulo, gráfica de un estudio, chat, veredicto, frase): solo salen si tu tema encaja
        "estilo_activo": True, "estilo_cada_seg": 100.0, "estilo_separacion_seg": 35.0,
        # lo que cada canal permite (clipper/ediciones_canal.py): qué estilos y si la escena con ilustración grande sale
        "estilos_permitidos": ["capitulo", "grafica", "chat", "veredicto", "cita"], "ilustracion_activo": True}


def parametros(cfg) -> dict:
    """`videos.escenas` de config.yaml encima de los valores por defecto."""
    p = dict(_DEF)
    try:
        propios = cfg.get_path("videos.escenas") or {}
    except Exception:  # noqa: BLE001
        propios = {}
    for k, v in (propios.items() if isinstance(propios, dict) else []):
        if k in p and v is not None:
            try:
                p[k] = bool(v) if isinstance(p[k], bool) else type(p[k])(v)
            except (TypeError, ValueError):
                pass
    return p


def tonos(n: int) -> list[str]:
    """El fondo de cada escena, en orden (el autor): la 1.ª naranja, la 2.ª crema, la 3.ª naranja… — 50/50 y con una sola, naranja."""
    return ["naranja" if i % 2 == 0 else "crema" for i in range(n)]


def _frase(texto) -> str:
    t = " ".join(str(texto or "").replace("{", "").replace("}", "").replace("\\", "").split()).strip()
    return t[:36].rsplit(" ", 1)[0] if len(t) > 36 else t


def candidatas(plan: dict, words: list[dict], mapa) -> list[dict]:
    """Las `escenas` que marcó el director, en tiempo FINAL (`mapa`), cada una en el segundo en que DICES su frase si la
    encuentra (`ritmo.ancla`), si no en el que dio. Las que caen en un corte, fuera."""
    from . import ritmo as _ritmo
    out = []
    for it in plan.get("escenas") or []:
        try:
            t = float(it["t"])
        except (KeyError, TypeError, ValueError):
            continue
        tipo, frase = str(it.get("tipo") or "").strip(), _frase(it.get("frase"))
        if tipo not in CATALOGO:
            continue
        m = _ritmo.ancla(frase, t, words) if frase and words else None
        if m and m[2]:
            t = float(words[m[0]]["start"])
        tf = mapa(t)
        if tf is None:
            continue
        try:
            dur = float(it.get("duracion") or _DEF["duracion"])
        except (TypeError, ValueError):
            dur = _DEF["duracion"]
        out.append({"t": tf, "tipo": tipo, "frase": frase.upper(), "duracion": dur, "fuente": "ia"})
    return out


def candidatas_estilo(plan: dict, words: list[dict], mapa, descartes: list | None = None) -> list[dict]:
    """Las `escenas_estilo` que marcó el director, en tiempo FINAL (`mapa`). Cada una se comprueba contra lo que DICES: su `cita` tiene que estar en
    la transcripción cerca de ese segundo (y, en una gráfica, alguna de sus cifras): lo que sale en pantalla nunca es un invento. La escena entra
    cuando empiezas a decir esa cita. `descartes` (opcional) recibe el motivo de cada una que se tira."""
    from . import escenas_estilo as _est
    from . import ritmo as _ritmo
    out = []
    for it in plan.get("escenas_estilo") or []:
        if not isinstance(it, dict):
            continue
        c = _est.limpia({**(it.get("datos") or {}), **it})
        if c is None:
            if descartes is not None:
                descartes.append(f"{it.get('estilo')} sin datos suficientes")
            continue
        if words and not _est.cita_en_transcripcion(c["cita"], words, c["t"]):
            if descartes is not None:
                descartes.append(f"{c['estilo']}: su cita no está en lo que dices")
            continue
        if c["estilo"] == "grafica" and words and not _est.cifras_dichas(c["datos"], words, c["t"]):
            if descartes is not None:
                descartes.append("grafica: ninguna de sus cifras se dice en el vídeo")
            continue
        t = c["t"]
        m = _ritmo.ancla(c["cita"], t, words) if words else None
        if m and m[2]:
            t = float(words[m[0]]["start"])
        tf = mapa(t)
        if tf is None:
            continue
        d = c["datos"]
        etiqueta = {"capitulo": f"{d.get('numero')} · {d.get('titulo', '')}", "grafica": d.get("titulo", ""), "chat": d.get("pregunta", ""),
                    "veredicto": " / ".join(i["texto"] for i in d.get("items", [])), "cita": d.get("frase", "")}[c["estilo"]]
        out.append({"t": tf, "tipo": c["estilo"], "frase": str(etiqueta).upper(), "duracion": c["duracion"], "fuente": "estilo", "datos": d})
    return out


def de_ilustraciones(ilus: list[dict]) -> list[dict]:
    """Sin escenas del director (un plan de antes, o no marcó ninguna): las ilustraciones de la esquina que eligió (tiempo
    FINAL) como candidatas, con su etiqueta de frase. Así TODO vídeo tiene al menos una (el autor: «al menos una vez por vídeo»)."""
    return [{"t": float(i["t"]), "tipo": i["tipo"], "frase": _frase(i.get("etiqueta")).upper(),
             "duracion": _DEF["duracion"], "fuente": "ilustracion"} for i in ilus or [] if i.get("tipo") in CATALOGO]


def selecciona(cands: list[dict], dur_final: float, ocupados: list[tuple[float, float]],
               vivos: list[tuple[float, float]], p: dict, fps: int = 60) -> list[dict]:
    """Cuáles salen: las del director primero (en su orden) y, si no queda ninguna, las de las ilustraciones. Nunca encima
    de `ocupados` (tarjetas, imágenes, escenas «explicativa») ni de `vivos` (una demostración en pantalla: ahí lo importante
    es la pantalla), ni en los primeros `inicio_min_seg` ni pegada al final; separadas `cada_seg` y como mucho una cada
    `cada_seg` del vídeo (al menos `minimo` si cabe). A la rejilla exacta de fotogramas y con su fondo (`tonos`)."""
    def en_rejilla(x: float) -> float:
        return round(x * fps) / fps

    from . import escenas_estilo as _est
    elegidas: list[dict] = []
    m = float(p["margen_seg"])

    def cabe(t0: float, dur: float) -> bool:
        if t0 < float(p["inicio_min_seg"]):
            return False
        return not any(t0 - m < b and t0 + dur + m > a for a, b in list(ocupados) + list(vivos))

    # 1) las de ESTILO (solo si tu tema encaja: cada una trae su cita comprobada), pocas y separadas
    if p.get("estilo_activo", True):
        tope_e = int(dur_final // float(p["estilo_cada_seg"]))
        sep_e = float(p["estilo_separacion_seg"])
        permitidos = set(p.get("estilos_permitidos") or [])
        for c in sorted((c for c in cands if c.get("fuente") == "estilo" and c.get("tipo") in permitidos), key=lambda c: c["t"]):
            if sum(1 for e in elegidas if e.get("fuente") == "estilo") >= tope_e:
                break
            mn, mx, df = _est.DURACIONES.get(c["tipo"], (p["duracion_min"], p["duracion_max"], p["duracion"]))
            t0 = en_rejilla(float(c["t"]))
            dur = max(mn, min(mx, float(c.get("duracion") or df)))
            if t0 + dur > dur_final - float(p["final_libre_seg"]):
                dur = dur_final - float(p["final_libre_seg"]) - t0
                if dur < mn:
                    continue
            dur = en_rejilla(dur)
            if not cabe(t0, dur) or any(abs(t0 - e["t"]) < max(sep_e, e["duracion"] + 3.0) for e in elegidas):
                continue
            elegidas.append({**c, "t": t0, "duracion": dur})
    # 2) las de la ilustración: las del director primero y, si no queda ninguna, las de la esquina. Con escenas de estilo ya no hace falta «al menos una».
    hay_estilo = any(e.get("fuente") == "estilo" for e in elegidas)
    ilus_ok = bool(p.get("ilustracion_activo", True))
    tope = max(0 if hay_estilo else int(p["minimo"]), int(dur_final // float(p["cada_seg"]))) if ilus_ok else 0
    n_ilus = 0
    for grupo in ("ia", "ilustracion"):
        if grupo == "ilustracion" and n_ilus:
            break                                          # las de la esquina son solo el plan B
        for c in sorted((c for c in cands if c.get("fuente") == grupo), key=lambda c: c["t"]):
            if n_ilus >= tope:
                break
            t0 = en_rejilla(float(c["t"]))
            dur = max(float(p["duracion_min"]), min(float(p["duracion_max"]), float(c.get("duracion") or p["duracion"])))
            if t0 < float(p["inicio_min_seg"]):
                continue
            if t0 + dur > dur_final - float(p["final_libre_seg"]):
                dur = dur_final - float(p["final_libre_seg"]) - t0
                if dur < float(p["duracion_min"]):
                    continue
            dur = en_rejilla(dur)
            if not cabe(t0, dur):
                continue
            if any(abs(t0 - e["t"]) < (float(p["cada_seg"]) if e.get("fuente") != "estilo"
                                       else float(p.get("estilo_separacion_seg", 35.0)) + e["duracion"]) for e in elegidas):
                continue
            elegidas.append({**c, "t": t0, "duracion": dur})
            n_ilus += 1
    elegidas.sort(key=lambda e: e["t"])
    # el fondo naranja/crema (1.ª naranja, 2.ª crema…) alterna entre las que lo usan: las ilustraciones y las frases; el resto lleva el suyo
    usan = [e for e in elegidas if e["tipo"] in CATALOGO or e["tipo"] == "cita"]
    for e, tono in zip(usan, tonos(len(usan))):
        e["tono"] = tono
    for e in elegidas:
        e.setdefault("tono", "oscuro")
    return elegidas


def _clave_escena(e: dict, fps: int, W: int, H: int, pal: Pal, lento: float) -> str:
    import hashlib
    base = [VERSION, e["tipo"], e["frase"], e["tono"], round(e["duracion"], 4), fps, W, H, lento, pal.clave()]
    if e.get("datos"):
        base.append(e["datos"])
    s = json.dumps(base, ensure_ascii=False)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def genera(escenas: list[dict], marca: dict, out_dir: Path, W: int, H: int, fps: int, cfg=None) -> list[dict]:
    """Los fotogramas de cada escena (caché por contenido en `out_dir/<clave>/`) -> las capas para `render.render_merged`:
    a pantalla completa (`ancla: completa`), a los fps exactos del vídeo, con «whoosh» al entrar y al volver."""
    p = parametros(cfg) if cfg is not None else dict(_DEF)
    pal = paleta(marca)
    out_dir = Path(out_dir)
    capas = []
    for e in escenas:
        carpeta = out_dir / _clave_escena(e, fps, W, H, pal, float(p["lento"]))
        n = int(round(e["duracion"] * fps))
        try:
            listo = json.loads((carpeta / "listo.json").read_text(encoding="utf-8")).get("n") == n
        except (OSError, ValueError):
            listo = False
        if not listo:
            secuencia(e["tipo"], e["duracion"], fps, W, H, marca, e["frase"], e["tono"], carpeta, float(p["lento"]), e.get("datos"))
        capas.append({"patron": (carpeta / "f_%04d.png").as_posix(), "n": n, "fps": fps, "inicio": e["t"],
                      "fin": e["t"] + n / fps, "w": W, "h": H, "ancla": "completa", "sonido": "whoosh",
                      "sonido_fin": True, "tipo": "escena", "escena": e["tipo"], "frase": e["frase"], "tono": e["tono"]})
    return capas


# ------------------------------------------------------------------ la MUESTRA sobre un vídeo real
def muestra(video: Path, t0: float, dur: float, tipo: str, frase: str, tono: str, salida: Path, cfg=None,
            antes: float = 2.5, despues: float = 2.5, lento: float = LENTO) -> Path:
    """Un trozo del vídeo YA editado (de `t0 - antes` a `t0 + dur + despues`) con la escena encima de `t0` a `t0 + dur`:
    corte seco de ida y vuelta, el «whoosh» de cambio de escena en cada corte y tu voz sin tocar debajo."""
    from . import ingest, render
    cfg = cfg or config.load()
    meta = ingest.probe(Path(video))
    W, H = int(meta["width"]), int(meta["height"])
    fps = int(round(float(meta.get("fps") or 30)))
    marca = dict(cfg.get_path("videos.marca") or {})
    carpeta = Path(salida).with_suffix("") / "fotogramas"
    n = secuencia(tipo, dur, fps, W, H, marca, frase, tono, carpeta, lento)
    ini = max(0.0, t0 - antes)
    total = (t0 - ini) + dur + despues
    entra = t0 - ini
    whoosh = render._whoosh_path(cfg)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{ini:.3f}", "-t", f"{total:.3f}", "-i", str(video),
           "-framerate", str(fps), "-start_number", "0", "-i", str(carpeta / "f_%04d.png")]
    fc = (f"[1:v]format=yuv420p,setpts=PTS-STARTPTS+{round(entra * fps) / fps:.6f}/TB[esc];"
          f"[0:v]setpts=PTS-STARTPTS[base];"
          f"[base][esc]overlay=0:0:eof_action=pass:enable='between(t,{entra:.3f},{entra + n / fps:.3f})'[v]")
    if whoosh and Path(whoosh).is_file():
        cmd += ["-i", str(whoosh)]
        fc += (f";[2:a]asplit=2[w1][w2];[w1]adelay={int(max(0, entra - 0.15) * 1000)}|{int(max(0, entra - 0.15) * 1000)},"
               f"volume=0.35[wa];[w2]adelay={int(max(0, entra + dur - 0.15) * 1000)}|{int(max(0, entra + dur - 0.15) * 1000)},"
               f"volume=0.35[wb];[0:a]asetpts=PTS-STARTPTS[va];[va][wa][wb]amix=inputs=3:normalize=0:duration=first[a]")
    else:
        fc += ";[0:a]asetpts=PTS-STARTPTS[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(salida)]
    subprocess.run(cmd, check=True)
    return Path(salida)


def prueba() -> list[dict]:
    """Las reglas del autor (al menos una, pocas, 1.ª naranja y alternando), dónde NO puede caer, el plan B de las
    ilustraciones, el anclaje a la frase dicha y el dibujo (fondo de cada tono, frase, caché) — sin ffmpeg ni Gemini."""
    import tempfile
    out: list[dict] = []

    def caso(n, ok, d=""):
        out.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    caso("fondos en orden: 1.ª naranja, 2.ª crema, 3.ª naranja… (con una sola, naranja)",
         tonos(1) == ["naranja"] and tonos(4) == ["naranja", "crema", "naranja", "crema"], tonos(4))
    p = dict(_DEF)

    def C(t, fuente="ia", tipo="grafica_sube", dur=6.0):
        return {"t": t, "tipo": tipo, "frase": "ESTO VA A EVOLUCIONAR", "duracion": dur, "fuente": fuente}
    una = selecciona([C(30.9)], 490.0, [], [], p)
    caso("una escena de la IA sale en su segundo (a la rejilla de fotogramas), con fondo naranja",
         len(una) == 1 and abs(una[0]["t"] - 30.9) < 1 / 60 and una[0]["tono"] == "naranja", una)
    varias = selecciona([C(3.0), C(30.0), C(100.0), C(200.0), C(350.0), C(420.0)], 490.0, [], [], p)
    caso("pocas: nunca en los primeros 5 s, separadas 2,5 min y como mucho una cada 2,5 min del vídeo; fondos alternados",
         [round(e["t"]) for e in varias] == [30, 200, 350] and [e["tono"] for e in varias] == ["naranja", "crema", "naranja"],
         [(round(e["t"]), e["tono"]) for e in varias])
    tapa = selecciona([C(30.0), C(100.0, "ilustracion")], 490.0, [(33.0, 36.5)], [], p)
    caso("nunca encima de una tarjeta: si la de la IA no cabe, entra la de la esquina (al menos una por vídeo)",
         len(tapa) == 1 and tapa[0]["fuente"] == "ilustracion" and round(tapa[0]["t"]) == 100, tapa)
    demo = selecciona([C(30.0), C(200.0)], 490.0, [], [(25.0, 40.0)], p)
    caso("nunca encima de una demostración en pantalla", [round(e["t"]) for e in demo] == [200], demo)
    caso("la de la esquina es solo el plan B: con una de la IA, no entra",
         [e["fuente"] for e in selecciona([C(30.0), C(200.0, "ilustracion")], 490.0, [], [], p)] == ["ia"])
    final = selecciona([C(484.0)], 490.0, [], [], p)
    corta = selecciona([C(483.0)], 490.0, [], [], p)
    caso("pegada al final: se acorta si cabe (≥ 4,5 s) o no sale",
         final == [] and len(corta) == 1 and abs(corta[0]["duracion"] - 5.0) < 1 / 60, (final, corta))

    ws = [{"word": w, "start": s, "end": s + 0.3} for w, s in (("pero", 30.2), ("esto", 31.0), ("va", 31.3), ("a", 31.5),
                                                               ("evolucionar", 31.6), ("y", 32.4))]
    plan = {"escenas": [{"t": 30.0, "tipo": "grafica_sube", "frase": "esto va a evolucionar", "duracion": 6},
                        {"t": 50.0, "tipo": "inventada", "frase": "NO", "duracion": 6},
                        {"t": 60.0, "tipo": "lupa", "frase": "CORTADA", "duracion": 6}]}
    cands = candidatas(plan, ws, lambda t: None if t >= 55 else t)
    caso("la escena de la IA sale cuando DICES su frase; una ilustración inventada o que cae en un corte, fuera",
         len(cands) == 1 and cands[0]["t"] == 31.0 and cands[0]["frase"] == "ESTO VA A EVOLUCIONAR", cands)
    caso("plan B: las ilustraciones de la esquina, con su etiqueta como frase",
         de_ilustraciones([{"t": 12.0, "tipo": "lupa", "etiqueta": "Buscar bien"}, {"t": 20.0, "tipo": "xx"}])
         == [{"t": 12.0, "tipo": "lupa", "frase": "BUSCAR BIEN", "duracion": 6.0, "fuente": "ilustracion"}])

    marca = dict(config.load().get_path("videos.marca") or {})
    pal = paleta(marca)
    for tono, color in (("crema", pal.crema[:3]), ("naranja", pal.naranja[:3])):
        fr = fotograma("grafica_sube", 2.0, 6.0, 384, 216, pal, "ESTO VA A EVOLUCIONAR", tono)
        esquina = fr.getpixel((3, 3))
        caso(f"fondo {tono}: el color de la marca, a pantalla completa ({fr.size[0]}×{fr.size[1]})",
             fr.size == (384, 216) and all(abs(a - b) < 20 for a, b in zip(esquina, color)), (esquina, color))
    fr = fotograma("grafica_sube", 2.0, 6.0, 384, 216, pal, "ESTO VA A EVOLUCIONAR", "crema")
    zona = fr.crop((60, 170, 324, 200)).convert("L")
    caso("la frase se dibuja debajo (tinta sobre crema)", min(zona.getdata()) < 60, min(zona.getdata()))
    with tempfile.TemporaryDirectory() as tmp:
        e = [{"t": 10.0, "tipo": "lupa", "frase": "BUSCAR", "duracion": 1.0, "tono": "naranja", "fuente": "ia"}]
        capas = genera(e, marca, Path(tmp), 192, 108, 6)
        f0 = Path(tmp) / Path(capas[0]["patron"]).parent.name / "f_0000.png"
        mt = f0.stat().st_mtime if f0.exists() else None
        capas2 = genera(e, marca, Path(tmp), 192, 108, 6)
        caso("dibuja sus fotogramas a los fps del vídeo, a pantalla completa, con «whoosh» al entrar y al volver; la 2.ª vez, "
             "de la caché",
             capas and capas[0]["n"] == 6 and capas[0]["ancla"] == "completa" and capas[0]["sonido"] == "whoosh"
             and capas[0]["sonido_fin"] and mt is not None and f0.stat().st_mtime == mt and capas2 == capas, capas)

    # --- las escenas de ESTILO (2026-10-10): comprobadas contra lo dicho, pocas, separadas y sin pisar nada ---
    pal_e = paleta(marca)
    wd = [{"word": w, "start": 60.0 + i * 0.4, "end": 60.3 + i * 0.4} for i, w in enumerate(
        "segun el estudio los textos humanos se leen 2:31 y los de IA 1:54 de media".split())]
    plan_e = {"escenas_estilo": [
        {"estilo": "grafica", "t": 59.0, "duracion": 7, "cita": "según el estudio los textos humanos se leen 2:31", "titulo": "Tiempo medio",
         "barras": [{"nombre": "Humano", "valor": 151, "etiqueta": "2:31"}, {"nombre": "IA", "valor": 114, "etiqueta": "1:54"}]},
        {"estilo": "chat", "t": 200.0, "cita": "si le preguntas a chatgpt cuanto tarda el seo", "pregunta": "¿Cuánto tarda el SEO?",
         "respuesta": "Entre 3 y 6 meses según el caso concreto."},
        {"estilo": "grafica", "t": 64.0, "cita": "según el estudio los textos humanos se leen", "titulo": "Inventada",
         "barras": [{"nombre": "A", "valor": 9, "etiqueta": "9:99"}, {"nombre": "B", "valor": 5, "etiqueta": "5:55"}]}]}
    desc: list = []
    ce = candidatas_estilo(plan_e, wd, lambda t: t, desc)
    caso("estilo: la gráfica con su cita y sus cifras dichas entra (cuando EMPIEZAS a decir la cita); la cita que no está en lo dicho y la gráfica con cifras "
         "inventadas se descartan con su motivo",
         len(ce) == 1 and ce[0]["tipo"] == "grafica" and abs(ce[0]["t"] - 60.0) < 0.5 and len(desc) == 2
         and any("cita" in d for d in desc) and any("cifras" in d for d in desc), (ce, desc))
    cand_e = [{"t": 60.0, "tipo": "grafica", "frase": "G", "duracion": 7.0, "fuente": "estilo", "datos": {}},
              {"t": 75.0, "tipo": "veredicto", "frase": "V", "duracion": 5.0, "fuente": "estilo", "datos": {}},
              {"t": 140.0, "tipo": "cita", "frase": "C", "duracion": 5.5, "fuente": "estilo", "datos": {}},
              {"t": 300.0, "tipo": "chat", "frase": "H", "duracion": 7.0, "fuente": "estilo", "datos": {}}]
    sel_e = selecciona(cand_e + [C(30.0)], 490.0, [(138.0, 142.0)], [], p)
    caso("estilo: respeta la separación (35 s) y no pisa una tarjeta; con escenas de estilo la ilustración ya no es obligatoria; los fondos alternan solo entre "
         "las que lo usan (ilustración y frase)",
         [e["tipo"] for e in sel_e] == ["grafica", "chat"] or [e["tipo"] for e in sel_e] == ["grafica", "veredicto", "chat"] or [e["tipo"] for e in sel_e] == ["grafica", "chat"],
         [(e["tipo"], round(e["t"])) for e in sel_e])
    caso("estilo: como mucho una cada 100 s del vídeo (4 en 490 s) y siempre separadas",
         len([e for e in selecciona(cand_e * 3, 490.0, [], [], p) if e["fuente"] == "estilo"]) <= 4, "")
    sin_estilo = selecciona(cand_e, 490.0, [], [], {**p, "estilo_activo": False})
    caso("estilo apagado (`videos.escenas.estilo_activo: false`): ninguna de estilo", all(e["fuente"] != "estilo" for e in sin_estilo), sin_estilo)
    with tempfile.TemporaryDirectory() as tmp:
        e = [{"t": 10.0, "tipo": "grafica", "frase": "G", "duracion": 1.0, "tono": "oscuro", "fuente": "estilo",
              "datos": {"titulo": "Tiempo", "barras": [{"nombre": "A", "valor": 2, "etiqueta": "2:00"}, {"nombre": "B", "valor": 1, "etiqueta": "1:00"}]}}]
        capas = genera(e, marca, Path(tmp), 192, 108, 6)
        carp = Path(tmp) / Path(capas[0]["patron"]).parent.name
        caso("estilo: sus fotogramas salen a pantalla completa por la misma vía (procesos en paralelo) y con sus datos",
             capas and capas[0]["n"] == 6 and capas[0]["ancla"] == "completa" and len(list(carp.glob("f_*.png"))) == 6
             and Image.open(carp / "f_0003.png").size == (192, 108), capas)
    return out


def hoja(tipo: str, frase: str, tono: str, destino: Path, dur: float = 6.0, tiempos=(0.15, 0.4, 1.0, 2.0, 3.5, 5.5),
         W: int = 1920, H: int = 1080, lento: float = LENTO) -> Path:
    """Hoja de contacto: los fotogramas clave de la escena, para mirarla de un vistazo."""
    pal = paleta(dict(config.load().get_path("videos.marca") or {}))
    esc = 0.25
    w, h = int(W * esc), int(H * esc)
    cols = 3
    filas = math.ceil(len(tiempos) / cols)
    out = Image.new("RGB", (cols * w + (cols + 1) * 10, filas * (h + 28) + 10), (27, 42, 65))
    d = ImageDraw.Draw(out)
    for i, t in enumerate(tiempos):
        fr = fotograma(tipo, t, dur, W, H, pal, frase, tono, lento).resize((w, h), Image.LANCZOS)
        x, y = 10 + (i % cols) * (w + 10), 10 + (i // cols) * (h + 28)
        out.paste(fr, (x, y))
        d.text((x + 4, y + h + 6), f"{t:.2f} s", fill=(255, 255, 255))
    out.save(destino)
    return Path(destino)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Muestra de la escena a pantalla completa sobre un vídeo real.")
    ap.add_argument("--video", required=True)
    ap.add_argument("--t", type=float, required=True, help="segundo del vídeo editado donde entra la escena")
    ap.add_argument("--dur", type=float, default=6.0)
    ap.add_argument("--tipo", default="grafica_sube")
    ap.add_argument("--frase", default="")
    ap.add_argument("--tono", default="crema", choices=("crema", "naranja"))
    ap.add_argument("--salida", required=True)
    a = ap.parse_args()
    log(f"Muestra: {a.tipo} «{a.frase}» ({a.tono}) en el {a.t:.1f} s de {a.video}")
    muestra(Path(a.video), a.t, a.dur, a.tipo, a.frase, a.tono, Path(a.salida))
    hoja(a.tipo, a.frase, a.tono, Path(a.salida).with_suffix(".png"), dur=a.dur)
    print(json.dumps({"ok": True, "mp4": a.salida}))
