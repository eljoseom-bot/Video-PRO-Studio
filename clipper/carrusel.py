"""🖼️ CARRUSEL PARA INSTAGRAM Y TIKTOK a partir de un vídeo ya editado (el autor, 2026-10-05: «un video donde explico cómo solucionar una canonical… que me genere un carrusel de
imágenes que pueda poner en Instagram y TikTok y que resuma todo lo que hice»; 100 % gratis, sin ninguna API de imágenes).

- UNA petición a Flash-Lite (500 al día: no toca las 20 de Flash que editan los vídeos). Ve la transcripción del vídeo YA MONTADO con su reloj (marcado 🖥 donde se ve tu pantalla),
  sus capítulos, el título y las etiquetas, y devuelve la portada, de 3 a 8 pasos (título corto + una frase + el minuto donde se ve + una CITA copiada de lo que dices) y el cierre,
  más el texto de la publicación de Instagram y de TikTok.
- Contra la invención: cada paso lleva una cita COPIADA de la transcripción y el código comprueba que existe (exacta o casi); el título del paso tiene que hablar de cosas que dices en
  el vídeo; el segundo de la captura sale de esa cita. Lo que no se apoya en tu vídeo se descarta. Los números de la portada («7 pasos») los pone el código.
- Las CAPTURAS no gastan IA: ffmpeg saca el fotograma de pro.mp4 en tu PC, apartado de las tarjetas y textos que haya encima (`articulo.momento_captura`) y sin fotogramas vacíos.
- Las DIAPOSITIVAS se dibujan con Pillow en el estilo de la marca (contorno de tinta, sombra dura, Rubik, tus colores y la trama de puntos de las escenas a pantalla completa): portada
  con tu cara (de `mis_caras/`), una por paso con su captura en un marco, y un cierre con la miniatura del vídeo. Instagram 1080×1350 (4:5), TikTok 1080×1920 (9:16, dentro de su zona
  segura) y un PDF para LinkedIn. JPEG (lo único que acepta la API de Instagram si algún día se automatiza).
- Se guarda en `videos/<vídeo>/carrusel/` (carrusel.json, ig/, tiktok/, capturas/, carrusel.pdf): se borra con el 🗑 del vídeo. `rehaz` vuelve a dibujar con textos o capturas
  cambiados a mano SIN llamar a la IA.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import time
import zipfile
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from .util import log

VERSION = 1
SS = 2                                   # se dibuja al doble y se reduce: bordes y letras sin dientes
MIN_PASOS, MAX_PASOS = 3, 8              # (Instagram admite 10 diapositivas: portada + 8 + cierre)
TIPOS = {"pasos": ("PASO", "PASOS"), "errores": ("ERROR", "ERRORES"), "consejos": ("CONSEJO", "CONSEJOS"), "ideas": ("IDEA", "IDEAS")}
FORMATOS = {                              # medidas de diseño (px sobre 1080 de ancho); «zona» = lo que no tapan las interfaces
    "ig": {"nombre": "Instagram", "ancho": 1080, "alto": 1350, "x0": 72, "x1": 1008, "y0": 88, "y1": 1262},
    "tiktok": {"nombre": "TikTok", "ancho": 1080, "alto": 1920, "x0": 72, "x1": 1008, "y0": 200, "y1": 1540},
}
PIE_ALTO = 54                             # la franja del pie (canal · web)
CALIDAD_JPG = 93
_SIN_VIDEO = "no se encuentra en el vídeo"
_NUMEROS = {"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}
_SUSTANTIVOS = (r"(?:pasos?|errores|error|ideas?|consejos?|claves?|formas?|maneras?|razones|trucos?|cosas|herramientas|m[eé]todos?|tips?|motivos|se[ñn]ales|puntos|"
                r"estrategias|reglas|hacks|cambios|ajustes|fallos|tareas)")
_VACIAS = set("de la el los las un una unos unas y o en con por para sin sobre tu tus mi mis su sus que como es son al del lo le se ya mas muy este esta estos estas "
              "esto eso ese esa pero porque cuando donde cual cuales hay hace hacer tiene tienen puede pueden solo tambien entre desde hasta cada todo toda todos todas".split())


# ------------------------------------------------------------------ texto
def _sin_tildes(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s) -> str:
    """Minúsculas, sin tildes ni signos y con las palabras separadas por un solo espacio: para comparar lo que dice la IA con lo que dice el vídeo."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", _sin_tildes(str(s or "").lower()))).strip()


def _txt(v, tope: int) -> str:
    """Una línea limpia, de como mucho `tope` caracteres, cortada en palabra entera (con «…» si se corta)."""
    t = re.sub(r"\s+", " ", str(v or "")).strip()
    if len(t) <= tope:
        return t
    corte = t[: tope - 1]
    if " " in corte:
        corte = corte[: corte.rfind(" ")]
    return corte.rstrip(" ,;:-–—.") + "…"


def _json_de(texto: str):
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1] if i >= 0 and j > i else t)


def _sin_urls(s: str) -> str:
    return re.sub(r"[ \t]*https?://\S+", "", str(s or ""))


def _pie(s, tope: int) -> str:
    """El texto de una publicación: sin URL (en Instagram no funcionan y la IA nunca las escribe), sin hashtags (los pone el código), con sus saltos de línea."""
    t = re.sub(r"(?<![\w#])#\w+", "", _sin_urls(s))
    t = re.sub(r"\s*\d\s+emojis?\.?", "", t, flags=re.I)                     # (la IA a veces copia la instrucción «0-3 emojis» en el texto)
    t = "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in t.replace("\r", "").split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t if len(t) <= tope else _txt(t.replace("\n", " ↵ "), tope).replace(" ↵ ", "\n")


def _ajusta_numero(titulo: str, n: int) -> str:
    """Un «7 pasos» o «siete errores» de la portada se corrige al número REAL de pasos (los números los pone el código, nunca la IA)."""
    def r(m):
        return f"{n}{m.group(2)}"
    return re.sub(rf"\b(\d{{1,2}}|{'|'.join(_NUMEROS)})(\s+{_SUSTANTIVOS})\b", r, titulo, flags=re.I)


# ------------------------------------------------------------------ la petición
_SYSTEM = """Eres el editor de contenido de «{canal}»: conviertes sus vídeos en carruseles para Instagram y TikTok que la gente guarda y comparte.
Respondes SOLO con JSON válido, sin markdown."""


def arma_prompt(d: dict, canal: str, objetivo: str, max_pasos: int = MAX_PASOS) -> str:
    L = [f"Convierte este vídeo de YouTube de «{canal}» en un CARRUSEL de imágenes para Instagram y TikTok: una portada, entre {MIN_PASOS} y {max_pasos} diapositivas (una por "
         "paso o idea, cada una con una captura REAL del vídeo) y un cierre. Quien solo vea el carrusel tiene que llevarse lo esencial del vídeo y querer verlo completo.", "",
         f"Objetivo del negocio (tenlo en cuenta, sin vender en cada frase): {objetivo}", "",
         "REGLAS:",
         "- Solo lo que dice el vídeo: no inventes pasos, datos, cifras ni herramientas. Una cifra que él da como estimación va como aproximada.",
         "- La transcripción es automática y a veces escribe mal un nombre: usa SIEMPRE cómo sale en el título, los capítulos o las etiquetas.",
         "- «tipo»: \"pasos\" si el vídeo enseña a hacer algo paso a paso, \"errores\" si trata de lo que se hace mal, \"consejos\" si son recomendaciones, \"ideas\" si son ideas o "
         "conceptos sueltos.",
         f"- «pasos»: de {MIN_PASOS} a {max_pasos}, en el ORDEN del vídeo y sin repetir ninguno. Si el vídeo tiene capítulos con pasos, úsalos de guía. Lo muy pequeño se junta con otro; lo que no "
         "aporta (saludo, intro, despedida, venta) se deja fuera.",
         "- Cada paso: «titulo» de 2 a 6 palabras, concreto («Revisa el sitemap», «Error: bloquear todo en robots.txt»), SIN número ni «Paso 1» delante (el diseño lo pone); «texto»: 1 o 2 "
         "frases cortas (18 palabras como mucho en total) con lo que hay que hacer y por qué, en su voz y tuteando, sin relleno.",
         "- «minuto»: el [m:ss] EXACTO, tal como sale entre corchetes, de UNA línea marcada con 🖥 donde se ve en pantalla lo que explica ese paso (ahí se saca su captura). Si en ninguna "
         "línea se ve en pantalla, el [m:ss] de la línea donde empieza a hablar de ello.",
         "- «cita»: de 4 a 12 palabras COPIADAS tal cual, seguidas, de la transcripción de la línea en que se basa ese paso (sin cambiar ninguna palabra). El programa comprueba que existen: "
         "un paso cuya cita no está en el vídeo se descarta.",
         "- «portada»: «titulo» de 3 a 8 palabras que prometa el resultado del vídeo y enganche, SIN cifras (el diseño pone el número de pasos) y sin prometer lo que el vídeo no cumple; "
         "«subtitulo» de hasta 10 palabras (para quién es o qué consigues).",
         "- «cierre»: «titulo» de 2 a 5 palabras que pida guardar el carrusel (p. ej. «¿Te sirvió? Guárdalo») y «texto» de hasta 16 palabras que invite a ver el vídeo completo en YouTube.",
         "- «pie_instagram»: el texto de la publicación (700 caracteres como mucho): la 1.ª línea con gancho, 3-5 líneas con lo más útil (una por paso importante, cada una en su línea), y una "
         "línea que pida guardar y diga que el vídeo completo está en YouTube («enlace en mi perfil»: en Instagram los enlaces no funcionan). SIN hashtags (los pone el programa). 0-3 emojis.",
         "- «tiktok_titulo»: 80 caracteres como mucho, con gancho. «tiktok_texto»: 200 caracteres como mucho, 1-2 frases. Sin hashtags.",
         "- Nunca escribas URLs.", "",
         f"DATOS DEL VÍDEO: «{d['titulo']}» · etiquetas: {', '.join(d['tags']) or '—'}", "Capítulos:", d["capitulos"] or "(sin capítulos)", "",
         "TRANSCRIPCIÓN del vídeo ya editado ([m:ss] = reloj del vídeo; 🖥 = en ese momento se ve su pantalla):"]
    from .articulo import _mmss
    L += [f"[{_mmss(f['t'])}]{' 🖥' if f['pantalla'] else ''} {f['texto']}" for f in d["frases"]]
    L += ["", 'Responde SOLO con JSON: {"tipo", "portada": {"titulo", "subtitulo"}, "pasos": [{"titulo", "texto", "minuto", "cita"}], "cierre": {"titulo", "texto"}, '
              '"pie_instagram", "tiktok_titulo", "tiktok_texto"}']
    return "\n".join(L)


_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from pydantic import BaseModel

            class Paso(BaseModel):
                titulo: str
                texto: str
                minuto: str
                cita: str

            class Portada(BaseModel):
                titulo: str
                subtitulo: str

            class Cierre(BaseModel):
                titulo: str
                texto: str

            class Carrusel(BaseModel):
                tipo: str
                portada: Portada
                pasos: list[Paso]
                cierre: Cierre
                pie_instagram: str
                tiktok_titulo: str
                tiktok_texto: str

            _ESQUEMA = Carrusel
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQUEMA = False
    return _ESQUEMA or None


# ------------------------------------------------------------------ comprobar lo que dice la IA contra el vídeo
def _desde(ventana: list[str], q: list[str]) -> int:
    n = len(q)
    for k in range(len(ventana) - n + 1):
        if ventana[k:k + n] == q:
            return k
    return -1


def ubica_cita(cita: str, frases: list[dict]) -> int:
    """El índice de la frase del vídeo donde empieza esa cita (copiada de la transcripción), o -1 si no está. Exacta (sin tildes ni signos, y puede cruzar a la frase
    siguiente) y, si no, CASI exacta: al menos el 75 % de sus palabras seguidas."""
    q = _norm(cita).split()
    if len(q) < 3:
        return -1
    textos = [_norm(f["texto"]).split() for f in frases]
    mejor, mejor_n = -1, 0
    for i, w in enumerate(textos):
        ventana = w + (textos[i + 1] if i + 1 < len(textos) else [])
        k = _desde(ventana, q)
        if 0 <= k < len(w):
            return i
        m = SequenceMatcher(None, q, ventana, autojunk=False).find_longest_match(0, len(q), 0, len(ventana))
        if m.size >= max(3, 0.75 * len(q)) and m.b < len(w) and m.size > mejor_n:
            mejor, mejor_n = i, m.size
    return mejor


