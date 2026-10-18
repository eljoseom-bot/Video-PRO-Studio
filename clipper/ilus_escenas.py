"""Escenas de las ilustraciones animadas (lupa, gráfica que sube, cohete…). Cada una es una función
pura `f(l, t, dur)`: dibuja en el `Lienzo` `l` el fotograma del segundo `t` (0 = entra en pantalla).

Reglas para escribir una escena (las comprueba `ilustraciones.prueba()`):
  · Dibujo dentro de la caja 0.16..0.84 (queda sitio para el rebote de entrada, el halo y la sombra).
  · Tiene que MOVERSE de verdad durante toda su vida, no solo al entrar: lo que pasa a partir de
    ~0.25 s (la entrada global dura 0.5 s) tiene que seguir cambiando (bucle o vaivén).
  · Colores solo de `l.pal` (naranja, dorado, crema, beige, tinta y sus tonos): nunca literales.
  · Nada aleatorio: el mismo `t` da siempre el mismo fotograma (la caché va por contenido).
"""
from __future__ import annotations

import math

from .ilus_motor import (Lienzo, clamp, e_back, e_bounce, e_elastic, e_in, e_io, e_out, lerp,
                         onda, prog, pulso, ciclo)


class Escena:
    def __init__(self, clave: str, nombre: str, significa: str, fn, dur_min: float = 2.6):
        self.clave, self.nombre, self.significa, self.fn, self.dur_min = clave, nombre, significa, fn, dur_min


CATALOGO: dict[str, Escena] = {}


def escena(clave: str, nombre: str, significa: str, dur_min: float = 2.6):
    def deco(fn):
        CATALOGO[clave] = Escena(clave, nombre, significa, fn, dur_min)
        return fn
    return deco


# ------------------------------------------------------------------ helpers de composición
def _punto_en_ruta(ps, k: float):
    """Punto a la fracción k (0..1) de la longitud de una polilínea, y los puntos recorridos."""
    if k <= 0:
        return [ps[0]]
    tramos = [math.dist(ps[i], ps[i + 1]) for i in range(len(ps) - 1)]
    total = sum(tramos)
    objetivo = total * clamp(k)
    out, acum = [ps[0]], 0.0
    for i, d in enumerate(tramos):
        if acum + d >= objetivo:
            r = (objetivo - acum) / d if d else 0
            out.append((lerp(ps[i][0], ps[i + 1][0], r), lerp(ps[i][1], ps[i + 1][1], r)))
            return out
        acum += d
        out.append(ps[i + 1])
    return out


def _rayos(l: Lienzo, cx, cy, r0, r1, n, t, color, ancho=0.028, rot=0.0, fase=0.0, periodo=1.4):
    """Rayitos que salen de un centro y pulsan (destello / éxito / idea)."""
    for i in range(n):
        ang = math.radians(rot + i * 360.0 / n)
        k = 0.55 + 0.45 * onda(t, periodo, fase + i * 0.11)
        a, b = r0 + (r1 - r0) * 0.15 * k, r0 + (r1 - r0) * (0.45 + 0.55 * k)
        l.trazo([(cx + a * math.cos(ang), cy + a * math.sin(ang)),
                 (cx + b * math.cos(ang), cy + b * math.sin(ang))], color, ancho, borde=0.008)


# ------------------------------------------------------------------ 1. check
@escena("check", "Check", "correcto, hecho, buena práctica, conseguido, aprobado", 2.4)
def _check(l: Lienzo, t: float, dur: float):
    p = l.pal
    k = e_back(prog(t, 0.05, 0.45), 1.9)
    r = 0.26 * k * (1 + 0.018 * onda(t, 1.4))
    if r > 0.01:
        l.circulo(0.5, 0.5, r, p.naranja)
        # brillo
        l.arco(0.5, 0.5, r * 0.78, 200, 262, p.naranja_cl, 0.028, borde=0)
    g = prog(t, 0.35, 0.80)
    if g > 0:
        ruta = [(0.352, 0.520), (0.460, 0.630), (0.670, 0.385)]
        pts = _punto_en_ruta(ruta, e_out(g))
        if len(pts) >= 2:
            l.trazo(pts, p.crema, 0.078, borde=0.013)
    # destellos que se repiten
    if t > 0.7:
        _rayos(l, 0.5, 0.5, 0.325, 0.395, 10, t - 0.7, p.dorado, 0.024, rot=12, periodo=1.5)
        for x, y, ph in ((0.22, 0.26, 0.0), (0.79, 0.72, 0.5)):
            s = 0.045 * pulso(t, 1.2, ph)
            l.chispa(x, y, s, rot=45 * t)


