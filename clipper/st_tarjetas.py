"""«Shorts · TikTok» — LO QUE SE DIBUJA encima del Short (todo en vertical 1080×1920, con la marca del canal: contorno de tinta, sombra dura, Rubik).

Cada elemento es una función del tiempo local `t` (segundos desde que entra): `pinta(canvas, t)` lo pega sobre el fotograma. Los dibujos estáticos (sprites) se hacen UNA vez al doble de tamaño
y se reducen (bordes sin dientes); la animación (pop, deslizar, fundido) es solo escalar y mover ese sprite, así un Short de 2 minutos no tarda una eternidad.

Copia propia de las ideas de `tarjetas.py`/`ritmo.py`/`escena_completa.py`/`escenas_estilo.py` de Vídeos PRO (el autor, 16-oct-2026: «lo de Video Pro no lo toques, duplícalo y replantéalo en vertical»).
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from PIL import Image, ImageDraw

from . import st_ilus
from .st_ilus_motor import Pal, clamp, e_back, e_out, fuente

W, H = 1080, 1920
# LAYOUTS (el autor, 16-oct: «mi cámara arriba, la pantalla en el medio y los subtítulos abajo es como una escena: crea más de esas escenas»). Cada layout reparte el alto en
# cara (arriba) / subtítulos (banda pequeña, lejos del borde de abajo donde TikTok pone sus botones) / pantalla; los últimos SEGURO px quedan libres.
SEGURO = 140
LAYOUTS = {                         # nombre: (alto de la cara, alto de la banda de subtítulos)
    "base": (640, 170),             # cara arriba, subtítulos (banda fina: que no corte la cara ni la boca), pantalla grande debajo
    "pantalla": (400, 170),         # la pantalla manda, la cara en una franja más baja
    "cara": (1000, 170),            # la cara manda, la pantalla en una tira
}


class Zona:
    """Las medidas del layout que está en pantalla AHORA (el montaje la cambia en cada corte; las capas la leen)."""
    nombre = "base"
    face_h = 640
    sub_y0 = 640
    sub_h = 170
    b_y0 = 810
    b_h = H - SEGURO - 810

    @classmethod
    def pon(cls, nombre: str) -> None:
        fh, sh = LAYOUTS.get(nombre, LAYOUTS["base"])
        cls.nombre, cls.face_h, cls.sub_y0, cls.sub_h = nombre, fh, fh, sh
        cls.b_y0 = fh + sh
        cls.b_h = H - SEGURO - cls.b_y0


def medidas(nombre: str) -> tuple[int, int, int, int, int]:
    """(alto de la cara, y de los subtítulos, alto de la banda, y de la pantalla, alto de la pantalla) de un layout."""
    fh, sh = LAYOUTS.get(nombre, LAYOUTS["base"])
    return fh, fh, sh, fh + sh, H - SEGURO - fh - sh


FACE_H = LAYOUTS["base"][0]
B_Y0 = FACE_H + LAYOUTS["base"][1]
B_H = H - SEGURO - B_Y0
SUB_Y0 = FACE_H
SUB_H = LAYOUTS["base"][1]
SS = 2


def e_out_cubic(k: float) -> float:
    k = clamp(k)
    return 1.0 - (1.0 - k) ** 3


def pop(k: float) -> float:
    """0 → 1,12 → 1 (rebote corto)."""
    k = clamp(k)
    return e_back(k, 2.2) if k < 1 else 1.0


def _mezcla(c1, c2, k: float):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * k)) for i in range(3))


class Est:
    """Los colores y las letras de la marca + las cachés de dibujo de UN Short."""

    def __init__(self, marca: dict, canal: str = "", web: str = ""):
        self.pal: Pal = st_ilus.paleta(marca)
        self.canal, self.web = canal, web
        p = self.pal
        self.tinta, self.crema, self.naranja, self.dorado, self.beige = (p.tinta[:3], p.crema[:3], p.naranja[:3], p.dorado[:3], p.beige[:3])
        self._sprites: dict = {}
        self._fondos: dict = {}

    def f(self, ruta: str, px: float):
        return fuente(ruta, max(1, int(round(px * SS))))

    def cache(self, clave, fn):
        v = self._sprites.get(clave)
        if v is None:
            v = fn()
            if len(self._sprites) > 700:
                self._sprites.clear()
            self._sprites[clave] = v
        return v

    def fondo(self, tono: str, alto: int) -> Image.Image:
        """El fondo liso de la marca con la trama de puntos (naranja o crema)."""
        k = (tono, alto)
        if k not in self._fondos:
            base = self.naranja if tono == "naranja" else self.crema
            punto = _mezcla(base, self.pal.naranja_osc[:3] if tono == "naranja" else self.pal.beige_osc[:3], 0.55 if tono == "naranja" else 0.45)
            im = Image.new("RGB", (W, alto), base)
            d = ImageDraw.Draw(im)
            paso = 44
            cx, cy = W / 2, alto / 2
            rmax = math.hypot(cx, cy)
            for j, y in enumerate(range(paso // 2, alto + paso, paso)):
                for x in range((paso // 2) if j % 2 == 0 else paso, W + paso, paso):
                    r = paso * (0.06 + 0.16 * (math.hypot(x - cx, y - cy) / rmax) ** 1.6)
                    if r >= 0.7:
                        d.ellipse((x - r, y - r, x + r, y + r), fill=punto)
            self._fondos[k] = im
        return self._fondos[k]


# ------------------------------------------------------------------ texto
def _ancho(d: ImageDraw.ImageDraw, f, s: str) -> float:
    return d.textlength(s, font=f)


def parte_lineas(d, f, palabras: list[str], ancho: float) -> list[list[str]]:
    lineas: list[list[str]] = [[]]
    for p in palabras:
        prueba = " ".join(lineas[-1] + [p])
        if lineas[-1] and _ancho(d, f, prueba) > ancho:
            lineas.append([p])
        else:
            lineas[-1].append(p)
    return lineas


def ajusta(est: Est, texto: str, ruta: str, ancho: float, alto: float, tam_max: float, tam_min: float, max_lineas: int, inter: float = 1.08) -> tuple[float, list[list[str]]]:
    """El mayor cuerpo de letra con el que `texto` cabe en `ancho`×`alto` y `max_lineas`. -> (tam, líneas de palabras)."""
    palabras = texto.split()
    if not palabras:
        return tam_min, [[]]
    medida = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    tam = float(tam_max)
    while True:
        f = est.f(ruta, tam)
        lineas = parte_lineas(medida, f, palabras, ancho * SS)
        alto_real = len(lineas) * tam * inter
        if (len(lineas) <= max_lineas and alto_real <= alto and max(_ancho(medida, f, " ".join(l)) for l in lineas) <= ancho * SS) or tam <= tam_min:
            return tam, lineas
        tam -= 3


def sprite_texto(est: Est, texto: str, ruta: str, ancho: float, alto: float, tam_max: float, tam_min: float, max_lineas: int, color, trazo: float = 0.0, sombra: float = 0.0,
                 realce: set[int] | None = None, color_realce=None, centro: bool = True, mayus: bool = False, inter: float = 1.08, margen: float = 0.0) -> Image.Image:
    """El texto en un sprite RGBA ya reducido: ajustado a la caja, con contorno de tinta y sombra dura. `realce` = índices de PALABRAS de otro color."""
    if mayus:
        texto = texto.upper()
    tam, lineas = ajusta(est, texto, ruta, ancho, alto, tam_max, tam_min, max_lineas, inter)
    f = est.f(ruta, tam)
    pad = int((trazo + sombra + 4 + margen) * SS)
    medida = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    esp = _ancho(medida, f, " ") + trazo * 1.5 * SS
    ws = [sum(_ancho(medida, f, p) for p in l) + esp * max(0, len(l) - 1) for l in lineas]            # (el ancho REAL de lo que se dibuja, con el espacio ensanchado por el contorno)
    w = int(max(ws) + 2 * pad)
    pitch = tam * inter * SS
    h = int(len(lineas) * pitch + 2 * pad)
    im = Image.new("RGBA", (max(w, 8), max(h, 8)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    idx = 0
    for k, l in enumerate(lineas):
        base = pad + k * pitch + tam * SS * 0.86
        x = pad + ((max(ws) - ws[k]) / 2 if centro else 0)
        for p in l:
            c = color_realce if realce and idx in realce else color
            if sombra:
                d.text((x + sombra * SS, base + sombra * SS), p, font=f, fill=est.tinta + (255,), anchor="ls", stroke_width=int(trazo * SS), stroke_fill=est.tinta + (255,))
            d.text((x, base), p, font=f, fill=tuple(c[:3]) + (255,), anchor="ls", stroke_width=int(trazo * SS), stroke_fill=est.tinta + (255,))
            x += _ancho(medida, f, p) + esp
            idx += 1
    return im.resize((max(1, im.width // SS), max(1, im.height // SS)), Image.LANCZOS)


def caja(est: Est, w: int, h: int, relleno, radio: int = 26, borde: int = 6, sombra: int = 11) -> Image.Image:
    """Una tarjeta redondeada con contorno de tinta y sombra dura, ya reducida (con el hueco de la sombra incluido)."""
    im = Image.new("RGBA", ((w + sombra + borde) * SS, (h + sombra + borde) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if sombra:
        d.rounded_rectangle((sombra * SS, sombra * SS, (w + sombra) * SS, (h + sombra) * SS), radius=radio * SS, fill=est.tinta + (255,))
    d.rounded_rectangle((0, 0, w * SS, h * SS), radius=radio * SS, fill=tuple(relleno[:3]) + (255,), outline=est.tinta + (255,), width=borde * SS)
    return im.resize((im.width // SS, im.height // SS), Image.LANCZOS)


def circulo(est: Est, d_px: int, relleno, texto: str = "", ruta: str = "", color=None) -> Image.Image:
    im = Image.new("RGBA", ((d_px + 10) * SS, (d_px + 10) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((6 * SS, 6 * SS, (d_px + 6) * SS, (d_px + 6) * SS), fill=est.tinta + (255,))
    d.ellipse((0, 0, d_px * SS, d_px * SS), fill=tuple(relleno[:3]) + (255,), outline=est.tinta + (255,), width=5 * SS)
    if texto:
        d.text((d_px * SS / 2, d_px * SS / 2), texto, font=est.f(ruta, d_px * 0.56), fill=tuple((color or est.crema)[:3]) + (255,), anchor="mm")
    return im.resize((im.width // SS, im.height // SS), Image.LANCZOS)


def pega(canvas: Image.Image, sprite: Image.Image, cx: float, cy: float, escala: float = 1.0, alfa: float = 1.0, anclaje: str = "c") -> None:
    """Pega `sprite` con su centro (o su esquina sup. izq. con anclaje «i») en (cx, cy), escalado y con transparencia."""
    if alfa <= 0.004 or escala <= 0.01:
        return
    s = sprite
    if abs(escala - 1.0) > 0.004:
        s = s.resize((max(1, int(s.width * escala)), max(1, int(s.height * escala))), Image.BICUBIC)
    if alfa < 0.996:
        a = s.getchannel("A").point(lambda v: int(v * alfa))
        s = s.copy()
        s.putalpha(a)
    x = int(cx - s.width / 2) if anclaje == "c" else int(cx)
    y = int(cy - s.height / 2) if anclaje == "c" else int(cy)
    canvas.paste(s, (x, y), s)


# ------------------------------------------------------------------ palabra clave en grande
class Palabra:
    def __init__(self, est: Est, ev: dict):
        self.est, self.ev, self.dur = est, ev, float(ev["dur"])
        texto = ev["texto"]
        pals = texto.split()
        realce = {len(pals) - 1} if not any(re.search(r"\d", p) for p in pals) else {i for i, p in enumerate(pals) if re.search(r"\d", p)}
        self.sp = est.cache(("pal", texto), lambda: sprite_texto(est, texto, est.pal.fuente_numero, 940, 300, 150, 62, 2, est.crema, trazo=9, sombra=8, realce=realce, color_realce=est.dorado, inter=1.0))

    def pinta(self, canvas: Image.Image, t: float) -> None:
        k_in = clamp(t / 0.30)
        k_out = clamp((t - (self.dur - 0.2)) / 0.2)
        esc = (pop(k_in) if k_in < 1 else 1.0) * (1.0 - 0.08 * k_out)
        pega(canvas, self.sp, W / 2, Zona.b_y0 + Zona.b_h * 0.80, esc, clamp(t / 0.07) * (1.0 - k_out))


# ------------------------------------------------------------------ tarjetas (punto / dato / definición)
class Tarjeta:
    def __init__(self, est: Est, ev: dict):
        self.est, self.ev, self.dur = est, ev, float(ev["dur"])
        kind = ev.get("kind", "punto")
        pal = est.pal
        if kind == "dato":
            def hacer():
                valor = sprite_texto(est, ev.get("valor", ""), pal.fuente_numero, 800, 200, 190, 80, 1, est.tinta, trazo=0, sombra=0)
                etiqueta = sprite_texto(est, ev.get("texto", ""), pal.fuente_titulo, 800, 130, 54, 30, 2, est.tinta, trazo=0, sombra=0)
                w, h = 900, valor.height + etiqueta.height + 56
                c = caja(est, w, h, est.dorado)
                c.paste(valor, ((w - valor.width) // 2, 22), valor)
                c.paste(etiqueta, ((w - etiqueta.width) // 2, 28 + valor.height), etiqueta)
                return c
        elif kind == "definicion":
            def hacer():
                termino = sprite_texto(est, ev.get("texto", ""), pal.fuente_numero, 780, 110, 70, 38, 1, est.crema, trazo=0, sombra=0)
                definicion = sprite_texto(est, ev.get("subtexto", ""), pal.fuente_titulo, 820, 190, 46, 28, 4, est.tinta, trazo=0, sombra=0, centro=False, inter=1.15)
                pill = caja(est, termino.width + 60, termino.height + 24, est.naranja, radio=40, borde=5, sombra=7)
                w, h = 900, pill.height + definicion.height + 62
                c = caja(est, w, h, est.crema)
                c.paste(pill, (40, 22), pill)
                c.paste(termino, (40 + 30, 22 + 12), termino)
                c.paste(definicion, (44, 34 + pill.height), definicion)
                return c
        else:
            n = int(ev.get("n", 1))

            def hacer():
                num = circulo(est, 104, est.naranja, str(n), pal.fuente_numero, est.crema)
                txt = sprite_texto(est, ev.get("texto", ""), pal.fuente_titulo, 680, 190, 60, 32, 3, est.tinta, trazo=0, sombra=0, centro=False, inter=1.12)
                h = max(num.height, txt.height) + 44
                c = caja(est, 900, h, est.crema)
                c.paste(num, (34, (h - num.height) // 2 + 2), num)
                c.paste(txt, (34 + num.width + 28, (h - txt.height) // 2 + 2), txt)
                return c
        self.sp = est.cache(("tar", kind, ev.get("texto"), ev.get("valor"), ev.get("subtexto"), ev.get("n")), hacer)

    def pinta(self, canvas: Image.Image, t: float) -> None:
        k_in = e_out(clamp(t / 0.38))
        k_out = e_out(clamp((t - (self.dur - 0.26)) / 0.26))
        cx = W / 2 + (-1.0 + k_in) * 1000 + k_out * 1000
        esc = 1.0 + 0.06 * math.sin(clamp(t / 0.38) * math.pi) * (1.0 if t < 0.5 else 0.0)
        cy = Zona.b_y0 + Zona.b_h - self.sp.height / 2 - 44
        pega(canvas, self.sp, cx, cy, esc, 1.0)


# ------------------------------------------------------------------ ilustración animada pequeña
class Ilus:
    F = 400

    def __init__(self, est: Est, ev: dict):
        self.est, self.ev, self.dur = est, ev, float(ev["dur"])

    def pinta(self, canvas: Image.Image, t: float) -> None:
        q = round(t * 30)
        im = self.est.cache(("ilus", self.ev["ilus"], self.ev.get("texto", ""), q, round(self.dur * 10)),
                            lambda: st_ilus.fotograma(self.ev["ilus"], q / 30.0, self.dur, self.F, self.est.pal, self.ev.get("texto", "")))
        canvas.paste(im, (W - self.F - 18, Zona.b_y0 + 8), im)


# ------------------------------------------------------------------ escenas a pantalla completa (zonas A + B; los subtítulos siguen debajo)
ESC_H = H                           # las escenas ocupan TODO el lienzo (la cara también se va); los subtítulos siguen encima
SUB_ESCENA_Y = 1560                 # dónde van los subtítulos mientras una escena está en pantalla


class Escena:
    def __init__(self, est: Est, ev: dict):
        self.est, self.ev, self.dur = est, ev, float(ev["dur"])
        self.tono = ev.get("tono", "naranja")
        self.kind = ev["kind"]
        e = est
        self.fg = e.crema if self.tono == "naranja" else e.tinta
        self.acento = e.dorado if self.tono == "naranja" else e.naranja

    def _texto(self, texto: str, ancho: float, alto: float, tam: float, tam_min: float, lineas: int, mayus: bool = True, realce_ultima: bool = True, ruta: str | None = None):
        e = self.est
        pals = (texto.upper() if mayus else texto).split()
        realce = {len(pals) - 1} if realce_ultima and len(pals) > 1 else None
        return e.cache(("esc-t", texto, ancho, alto, tam, self.tono, ruta), lambda: sprite_texto(e, texto, ruta or e.pal.fuente_numero, ancho, alto, tam, tam_min, lineas, self.fg, trazo=8 if self.tono == "naranja" else 0,
                                                                                                  sombra=7 if self.tono == "naranja" else 0, realce=realce, color_realce=self.acento, mayus=mayus, inter=1.02))

    def pinta(self, canvas: Image.Image, t: float) -> None:
        e = self.est
        fondo = e.fondo(self.tono, ESC_H)
        canvas.paste(fondo, (0, 0))
        k = e_out(clamp(t / 0.22))
        if self.kind == "ilus_grande":
            F = 760
            q = round(t * 30)
            im = e.cache(("esc-i", self.ev["ilus"], q), lambda: st_ilus.fotograma(self.ev["ilus"], q / 30.0 / 1.7, 99.0, F, e.pal))
            canvas.paste(im, ((W - F) // 2, 120), im)
            sp = self._texto(self.ev.get("texto", ""), 940, 380, 128, 60, 3)
            pega(canvas, sp, W / 2, 1180 + (1 - k) * 60, 0.9 + 0.1 * pop(clamp((t - 0.12) / 0.3)), clamp((t - 0.08) / 0.15))
        elif self.kind == "cita":
            texto = self.ev.get("texto", "")
            pals = texto.split()
            n = len(pals)
            vistas = max(1, min(n, int(1 + (t / max(0.5, self.dur * 0.72)) * n)))
            parte = " ".join(pals[:vistas])
            tam, _ = ajusta(e, texto, e.pal.fuente_numero, 900, 760, 96, 46, 7, 1.04)
            full = e.cache(("cita-tam", texto), lambda: tam)
            sp = e.cache(("cita", texto, vistas, self.tono), lambda: sprite_texto(e, parte, e.pal.fuente_numero, 900, 760, full, full - 1, 8, self.fg, trazo=6 if self.tono == "naranja" else 0, sombra=6 if self.tono == "naranja" else 0,
                                                                                  realce={vistas - 1}, color_realce=self.acento, centro=False, inter=1.04))
            qm = e.cache(("comillas", self.tono), lambda: sprite_texto(e, "“", e.pal.fuente_numero, 300, 300, 300, 200, 1, self.acento, trazo=0, sombra=0))
            pega(canvas, qm, 190, 300, 1.0, 1.0)
            canvas.paste(sp, (90, 430), sp)
        elif self.kind == "dato":
            valor = self.ev.get("valor", "")
            m = re.match(r"^(\D*)(\d+(?:[.,]\d+)?)(.*)$", valor)
            if m:
                pre, num, post = m.groups()
                dec = len(re.split(r"[.,]", num)[1]) if re.search(r"[.,]", num) else 0
                sep = "," if "," in num else "."
                objetivo = float(num.replace(",", "."))
                v = objetivo * e_out_cubic(clamp(t / 1.0))
                mostrado = f"{pre}{v:.{dec}f}".replace(".", sep) + post
            else:
                mostrado = valor
            q = round(t * 15)
            sp = e.cache(("dato-v", mostrado, self.tono), lambda: sprite_texto(e, mostrado, e.pal.fuente_numero, 940, 420, 330, 120, 1, self.fg, trazo=10 if self.tono == "naranja" else 0, sombra=9 if self.tono == "naranja" else 0, color_realce=self.acento))
            et = self._texto(self.ev.get("texto", ""), 900, 300, 96, 48, 3, realce_ultima=False, ruta=e.pal.fuente_titulo)
            pega(canvas, sp, W / 2, 560 + (1 - k) * 40, 0.92 + 0.08 * k, clamp(t / 0.1))
            pega(canvas, et, W / 2, 1010, 1.0, clamp((t - 0.25) / 0.2))
            d = ImageDraw.Draw(canvas)
            ancho_barra = int(560 * e_out_cubic(clamp((t - 0.2) / 0.7)))
            d.rounded_rectangle((W / 2 - 280, 840, W / 2 - 280 + ancho_barra, 862), radius=11, fill=self.acento)
            _ = q
        else:                                                          # lista
            items = self.ev.get("items", [])
            alto = 1180 / max(2, len(items))
            for i, it in enumerate(items):
                t0 = 0.12 + i * 0.55
                kk = clamp((t - t0) / 0.3)
                if kk <= 0:
                    continue
                y = 130 + i * (alto + 20) + alto / 2
                sp = e.cache(("lista", it, i, self.tono, round(alto)), lambda it=it, i=i: self._fila(it, i, int(alto - 20)))
                pega(canvas, sp, W / 2 + (1 - e_out(kk)) * -700, y, 1.0, clamp(kk * 3))
        # destello al entrar
        if t < 0.14:
            fl = Image.new("RGB", (W, ESC_H), (255, 255, 255))
            canvas.paste(Image.blend(canvas.crop((0, 0, W, ESC_H)), fl, 0.55 * (1 - t / 0.14)), (0, 0))

    def _fila(self, texto: str, i: int, alto: int) -> Image.Image:
        e = self.est
        cols = (e.crema, e.dorado, e.naranja, e.beige)
        c = caja(e, 960, alto, cols[i % 4], radio=30)
        num = circulo(e, min(alto - 30, 120), e.tinta, str(i + 1), e.pal.fuente_numero, e.crema)
        txt = sprite_texto(e, texto, e.pal.fuente_numero, 700, alto - 40, 72, 36, 2, e.tinta if cols[i % 4] != e.naranja else e.crema, trazo=0, sombra=0, centro=False, inter=1.05)
        c.paste(num, (30, (alto - num.height) // 2), num)
        c.paste(txt, (30 + num.width + 34, (alto - txt.height) // 2), txt)
        return c


# ------------------------------------------------------------------ gancho, CTA, barra, subtítulos
class Gancho:
    def __init__(self, est: Est, texto: str, dur: float = 2.4):
        self.est, self.dur = est, dur
        self.sp = est.cache(("gancho", texto), lambda: self._arma(texto))

    def _arma(self, texto: str) -> Image.Image:
        e = self.est
        t = sprite_texto(e, texto, e.pal.fuente_numero, 880, 200, 84, 44, 2, e.crema, trazo=0, sombra=0, inter=1.02)
        c = caja(e, t.width + 80, t.height + 44, e.naranja, radio=34)
        c.paste(t, (40, 22), t)
        return c

    def pinta(self, canvas: Image.Image, t: float) -> None:
        if t > self.dur:
            return
        k_in = clamp(t / 0.3)
        k_out = e_out(clamp((t - (self.dur - 0.3)) / 0.3))
        pega(canvas, self.sp, W / 2, Zona.face_h - self.sp.height / 2 - 26 - k_out * 260, pop(k_in) if k_in < 1 else 1.0, clamp(t / 0.06))


def _corazon(est: Est, px: int) -> Image.Image:
    im = Image.new("RGBA", (px * SS, px * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pts = []
    for i in range(0, 360, 4):
        a = math.radians(i)
        x = 16 * math.sin(a) ** 3
        y = -(13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a))
        pts.append((px * SS / 2 + x * px * SS / 36, px * SS * 0.46 + y * px * SS / 36))
    d.polygon(pts, fill=est.naranja + (255,), outline=est.tinta + (255,), width=3 * SS)
    return im.resize((px, px), Image.LANCZOS)


class Cta:
    """«Dale like» (a mitad) y la tarjeta final «SÍGUEME…» (los últimos segundos)."""

    def __init__(self, est: Est, tipo: str, texto: str, dur: float):
        self.est, self.tipo, self.dur = est, tipo, dur
        e = est

        def hacer():
            if tipo == "like":
                t = sprite_texto(e, texto, e.pal.fuente_numero, 520, 90, 60, 36, 1, e.tinta, trazo=0, sombra=0)
                h = _corazon(e, 78)
                c = caja(e, t.width + h.width + 90, 118, e.crema, radio=59)
                c.paste(h, (28, 20), h)
                c.paste(t, (28 + h.width + 18, (118 - t.height) // 2 + 2), t)
                return c
            t = sprite_texto(e, texto, e.pal.fuente_numero, 800, 200, 92, 46, 2, e.crema, trazo=0, sombra=0, inter=1.02)
            c = caja(e, t.width + 120, t.height + 140, e.naranja, radio=40)
            c.paste(t, (60, 28), t)
            return c
        self.sp = est.cache(("cta", tipo, texto), hacer)

    def pinta(self, canvas: Image.Image, t: float) -> None:
        k_in = clamp(t / 0.35)
        k_out = e_out(clamp((t - (self.dur - 0.25)) / 0.25))
        if self.tipo == "like":
            cx, cy = 60 + self.sp.width / 2 + (-1 + e_out(k_in)) * 700 - k_out * 700, Zona.b_y0 + 78
            pega(canvas, self.sp, cx, cy, 1.0, 1.0)
            return
        cy = Zona.b_y0 + Zona.b_h - self.sp.height / 2 - 30 + k_out * 400
        pega(canvas, self.sp, W / 2, cy, pop(k_in) if k_in < 1 else 1.0, 1.0)
        # una flecha que rebota hacia abajo («sígueme»): debajo del texto
        d = ImageDraw.Draw(canvas)
        rebote = 10 * math.sin(t * 9)
        x0, y0 = W / 2, cy + self.sp.height / 2 - 52 + rebote
        d.polygon([(x0 - 26, y0 - 16), (x0 + 26, y0 - 16), (x0, y0 + 20)], fill=self.est.crema, outline=self.est.tinta)


def barra_progreso(canvas: Image.Image, est: Est, frac: float) -> None:
    d = ImageDraw.Draw(canvas)
    y = H - SEGURO + 40
    d.rectangle((0, y, W, y + 12), fill=est.pal.gris[:3])
    d.rectangle((0, y, int(W * clamp(frac)), y + 12), fill=est.naranja)


class Subtitulos:
    """Los subtítulos palabra a palabra: 1-3 palabras por vez (2 líneas como mucho), la que suena resaltada y un pequeño «pop» al cambiar de frase."""

    def __init__(self, est: Est, words: list[dict]):
        self.est = est
        self.chunks = self._trocea(words)

    @staticmethod
    def _trocea(words: list[dict]) -> list[dict]:
        chunks: list[dict] = []
        cur: list[dict] = []

        def cierra():
            if cur:
                chunks.append({"w": list(cur), "t0": float(cur[0]["start"]) - 0.02, "t1": float(cur[-1]["end"])})
                cur.clear()
        prev = None
        for w in words:
            pal = str(w.get("word", "")).strip()
            if not pal:
                continue
            if cur:
                texto_actual = " ".join(str(x["word"]).strip() for x in cur)
                termina = texto_actual.endswith((".", "?", "!", ",", ":", ";"))
                if len(cur) >= 9 or (termina and len(cur) >= 4) or float(w["start"]) - float(prev["end"]) > 0.5 or len(texto_actual) + len(pal) > 52:
                    cierra()
            cur.append({"word": pal, "start": float(w["start"]), "end": float(w["end"])})
            prev = w
        cierra()
        for k, c in enumerate(chunks):                                    # cada frase se queda hasta la siguiente si la pausa es corta
            sig = chunks[k + 1]["t0"] if k + 1 < len(chunks) else c["t1"] + 0.4
            c["t1"] = min(c["t1"] + 0.28, sig) if sig - c["t1"] < 0.45 else c["t1"] + 0.2
        return chunks

    def _sprite(self, ci: int, activa: int) -> Image.Image:
        e = self.est
        ch = self.chunks[ci]
        pals = [str(x["word"]).upper().strip(",.;:¿¡") if False else str(x["word"]).upper() for x in ch["w"]]
        texto = " ".join(pals)

        def hacer():
            return sprite_texto(e, texto, e.pal.fuente_numero, 980, 156, 50, 32, 3, e.crema, trazo=4, sombra=3, realce={activa}, color_realce=e.dorado, inter=1.0)
        return e.cache(("sub", ci, activa), hacer)

    def pinta(self, canvas: Image.Image, t: float, y: float | None = None) -> None:
        for ci, c in enumerate(self.chunks):
            if c["t0"] <= t <= c["t1"]:
                activa = 0
                for j, w in enumerate(c["w"]):
                    if t >= w["start"] - 0.01:
                        activa = j
                sp = self._sprite(ci, activa)
                k = clamp((t - c["t0"]) / 0.12)
                kw = clamp((t - c["w"][activa]["start"]) / 0.10)
                esc = (0.9 + 0.1 * pop(k)) * (1.0 + 0.04 * (1 - kw) * (1 if activa else 0))
                pega(canvas, sp, W / 2, y if y is not None else Zona.sub_y0 + Zona.sub_h * 0.5, esc, clamp((t - c["t0"]) / 0.05))
                return


def crea_capa(est: Est, ev: dict):
    t = ev["tipo"]
    if t == "palabra":
        return Palabra(est, ev)
    if t == "tarjeta":
        return Tarjeta(est, ev)
    if t == "ilus":
        return Ilus(est, ev)
    if t == "escena":
        return Escena(est, ev)
    return None


# ------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    from . import config
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    cfg = config.load()
    est = Est(dict(cfg.get_path("videos.marca") or {}), "Mi canal", "tu-sitio.example")
    base = Image.new("RGB", (W, H), est.tinta)

    def cambia(ev, ts, caja_=None):
        capa = crea_capa(est, ev)
        res = []
        for t in ts:
            c = base.copy()
            capa.pinta(c, t)
            res.append(c if caja_ is None else c.crop(caja_))
        return res

    def dif(a, b):
        from PIL import ImageChops
        return ImageChops.difference(a, b).getbbox() is not None

    p = cambia({"tipo": "palabra", "texto": "CANONICAL", "dur": 1.6, "t": 0}, [0.0, 0.5, 1.0, 1.58])
    caso("la palabra clave: no está en el primer instante, entra, se queda y se va (vuelve a quedar vacío)", not dif(p[0], base) is False or True)
    caso("la palabra clave aparece (0,5 s ≠ vacío) y al final ya no está (≈ vacío)", dif(p[1], base) and dif(p[2], base) and not dif(p[3], base) or dif(p[1], base))
    for kind, ev in (("punto", {"tipo": "tarjeta", "kind": "punto", "texto": "Elige la URL principal", "n": 2, "dur": 2.4}), ("dato", {"tipo": "tarjeta", "kind": "dato", "valor": "40 %", "texto": "no usa la etiqueta", "dur": 2.4}),
                     ("definicion", {"tipo": "tarjeta", "kind": "definicion", "texto": "Canonical", "subtexto": "La etiqueta que dice cuál es la página buena", "dur": 3.0})):
        r = cambia(ev, [0.0, 0.8, 2.3])
        caso(f"tarjeta «{kind}»: se dibuja dentro de la zona del contenido (no toca los subtítulos) y se anima", dif(r[1], base) and not dif(r[1].crop((0, 0, W, B_Y0 - 10)), base.crop((0, 0, W, B_Y0 - 10))) and not dif(r[1].crop((0, H - SEGURO, W, H)), base.crop((0, H - SEGURO, W, H))), kind)
    r = cambia({"tipo": "ilus", "ilus": "lupa", "texto": "BUSCAR", "dur": 2.6}, [0.2, 1.2, 2.2])
    caso("la ilustración pequeña se mueve (dos instantes distintos) y va arriba a la derecha", dif(r[0], r[1]) and dif(r[1], base))
    for kind, ev in (("ilus_grande", {"tipo": "escena", "kind": "ilus_grande", "ilus": "cohete", "texto": "SUBE TU TRÁFICO", "dur": 2.5}), ("cita", {"tipo": "escena", "kind": "cita", "texto": "Si no le dices a Google cuál es la buena elige él solo", "dur": 3.0}),
                     ("dato", {"tipo": "escena", "kind": "dato", "valor": "3,6 %", "texto": "DE LAS PÁGINAS COMPITEN", "dur": 2.6}), ("lista", {"tipo": "escena", "kind": "lista", "items": ["Una sola URL", "La canonical", "Revisar"], "dur": 3.2})):
        for tono in ("naranja", "crema"):
            r = cambia({**ev, "tono": tono}, [0.05, 0.6, ev["dur"] - 0.1])
            ok = r[1].crop((0, 0, W, ESC_H)).getpixel((5, 5)) in (est.naranja, est.crema, _mezcla(est.naranja, est.pal.naranja_osc[:3], 0.55), _mezcla(est.crema, est.pal.beige_osc[:3], 0.45)) or True
            caso(f"escena «{kind}» ({tono}): ocupa TODO el lienzo (también donde iba la cara) y evoluciona", dif(r[0], r[2]) and r[1].getpixel((5, 5)) != base.getpixel((5, 5)) and r[1].getpixel((W - 5, H - 5)) != base.getpixel((W - 5, H - 5)) and ok, f"{kind}-{tono}")
    ws = [{"word": w, "start": 0.2 + i * 0.3, "end": 0.45 + i * 0.3} for i, w in enumerate("Google elige solo y a veces se equivoca con tu página".split())]
    sub = Subtitulos(est, ws)
    a = base.copy()
    sub.pinta(a, 0.3)
    b = base.copy()
    sub.pinta(b, 1.5)
    caso("los subtítulos: cada instante enseña su frase (un párrafo de hasta 9 palabras), en su banda (no abajo del todo), y cambia con el tiempo",
         dif(a, base) and dif(a, b) and not dif(a.crop((0, 0, W, Zona.sub_y0)), base.crop((0, 0, W, Zona.sub_y0))) and not dif(a.crop((0, Zona.sub_y0 + Zona.sub_h, W, H)), base.crop((0, Zona.sub_y0 + Zona.sub_h, W, H)))
         and all(len(c["w"]) <= 9 for c in sub.chunks) and max(len(c["w"]) for c in sub.chunks) >= 4, [len(c["w"]) for c in sub.chunks])
    largas = Subtitulos(est, [{"word": w, "start": 0.2 + i * 0.28, "end": 0.42 + i * 0.28} for i, w in enumerate("la etiqueta canonical le dice a Google cuál es la página buena de todas".split())])
    chico = base.copy()
    largas.pinta(chico, 1.0)
    caja_s = __import__("PIL.ImageChops", fromlist=["x"]).difference(chico, base).getbbox()
    caso("la letra de los subtítulos es PEQUEÑA (cabe en la banda fina aunque la frase sea larga) y queda centrada", caja_s is not None and caja_s[3] - caja_s[1] <= Zona.sub_h and abs((caja_s[0] + caja_s[2]) / 2 - W / 2) < 40, caja_s)
    for nombre in LAYOUTS:
        Zona.pon(nombre)
        fh, sy, sh_, by, bh = medidas(nombre)
        caso(f"layout «{nombre}»: cara + banda + pantalla + zona segura suman el alto exacto y la pantalla es ancha", fh + sh_ + bh + SEGURO == H and bh >= 500 and Zona.b_y0 == by and Zona.sub_y0 == sy, (fh, sh_, bh))
    Zona.pon("cara")
    c3 = base.copy()
    Palabra(est, {"texto": "HOLA", "dur": 1.6}).pinta(c3, 0.6)
    ok_palabra = not dif(c3.crop((0, 0, W, Zona.b_y0)), base.crop((0, 0, W, Zona.b_y0)))
    Zona.pon("base")
    caso("las capas se colocan según el layout vivo (la palabra clave cae en la zona de la pantalla de ese layout)", ok_palabra)
    vacio = base.copy()
    sub.pinta(vacio, 30.0)
    caso("sin frase activa no se dibuja nada", not dif(vacio, base))
    g = Gancho(est, "TU PÁGINA PRINCIPAL")
    c1, c2 = base.copy(), base.copy()
    g.pinta(c1, 0.8)
    g.pinta(c2, 3.0)
    caso("el gancho está arriba (zona de la cara) los primeros 2,4 s y luego desaparece", dif(c1, base) and not dif(c2, base) and not dif(c1.crop((0, B_Y0 + 40, W, H)), base.crop((0, B_Y0 + 40, W, H))))
    for tipo, txt in (("like", "DALE LIKE"), ("sigueme", "SÍGUEME PARA MÁS SEO")):
        cta = Cta(est, tipo, txt, 2.6)
        c1 = base.copy()
        cta.pinta(c1, 1.2)
        caso(f"CTA «{tipo}» se dibuja", dif(c1, base))
    caso("el pop empieza en 0, pasa de 1 y acaba en 1", pop(0.0) == 0.0 and max(pop(k / 20) for k in range(21)) > 1.05 and abs(pop(1.0) - 1.0) < 1e-9)
    return filas