def _a_segundos(v) -> float | None:
    """«2:05», «0:02:05» o un número de segundos -> segundos (None si no se entiende)."""
    t = str(v if v is not None else "")
    m = re.search(r"(\d+):(\d{2})(?::(\d{2}))?", t)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        return float(a * 3600 + b * 60 + int(c)) if c is not None else float(a * 60 + b)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:s|seg)?\s*", t)
    return float(m.group(1)) if m else None


def _vocabulario(frases: list[dict]) -> set[str]:
    """Las raíces (5 primeras letras) de lo que se dice en el vídeo: para comprobar que el título de un paso habla de cosas que sí se dicen."""
    return {w[:5] for f in frases for w in _norm(f["texto"]).split() if len(w) >= 4}


def _habla_del_video(titulo: str, vocab: set[str]) -> bool:
    pal = [w for w in _norm(titulo).split() if len(w) >= 4 and w not in _VACIAS]
    if not pal:
        return True
    return sum(1 for w in pal if w[:5] in vocab) >= max(1, round(0.4 * len(pal)))


def parsea(texto: str, d: dict, max_pasos: int = MAX_PASOS) -> dict:
    """El JSON de la IA -> el carrusel LIMPIO: cada paso con su cita comprobada contra el vídeo (el que no se apoya en nada que digas se descarta), ordenados como en el vídeo, sin
    dos pasos de la misma frase, el número de la portada corregido y los textos sin URL ni hashtags. Lanza si quedan menos de 3 pasos (así se prueba otra vez / con otro modelo)."""
    x = _json_de(texto)
    if not isinstance(x, dict):
        raise ValueError("la respuesta no es un carrusel")
    frases, vocab = d["frases"], _vocabulario(d["frases"])
    pasos: list[dict] = []
    usadas: set[int] = set()
    descartados = 0
    for p in x.get("pasos") or []:
        if not isinstance(p, dict):
            continue
        titulo, cuerpo = _txt(p.get("titulo"), 70).strip(" .:"), _txt(p.get("texto"), 200)
        if not titulo or not cuerpo:
            continue
        i = ubica_cita(p.get("cita"), frases)
        if i < 0 or not _habla_del_video(titulo, vocab):
            descartados += 1
            continue
        if i in usadas:                                        # dos pasos de la misma frase son el mismo
            continue
        usadas.add(i)
        t_cita = float(frases[i]["t"])
        if d.get("texto_mode"):                                # de un artículo: «P12» = la frase 12 (el «segundo» es de mentira: 4 s por frase)
            m = re.search(r"(\d+)", str(p.get("minuto") or ""))
            k = int(m.group(1)) - 1 if m else -1
            t = float(frases[k]["t"]) if 0 <= k < len(frases) and abs(k - i) <= 6 else t_cita
        else:
            seg = _a_segundos(p.get("minuto"))
            t = seg if seg is not None and 0 < seg < d["duracion"] and abs(seg - t_cita) <= 25 else t_cita    # el minuto que dice la IA, si cuadra con lo que cita
        paso = {"titulo": titulo, "texto": cuerpo, "t": round(t, 1), "cita": _txt(p.get("cita"), 200)}
        if d.get("texto_mode"):                                # el símbolo de tu biblioteca que representa el paso (solo claves que existen)
            from .ilus_escenas import CATALOGO
            ic = str(p.get("icono") or "").strip().lower()
            paso["icono"] = ic if ic in CATALOGO else ""
        pasos.append(paso)
    pasos.sort(key=lambda s: s["t"])
    limpios: list[dict] = []
    for s in pasos:                                            # (dos casi en el mismo segundo: se queda el primero)
        if not limpios or s["t"] - limpios[-1]["t"] >= 3.0:
            limpios.append(s)
    pasos = limpios[:max_pasos]
    if len(pasos) < MIN_PASOS:
        raise ValueError(f"el carrusel salió con solo {len(pasos)} pasos que se apoyen en el vídeo")
    for k, s in enumerate(pasos, 1):
        s["id"] = f"p{k}"
    tipo = str(x.get("tipo") or "").strip().lower()
    tipo = tipo if tipo in TIPOS else "pasos"
    por = x.get("portada") if isinstance(x.get("portada"), dict) else {}
    titulo_p = _ajusta_numero(_txt(por.get("titulo"), 90), len(pasos)) or _txt(d["titulo"], 70)
    sub = _txt(por.get("subtitulo"), 110)
    cie = x.get("cierre") if isinstance(x.get("cierre"), dict) else {}
    de_texto = bool(d.get("texto_mode"))
    pie_ig = _pie(x.get("pie_instagram"), 900) or f"{titulo_p}\n\n{sub}".strip() + ("\n\nGuárdalo para cuando lo necesites y lee el artículo completo en mi web (enlace en mi perfil)." if de_texto
                                                                                     else "\n\nGuárdalo para cuando lo necesites y mira el vídeo completo en YouTube (enlace en mi perfil).")
    return {"tipo": tipo, "portada": {"titulo": titulo_p, "subtitulo": sub}, "pasos": pasos,
            "cierre": {"titulo": _txt(cie.get("titulo"), 50) or "¿Te sirvió? Guárdalo",
                       "texto": _txt(cie.get("texto"), 130) or ("El artículo completo, con todo el detalle, está en mi web." if de_texto else "El vídeo completo, con todo el detalle, está en mi canal de YouTube.")},
            "pie_instagram": pie_ig, "tiktok_titulo": _txt(_sin_urls(x.get("tiktok_titulo")), 90) or _txt(titulo_p, 90),
            "tiktok_texto": _pie(x.get("tiktok_texto"), 220).replace("\n", " "), "descartados": descartados}


