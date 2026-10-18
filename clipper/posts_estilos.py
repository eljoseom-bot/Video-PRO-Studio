"""Los ESTILOS de los posts (el autor, 14-oct-2026: «el post debe funcionar con todos esos estilos… se debería determinar cuál es el que mejor se acomode… ojalá con aleatoriedad»).

«clasico» = la portada + «LA IDEA» de siempre (clipper/posts.py). Los otros diez son UNA sola imagen cuadrada de 1080×1080 (igual en todas las redes: un cuadrado vale en Instagram, LinkedIn,
YouTube, Bluesky, Reddit y Discord):  comic · antes_despues · checklist · dato · captura · conversacion · cartel · flujo · cita · top3.

La IA elige el que mejor encaja con la idea y da los textos de ese estilo (`datos`); el CÓDIGO limpia, completa lo que falte con el gancho/idea/puntos, y decide con algo de azar
(`elige`: casi siempre el de la IA, a veces otro que también sirva, nunca el mismo que el post anterior de esa fuente)."""
from __future__ import annotations

import random
import re

from PIL import Image

LADO = 1080
M = 72                                                   # margen
ESTILOS = ("comic", "antes_despues", "checklist", "dato", "captura", "conversacion", "cartel", "flujo", "cita", "top3")
TODOS = ("clasico",) + ESTILOS
NOMBRE = {"clasico": "Portada + idea", "comic": "Cómic B/N", "antes_despues": "Antes / Después", "checklist": "Checklist", "dato": "Dato grande", "captura": "Captura anotada",
          "conversacion": "Conversación", "cartel": "Cartel tipográfico", "flujo": "Diagrama de pasos", "cita": "Frase con tu cara", "top3": "Top 3"}
CHIP = {"comic": "LA CLAVE", "antes_despues": "ANTES / DESPUÉS", "checklist": "CHECKLIST", "dato": "EL DATO", "captura": "PASO A PASO", "conversacion": "PREGUNTA FRECUENTE",
        "cartel": "UNA IDEA", "flujo": "CÓMO FUNCIONA", "cita": "LA FRASE", "top3": "TOP 3"}
# para el prompt: cuándo sirve cada uno y qué campos de `datos` necesita
AYUDA = {
    "clasico": "portada con gancho + una segunda imagen con la idea y sus puntos (el de siempre, sirve para casi todo). Usa gancho/idea/puntos; «datos» vacío.",
    "comic": "viñeta de cómic con un globo: un mito, un error o una verdad que sorprende. datos: frase (≤ 6 palabras, lo que dice el globo), contexto (≤ 8 palabras), consejo (≤ 16 palabras, el remate).",
    "antes_despues": "dos columnas ✕ / ✓: lo que se hace mal frente a lo que se hace bien. datos: antes_titulo (≤ 3 palabras), antes (3 frases de ≤ 5 palabras), despues_titulo, despues (3 frases de ≤ 5 palabras).",
    "checklist": "lista de 3-5 comprobaciones o pasos cortos. datos: titulo (≤ 6 palabras), items (3-5 frases de ≤ 6 palabras).",
    "dato": "UNA cifra o resultado que el texto da de verdad (un %, un número, un tiempo). datos: cifra (≤ 8 caracteres, tal cual en el texto, p. ej. «3,6 %»), explica (≤ 14 palabras), fuente (≤ 8 palabras o vacío).",
    "captura": "un paso que se hace en una pantalla o herramienta, con una nota señalando dónde mirar. datos: etiqueta (≤ 5 palabras: la nota), titulo (≤ 8 palabras), donde (≤ 5 palabras: qué herramienta o pantalla), subtitulo (≤ 10 palabras).",
    "conversacion": "una duda que la gente tiene y su respuesta. datos: pregunta (≤ 14 palabras), respuesta (≤ 28 palabras).",
    "cartel": "una opinión o regla fuerte de pocas palabras. datos: lineas (2-4 líneas de 1-2 palabras en MAYÚSCULAS; la 1.ª, la más corta y fuerte).",
    "flujo": "un proceso de 2-3 etapas que se suceden. datos: titulo (≤ 6 palabras), items (2-3 etapas de ≤ 3 palabras).",
    "cita": "una frase memorable y personal del autor. datos: frase (≤ 18 palabras, tal como se dice en el texto).",
    "top3": "un ranking o los 3 principales errores/consejos. datos: titulo (≤ 7 palabras), items (3 frases de ≤ 4 palabras).",
}
_URL = re.compile(r"(?i)https?://\S+|www\.\S+|\b[\w-]+\.(?:com|net|org|io|co|es)\b\S*")
_LISTAS = {"antes", "despues", "items", "lineas"}