# ------------------------------------------------------------------ 2. gráfica que sube
@escena("grafica_sube", "Gráfica que sube", "crecer, más tráfico, aumento, mejora, subir posiciones, éxito", 3.0)
def _grafica_sube(l: Lienzo, t: float, dur: float):
    p = l.pal
    base = 0.77
    l.trazo([(0.20, 0.22), (0.20, base), (0.82, base)], p.beige, 0.026, borde=0.008)
    alturas = (0.16, 0.26, 0.37, 0.50)
    xs = (0.33, 0.47, 0.61, 0.75)
    cols = (p.dorado, p.dorado, p.dorado, p.naranja)
    for i, (x, h, c) in enumerate(zip(xs, alturas, cols)):
        k = e_back(prog(t, 0.12 + 0.13 * i, 0.55 + 0.13 * i), 1.5)
        latido = 1 + 0.035 * onda(t - 1.2, 1.6, i * 0.15) if t > 1.2 else 1
        hh = h * k * latido
        if hh > 0.01:
            l.rect(x - 0.052, base - hh, x + 0.052, base - 0.012, c, radio=0.02)
    g = prog(t, 0.75, 1.30)
    if g > 0:
        ruta = [(0.29, 0.60), (0.43, 0.50), (0.57, 0.42), (0.72, 0.25)]
        pts = _punto_en_ruta(ruta, e_out(g))
        vaiven = 0.010 * onda(t - 1.3, 1.5) if t > 1.3 else 0
        pts = [(x, y - vaiven * (i / max(1, len(pts) - 1))) for i, (x, y) in enumerate(pts)]
        l.trazo(pts, p.naranja_osc, 0.038, borde=0.012)
        if g > 0.98:
            x, y = pts[-1]
            l.forma([(x + 0.075, y - 0.075), (x - 0.03, y - 0.06), (x + 0.06, y + 0.035)], p.naranja_osc)
            l.chispa(x + 0.06, y - 0.12, 0.05 * pulso(t, 1.1), rot=30 * t)


# ------------------------------------------------------------------ 3. lupa
@escena("lupa", "Lupa", "buscar, investigar, analizar, palabras clave, encontrar, revisar", 3.0)
def _lupa(l: Lienzo, t: float, dur: float):
    p = l.pal
    # la página con líneas de texto
    l.rect(0.19, 0.22, 0.65, 0.80, p.crema, radio=0.035)
    l.rect(0.25, 0.27, 0.45, 0.32, p.naranja, radio=0.012, borde=0)         # título
    lineas = (0.40, 0.50, 0.60, 0.70)
    anchos = (0.34, 0.26, 0.34, 0.20)
    cx = 0.52 + 0.07 * onda(t, 3.0)
    cy = 0.47 + 0.11 * onda(t, 1.5, 0.30)
    ent = e_out(prog(t, 0.05, 0.55))
    cx, cy = lerp(0.80, cx, ent), lerp(0.24, cy, ent)
    for y, w in zip(lineas, anchos):
        dentro = abs(y - cy) < 0.10 and (0.25 < cx + 0.1 and cx - 0.16 < 0.25 + w)
        l.rect(0.25, y - 0.017, 0.25 + w, y + 0.017, p.naranja if dentro else p.beige_osc, radio=0.014, borde=0)
    # mango + lente
    ang = math.radians(48)
    mx, my = cx + 0.165 * math.cos(ang), cy + 0.165 * math.sin(ang)
    l.trazo([(mx, my), (mx + 0.17 * math.cos(ang), my + 0.17 * math.sin(ang))], p.naranja, 0.07, borde=0.014)
    l.vidrio(cx, cy, 0.165, p.crema, 0.55)                                    # el cristal
    l.arco(cx, cy, 0.165, 0, 359.9, p.dorado, 0.055, borde=0.014)
    l.arco(cx, cy, 0.125, 200, 265, p.crema, 0.022, borde=0)                  # reflejo
    l.chispa(cx - 0.07, cy - 0.07, 0.03 * (0.6 + 0.4 * pulso(t, 0.9)), rot=20 * t, fill=p.crema, borde=0.004)


