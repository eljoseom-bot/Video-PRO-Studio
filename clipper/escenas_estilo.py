"""ESCENAS DE ESTILO A PANTALLA COMPLETA (el autor, 2026-10-10, tras analizar el vídeo «The 5 Rules of AI SEO» de Neil Patel): cinco escenas «aplicadas» que,
a diferencia de las ilustraciones, solo salen cuando tu tema encaja. Van por la MISMA vía que `escena_completa` (una capa a pantalla completa de fotogramas PNG a los
fps del vídeo, tu voz debajo, «whoosh» al entrar y al volver) y salen en «Ediciones».

  · capitulo   — un punto numerado («número dos: …»): mosaico de píxeles que se aclara, marco dorado que se dibuja, número grande y la frase palabra a palabra.
  · grafica    — un ESTUDIO / informe / reporte con cifras comparables: barras que crecen con los números contando y un empuje de cámara muy suave.
  · chat       — un ejemplo de pregunta a una IA (ChatGPT & co.): la pregunta se teclea, la respuesta se escribe y sale la fuente. Estilo ChatGPT a propósito.
  · veredicto  — un ejemplo bien/mal («esto sí lo cita, esto no»): píldoras con ✔ / ✖ que entran con rebote.
  · cita       — una frase completa y de peso, dicha por ti: letras grandes que suben palabra a palabra y el marcador pasa por lo clave.

Reglas anti-invento: el director marca cada una con una `cita` COPIADA de lo que dices (se comprueba contra la transcripción); lo que sale en pantalla (cifras, pregunta,
respuesta…) es lo que dijiste, nunca un dato nuevo. Cada función es una función PURA del tiempo (mismo t → mismo fotograma).
"""
from __future__ import annotations

import math
import re
import unicodedata

from PIL import Image, ImageDraw, ImageFilter

from .ilus_motor import Pal, fuente

ESTILOS = ("capitulo", "grafica", "chat", "veredicto", "cita")
VERDE = (46, 190, 108)
NOMBRES = {"capitulo": "Capítulo numerado", "grafica": "Gráfica de un estudio", "chat": "Chat de IA", "veredicto": "Bien / mal", "cita": "Frase importante"}
# duración (mín, máx, por defecto) de cada una: el capítulo es un golpe corto; la gráfica y el chat necesitan tiempo para leerse
DURACIONES = {"capitulo": (3.2, 4.5, 3.6), "grafica": (5.0, 8.0, 7.0), "chat": (5.0, 8.0, 7.0), "veredicto": (4.0, 6.5, 5.0), "cita": (4.0, 7.0, 5.5)}


