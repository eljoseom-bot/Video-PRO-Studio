# COPIA PROPIA para «Shorts · TikTok» (el autor, 16-oct-2026: «lo de Video Pro no lo toques, duplícalo»): si cambia ilus_motor.py de Vídeos PRO, esto no cambia.
"""Motor de dibujo y animación de las ILUSTRACIONES de Vídeos PRO (Pillow puro, sin dependencias).

Una ilustración es una escena animada (un símbolo con movimiento: lupa, gráfica que sube, cohete…)
que sale en pantalla mientras el presentador explica algo muy visual. Aquí solo está la caja de
herramientas; las escenas están en `ilus_escenas.py` y el ensamblado (fotogramas, caché,
selección) en `ilustraciones.py`.

Estilo = el de las tarjetas de la marca: contorno de tinta, colores planos exactos de
`videos.marca`, sombra dura sin difuminar — más un halo crema alrededor de la silueta ("pegatina")
para que se lea sobre cualquier fondo, sea la cara o una web a pantalla completa.

Convenciones:
  · Todo se dibuja en UNIDADES 0..1 del lado del lienzo (así una escena vale a cualquier tamaño).
  · El lienzo se dibuja con supersampling (SS×) y se reduce: bordes suaves sin dependencias.
  · Cada escena es una función pura de `t` (segundos): mismo t -> mismo fotograma. Eso hace las
    ilustraciones reproducibles (caché por contenido) y comprobables en los tests.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 2                      # supersampling: se dibuja a SS× y se reduce con un filtro de caja


# ------------------------------------------------------------------ easings / utilidades
def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return a if x < a else b if x > b else x


def lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k


def prog(t: float, t0: float, t1: float) -> float:
    """Progreso 0..1 de `t` entre t0 y t1 (0 antes, 1 después)."""
    if t1 <= t0:
        return 1.0 if t >= t0 else 0.0
    return clamp((t - t0) / (t1 - t0))


def e_out(k: float) -> float:
    return 1.0 - (1.0 - k) ** 3


def e_in(k: float) -> float:
    return k ** 3


def e_io(k: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * k)


def e_back(k: float, s: float = 1.70158) -> float:
    """Sale pasándose y vuelve (rebote de entrada)."""
    k -= 1.0
    return 1.0 + (s + 1.0) * k ** 3 + s * k ** 2


def e_elastic(k: float) -> float:
    if k <= 0.0:
        return 0.0
    if k >= 1.0:
        return 1.0
    return 2.0 ** (-10.0 * k) * math.sin((k * 10.0 - 0.75) * (2.0 * math.pi) / 3.0) + 1.0


def e_bounce(k: float) -> float:
    n1, d1 = 7.5625, 2.75
    if k < 1 / d1:
        return n1 * k * k
    if k < 2 / d1:
        k -= 1.5 / d1
        return n1 * k * k + 0.75
    if k < 2.5 / d1:
        k -= 2.25 / d1
        return n1 * k * k + 0.9375
    k -= 2.625 / d1
    return n1 * k * k + 0.984375


def onda(t: float, periodo: float, fase: float = 0.0) -> float:
    """Seno de -1..1 con `periodo` segundos."""
    return math.sin(2.0 * math.pi * (t / periodo + fase))


def pulso(t: float, periodo: float, fase: float = 0.0) -> float:
    """Latido suave de 0..1."""
    return 0.5 + 0.5 * onda(t, periodo, fase)


def ciclo(t: float, periodo: float) -> float:
    """Fracción 0..1 dentro de un bucle de `periodo` segundos."""
    return (t % periodo) / periodo


# ------------------------------------------------------------------ paleta
def _hex(h: str, a: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def _mezcla(c1, c2, k: float):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * k)) for i in range(3)) + (255,)


class Pal:
    """Los colores de `videos.marca` (+ tonos derivados) y las rutas de sus fuentes."""

    def __init__(self, marca: dict):
        self.tinta = _hex(marca["tinta"])
        self.crema = _hex(marca["crema"])
        self.naranja = _hex(marca["naranja"])
        self.dorado = _hex(marca["dorado"])
        self.beige = _hex(marca["beige"])
        self.gris = (61, 61, 61, 255)
        self.naranja_osc = _mezcla(self.naranja, self.tinta, 0.28)
        self.dorado_osc = _mezcla(self.dorado, self.tinta, 0.22)
        self.beige_osc = _mezcla(self.beige, self.tinta, 0.16)
        self.naranja_cl = _mezcla(self.naranja, self.crema, 0.55)
        self.dorado_cl = _mezcla(self.dorado, self.crema, 0.55)
        self.fuente_titulo = str(marca.get("fuente_titulo", ""))
        self.fuente_numero = str(marca.get("fuente_numero", ""))
        self.fuente_texto = str(marca.get("fuente_texto", ""))

    def clave(self) -> str:
        return "|".join(str(c) for c in (self.tinta, self.crema, self.naranja, self.dorado, self.beige,
                                         self.fuente_titulo, self.fuente_numero))


@lru_cache(maxsize=64)
def fuente(path: str, px: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        return ImageFont.load_default(size=px)


# ------------------------------------------------------------------ lienzo
class Lienzo:
    """Lienzo cuadrado en unidades 0..1. `n` = píxeles del lado (ya con el supersampling)."""

    def __init__(self, n: int, pal: Pal, borde: float = 0.017):
        self.n = int(n)
        self.pal = pal
        self.borde = borde
        self.im = Image.new("RGBA", (self.n, self.n), pal.tinta[:3] + (0,))
        self.d = ImageDraw.Draw(self.im)

    # -- conversión
    def _p(self, ps) -> list[tuple[float, float]]:
        n = self.n
        return [(x * n, y * n) for x, y in ps]

    def _w(self, v: float) -> int:
        return max(1, int(round(v * self.n)))

    # -- formas con contorno de tinta (el contorno sobresale `borde` hacia fuera de la forma nominal)
    def forma(self, ps, fill, borde: float | None = None) -> None:
        b = self.borde if borde is None else borde
        pts = self._p(ps)
        if b > 0:
            w = self._w(b * 2)
            self.d.line(pts + [pts[0]], fill=self.pal.tinta, width=w, joint="curve")
            r = w / 2.0
            for x, y in pts:
                self.d.ellipse((x - r, y - r, x + r, y + r), fill=self.pal.tinta)
        if fill is not None:
            self.d.polygon(pts, fill=fill)

    def circulo(self, cx: float, cy: float, r: float, fill, borde: float | None = None) -> None:
        self.elipse(cx, cy, r, r, fill, borde)

    def elipse(self, cx: float, cy: float, rx: float, ry: float, fill, borde: float | None = None) -> None:
        b = self.borde if borde is None else borde
        n = self.n
        if b > 0:
            self.d.ellipse(((cx - rx - b) * n, (cy - ry - b) * n, (cx + rx + b) * n, (cy + ry + b) * n),
                           fill=self.pal.tinta)
        if fill is not None:
            self.d.ellipse(((cx - rx) * n, (cy - ry) * n, (cx + rx) * n, (cy + ry) * n), fill=fill)

    def rect(self, x0: float, y0: float, x1: float, y1: float, fill, radio: float = 0.03,
             borde: float | None = None) -> None:
        b = self.borde if borde is None else borde
        n = self.n
        if x1 <= x0 or y1 <= y0:
            return
        if b > 0:
            self.d.rounded_rectangle(((x0 - b) * n, (y0 - b) * n, (x1 + b) * n, (y1 + b) * n),
                                     radius=(radio + b) * n, fill=self.pal.tinta)
        if fill is not None:
            self.d.rounded_rectangle((x0 * n, y0 * n, x1 * n, y1 * n), radius=radio * n, fill=fill)

    def trazo(self, ps, color, ancho: float = 0.04, borde: float | None = None) -> None:
        """Línea gruesa con puntas redondas y contorno de tinta."""
        b = self.borde if borde is None else borde
        pts = self._p(ps)
        if len(pts) < 2:
            return
        capas = ([(self.pal.tinta, ancho + 2 * b)] if b > 0 else []) + [(color, ancho)]
        for col, w in capas:
            wp = self._w(w)
            self.d.line(pts, fill=col, width=wp, joint="curve")
            r = wp / 2.0
            for x, y in (pts[0], pts[-1]):
                self.d.ellipse((x - r, y - r, x + r, y + r), fill=col)

    def arco(self, cx: float, cy: float, r: float, a0: float, a1: float, color, ancho: float = 0.04,
             borde: float | None = None) -> None:
        """Arco grueso (grados; 0 = derecha, sentido horario) con contorno de tinta y puntas redondas."""
        b = self.borde if borde is None else borde
        n = self.n
        capas = ([(self.pal.tinta, ancho + 2 * b)] if b > 0 else []) + [(color, ancho)]
        for col, w in capas:
            wp = self._w(w)
            rb = r + w / 2.0                       # PIL dibuja el trazo hacia dentro de la caja
            bbox = ((cx - rb) * n, (cy - rb) * n, (cx + rb) * n, (cy + rb) * n)
            self.d.arc(bbox, a0, a1, fill=col, width=wp)
            rr = wp / 2.0
            for ang in (a0, a1):
                x = (cx + r * math.cos(math.radians(ang))) * n
                y = (cy + r * math.sin(math.radians(ang))) * n
                self.d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col)

    def texto(self, x: float, y: float, s: str, tam: float, color, negrita: str = "numero",
              ancla: str = "mm") -> None:
        ruta = self.pal.fuente_numero if negrita == "numero" else self.pal.fuente_titulo
        f = fuente(ruta, max(6, int(tam * self.n)))
        self.d.text((x * self.n, y * self.n), s, font=f, fill=color, anchor=ancla)

    def vidrio(self, cx: float, cy: float, r: float, color, alpha: float) -> None:
        """Círculo translúcido (cristal de una lupa, resplandor): se mezcla con lo que hay debajo."""
        n = self.n
        x0, y0 = max(0, int((cx - r) * n) - 1), max(0, int((cy - r) * n) - 1)
        x1, y1 = min(n, int((cx + r) * n) + 2), min(n, int((cy + r) * n) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        cap = Image.new("RGBA", (x1 - x0, y1 - y0), color[:3] + (0,))
        ImageDraw.Draw(cap).ellipse(((cx - r) * n - x0, (cy - r) * n - y0, (cx + r) * n - x0, (cy + r) * n - y0),
                                    fill=color[:3] + (int(255 * alpha),))
        self.im.alpha_composite(cap, (x0, y0))

    # -- figuras de uso común
    def estrella(self, cx: float, cy: float, r: float, ri: float | None, puntas: int, rot: float,
                 fill, borde: float | None = None) -> None:
        ri = r * 0.45 if ri is None else ri
        ps = []
        for i in range(puntas * 2):
            ang = math.radians(rot + i * 180.0 / puntas - 90.0)
            rr = r if i % 2 == 0 else ri
            ps.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
        self.forma(ps, fill, borde)

    def chispa(self, cx: float, cy: float, r: float, rot: float = 0.0, fill=None, borde: float = 0.008) -> None:
        """Destello de 4 puntas (concavo)."""
        if r <= 0.002:
            return
        self.estrella(cx, cy, r, r * 0.28, 4, rot, fill or self.pal.dorado, borde)

    def engranaje(self, cx: float, cy: float, r: float, dientes: int, rot: float, fill,
                  agujero: float = 0.0, borde: float | None = None) -> None:
        ps = []
        paso = 360.0 / dientes
        for i in range(dientes):
            base = rot + i * paso
            for ang, rr in ((-0.30, r * 0.80), (-0.19, r), (0.19, r), (0.30, r * 0.80)):
                a = math.radians(base + ang * paso)
                ps.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        self.forma(ps, fill, borde)
        if agujero > 0:
            self.circulo(cx, cy, agujero, self.pal.crema, borde)

    # -- sprite: dibuja algo en un lienzo propio y lo pega con escala/rotación/opacidad (subpíxel)
    def sprite(self, tam: float, fn, cx: float, cy: float, rot: float = 0.0, esc: float = 1.0,
               sx: float = 1.0, sy: float = 1.0, alpha: float = 1.0) -> None:
        """`fn(l)` dibuja en un lienzo local de lado `tam` (unidades de este lienzo). Se pega centrado
        en (cx, cy), rotado `rot` grados (horario), escalado `esc*sx` × `esc*sy`."""
        if esc <= 0.001 or alpha <= 0.001:
            return
        m = max(4, int(round(tam * self.n)))
        loc = Lienzo(m, self.pal, self.borde * (self.n / m))     # mismo grosor de contorno en píxeles
        fn(loc)
        img = transforma(loc.im, esc, rot, 0.0, 0.0, alpha, sx, sy, bbox=True,
                         destino=(self.n, self.n), centro_dest=(cx * self.n, cy * self.n))
        if img is not None:
            self.im.alpha_composite(img[0], img[1])


def transforma(im: Image.Image, esc: float = 1.0, rot: float = 0.0, dx: float = 0.0, dy: float = 0.0,
               alpha: float = 1.0, sx: float = 1.0, sy: float = 1.0, bbox: bool = False,
               destino: tuple[int, int] | None = None, centro_dest: tuple[float, float] | None = None):
    """Escala/rota/desplaza `im` alrededor de su centro con precisión de subpíxel (bicúbico).

    · bbox=False: devuelve una imagen del MISMO tamaño (dx, dy en píxeles).
    · bbox=True: devuelve (imagen_recortada, (x, y)) lista para `alpha_composite` sobre un lienzo de
      tamaño `destino`, con el centro de `im` puesto en `centro_dest` — solo procesa su caja, así un
      sprite pequeño no cuesta un lienzo entero."""
    w, h = im.size
    ex, ey = max(1e-4, esc * sx), max(1e-4, esc * sy)
    th = math.radians(rot)
    c, s = math.cos(th), math.sin(th)
    if not bbox:
        cx, cy = w / 2.0, h / 2.0
        ox, oy = cx + dx, cy + dy
        a, b = c / ex, s / ey
        d, e = -s / ex, c / ey
        coef = (a, b, cx - a * ox - b * oy, d, e, cy - d * ox - e * oy)
        out = im.transform((w, h), Image.AFFINE, coef, resample=Image.BICUBIC)
        return _con_alfa(out, alpha)
    # caja de salida
    W, H = destino
    ccx, ccy = centro_dest
    hw, hh = w / 2.0, h / 2.0
    ext_x = abs(c) * hw * ex + abs(s) * hh * ey
    ext_y = abs(s) * hw * ex + abs(c) * hh * ey
    x0, y0 = int(math.floor(ccx - ext_x)) - 1, int(math.floor(ccy - ext_y)) - 1
    x1, y1 = int(math.ceil(ccx + ext_x)) + 1, int(math.ceil(ccy + ext_y)) + 1
    x0c, y0c, x1c, y1c = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1c <= x0c or y1c <= y0c:
        return None
    ox, oy = ccx - x0c, ccy - y0c            # centro de destino dentro de la caja
    a, b = c / ex, s / ey
    d, e = -s / ex, c / ey
    coef = (a, b, hw - a * ox - b * oy, d, e, hh - d * ox - e * oy)
    out = im.transform((x1c - x0c, y1c - y0c), Image.AFFINE, coef, resample=Image.BICUBIC)
    return _con_alfa(out, alpha), (x0c, y0c)


def _con_alfa(im: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return im
    a = im.getchannel("A").point(lambda v: int(v * alpha + 0.5))
    im = im.copy()
    im.putalpha(a)
    return im


# ------------------------------------------------------------------ pegatina (halo + contorno + sombra dura)
def dilata(alfa: Image.Image, px: float) -> Image.Image:
    """Silueta agrandada `px` píxeles (desenfoque + umbral: esquinas redondeadas y borde limpio)."""
    if px < 0.5:
        return alfa
    sigma = max(0.6, px / 1.48)
    b = alfa.filter(ImageFilter.GaussianBlur(sigma))
    return b.point(lambda v: 0 if v < 12 else 255 if v > 30 else int((v - 12) * 255 / 18))


def pegatina(icono: Image.Image, pal: Pal, crema_px: float, tinta_px: float, sombra_px: float) -> Image.Image:
    """Ilustración RGBA -> con halo crema, contorno de tinta y sombra dura hacia abajo-derecha
    (la misma sombra sin difuminar de las tarjetas)."""
    W, H = icono.size
    a = icono.getchannel("A")
    a_crema = dilata(a, crema_px)
    a_tinta = dilata(a, crema_px + tinta_px)
    s = int(round(sombra_px))
    ink = pal.tinta[:3]
    out = Image.new("RGBA", (W, H), ink + (0,))
    if s > 0:
        sombra_a = Image.new("L", (W, H), 0)
        sombra_a.paste(a_tinta, (s, s))
        cap = Image.new("RGBA", (W, H), ink + (255,))
        cap.putalpha(sombra_a)
        out.alpha_composite(cap)
    cap = Image.new("RGBA", (W, H), ink + (255,))
    cap.putalpha(a_tinta)
    out.alpha_composite(cap)
    cap = Image.new("RGBA", (W, H), pal.crema[:3] + (255,))
    cap.putalpha(a_crema)
    out.alpha_composite(cap)
    out.alpha_composite(icono)
    return out


# ------------------------------------------------------------------ cinta con texto (etiqueta bajo la ilustración)
def cinta(texto: str, pal: Pal, alto_px: int, ancho_max_px: int) -> Image.Image:
    """Etiqueta corta sobre una cinta dorada con contorno de tinta y sombra dura, RGBA."""
    px = max(10, int(alto_px * 0.56))
    f = fuente(pal.fuente_numero, px)
    tmp = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    while px > 12 and tmp.textbbox((0, 0), texto, font=f)[2] + alto_px * 0.9 > ancho_max_px:
        px -= 2
        f = fuente(pal.fuente_numero, px)
    bb = tmp.textbbox((0, 0), texto, font=f)
    tw = bb[2] - bb[0]
    w = int(tw + alto_px * 0.9)
    h = int(alto_px)
    borde = max(2, int(alto_px * 0.09))
    sombra = max(3, int(alto_px * 0.16))
    img = Image.new("RGBA", (w + sombra + 2, h + sombra + 2), pal.tinta[:3] + (0,))
    d = ImageDraw.Draw(img)
    r = h * 0.28
    d.rounded_rectangle((sombra, sombra, w + sombra, h + sombra), radius=r, fill=pal.tinta)
    d.rounded_rectangle((0, 0, w, h), radius=r, fill=pal.dorado, outline=pal.tinta, width=borde)
    d.text((w / 2, h / 2), texto, font=f, fill=pal.tinta, anchor="mm")
    return img