# ------------------------------------------------------------------ 4. cohete
@escena("cohete", "Cohete", "lanzar, despegar, crecer rápido, impulso, empezar, acelerar", 3.0)
def _cohete(l: Lienzo, t: float, dur: float):
    def _dibuja(s: Lienzo):
        _cohete_(s, t)
    l.sprite(1.0, _dibuja, 0.5, 0.455, esc=0.80)
    for x, y, ph in ((0.20, 0.26, 0.0), (0.82, 0.32, 0.3), (0.22, 0.70, 0.6), (0.80, 0.74, 0.15)):
        l.chispa(x, y, 0.040 * pulso(t, 1.0, ph), rot=30 * t)


def _cohete_(l: Lienzo, t: float):
    p = l.pal
    sube = e_out(prog(t, 0.0, 0.70))
    ox = 0.004 * onda(t, 0.08)
    oy = lerp(0.22, 0.0, sube) + 0.012 * onda(t, 1.8)
    # llama
    f = 0.115 + 0.035 * onda(t, 0.13)
    cx = 0.5 + ox
    base_y = 0.70 + oy
    l.forma([(cx - 0.055, base_y), (cx + 0.055, base_y), (cx, base_y + f * 1.9)], p.dorado)
    l.forma([(cx - 0.028, base_y), (cx + 0.028, base_y), (cx, base_y + f * 1.15)], p.naranja, borde=0)
    # humo que baja
    for i in range(3):
        k = ciclo(t + i * 0.33, 1.0)
        y = base_y + 0.06 + 0.16 * k
        x = cx + (0.05 + 0.05 * k) * (1 if i % 2 else -1)
        rr = 0.045 * (1 - 0.7 * k)
        if rr > 0.008 and k < 0.95:
            l.circulo(x, y, rr, p.crema, borde=0.008)
    # aletas
    l.forma([(cx - 0.085, 0.50 + oy), (cx - 0.20, 0.71 + oy), (cx - 0.085, 0.69 + oy)], p.naranja)
    l.forma([(cx + 0.085, 0.50 + oy), (cx + 0.20, 0.71 + oy), (cx + 0.085, 0.69 + oy)], p.naranja)
    # cuerpo
    pts = [(cx + 0.115 * math.sin(a), 0.43 + oy - 0.27 * math.cos(a)) for a in [i * 2 * math.pi / 48 for i in range(48)]]
    l.forma(pts, p.crema)
    # nariz
    nariz = [(cx + 0.115 * math.sin(a), 0.43 + oy - 0.27 * math.cos(a)) for a in [(-1.1 + i * 2.2 / 12) for i in range(13)]]
    l.forma(nariz, p.naranja)
    # ventana
    l.circulo(cx, 0.46 + oy, 0.052, p.dorado)
    l.arco(cx, 0.46 + oy, 0.03, 200, 265, p.crema, 0.014, borde=0)
    # tobera
    l.rect(cx - 0.055, 0.665 + oy, cx + 0.055, 0.70 + oy, p.dorado_osc, radio=0.012)