# ------------------------------------------------------------------ utilidades
def _c(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def _out(x):
    x = _c(x)
    return 1 - (1 - x) ** 3


def _io(x):
    x = _c(x)
    return 3 * x * x - 2 * x ** 3


def _back(x, s=1.8):
    x = _c(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def _lerp(a, b, t):
    return a + (b - a) * t


def _mix(c1, c2, t):
    return tuple(int(round(_lerp(a, b, t))) for a, b in zip(c1[:3], c2[:3]))


def _rgb(c):
    return tuple(c[:3])


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(ch for ch in s if not unicodedata.combining(ch))).strip()


def empuja(im: Image.Image, z: float) -> Image.Image:
    """Acerca el fotograma `z` veces (≥ 1) hacia el centro con interpolación BICÚBICA de sub-píxel (una transformación afín, sin redondear a píxeles
    enteros: el zoom lento de un `crop` + `resize` salta de píxel en píxel y se ve «raro»)."""
    if z <= 1.0001:
        return im
    W, H = im.size
    a = 1.0 / z
    return im.transform((W, H), Image.AFFINE, (a, 0, W / 2 * (1 - a), 0, a, H / 2 * (1 - a)), resample=Image.BICUBIC)


def pixelado(im: Image.Image, bloque: float) -> Image.Image:
    b = max(1, int(round(bloque)))
    if b <= 1:
        return im
    W, H = im.size
    return im.resize((max(1, W // b), max(1, H // b)), Image.BILINEAR).resize((W, H), Image.NEAREST)


def _glow_abajo(W, H, color, fuerza=0.85):
    import numpy as np
    y = np.linspace(0, 1, H)[:, None]
    g = np.clip((y - 0.5) / 0.5, 0, 1) ** 1.7 * fuerza
    return g[..., None].astype(np.float32), np.array(color[:3], np.float32)[None, None, :]


def _fondo_oscuro(W, H, pal, glow=0.85, base=None):
    import numpy as np
    base = base or _mix(pal.tinta, (0, 0, 0), 0.35)
    arr = np.zeros((H, W, 3), np.float32) + np.array(base, np.float32)
    g, col = _glow_abajo(W, H, pal.naranja, glow)
    arr = arr * (1 - g) + col * g
    return Image.fromarray(arr.clip(0, 255).astype("uint8"))


def _lineas(palabras: list[str], ancho: float, f, d, max_lineas=3) -> list[list[str]]:
    out: list[list[str]] = []
    cur: list[str] = []
    for w in palabras:
        if cur and d.textlength(" ".join(cur + [w]), font=f) > ancho and len(out) < max_lineas - 1:
            out.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------------ 1) capítulo numerado
def f_capitulo(t, dur, W, H, pal, datos, tono="naranja"):
    u = H / 720.0
    num = str(int(datos.get("numero") or 1))
    titulo = str(datos.get("titulo") or "").strip()
    im = _fondo_oscuro(W, H, pal)
    # marco que se dibuja recorriendo su perímetro, con brillo
    x0, y0, x1, y1 = int(W * 0.11), int(H * 0.17), int(W * 0.89), int(H * 0.83)
    per = 2 * ((x1 - x0) + (y1 - y0))
    p = _io(t / 1.3) * per
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    rec = 0.0
    for a, b in zip(pts, pts[1:]):
        L = math.dist(a, b)
        if p <= rec:
            break
        k = _c((p - rec) / L)
        dc.line((a, (_lerp(a[0], b[0], k), _lerp(a[1], b[1], k))), fill=_rgb(pal.dorado) + (255,), width=max(2, int(3 * u)))
        rec += L
    glow = capa.filter(ImageFilter.GaussianBlur(7 * u))
    im = Image.alpha_composite(Image.alpha_composite(im.convert("RGBA"), glow), capa)
    # el número: dorado con caída a naranja y una sombra de luz
    a = _out((t - 0.45) / 0.55)
    if a > 0:
        big = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sc = _lerp(0.6, 1.0, _back((t - 0.45) / 0.55))
        ImageDraw.Draw(big).text((W // 2, int(H * 0.34)), num, font=fuente(pal.fuente_numero or pal.fuente_titulo, int(230 * u * sc)),
                                 fill=(255, 255, 255, int(255 * a)), anchor="mm")
        import numpy as np
        al = np.asarray(big)[..., 3].astype(np.float32) / 255.0
        yy = np.linspace(0, 1, H)[:, None]
        k = np.clip((yy - 0.18) / 0.34, 0, 1)
        rgb = np.zeros((H, W, 3), np.float32)
        for i in range(3):
            rgb[..., i] = pal.dorado[i] * (1 - k) + pal.naranja[i] * k
        num_img = Image.fromarray(np.dstack([rgb, al * 255]).clip(0, 255).astype("uint8"), "RGBA")
        luz = num_img.filter(ImageFilter.GaussianBlur(16 * u))
        im = Image.alpha_composite(Image.alpha_composite(im, luz), num_img)
    # el título, palabra a palabra
    d = ImageDraw.Draw(im, "RGBA")
    f = fuente(pal.fuente_titulo, int(50 * u))
    pal_t = titulo.split()
    lin = _lineas(pal_t, W * 0.62, f, d, 2)
    y = int(H * 0.66)
    k = 0
    for linea in lin:
        anchos = [d.textlength(w + " ", font=f) for w in linea]
        x = W / 2 - sum(anchos) / 2
        for w, ww in zip(linea, anchos):
            al = _out((t - 1.2 - k * 0.22) / 0.32)
            if al > 0:
                d.text((x, y + (1 - al) * 16 * u), w, font=f, fill=_rgb(pal.crema) + (int(255 * al),), anchor="lm")
            x += ww
            k += 1
        y += int(66 * u)
    return pixelado(im.convert("RGB"), _lerp(60 * u, 1, _out(t / 0.8))) if t < 0.8 else im.convert("RGB")


# ------------------------------------------------------------------ 2) gráfica de un estudio
def f_grafica(t, dur, W, H, pal, datos, tono="crema"):
    u = H / 720.0
    im = Image.new("RGB", (W, H), _mix(pal.crema, pal.beige, 0.35))
    d = ImageDraw.Draw(im)
    rej = _mix(pal.crema, pal.beige_osc, 0.45)
    paso = int(40 * u)
    for gx in range(0, W, paso):
        d.line((gx, 0, gx, H), fill=rej)
    for gy in range(0, H, paso):
        d.line((0, gy, W, gy), fill=rej)
    ent = _out(t / 0.5)
    titulo = str(datos.get("titulo") or "").strip()
    d.text((int(W * 0.07), int(H * 0.12) - (1 - ent) * 18 * u), "ESTUDIO", font=fuente(pal.fuente_texto, int(21 * u)), fill=_rgb(pal.naranja), anchor="lm")
    ft = fuente(pal.fuente_titulo, int(38 * u))
    lin = _lineas(titulo.split(), W * 0.86, ft, d, 2)
    for i, l in enumerate(lin):
        d.text((int(W * 0.07), int(H * 0.19) + i * int(46 * u) - (1 - ent) * 18 * u), " ".join(l), font=ft, fill=_rgb(pal.tinta), anchor="lm")
    barras = list(datos.get("barras") or [])[:4]
    n = max(1, len(barras))
    vmax = max([float(b.get("valor") or 0) for b in barras] + [1e-9])
    x_eje = int(W * 0.27)
    ancho = int(W * 0.62)
    top = int(H * 0.36)
    alto_fila = int((H * 0.80 - top) / n)
    d.line((x_eje, top - int(10 * u), x_eje, int(H * 0.80)), fill=_rgb(pal.tinta), width=max(2, int(3 * u)))
    cols = [_rgb(pal.naranja), (95, 95, 95), _rgb(pal.dorado), _rgb(pal.naranja_osc)]
    grosor = min(int(70 * u), int(alto_fila * 0.62))
    for i, b in enumerate(barras):
        cy = top + i * alto_fila + alto_fila // 2
        ini = 0.7 + i * 0.7
        col = cols[i % len(cols)]
        nom = str(b.get("nombre") or "")
        d.text((x_eje - int(18 * u), cy), nom, font=fuente(pal.fuente_titulo, int(27 * u)), fill=_rgb(pal.tinta), anchor="rm")
        d.rounded_rectangle((x_eje, cy - grosor // 2, x_eje + ancho, cy + grosor // 2), int(10 * u), fill=_mix(pal.crema, pal.beige_osc, 0.55))
        p = _out((t - ini) / 1.5)
        w = max(0, int(ancho * (float(b.get("valor") or 0) / vmax) * p))
        if w > 2:
            d.rounded_rectangle((x_eje, cy - grosor // 2, x_eje + w, cy + grosor // 2), int(10 * u), fill=col)
            et = str(b.get("etiqueta") or "")
            # el número «cuenta» hacia su cifra final: si el texto es del tipo 2:31, 14,5 % o 3 meses, solo se cuenta la parte numérica principal
            txt = _cuenta(et, p)
            f_n = fuente(pal.fuente_numero or pal.fuente_titulo, int(40 * u))
            if w < ancho * 0.8:
                d.text((x_eje + w + int(16 * u), cy), txt, font=f_n, fill=col if col != (95, 95, 95) else _rgb(pal.tinta), anchor="lm")
            else:
                d.text((x_eje + w - int(16 * u), cy), txt, font=f_n, fill=_rgb(pal.crema), anchor="rm")
    return empuja(im, 1.0 + 0.035 * _io(t / max(dur, 0.1)))


def _cuenta(etiqueta: str, p: float) -> str:
    """El texto de la cifra con su número principal «contando» de 0 a su valor (p de 0 a 1): `14,5 %` → `7,3 %`… `2:31` → `1:20`… Si no hay
    ninguna cifra, el texto entra tal cual con el avance."""
    m = re.search(r"(\d+)([.,:](\d+))?", etiqueta)
    if not m:
        return etiqueta if p > 0.5 else ""
    if ":" in m.group(0):
        mm, ss = int(m.group(1)), int(m.group(3))
        tot = int(round((mm * 60 + ss) * p))
        nuevo = f"{tot // 60}:{tot % 60:02d}"
    else:
        dec = len(m.group(3)) if m.group(3) else 0
        val = float(m.group(1) + ("." + m.group(3) if m.group(3) else "")) * p
        sep = "," if "," in m.group(0) else "."
        nuevo = f"{val:.{dec}f}".replace(".", sep) if dec else f"{int(round(val))}"
    return etiqueta[:m.start()] + nuevo + etiqueta[m.end():]


# ------------------------------------------------------------------ 3) chat de IA
def f_chat(t, dur, W, H, pal, datos, tono="oscuro"):
    u = H / 720.0
    im = Image.new("RGB", (W, H), (14, 13, 13))
    d = ImageDraw.Draw(im, "RGBA")
    bx0, by0, bx1, by1 = int(W * 0.14), int(H * 0.09), int(W * 0.86), int(H * 0.91)
    d.rounded_rectangle((bx0, by0, bx1, by1), int(26 * u), fill=(24, 23, 23), outline=(48, 45, 45), width=max(1, int(2 * u)))
    fcab = fuente(pal.fuente_texto, int(21 * u))
    d.rounded_rectangle((W / 2 - 92 * u, by0 + 24 * u, W / 2 + 92 * u, by0 + 60 * u), int(18 * u), fill=(40, 38, 38))
    d.text((W / 2 - 46 * u, by0 + 42 * u), "Chat", font=fcab, fill=(240, 236, 228), anchor="mm")
    d.text((W / 2 + 46 * u, by0 + 42 * u), "Trabajo", font=fcab, fill=(130, 125, 120), anchor="mm")
    pregunta = str(datos.get("pregunta") or "")
    respuesta = str(datos.get("respuesta") or "")
    fuente_txt = str(datos.get("fuente") or "")
    fq = fuente(pal.fuente_texto, int(31 * u))
    fa = fuente(pal.fuente_texto, int(34 * u))
    t_preg = min(1.6, 0.35 + len(pregunta) * 0.03)
    n = int(len(pregunta) * _c(t / t_preg))
    if n > 0:
        w = d.textlength(pregunta, font=fq) + 44 * u
        x1 = bx1 - 40 * u
        d.rounded_rectangle((x1 - w, by0 + 100 * u, x1, by0 + 168 * u), int(34 * u), fill=(52, 49, 49))
        d.text((x1 - w + 22 * u, by0 + 134 * u), pregunta[:n], font=fq, fill=(240, 236, 228), anchor="lm")
    t0 = t_preg + 0.5
    t_resp = _c(dur * 0.38, 1.6, 3.2)
    lin = _lineas(respuesta.split(), (bx1 - bx0) - 120 * u, fa, d, 4)
    total = sum(len(" ".join(l)) + 1 for l in lin)
    k = int(total * _c((t - t0) / t_resp))
    y = by0 + 245 * u
    ult = None
    for l in lin:
        s = " ".join(l)
        c = min(k, len(s))
        if c > 0:
            d.text((bx0 + 60 * u, y), s[:c], font=fa, fill=(226, 221, 214), anchor="lm")
            ult = (bx0 + 60 * u + d.textlength(s[:c], font=fa), y)
        k -= len(s) + 1
        y += 54 * u
        if k <= 0:
            break
    t_fin = t0 + t_resp
    if fuente_txt:
        cit = _back((t - t_fin - 0.2) / 0.45, 2.0) * _c((t - t_fin - 0.2) / 0.1)
        if cit > 0.02:
            ff = fuente(pal.fuente_texto, int(21 * u))
            w2 = (d.textlength(fuente_txt, font=ff) + 36 * u) * cit
            cx, cy = bx0 + 60 * u, y + 14 * u
            d.rounded_rectangle((cx, cy - 23 * u * cit, cx + w2, cy + 23 * u * cit), int(20 * u), fill=_rgb(pal.naranja))
            if cit > 0.7:
                d.text((cx + w2 / 2, cy), fuente_txt, font=ff, fill=_rgb(pal.crema), anchor="mm")
    return im


# ------------------------------------------------------------------ 4) veredicto (bien / mal)
def _pastilla(im, cx, cy, texto, col, ok, esc, f, u):
    if esc <= 0.02:
        return
    d0 = ImageDraw.Draw(im)
    w = int(d0.textlength(texto, font=f)) + int(150 * u)
    h = int(96 * u)
    m = int(20 * u)
    capa = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    sb = int(7 * u)
    d.rounded_rectangle((m + sb, m + sb, m + w + sb, m + h + sb), int(18 * u), fill=(0, 0, 0, 130))
    d.rounded_rectangle((m, m, m + w, m + h), int(18 * u), fill=col, outline=(20, 16, 15), width=max(3, int(5 * u)))
    ix, iy, r = m + int(52 * u), m + h // 2, int(26 * u)
    d.ellipse((ix - r, iy - r, ix + r, iy + r), fill=(255, 251, 242))
    g = max(4, int(8 * u))
    if ok:
        d.line((ix - 12 * u, iy + 1 * u, ix - 3 * u, iy + 11 * u), fill=col, width=g)
        d.line((ix - 3 * u, iy + 11 * u, ix + 13 * u, iy - 10 * u), fill=col, width=g)
    else:
        d.line((ix - 11 * u, iy - 11 * u, ix + 11 * u, iy + 11 * u), fill=col, width=g)
        d.line((ix - 11 * u, iy + 11 * u, ix + 11 * u, iy - 11 * u), fill=col, width=g)
    d.text((ix + 44 * u, iy), texto, font=f, fill=(255, 251, 242), anchor="lm")
    capa = capa.resize((max(1, int(capa.width * esc)), max(1, int(capa.height * esc))), Image.LANCZOS)
    im.paste(capa, (int(cx - capa.width / 2), int(cy - capa.height / 2)), capa)


def f_veredicto(t, dur, W, H, pal, datos, tono="oscuro"):
    u = H / 720.0
    im = Image.new("RGB", (W, H), (16, 14, 14))
    d = ImageDraw.Draw(im, "RGBA")
    ctx = str(datos.get("contexto") or "").strip()
    items = list(datos.get("items") or [])[:3]
    if ctx:
        a = _out(t / 0.4)
        x0, x1, y0, y1 = int(W * 0.2), int(W * 0.8), int(H * 0.10) - (1 - a) * 20 * u, int(H * 0.36)
        d.rounded_rectangle((x0, y0, x1, y1), int(24 * u), fill=(28, 26, 26), outline=(52, 48, 48), width=max(1, int(2 * u)))
        f = fuente(pal.fuente_texto, int(31 * u))
        for i, l in enumerate(_lineas(ctx.split(), x1 - x0 - 70 * u, f, d, 3)):
            d.text((x0 + 36 * u, y0 + 52 * u + i * 40 * u), " ".join(l), font=f, fill=(235, 230, 222), anchor="lm")
        for i, w in enumerate((0.55, 0.62, 0.40)):
            d.rounded_rectangle((x0 + 36 * u, y1 - 100 * u + i * 26 * u, x0 + 36 * u + (x1 - x0 - 72 * u) * w, y1 - 88 * u + i * 26 * u), int(6 * u), fill=(60, 56, 56))
    f = fuente(pal.fuente_titulo, int(46 * u))
    base_y = int(H * 0.56) if ctx else int(H * 0.40)
    sep = int(120 * u)
    for i, it in enumerate(items):
        ini = 0.7 + i * 1.0
        ok = bool(it.get("ok"))
        col = VERDE if ok else _rgb(pal.naranja)
        e = _back((t - ini) / 0.5, 2.2) * _c((t - ini) / 0.15)
        _pastilla(im, W // 2, base_y + i * sep, str(it.get("texto") or ("Sí" if ok else "No")), col, ok, e, f, u)
    return im


# ------------------------------------------------------------------ 5) frase importante
def f_cita(t, dur, W, H, pal, datos, tono="crema"):
    u = H / 720.0
    naranja = tono == "naranja"
    fondo = _rgb(pal.naranja) if naranja else _rgb(pal.crema)
    im = Image.new("RGB", (W, H), fondo)
    d = ImageDraw.Draw(im)
    punto = _mix(pal.naranja, pal.naranja_osc, 0.5) if naranja else _mix(pal.crema, pal.beige_osc, 0.45)
    paso = int(32 * u)
    for gx in range(paso // 2, W, paso):
        for gy in range(paso // 2, H, paso):
            d.ellipse((gx - 2 * u, gy - 2 * u, gx + 2 * u, gy + 2 * u), fill=punto)
    frase = str(datos.get("frase") or "").strip()
    resalta = {_norm(x) for x in (datos.get("resalta") or []) if _norm(x)}
    px = int(0.105 * H)
    f = fuente(pal.fuente_numero or pal.fuente_titulo, px)
    palabras = frase.split()
    while px > 30 and len(_lineas(palabras, W * 0.82, f, d, 4)) > 4:
        px -= 4
        f = fuente(pal.fuente_numero or pal.fuente_titulo, px)
    lin = _lineas(palabras, W * 0.82, f, d, 4)
    alto = int(px * 1.28)
    y = H / 2 - (len(lin) - 1) * alto / 2
    k = 0
    col_txt = _rgb(pal.crema) if naranja else _rgb(pal.tinta)
    marcador = _rgb(pal.tinta) if naranja else _rgb(pal.dorado)
    for linea in lin:
        anchos = [d.textlength(w + " ", font=f) for w in linea]
        x = W / 2 - (sum(anchos) - d.textlength(" ", font=f)) / 2
        for w, ww in zip(linea, anchos):
            al = _out((t - 0.2 - k * 0.14) / 0.3)
            if _norm(w) in resalta:
                pm = _out((t - 0.2 - len(palabras) * 0.14 - 0.1 - 0.2 * (k % 2)) / 0.45)
                if pm > 0:
                    d.rectangle((x - 8 * u, y - px * 0.55, x - 8 * u + (ww - d.textlength(" ", font=f) + 16 * u) * pm, y + px * 0.55), fill=marcador)
            if al > 0:
                c = _mix(fondo, col_txt, al)
                d.text((x, y + (1 - al) * 40 * u), w, font=f, fill=c, anchor="lm")
            x += ww
            k += 1
        y += alto
    return im


_FN = {"capitulo": f_capitulo, "grafica": f_grafica, "chat": f_chat, "veredicto": f_veredicto, "cita": f_cita}


def fotograma(estilo: str, t: float, dur: float, W: int, H: int, pal: Pal, datos: dict, tono: str = "crema") -> Image.Image:
    """El fotograma RGB (W×H) de una escena de estilo en el segundo `t` (de `dur`)."""
    return _FN[estilo](max(0.0, t), dur, W, H, pal, datos or {}, tono).convert("RGB")


# ------------------------------------------------------------------ lo que dice el director: limpiar y comprobar contra lo dicho
def _txt(v, tope: int) -> str:
    s = " ".join(str(v or "").replace("{", "").replace("}", "").replace("\\", "").split())
    if len(s) > tope:
        s = s[:tope].rsplit(" ", 1)[0] if " " in s[:tope] else s[:tope]
    return s


def limpia(it: dict) -> dict | None:
    """Un elemento de `escenas_estilo` del plan → {"estilo", "t", "duracion", "cita", "datos"} limpio, o None si no sirve (le falta lo imprescindible)."""
    try:
        estilo = str(it.get("estilo") or "").strip().lower()
        t = float(it["t"])
    except (KeyError, TypeError, ValueError):
        return None
    if estilo not in ESTILOS:
        return None
    mn, mx, df = DURACIONES[estilo]
    try:
        dur = max(mn, min(mx, float(it.get("duracion") or df)))
    except (TypeError, ValueError):
        dur = df
    cita = _txt(it.get("cita"), 160)
    if len(cita.split()) < 3:
        return None
    datos: dict = {}
    if estilo == "capitulo":
        try:
            n = int(float(it.get("numero")))
        except (TypeError, ValueError):
            return None
        titulo = _txt(it.get("titulo"), 44)
        if not (1 <= n <= 20) or not titulo:
            return None
        datos = {"numero": n, "titulo": titulo}
    elif estilo == "grafica":
        barras = []
        for b in it.get("barras") or []:
            try:
                v = float(b.get("valor"))
            except (AttributeError, TypeError, ValueError):
                continue
            nom, et = _txt(b.get("nombre"), 26), _txt(b.get("etiqueta"), 14)
            if v > 0 and math.isfinite(v) and nom and et:
                barras.append({"nombre": nom, "valor": v, "etiqueta": et})
        titulo = _txt(it.get("titulo"), 70)
        if not (2 <= len(barras) <= 4) or not titulo:
            return None
        datos = {"titulo": titulo, "barras": barras}
    elif estilo == "chat":
        preg, resp = _txt(it.get("pregunta"), 90), _txt(it.get("respuesta"), 190)
        if len(preg) < 8 or len(resp) < 12:
            return None
        datos = {"pregunta": preg, "respuesta": resp, "fuente": _txt(it.get("fuente"), 32)}
    elif estilo == "veredicto":
        its = []
        for x in it.get("items") or []:
            tx = _txt(x.get("texto") if isinstance(x, dict) else "", 24)
            if tx:
                its.append({"texto": tx, "ok": bool(x.get("ok"))})
        its = its[:3]
        if not its:
            return None
        datos = {"contexto": _txt(it.get("contexto"), 110), "items": its}
    else:                                                   # cita
        frase = _txt(it.get("frase"), 96)
        if len(frase.split()) < 4:
            return None
        res = [_txt(x, 20) for x in (it.get("resalta") or []) if _txt(x, 20)][:3]
        pn = {_norm(w) for w in frase.split()}
        datos = {"frase": frase, "resalta": [r for r in res if all(_norm(w) in pn for w in r.split())]}
    return {"estilo": estilo, "t": round(t, 2), "duracion": round(dur, 2), "cita": cita, "datos": datos}


def cita_en_transcripcion(cita: str, words: list[dict], t: float, ventana: float = 45.0) -> bool:
    """¿La `cita` (copiada por la IA) está de verdad en lo que se dice cerca de `t`? Compara las palabras sin tildes ni signos y exige que el 70 %
    de las de la cita aparezca, en el mismo orden, dentro de la ventana — así lo que sale en pantalla nunca es un invento."""
    toks = [x for x in _norm(cita).split() if x]
    if len(toks) < 3:
        return False
    zona = [_norm(w.get("word")) for w in words if t - ventana <= float(w.get("start", 0)) <= t + ventana]
    zona = [z for z in zona if z]
    pos = 0
    hallados = 0
    for tk in toks:
        try:
            j = zona.index(tk, pos)
        except ValueError:
            continue
        hallados += 1
        pos = j + 1
    return hallados / len(toks) >= 0.7


def cifras_dichas(datos: dict, words: list[dict], t: float, ventana: float = 60.0) -> bool:
    """Para una gráfica: al menos una cifra de las etiquetas tiene que aparecer en lo dicho (en dígitos o ya escrita así por Whisper) cerca de `t`."""
    zona = " ".join(str(w.get("word", "")) for w in words if t - ventana <= float(w.get("start", 0)) <= t + ventana)
    zona = zona.replace(".", "").replace(",", "")
    for b in datos.get("barras") or []:
        for n in re.findall(r"\d+", str(b.get("etiqueta", "")).replace(".", "").replace(",", "")):
            if re.search(rf"(?<!\d){re.escape(n)}(?!\d)", zona):
                return True
    return False


# ------------------------------------------------------------------ pruebas (sin ffmpeg ni Gemini)
def prueba() -> list[dict]:
    from . import config
    from .ilustraciones import paleta
    import numpy as np
    out: list[dict] = []

    def caso(n, ok, d=""):
        out.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:400]})

    pal = paleta(dict(config.load().get_path("videos.marca") or {}))
    W, H = 640, 360
    ejemplos = {
        "capitulo": {"numero": 2, "titulo": "Deja que un humano escriba la versión final"},
        "grafica": {"titulo": "Tiempo medio en la página", "barras": [{"nombre": "Humano", "valor": 151, "etiqueta": "2:31"},
                                                                  {"nombre": "IA", "valor": 114, "etiqueta": "1:54"}]},
        "chat": {"pregunta": "¿Cuánto tarda el SEO en dar resultados?", "respuesta": "Normalmente entre 3 y 6 meses según la competencia.", "fuente": "tu-sitio.example"},
        "veredicto": {"contexto": "¿Cuánto dura un tejado?", "items": [{"texto": "Sin citar", "ok": False}, {"texto": "Citado", "ok": True}]},
        "cita": {"frase": "La IA solo cita lo que un experto podría responder", "resalta": ["experto"]},
    }
    for est, datos in ejemplos.items():
        a, b = fotograma(est, 0.2, 6.0, W, H, pal, datos), fotograma(est, 4.5, 6.0, W, H, pal, datos)
        d = float(np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).mean())
        caso(f"{est}: se dibuja al tamaño pedido, cambia con el tiempo y es determinista", a.size == (W, H) and d > 1.0
             and fotograma(est, 4.5, 6.0, W, H, pal, datos).tobytes() == b.tobytes(), f"cambio medio {d:.1f}")
    # la cuenta de la cifra: llega EXACTA a su valor y nunca lo pasa
    caso("la cifra de una barra cuenta hasta su valor exacto (2:31, 14,5 %, 3 meses) y no se pasa por el camino",
         _cuenta("2:31", 1.0) == "2:31" and _cuenta("14,5 %", 1.0) == "14,5 %" and _cuenta("3 meses", 1.0) == "3 meses" and _cuenta("2:31", 0.5) == "1:16"
         and _cuenta("14,5 %", 0.5) in ("7,2 %", "7,3 %") and _cuenta("sin cifra", 0.2) == "", (_cuenta("2:31", 0.5), _cuenta("14,5 %", 0.5)))
    # el empuje de cámara es suave: fotograma a fotograma el zoom crece sin saltos (sub-píxel) y nunca sale del lienzo
    zs = [1.0 + 0.035 * _io(t / 7.0) for t in np.linspace(0, 7, 211)]
    saltos = np.diff(zs)
    caso("el empuje de cámara de la gráfica es lento y sin saltos: ≤ 3,5 % en total y cada fotograma crece una pizca",
         zs[-1] <= 1.0351 and float(saltos.max()) < 0.0015 and float(saltos.min()) >= 0, (zs[-1], float(saltos.max())))
    # limpieza de lo que dice el director
    ok_c = limpia({"estilo": "capitulo", "t": 10, "duracion": 20, "cita": "numero dos deja que un humano", "numero": 2, "titulo": "Deja que un humano escriba"})
    caso("limpia: la duración se ajusta al estilo; lo incompleto (sin cita, sin número, gráfica con una barra, estilo inventado) se tira",
         ok_c and ok_c["duracion"] == 4.5 and limpia({"estilo": "capitulo", "t": 1, "cita": "uno dos", "numero": 2, "titulo": "x"}) is None
         and limpia({"estilo": "capitulo", "t": 1, "cita": "numero dos de la lista", "numero": 99, "titulo": "x"}) is None
         and limpia({"estilo": "grafica", "t": 1, "cita": "el estudio dice que 20", "titulo": "t", "barras": [{"nombre": "a", "valor": 1, "etiqueta": "1"}]}) is None
         and limpia({"estilo": "baile", "t": 1, "cita": "una dos tres cuatro"}) is None, ok_c)
    ch = limpia({"estilo": "chat", "t": 5, "cita": "si le preguntas cuanto tarda el seo", "pregunta": "¿Cuánto tarda el SEO?", "respuesta": "Depende, entre 3 y 6 meses según el caso.", "fuente": ""})
    ve = limpia({"estilo": "veredicto", "t": 5, "cita": "esto si lo cita y esto no", "items": [{"texto": "Lo cita", "ok": True}, {"texto": "No lo cita", "ok": False}] + [{"texto": "x"}] * 3})
    ci = limpia({"estilo": "cita", "t": 5, "cita": "la ia solo cita lo que un experto", "frase": "La IA solo cita lo que un experto podría responder", "resalta": ["experto", "inventada"]})
    caso("limpia: chat completo, veredicto con 3 como mucho y las palabras resaltadas solo si están en la frase",
         ch and ve and len(ve["datos"]["items"]) == 3 and ci and ci["datos"]["resalta"] == ["experto"], (ch, ve, ci))
    # anti-invento: la cita tiene que estar en lo dicho, y una gráfica necesita alguna cifra dicha
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(
        "el estudio dice que los textos humanos se leen dos minutos con treinta y uno y los de IA uno cincuenta y cuatro 2:31 1:54".split())]
    caso("la cita copiada se encuentra en lo dicho (sin tildes ni signos) y una inventada no",
         cita_en_transcripcion("el estudio dice que los textos humanos se leen", words, 3.0)
         and not cita_en_transcripcion("el gato come pescado en la playa de noche", words, 3.0), "")
    caso("una gráfica solo vale si alguna de sus cifras se dice (en dígitos); si no, es un invento",
         cifras_dichas({"barras": [{"etiqueta": "2:31"}]}, words, 3.0) and not cifras_dichas({"barras": [{"etiqueta": "9:99"}]}, words, 3.0), "")
    return out
