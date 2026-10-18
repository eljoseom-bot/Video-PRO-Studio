# COPIA PROPIA para «Shorts · TikTok» (el autor, 16-oct-2026: «lo de Video Pro no lo toques, duplícalo»): si cambia ilus_escenas2.py de Vídeos PRO, esto no cambia.
"""Segunda tanda de escenas de las ilustraciones (ver `ilus_escenas.py` para las reglas)."""
from __future__ import annotations

import math

from .st_ilus_escenas import _punto_en_ruta, _rayos, escena
from .st_ilus_motor import (Lienzo, clamp, e_back, e_bounce, e_elastic, e_in, e_io, e_out, lerp,
                         onda, prog, pulso, ciclo)


def _punta(l: Lienzo, p0, p1, tam: float, color, borde=None):
    """Punta de flecha en p1 mirando de p0 a p1."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    px, py = -uy, ux
    l.forma([(p1[0] + ux * tam * 0.9, p1[1] + uy * tam * 0.9),
             (p1[0] - ux * tam * 0.35 + px * tam * 0.85, p1[1] - uy * tam * 0.35 + py * tam * 0.85),
             (p1[0] - ux * tam * 0.35 - px * tam * 0.85, p1[1] - uy * tam * 0.35 - py * tam * 0.85)],
            color, borde)


# ------------------------------------------------------------------ gráfica que baja
@escena("grafica_baja", "Gráfica que baja", "caída, pérdida de tráfico, bajar posiciones, penalización, problema", 3.0)
def _grafica_baja(l: Lienzo, t: float, dur: float):
    p = l.pal
    base = 0.77
    l.trazo([(0.20, 0.22), (0.20, base), (0.82, base)], p.beige, 0.026, borde=0.008)
    alturas = (0.50, 0.38, 0.27, 0.16)
    xs = (0.33, 0.47, 0.61, 0.75)
    cols = (p.beige_osc, p.beige_osc, p.dorado_osc, p.naranja)
    for i, (x, h, c) in enumerate(zip(xs, alturas, cols)):
        k = e_back(prog(t, 0.12 + 0.13 * i, 0.55 + 0.13 * i), 1.5)
        latido = 1 + 0.035 * onda(t - 1.2, 1.6, i * 0.15) if t > 1.2 else 1
        hh = h * k * latido
        if hh > 0.01:
            l.rect(x - 0.052, base - hh, x + 0.052, base - 0.012, c, radio=0.02)
    g = prog(t, 0.75, 1.30)
    if g > 0:
        ruta = [(0.28, 0.24), (0.43, 0.34), (0.58, 0.46), (0.73, 0.62)]
        pts = _punto_en_ruta(ruta, e_out(g))
        vaiven = 0.010 * onda(t - 1.3, 1.5) if t > 1.3 else 0
        pts = [(x, y + vaiven * (i / max(1, len(pts) - 1))) for i, (x, y) in enumerate(pts)]
        l.trazo(pts, p.naranja_osc, 0.038, borde=0.012)
        if g > 0.98 and len(pts) >= 2:
            _punta(l, pts[-2], pts[-1], 0.075, p.naranja_osc)


# ------------------------------------------------------------------ podio
@escena("podio", "Podio", "primer puesto, ranking, posición 1, ganar, el mejor, competencia", 3.0)
def _podio(l: Lienzo, t: float, dur: float):
    p = l.pal
    base = 0.80
    for x0, x1, h, c, num, t0 in ((0.15, 0.37, 0.20, p.beige, "2", 0.25), (0.38, 0.62, 0.33, p.dorado, "1", 0.40),
                                  (0.63, 0.85, 0.14, p.naranja_cl, "3", 0.10)):
        k = e_back(prog(t, t0, t0 + 0.45), 1.4)
        hh = h * k
        if hh > 0.01:
            l.rect(x0, base - hh, x1, base, c, radio=0.02)
            if k > 0.7:
                l.texto((x0 + x1) / 2, base - hh / 2 + 0.006, num, 0.15 if num == "1" else 0.11, p.tinta)
    s = e_back(prog(t, 0.85, 1.25), 2.0)
    if s > 0.02:
        y = 0.80 - 0.33 - 0.115 + 0.014 * onda(t, 1.4)
        l.estrella(0.5, y, 0.105 * s, 0.047 * s, 5, 14 * onda(t, 2.2), p.dorado)
    if t > 1.2:
        for x, y, ph in ((0.26, 0.40, 0.0), (0.76, 0.34, 0.5)):
            l.chispa(x, y, 0.040 * pulso(t, 1.1, ph), rot=40 * t)


# ------------------------------------------------------------------ objetivo
@escena("objetivo", "Diana", "objetivo, meta, público objetivo, acertar, enfocar, palabra clave exacta", 3.0)
def _objetivo(l: Lienzo, t: float, dur: float):
    p = l.pal
    cx, cy = 0.45, 0.56
    k = e_elastic(prog(t, 0.0, 0.6))
    pul = 1 + 0.02 * onda(t, 1.2)
    for r, c in zip((0.27, 0.20, 0.13, 0.06), (p.crema, p.naranja, p.crema, p.naranja)):
        if r * k * pul > 0.005:
            l.circulo(cx, cy, r * k * pul, c)
    vuelo = e_out(prog(t, 0.35, 0.72))
    if t > 0.32:
        ang = math.radians(-45)
        if t > 0.72:
            ang += math.radians(7 * math.exp(-7 * (t - 0.72)) * onda(t - 0.72, 0.17))
        ux, uy = math.cos(ang), math.sin(ang)
        off = (1 - vuelo) * 0.50
        tip = (cx + ux * off, cy + uy * off)
        tail = (tip[0] + ux * 0.34, tip[1] + uy * 0.34)
        px, py = -uy, ux
        l.trazo([tip, tail], p.dorado_osc, 0.022, borde=0.010)
        for s in (-1, 1):                      # plumas
            l.forma([(tail[0] - ux * 0.02, tail[1] - uy * 0.02),
                     (tail[0] + ux * 0.075 + px * 0.06 * s, tail[1] + uy * 0.075 + py * 0.06 * s),
                     (tail[0] + ux * 0.11, tail[1] + uy * 0.11)], p.naranja, borde=0.008)
        _punta(l, tail, tip, 0.05, p.tinta, borde=0.004)
    if t > 0.72:
        k2 = ciclo(t - 0.72, 1.3)
        w = 0.026 * (1 - k2)
        if w > 0.004:
            l.arco(cx, cy, 0.07 + 0.22 * k2, 0, 359.9, p.dorado, w, borde=0)


# ------------------------------------------------------------------ embudo
@escena("embudo", "Embudo", "conversión, embudo de ventas, filtrar, captar clientes, leads, proceso", 3.0)
def _embudo(l: Lienzo, t: float, dur: float):
    p = l.pal
    l.forma([(0.17, 0.25), (0.83, 0.25), (0.575, 0.52), (0.575, 0.67), (0.425, 0.67), (0.425, 0.52)], p.dorado)
    l.rect(0.15, 0.20, 0.85, 0.27, p.naranja, radio=0.02)
    l.trazo([(0.28, 0.33), (0.42, 0.50)], p.dorado_cl, 0.028, borde=0)
    xs = (0.28, 0.42, 0.60, 0.72)
    for i, x0 in enumerate(xs):
        k = ciclo(t + i * 0.4, 1.6)
        if t < 0.15 + 0.1 * i:
            continue
        y = lerp(0.15, 0.50, e_in(k) * 0.5 + k * 0.5)
        x = lerp(x0, 0.5, clamp((y - 0.22) / 0.28))
        r = 0.045 * (1 - 0.85 * clamp((y - 0.34) / 0.20))
        if r > 0.008:
            l.circulo(x, y, r, p.naranja if i % 2 == 0 else p.crema, borde=0.009)
    llegada = pulso(t, 0.8)
    l.circulo(0.5, 0.77, 0.062 * (0.9 + 0.2 * llegada), p.dorado_osc)
    l.texto(0.5, 0.775, "$", 0.08, p.tinta)


# ------------------------------------------------------------------ enlace
def _anillo(cx, cy, rx, ry, rot, n=44, ex=0.55):
    """Trazado de una píldora (superelipse) rotada `rot` grados, para hacer eslabones."""
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        ca, sa = math.cos(a), math.sin(a)
        x = rx * math.copysign(abs(ca) ** ex, ca)
        y = ry * math.copysign(abs(sa) ** ex, sa)
        pts.append((cx + x * c - y * s, cy + x * s + y * c))
    return pts


@escena("enlace", "Enlace", "enlace, backlink, link, vincular, conectar, url, enlazado interno", 3.0)
def _enlace(l: Lienzo, t: float, dur: float):
    p = l.pal
    k = e_out(prog(t, 0.05, 0.60))
    sep = (1 - k) * 0.20
    bal = 4.5 * onda(t, 2.4) if t > 0.6 else 0.0
    d = (math.cos(math.radians(-45)), math.sin(math.radians(-45)))
    c2 = (0.5 + d[0] * (0.105 + sep), 0.5 + d[1] * (0.105 + sep))
    c1 = (0.5 - d[0] * (0.105 + sep), 0.5 - d[1] * (0.105 + sep))
    r2 = _anillo(c2[0], c2[1], 0.205, 0.105, -45 + bal)
    r1 = _anillo(c1[0], c1[1], 0.205, 0.105, -45 + bal)
    l.trazo(r2, p.dorado, 0.062, borde=0.014)
    l.trazo(r1, p.naranja, 0.062, borde=0.014)
    # el eslabón dorado vuelve a pasar POR ENCIMA en un cruce: parece enlazado
    sub = [(x, y) for x, y in r2 if 0.02 < (x + y - 1.0) * 0.7071 < 0.20 and abs((x - y) * 0.7071) < 0.10]
    if len(sub) > 3:
        sub.sort(key=lambda q: q[0])
        l.trazo(sub, p.dorado, 0.062, borde=0.014)
    if 0.55 < t < 1.0:
        l.chispa(0.5, 0.5, 0.05 + 0.09 * math.sin(math.pi * prog(t, 0.55, 1.0)), rot=90 * t, fill=p.crema, borde=0.008)
    elif t >= 1.0:
        l.chispa(0.5, 0.5, 0.045 * (0.6 + 0.4 * pulso(t, 0.9)), rot=45 * t, fill=p.crema, borde=0.006)


# ------------------------------------------------------------------ red
@escena("red", "Red de nodos", "estructura del sitio, enlaces internos, conexiones, clúster, arquitectura web", 3.2)
def _red(l: Lienzo, t: float, dur: float):
    p = l.pal
    cx, cy = 0.5, 0.52
    nodos = []
    for i, ang in enumerate((-90, -18, 54, 126, 198)):
        a = math.radians(ang)
        r = 0.29 + 0.012 * onda(t, 2.0, i * 0.2)
        nodos.append((cx + r * math.cos(a), cy + r * math.sin(a) * 0.95))
    # aristas (crecen desde el centro y unen vecinos)
    aristas = [((cx, cy), n) for n in nodos] + [(nodos[i], nodos[(i + 1) % 5]) for i in range(5)]
    for i, (a, b) in enumerate(aristas):
        g = e_out(prog(t, 0.10 + 0.06 * i, 0.55 + 0.06 * i))
        if g > 0.01:
            l.trazo([a, (lerp(a[0], b[0], g), lerp(a[1], b[1], g))], p.crema, 0.022, borde=0.008)
    # paquetes viajando por las aristas del centro
    if t > 0.9:
        for i, n in enumerate(nodos):
            k = ciclo(t + i * 0.27, 1.5)
            l.circulo(lerp(cx, n[0], k), lerp(cy, n[1], k), 0.020, p.naranja, borde=0.006)
    for i, n in enumerate(nodos):
        s = e_back(prog(t, 0.45 + 0.07 * i, 0.85 + 0.07 * i), 2.0)
        if s > 0.02:
            l.circulo(n[0], n[1], 0.062 * s, p.dorado if i % 2 == 0 else p.crema)
    s = e_back(prog(t, 0.0, 0.45), 2.0) * (1 + 0.05 * onda(t, 1.0))
    l.circulo(cx, cy, 0.10 * s, p.naranja)


# ------------------------------------------------------------------ documento
@escena("documento", "Documento", "contenido, artículo, blog, texto, escribir, redacción, guion, página", 3.4)
def _documento(l: Lienzo, t: float, dur: float):
    p = l.pal
    l.rect(0.24, 0.16, 0.76, 0.84, p.crema, radio=0.035)
    l.forma([(0.66, 0.16), (0.76, 0.26), (0.66, 0.26)], p.beige_osc, borde=0.010)
    l.rect(0.31, 0.27, 0.55, 0.325, p.naranja, radio=0.012, borde=0)
    ys = (0.42, 0.51, 0.60, 0.69, 0.78)
    ws = (0.38, 0.34, 0.38, 0.24, 0.30)
    punta = (0.31, ys[0])
    tc = t % 4.2
    for i, (y, w) in enumerate(zip(ys, ws)):
        g = e_io(prog(tc, 0.30 + 0.48 * i, 0.30 + 0.48 * i + 0.44))
        if g > 0.005:
            l.rect(0.31, y - 0.017, 0.31 + w * g, y + 0.017, p.beige_osc, radio=0.014, borde=0)
            if 0 < g < 1 or i == len(ys) - 1:
                punta = (0.31 + w * g, y)
    # lápiz en la punta de la línea que se escribe
    ang = -38 + 4 * onda(t, 0.3)

    def lapiz(s: Lienzo):
        s.rect(0.44, 0.12, 0.56, 0.72, p.dorado, radio=0.03)
        s.forma([(0.44, 0.72), (0.56, 0.72), (0.50, 0.88)], p.crema, borde=0.014)
        s.forma([(0.475, 0.83), (0.525, 0.83), (0.50, 0.88)], p.naranja, borde=0.006)
        s.rect(0.44, 0.06, 0.56, 0.16, p.naranja, radio=0.03)
    ent = e_out(prog(t, 0.0, 0.5))
    l.sprite(0.5, lapiz, punta[0] + 0.115, punta[1] - 0.12 - (1 - ent) * 0.15, rot=ang, esc=0.8)


# ------------------------------------------------------------------ navegador
@escena("navegador", "Navegador", "página web, sitio web, landing, abrir una web, internet, diseño web", 3.4)
def _navegador(l: Lienzo, t: float, dur: float):
    l.sprite(1.0, lambda s: _navegador_(s, t, dur), 0.5, 0.53, esc=0.82)      # más pequeño: no tapa el vídeo


def _navegador_(l: Lienzo, t: float, dur: float):
    p = l.pal
    l.rect(0.13, 0.20, 0.87, 0.82, p.crema, radio=0.04)
    l.rect(0.13, 0.20, 0.87, 0.315, p.tinta, radio=0.04, borde=0)
    l.rect(0.13, 0.27, 0.87, 0.315, p.tinta, radio=0.0, borde=0)
    for i, c in enumerate((p.naranja, p.dorado, p.beige_osc)):
        l.circulo(0.19 + 0.05 * i, 0.258, 0.016, c, borde=0)
    l.rect(0.36, 0.235, 0.80, 0.282, p.beige, radio=0.02, borde=0)
    a = e_out(prog(t, 0.15, 0.55))
    l.rect(0.19, 0.36, 0.19 + 0.62 * a, 0.56, p.dorado, radio=0.02)
    if a > 0.5:
        l.circulo(0.5, 0.46, 0.055 * (1 + 0.08 * onda(t, 1.2)), p.naranja)
    for j, (y, w) in enumerate(((0.63, 0.44), (0.69, 0.32))):
        b = e_out(prog(t, 0.35 + 0.15 * j, 0.75 + 0.15 * j))
        if b > 0.01:
            l.rect(0.19, y - 0.016, 0.19 + w * b, y + 0.016, p.beige_osc, radio=0.014, borde=0)
    c = e_back(prog(t, 0.70, 1.0), 2.0)
    squish = 1.0
    click = prog(t, 1.62, 1.85)
    if 0 < click < 1:
        squish = 1 - 0.14 * math.sin(math.pi * click)
    if c > 0.02:
        l.rect(0.19, 0.735, 0.19 + 0.24 * c * squish, 0.735 + 0.058 * squish, p.naranja, radio=0.02)
    # cursor: baja hasta el botón, hace clic, y se queda moviéndose un poco
    k = e_io(prog(t, 1.0, 1.6))
    mx = lerp(0.72, 0.32, k) + (0.012 * onda(t, 1.4) if t > 1.7 else 0)
    my = lerp(0.36, 0.78, k) + (0.010 * onda(t, 1.1) if t > 1.7 else 0)
    if t > 0.9:
        l.forma([(mx, my), (mx, my + 0.115), (mx + 0.032, my + 0.086), (mx + 0.062, my + 0.132),
                 (mx + 0.088, my + 0.118), (mx + 0.058, my + 0.075), (mx + 0.10, my + 0.070)], p.tinta, borde=0.010)
        l.forma([(mx + 0.012, my + 0.030), (mx + 0.012, my + 0.085), (mx + 0.036, my + 0.064), (mx + 0.066, my + 0.108),
                 (mx + 0.074, my + 0.100), (mx + 0.044, my + 0.056), (mx + 0.070, my + 0.052)], p.crema, borde=0)
    if 1.6 < t < 2.1:
        kk = prog(t, 1.6, 2.1)
        l.arco(0.34, 0.78, 0.04 + 0.10 * kk, 0, 359.9, p.dorado, 0.02 * (1 - kk) + 0.002, borde=0)


# ------------------------------------------------------------------ código
@escena("codigo", "Código", "programar, código, HTML, desarrollo web, desarrollador, script, vibe coding", 3.4)
def _codigo(l: Lienzo, t: float, dur: float):
    l.sprite(1.0, lambda s: _codigo_(s, t, dur), 0.5, 0.55, esc=0.82)


def _codigo_(l: Lienzo, t: float, dur: float):
    p = l.pal
    l.rect(0.13, 0.24, 0.87, 0.82, p.tinta, radio=0.05, borde=0.020)
    for i, c in enumerate((p.naranja, p.dorado, p.beige_osc)):
        l.circulo(0.19 + 0.05 * i, 0.295, 0.017, c, borde=0)
    lineas = (((0.20, 0.34, p.naranja), (0.37, 0.62, p.crema)),
              (((0.27, 0.42, p.dorado), (0.45, 0.74, p.beige))),
              (((0.27, 0.58, p.crema),)),
              (((0.27, 0.36, p.naranja), (0.39, 0.66, p.dorado))),
              (((0.20, 0.30, p.naranja),)))
    tc = t % 5.0
    u = clamp((tc - 0.30) / 2.2) * len(lineas)
    y0 = 0.385
    fin = (0.20, y0)
    for i, segs in enumerate(lineas):
        f = clamp(u - i)
        if f <= 0:
            continue
        total = sum(b - a for a, b, _ in segs)
        hecho = total * f
        for a, b, c in segs:
            w = min(b - a, hecho)
            if w <= 0:
                break
            l.rect(a, y0 + i * 0.078 - 0.014, a + w, y0 + i * 0.078 + 0.014, c, radio=0.012, borde=0)
            hecho -= w
            fin = (a + w, y0 + i * 0.078)
    if int(t * 2.4) % 2 == 0 or u < len(lineas):
        l.rect(fin[0] + 0.012, fin[1] - 0.026, fin[0] + 0.034, fin[1] + 0.026, p.dorado, radio=0.004, borde=0)
    s = e_back(prog(t, 0.55, 0.95), 2.0)
    if s > 0.02:
        l.circulo(0.80, 0.255, 0.095 * s * (1 + 0.04 * onda(t, 1.0)), p.naranja)
        l.texto(0.80, 0.257, "</>", 0.066 * s, p.crema)


# ------------------------------------------------------------------ reloj
@escena("reloj", "Cronómetro", "tiempo, velocidad de carga, rápido, minutos, cronómetro, esperar, duración", 3.0)
def _reloj(l: Lienzo, t: float, dur: float):
    p = l.pal
    cx, cy = 0.5, 0.56
    l.rect(0.455, 0.17, 0.545, 0.245, p.dorado, radio=0.02)
    l.rect(0.485, 0.235, 0.515, 0.29, p.dorado_osc, radio=0.008)
    l.circulo(cx, cy, 0.265, p.crema)
    ang = -90 + 360 * (t / 2.2) * (1 if t > 0.3 else 0)
    if ang > -89.5:
        l.d.pieslice(((cx - 0.235) * l.n, (cy - 0.235) * l.n, (cx + 0.235) * l.n, (cy + 0.235) * l.n),
                     -90, ang, fill=p.naranja_cl)
    l.arco(cx, cy, 0.265, 0, 359.9, p.dorado, 0.035, borde=0.012)
    for i in range(12):
        a = math.radians(i * 30 - 90)
        r0, r1 = (0.19, 0.225) if i % 3 == 0 else (0.205, 0.225)
        l.trazo([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)), (cx + r1 * math.cos(a), cy + r1 * math.sin(a))],
                p.tinta, 0.012 if i % 3 else 0.02, borde=0)
    a = math.radians(ang)
    l.trazo([(cx, cy), (cx + 0.20 * math.cos(a), cy + 0.20 * math.sin(a))], p.naranja, 0.03, borde=0.010)
    l.circulo(cx, cy, 0.03, p.tinta, borde=0)


# ------------------------------------------------------------------ rayo
@escena("rayo", "Rayo", "rapidez, velocidad, rendimiento, energía, instantáneo, potencia, core web vitals", 2.8)
def _rayo(l: Lienzo, t: float, dur: float):
    p = l.pal
    f = 1.0 + 0.05 * max(0.0, onda(t, 0.7)) + 0.10 * e_out(1 - prog(t, 0.0, 0.5)) * 0
    cx, cy = 0.52, 0.50
    base = [(0.58, 0.14), (0.28, 0.54), (0.47, 0.54), (0.38, 0.86), (0.74, 0.43), (0.55, 0.43), (0.66, 0.14)]
    pts = [(cx + (x - cx) * f, cy + (y - cy) * f) for x, y in base]
    flash = (t % 1.1) < 0.09 and t > 0.4
    for i in range(3):
        k = ciclo(t + i * 0.27, 0.8)
        y = 0.35 + 0.15 * i
        x0 = 0.10 + 0.20 * k
        l.trazo([(x0, y), (x0 + 0.14 * (1 - k * 0.6), y)], p.crema, 0.020, borde=0.008)
    l.forma(pts, p.crema if flash else p.dorado)
    if not flash:
        l.forma([(0.575, 0.22), (0.38, 0.50), (0.50, 0.50)], p.dorado_cl, borde=0)
    for x, y, ph in ((0.80, 0.24, 0.0), (0.22, 0.72, 0.4), (0.78, 0.74, 0.7)):
        l.chispa(x, y, 0.045 * pulso(t, 0.9, ph), rot=50 * t, fill=p.naranja)


# ------------------------------------------------------------------ cruz
@escena("cruz", "Cruz", "error, mal, evitar, incorrecto, no hacer, mala práctica, prohibido", 2.6)
def _cruz(l: Lienzo, t: float, dur: float):
    p = l.pal

    def dibujo(s: Lienzo):
        k = e_back(prog(t, 0.05, 0.45), 1.9)
        r = 0.29 * k * (1 + 0.018 * onda(t, 1.3))
        if r > 0.01:
            s.circulo(0.5, 0.5, r, p.naranja)
            s.arco(0.5, 0.5, r * 0.78, 200, 262, p.naranja_cl, 0.028, borde=0)
        g = prog(t, 0.35, 0.75)
        if g > 0:
            s.trazo([(0.38, 0.38), (lerp(0.38, 0.62, e_out(min(1, g * 2))), lerp(0.38, 0.62, e_out(min(1, g * 2))))],
                    p.crema, 0.075, borde=0.013)
            if g > 0.5:
                h = e_out((g - 0.5) * 2)
                s.trazo([(0.62, 0.38), (lerp(0.62, 0.38, h), lerp(0.38, 0.62, h))], p.crema, 0.075, borde=0.013)
    sacude = 0.0
    if 0.85 < t < 1.5:
        sacude = 7.0 * math.exp(-4.0 * (t - 0.85)) * onda(t - 0.85, 0.16)
    l.sprite(1.0, dibujo, 0.5, 0.5, rot=sacude)


# ------------------------------------------------------------------ móvil
@escena("movil", "Móvil", "móvil, mobile, responsive, aplicación, app, smartphone, pantalla táctil", 3.2)
def _movil(l: Lienzo, t: float, dur: float):
    p = l.pal
    l.rect(0.31, 0.15, 0.69, 0.85, p.tinta, radio=0.07, borde=0.016)
    l.rect(0.34, 0.21, 0.66, 0.77, p.crema, radio=0.035, borde=0)
    l.rect(0.44, 0.175, 0.56, 0.192, p.beige_osc, radio=0.008, borde=0)
    l.circulo(0.5, 0.81, 0.017, p.beige_osc, borde=0)
    l.rect(0.34, 0.21, 0.66, 0.29, p.naranja, radio=0.0, borde=0)
    for i in range(3):
        k = e_out(prog(ciclo(t, 2.4) * 2.4, 0.15 + 0.22 * i, 0.55 + 0.22 * i)) if t > 0.3 else e_out(prog(t, 0.2 + 0.1 * i, 0.5 + 0.1 * i))
        y = 0.33 + 0.145 * i
        if k > 0.02:
            l.rect(0.375, y, 0.375 + 0.25 * k, y + 0.11, (p.dorado, p.beige, p.naranja_cl)[i], radio=0.02, borde=0.008)
    for i in range(3):
        k = ciclo(t + i * 0.26, 1.2)
        r = 0.06 + 0.04 * i
        a = 0.85 * (1 - k)
        if a > 0.15:
            l.arco(0.69, 0.50, r, -38, 38, p.dorado, 0.022 * (0.5 + 0.5 * a), borde=0.006)


# ------------------------------------------------------------------ dinero
@escena("dinero", "Moneda", "dinero, ingresos, monetizar, precio, coste, ganar, ventas, rentabilidad", 3.0)
def _dinero(l: Lienzo, t: float, dur: float):
    p = l.pal
    cx, cy = 0.5, 0.52
    flip = math.cos(2 * math.pi * (t - 0.2) / 1.6) if t > 0.2 else 1.0
    rx = max(0.012, 0.25 * abs(flip))
    l.elipse(cx, cy, rx, 0.25, p.dorado)
    if rx > 0.07:
        l.elipse(cx, cy, rx * 0.78, 0.195, None, borde=0)
        l.arco(cx, cy, 0.19, 0, 359.9, p.dorado_osc, 0.02, borde=0) if rx > 0.24 else None
        l.texto(cx, cy + 0.008, "$", 0.30 * min(1.0, rx / 0.22), p.tinta)
    for i, (x, ph) in enumerate(((0.22, 0.0), (0.80, 0.4))):
        k = e_bounce(ciclo(t + ph, 1.0)) if t > 0.4 else 0
        y = 0.80 - 0.10 * (1 - abs(2 * k - 1)) if t > 0.4 else 0.8
        l.elipse(x, y, 0.06, 0.06, p.dorado_osc)
    for x, y, ph in ((0.80, 0.24, 0.0), (0.20, 0.30, 0.5)):
        l.chispa(x, y, 0.045 * pulso(t, 1.0, ph), rot=45 * t)


# ------------------------------------------------------------------ usuarios
@escena("usuarios", "Usuarios", "audiencia, comunidad, usuarios, clientes, seguidores, gente, visitantes", 3.0)
def _usuarios(l: Lienzo, t: float, dur: float):
    p = l.pal

    def persona(cx, base_y, r, c, k):
        if k <= 0.02:
            return
        y = base_y - 0.02 * math.sin(math.pi * clamp(k))
        l.elipse(cx, y + r * 0.33, r * 1.50 * k, r * 1.25 * k, c)          # hombros
        l.circulo(cx, y - r * 1.50 * k, r * 0.82 * k, c)                    # cabeza
    persona(0.28, 0.62, 0.10, p.dorado, e_back(prog(t, 0.15, 0.55), 1.8))
    persona(0.72, 0.62, 0.10, p.naranja_cl, e_back(prog(t, 0.28, 0.68), 1.8))
    persona(0.5, 0.60, 0.13, p.naranja, e_back(prog(t, 0.05, 0.50), 1.8) * (1 + 0.02 * onda(t, 1.2)))
    s = e_back(prog(t, 0.8, 1.2), 2.2)
    if s > 0.02:
        l.circulo(0.76, 0.24, 0.085 * s * (1 + 0.06 * onda(t, 0.9)), p.crema)
        l.texto(0.76, 0.242, "+", 0.11 * s, p.naranja)


# ------------------------------------------------------------------ estrellas
@escena("estrellas", "Estrellas", "calidad, reseñas, valoración, reputación, mejor, cinco estrellas, autoridad", 3.0)
def _estrellas(l: Lienzo, t: float, dur: float):
    p = l.pal
    for i in range(5):
        x = 0.20 + 0.15 * i
        k = e_back(prog(t, 0.15 + 0.17 * i, 0.55 + 0.17 * i), 2.2)
        llena = k > 0.05
        s = 0.082 * (0.7 + 0.3 * min(1.0, k)) * (1.0 if not llena else max(0.2, k))
        y = 0.52 - 0.02 * i * 0.0 + (0.014 * onda(t - 1.2, 1.4, i * 0.12) if t > 1.2 else 0)
        l.estrella(x, y, 0.082, 0.036, 5, 0, p.beige_osc if not llena else p.dorado) if not llena else \
            l.estrella(x, y, s, s * 0.44, 5, 12 * (1 - min(1, k)), p.dorado)
    if t > 1.3:
        k = ciclo(t - 1.3, 1.6)
        x = lerp(0.14, 0.86, k)
        l.chispa(x, 0.40, 0.06 * math.sin(math.pi * k), rot=60 * t, fill=p.crema, borde=0.006)


# ------------------------------------------------------------------ globo
_CONTINENTES = (
    ((-125, 50), (-100, 62), (-80, 52), (-72, 28), (-88, 16), (-78, 8), (-72, -8), (-70, -30), (-72, -52),
     (-60, -42), (-45, -22), (-36, -8), (-52, 2), (-62, 12), (-82, 26), (-96, 30), (-118, 36)),
    ((-10, 36), (2, 50), (22, 60), (42, 55), (34, 36), (50, 14), (42, -14), (32, -34), (16, -34), (10, -6),
     (-16, 10), (-16, 26)),
    ((60, 52), (92, 66), (140, 62), (152, 46), (122, 26), (102, 8), (82, 10), (70, 26)),
    ((114, -20), (134, -12), (152, -26), (142, -40), (120, -34)),
)


@escena("globo", "Globo terráqueo", "internet, web, internacional, global, mundo, idiomas, alcance, hreflang", 3.2)
def _globo(l: Lienzo, t: float, dur: float):
    p = l.pal
    cx, cy, r = 0.5, 0.5, 0.29
    ent = e_back(prog(t, 0.0, 0.5), 1.6)
    rr = r * ent
    if rr < 0.02:
        return
    l.circulo(cx, cy, rr, p.beige)
    fase = math.radians(48 * t)

    def proy(lon, lat):
        a, b = math.radians(lon) - fase, math.radians(lat)
        vis = math.cos(b) * math.cos(a)
        return (cx + rr * math.cos(b) * math.sin(a), cy - rr * math.sin(b)), vis
    # paralelos y meridianos (finos)
    for lat in (-40, 0, 40):
        pts = [proy(lon, lat) for lon in range(-90 + int(math.degrees(fase)), 91 + int(math.degrees(fase)), 10)]
        ps = [q for q, v in (proy(lon, lat) for lon in range(-180, 181, 10)) if True]
        vis = [q for q, v in (proy(lon, lat) for lon in range(-180, 181, 6)) if v > 0.02]
        if len(vis) > 2:
            l.trazo(vis, p.beige_osc, 0.010, borde=0)
    for lon in range(0, 360, 45):
        vis = [q for q, v in (proy(lon, lat) for lat in range(-90, 91, 6)) if v > 0.02]
        if len(vis) > 2:
            l.trazo(vis, p.beige_osc, 0.010, borde=0)
    # continentes: solo los puntos visibles del contorno
    for cont in _CONTINENTES:
        pts = [q for q, v in (proy(lon, lat) for lon, lat in cont) if v > 0.06]
        if len(pts) >= 3:
            l.forma(pts, p.dorado, borde=0.010)
    l.arco(cx, cy, rr, 0, 359.9, p.tinta, 0.014, borde=0)
    a = 2 * math.pi * ciclo(t, 2.4)
    l.circulo(cx + (r + 0.07) * math.cos(a), cy + (r + 0.07) * 0.42 * math.sin(a), 0.03 * ent, p.naranja)


# ------------------------------------------------------------------ nube
@escena("nube", "Nube", "hosting, nube, servidor, subir archivos, cloud, almacenamiento, backup, descarga", 3.0)
def _nube(l: Lienzo, t: float, dur: float):
    p = l.pal
    l.circulo(0.36, 0.56, 0.135, p.beige)
    l.circulo(0.54, 0.47, 0.18, p.beige)
    l.circulo(0.70, 0.58, 0.115, p.beige)
    l.rect(0.24, 0.56, 0.78, 0.70, p.beige, radio=0.07)
    # relleno interior para quitar los contornos internos
    l.circulo(0.36, 0.56, 0.134, p.beige, borde=0)
    l.circulo(0.54, 0.47, 0.179, p.beige, borde=0)
    l.circulo(0.70, 0.58, 0.114, p.beige, borde=0)
    l.rect(0.245, 0.56, 0.775, 0.695, p.beige, radio=0.065, borde=0)
    k = ciclo(t + 0.2, 1.7)
    y = lerp(0.72, 0.36, e_out(k))
    a = math.sin(math.pi * k)

    def flecha(s: Lienzo):
        s.forma([(0.5, 0.20), (0.72, 0.46), (0.58, 0.46), (0.58, 0.80), (0.42, 0.80), (0.42, 0.46), (0.28, 0.46)], p.naranja)
    l.sprite(0.34, flecha, 0.5, y, esc=1.0, alpha=clamp(a * 1.6))


# ------------------------------------------------------------------ chat
@escena("chat", "Conversación", "chat, preguntar, prompt, conversación, comentarios, respuesta, mensajes, IA conversacional", 3.2)
def _chat(l: Lienzo, t: float, dur: float):
    p = l.pal
    a = e_back(prog(t, 0.10, 0.50), 1.8)
    if a > 0.02:
        def burbuja1(s: Lienzo):
            s.forma([(0.30, 0.66), (0.24, 0.80), (0.42, 0.70)], p.crema)
            s.rect(0.10, 0.20, 0.90, 0.70, p.crema, radio=0.12)
            for j, w in enumerate((0.50, 0.36)):
                s.rect(0.22, 0.34 + 0.15 * j, 0.22 + w, 0.40 + 0.15 * j, p.beige_osc, radio=0.03, borde=0)
        l.sprite(0.56, burbuja1, 0.36, 0.37, esc=a)
    b = e_back(prog(t, 0.45, 0.85), 1.8)
    if b > 0.02:
        def burbuja2(s: Lienzo):
            s.forma([(0.70, 0.66), (0.76, 0.80), (0.58, 0.70)], p.naranja)
            s.rect(0.10, 0.20, 0.90, 0.70, p.naranja, radio=0.12)
            for j in range(3):
                rebote = abs(math.sin(2 * math.pi * (t * 1.3 - j * 0.16)))
                s.circulo(0.32 + 0.18 * j, 0.45 - 0.07 * rebote, 0.055, p.crema, borde=0)
        l.sprite(0.56, burbuja2, 0.62, 0.62, esc=b)


# ------------------------------------------------------------------ pregunta
@escena("pregunta", "Pregunta", "duda, pregunta, por qué, cómo, misterio, ¿sabías que?, problema a resolver", 2.8)
def _pregunta(l: Lienzo, t: float, dur: float):
    p = l.pal
    k = ciclo(t, 1.3)
    salto = abs(math.sin(math.pi * k)) * 0.06 if t > 0.5 else 0.0
    sq = 1 - 0.10 * max(0.0, 1 - abs(math.sin(math.pi * k)) * 6) if t > 0.5 else 1
    ent = e_back(prog(t, 0.0, 0.45), 1.9)

    def burbuja(s: Lienzo):
        s.forma([(0.34, 0.74), (0.26, 0.90), (0.50, 0.78)], p.dorado)
        s.rect(0.14, 0.14, 0.86, 0.80, p.dorado, radio=0.20)
        s.texto(0.5, 0.49, "?", 0.62, p.tinta)
    l.sprite(0.72, burbuja, 0.5, 0.52 - salto, esc=ent, sx=1 / max(0.6, sq) ** 0.5, sy=sq)
    for i, (ang, rad) in enumerate(((-50, 0.30), (200, 0.30), (-140, 0.32))):
        kk = ciclo(t + i * 0.4, 1.6)
        a = math.radians(ang + 25 * onda(t, 2.0, i * 0.3))
        s = 0.07 * math.sin(math.pi * kk) * ent
        if s > 0.012:
            l.texto(0.5 + rad * math.cos(a), 0.5 + rad * math.sin(a), "?", s * 1.4, p.naranja)


# ------------------------------------------------------------------ alerta
@escena("alerta", "Alerta", "cuidado, advertencia, ojo, peligro, riesgo, importante, penalización, aviso", 2.8)
def _alerta(l: Lienzo, t: float, dur: float):
    p = l.pal
    ent = e_back(prog(t, 0.0, 0.45), 1.9)
    pul = 1 + 0.04 * onda(t, 0.9) if t > 0.5 else 1
    sacude = 6 * math.exp(-5 * (t - 0.5)) * onda(t - 0.5, 0.14) if t > 0.5 else 0

    def tri(s: Lienzo):
        s.forma([(0.5, 0.14), (0.88, 0.80), (0.12, 0.80)], p.dorado, borde=0.028)
        s.trazo([(0.5, 0.38), (0.5, 0.57)], p.tinta, 0.085, borde=0)
        s.circulo(0.5, 0.685, 0.048, p.tinta, borde=0)
    l.sprite(0.80, tri, 0.5, 0.52, rot=sacude, esc=ent * pul)
    if t > 0.5:
        k = ciclo(t - 0.5, 1.2)
        w = 0.022 * (1 - k)
        if w > 0.004:
            l.arco(0.5, 0.58, 0.22 + 0.13 * k, 205, 335, p.naranja, w, borde=0)


# ------------------------------------------------------------------ ciclo
@escena("ciclo", "Flechas en ciclo", "actualizar, repetir, ciclo, proceso continuo, iterar, mejora continua, reciclar", 3.0)
def _ciclo(l: Lienzo, t: float, dur: float):
    p = l.pal
    ent = e_back(prog(t, 0.0, 0.5), 1.6)
    rot = 90 * t + 60 * (1 - e_out(prog(t, 0.0, 0.7))) * -1
    cx, cy, r = 0.5, 0.5, 0.245 * ent
    if r < 0.03:
        return
    for base in (0, 180):
        a0, a1 = base + rot + 20, base + rot + 145
        l.arco(cx, cy, r, a0, a1, p.naranja if base == 0 else p.dorado, 0.075, borde=0.014)
        ang = math.radians(a1)
        ex, ey = cx + r * math.cos(ang), cy + r * math.sin(ang)
        tx, ty = -math.sin(ang), math.cos(ang)
        _punta(l, (ex - tx * 0.05, ey - ty * 0.05), (ex + tx * 0.06, ey + ty * 0.06), 0.085,
               p.naranja if base == 0 else p.dorado)
    l.circulo(cx, cy, 0.05 * (1 + 0.15 * onda(t, 0.9)) * ent, p.crema)


# ------------------------------------------------------------------ balanza
@escena("balanza", "Balanza", "comparar, versus, pros y contras, equilibrio, decidir, diferencia entre, elegir", 3.2)
def _balanza(l: Lienzo, t: float, dur: float):
    p = l.pal
    inc = 13 * math.exp(-2.6 * max(0, t - 0.25)) * onda(t - 0.25, 0.9) if t > 0.25 else -16 * (1 - e_out(prog(t, 0, 0.25)))
    inc += 2.5 * onda(t, 2.6) * prog(t, 1.0, 1.6)
    a = math.radians(inc)
    cx, cy, L = 0.5, 0.28, 0.28
    l.rect(0.43, 0.78, 0.57, 0.84, p.tinta, radio=0.02)
    l.rect(0.482, 0.26, 0.518, 0.80, p.tinta, radio=0.01, borde=0.012)
    x1, y1 = cx - L * math.cos(a), cy - L * math.sin(a)
    x2, y2 = cx + L * math.cos(a), cy + L * math.sin(a)
    l.trazo([(x1, y1), (x2, y2)], p.dorado_osc, 0.035, borde=0.012)
    for (x, y, c, peso) in ((x1, y1, p.naranja, 0.05), (x2, y2, p.dorado, 0.04)):
        py = y + 0.21
        for dx in (-0.085, 0.085):
            l.trazo([(x, y), (x + dx, py)], p.beige, 0.012, borde=0.006)
        l.forma([(x - 0.11, py), (x + 0.11, py), (x + 0.072, py + 0.05), (x - 0.072, py + 0.05)], c)
        l.circulo(x, py - peso, peso, c, borde=0.010)
    l.circulo(cx, cy, 0.035, p.naranja)


# ------------------------------------------------------------------ lista
@escena("lista", "Lista de tareas", "pasos, checklist, lista, tareas, requisitos, guía paso a paso, resumen", 3.4)
def _lista(l: Lienzo, t: float, dur: float):
    p = l.pal
    l.rect(0.20, 0.19, 0.80, 0.85, p.beige_osc, radio=0.05)
    l.rect(0.24, 0.24, 0.76, 0.81, p.crema, radio=0.03, borde=0)
    l.rect(0.39, 0.15, 0.61, 0.23, p.naranja, radio=0.03)
    tc = t % 4.6
    for i in range(4):
        y = 0.35 + 0.13 * i
        k = prog(tc, 0.45 + 0.50 * i, 0.80 + 0.50 * i)
        marcada = k > 0
        l.rect(0.29, y - 0.038, 0.365, y + 0.038, p.naranja if marcada else p.crema, radio=0.014, borde=0.010)
        if marcada:
            ruta = [(0.305, y + 0.002), (0.325, y + 0.024), (0.352, y - 0.022)]
            pts = _punto_en_ruta(ruta, e_out(k))
            if len(pts) >= 2:
                l.trazo(pts, p.crema, 0.018, borde=0)
        l.rect(0.40, y - 0.016, 0.40 + (0.30, 0.24, 0.30, 0.18)[i], y + 0.016, p.beige_osc if not marcada else p.dorado_cl,
               radio=0.014, borde=0)