# ------------------------------------------------------------------ 5. candado
@escena("candado", "Candado", "seguridad, privacidad, https, proteger, acceso, bloqueo", 3.0)
def _candado(l: Lienzo, t: float, dur: float):
    p = l.pal
    cierra = e_out(prog(t, 0.30, 0.62))
    lift = 0.11 * (1 - cierra)
    golpe = prog(t, 0.60, 0.80)
    sy = 1 - 0.07 * math.sin(math.pi * golpe) if 0 < golpe < 1 else 1
    cx = 0.5
    # arco del candado
    pts = []
    for i in range(0, 21):
        a = math.pi + math.pi * i / 20
        pts.append((cx + 0.145 * math.cos(a), 0.415 - lift + 0.145 * math.sin(a) * 1.05))
    pts = [(cx - 0.145, 0.475 - lift * 0.35)] + pts + [(cx + 0.145, 0.475 + (0 if cierra > 0.95 else -lift * 0.9))]
    l.trazo(pts, p.beige, 0.062, borde=0.015)
    # cuerpo
    y0, y1 = 0.47 + (1 - sy) * 0.33, 0.81
    l.rect(0.27, y0, 0.73, y1, p.dorado, radio=0.06)
    l.rect(0.27, y0 + 0.02, 0.73, y0 + 0.04, p.dorado_cl, radio=0.01, borde=0)
    # ojo de la cerradura
    l.circulo(0.5, 0.60 + (1 - sy) * 0.3, 0.048, p.tinta, borde=0)
    l.rect(0.482, 0.60 + (1 - sy) * 0.3, 0.518, 0.70, p.tinta, radio=0.01, borde=0)
    # brillo que barre el cuerpo
    x = lerp(0.20, 0.82, ciclo(t - 0.9, 1.7)) if t > 0.9 else -1
    if 0.28 < x < 0.72:
        l.trazo([(x, y0 + 0.05), (x - 0.06, y1 - 0.05)], p.dorado_cl, 0.03, borde=0)
    # chispas del "clic"
    if 0.60 < t < 1.1:
        k = prog(t, 0.60, 1.1)
        for i in range(6):
            ang = math.radians(-90 + (i - 2.5) * 34)
            l.trazo([(cx + 0.20 * math.cos(ang), 0.41 + 0.20 * math.sin(ang)),
                     (cx + (0.20 + 0.11 * (1 - k)) * math.cos(ang), 0.41 + (0.20 + 0.11 * (1 - k)) * math.sin(ang))],
                    p.naranja, 0.022, borde=0.006)
    # insignia de verificado
    b = e_back(prog(t, 0.95, 1.35), 2.0)
    if b > 0.02:
        l.circulo(0.72, 0.74, 0.115 * b, p.naranja)
        pts = _punto_en_ruta([(0.67, 0.745), (0.705, 0.78), (0.77, 0.70)], 1.0)
        l.trazo(pts, p.crema, 0.03 * b + 0.002, borde=0)


# ------------------------------------------------------------------ 6. robot
@escena("robot", "Robot (IA)", "inteligencia artificial, IA, ChatGPT, asistente, automatizar, vibe coding, bot", 3.2)
def _robot(l: Lienzo, t: float, dur: float):
    p = l.pal

    def cuerpo(s: Lienzo):
        # antena
        s.trazo([(0.5, 0.20), (0.5, 0.11)], p.tinta, 0.016, borde=0)
        b = 0.038 * (1 + 0.25 * pulso(s_t[0], 0.9))
        s.circulo(0.5, 0.085, b, p.naranja if pulso(s_t[0], 1.2) > 0.4 else p.dorado)
        # orejas
        s.rect(0.155, 0.36, 0.215, 0.50, p.naranja, radio=0.02)
        s.rect(0.785, 0.36, 0.845, 0.50, p.naranja, radio=0.02)
        # cabeza
        s.rect(0.20, 0.20, 0.80, 0.60, p.crema, radio=0.08)
        s.rect(0.26, 0.26, 0.74, 0.54, p.tinta, radio=0.05, borde=0)
        # ojos (parpadean cada ~2 s, miran a los lados)
        mira = 0.022 * onda(s_t[0], 2.6)
        parp = 0.12 if (s_t[0] % 2.0) > 1.86 else 1.0
        for ex in (0.39, 0.61):
            s.elipse(ex + mira, 0.385, 0.050, 0.050 * parp, p.dorado, borde=0)
        # boca: ecualizador
        for i in range(5):
            h = 0.014 + 0.028 * pulso(s_t[0], 0.55, i * 0.19)
            s.rect(0.41 + i * 0.045, 0.50 - h, 0.435 + i * 0.045, 0.50, p.naranja, radio=0.008, borde=0)
        # cuello + cuerpo
        s.rect(0.44, 0.60, 0.56, 0.66, p.beige_osc, radio=0.012)
        s.rect(0.30, 0.66, 0.70, 0.84, p.naranja, radio=0.05)
        s.circulo(0.5, 0.75, 0.045, p.dorado)

    s_t = [t]
    ent = e_back(prog(t, 0.0, 0.5), 1.4)
    l.sprite(1.0, cuerpo, 0.5, 0.52 + 0.012 * onda(t, 1.9), rot=3.2 * onda(t, 2.4), esc=0.86 * ent)
    for x, y, ph in ((0.83, 0.20, 0.0), (0.16, 0.30, 0.5), (0.80, 0.62, 0.25)):
        l.chispa(x, y, 0.05 * pulso(t, 1.0, ph) * ent, rot=40 * t)


