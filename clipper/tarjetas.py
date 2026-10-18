"""Tarjetas de Vídeos PRO: la card animada que reemplaza al subtítulo para
puntos/herramientas/definiciones/datos (el plan sale de `director.py`, campo
"tarjetas"). Se dibujan con Pillow — ffmpeg no hace esquinas redondeadas ni
sombra dura limpia sin líos — y se pegan al vídeo con `overlay`. Mismo
estilo que se aprobó en el mockup: borde de tinta, sombra dura sin
difuminar, tal cual está en tu-sitio.example (no inventado, se sacó
inspeccionando el sitio real).

Solo dibuja PNGs sueltos (una función = una tarjeta = un archivo). Pegarlos
al vídeo con el timing correcto es trabajo de `render.py`/`pipeline.py`.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import ROOT
from .util import log

_GRIS = (61, 61, 61, 255)          # texto secundario — color real medido en tu-sitio.example
# Colores FIJOS (no de la marca del canal) para las dos CTA automáticas — el autor pidió (2026-09-23)
# que se vean SIEMPRE igual sea cual sea el canal: rojo de YouTube para suscribirse (el color que
# todo el mundo asocia con ese botón) y un verde claro para el like.
_ROJO_YT = "#FF0000"
_VERDE_LIKE = "#8FE3B0"             # el mismo verde «bien» que ya usa el panel (--pila-ok)
# ...y con su PROPIO sonido: un «pop», no el etéreo de las tarjetas del contenido (el autor, 2026-09-26: «el sonido que
# acompaña las alertas de suscribirse y like, que sea distinto al de las tarjetas»). Ver render._pop_path.
TIPOS_CON_POP = ("suscribete", "like")

# Logo OFICIAL de YouTube (el autor lo pidió, 2026-09-23: "el logo de youtube original" en la
# tarjeta de suscríbete) — assets/youtube_icon.png es el PNG a color tal cual lo reparte Google
# en brand.youtube (YouTube_Icon/Digital/01 Red/yt_icon_red_digital.png del zip oficial), sin
# tocar un píxel. Sus normas de marca ("Misuse") prohíben ponerle contorno, sombra, recoloreado
# o rotación al icono — por eso vive en su propia placa blanca lisa (_placa_icono_youtube) en
# vez de fundirse con el estilo tinta+sombra del resto de las tarjetas.
_ICONO_YT_PATH = ROOT / "assets" / "youtube_icon.png"
_icono_yt_cache: object = None


def _lum(c) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _gris(marca: dict) -> tuple[int, int, int, int]:
    """Texto secundario: el gris medido en tu-sitio.example; si la marca es OSCURA (tinta más clara que el fondo),
    un gris claro para que se lea."""
    return _GRIS if _lum(_hex(marca["tinta"])) <= _lum(_hex(marca["crema"])) else (205, 205, 205, 255)


def _hex(h: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _fuente(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        log(f"AVISO: no encuentro la fuente {path}, uso la de PIL por defecto.")
        return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, texto: str, font: ImageFont.FreeTypeFont,
         max_w: int) -> list[str]:
    """Envuelve `texto` en líneas que quepan en `max_w` px — igual que hace
    un navegador, pero a mano: PIL no envuelve texto solo."""
    palabras = texto.split()
    if not palabras:
        return [""]
    lineas, actual = [], palabras[0]
    for w in palabras[1:]:
        prueba = actual + " " + w
        if draw.textbbox((0, 0), prueba, font=font)[2] <= max_w:
            actual = prueba
        else:
            lineas.append(actual)
            actual = w
    lineas.append(actual)
    return lineas


class _Medidas:
    """Todas las medidas pensadas para un lienzo de 1080 px de alto y
    escaladas al `alto` real configurado — así una tarjeta se ve igual de
    proporcionada si algún día cambias `videos.alto`."""
    def __init__(self, alto_canvas: int):
        e = alto_canvas / 1080.0
        self.e = e
        self.borde = max(2, round(6 * e))
        self.radio = max(3, round(8 * e))
        self.sombra = max(4, round(14 * e))
        self.pad_x = round(34 * e)
        self.pad_y = round(26 * e)
        self.kicker = round(24 * e)
        self.tab_d = round(64 * e)
        self.tab_font = round(34 * e)
        self.punto_font = round(40 * e)
        self.herr_font = round(46 * e)
        self.def_term_font = round(50 * e)
        self.def_font = round(30 * e)
        self.dato_num_font = round(100 * e)
        self.dato_sub_font = round(32 * e)
        self.gap = round(20 * e)
        self.max_ancho = round(760 * e)   # tope de ancho de una tarjeta


def _base(w: int, h: int, m: _Medidas, marca: dict, relleno: str | None = None,
         relleno_hex: str | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Lienzo transparente con la sombra dura + el cuerpo (crema por
    defecto) con borde de tinta ya dibujados — lo que devuelve queda listo
    para escribirle encima. `relleno`: clave de `marca` para el fondo del
    cuerpo si no quieres el crema de siempre (una tarjeta educativa más).
    `relleno_hex`: un color FIJO (no de la marca del canal) para las
    tarjetas que quiere que se vean SIEMPRE igual sea cual sea el canal —
    like (verde) y suscríbete (rojo de YouTube): tiene prioridad sobre
    `relleno` si se da."""
    tinta = _hex(marca["tinta"])
    cuerpo = _hex(relleno_hex) if relleno_hex else _hex(marca[relleno or "crema"])
    img = Image.new("RGBA", (w + m.sombra, h + m.sombra), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((m.sombra, m.sombra, w + m.sombra, h + m.sombra),
                        radius=m.radio, fill=tinta)
    d.rounded_rectangle((0, 0, w, h), radius=m.radio, fill=cuerpo,
                        outline=tinta, width=m.borde)
    return img, d


def _punto(m: _Medidas, marca: dict, numero: int, texto: str) -> Image.Image:
    f_tab = _fuente(marca["fuente_numero"], m.tab_font)
    f_txt = _fuente(marca["fuente_titulo"], m.punto_font)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    max_txt_w = m.max_ancho - m.tab_d - m.gap - m.pad_x * 2
    lineas = _wrap(tmp, texto, f_txt, max_txt_w)
    txt_w = max(tmp.textbbox((0, 0), l, font=f_txt)[2] for l in lineas)
    lh = f_txt.size + round(6 * m.e)
    txt_h = lh * len(lineas)
    w = m.pad_x * 2 + m.tab_d + m.gap + txt_w
    h = m.pad_y * 2 + max(m.tab_d, txt_h)
    img, d = _base(w, h, m, marca)
    cy = h // 2
    d.ellipse((m.pad_x, cy - m.tab_d // 2, m.pad_x + m.tab_d, cy + m.tab_d // 2),
              fill=_hex(marca["tinta"]))
    num = str(numero)
    bb = d.textbbox((0, 0), num, font=f_tab)
    d.text((m.pad_x + m.tab_d / 2 - (bb[2] - bb[0]) / 2 - bb[0],
            cy - (bb[3] - bb[1]) / 2 - bb[1]), num, font=f_tab, fill=_hex(marca["crema"]))
    ty = cy - txt_h / 2
    for l in lineas:
        d.text((m.pad_x + m.tab_d + m.gap, ty), l, font=f_txt, fill=_hex(marca["tinta"]))
        ty += lh
    return img


def _herramienta(m: _Medidas, marca: dict, texto: str) -> Image.Image:
    f_kick = _fuente(marca["fuente_texto"], m.kicker)
    f_txt = _fuente(marca["fuente_titulo"], m.herr_font)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    kick = "HERRAMIENTA"
    kb = tmp.textbbox((0, 0), kick, font=f_kick)
    tb = tmp.textbbox((0, 0), texto, font=f_txt)
    w = m.pad_x * 2 + max(kb[2] - kb[0], tb[2] - tb[0])
    gap = round(12 * m.e)      # aire entre el rótulo y el título (con el texto ya bien alineado)
    h = m.pad_y * 2 + (kb[3] - kb[1]) + gap + (tb[3] - tb[1])
    img, d = _base(w, h, m, marca)
    y = m.pad_y
    d.text((m.pad_x, y - kb[1]), kick, font=f_kick, fill=_hex(marca["naranja"]))
    y += (kb[3] - kb[1]) + gap
    d.text((m.pad_x, y - tb[1]), texto, font=f_txt, fill=_hex(marca["tinta"]))
    return img


def _definicion(m: _Medidas, marca: dict, termino: str, extra: str) -> Image.Image:
    f_kick = _fuente(marca["fuente_texto"], m.kicker)
    f_term = _fuente(marca["fuente_numero"], m.def_term_font)
    f_def = _fuente(marca["fuente_texto"], m.def_font)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    max_w = m.max_ancho - m.pad_x * 2
    lineas = _wrap(tmp, extra, f_def, max_w) if extra.strip() else []
    kick = "DEFINICIÓN"
    kb = tmp.textbbox((0, 0), kick, font=f_kick)
    tb = tmp.textbbox((0, 0), termino, font=f_term)
    def_w = max((tmp.textbbox((0, 0), l, font=f_def)[2] for l in lineas), default=0)
    w = m.pad_x * 2 + max(kb[2] - kb[0], tb[2] - tb[0], def_w)
    gap = round(12 * m.e)      # aire entre el rótulo y el título (con el texto ya bien alineado)
    lh = f_def.size + round(6 * m.e)
    h = m.pad_y * 2 + (kb[3] - kb[1]) + gap + (tb[3] - tb[1]) + (gap + lh * len(lineas) if lineas else 0)
    img, d = _base(w, h, m, marca)
    y = m.pad_y
    d.text((m.pad_x, y - kb[1]), kick, font=f_kick, fill=_hex(marca["naranja"]))
    y += (kb[3] - kb[1]) + gap
    d.text((m.pad_x, y - tb[1]), termino, font=f_term, fill=_hex(marca["naranja"]))
    y += (tb[3] - tb[1]) + gap
    for l in lineas:
        d.text((m.pad_x, y), l, font=f_def, fill=_gris(marca))
        y += lh
    return img


def dibuja_explicativa(termino: str, definicion: str, marca: dict, w: int, h: int) -> Image.Image:
    """El modo de cámara "explicativa" (clipper/pipeline.py, clipper/render.py):
    en vez de una tarjeta flotando ENCIMA del vídeo, esto RELLENA el hueco
    donde no está la cara (el otro cam_fx recorta la cara al tercio derecho
    del lienzo) — mismo lenguaje visual que `_definicion` (kicker naranja +
    término + definición), pero el lienzo es FIJO (lo manda el vídeo, no el
    contenido) y el bloque de texto se centra verticalmente en vez de que el
    lienzo se ajuste a él."""
    e = h / 1080.0
    f_kick = _fuente(marca["fuente_texto"], round(30 * e))
    f_term = _fuente(marca["fuente_numero"], round(86 * e))
    f_def = _fuente(marca["fuente_texto"], round(42 * e))
    img = Image.new("RGBA", (w, h), _hex(marca["crema"]))
    d = ImageDraw.Draw(img)
    pad_x = round(85 * e)
    max_w = max(50, w - pad_x * 2)
    kick = "DEFINICIÓN"
    l_term = _wrap(d, termino, f_term, max_w) if termino.strip() else [""]
    l_def = _wrap(d, definicion, f_def, max_w) if definicion.strip() else []
    kb = d.textbbox((0, 0), kick, font=f_kick)
    gap1, gap2 = round(24 * e), round(30 * e)
    lh_term = round(f_term.size * 1.08)
    lh_def = round(f_def.size * 1.32)
    alto = ((kb[3] - kb[1]) + gap1 + lh_term * len(l_term)
           + (gap2 + lh_def * len(l_def) if l_def else 0))
    y = max(pad_x, (h - alto) // 2)
    d.text((pad_x, y - kb[1]), kick, font=f_kick, fill=_hex(marca["naranja"]))
    y += (kb[3] - kb[1]) + gap1
    for linea in l_term:
        d.text((pad_x, y), linea, font=f_term, fill=_hex(marca["naranja"]))
        y += lh_term
    if l_def:
        y += gap2
        for linea in l_def:
            d.text((pad_x, y), linea, font=f_def, fill=_gris(marca))
            y += lh_def
    return img


def _dato(m: _Medidas, marca: dict, numero: str, extra: str, estimado: bool = False) -> Image.Image:
    # una cifra que es un ESTIMADO del presentador (no un dato comprobable) se dice como tal: «≈90%» y «Mi estimación · …»
    # (el autor, 2026-09-26: «que no se vea como que yo estoy inventando esos números»)
    if estimado:
        numero = numero if numero.lstrip().startswith(("≈", "~")) else "≈" + numero.strip()
        extra = "Mi estimación" + (f" · {extra.strip()}" if extra.strip() else "")
    f_num = _fuente(marca["fuente_numero"], m.dato_num_font)
    f_sub = _fuente(marca["fuente_texto"], m.dato_sub_font)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    max_w = m.max_ancho - m.pad_x * 2
    lineas = _wrap(tmp, extra, f_sub, max_w) if extra.strip() else []
    nb = tmp.textbbox((0, 0), numero, font=f_num)
    sub_w = max((tmp.textbbox((0, 0), l, font=f_sub)[2] for l in lineas), default=0)
    w = m.pad_x * 2 + max(nb[2] - nb[0], sub_w)
    gap = round(6 * m.e)
    lh = f_sub.size + round(6 * m.e)
    h = m.pad_y * 2 + (nb[3] - nb[1]) + (gap + lh * len(lineas) if lineas else 0)
    img, d = _base(w, h, m, marca)
    y = m.pad_y
    d.text((m.pad_x, y - nb[1]), numero, font=f_num, fill=_hex(marca["naranja"]))   # -nb[1]: el hueco de arriba de la fuente
    y += (nb[3] - nb[1]) + gap
    for l in lineas:
        d.text((m.pad_x, y), l, font=f_sub, fill=_gris(marca))
        y += lh
    return img


def _icono_youtube() -> Image.Image | None:
    """El PNG oficial de YouTube (ver constante `_ICONO_YT_PATH`), cacheado en memoria. None si
    por lo que sea no está el archivo — la tarjeta de suscríbete sigue funcionando igual, solo
    que sin el logo (mejor eso que reventar el render)."""
    global _icono_yt_cache
    if _icono_yt_cache is None:
        try:
            _icono_yt_cache = Image.open(_ICONO_YT_PATH).convert("RGBA")
        except OSError:
            log(f"AVISO: no encuentro {_ICONO_YT_PATH} — la tarjeta de suscríbete sale sin el logo de YouTube.")
            _icono_yt_cache = False
    return _icono_yt_cache or None


def _placa_icono_youtube(alto: float) -> Image.Image | None:
    """Placa BLANCA lisa — SIN el contorno de tinta ni la sombra dura del resto de la tarjeta —
    con el logo oficial de YouTube dentro y aire alrededor (el «clear space» que piden sus
    normas de marca). `alto`: alto de la placa; el ancho sale solo de la proporción real del
    PNG (es más ancho que alto, no lo fuerces a cuadrado o se recorta)."""
    icono = _icono_youtube()
    if icono is None:
        return None
    H = round(alto)
    pad = round(H * 0.16)
    alto_icono = H - pad * 2
    ancho_icono = round(alto_icono * icono.width / icono.height)
    icono_r = icono.resize((ancho_icono, alto_icono), Image.LANCZOS)
    W = ancho_icono + pad * 2
    placa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(placa)
    d.rounded_rectangle((0, 0, W, H), radius=round(H * 0.18), fill=(255, 255, 255, 255))
    placa.alpha_composite(icono_r, (pad, pad))
    return placa


def _suscribete(m: _Medidas, marca: dict, texto: str, sub: str) -> Image.Image:
    """El CTA de suscripción — SIEMPRE se pone (pipeline.py la agrega sola
    al 60% del vídeo, no depende de que el director la pida). Fondo ROJO DE
    YOUTUBE (fijo, no el naranja de la marca): el autor lo pidió así — el color
    que todo el mundo asocia con ese botón, sea cual sea el canal. Lleva el
    logo oficial de YouTube en su propia placa blanca (ver
    `_placa_icono_youtube`) — el autor lo pidió (2026-09-23)."""
    f_kick = _fuente(marca["fuente_texto"], m.kicker)
    f_txt = _fuente(marca["fuente_numero"], round(m.herr_font * 1.15))
    f_sub = _fuente(marca["fuente_texto"], m.dato_sub_font)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    kick = "¿TE ESTÁ GUSTANDO?"
    placa = _placa_icono_youtube(m.tab_d)
    extra_w = (placa.width + m.gap) if placa else 0
    max_w = m.max_ancho - m.pad_x * 2 - extra_w
    lineas_sub = _wrap(tmp, sub, f_sub, max_w) if sub.strip() else []
    kb = tmp.textbbox((0, 0), kick, font=f_kick)
    tb = tmp.textbbox((0, 0), texto, font=f_txt)
    sub_w = max((tmp.textbbox((0, 0), l, font=f_sub)[2] for l in lineas_sub), default=0)
    txt_w = max(kb[2] - kb[0], tb[2] - tb[0], sub_w)
    gap = round(12 * m.e)      # aire entre el rótulo y el título (con el texto ya bien alineado)
    lh = f_sub.size + round(6 * m.e)
    txt_h = ((kb[3] - kb[1]) + gap + (tb[3] - tb[1])
             + (gap + lh * len(lineas_sub) if lineas_sub else 0))
    w = m.pad_x * 2 + extra_w + txt_w
    h = m.pad_y * 2 + max((placa.height if placa else 0), txt_h)
    img, d = _base(w, h, m, marca, relleno_hex=_ROJO_YT)
    if placa:
        img.alpha_composite(placa, (m.pad_x, round((h - placa.height) / 2)))
    crema = _hex(marca["crema"])
    x = m.pad_x + extra_w
    y = (h - txt_h) / 2
    d.text((x, y - kb[1]), kick, font=f_kick, fill=crema)
    y += (kb[3] - kb[1]) + gap
    d.text((x, y - tb[1]), texto, font=f_txt, fill=crema)
    y += (tb[3] - tb[1]) + gap
    for l in lineas_sub:
        d.text((x, y), l, font=f_sub, fill=crema)
        y += lh
    return img


# El icono «thumb_up» de Material Design (Google, licencia Apache 2.0): el pulgar que todo el mundo reconoce como «me
# gusta», la misma forma que el de YouTube. Coordenadas en su rejilla de 24×24 (de su trazado SVG oficial), con las
# esquinas redondeadas aproximadas con puntos. el autor (2026-09-24): el pulgar dibujado a mano «no se ve bien, no es tan
# diciente» — parecía una mancha.
_PULGAR_MANGA = (1.0, 9.0, 5.0, 21.0)
_PULGAR_MANO = [(23, 10), (22.85, 9.25), (22.4, 8.6), (21.75, 8.15), (21, 8), (14.69, 8), (15.64, 3.43), (15.67, 3.11),
                (15.62, 2.7), (15.45, 2.33), (15.23, 2.05), (14.17, 1), (7.59, 7.59), (7.3, 7.95), (7.1, 8.45), (7, 9),
                (7, 19), (7.15, 19.75), (7.6, 20.4), (8.25, 20.85), (9, 21), (18, 21), (18.83, 21), (19.54, 20.5),
                (19.84, 19.78), (22.86, 12.73), (22.95, 12.5), (23, 12.26), (23, 12)]


def _dibuja_pulgar(base: Image.Image, x0: float, y0: float, s: float, color) -> None:
    """El pulgar de «me gusta» (el «thumb_up» de Material Design) dentro del cuadrado (x0,y0)-(x0+s,y0+s). Se dibuja a 4×
    y se reduce (bordes suaves, sin dientes) y se centra (en su rejilla ocupa de 1 a 23 de ancho y de 1 a 21 de alto).
    Nada de emoji: Rubik no trae esos glifos y Pillow no los pinta en color."""
    k = 4
    lado = max(8, int(round(s)))
    esc = lado * k / 24.0
    capa = Image.new("L", (lado * k, lado * k), 0)
    d = ImageDraw.Draw(capa)
    dy = 1.0 * esc                                          # baja 1/24 para quedar centrado en alto
    m0, m1, m2, m3 = _PULGAR_MANGA
    d.rectangle((m0 * esc, m1 * esc + dy, m2 * esc, m3 * esc + dy), fill=255)
    d.polygon([(x * esc, y * esc + dy) for x, y in _PULGAR_MANO], fill=255)
    mascara = capa.resize((lado, lado), Image.LANCZOS)
    relleno = Image.new("RGBA", (lado, lado), tuple(color[:3]) + (255,))
    base.paste(relleno, (int(round(x0)), int(round(y0))), mascara)


def _like(m: _Medidas, marca: dict, texto: str, sub: str) -> Image.Image:
    """El CTA de «dale like» — igual que `_suscribete` (pipeline.py la agrega sola, SIEMPRE, al
    80% del vídeo: no depende de que el director la pida), pero con su propio icono dibujado a
    mano y fondo VERDE CLARO fijo (no de la marca), para que no se confunda con la de suscribirse
    (roja) si el espectador ve las dos en el mismo vídeo."""
    f_kick = _fuente(marca["fuente_texto"], m.kicker)
    f_txt = _fuente(marca["fuente_numero"], round(m.herr_font * 1.0))
    f_sub = _fuente(marca["fuente_texto"], m.dato_sub_font)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    kick = "¿TE SIRVIÓ ESTE VÍDEO?"
    dia = round(m.tab_d * 1.35)                    # el círculo del pulgar, más grande que el número de «punto»: se lee de lejos
    max_w = m.max_ancho - m.pad_x * 2 - dia - m.gap
    lineas_sub = _wrap(tmp, sub, f_sub, max_w) if sub.strip() else []
    kb = tmp.textbbox((0, 0), kick, font=f_kick)
    tb = tmp.textbbox((0, 0), texto, font=f_txt)
    sub_w = max((tmp.textbbox((0, 0), l, font=f_sub)[2] for l in lineas_sub), default=0)
    txt_w = max(kb[2] - kb[0], tb[2] - tb[0], sub_w)
    gap = round(12 * m.e)
    lh = f_sub.size + round(6 * m.e)
    txt_h = ((kb[3] - kb[1]) + gap + (tb[3] - tb[1])
             + (gap + lh * len(lineas_sub) if lineas_sub else 0))
    w = m.pad_x * 2 + dia + m.gap + txt_w
    h = m.pad_y * 2 + max(dia, txt_h)
    img, d = _base(w, h, m, marca, relleno_hex=_VERDE_LIKE)
    tinta, crema = _hex(marca["tinta"]), _hex(marca["crema"])
    cy = h / 2
    d.ellipse((m.pad_x, cy - dia / 2, m.pad_x + dia, cy + dia / 2), fill=tinta)
    _dibuja_pulgar(img, m.pad_x + dia * 0.20, cy - dia * 0.30, dia * 0.60, crema)
    x = m.pad_x + dia + m.gap
    y = cy - txt_h / 2
    d.text((x, y - kb[1]), kick, font=f_kick, fill=tinta)
    y += (kb[3] - kb[1]) + gap
    d.text((x, y - tb[1]), texto, font=f_txt, fill=tinta)
    y += (tb[3] - tb[1]) + gap
    for l in lineas_sub:
        d.text((x, y), l, font=f_sub, fill=tinta)
        y += lh
    return img


def dibuja(tarjeta: dict, marca: dict, alto_canvas: int = 1080) -> Image.Image | None:
    """La tarjeta (dict del plan del director) -> imagen RGBA lista para
    pegar al vídeo. None si el tipo no se reconoce (no debería pasar: ya
    viene validado por director._valida, pero mejor no reventar el render
    por un campo raro)."""
    m = _Medidas(alto_canvas)
    tipo = tarjeta.get("tipo")
    try:
        if tipo == "punto":
            return _punto(m, marca, tarjeta.get("numero") or 1, tarjeta["texto"])
        if tipo == "herramienta":
            return _herramienta(m, marca, tarjeta["texto"])
        if tipo == "definicion":
            return _definicion(m, marca, tarjeta["texto"], tarjeta.get("extra", ""))
        if tipo == "dato":
            return _dato(m, marca, tarjeta["texto"], tarjeta.get("extra", ""), bool(tarjeta.get("estimado")))
        if tipo == "suscribete":
            return _suscribete(m, marca, tarjeta["texto"], tarjeta.get("extra", ""))
        if tipo == "like":
            return _like(m, marca, tarjeta["texto"], tarjeta.get("extra", ""))
    except Exception as exc:                                    # noqa: BLE001
        log(f"AVISO: no se pudo dibujar la tarjeta {tipo!r} ({exc}).")
        return None
    log(f"AVISO: tipo de tarjeta desconocido: {tipo!r}.")
    return None


def genera_pngs(tarjetas: list[dict], marca: dict, out_dir: Path,
                alto_canvas: int = 1080) -> list[dict]:
    """Dibuja TODAS las tarjetas del plan a disco (`out_dir/tarjeta_00.png`,
    ...) y devuelve la lista de overlays lista para render.py: cada item
    trae el path del PNG + cuándo debe entrar/salir + su tamaño real."""
    out_dir.mkdir(parents=True, exist_ok=True)
    overlays = []
    for i, tj in enumerate(tarjetas):
        img = dibuja(tj, marca, alto_canvas)
        if img is None:
            continue
        p = out_dir / f"tarjeta_{i:02d}.png"
        img.save(p)
        dur = float(tj.get("duracion", 3.5) or 3.5)
        t0 = float(tj["t"])
        ov = {"path": str(p), "w": img.width, "h": img.height, "inicio": t0, "fin": t0 + dur}
        if tj.get("tipo") in TIPOS_CON_POP:
            ov["sonido"] = "pop"          # las alertas de suscríbete y like no suenan como una tarjeta (render._pop_path)
        overlays.append(ov)
    return overlays


def prueba() -> list[dict]:
    """`dibuja_explicativa` — la única función de este archivo con lienzo FIJO (lo manda el vídeo
    de salida, no el contenido): a diferencia de las demás tarjetas (que crecen o encogen según el
    texto), esta SIEMPRE devuelve exactamente w x h, pase lo que pase con el término/definición."""
    from . import config as config_mod

    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    marca = dict(config_mod.load().get_path("videos.marca") or {})
    if not marca:
        r("hay marca configurada para probar dibuja_explicativa (config.yaml)", False,
          "sin videos.marca no se puede probar de verdad")
        return out

    def _dibujo_no_vacio(img) -> bool:
        # esquina superior izquierda: fuera del bloque de texto (que se
        # centra verticalmente con relleno a los lados) -> siempre debe
        # quedar del color de fondo puro. Si ALGÚN píxel de una rejilla
        # amplia difiere, es que sí se dibujó texto (no importa qué fuente
        # haya cargado PIL: hasta la de repuesto pinta algo) — se barre TODA
        # la altura (no solo la fila central) porque el bloque de texto
        # puede no cruzar exactamente esa fila según cuántas líneas tenga.
        fondo = img.getpixel((0, 0))
        return any(img.getpixel((x, y)) != fondo
                  for y in range(0, img.height, max(1, img.height // 40))
                  for x in range(0, img.width, max(1, img.width // 40)))

    w, h = 1280, 1080
    img1 = dibuja_explicativa("SEO", "Optimizar tu web para que Google te encuentre.", marca, w, h)
    r("dibuja_explicativa devuelve EXACTO w x h (lienzo fijo, no ajustado al texto)",
      img1.size == (w, h), img1.size)
    r("con término y definición normales, sí se dibuja algo (no queda en blanco)",
      _dibujo_no_vacio(img1))

    definicion_larga = ("Una definición bastante larga que debería envolverse en varias líneas "
                        "sin salirse del ancho disponible ni reventar el layout de la tarjeta.")
    img2 = dibuja_explicativa("Indexación", definicion_larga, marca, w, h)
    r("una definición larga (varias líneas) NO cambia el tamaño del lienzo",
      img2.size == (w, h), img2.size)
    r("una definición larga sí se alcanza a dibujar", _dibujo_no_vacio(img2))

    img3 = dibuja_explicativa("Un término bastante más largo de lo normal para forzar el ajuste",
                              "", marca, w, h)
    r("un término largo sin definición no revienta y mantiene el lienzo fijo",
      img3.size == (w, h), img3.size)

    img4 = dibuja_explicativa("CMS", "Sistema para gestionar contenido web.", marca, 640, 1080)
    r("un lienzo más angosto (tercio más pequeño) también sale exacto y con algo dibujado",
      img4.size == (640, 1080) and _dibujo_no_vacio(img4), img4.size)

    # un DATO que es un estimado del presentador (el autor, 2026-09-26): «≈90%» y «Mi estimación · …», más ancho y más alto que
    # el mismo dato presentado como hecho (el «≈» y la línea de más se dibujan de verdad)
    normal = dibuja({"tipo": "dato", "texto": "90%", "extra": "más fuerza"}, marca)
    estim = dibuja({"tipo": "dato", "texto": "90%", "extra": "más fuerza", "estimado": True}, marca)
    r("un dato estimado sale con «≈» y «Mi estimación» (se dibujan: la tarjeta crece)",
      normal is not None and estim is not None and estim.width > normal.width,
      (getattr(normal, "size", None), getattr(estim, "size", None)))

    # las alertas de suscríbete y like piden su «pop» al render; una tarjeta del contenido, el sonido de siempre (el autor,
    # 2026-09-26: «que sea distinto al de las tarjetas»)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ovs = genera_pngs([{"t": 1.0, "tipo": "dato", "texto": "90%", "extra": "x"},
                           {"t": 5.0, "tipo": "suscribete", "texto": "¡SUSCRÍBETE!", "extra": "y", "duracion": 4.0},
                           {"t": 9.0, "tipo": "like", "texto": "¡DALE LIKE!", "extra": "z", "duracion": 4.0}],
                          marca, Path(tmp))
    r("suscríbete y like llevan su propio sonido («pop»); la tarjeta del contenido, el de siempre",
      [o.get("sonido") for o in ovs] == [None, "pop", "pop"], [o.get("sonido") for o in ovs])

    return out