# ------------------------------------------------------------------ dibujo (Pillow, el estilo de la marca)
def _recorta_palabras(palabras: list[str], f, ancho: float, medir) -> list[str]:
    lineas, actual = [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if actual and medir(prueba, f) > ancho:
            lineas.append(actual)
            actual = p
        else:
            actual = prueba
    if actual:
        lineas.append(actual)
    return lineas


class _Hoja:
    """Una diapositiva en construcción: se dibuja al doble (`SS`) y `final()` la reduce. Todo se mide en unidades de diseño (un lienzo de 1080 de ancho)."""

    def __init__(self, fmt: dict, pal, tono: str):
        from . import escena_completa
        self.fmt, self.pal = fmt, pal
        self.W, self.H = fmt["ancho"], fmt["alto"]
        self.im = escena_completa.fondo(self.W * SS, self.H * SS, pal, tono).copy().convert("RGB")
        self.d = ImageDraw.Draw(self.im)
        self.tinta, self.crema, self.naranja, self.dorado, self.beige = (pal.tinta[:3], pal.crema[:3], pal.naranja[:3], pal.dorado[:3], pal.beige[:3])
        self.cajas: list[tuple[str, tuple]] = []               # lo dibujado (para las pruebas): nombre -> (x0, y0, x1, y1)

    # -- utilidades
    def px(self, v: float) -> int:
        return int(round(v * SS))

    def fuente(self, ruta: str, tam: float):
        from .ilus_motor import fuente
        return fuente(ruta, max(1, self.px(tam)))

    def ancho(self, s: str, f) -> float:
        return self.d.textlength(s, font=f) / SS

    def anota(self, nombre: str, caja) -> None:
        self.cajas.append((nombre, tuple(round(float(v), 1) for v in caja)))

    # -- texto ajustado a una caja
    def _equilibra(self, palabras, f, ancho, n):
        lo, hi = max(self.ancho(p, f) for p in palabras), ancho
        mejor = _recorta_palabras(palabras, f, hi, self.ancho)
        for _ in range(12):
            mid = (lo + hi) / 2
            c = _recorta_palabras(palabras, f, mid, self.ancho)
            if len(c) <= n:
                mejor, hi = c, mid
            else:
                lo = mid
        return mejor

    def bloque(self, texto: str, ruta: str, ancho: float, alto: float, tam_max: float, tam_min: float, lineas_max: int, inter: float = 1.14, mayus: bool = False):
        """El texto ajustado a la caja (ancho × alto): baja el cuerpo de letra hasta que quepa en como mucho `lineas_max` líneas PAREJAS; si no cabe ni con la letra más
        pequeña, lo corta con «…». -> {"f", "lineas", "tam", "pitch", "alto", "asc", "des", "ancho"}"""
        palabras = (texto.upper() if mayus else texto).split()
        if not palabras:
            return None
        tam = float(tam_max)
        while True:
            f = self.fuente(ruta, tam)
            lineas = _recorta_palabras(palabras, f, ancho, self.ancho)
            if len(lineas) > 1:
                lineas = self._equilibra(palabras, f, ancho, len(lineas))
            asc, des = f.getmetrics()
            asc, des, pitch = asc / SS, des / SS, tam * inter
            alto_real = (len(lineas) - 1) * pitch + asc + des
            w = max(self.ancho(l, f) for l in lineas)
            if (len(lineas) <= lineas_max and alto_real <= alto and w <= ancho) or tam <= tam_min:
                break
            tam -= 2
        cabe_lineas = max(1, min(lineas_max, int((alto - asc - des) // pitch) + 1))
        if len(lineas) > cabe_lineas or w > ancho:             # no cabe ni con la letra más pequeña: se corta
            lineas = _recorta_palabras(palabras, f, ancho, self.ancho)[:cabe_lineas]
            ultima = lineas[-1].split()
            while ultima and self.ancho(" ".join(ultima) + "…", f) > ancho:
                ultima.pop()
            lineas[-1] = (" ".join(ultima) + "…") if ultima else "…"
            alto_real = (len(lineas) - 1) * pitch + asc + des
            w = max(self.ancho(l, f) for l in lineas)
        return {"f": f, "lineas": lineas, "tam": tam, "pitch": pitch, "alto": alto_real, "asc": asc, "des": des, "ancho": w}

    def dibuja(self, b: dict, x: float, y: float, color, ancho_caja: float | None = None, centro: bool = False, trazo: float = 0, sombra: float = 0, realce=None,
               color_realce=None, nombre: str = "") -> tuple:
        """Dibuja el bloque con su borde superior en `y`. `realce`: índices de PALABRAS (contadas en todo el texto) que van de otro color. -> la caja que ocupa."""
        f, idx = b["f"], 0
        ancho_caja = ancho_caja if ancho_caja is not None else b["ancho"]
        ancho_esp = self.ancho(" ", f) + trazo * 1.7                # (con el contorno grueso las palabras se pegaban)
        for k, linea in enumerate(b["lineas"]):
            base = y + b["asc"] + k * b["pitch"]
            palabras = linea.split()
            x_ini = x + (ancho_caja - self.ancho(linea, f)) / 2 if centro else x
            cx = x_ini
            for p in palabras:
                c = color_realce if realce and idx in realce else color
                if sombra:
                    self.d.text((self.px(cx + sombra), self.px(base + sombra)), p, font=f, fill=self.tinta, anchor="ls", stroke_width=self.px(trazo), stroke_fill=self.tinta)
                self.d.text((self.px(cx), self.px(base)), p, font=f, fill=c, anchor="ls", stroke_width=self.px(trazo), stroke_fill=self.tinta)
                cx += self.ancho(p, f) + ancho_esp
                idx += 1
        caja = (x, y, x + max(b["ancho"], 1), y + b["alto"]) if not centro else (x + (ancho_caja - b["ancho"]) / 2, y, x + (ancho_caja + b["ancho"]) / 2, y + b["alto"])
        if nombre:
            self.anota(nombre, caja)
        return caja

    # -- formas
    def tarjeta(self, x0, y0, x1, y1, relleno, radio=16, borde=5, sombra=10) -> None:
        px, d = self.px, self.d
        if sombra:
            d.rounded_rectangle((px(x0 + sombra), px(y0 + sombra), px(x1 + sombra), px(y1 + sombra)), radius=px(radio), fill=self.tinta)
        d.rounded_rectangle((px(x0), px(y0), px(x1), px(y1)), radius=px(radio), fill=relleno, outline=self.tinta, width=px(borde))

    def tarjeta_imagen(self, x0, y0, x1, y1, imagen: Image.Image, radio=16, borde=5, sombra=10) -> None:
        """La imagen dentro de un marco con contorno de tinta y sombra dura (esquinas redondeadas)."""
        px = self.px
        self.tarjeta(x0, y0, x1, y1, self.crema, radio, borde, sombra)
        ix0, iy0, ix1, iy1 = px(x0 + borde), px(y0 + borde), px(x1 - borde), px(y1 - borde)
        w, h = ix1 - ix0, iy1 - iy0
        im = imagen.convert("RGB").resize((w, h), Image.LANCZOS)
        mascara = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mascara).rounded_rectangle((0, 0, w - 1, h - 1), radius=max(1, px(radio - borde)), fill=255)
        self.im.paste(im, (ix0, iy0), mascara)
        self.d.rounded_rectangle((px(x0), px(y0), px(x1), px(y1)), radius=px(radio), outline=self.tinta, width=px(borde))

    def pastilla(self, x, y, texto: str, ruta: str, tam: float, fondo, color, pad_x=26, pad_y=13, borde=4, sombra=7, derecha: bool = False, nombre: str = "",
                 extra_izq: float = 0, extra_der: float = 0) -> tuple:
        """Una pastilla con el texto centrado; `extra_izq`/`extra_der` dejan sitio dentro para un icono (flecha, triángulo)."""
        f = self.fuente(ruta, tam)
        bb = self.d.textbbox((0, 0), texto, font=f, anchor="ls")
        tw, th = (bb[2] - bb[0]) / SS, (bb[3] - bb[1]) / SS
        w, h = tw + 2 * pad_x + extra_izq + extra_der, th + 2 * pad_y
        x0 = x - w if derecha else x
        self.tarjeta(x0, y, x0 + w, y + h, fondo, radio=h / 2, borde=borde, sombra=sombra)
        self.d.text((self.px(x0 + extra_izq + (w - extra_izq - extra_der) / 2), self.px(y + h / 2 - (bb[1] + bb[3]) / 2 / SS)), texto, font=f, fill=color, anchor="ms")
        if nombre:
            self.anota(nombre, (x0, y, x0 + w + sombra, y + h + sombra))
        return (x0, y, x0 + w, y + h)

    def poligono(self, pts, relleno, sombra: float = 0, borde: float = 3) -> None:
        px, d = self.px, self.d
        if sombra:
            d.polygon([(px(a + sombra), px(b + sombra)) for a, b in pts], fill=self.tinta)
        d.polygon([(px(a), px(b)) for a, b in pts], fill=relleno)
        d.line([(px(a), px(b)) for a, b in pts + [pts[0]]], fill=self.tinta, width=px(borde), joint="curve")

    def flecha(self, x, y, w, h, relleno, sombra: float = 0) -> None:
        self.poligono([(x, y + h * .34), (x + w * .56, y + h * .34), (x + w * .56, y), (x + w, y + h / 2), (x + w * .56, y + h), (x + w * .56, y + h * .66),
                       (x, y + h * .66)], relleno, sombra)

    def marcador(self, x, y, w, h, relleno, sombra: float = 8) -> None:
        self.poligono([(x, y), (x + w, y), (x + w, y + h), (x + w / 2, y + h * .72), (x, y + h)], relleno, sombra, borde=5)

    def pie(self, nombre: str, web: str, linea: bool = True, derecha: bool = True, margen_der: float = 0, logo=None) -> None:
        """El pie de cada diapositiva: el canal a la izquierda y la web a la derecha. Con `logo` (tu retrato en círculo, ya a tamaño de trabajo) va a la izquierda del todo y el
        nombre del canal se corre a su lado."""
        z, p = self.fmt, self.pal
        y_base = z["y1"] - 12
        x_nom = z["x0"]
        if linea:
            self.d.line([(self.px(z["x0"]), self.px(z["y1"] - PIE_ALTO + 4)), (self.px(z["x1"]), self.px(z["y1"] - PIE_ALTO + 4))], fill=self.tinta, width=self.px(3))
        f1, f2 = self.fuente(p.fuente_numero, 30), self.fuente(p.fuente_texto, 27)
        if logo is not None:
            xl, yl = z["x0"], z["y1"] - 4 - LOGO_D
            self.im.paste(logo, (self.px(xl), self.px(yl)), logo)
            self.d.ellipse([self.px(xl), self.px(yl), self.px(xl + LOGO_D) - 1, self.px(yl + LOGO_D) - 1], outline=self.tinta, width=self.px(3))
            self.anota("logo", (xl, yl, xl + LOGO_D, yl + LOGO_D))
            x_nom = z["x0"] + LOGO_D + 14
        if nombre:
            self.d.text((self.px(x_nom), self.px(y_base)), nombre, font=f1, fill=self.tinta, anchor="ls")
            self.anota("pie", (x_nom, y_base - 30, x_nom + self.ancho(nombre, f1), y_base + 6))
        if web and derecha:
            self.d.text((self.px(z["x1"] - margen_der), self.px(y_base)), web, font=f2, fill=p.gris[:3], anchor="rs")

    def final(self) -> Image.Image:
        return self.im.resize((self.W, self.H), Image.LANCZOS)


def _pal_de(marca: dict):
    from .ilustraciones import paleta
    return paleta(marca)


def _ratio(img: Image.Image | None) -> float:
    if img is None or not img.height:
        return 16 / 9
    return max(1.2, min(2.3, img.width / img.height))


PESOS_CARA = {"feliz": 70, "sorpresa": 20, "pensando": 10}      # la cara de la portada (el autor, 11-oct-2026: «el 70 % feliz, pero unas que prueben otras»)
# La cara según el CONTEXTO del carrusel (el autor, 13-oct-2026: «que no solo salga mi cara sonriendo sino las demás caras dependiendo del contexto»): `tipo` lo decide la IA al leer el contenido.
# Los «pasos» usan los pesos de siempre (`carrusel.caras`); los demás, estos (`carrusel.caras_por_tipo.<tipo>` en config.yaml los cambia): de errores no se sonríe.
PESOS_CARA_TIPO = {"consejos": {"feliz": 55, "pensando": 25, "sorpresa": 20},
                   "ideas": {"pensando": 40, "sorpresa": 30, "feliz": 30},
                   "errores": {"decepcion": 30, "what": 25, "asco": 15, "pensando": 12, "sorpresa": 10, "triste": 8}}
LOGO_D = 60                                                      # diámetro (px de diseño) del sello con tu retrato en el pie


def elige_gesto(cfg, dir_caras=None, rng=None, tipo: str | None = None) -> str:
    """El gesto de tu cara para la portada de ESTE carrusel, al azar con pesos entre los gestos que de verdad tienes en `mis_caras/` (las de brazos abiertos también: en la portada se
    reducen para no tapar el título). Los pesos dependen del CONTEXTO (`tipo`: pasos → `carrusel.caras`, por defecto 70 % feliz, 20 % sorpresa, 10 % pensando; consejos, ideas y errores →
    `PESOS_CARA_TIPO` / `carrusel.caras_por_tipo.<tipo>`). Sin ninguno disponible, el de siempre (`cara_defecto`)."""
    import random

    from . import miniaturas_ia as mia
    defecto = str(mia.parametros(cfg).get("cara_defecto") or "feliz")
    try:
        crudo = cfg.get_path("carrusel.caras") if cfg is not None else None
        if tipo and tipo != "pasos":
            crudo = (cfg.get_path(f"carrusel.caras_por_tipo.{tipo}") if cfg is not None else None) or PESOS_CARA_TIPO.get(tipo) or crudo
        pesos = {str(k): float(v) for k, v in (crudo or PESOS_CARA).items() if float(v) > 0}
    except (TypeError, ValueError, AttributeError):
        pesos = dict(PESOS_CARA)
    try:
        hay = {c["gesto"] for c in mia.caras(dir_caras, cfg)}
    except Exception:  # noqa: BLE001
        hay = set()
    pool = {g: w for g, w in pesos.items() if mia.gesto_de(g)[0] in hay}
    if not pool:
        return defecto
    return (rng or random).choices(list(pool), weights=list(pool.values()))[0]


_LOGO: dict = {}


def _logo_pie(cfg):
    """Tu retrato (`carrusel.logo`, por defecto mi_marca/retrato.webp: lo subes en el asistente de tu canal) recortado en círculo y a tamaño de trabajo, o None si no está."""
    try:
        from PIL import ImageChops
        ruta = str((cfg.get_path("carrusel.logo") if cfg is not None else None) or "mi_marca/retrato.webp")
        p = Path(ruta)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / ruta
        clave = (str(p), p.stat().st_mtime)
        if clave not in _LOGO:
            im = Image.open(p).convert("RGBA")
            lado = round(LOGO_D * SS)
            im = ImageOps.fit(im, (lado, lado), Image.LANCZOS)
            mascara = Image.new("L", (lado, lado), 0)
            ImageDraw.Draw(mascara).ellipse([0, 0, lado - 1, lado - 1], fill=255)
            im.putalpha(ImageChops.multiply(im.getchannel("A"), mascara))
            _LOGO.clear()
            _LOGO[clave] = im
        return _LOGO[clave]
    except Exception as exc:                                   # noqa: BLE001 — sin el sello, el carrusel sale igual
        log(f"AVISO: carrusel sin tu retrato en el pie ({str(exc)[:100]}).")
        return None


def _cara_para_portada(cfg, alto: float, dir_caras=None, gesto: str | None = None):
    """Tu cara (recorte de `mis_caras/`) lista para la portada: la que no abre los brazos, de las del gesto por defecto y mirando hacia el título, con el contorno blanco de tu
    truco de Photoshop, a `alto` px de diseño. -> (imagen RGBA al doble de tamaño, relleno del contorno) o None si no hay caras (la portada sale sin ella)."""
    try:
        from . import miniaturas_ia as mia
        lista = mia.caras(dir_caras, cfg)
        if not lista:
            return None
        defecto = str(mia.parametros(cfg).get("cara_defecto") or "feliz")
        cara, espejo = mia.elige_cara(lista, gesto or defecto, "derecha", defecto)
        if cara is None:
            return None
        im = Image.open(cara["ruta"]).convert("RGBA").crop(cara["caja"])
        if espejo:
            im = ImageOps.mirror(im)
        alto_px = max(8, int(round(alto * SS)))
        im = im.resize((max(8, round(im.width * alto_px / im.height)), alto_px), Image.LANCZOS)
        return mia._con_contorno(im, int(round(7 * SS)))
    except Exception as exc:                                   # noqa: BLE001 — sin cara, la portada sale igual
        log(f"AVISO: carrusel sin tu cara en la portada ({str(exc)[:120]}).")
        return None


def dibuja_portada(c: dict, fmt: dict, marca: dict, cara=None) -> _Hoja:
    """La portada: fondo naranja con trama, el número de pasos, el título grande (crema con contorno de tinta y sombra dura, la última palabra en dorado), el subtítulo en una
    tarjeta, tu cara abajo a la derecha (o un número grande si no hay) y «DESLIZA»."""
    pal = _pal_de(marca)
    h = _Hoja(fmt, pal, "naranja")
    x0, x1, y0, y1 = fmt["x0"], fmt["x1"], fmt["y0"], fmt["y1"]
    zona_w = x1 - x0
    n = len(c["pasos"])
    etiqueta = f"{n} {TIPOS.get(c.get('tipo'), TIPOS['pasos'])[1]}"
    chip = h.pastilla(x0, y0, etiqueta, pal.fuente_numero, 36, h.dorado, h.tinta, nombre="chip")
    y_t = chip[3] + 40
    alto_t = fmt["alto"] * (0.31 if fmt["alto"] > 1500 else 0.33)
    b_t = h.bloque(c["portada"]["titulo"], pal.fuente_numero, zona_w, alto_t, 124 if fmt["alto"] < 1500 else 128, 64, 5, inter=1.04, mayus=True)
    palabras = c["portada"]["titulo"].upper().split()
    con_cifra = {i for i, p in enumerate(palabras) if any(ch.isdigit() for ch in p)}
    realce = con_cifra or {len(palabras) - 1}
    trazo = max(3.0, b_t["tam"] * 0.075)
    caja_t = h.dibuja(b_t, x0, y_t, h.crema, zona_w, False, trazo=trazo, sombra=max(4.0, b_t["tam"] * 0.07), realce=realce, color_realce=h.dorado, nombre="titulo")
    y_s = caja_t[3] + 34
    b_s = h.bloque(c["portada"].get("subtitulo") or "", pal.fuente_titulo, zona_w - 64, 190, 44, 30, 3, inter=1.2) if c["portada"].get("subtitulo") else None
    y_fondo = y1 - PIE_ALTO - 22
    if b_s:
        alto_c = b_s["alto"] + 52
        h.tarjeta(x0, y_s, x1, y_s + alto_c, h.crema, radio=18, borde=5, sombra=9)
        h.dibuja(b_s, x0 + 32, y_s + 26, h.tinta, zona_w - 64, False, nombre="subtitulo")
        y_cara = y_s + alto_c + 30
    else:
        y_cara = y_s
    # «DESLIZA» abajo a la izquierda
    des = h.pastilla(x0, y_fondo - 64, "DESLIZA", pal.fuente_numero, 34, h.tinta, h.crema, pad_x=28, pad_y=14, extra_der=64, nombre="desliza")
    h.flecha(des[2] - 28 - 40, des[1] + (des[3] - des[1]) / 2 - 13, 40, 26, h.dorado)
    # tu cara abajo a la derecha (o el número grande)
    hueco = fmt["alto"] - y_cara
    if cara is not None:
        im, pad = cara
        alto_f = max(120.0, min(hueco, (im.height - 2 * pad) / SS * 1.0))
        ancho_max = (x1 - x0) * 0.55                           # (una foto ancha, con los brazos arriba, no puede pasar de aquí: taparía el título)
        escala = min(1.0, alto_f / max(1.0, (im.height - 2 * pad) / SS), ancho_max / max(1.0, (im.width - 2 * pad) / SS))
        if escala < 1.0:
            im = im.resize((max(1, round(im.width * escala)), max(1, round(im.height * escala))), Image.LANCZOS)
            pad = round(pad * escala)
        ancho_f, alto_im = im.width / SS, im.height / SS
        px_f = h.px(x1 + 26 - (ancho_f - 2 * pad / SS))
        py_f = h.px(fmt["alto"] - (alto_im - pad / SS))
        h.im.paste(im, (px_f, py_f), im)
        h.anota("cara", (x1 + 26 - (ancho_f - 2 * pad / SS), fmt["alto"] - (alto_im - 2 * pad / SS), x1 + 26, fmt["alto"]))
    else:
        r = min(hueco - 40, 420) / 2
        cx, cy = x1 - r - 10, fmt["alto"] - PIE_ALTO - 40 - r
        h.tarjeta(cx - r, cy - r, cx + r, cy + r, h.crema, radio=r, borde=6, sombra=12)
        fn = h.fuente(pal.fuente_numero, r * 1.25)
        bb = h.d.textbbox((0, 0), str(n), font=fn, anchor="ls")
        h.d.text((h.px(cx), h.px(cy - (bb[1] + bb[3]) / 2 / SS - r * 0.12)), str(n), font=fn, fill=h.naranja, anchor="ms", stroke_width=h.px(5), stroke_fill=h.tinta)
        fp = h.fuente(pal.fuente_numero, r * 0.28)
        h.d.text((h.px(cx), h.px(cy + r * 0.62)), TIPOS.get(c.get("tipo"), TIPOS["pasos"])[1], font=fp, fill=h.tinta, anchor="ms")
        h.anota("cara", (cx - r, cy - r, cx + r, cy + r))
    h.pie(c.get("canal", ""), c.get("web", ""), linea=False, derecha=False)
    return h


def dibuja_paso(c: dict, k: int, fmt: dict, marca: dict, captura: Image.Image | None, logo=None) -> _Hoja:
    """La diapositiva del paso `k` (0 = el primero): su número en una pastilla, el título, la captura en un marco y la frase; sin captura, el texto en una tarjeta grande.
    El título va siempre arriba y la captura en el mismo sitio (dejando sitio a 2 líneas de título): al pasar de una diapositiva a otra no salta nada."""
    pal = _pal_de(marca)
    h = _Hoja(fmt, pal, "crema")
    s, n = c["pasos"][k], len(c["pasos"])
    x0, x1, y0, y1 = fmt["x0"], fmt["x1"], fmt["y0"], fmt["y1"]
    zona_w = x1 - x0
    grande = fmt["alto"] > 1500                                # (TikTok: más alto, letras más grandes)
    etiqueta = f"{TIPOS.get(c.get('tipo'), TIPOS['pasos'])[0]} {k + 1}"
    chip = h.pastilla(x0, y0, etiqueta, pal.fuente_numero, 38, h.naranja, h.crema, nombre="chip")
    fcont = h.fuente(pal.fuente_numero, 36)
    h.d.text((h.px(x1), h.px(chip[1] + (chip[3] - chip[1]) / 2 + 12)), f"{k + 1}/{n}", font=fcont, fill=h.tinta, anchor="rs")
    arriba, abajo = chip[3] + 38, y1 - PIE_ALTO - 26
    disp = abajo - arriba
    if captura is not None:
        b_t = h.bloque(s["titulo"], pal.fuente_titulo, zona_w, 300 if grande else 240, 84 if grande else 70, 50 if grande else 44, 3, inter=1.1)
        b_x = h.bloque(s["texto"], pal.fuente_texto, zona_w, 300 if grande else 230, 49 if grande else 41, 34 if grande else 30, 6 if grande else 5, inter=1.3)
        g1, g2 = 32, 38
        slot_t = max(b_t["alto"], b_t["tam"] * 1.1 + b_t["asc"] + b_t["des"])      # sitio de 2 líneas de título como mínimo
        ratio = _ratio(captura)
        alto_c_max = disp - slot_t - b_x["alto"] - g1 - g2
        cw = min(zona_w - 12, alto_c_max * ratio)
        ch = cw / ratio
        y = arriba + max(0.0, disp - (slot_t + g1 + ch + g2 + b_x["alto"])) * (0.3 if grande else 0.0)
        h.dibuja(b_t, x0, y, h.tinta, zona_w, False, nombre="titulo")
        y += slot_t + g1
        cx0 = x0 + (zona_w - 12 - cw) / 2
        h.tarjeta_imagen(cx0, y, cx0 + cw, y + ch, captura, radio=16, borde=5, sombra=11)
        h.anota("captura", (cx0, y, cx0 + cw + 11, y + ch + 11))
        y += ch + g2
        h.dibuja(b_x, x0, y, h.tinta, zona_w, False, nombre="texto")
    else:
        b_t = h.bloque(s["titulo"], pal.fuente_titulo, zona_w, 380 if grande else 330, 104 if grande else 92, 56 if grande else 52, 4, inter=1.08)
        pad = 38
        b_x = h.bloque(s["texto"], pal.fuente_texto, zona_w - 2 * pad - 12, 700 if grande else 520, 60 if grande else 52, 36 if grande else 32, 9, inter=1.3)
        g1 = 44
        alto_tarjeta = b_x["alto"] + 2 * pad
        y = arriba + max(0.0, disp - (b_t["alto"] + g1 + alto_tarjeta)) * (0.3 if grande else 0.12)
        h.dibuja(b_t, x0, y, h.tinta, zona_w, False, nombre="titulo")
        y += b_t["alto"] + g1
        h.tarjeta(x0, y, x1 - 12, y + alto_tarjeta, h.crema, radio=20, borde=5, sombra=12)
        h.anota("tarjeta", (x0, y, x1, y + alto_tarjeta + 12))
        h.dibuja(b_x, x0 + pad, y + pad, h.tinta, zona_w - 2 * pad - 12, False, nombre="texto")
    sigue = k < n - 1
    h.pie(c.get("canal", ""), c.get("web", ""), margen_der=62 if sigue else 0, logo=logo)
    if sigue:                                                  # «sigue →» al final del pie
        h.flecha(x1 - 46, y1 - PIE_ALTO + 16, 46, 30, h.naranja)
    return h


def _placa_youtube(h: _Hoja, alto: float):
    from . import tarjetas
    try:
        return tarjetas._placa_icono_youtube(alto * SS)
    except Exception:                                          # noqa: BLE001
        return None


def dibuja_cierre(c: dict, fmt: dict, marca: dict, miniatura: Image.Image | None, logo=None) -> _Hoja:
    """El cierre: un marcador (guarda esto), el título grande, el texto en una tarjeta y la miniatura del vídeo con el logo de YouTube y «VÍDEO COMPLETO EN YOUTUBE»."""
    pal = _pal_de(marca)
    h = _Hoja(fmt, pal, "naranja")
    x0, x1, y0, y1 = fmt["x0"], fmt["x1"], fmt["y0"], fmt["y1"]
    zona_w = x1 - x0
    grande = fmt["alto"] > 1500
    abajo = y1 - PIE_ALTO - 26
    mw, mh = (110, 158) if grande else (84, 120)
    h.marcador(x0 + 4, y0, mw, mh, h.crema)
    h.anota("marcador", (x0 + 4, y0, x0 + 4 + mw + 8, y0 + mh + 8))
    y = y0 + mh + 36
    b_t = h.bloque(c["cierre"]["titulo"], pal.fuente_numero, zona_w, 380 if grande else 280, 124 if grande else 100, 60, 3, inter=1.06, mayus=True)
    trazo = max(3.0, b_t["tam"] * 0.075)
    caja_t = h.dibuja(b_t, x0, y, h.crema, zona_w, False, trazo=trazo, sombra=max(4.0, b_t["tam"] * 0.07), nombre="titulo")
    y = caja_t[3] + 32
    pad = 32
    b_x = h.bloque(c["cierre"]["texto"], pal.fuente_titulo, zona_w - 2 * pad - 12, 260 if grande else 220, 48 if grande else 42, 30, 4, inter=1.22)
    alto_x = b_x["alto"] + 2 * pad
    h.tarjeta(x0, y, x1 - 12, y + alto_x, h.crema, radio=20, borde=5, sombra=11)
    h.anota("tarjeta", (x0, y, x1, y + alto_x + 11))
    h.dibuja(b_x, x0 + pad, y + pad, h.tinta, zona_w - 2 * pad - 12, False, nombre="texto")
    y += alto_x + 40
    if miniatura is not None:
        ratio = _ratio(miniatura)
        disp = abajo - y - 30
        cw = min(zona_w - 24, disp * ratio)
        ch = cw / ratio
        cx0 = x0 + (zona_w - 12 - cw) / 2
        h.tarjeta_imagen(cx0, y, cx0 + cw, y + ch, miniatura, radio=16, borde=5, sombra=11)
        h.anota("miniatura", (cx0, y, cx0 + cw + 11, y + ch + 11))
        de_texto = bool(c.get("texto_mode"))
        placa = None if de_texto else _placa_youtube(h, 64)
        if placa is not None:                                  # el logo oficial, en su placa blanca (sin contorno ni sombra: sus normas de marca)
            h.im.paste(placa, (h.px(cx0 + cw - placa.width / SS - 18), h.px(y + 18)), placa)
        et = h.pastilla(cx0 + 22, y + ch - 24, "ARTÍCULO COMPLETO EN MI WEB" if de_texto else "VÍDEO COMPLETO EN YOUTUBE", pal.fuente_numero, 27, h.tinta, h.crema, pad_x=22, pad_y=11,
                        extra_izq=0 if de_texto else 40, nombre="etiqueta")
        cy = et[1] + (et[3] - et[1]) / 2
        if not de_texto:                                       # (el triángulo de «play» solo en lo de YouTube)
            h.poligono([(et[0] + 24, cy - 13), (et[0] + 24 + 22, cy), (et[0] + 24, cy + 13)], h.naranja, 0, borde=3)
    h.pie(c.get("canal", ""), c.get("web", ""), logo=logo)
    return h


# ------------------------------------------------------------------ capturas (ffmpeg, en tu PC: sin IA)
def _captura_hd(video: Path, t: float, destino: Path) -> bool:
    from .util import run
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale='min(1920,iw)':-2", "-q:v", "2",
             str(destino)])
        return destino.is_file() and destino.stat().st_size > 1000
    except Exception as exc:                                   # noqa: BLE001
        log(f"AVISO: no se pudo sacar la captura del segundo {t:.1f} ({exc}).")
        return False


def _reduce(ruta: Path, ancho_max: int) -> None:
    """Deja la captura (ya recortada) en `ancho_max` px de ancho como mucho: el artículo no necesita 1920."""
    try:
        with Image.open(ruta) as im:
            im = im.convert("RGB")
            if im.width <= ancho_max:
                return
            im.resize((ancho_max, max(1, round(im.height * ancho_max / im.width))), Image.LANCZOS).save(ruta, "JPEG", quality=90)
    except OSError:
        pass


def _plana(ruta: Path, umbral: float = 7.0) -> bool:
    """Un fotograma casi liso (negro, blanco, una transición): no sirve de captura."""
    try:
        with Image.open(ruta) as im:
            return ImageStat.Stat(im.convert("L").resize((96, 54))).stddev[0] < umbral
    except OSError:
        return True


def _en_pantalla(t: float, d: dict) -> bool:
    if not any(e.get("tipo") == "camara" for e in d["eventos"]):    # sin «Ritmo del vídeo» no se sabe: vale
        return True
    return any(a <= t < b for a, b in d["pantalla"])


def _modo_en(t: float, d: dict) -> str | None:
    """El modo de cámara que se ve en el segundo `t` del vídeo montado (cara / pantalla / pantalla_zoom / ambos…), o None si el vídeo no trae «Ritmo»."""
    modo = None
    for e in sorted((e for e in d["eventos"] if e.get("tipo") == "camara"), key=lambda e: e.get("t", 0)):
        if float(e.get("t", 0)) <= t:
            modo = str(e.get("detalle") or "")
    return modo


def _camara_de(d: dict) -> dict | None:
    """El recuadro de tu cámara (fracciones del lienzo) del canal de este vídeo, o None."""
    try:
        from . import config
        cam = ((config.load().get_path("videos.canales") or {}).get(d.get("canal")) or {}).get("camara")
        return {k: float(cam[k]) for k in ("x", "y", "w", "h")} if cam else None
    except Exception:                                          # noqa: BLE001
        return None


def caja_sin_camara(cam: dict, ancho: int, alto: int, margen: float = 0.012) -> tuple[float, float, float, float] | None:
    """La zona MÁS GRANDE del lienzo (x0, y0, x1, y1 en fracciones) que no toca el recuadro de la cámara y no es una tira demasiado ancha: a la izquierda, a la derecha, arriba o abajo de ella."""
    x, y, w, h = cam["x"], cam["y"], cam["w"], cam["h"]
    cand = [(0.0, 0.0, x - margen, 1.0), (x + w + margen, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, y - margen), (0.0, y + h + margen, 1.0, 1.0)]
    mejor, area_mejor = None, 0.0
    for x0, y0, x1, y1 in cand:
        if x1 - x0 < 0.3 or y1 - y0 < 0.3:
            continue
        asp = ((x1 - x0) * ancho) / ((y1 - y0) * alto)
        area = (x1 - x0) * (y1 - y0)
        if 0.8 <= asp <= 2.2 and area > area_mejor:
            mejor, area_mejor = (x0, y0, x1, y1), area
    return mejor


def recorta_sin_camara(ruta: Path, t: float, d: dict) -> bool:
    """el autor (9-oct-2026): «que no salga la cámara». En un momento de pantalla ENTERA (modo «pantalla»: la cámara va pegada en una esquina), la captura se recorta a la zona
    grande que no la incluye = más zoom a lo que enseñas. En «pantalla con zoom» ya no sale cámara y en «cara» ni se elige esa captura: no se toca. Sin «Ritmo» o sin cámara
    medida, no se toca. -> True si se recortó. Nunca lanza (una captura sin recortar vale)."""
    try:
        from . import config
        if not config.load().get_path("carrusel.sin_camara", True) or _modo_en(t, d) != "pantalla":
            return False
        cam = _camara_de(d)
        if not cam:
            return False
        with Image.open(ruta) as im:
            im = im.convert("RGB")
            caja = caja_sin_camara(cam, im.width, im.height)
            if not caja:
                return False
            W, H = im.size
            im.crop((round(caja[0] * W), round(caja[1] * H), round(caja[2] * W), round(caja[3] * H))).save(ruta, "JPEG", quality=92)
        return True
    except Exception as exc:                                   # noqa: BLE001
        log(f"AVISO: no se pudo recortar la cámara de la captura del segundo {t:.1f} ({exc}).")
        return False


DESFASES_CAPTURA = (0.0, 1.5, -1.5, 3.0, -3.0, 5.0)


def busca_captura(d: dict, t: float, destino: Path, evita: list[float], desfases=DESFASES_CAPTURA, ancho_max: int | None = None) -> float | None:
    """Saca la captura de ese momento del vídeo (y, si no sirve, de uno cercano): en tu pantalla, apartada de lo que tapa el cuadro, sin fotogramas vacíos y a ≥ 4 s de las
    otras capturas. -> el segundo de la captura, o None si no hay ninguna buena (esa diapositiva sale sin captura)."""
    from .articulo import momento_captura
    vistos: set[float] = set()
    for desfase in desfases:
        tc = momento_captura(t + desfase, d)
        if tc in vistos or not _en_pantalla(tc, d) or any(abs(tc - o) < 4.0 for o in evita):
            continue
        vistos.add(tc)
        if _captura_hd(d["video"], tc, destino) and not _plana(destino):
            recorta_sin_camara(destino, tc, d)
            if ancho_max:
                _reduce(destino, ancho_max)
            return tc
        destino.unlink(missing_ok=True)
    return None


# ------------------------------------------------------------------ crear / leer / rehacer
def ruta(jid: str, dir_outbox=None) -> Path:
    from . import config
    return Path(dir_outbox or config.paths()["outbox"]) / jid / "carrusel"


def lee(jid: str, dir_outbox=None) -> dict | None:
    try:
        d = json.loads((ruta(jid, dir_outbox) / "carrusel.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _llama_gemini(cfg, prompt: str, canal: str, valida, esquema=None):
    from . import modelos as _m
    from . import util as _util
    from .director import _gemini as _abre
    g = _abre(cfg)
    if not g:
        raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso».")
    client, types = g[:2]
    res, modelo, _uso = _util.call_gemini_cadena(client, types, _m.cadena_lite(cfg, client), prompt, system=_SYSTEM.format(canal=canal), valida=valida, reintento_s=60.0,
                                                 pasadas=1, timeout_s=150.0, esquema=esquema or _esquema(), etiqueta="carrusel")
    return res, modelo


def _datos(jid: str, dir_outbox=None, dir_work=None) -> dict:
    """Lo que hace falta de la fuente del carrusel: un vídeo terminado (`articulo.datos_del_video`) o un artículo/texto (`carrusel_texto.datos`, jid «texto-…--pro»)."""
    from . import carrusel_texto
    if carrusel_texto.es_texto(jid):
        return carrusel_texto.datos(jid, dir_outbox, dir_work)
    from . import articulo
    return articulo.datos_del_video(jid, dir_outbox, dir_work)


def _contexto(cfg, d: dict) -> dict:
    """La marca, el nombre y la web del canal de ese vídeo (la marca del canal manda sobre la de `videos.marca`)."""
    from urllib.parse import urlparse
    info = (cfg.get_path(f"videos.canales.{d['canal']}") or {}) if cfg is not None and d["canal"] else {}
    marca = {**(dict(cfg.get_path("videos.marca") or {}) if cfg is not None else {}), **{k: v for k, v in (info.get("marca") or {}).items() if v}}
    web = str((info.get("enlaces") or {}).get("web") or "")
    try:
        host = (urlparse(web).hostname or "").removeprefix("www.")
    except ValueError:
        host = ""
    return {"info": info, "marca": marca, "nombre": str(info.get("nombre") or d["canal"] or ""), "web": host}


def _imagen_miniatura(d: dict, carpeta: Path, c: dict) -> Image.Image | None:
    """La miniatura del vídeo (la que pone el panel) para el cierre; si no está, la primera captura."""
    try:
        man = json.loads((d["out_dir"] / "manifest.json").read_text(encoding="utf-8"))
        e = man[0] if isinstance(man, list) else man
        p = d["out_dir"] / str(e.get("miniatura") or "pro.jpg")
        if p.is_file():
            return Image.open(p).convert("RGB")
    except (OSError, ValueError, IndexError, TypeError):
        pass
    for s in c["pasos"]:
        if s.get("captura") and (carpeta / "capturas" / s["captura"]).is_file():
            return Image.open(carpeta / "capturas" / s["captura"]).convert("RGB")
    return None


def renderiza(c: dict, carpeta: Path, marca: dict, miniatura: Image.Image | None, cara_de=None, progreso=None, logo=None, logo_cada: int = 2) -> dict:
    """Dibuja TODAS las diapositivas (Instagram y TikTok) y el PDF en `carpeta`, desde los datos `c`. -> {"ig": [archivos], "tiktok": [archivos], "pdf": nombre}"""
    from . import escena_completa
    out: dict = {}
    capturas = {s["id"]: (Image.open(carpeta / "capturas" / s["captura"]).convert("RGB") if s.get("captura") and (carpeta / "capturas" / s["captura"]).is_file() else None)
                for s in c["pasos"]}
    try:
        for clave, fmt in FORMATOS.items():
            sub = carpeta / clave
            if sub.exists():
                shutil.rmtree(sub, ignore_errors=True)
            sub.mkdir(parents=True, exist_ok=True)
            if progreso:
                progreso(f"Dibujando las diapositivas de {fmt['nombre']}…")
            cara = cara_de(fmt) if cara_de else None
            hojas = [dibuja_portada(c, fmt, marca, cara)]
            # el sello con tu retrato: en el cierre SIEMPRE y en un paso de cada `logo_cada` («a veces abajo»; 0 = nunca en los pasos)
            hojas += [dibuja_paso(c, k, fmt, marca, capturas[s["id"]], logo=(logo if logo is not None and logo_cada and k % logo_cada == logo_cada - 1 else None))
                      for k, s in enumerate(c["pasos"])]
            hojas.append(dibuja_cierre(c, fmt, marca, miniatura, logo=logo))
            nombres = []
            for k, hoja in enumerate(hojas, 1):
                nombre = f"slide-{k:02d}.jpg"
                hoja.final().save(sub / nombre, "JPEG", quality=CALIDAD_JPG, subsampling=0, optimize=True)
                nombres.append(nombre)
            out[clave] = nombres
        ims = [Image.open(carpeta / "ig" / n).convert("RGB") for n in out["ig"]]
        ims[0].save(carpeta / "carrusel.pdf", "PDF", save_all=True, append_images=ims[1:], resolution=150.0, quality=90)
        out["pdf"] = "carrusel.pdf"
    finally:
        escena_completa._fondo.cache_clear()                   # (los fondos al doble de tamaño ocupan decenas de MB: no se quedan en memoria)
    return out


def _hashtags(cfg, d: dict) -> str:
    from . import enlaces as enl
    return enl.hashtags_de(d["tags"])


def _guarda(carpeta: Path, c: dict) -> None:
    (carpeta / "carrusel.json").write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")


def crea(jid: str, cfg, dir_outbox=None, dir_work=None, _llama=None, progreso=None, dir_caras=None, _verifica=None, modo_imagen: str = "auto") -> dict:
    """El carrusel de un vídeo terminado (o de un artículo/texto, jid «texto-…--pro»: `modo_imagen` = auto / articulo / simbolo / texto, ver `carrusel_texto`): datos -> UNA petición a
    Flash-Lite -> capturas (o imágenes del artículo / símbolos) -> diapositivas (Instagram, TikTok) + PDF. Nunca lanza.
    -> {"ok": True, "carrusel": {...}} o {"ok": False, "error": qué pasó y qué hacer}. `_llama`: pruebas (sin red)."""
    from . import carrusel_texto
    from .util import nombre_modelo
    de_texto = carrusel_texto.es_texto(jid)
    try:
        d = _datos(jid, dir_outbox, dir_work)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": ("No encuentro el texto de este carrusel en el PC" if de_texto else "No encuentro todo lo de este vídeo en el PC") + f" ({str(exc)[:160]})."}
    if len(d["frases"]) < 5:
        return {"ok": False, "error": "Hace falta más texto para sacar de ahí un carrusel." if de_texto else "Este vídeo casi no tiene voz transcrita: no da para un carrusel."}
    ctx = _contexto(cfg, d)
    objetivo = str((cfg.get_path("ideas.objetivo") if cfg is not None else "") or "Conseguir clientes para sus servicios de SEO; crecer la audiencia es el medio.")
    prompt = (carrusel_texto.arma_prompt if de_texto else arma_prompt)(d, ctx["nombre"] or "tu canal", objetivo)
    if progreso:
        progreso("Escribiendo el carrusel (Flash-Lite)…")
    try:
        from . import uso as _uso
        _uso.contexto(job=jid)
        try:
            llama = _llama or (lambda pr, va: _llama_gemini(cfg, pr, ctx["nombre"] or "tu canal", va, carrusel_texto.esquema() if de_texto else None))
            c, modelo = llama(prompt, lambda t: parsea(t, d))
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Carrusel de {jid}: {exc}")
        from .ideas import _error_claro
        return {"ok": False, "error": _error_claro(exc).replace("las ideas", "el carrusel")}

    carpeta = ruta(jid, dir_outbox)
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)             # «↻ Otro carrusel»: nada de lo de la vez anterior
    (carpeta / "capturas").mkdir(parents=True, exist_ok=True)
    avisos_coh: list[str] = []
    sin = 0
    c["jid"] = jid
    if de_texto:
        # imágenes de cada paso (las del artículo / un símbolo / ninguna) + 🧭 coherencia imagen ↔ paso + 🧠 claridad de los textos (carrusel_texto.py). Con una IA de mentira inyectada
        # (pruebas), la IA de las revisiones solo se usa si se da también `_verifica`.
        c.update(texto_mode=True, modo_imagen=modo_imagen if modo_imagen in carrusel_texto.MODOS else "auto", origen=d.get("origen", "texto"))
        try:
            avisos_coh = carrusel_texto.pone_imagenes(c, d, carpeta, cfg, c["modo_imagen"], progreso, _verifica, ia=(_llama is None or _verifica is not None))
        except Exception as exc:                               # noqa: BLE001 — esto nunca puede tumbar el carrusel
            log(f"AVISO: las imágenes del carrusel desde texto fallaron ({str(exc)[:140]}).")
            avisos_coh = ["No pude poner imágenes en las diapositivas: salen solo con texto (puedes elegirlas con «✏ Editar»)."]
    else:
        if progreso:
            progreso("Sacando las capturas del vídeo…")
        ocupados: list[float] = []
        for s in c["pasos"]:
            nombre = f"cap-{s['id']}.jpg"
            tc = busca_captura(d, s["t"], carpeta / "capturas" / nombre, ocupados)
            if tc is None:
                s.update(captura="", t_captura=None)
                sin += 1
            else:
                s.update(captura=nombre, t_captura=tc)
                ocupados.append(tc)
        # 🧭 coherencia imagen ↔ paso (carrusel_coherencia.py): Flash-Lite MIRA cada captura. Con una IA de mentira inyectada (pruebas), solo si se da también la del revisor.
        if cfg is not None and cfg.get_path("carrusel.verifica_coherencia", True) and (_llama is None or _verifica is not None):
            try:
                from . import carrusel_coherencia
                avisos_coh = carrusel_coherencia.verifica(c, d, carpeta, cfg, _verifica, progreso)
            except Exception as exc:                           # noqa: BLE001 — esto nunca puede tumbar el carrusel
                log(f"AVISO: la comprobación de coherencia del carrusel falló ({str(exc)[:140]}).")
    hashtags = _hashtags(cfg, d)
    n_desc = c.get("descartados", 0)
    desc = f"{n_desc} paso(s) que propuso la IA se descartaron: no se apoyaban en nada que " + ("diga el artículo." if de_texto else "digas en el vídeo.")
    c.update(version=VERSION, jid=jid, ts=time.time(), modelo=modelo, nombre_modelo=nombre_modelo(modelo), canal=ctx["nombre"], web=ctx["web"], video_titulo=d["titulo"],
             duracion=d["duracion"], hashtags=hashtags,
             avisos=avisos_coh + [x for x in (desc if n_desc else "",
                                 f"{sin} diapositiva(s) salen sin captura: no había una pantalla buena cerca de ese momento." if sin else "") if x])
    c.pop("descartados", None)
    c["cara_gesto"] = elige_gesto(cfg, dir_caras, tipo=c.get("tipo"))   # (según el contexto; queda guardada: «✏ Editar» no cambia de cara)
    log(f"Carrusel de {jid}: portada con tu cara «{c['cara_gesto']}» (carrusel de {c.get('tipo')}).")
    return _termina(c, carpeta, d, ctx, cfg, dir_caras, progreso)


def _termina(c: dict, carpeta: Path, d: dict, ctx: dict, cfg, dir_caras, progreso) -> dict:
    if c.get("texto_mode"):                                    # (de un artículo: la imagen destacada o la tarjeta con el logo, no la miniatura de un vídeo)
        from . import carrusel_texto
        miniatura = carrusel_texto.imagen_cierre(d, ctx["marca"])
    else:
        miniatura = _imagen_miniatura(d, carpeta, c)
    caras: dict = {}

    def cara_de(fmt):                                          # (una sola vez por formato)
        k = fmt["alto"]
        if k not in caras:
            caras[k] = _cara_para_portada(cfg, fmt["alto"] * (0.42 if fmt["alto"] > 1500 else 0.40), dir_caras, c.get("cara_gesto"))
        return caras[k]

    try:
        logo_cada = int(cfg.get_path("carrusel.logo_cada", 2)) if cfg is not None else 2
    except (TypeError, ValueError):
        logo_cada = 2
    c["archivos"] = renderiza(c, carpeta, ctx["marca"], miniatura, cara_de, progreso, logo=_logo_pie(cfg), logo_cada=max(0, logo_cada))
    c["diapositivas"] = len(c["pasos"]) + 2
    _guarda(carpeta, c)
    log(f"Carrusel de {c['jid']}: {c['diapositivas']} diapositivas ({c.get('nombre_modelo', '')}).")
    return {"ok": True, "carrusel": c}


def rehaz(jid: str, cambios: dict, cfg, dir_outbox=None, dir_work=None, progreso=None, dir_caras=None) -> dict:
    """Vuelve a dibujar el carrusel con lo que cambiaste a mano (textos, qué pasos quedan, el momento de cada captura) SIN llamar a la IA. `cambios`: {"portada": {titulo,
    subtitulo}, "pasos": [{"id", "titulo", "texto", "t_captura", "sin_captura"}…] (los que no vengan se quitan), "cierre": {titulo, texto}, "pie_instagram", "tiktok_titulo",
    "tiktok_texto"}. Nunca lanza. -> {"ok": True, "carrusel": {...}} o {"ok": False, "error"}"""
    c = lee(jid, dir_outbox)
    if not c:
        return {"ok": False, "error": "Este vídeo aún no tiene carrusel: créalo primero."}
    try:
        d = _datos(jid, dir_outbox, dir_work)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": f"No encuentro todo lo de este vídeo en el PC ({str(exc)[:160]})."}
    cambios = cambios if isinstance(cambios, dict) else {}
    carpeta = ruta(jid, dir_outbox)
    previos = {s["id"]: s for s in c["pasos"]}
    pasos: list[dict] = []
    for p in cambios.get("pasos") or []:
        if not isinstance(p, dict) or p.get("id") not in previos:
            continue
        s = dict(previos[p["id"]])
        s["titulo"] = _txt(p.get("titulo"), 70).strip(" .:") or s["titulo"]
        s["texto"] = _txt(p.get("texto"), 200) or s["texto"]
        pasos.append({**s, "_pide": p})
    if len(pasos) < 2:
        return {"ok": False, "error": "Un carrusel necesita al menos 2 pasos: deja más de uno."}
    ctx = _contexto(cfg, d)
    avisos: list[str] = []
    ocupados = [s["t_captura"] for s in pasos if s.get("t_captura") is not None and not s["_pide"].get("sin_captura")]
    if c.get("texto_mode"):                                    # de un artículo: la imagen de cada paso se elige (conservar / ninguna / una del artículo / un símbolo), sin «segundos»
        from . import carrusel_texto
        for k, s in enumerate(pasos):
            p = s.pop("_pide")
            ap = carrusel_texto.cambia_imagen(s, "ninguna" if p.get("sin_captura") else p.get("imagen"), d, carpeta, ctx["marca"], k)
            if ap:
                avisos.append(ap)
    for s in ([] if c.get("texto_mode") else pasos):
        p = s.pop("_pide")
        if p.get("sin_captura"):
            s.update(captura="", t_captura=None)
            continue
        try:
            t_nuevo = float(p["t_captura"]) if p.get("t_captura") is not None else None
        except (TypeError, ValueError):
            t_nuevo = None
        if t_nuevo is not None and 0.5 <= t_nuevo <= d["duracion"] - 0.5 and (s.get("t_captura") is None or abs(t_nuevo - s["t_captura"]) > 0.05 or not s.get("captura")):
            tmp = carpeta / "capturas" / f"nueva-{s['id']}.jpg"
            if _captura_hd(d["video"], t_nuevo, tmp) and not _plana(tmp):
                recorta_sin_camara(tmp, t_nuevo, d)
                tmp.replace(carpeta / "capturas" / f"cap-{s['id']}.jpg")
                s.update(captura=f"cap-{s['id']}.jpg", t_captura=round(t_nuevo, 2))
            else:
                tmp.unlink(missing_ok=True)
                avisos.append(f"En el momento {t_nuevo:.1f} s no hay un fotograma bueno (está vacío): me quedé con la captura de antes.")
        elif not s.get("captura"):                             # el que salió sin captura y no se pidió otra: se vuelve a intentar con lo normal
            tc = busca_captura(d, s["t"], carpeta / "capturas" / f"cap-{s['id']}.jpg", [o for o in ocupados if o is not None])
            if tc is not None:
                s.update(captura=f"cap-{s['id']}.jpg", t_captura=tc)
    c["pasos"] = pasos
    por, cie = cambios.get("portada") or {}, cambios.get("cierre") or {}
    c["portada"] = {"titulo": _txt(por.get("titulo"), 90) or c["portada"]["titulo"], "subtitulo": _txt(por.get("subtitulo"), 110) if "subtitulo" in por else c["portada"]["subtitulo"]}
    c["cierre"] = {"titulo": _txt(cie.get("titulo"), 50) or c["cierre"]["titulo"], "texto": _txt(cie.get("texto"), 130) or c["cierre"]["texto"]}
    if "pie_instagram" in cambios:
        c["pie_instagram"] = _pie(cambios["pie_instagram"], 2000) or c["pie_instagram"]
    if "tiktok_titulo" in cambios:
        c["tiktok_titulo"] = _txt(_sin_urls(cambios["tiktok_titulo"]), 90) or c["tiktok_titulo"]
    if "tiktok_texto" in cambios:
        c["tiktok_texto"] = _pie(cambios["tiktok_texto"], 300).replace("\n", " ")
    c["avisos"] = avisos + [a for a in c.get("avisos", []) if "descartaron" in a]
    c["ts_edicion"] = time.time()
    usadas = {s["captura"] for s in pasos if s.get("captura")}
    for f in (carpeta / "capturas").glob("*.jpg"):             # (las capturas de los pasos que quitaste)
        if f.name not in usadas:
            f.unlink(missing_ok=True)
    return _termina(c, carpeta, d, ctx, cfg, dir_caras, progreso)


def captura_previa(jid: str, t: float, dir_outbox=None, dir_work=None, ancho: int = 640) -> Path | None:
    """Un fotograma pequeño del vídeo en el segundo `t` (para elegir otra captura sin dibujar nada): guardado junto al carrusel, en `previas/`. None si no se pudo."""
    from . import carrusel_texto
    if carrusel_texto.es_texto(jid):                           # (un artículo no tiene fotogramas que previsualizar)
        return None
    try:
        d = _datos(jid, dir_outbox, dir_work)
        t = min(max(0.5, float(t)), max(0.5, d["duracion"] - 0.5))
    except Exception:                                          # noqa: BLE001
        return None
    carpeta = ruta(jid, dir_outbox) / "previas"
    carpeta.mkdir(parents=True, exist_ok=True)
    dest = carpeta / f"t{int(round(t * 10)):05d}.jpg"
    if dest.is_file():
        return dest
    grande = carpeta / "_grande.jpg"
    if not _captura_hd(d["video"], t, grande):
        return None
    recorta_sin_camara(grande, t, d)                           # (la vista previa enseña lo mismo que saldrá en la diapositiva)
    with Image.open(grande) as im:
        im.convert("RGB").resize((ancho, max(1, round(im.height * ancho / im.width))), Image.LANCZOS).save(dest, "JPEG", quality=82)
    grande.unlink(missing_ok=True)
    viejas = sorted(carpeta.glob("t*.jpg"), key=lambda p: p.stat().st_mtime)
    for p in viejas[:-40]:                                     # (no se acumulan: las 40 últimas)
        p.unlink(missing_ok=True)
    return dest


# ------------------------------------------------------------------ descargas
def texto_instagram(c: dict) -> str:
    return (c.get("pie_instagram", "").rstrip() + ("\n\n" + c["hashtags"] if c.get("hashtags") else "")).strip()


def texto_tiktok(c: dict) -> str:
    tags = " ".join(str(c.get("hashtags") or "").split()[:4])
    return "\n".join(x for x in (c.get("tiktok_titulo", ""), (c.get("tiktok_texto", "") + (" " + tags if tags else "")).strip()) if x)


def zip_de(jid: str, formato: str, dir_outbox=None) -> tuple[bytes, str] | None:
    """Las diapositivas de un formato («ig», «tiktok» o «todo») con el texto de la publicación, en un .zip. -> (bytes, nombre del archivo) o None."""
    c = lee(jid, dir_outbox)
    carpeta = ruta(jid, dir_outbox)
    if not c or formato not in ("ig", "tiktok", "todo"):
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for clave in ("ig", "tiktok"):
            if formato not in (clave, "todo"):
                continue
            for n in (c.get("archivos") or {}).get(clave, []):
                if (carpeta / clave / n).is_file():
                    z.write(carpeta / clave / n, f"{FORMATOS[clave]['nombre']}/{n}" if formato == "todo" else n)
            z.writestr((f"{FORMATOS[clave]['nombre']}/" if formato == "todo" else "") + f"texto-{clave}.txt", (texto_instagram(c) if clave == "ig" else texto_tiktok(c)) + "\n")
        if formato == "todo" and (carpeta / "carrusel.pdf").is_file():
            z.write(carpeta / "carrusel.pdf", "carrusel.pdf")
    nombre = re.sub(r"[^a-z0-9]+", "-", _sin_tildes(str(c.get("portada", {}).get("titulo") or jid).lower())).strip("-")[:50] or "carrusel"
    return buf.getvalue(), f"carrusel-{nombre}-{formato}.zip"


# ------------------------------------------------------------------ pruebas (sin red: Gemini de mentira y un vídeo sintético; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    import zipfile as _zf
    from .util import run
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    SENT = [("Hola amigos hoy vamos a ver cómo arreglar un error de indexación.", 0.5),
            ("Primero revisa el archivo sitemap de tu sitio web con calma.", 4.0),
            ("Segundo comprueba que robots punto txt no bloquee tus páginas importantes.", 8.0),
            ("Tercero usa la inspección de URL para pedir que Google indexe la página.", 12.0),
            ("Cuarto espera unos días y mide el resultado en Search Console.", 16.0),
            ("Gracias por ver el vídeo hasta el final.", 20.0)]
    words = []
    for texto, t0 in SENT:
        for k, w in enumerate(texto.split()):
            words.append({"word": w, "start": round(t0 + k * 0.14, 2), "end": round(t0 + k * 0.14 + 0.12, 2)})
    eventos = [{"t": 0.0, "tipo": "camara", "detalle": "cara"}, {"t": 1.5, "tipo": "camara", "detalle": "pantalla"}, {"t": 21.0, "tipo": "camara", "detalle": "cara"}]

    def pasos_ia(extra=None):
        p = [{"titulo": "Revisa el sitemap", "texto": "Comprueba que tu sitemap esté al día y lleve tus páginas.", "minuto": "0:04", "cita": "revisa el archivo sitemap de tu sitio web"},
             {"titulo": "Cuidado con robots", "texto": "Que robots.txt no bloquee lo importante.", "minuto": "0:08", "cita": "comprueba que robots punto txt no bloquee tus páginas"},
             {"titulo": "Pide la indexación", "texto": "Usa la inspección de URL y pide que Google indexe la página.", "minuto": "0:12", "cita": "usa la inspección de URL para pedir que Google indexe"},
             {"titulo": "Mide el resultado", "texto": "Espera unos días y míralo en Search Console.", "minuto": "0:16", "cita": "espera unos días y mide el resultado en Search Console"}]
        return p + (extra or [])

    def respuesta(pasos):
        return {"tipo": "pasos", "portada": {"titulo": "Arregla tu indexación en 7 pasos", "subtitulo": "Para quien no sale en Google"}, "pasos": pasos,
                "cierre": {"titulo": "¿Te sirvió? Guárdalo", "texto": "El vídeo completo está en mi canal."},
                "pie_instagram": "Arregla tu indexación 👇\nUna línea por paso.\nGuárdalo #seo https://x.com", "tiktok_titulo": "Arregla tu indexación",
                "tiktok_texto": "Mira esto https://ejemplo.com ahora"}

    # --- citas y parser
    fr = [{"t": 0.5, "texto": "Hola amigos hoy vamos a ver cómo arreglar un error.", "pantalla": False},
          {"t": 4.0, "texto": "Primero revisa el archivo sitemap de tu sitio web con calma.", "pantalla": True},
          {"t": 4.0, "texto": "Segundo comprueba que robots punto txt no bloquee tus páginas.", "pantalla": True}]
    caso("una cita copiada del vídeo se ubica (exacta, sin tildes ni signos, y casi exacta); una inventada no",
         ubica_cita("revisa el archivo sitemap de tu sitio", fr) == 1 and ubica_cita("Revisa el ARCHIVO sitemap, de tu sitio web!", fr) == 1
         and ubica_cita("revisa el archivo sitemap de tu sitio web con muchisima calma ahora", fr) == 1 and ubica_cita("configura el pixel de facebook en tu tienda", fr) == -1
         and ubica_cita("hola", fr) == -1, [ubica_cita("revisa el archivo sitemap de tu sitio", fr), ubica_cita("configura el pixel de facebook en tu tienda", fr)])
    caso("«2:05», «0:02:05» y «125 s» son el mismo segundo; un texto sin tiempo no se entiende",
         _a_segundos("2:05") == 125.0 and _a_segundos("0:02:05") == 125.0 and _a_segundos("125 s") == 125.0 and _a_segundos("pronto") is None)
    caso("el «7 pasos» de la IA se corrige al número REAL; «siete errores» también", _ajusta_numero("Arregla tu web en 7 pasos", 4) == "Arregla tu web en 4 pasos"
         and _ajusta_numero("Siete errores de SEO", 5) == "5 errores de SEO")
    d_p = {"frases": [{"t": t0, "texto": x, "pantalla": True} for x, t0 in SENT], "duracion": 24.0, "titulo": "Indexación", "tags": [], "capitulos": ""}
    inventado = {"titulo": "Configura el pixel de Facebook", "texto": "Esto no se dice en el vídeo.", "minuto": "0:05", "cita": "instala el pixel de facebook en tu tienda online ya"}
    c = parsea(json.dumps(respuesta(pasos_ia([inventado]))), d_p)
    caso("el parser descarta el paso inventado (su cita no está en el vídeo), ordena, corrige el número de la portada y quita URLs de TikTok",
         len(c["pasos"]) == 4 and c["descartados"] == 1 and [s["id"] for s in c["pasos"]] == ["p1", "p2", "p3", "p4"] and c["portada"]["titulo"] == "Arregla tu indexación en 4 pasos"
         and "http" not in c["tiktok_titulo"] + c["tiktok_texto"] and [s["t"] for s in c["pasos"]] == sorted(s["t"] for s in c["pasos"]), c)
    try:
        parsea(json.dumps(respuesta(pasos_ia()[:2])), d_p)
        caso("con menos de 3 pasos que se apoyen en el vídeo se rechaza (para probar otro modelo)", False)
    except ValueError:
        caso("con menos de 3 pasos que se apoyen en el vídeo se rechaza (para probar otro modelo)", True)

    # --- de punta a punta: un vídeo de verdad (sintético), la IA de mentira
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        jid = "2026-01-01-car--pro"
        out = t / "videos" / jid
        out.mkdir(parents=True)
        (t / "work" / jid).mkdir(parents=True)
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", "-t", "24", "-pix_fmt", "yuv420p", str(out / "pro.mp4")])
        (t / "work" / jid / "words.json").write_text(json.dumps(words), encoding="utf-8")
        (out / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "titulo": "Indexación", "duracion": 24.0, "segments": [[0.0, 24.0]], "eventos_visuales": eventos,
                                                        "tema": "Prueba", "tags": ["seo", "indexación"]}]), encoding="utf-8")
        pedidos: list = []

        def llama(pr, va):
            pedidos.append(pr)
            return va(json.dumps(respuesta(pasos_ia()))), "gemini-3.5-flash-lite"

        from . import config as _config
        cfg = _config.load()                      # (la marca —colores y letras— sale del config.yaml, como en el panel)
        r = crea(jid, cfg, t / "videos", t / "work", llama, dir_caras=t / "sin_caras")
        carp = out / "carrusel"
        c = lee(jid, t / "videos") or {}
        arch = c.get("archivos") or {}
        medidas = {}
        for k in ("ig", "tiktok"):
            try:
                medidas[k] = [Image.open(carp / k / n).size for n in arch.get(k, [])]
            except OSError:
                medidas[k] = []
        caso("de punta a punta: UNA petición, 6 diapositivas por formato (portada + 4 pasos + cierre), Instagram 1080×1350 y TikTok 1080×1920, PDF y una captura por paso",
             r.get("ok") and len(pedidos) == 1 and len(arch.get("ig", [])) == 6 and len(arch.get("tiktok", [])) == 6 and set(medidas["ig"]) == {(1080, 1350)}
             and set(medidas["tiktok"]) == {(1080, 1920)} and (carp / "carrusel.pdf").is_file() and len(list((carp / "capturas").glob("cap-*.jpg"))) == 4
             and "🖥" in pedidos[0] and c.get("diapositivas") == 6, r.get("error") or (medidas, arch))
        caso("los textos de las publicaciones: Instagram lleva los hashtags de las etiquetas y TikTok no lleva URLs",
             "#" in texto_instagram(c) and "http" not in texto_tiktok(c) and c.get("hashtags"), (texto_instagram(c), texto_tiktok(c)))

        # --- descargas
        zi, zt = zip_de(jid, "ig", t / "videos"), zip_de(jid, "todo", t / "videos")
        nom_i = _zf.ZipFile(io.BytesIO(zi[0])).namelist() if zi else []
        nom_t = _zf.ZipFile(io.BytesIO(zt[0])).namelist() if zt else []
        caso("el .zip de Instagram lleva las 6 diapositivas y su texto; el de «todo», las dos carpetas, el PDF y los dos textos; un formato raro no da nada",
             len([n for n in nom_i if n.endswith(".jpg")]) == 6 and "texto-ig.txt" in nom_i and "carrusel.pdf" in nom_t and "Instagram/slide-01.jpg" in nom_t
             and "TikTok/texto-tiktok.txt" in nom_t and zip_de(jid, "xx", t / "videos") is None and zip_de("no-existe--pro", "ig", t / "videos") is None, (nom_i, nom_t))

        # --- editar sin IA
        ids = [s["id"] for s in c["pasos"]]
        cambios = {"portada": {"titulo": "Mi portada nueva", "subtitulo": ""}, "pie_instagram": "Mi texto nuevo",
                   "pasos": [{"id": ids[0], "titulo": "Sitemap al día", "texto": "Texto nuevo del primer paso."}, {"id": ids[2], "titulo": "Pide indexar", "texto": "Otro texto.", "sin_captura": True},
                             {"id": ids[3], "titulo": "Mide", "texto": "Y mide.", "t_captura": 17.0}, {"id": "p99", "titulo": "no existe", "texto": "no"}]}
        r2 = rehaz(jid, cambios, cfg, t / "videos", t / "work", dir_caras=t / "sin_caras")
        c2 = lee(jid, t / "videos") or {}
        caso("«✏ Editar» vuelve a dibujar SIN IA: quita el paso que no mandas (y el que no existe), cambia los textos, deja uno sin captura y mueve la captura de otro",
             r2.get("ok") and len(pedidos) == 1 and len(c2.get("pasos", [])) == 3 and c2["pasos"][0]["titulo"] == "Sitemap al día" and c2["portada"]["titulo"] == "Mi portada nueva"
             and c2["pasos"][1]["captura"] == "" and c2["pasos"][2]["t_captura"] == 17.0 and len(c2["archivos"]["ig"]) == 5 and len(c2["archivos"]["tiktok"]) == 5
             and texto_instagram(c2).startswith("Mi texto nuevo") and not (carp / "capturas" / f"cap-{ids[1]}.jpg").exists(), r2.get("error") or c2.get("pasos"))
        un_paso = rehaz(jid, {"pasos": [{"id": ids[0], "titulo": "a", "texto": "b"}]}, cfg, t / "videos", t / "work", dir_caras=t / "sin_caras")
        caso("con menos de 2 pasos no se dibuja y lo anterior se queda como estaba", not un_paso["ok"] and len((lee(jid, t / "videos") or {}).get("pasos", [])) == 3, un_paso)
        caso("sin carrusel creado, «✏ Editar» avisa en vez de fallar", not rehaz("2026-01-02-otro--pro", {}, cfg, t / "videos", t / "work")["ok"])

        # --- fotograma de previa (para elegir otra captura)
        p1 = captura_previa(jid, 5.0, t / "videos", t / "work")
        p2 = captura_previa(jid, 5.0, t / "videos", t / "work")
        caso("el fotograma de previa sale pequeño (640 px) y se reusa la segunda vez; un segundo fuera del vídeo se lleva dentro",
             p1 is not None and p1 == p2 and Image.open(p1).width == 640 and captura_previa(jid, 999.0, t / "videos", t / "work") is not None, p1)
        # --- coherencia imagen ↔ paso (10-oct-2026): una IA de mentira que mira las capturas
        from . import articulo as _art
        from . import carrusel_coherencia as _coh
        dd = _art.datos_del_video(jid, t / "videos", t / "work")
        copia_c = {"jid": jid, "pasos": [dict(s) for s in c["pasos"]]}
        copia_dir = t / "coh"
        shutil.copytree(carp, copia_dir)
        id0 = copia_c["pasos"][0]["id"]
        viejo_t = copia_c["pasos"][0]["t_captura"]
        n_pedidos: list = []
        con_archivo = {s["id"] for s in c["pasos"] if s.get("captura") and (carp / "capturas" / s["captura"]).is_file()}

        def verif(elegida):
            def f(partes, va):
                n_pedidos.append(len(partes))
                txt = json.dumps({"pasos": [{"id": s["id"], "coincide": s["id"] != id0} for s in copia_c["pasos"] if s["id"] in con_archivo]}) if "coincide" in partes[0] else \
                    json.dumps({"pasos": [{"id": id0, "elegida": elegida}]})
                return va(txt), "gemini-3.5-flash-lite"
            return f

        av = _coh.verifica(copia_c, dd, copia_dir, cfg, verif(1))
        p0 = copia_c["pasos"][0]
        caso("la coherencia: UNA petición mira las capturas; la que no encaja se cambia por otra del vídeo (2.ª petición) y las demás se quedan",
             len(n_pedidos) == 2 and p0["coherencia"] == "cambiada" and p0["t_captura"] != viejo_t and (copia_dir / "capturas" / p0["captura"]).is_file()
             and all(s.get("coherencia") == "ok" for s in copia_c["pasos"][1:] if s["id"] in con_archivo) and any("cambiaron" in a for a in av), (n_pedidos, p0, av, viejo_t, [s.get('coherencia') for s in copia_c['pasos']]))
        copia_c2 = {"jid": jid, "pasos": [dict(s) for s in c["pasos"]]}
        copia_c = copia_c2
        shutil.rmtree(copia_dir)
        shutil.copytree(carp, copia_dir)
        av2 = _coh.verifica(copia_c2, dd, copia_dir, cfg, verif(0))
        caso("si ninguna alternativa encaja, el paso sale SIN captura (mejor ninguna que una que no tiene que ver) y se avisa",
             copia_c2["pasos"][0]["coherencia"] == "quitada" and copia_c2["pasos"][0]["captura"] == "" and not (copia_dir / "capturas" / f"cap-{id0}.jpg").exists()
             and any("sin captura" in a for a in av2) and not list((copia_dir / "capturas").glob("alt-*")), (copia_c2["pasos"][0], av2))
        copia_c3 = {"jid": jid, "pasos": [dict(s) for s in c["pasos"]]}

        def roto(partes, va):
            raise RuntimeError("503 UNAVAILABLE")

        shutil.rmtree(copia_dir)
        shutil.copytree(carp, copia_dir)
        av3 = _coh.verifica(copia_c3, dd, copia_dir, cfg, roto)
        caso("si Gemini no responde, las capturas se quedan como estaban y se avisa (nunca tumba el carrusel)",
             all(s["coherencia"] == "sin_comprobar" for s in copia_c3["pasos"] if s["captura"] and (copia_dir / "capturas" / s["captura"]).is_file()) and len(av3) == 1 and "No pude comprobar" in av3[0], av3)
        # sin la IA de revisión inyectada, crea() con una IA de texto de mentira NO llama al revisor real (no gasta nada en pruebas)
        caso("las pruebas con IA de mentira no llaman al revisor real", len(pedidos) == 1, len(pedidos))

        # --- que no salga la cámara (9-oct-2026)
        cam = {"x": 0.7672, "y": 0.6583, "w": 0.2172, "h": 0.2847}
        cj = caja_sin_camara(cam, 1920, 1080)
        caso("la zona sin cámara es la más grande que no la toca (con la cámara abajo a la derecha: todo lo de su izquierda) y una tira muy ancha no vale",
             cj is not None and cj[0] == 0.0 and cj[2] < cam["x"] and cj[1] == 0.0 and cj[3] == 1.0 and caja_sin_camara({"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8}, 1920, 1080) is None, cj)
        im0 = Image.new("RGB", (1920, 1080), (240, 240, 240))
        im0.paste((255, 0, 0), (round(cam["x"] * 1920), round(cam["y"] * 1080), round((cam["x"] + cam["w"]) * 1920), round((cam["y"] + cam["h"]) * 1080)))
        resultados = {}
        for modo in ("pantalla", "pantalla_zoom", "cara", None):
            f_ = t / f"cap-{modo}.jpg"
            im0.save(f_, "JPEG", quality=95)
            dd = {"canal": "ejemplo", "eventos": [{"t": 0.0, "tipo": "camara", "detalle": modo}] if modo else []}
            resultados[modo] = (recorta_sin_camara(f_, 5.0, dd), Image.open(f_).size)
        caso("en pantalla entera la captura se recorta y ya no lleva la cámara; en zoom, en «cara» o sin «Ritmo» no se toca",
             resultados["pantalla"][0] and resultados["pantalla"][1][0] < 1920 and not any(px[0] > 200 and px[1] < 60 for px in Image.open(t / "cap-pantalla.jpg").convert("RGB").resize((96, 54)).getdata())
             and not any(resultados[m][0] for m in ("pantalla_zoom", "cara", None)) and resultados["cara"][1] == (1920, 1080), resultados)

        # --- la cara de la portada varía (70 % feliz…) y el sello con tu retrato (el autor, 11-oct-2026)
        import collections
        import random
        caras_t = t / "mis_caras_prueba"
        caras_t.mkdir()

        def foto(nombre, ancho, alto):
            im_ = Image.new("RGBA", (ancho + 40, alto + 40), (0, 0, 0, 0))
            ImageDraw.Draw(im_).ellipse([20, 20, 20 + ancho, 20 + alto], fill=(200, 150, 120, 255))
            im_.save(caras_t / nombre)
        foto("cara feliz mirando hacia la derecha.png", 300, 400)
        foto("cara de sorpresa.png", 800, 400)                 # (brazos arriba: ANCHA)
        foto("cara pensando mirando derecha.png", 300, 400)
        foto("cara decepcion mirando al frente.png", 800, 400)  # (un gesto que NO está en los pesos)
        rng_t = random.Random(3)
        cuenta = collections.Counter(elige_gesto(cfg, caras_t, rng_t) for _ in range(2000))
        caso("la cara de la portada sale al azar con pesos: ~70 % feliz, ~20 % sorpresa, ~10 % pensando (y nunca un gesto que no está en los pesos)",
             0.65 < cuenta["feliz"] / 2000 < 0.75 and 0.16 < cuenta["sorpresa"] / 2000 < 0.24 and 0.07 < cuenta["pensando"] / 2000 < 0.13 and set(cuenta) <= {"feliz", "sorpresa", "pensando"}, cuenta)

        class CfgPesos:
            def __init__(self, pesos):
                self.pesos = pesos

            def get_path(self, k, d=None):
                return self.pesos if k == "carrusel.caras" else cfg.get_path(k, d)
        caso("los pesos se cambian en config.yaml (carrusel.caras): con solo «sorpresa» sale siempre sorpresa; un peso 0 no sale",
             {elige_gesto(CfgPesos({"sorpresa": 1, "feliz": 0}), caras_t, random.Random(i)) for i in range(40)} == {"sorpresa"})
        ctx = {tp: collections.Counter(elige_gesto(cfg, caras_t, random.Random(i), tipo=tp) for i in range(1500)) for tp in ("pasos", "consejos", "ideas", "errores")}
        caso("la cara depende del CONTEXTO: en un carrusel de errores no se sonríe (nunca «feliz»; sale la decepción, la sorpresa o el «pensando» que haya), en ideas manda el «pensando» y en pasos/consejos "
             "sigue mandando «feliz»",
             "feliz" not in ctx["errores"] and set(ctx["errores"]) <= {"decepcion", "sorpresa", "pensando"} and ctx["errores"]["decepcion"] > 600
             and ctx["ideas"].most_common(1)[0][0] == "pensando" and ctx["pasos"].most_common(1)[0][0] == "feliz" and ctx["consejos"].most_common(1)[0][0] == "feliz"
             and elige_gesto(cfg, caras_t, random.Random(1), tipo="inventado") in {"feliz", "sorpresa", "pensando"}, ctx)
        caso("sin fotos (o sin ninguna del gesto pedido) vuelve a la cara de siempre («feliz»), nunca falla",
             elige_gesto(cfg, t / "no_hay") == "feliz" and elige_gesto(CfgPesos({"asombro": 1}), caras_t) == "feliz")
        fmt_ig = FORMATOS["ig"]
        marca_t = dict(cfg.get_path("videos.marca") or {})
        portadas = {}
        for g in ("feliz", "sorpresa"):
            cara_g = _cara_para_portada(cfg, fmt_ig["alto"] * 0.40, caras_t, g)
            hoja_g = dibuja_portada(c, fmt_ig, marca_t, cara_g)
            portadas[g] = next((b for n_, b in hoja_g.cajas if n_ == "cara"), None)
        zona_w_ = fmt_ig["x1"] - fmt_ig["x0"]
        caso("una foto ANCHA (sorpresa, brazos arriba) sale en la portada pero reducida: no pasa del 55 % del ancho y no tapa el título",
             portadas["sorpresa"] is not None and (portadas["sorpresa"][2] - portadas["sorpresa"][0]) <= zona_w_ * 0.56 and portadas["feliz"] is not None, portadas)
        c_ger = lee(jid, t / "videos") or {}
        caso("el gesto elegido queda GUARDADO en el carrusel y «✏ Editar» no lo cambia", c_ger.get("cara_gesto") == "feliz" and c2.get("cara_gesto") == c_ger.get("cara_gesto") == c.get("cara_gesto"),
             (c.get("cara_gesto"), c2.get("cara_gesto")))
        retr_ = t / "retrato_prueba.png"                 # (un retrato de mentira: la copia que se comparte no trae ninguno)
        _im_r = Image.new("RGB", (200, 200), (200, 80, 40))
        ImageDraw.Draw(_im_r).ellipse([40, 40, 160, 160], fill=(250, 220, 190))
        _im_r.save(retr_)

        class _CfgLogo:
            def get_path(self, k, d=None):
                return str(retr_) if k == "carrusel.logo" else d
        logo_t = _logo_pie(_CfgLogo())
        r_sin, r_con = t / "sin_logo", t / "con_logo"
        for dd_ in (r_sin, r_con):
            shutil.copytree(carp / "capturas", dd_ / "capturas")
        a_sin = renderiza(c, r_sin, marca_t, None, None, None, logo=None)
        a_con = renderiza(c, r_con, marca_t, None, None, None, logo=logo_t, logo_cada=2)
        from PIL import ImageChops
        difieren = []
        for k_, (n_sin, n_con) in enumerate(zip(a_sin["ig"], a_con["ig"])):
            bb = ImageChops.difference(Image.open(r_sin / "ig" / n_sin).convert("RGB"), Image.open(r_con / "ig" / n_con).convert("RGB")).getbbox()
            difieren.append(bb)
        n_hojas = len(difieren)
        pasos_con = [k_ for k_ in range(1, n_hojas - 1) if difieren[k_]]
        caso("el sello con tu retrato sale en el CIERRE siempre y en un paso de cada 2 (el 2.º, el 4.º…); la portada y los otros pasos no cambian",
             logo_t is not None and difieren[0] is None and difieren[-1] is not None and pasos_con == [k_ for k_ in range(1, n_hojas - 1) if k_ % 2 == 0], difieren)
        caso("…y solo toca el pie (abajo a la izquierda): nada más de la diapositiva cambia",
             all(bb is None or (bb[1] > fmt_ig["alto"] - 200 and bb[0] < 520) for bb in difieren), difieren)
        sin_cada = renderiza(c, t / "con_logo", marca_t, None, None, None, logo=logo_t, logo_cada=0)
        caso("con `logo_cada: 0` solo el cierre lleva el sello",
             [bool(ImageChops.difference(Image.open(r_sin / "ig" / a).convert("RGB"), Image.open(r_con / "ig" / b).convert("RGB")).getbbox())
              for a, b in zip(a_sin["ig"], sin_cada["ig"])] == [False] * (n_hojas - 1) + [True])
    return filas