# ------------------------------------------------------------------ 7. bombilla
@escena("bombilla", "Bombilla", "idea, consejo, truco, ocurrencia, tip, descubrir, solución", 3.0)
def _bombilla(l: Lienzo, t: float, dur: float):
    p = l.pal
    encendida = t > 0.50 and not (0.56 < t < 0.62)
    glow = pulso(t, 1.3) if encendida else 0
    cx, cy = 0.5, 0.45
    if encendida:
        _rayos(l, cx, cy, 0.25, 0.37, 12, t, p.dorado, 0.028, rot=8 * t, periodo=1.3)
    # cuello
    l.forma([(0.42, 0.61), (0.58, 0.61), (0.56, 0.70), (0.44, 0.70)], p.beige)
    # cristal
    l.circulo(cx, cy, 0.185, p.dorado if encendida else p.beige)
    if encendida:
        l.arco(cx, cy, 0.135, 205, 262, p.dorado_cl, 0.03, borde=0)
        # filamento
        l.trazo([(0.44, 0.62), (0.46, 0.50), (0.5, 0.55), (0.54, 0.50), (0.56, 0.62)], p.naranja, 0.022, borde=0)
    else:
        l.trazo([(0.44, 0.62), (0.46, 0.50), (0.5, 0.55), (0.54, 0.50), (0.56, 0.62)], p.beige_osc, 0.022, borde=0)
    # casquillo
    for i, y in enumerate((0.70, 0.745, 0.79)):
        l.rect(0.445 + (0.01 if i == 2 else 0), y, 0.555 - (0.01 if i == 2 else 0), y + 0.035,
               p.tinta if i % 2 == 0 else p.beige_osc, radio=0.012)
    if t > 0.7:
        for x, y, ph in ((0.20, 0.20, 0.0), (0.82, 0.62, 0.5)):
            l.chispa(x, y, 0.05 * pulso(t, 1.1, ph), rot=35 * t)


# ------------------------------------------------------------------ 8. engranajes
@escena("engranajes", "Engranajes", "automatizar, configurar, proceso, funcionamiento, ajustes, mecanismo", 3.0)
def _engranajes(l: Lienzo, t: float, dur: float):
    p = l.pal
    ent = e_out(prog(t, 0.0, 0.6))
    v = 42.0
    a1 = -110 * (1 - ent) + v * t
    a2 = 110 * (1 - ent) - v * t * 10 / 7 + 18
    l.engranaje(0.42, 0.45, 0.235, 10, a1, p.naranja, agujero=0.075)
    l.engranaje(0.685, 0.685, 0.145, 7, a2, p.dorado, agujero=0.048)
    l.chispa(0.60, 0.53, 0.04 * pulso(t, 0.8), rot=50 * t, fill=p.crema, borde=0.005)