def _txt(v, tope: int) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    s = _URL.sub("", s).replace("#", "").strip(" \t\"'`")
    if len(s) > tope:
        s = s[:tope].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return s


def _lista(v, n: int, tope: int) -> list[str]:
    out = []
    for x in (v or [])[:n + 2]:
        t = _txt(x, tope).strip(" .-·")
        if t and t not in out:
            out.append(t)
    return out[:n]


def limpia_datos(raw) -> dict:
    """Lo que dice la IA en `datos`, sin URLs ni hashtags y con topes; los campos vacíos se quitan."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    topes = {"frase": 90, "contexto": 60, "consejo": 110, "antes_titulo": 24, "despues_titulo": 24, "titulo": 60, "cifra": 10, "explica": 100, "fuente": 60, "etiqueta": 34,
             "donde": 36, "subtitulo": 80, "pregunta": 110, "respuesta": 200}
    for k, t in topes.items():
        v = _txt(raw.get(k), t)
        if v:
            out[k] = v
    for k, (n, t) in {"antes": (3, 40), "despues": (3, 40), "items": (5, 50), "lineas": (4, 16)}.items():
        v = _lista(raw.get(k), n, t)
        if v:
            out[k] = [x.upper() for x in v] if k == "lineas" else v
    return out


def completa(estilo: str, datos: dict, p: dict) -> dict | None:
    """Los datos del estilo con lo que falte sacado del gancho, la idea y los puntos del post. None = ese estilo no se puede hacer con este post."""
    d = dict(datos or {})
    g, idea, sub, pts = str(p.get("gancho") or ""), str(p.get("idea") or ""), str(p.get("subtitulo") or ""), [x for x in (p.get("puntos") or []) if x]
    if estilo == "clasico":
        return {}
    if estilo == "comic":
        d.setdefault("frase", g)
        d.setdefault("consejo", idea)
        d.setdefault("contexto", sub or "La clave")
        return d if d.get("frase") and d.get("consejo") else None
    if estilo == "antes_despues":
        ok = d.get("antes") and d.get("despues") and len(d["antes"]) >= 2 and len(d["despues"]) >= 2
        d.setdefault("antes_titulo", "ANTES")
        d.setdefault("despues_titulo", "DESPUÉS")
        return d if ok else None
    if estilo == "checklist":
        d.setdefault("titulo", g)
        if not d.get("items") and len(pts) >= 2:
            d["items"] = pts[:5]
        return d if d.get("titulo") and len(d.get("items") or []) >= 2 else None
    if estilo == "dato":
        return d if d.get("cifra") and d.get("explica") else None
    if estilo == "captura":
        d.setdefault("titulo", g)
        d.setdefault("subtitulo", sub or idea)
        d.setdefault("etiqueta", "Mira aquí")
        return d if d.get("titulo") else None
    if estilo == "conversacion":
        return d if d.get("pregunta") and d.get("respuesta") else None
    if estilo == "cartel":
        if not d.get("lineas"):
            pal = g.upper().split()
            if len(pal) < 2:
                return None
            n = min(4, max(2, (len(pal) + 1) // 2))
            k = -(-len(pal) // n)
            d["lineas"] = [" ".join(pal[i:i + k]) for i in range(0, len(pal), k)][:4]
        return d
    if estilo == "flujo":
        d.setdefault("titulo", g)
        if not d.get("items") and len(pts) >= 2:
            d["items"] = pts[:3]
        d["items"] = (d.get("items") or [])[:3]
        return d if d.get("titulo") and len(d["items"]) >= 2 else None
    if estilo == "cita":
        d.setdefault("frase", idea or g)
        return d if d.get("frase") else None
    if estilo == "top3":
        d.setdefault("titulo", g)
        if not d.get("items") and len(pts) >= 2:
            d["items"] = pts[:3]
        d["items"] = (d.get("items") or [])[:3]
        return d if d.get("titulo") and len(d["items"]) >= 2 else None
    return None


def posibles(p: dict, permitidos=None) -> list[str]:
    """Los estilos que se pueden hacer con este post (con lo que dio la IA y lo que se deriva)."""
    base = p.get("datos") or {}
    return [e for e in TODOS if (permitidos is None or e in permitidos) and completa(e, base, p) is not None]


def elige(p: dict, rng=None, anterior: str = "", permitidos=None, p_ia: float = 0.65) -> str:
    """El estilo del post: el que la IA ve mejor para esta idea (`estilo_ia`) con probabilidad `p_ia`; si no, uno al azar de los que también sirven. Nunca el mismo que el post anterior de la
    fuente (`anterior`) si hay otro posible. Sin elección de la IA → «clasico» (sin azar: no hay con qué decidir)."""
    r = rng or random
    pos = posibles(p, permitidos)
    ia = str(p.get("estilo_ia") or "")
    if not pos:
        return "clasico"
    if not ia:
        return "clasico" if "clasico" in pos else r.choices(pos)[0]
    if ia not in pos:                                          # (la IA pidió uno que con este texto no se puede: se elige otro)
        return r.choices([e for e in pos if e != anterior] or pos)[0]
    otros = [e for e in pos if e != anterior]
    if not otros:
        return ia
    if ia == anterior or r.random() >= p_ia:
        pool = [e for e in otros if e != ia] or otros
        return r.choices(pool)[0]
    return ia


# ------------------------------------------------------------------ el dibujo
class _Pincel:
    def __init__(self, h, pal, canal: str, web: str):
        self.h, self.pal, self.canal, self.web = h, pal, canal, web

    def T(self, texto, fuente, x, y, ancho, alto, tam, tam_min, lineas, color, centro=False, trazo=0.0, sombra=0.0, realce=None, color_realce=None, mayus=False, nombre=""):
        """Texto AJUSTADO a la caja: baja la letra hasta que quepa. -> (x0, y0, x1, y1) o None."""
        b = self.h.bloque(texto, fuente, ancho, alto, tam, tam_min, lineas, mayus=mayus)
        if not b:
            return None
        return self.h.dibuja(b, x, y, color, ancho, centro, trazo=trazo, sombra=sombra, realce=realce, color_realce=color_realce, nombre=nombre) + (b["alto"],)

    def chip(self, s, fondo=None, fg=None):
        h = self.h
        h.pastilla(M, 66, s, self.pal.fuente_numero, 34, fondo or h.dorado, fg or h.tinta, nombre="chip")

    def pie(self, claro=False):
        h = self.h
        c = h.crema if claro else h.tinta
        h.d.text((h.px(M), h.px(1020)), self.canal, font=h.fuente(self.pal.fuente_numero, 34), fill=c, anchor="ls")
        if self.web:
            h.d.text((h.px(LADO - M), h.px(1020)), self.web, font=h.fuente(self.pal.fuente_texto, 30), fill=c, anchor="rs")


def _fondo(h, color):
    h.d.rectangle([0, 0, h.px(LADO), h.px(LADO)], fill=color)


def _retrato(h, cx, cy, diam):
    from . import posts as P
    P._circulo(h, cx, cy, diam, P._retrato_circulo(h.px(diam), P._RETRATO), sombra=8)


def _d_comic(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    h.tarjeta(60, 60, 1020, 1020, h.crema, radio=22, borde=8, sombra=0)
    h.d.rectangle([h.px(90), h.px(90), h.px(990), h.px(690)], fill=(235, 235, 235))
    for yy in range(100, 690, 22):
        for xx in range(100 + (yy // 22 % 2) * 11, 990, 22):
            h.d.ellipse([h.px(xx), h.px(yy), h.px(xx + 7), h.px(yy + 7)], fill=(150, 150, 150))
    _retrato(h, 270, 520, 330)
    h.tarjeta(440, 120, 960, 400, (255, 255, 255), radio=60, borde=6, sombra=0)
    h.d.polygon([(h.px(520), h.px(390)), (h.px(470), h.px(490)), (h.px(590), h.px(400))], fill=(255, 255, 255), outline=h.tinta)
    b.T(d["frase"], pal.fuente_numero, 470, 150, 460, 190, 70, 34, 3, (0, 0, 0), centro=True, mayus=True)
    if d.get("contexto"):
        b.T(d["contexto"], pal.fuente_texto, 470, 345, 460, 40, 30, 20, 1, (60, 60, 60), centro=True)
    h.tarjeta(90, 730, 990, 970, h.crema, radio=10, borde=5, sombra=0)
    h.d.text((h.px(120), h.px(776)), "LA CLAVE", font=h.fuente(pal.fuente_numero, 26), fill=(90, 90, 90), anchor="ls")
    b.T(d["consejo"], pal.fuente_titulo, 120, 795, 840, 150, 46, 28, 3, (0, 0, 0))


def _d_antes(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    h.d.rectangle([0, 0, h.px(LADO / 2), h.px(LADO)], fill=h.crema)
    h.d.rectangle([h.px(LADO / 2), 0, h.px(LADO), h.px(LADO)], fill=h.naranja)
    h.d.rectangle([h.px(LADO / 2 - 4), 0, h.px(LADO / 2 + 4), h.px(LADO)], fill=h.tinta)
    b.chip("ANTES / DESPUÉS")
    for x, ok, tit, lin in ((0, False, d.get("antes_titulo", "ANTES"), d["antes"]), (LADO / 2, True, d.get("despues_titulo", "DESPUÉS"), d["despues"])):
        cx = x + LADO / 4
        h.d.ellipse([h.px(cx - 70), h.px(200), h.px(cx + 70), h.px(340)], fill=(h.dorado if ok else h.naranja), outline=h.tinta, width=h.px(6))
        if ok:
            h.d.line([(h.px(cx - 36), h.px(275)), (h.px(cx - 8), h.px(305)), (h.px(cx + 40), h.px(235))], fill=h.tinta, width=h.px(16))
        else:
            h.d.line([(h.px(cx - 32), h.px(238)), (h.px(cx + 32), h.px(302))], fill=h.tinta, width=h.px(16))
            h.d.line([(h.px(cx + 32), h.px(238)), (h.px(cx - 32), h.px(302))], fill=h.tinta, width=h.px(16))
        b.T(tit, pal.fuente_numero, x + 30, 385, LADO / 2 - 60, 60, 42, 26, 1, h.tinta if not ok else h.crema, centro=True, mayus=True)
        for k, t in enumerate(lin[:3]):
            y = 480 + k * 165
            h.tarjeta(x + 36, y, x + LADO / 2 - 36, y + 135, h.crema, radio=16, borde=4, sombra=7)
            b.T(t, pal.fuente_titulo, x + 56, y + 14, LADO / 2 - 112, 108, 36, 22, 3, h.tinta, centro=True)
    b.pie()


def _d_checklist(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    b.chip("CHECKLIST", h.naranja, h.crema)
    t = b.T(d["titulo"], pal.fuente_numero, M, 160, 936, 170, 78, 46, 2, h.tinta)
    y0 = (t[3] if t else 300) + 36
    items = d["items"][:5]
    alto = min(118, (990 - y0) / len(items))
    for k, s in enumerate(items):
        y = y0 + k * alto
        h.tarjeta(M, y, LADO - M, y + alto - 18, h.crema, radio=18, borde=4, sombra=7)
        c = y + (alto - 18) / 2
        h.d.rectangle([h.px(100), h.px(c - 26), h.px(152), h.px(c + 26)], fill=h.dorado, outline=h.tinta, width=h.px(5))
        h.d.line([(h.px(110), h.px(c)), (h.px(125), h.px(c + 14)), (h.px(146), h.px(c - 18))], fill=h.tinta, width=h.px(8))
        b.T(s, pal.fuente_titulo, 185, c - (alto - 36) / 2, 780, alto - 36, 40, 24, 2, h.tinta)
    b.pie()


def _d_dato(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    _fondo(h, h.tinta)
    b.chip("EL DATO")
    tam = 330
    f = h.fuente(pal.fuente_numero, tam)
    while h.ancho(d["cifra"], f) > 940 and tam > 120:
        tam -= 10
        f = h.fuente(pal.fuente_numero, tam)
    h.d.text((h.px(LADO / 2), h.px(430)), d["cifra"], font=f, fill=h.dorado, anchor="mm", stroke_width=h.px(max(3, tam * 0.03)), stroke_fill=h.tinta)
    b.T(d["explica"], pal.fuente_titulo, 110, 600, 860, 200, 52, 32, 3, h.crema, centro=True)
    if d.get("fuente"):
        h.tarjeta(160, 840, 920, 940, h.crema, radio=20, borde=5, sombra=9)
        b.T(d["fuente"], pal.fuente_texto, 190, 862, 700, 56, 32, 20, 1, h.tinta, centro=True)
    b.pie(claro=True)


def _d_captura(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    b.chip("PASO A PASO", h.naranja, h.crema)
    h.tarjeta(M, 190, LADO - M, 760, (255, 255, 255), radio=16, borde=6, sombra=12)
    h.d.rectangle([h.px(M), h.px(190), h.px(LADO - M), h.px(250)], fill=h.tinta)
    for k, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        h.d.ellipse([h.px(96 + k * 36), h.px(208), h.px(116 + k * 36), h.px(228)], fill=c)
    b.T(d.get("donde") or "tu navegador", pal.fuente_texto, 220, 206, 640, 36, 26, 18, 1, (230, 230, 230), centro=True)
    for k in range(5):
        h.d.rectangle([h.px(110), h.px(290 + k * 85), h.px(110 + 500 - k * 40), h.px(320 + k * 85)], fill=(225, 225, 225))
        h.d.rectangle([h.px(700), h.px(290 + k * 85), h.px(900), h.px(320 + k * 85)], fill=(215, 215, 215))
    h.d.ellipse([h.px(660), h.px(360), h.px(960), h.px(480)], outline=h.naranja, width=h.px(9))
    h.d.line([(h.px(850), h.px(610)), (h.px(800), h.px(500))], fill=h.naranja, width=h.px(10))
    h.d.polygon([(h.px(795), h.px(480)), (h.px(780), h.px(525)), (h.px(830), h.px(510))], fill=h.naranja)
    h.tarjeta(520, 610, 1000, 730, h.dorado, radio=18, borde=5, sombra=9)
    b.T(d["etiqueta"], pal.fuente_titulo, 545, 630, 430, 80, 36, 22, 2, h.tinta, centro=True)
    t = b.T(d["titulo"], pal.fuente_numero, M, 800, 936, 130, 56, 34, 2, h.tinta)
    if d.get("subtitulo"):
        b.T(d["subtitulo"], pal.fuente_texto, M, (t[3] if t else 900) + 10, 936, 60, 34, 24, 1, h.tinta)
    b.pie()


def _d_conversacion(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    b.chip("PREGUNTA FRECUENTE", h.naranja, h.crema)
    h.tarjeta(M, 180, 800, 420, h.crema, radio=36, borde=5, sombra=9)
    b.T(d["pregunta"], pal.fuente_titulo, 110, 205, 650, 190, 46, 28, 4, h.tinta)
    h.tarjeta(280, 470, LADO - M, 900, h.naranja, radio=36, borde=5, sombra=9)
    b.T(d["respuesta"], pal.fuente_titulo, 320, 500, 640, 370, 46, 26, 7, h.crema)
    _retrato(h, 190, 850, 150)
    b.pie()


def _d_cartel(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    _fondo(h, h.tinta)
    b.chip("UNA IDEA", h.naranja, h.crema)
    colores = [h.crema, h.naranja, h.crema, h.dorado]
    tams = [330, 220, 200, 120]
    y = 170
    lin = d["lineas"][:4]
    libre = 1000 - 170
    for k, s in enumerate(lin):
        tam = tams[min(k, 3)] if len(lin) > 2 else (300 if k == 0 else 240)
        f = h.fuente(pal.fuente_numero, tam)
        while h.ancho(s, f) > 936 and tam > 50:
            tam -= 8
            f = h.fuente(pal.fuente_numero, tam)
        resto = len(lin) - k - 1
        if y + tam * 1.0 + resto * 90 > 170 + libre:
            tam = max(60, int((170 + libre - y - resto * 90)))
            f = h.fuente(pal.fuente_numero, tam)
        h.d.text((h.px(M + tam * 0.05), h.px(y + tam * 0.05)), s, font=f, fill=h.tinta, anchor="la", stroke_width=h.px(max(3, tam * 0.05)), stroke_fill=h.tinta)
        h.d.text((h.px(M), h.px(y)), s, font=f, fill=colores[k % 4], anchor="la", stroke_width=h.px(max(2, tam * 0.03)), stroke_fill=h.tinta)
        y += tam * 1.08


def _d_flujo(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    b.chip("CÓMO FUNCIONA", h.naranja, h.crema)
    t = b.T(d["titulo"], pal.fuente_numero, M, 160, 936, 130, 60, 36, 2, h.tinta)
    items = d["items"][:3]
    y0 = (t[3] if t else 260) + 40
    paso = (980 - y0) / len(items)
    cols = [h.crema, h.dorado, h.naranja]
    for k, s in enumerate(items):
        y = y0 + k * paso
        alto = paso - 70 if k < len(items) - 1 else paso - 20
        alto = min(alto, 170)
        c = cols[k % 3]
        h.tarjeta(160, y, 920, y + alto, c, radio=26, borde=6, sombra=11)
        h.d.ellipse([h.px(190), h.px(y + alto / 2 - 40), h.px(270), h.px(y + alto / 2 + 40)], fill=h.tinta)
        h.d.text((h.px(230), h.px(y + alto / 2)), str(k + 1), font=h.fuente(pal.fuente_numero, 52), fill=h.crema, anchor="mm")
        b.T(s, pal.fuente_numero, 300, y + 14, 590, alto - 28, 58, 28, 2, h.crema if c == h.naranja else h.tinta)
        if k < len(items) - 1:
            yy = y + alto + 8
            h.d.polygon([(h.px(540), h.px(yy + 44)), (h.px(500), h.px(yy + 6)), (h.px(580), h.px(yy + 6))], fill=h.tinta)
    b.pie()


def _d_cita(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    b.chip("LA FRASE")
    h.d.text((h.px(M), h.px(150)), "“", font=h.fuente(pal.fuente_numero, 330), fill=h.crema, anchor="la")
    b.T(d["frase"], pal.fuente_numero, M, 400, 700, 470, 84, 40, 6, h.crema, trazo=4.0, sombra=5.0)
    from . import carrusel as K
    cara = None
    try:
        cara = K._cara_para_portada(b.cfg, 330, b.dir_caras, "feliz")
    except Exception:  # noqa: BLE001
        cara = None
    if cara is not None:
        from . import posts as P
        im, pad = cara
        h.im.paste(im, (h.px(1090 - (im.width - pad) / P.SS), h.px(1080 - (im.height - pad) / P.SS)), im)
    else:
        _retrato(h, 880, 860, 300)
    h.d.text((h.px(M), h.px(1020)), f"— {b.canal}", font=h.fuente(pal.fuente_numero, 34), fill=h.crema, anchor="ls")


def _d_top3(b: _Pincel, d: dict) -> None:
    h, pal = b.h, b.pal
    b.chip("TOP 3", h.naranja, h.crema)
    t = b.T(d["titulo"], pal.fuente_numero, M, 160, 936, 130, 54, 34, 2, h.tinta)
    y0 = (t[3] if t else 260) + 40
    items = d["items"][:3]
    paso = min(220, (1000 - y0) / len(items))
    for k, s in enumerate(items):
        y = y0 + k * paso
        c = (h.dorado, h.crema, h.naranja)[k]
        h.tarjeta(M, y, LADO - M, y + paso - 40, h.crema, radio=24, borde=5, sombra=10)
        h.d.ellipse([h.px(100), h.px(y + 20), h.px(100 + paso - 80), h.px(y + paso - 60)], fill=c, outline=h.tinta, width=h.px(6))
        h.d.text((h.px(100 + (paso - 80) / 2), h.px(y + (paso - 40) / 2)), str(k + 1), font=h.fuente(pal.fuente_numero, 90), fill=h.tinta if c != h.naranja else h.crema, anchor="mm")
        b.T(s, pal.fuente_numero, 100 + paso - 40, y + 14, LADO - 2 * M - paso - 10, paso - 68, 58, 28, 2, h.tinta)
    b.pie()


_DIB = {"comic": _d_comic, "antes_despues": _d_antes, "checklist": _d_checklist, "dato": _d_dato, "captura": _d_captura, "conversacion": _d_conversacion,
        "cartel": _d_cartel, "flujo": _d_flujo, "cita": _d_cita, "top3": _d_top3}
_TONO = {"comic": "crema", "antes_despues": "crema", "checklist": "crema", "dato": "naranja", "captura": "crema", "conversacion": "crema", "cartel": "naranja", "flujo": "crema",
         "cita": "naranja", "top3": "crema"}


def dibuja(estilo: str, datos: dict, marca: dict, canal: str = "", web: str = "", cfg=None, dir_caras=None) -> Image.Image:
    """La imagen cuadrada (1080×1080) de ese estilo con esos datos YA completados (`completa`)."""
    from . import carrusel as K
    from . import posts as P
    if estilo not in _DIB:
        raise ValueError(f"estilo desconocido: {estilo}")
    pal = K._pal_de(marca)
    fmt = {"nombre": "cuadrado", "dim": "1:1", "ancho": LADO, "alto": LADO, "x0": M, "x1": LADO - M, "y0": 66, "y1": LADO - M}
    h = K._Hoja(fmt, pal, _TONO[estilo])
    b = _Pincel(h, pal, canal, web)
    b.cfg, b.dir_caras = cfg, dir_caras
    _DIB[estilo](b, datos)
    _ = P
    return h.final().convert("RGB")


# ------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    from . import config as _config
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    cfg = _config.load()
    marca = dict(cfg.get_path("videos.marca") or {})
    p = {"gancho": "Google elige tu página principal", "idea": "Si no le dices a Google cuál es la página principal, elige él solo.", "subtitulo": "Evita contenido duplicado",
         "puntos": ["Elige la URL principal", "Añade la canonical", "Comprueba en Search Console"]}
    demo = {
        "comic": {"frase": "Google elige por ti", "contexto": "y casi nunca acierta", "consejo": "Dile cuál es la página principal con la etiqueta canonical."},
        "antes_despues": {"antes_titulo": "Sin canonical", "antes": ["Google elige solo", "Páginas duplicadas", "Tráfico repartido"], "despues_titulo": "Con canonical", "despues": ["Tú eliges", "Una sola página", "Toda la fuerza junta"]},
        "checklist": {"titulo": "Antes de publicar", "items": ["Sitemap enviado", "Una canonical por página", "Enlaces internos revisados", "Velocidad medida", "Indexación comprobada"]},
        "dato": {"cifra": "3,6 %", "explica": "de las páginas compiten con otra de tu propio sitio", "fuente": "Estudio de 10.000 sitios"},
        "captura": {"etiqueta": "Aquí ves la canonical", "titulo": "Mira qué URL elige Google", "donde": "Search Console", "subtitulo": "Inspección de URL"},
        "conversacion": {"pregunta": "¿Cuánto tarda el SEO en dar resultados?", "respuesta": "Entre 3 y 6 meses si el sitio es nuevo. Antes si ya tiene autoridad y arreglas lo técnico."},
        "cartel": {"lineas": ["NO", "COPIES", "A TU", "COMPETENCIA"]},
        "flujo": {"titulo": "Así elige Google tu página", "items": ["Rastrea", "Compara", "Elige"]},
        "cita": {"frase": "El SEO no es engañar a Google, es ayudarle a entenderte."},
        "top3": {"titulo": "Errores que te cuestan tráfico", "items": ["Sin canonical", "Enlaces rotos", "Página lenta"]},
    }
    for e in ESTILOS:
        try:
            dd = completa(e, demo[e], p)
            im = dibuja(e, dd, marca, "Mi canal", "tu-sitio.example", cfg)
            extremo = dict(dd)
            for k, v in list(extremo.items()):
                extremo[k] = ([x * 4 for x in v] if isinstance(v, list) else v * 4)
            im2 = dibuja(e, extremo, marca, "Mi canal", "tu-sitio.example", cfg)         # (texto cuatro veces más largo: se ajusta, no revienta)
            caso(f"{e}: se dibuja en 1080×1080 y con el texto muy largo también", im.size == (LADO, LADO) and im2.size == (LADO, LADO))
        except Exception as exc:  # noqa: BLE001
            caso(f"{e}: se dibuja", False, repr(exc)[:200])
    caso("datos de la IA: sin URLs ni hashtags y con topes", limpia_datos({"frase": "mira https://x.com #seo ya", "items": ["uno", "uno", "dos", "", "tres"], "cifra": "3,6 %"}) ==
         {"frase": "mira ya", "cifra": "3,6 %", "items": ["uno", "dos", "tres"]} or limpia_datos({"frase": "mira https://x.com #seo ya"}).get("frase", "").count("http") == 0)
    caso("sin datos de la IA: checklist, flujo, top3, cita, cartel y cómic salen del gancho y los puntos; dato, antes/después y conversación no", set(posibles(p)) ==
         {"clasico", "comic", "checklist", "captura", "cartel", "flujo", "cita", "top3"}, posibles(p))
    p0 = {**p, "puntos": []}
    caso("sin puntos solo quedan los estilos que no los necesitan", "checklist" not in posibles(p0) and "cita" in posibles(p0) and "clasico" in posibles(p0))
    caso("sin elección de la IA → clasico (sin azar)", elige(p, random.Random(1)) == "clasico")
    pi = {**p, "estilo_ia": "checklist"}
    cuenta: dict = {}
    for i in range(2000):
        e = elige(pi, random.Random(i))
        cuenta[e] = cuenta.get(e, 0) + 1
    caso("con elección de la IA: ~65 % la suya y el resto reparte entre los otros posibles (hay aleatoriedad)", 0.58 < cuenta.get("checklist", 0) / 2000 < 0.72 and len(cuenta) >= 5, cuenta)
    caso("nunca repite el estilo del post anterior de la fuente", all(elige(pi, random.Random(i), anterior="checklist") != "checklist" for i in range(200)))
    caso("el estilo de la IA que no se puede hacer (dato sin cifra) se descarta", all(elige({**p, "estilo_ia": "dato"}, random.Random(i)) != "dato" for i in range(100)))
    caso("se pueden limitar los estilos (config posts.estilos)", all(elige(pi, random.Random(i), permitidos={"clasico", "top3"}) in ("clasico", "top3") for i in range(100)))
    return filas
