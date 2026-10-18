"""📷 La CÁMARA GRABADA APARTE (el autor, 2026-09-28).

En la grabación normal de OBS su cámara es un recuadro de ~417×307 px dentro de la pantalla 1080p: para los modos «cara»
y «explicativa» el editor lo ampliaba ~3× y ~3,5× y su cara salía blanda («algo se está perdiendo»). Desde el 28-sep, OBS
graba ADEMÁS la fuente «Cámara» en su propio archivo a 1920×1080 (plugin Source Record, sin el borde naranja y sin el
espejo): `D:\\Videos\\<fecha hora> camara.mkv`, junto a `<fecha hora>.mkv`. Aquí:

- `busca`: el archivo «camara» de ESA grabación (junto a ella o en la carpeta de OBS; por la fecha y hora del nombre,
  aunque al subirla al panel los espacios pasen a «_» y aunque el de la cámara diga unos segundos más), con una duración
  parecida.
- ENCENDIDA por defecto en «marco» (el autor eligió la versión 3 el 28-sep): si hay archivo de cámara y cuadra, se usa; si no
  (grabaciones de antes, Source Record apagado, no sincroniza), el vídeo sale EXACTAMENTE como siempre.
- `sincroniza`: cuánto va por delante o por detrás. El archivo de cámara NO trae sonido, así que se compara el
  MOVIMIENTO del recuadro de la grabación con el del archivo de cámara (correlación de la energía de movimiento).
- `encuadra`: qué parte de la cámara enseña el recuadro y si está en espejo, buscándolo dentro de la cámara
  (correlación cruzada normalizada con numpy: sin librerías nuevas).
- `prepara`: todo junto, con caché. Si algo no cuadra (no está, no sincroniza con confianza, no se encuentra el
  recuadro) devuelve None y el vídeo sale como siempre, con el recuadro ampliado: nunca a medias.
- Cómo sale el modo «cara» (`videos.camara_aparte.cara`): "pantalla" = tu cámara a pantalla completa (la versión 2 de la
  comparación) o "marco" (el autor, 28-sep noche: «no me gusta porque le da mucho protagonismo a mi setup… un fondo naranja,
  como un marco… que ocupe por ahí el 80 %… recortarlo un poco a los costados… un cuadrado»): `recorte_marco` (un
  cuadrado de toda la altura de tu recuadro, centrado en TI: `centro_persona`), `caja_marco` y `dibuja_marco` (el fondo
  naranja de las escenas a pantalla completa, con el contorno de tinta y la sombra dura de las tarjetas).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .util import log

_DEF = {"activo": True, "carpeta": "D:/Videos", "sufijo": "camara",    # (sin archivo de cámara: como siempre)
        "zoom_cara": 1.0,          # 1.0 = el ancho que enseña tu recuadro; más alto = más plano (te alejas)
        "min_correlacion": 0.8,    # parecido mínimo de las imágenes (1 = idénticas): por debajo, no se usa
        "cara": "marco",           # el modo «cara»: "marco" (en un recuadro sobre el naranja de la marca: la que
                                   # eligió el autor) o "pantalla" (tu cámara a pantalla completa)
        "marco_alto": 0.80,        # «marco»: alto del recuadro, en fracción del alto del vídeo
        "marco_aspecto": 1.0}      # «marco»: ancho/alto del recuadro (1 = cuadrado; tu recuadro de OBS es ~1,37)
_ESTILOS = ("pantalla", "marco")
_HOLGURA_NOMBRE_S = 5.0            # la cámara puede llamarse hasta 5 s DESPUÉS que la grabación (o 2 s antes)
_FECHA = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})")
_MARCO_V = 2                       # sube si cambia cómo se dibuja el fondo de «marco» (se redibuja)
_ANCHO_T = 96                      # ancho del recuadro al buscarlo dentro de la cámara (px)


def parametros(cfg=None) -> dict:
    p = dict(_DEF)
    try:
        propios = cfg.get_path("videos.camara_aparte") or {} if cfg is not None else {}
    except Exception:  # noqa: BLE001
        propios = {}
    for k, v in (propios.items() if isinstance(propios, dict) else []):
        if k in p and v is not None:
            try:
                p[k] = bool(v) if isinstance(p[k], bool) else type(p[k])(v)
            except (TypeError, ValueError):
                pass
    return p


# ------------------------------------------------------------------------------------------------ encontrarla
def _clave(nombre: str) -> str:
    """«2026-09-28_14-52-06-2» y «2026-09-28 14-52-06» -> «2026-09-28 14-52-06» (lo que sube el panel cambia los
    espacios por «_» y una subida repetida lleva «-2»)."""
    s = str(nombre).lower().replace("_", " ").strip()
    s = re.sub(r"(\d{2}-\d{2}-\d{2})-\d{1,2}$", r"\1", s)
    return re.sub(r"\s+", " ", s)


def _duracion(ruta: Path) -> float | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(ruta)],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _instante(nombre: str) -> float | None:
    """La fecha y hora que OBS pone en el nombre («2026-09-28 16-03-33», o con «_» si pasó por el panel), en segundos."""
    m = _FECHA.search(str(nombre))
    if not m:
        return None
    try:
        import datetime as _dt
        return _dt.datetime(*(int(g) for g in m.groups())).timestamp()
    except ValueError:
        return None


def busca(source: Path, cfg=None, carpetas: list[Path] | None = None) -> Path | None:
    """El archivo de cámara de esa grabación, o None: «<el mismo nombre> camara» junto a ella o en la carpeta de OBS. Si
    el nombre lleva la fecha y hora de OBS, vale una cámara que diga hasta `_HOLGURA_NOMBRE_S` s MÁS (o 2 s menos):
    Source Record arranca unas décimas más tarde que la grabación y, si en medio cambia el segundo, su nombre dice un
    segundo más (con el nombre exacto, ~1 de cada 5 vídeos habría salido como siempre sin avisar). De varias, la más
    cercana en hora — y tiene que durar lo mismo que la grabación."""
    p = parametros(cfg)
    source = Path(source)
    clave = _clave(source.stem)
    t_src = _instante(clave)
    sufijo = " " + str(p["sufijo"]).lower()
    lugares = carpetas if carpetas is not None else [source.parent, Path(p["carpeta"])]
    candidatas: list[tuple[float, Path]] = []
    vistas: set = set()
    for carpeta in lugares:
        if not carpeta or not Path(carpeta).is_dir():
            continue
        for f in Path(carpeta).iterdir():
            if f.suffix.lower() not in (".mkv", ".mp4", ".mov") or not f.is_file():
                continue
            k = _clave(f.stem)
            if not k.endswith(sufijo):
                continue
            base_k = k[: -len(sufijo)].strip()
            if base_k == clave:
                dist = 0.0
            else:
                t_cam = _instante(base_k) if t_src is not None else None
                if t_cam is None or not (-2.0 <= t_cam - t_src <= _HOLGURA_NOMBRE_S):
                    continue
                dist = abs(t_cam - t_src)
            real = str(f.resolve()).lower()
            if real not in vistas:
                vistas.add(real)
                candidatas.append((dist, f))
    dur_src = None
    for _dist, f in sorted(candidatas, key=lambda c: c[0]):
        dur_src = dur_src if dur_src is not None else _duracion(source)
        dur_cam = _duracion(f)
        if dur_src and dur_cam and abs(dur_src - dur_cam) <= max(5.0, 0.03 * dur_src):
            return f
        log(f"Cámara aparte: {f.name} no dura lo mismo que la grabación ({dur_cam} s frente a {dur_src} s): no la uso.")
    return None


# ------------------------------------------------------------------------------------------------ fotogramas
def _grises(ruta: Path, t0: float, dur: float, fps: int, ancho: int, alto: int, crop: tuple | None = None,
            espejo: bool = False):
    """Fotogramas en gris (numpy [n, alto, ancho], float32) de un trozo del vídeo."""
    import numpy as np
    vf = []
    if crop:
        x, y, w, h = crop
        vf.append(f"crop={int(w)}:{int(h)}:{int(x)}:{int(y)}")
    if espejo:
        vf.append("hflip")
    vf += [f"fps={fps}", f"scale={ancho}:{alto}:flags=area", "format=gray"]
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{dur:.3f}", "-i", str(ruta),
           "-an", "-vf", ",".join(vf), "-f", "rawvideo", "-"]
    datos = subprocess.run(cmd, capture_output=True, timeout=600).stdout
    n = len(datos) // (ancho * alto)
    return np.frombuffer(datos[: n * ancho * alto], dtype=np.uint8).reshape(n, alto, ancho).astype(np.float32)


def _movimiento(fr):
    import numpy as np
    if len(fr) < 3:
        return np.zeros(0, dtype=np.float32)
    return np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))


# ------------------------------------------------------------------------------------------------ sincronizar
def sincroniza(src: Path, cam: Path, caja_px: tuple, dur: float, fps: int = 30, maximo_s: float = 3.0,
               ventana_s: float = 60.0, desde_s: float = 2.0, centro: float = 0.0, zona_cam: list | None = None,
               espejo: bool = False) -> tuple[float, float] | None:
    """-> (desfase, confianza): el mismo instante está en `t` en la grabación y en `t + desfase` en el archivo de
    cámara, buscado en `centro ± maximo_s`. Por la energía de movimiento del recuadro (sin su borde) y de la cámara:
    entera en la 1.ª pasada; con `zona_cam` (lo que enseña el recuadro, de `encuadra`), la MISMA zona en la 2.ª, que
    afina y da una confianza de verdad. None si no hay datos."""
    import numpy as np
    x, y, w, h = caja_px
    m = 0.06
    interior = (x + w * m, y + h * m, w * (1 - 2 * m), h * (1 - 2 * m))
    desde = min(desde_s, max(0.0, dur * 0.1))
    largo = max(2.0, min(ventana_s, dur - desde - 0.5))
    a = _movimiento(_grises(src, desde, largo, fps, 64, 48, interior))
    ini_b = max(0.0, desde + centro - maximo_s)
    if zona_cam:
        zx, zy, zw, zh = zona_cam
        zona = (zx + zw * m, zy + zh * m, zw * (1 - 2 * m), zh * (1 - 2 * m))
        b = _movimiento(_grises(cam, ini_b, largo + 2 * maximo_s, fps, 64, 48, zona, espejo))
    else:
        b = _movimiento(_grises(cam, ini_b, largo + 2 * maximo_s, fps, 96, 54))
    if len(a) < fps or len(b) < len(a):
        return None
    a = (a - a.mean()) / (a.std() + 1e-6)
    mejor, mejor_c, corrs = None, -2.0, []
    for s in range(0, len(b) - len(a) + 1):
        bb = b[s: s + len(a)]
        c = float(np.mean(a * (bb - bb.mean()) / (bb.std() + 1e-6)))
        corrs.append(c)
        if c > mejor_c:
            mejor, mejor_c = s, c
    if mejor is None:
        return None
    frac = 0.0                                       # afinar entre fotogramas (parábola alrededor del pico)
    if 0 < mejor < len(corrs) - 1:
        c0, c1, c2 = corrs[mejor - 1], corrs[mejor], corrs[mejor + 1]
        den = c0 - 2 * c1 + c2
        if den < 0:
            frac = max(-0.5, min(0.5, 0.5 * (c0 - c2) / den))
    # (a[k] es el movimiento entre k y k+1 en la grabación desde `desde`; b[j], en la cámara desde `ini_b`)
    desfase = ini_b + (mejor + frac) / fps - desde
    return round(desfase, 3), round(mejor_c, 3)


def sincroniza_imagen(src: Path, cam: Path, caja_px: tuple, dur: float, zona_cam: list, espejo: bool,
                      fps: int = 30, maximo_s: float = 3.0, ventana_s: float = 40.0,
                      desde_s: float = 2.0) -> tuple[float, float] | None:
    """El desfase comparando las IMÁGENES: una vez sabido qué zona de la cámara enseña el recuadro (`encuadra`), cada
    fotograma del recuadro tiene que parecerse al de esa zona de la cámara en `t + desfase`. Se prueba cada desfase de
    ±`maximo_s` y gana el de mayor parecido medio (correlación normalizada; 1 = idénticos). -> (desfase, parecido)."""
    import numpy as np
    x, y, w, h = caja_px
    m = 0.06
    interior = (x + w * m, y + h * m, w * (1 - 2 * m), h * (1 - 2 * m))
    zx, zy, zw, zh = zona_cam
    zona = (zx + zw * m, zy + zh * m, zw * (1 - 2 * m), zh * (1 - 2 * m))
    desde = min(desde_s, max(0.0, dur * 0.1))
    largo = max(2.0, min(ventana_s, dur - desde - 0.5))
    a = _grises(src, desde, largo, fps, 48, 36, interior)
    ini_b = max(0.0, desde - maximo_s)
    b = _grises(cam, ini_b, largo + 2 * maximo_s, fps, 48, 36, zona, espejo)
    if len(a) < fps // 2 or len(b) < len(a):
        return None

    def norma(f):
        f = f.reshape(len(f), -1)
        f = f - f.mean(axis=1, keepdims=True)
        return f / (np.linalg.norm(f, axis=1, keepdims=True) + 1e-6)

    A, B = norma(a), norma(b)
    M = A @ B.T                                           # parecido de cada fotograma del recuadro con cada uno de la cámara
    n = len(A)
    medias = [float(np.mean(M[np.arange(n), np.arange(n) + s])) for s in range(0, len(B) - n + 1)]
    mejor = int(np.argmax(medias))
    frac = 0.0
    if 0 < mejor < len(medias) - 1:
        c0, c1, c2 = medias[mejor - 1], medias[mejor], medias[mejor + 1]
        den = c0 - 2 * c1 + c2
        if den < 0:
            frac = max(-0.5, min(0.5, 0.5 * (c0 - c2) / den))
    return round(ini_b + (mejor + frac) / fps - desde, 3), round(medias[mejor], 3)


# ------------------------------------------------------------------------------------------------ encuadrar
def _ncc(img, tpl):
    """Correlación cruzada normalizada de `tpl` en cada posición válida de `img` (FFT + imágenes integrales)."""
    import numpy as np
    H, W = img.shape
    h, w = tpl.shape
    if H < h or W < w:
        return None
    t = tpl - tpl.mean()
    tn = np.sqrt((t * t).sum()) + 1e-6
    fh, fw = H + h - 1, W + w - 1
    num = np.fft.irfft2(np.fft.rfft2(img, (fh, fw)) * np.fft.rfft2(t[::-1, ::-1], (fh, fw)), (fh, fw))
    num = num[h - 1:H, w - 1:W]
    ii = np.pad(img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ii2 = np.pad(img * img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def suma(z):
        return z[h:, w:] - z[:-h, w:] - z[h:, :-w] + z[:-h, :-w]

    s1, s2 = suma(ii), suma(ii2)
    var = np.maximum(s2 - s1 * s1 / (h * w), 1e-6)
    return num / (tn * np.sqrt(var))


def encuadra(src: Path, cam: Path, caja_px: tuple, desfase: float, tiempos: list[float],
             ancho_cam: int, alto_cam: int) -> dict | None:
    """Qué zona de la cámara enseña el recuadro de la grabación y si va en espejo: se busca el recuadro (sin su borde)
    dentro de la cámara a distintos tamaños, en espejo y sin él. -> {"caja": [x, y, w, h] en px de la cámara,
    "espejo", "ncc"} (mediana de varios instantes) o None si no se encuentra con claridad."""
    import numpy as np
    x, y, w, h = caja_px
    m = 0.06
    interior = (x + w * m, y + h * m, w * (1 - 2 * m), h * (1 - 2 * m))
    th = max(8, round(_ANCHO_T * interior[3] / interior[2]))
    resultados = []
    for t in tiempos:
        tpl = _grises(src, t, 0.05, 30, _ANCHO_T, th, interior)
        if not len(tpl):
            continue
        tpl = tpl[0]
        mejor = None
        for espejo in (False, True):
            base = _grises(cam, t + desfase, 0.05, 30, 480, 270, None, espejo)
            if not len(base):
                continue
            base = base[0]
            # ancho de la zona buscada, en px de la cámara: del 20 % al 100 % de su ancho
            for frac_w in np.geomspace(0.2, 1.0, 34):
                zona_w = frac_w * ancho_cam
                k = _ANCHO_T / zona_w                       # px del recuadro por px de la cámara
                cw, ch = max(_ANCHO_T, round(ancho_cam * k)), max(th, round(alto_cam * k))
                img = _redimensiona(base, cw, ch)
                mapa = _ncc(img, tpl)
                if mapa is None:
                    continue
                iy, ix = np.unravel_index(int(np.argmax(mapa)), mapa.shape)
                v = float(mapa[iy, ix])
                if mejor is None or v > mejor[0]:
                    mejor = (v, espejo, ix / k, iy / k, zona_w, th / k)
        if mejor:
            resultados.append(mejor)
    if not resultados:
        return None
    ncc = float(np.median([r[0] for r in resultados]))
    espejos = [r[1] for r in resultados]
    espejo = max(set(espejos), key=espejos.count)
    buenos = [r for r in resultados if r[1] == espejo]
    zx, zy, zw, zh = (float(np.median([r[i] for r in buenos])) for i in (2, 3, 4, 5))
    if espejo:                                             # se buscó en la cámara volteada: a coordenadas de la original
        zx = ancho_cam - (zx + zw)
    # del interior (sin el borde) a la zona entera que enseña el recuadro
    mx, my = zw * m / (1 - 2 * m), zh * m / (1 - 2 * m)
    caja = [max(0.0, zx - mx), max(0.0, zy - my), min(float(ancho_cam), zw + 2 * mx), min(float(alto_cam), zh + 2 * my)]
    caja[2] = min(caja[2], ancho_cam - caja[0])
    caja[3] = min(caja[3], alto_cam - caja[1])
    if ncc < 0.6:
        return None
    return {"caja": [round(v, 1) for v in caja], "espejo": bool(espejo), "ncc": round(ncc, 3)}


def _redimensiona(img, w: int, h: int):
    """Reescalado por área (media de bloques) con numpy, suficiente para buscar el recuadro."""
    import numpy as np
    H, W = img.shape
    ys = (np.arange(h + 1) * H / h).astype(int)
    xs = (np.arange(w + 1) * W / w).astype(int)
    ii = np.pad(img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    y0, y1 = ys[:-1], np.maximum(ys[1:], ys[:-1] + 1)
    x0, x1 = xs[:-1], np.maximum(xs[1:], xs[:-1] + 1)
    s = ii[y1][:, x1] - ii[y0][:, x1] - ii[y1][:, x0] + ii[y0][:, x0]
    return s / ((y1 - y0)[:, None] * (x1 - x0)[None, :])


# ------------------------------------------------------------------------------------------------ todo junto
def _meta(ruta: Path) -> tuple[int, int, float] | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "json", str(ruta)],
                           capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout)
        s = d["streams"][0]
        return int(s["width"]), int(s["height"]), float(d["format"]["duration"])
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def _encuadra_y_sincroniza(src: Path, cam: Path, caja_px: tuple, dur: float, ancho_cam: int, alto_cam: int,
                           desde_s: float = 2.0) -> tuple[dict | None, tuple[float, float] | None]:
    """1) El desfase APROXIMADO por el movimiento de la cámara entera (`sincroniza`). 2) Dónde está el recuadro dentro
    de la cámara, buscándolo en ese desfase y en 0 (OBS arranca los dos casi a la vez): gana el de mayor parecido (en la
    prueba: 0,987 en el desfase bueno frente a 0,871 en 0). 3) El desfase EXACTO comparando las imágenes de esa zona
    (`sincroniza_imagen`). 4) Si se movió, se vuelve a encuadrar una vez con él. -> (encuadre, (desfase, parecido))."""
    tiempos = [dur * f for f in (0.3, 0.5, 0.7)]
    s1 = sincroniza(src, cam, caja_px, dur, desde_s=desde_s)
    desfases = [0.0] + ([s1[0]] if s1 and abs(s1[0]) > 0.05 else [])
    candidatos = []
    for off in desfases:
        e = encuadra(src, cam, caja_px, off, tiempos, ancho_cam, alto_cam)
        if e is not None:
            candidatos.append((e["ncc"], off, e))
    if not candidatos:
        return None, None
    _ncc_, usado, e = max(candidatos, key=lambda c: c[0])
    s2 = sincroniza_imagen(src, cam, caja_px, dur, e["caja"], e["espejo"], desde_s=desde_s)
    if s2 and abs(s2[0] - usado) > 0.15:
        e2 = encuadra(src, cam, caja_px, s2[0], tiempos, ancho_cam, alto_cam)
        if e2 is not None and e2["ncc"] > e["ncc"]:
            e = e2
            s2 = sincroniza_imagen(src, cam, caja_px, dur, e["caja"], e["espejo"], desde_s=desde_s) or s2
    return e, s2


def prepara(source: Path, meta: dict, camara_rect: dict | None, cfg, work: Path | None = None,
            cara: str | None = None) -> dict | None:
    """El archivo de cámara de esta grabación, sincronizado y encuadrado, para `render.render_merged(camara_aparte=…)`:
    {"ruta", "desfase", "espejo", "caja", "ancho", "alto", "duracion", "zoom_cara", "cara", …} — o None (el vídeo sale
    como siempre). `cara` ("pantalla"/"marco") manda sobre `videos.camara_aparte.cara` (la versión de comparación). Con
    caché en `work/camara_aparte.json` (no se recalcula si nada cambió; dónde estás tú, solo para «marco» y una vez)."""
    p = parametros(cfg)
    estilo = cara if cara in _ESTILOS else (p["cara"] if p["cara"] in _ESTILOS else "pantalla")
    if not camara_rect:                                  # (si se usa o no lo decide quien llama: `activo` o --camara-aparte)
        return None
    cam = busca(Path(source), cfg)
    if cam is None:
        log(f"Cámara aparte: no hay archivo «… {p['sufijo']}» de esta grabación (ni junto a ella ni en "
            f"{p['carpeta']}): tu cara sale del recuadro, como siempre.")
        return None
    info = _meta(cam)
    if not info:
        return None
    ancho_cam, alto_cam, dur_cam = info
    firma = {"cam": str(cam), "tam": cam.stat().st_size, "src_tam": Path(source).stat().st_size,
             "rect": [round(float(camara_rect[k]), 4) for k in ("x", "y", "w", "h")], "v": 1}
    cache = Path(work) / "camara_aparte.json" if work else None
    if cache and cache.is_file():
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("firma") == firma:
                if c.get("ok"):
                    return _con_estilo(c["resultado"], p, estilo, cache, c)
                return None
        except (OSError, ValueError):
            pass
    src_w, src_h = int(meta["width"]), int(meta["height"])
    caja_px = (float(camara_rect["x"]) * src_w, float(camara_rect["y"]) * src_h,
               float(camara_rect["w"]) * src_w, float(camara_rect["h"]) * src_h)
    dur = float(meta.get("duration") or dur_cam)
    resultado, motivo = None, ""
    e, s2 = _encuadra_y_sincroniza(Path(source), cam, caja_px, dur, ancho_cam, alto_cam)
    if e is None:
        motivo = "no encuentro tu recuadro dentro de la cámara"
    elif s2 is None or s2[1] < p["min_correlacion"]:
        motivo = f"no sincroniza con confianza (correlación {s2[1] if s2 else '—'})"
    else:
        desfase, conf = s2
        resultado = {"ruta": str(cam), "desfase": desfase, "confianza": conf, "espejo": e["espejo"],
                     "caja": e["caja"], "ncc": e["ncc"], "ancho": ancho_cam, "alto": alto_cam, "duracion": dur_cam}
    if cache:
        try:
            cache.write_text(json.dumps({"firma": firma, "ok": resultado is not None, "resultado": resultado,
                                         "motivo": motivo}, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    if resultado is None:
        log(f"Cámara aparte: {cam.name} encontrada, pero {motivo}: tu cara sale del recuadro, como siempre.")
        return None
    log(f"Cámara aparte: {cam.name} ({ancho_cam}×{alto_cam}) — va {resultado['desfase']:+.2f} s respecto a la "
        f"grabación (confianza {resultado['confianza']:.2f}); tu recuadro es su zona "
        f"{int(resultado['caja'][2])}×{int(resultado['caja'][3])}{', en espejo' if resultado['espejo'] else ''}. "
        f"Los modos «cara» y «explicativa» la usan a resolución completa.")
    return _con_estilo(resultado, p, estilo, cache, None)


def _con_estilo(resultado: dict, p: dict, estilo: str, cache: Path | None, datos_cache: dict | None) -> dict:
    """El resultado + cómo sale el modo «cara». Para «marco», dónde estás tú dentro de tu recuadro (`centro_x`): se
    mide una vez por grabación y se guarda en la caché."""
    r = {**resultado, "zoom_cara": p["zoom_cara"], "cara": estilo, "marco_alto": p["marco_alto"],
         "marco_aspecto": p["marco_aspecto"]}
    if estilo == "marco" and r.get("centro_x") is None:
        cx = centro_persona(Path(r["ruta"]), float(r.get("duracion") or 0.0), r["caja"])
        if cx is not None:
            r["centro_x"] = round(cx, 1)
            if cache:
                try:
                    c = datos_cache if datos_cache is not None else json.loads(cache.read_text(encoding="utf-8"))
                    if isinstance(c.get("resultado"), dict):
                        c["resultado"]["centro_x"] = r["centro_x"]
                        cache.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
                except (OSError, ValueError):
                    pass
    return r


def centro_persona(cam: Path, dur: float, caja, n: int = 12) -> float | None:
    """Dónde estás TÚ en horizontal (x en píxeles de la cámara): el cuarto no se mueve y tú sí, así que es la mediana
    de la energía de movimiento por columnas dentro de tu recuadro, en `n` ventanas de 1 s repartidas por la grabación,
    solo en su franja de ARRIBA (cabeza y hombros: las manos, que se mueven más hacia un lado, no desplazan el encuadre)
    y sin el ruido de la compresión (por debajo de un umbral). Para centrar el cuadrado de «marco» en ti y no en el
    hueco que dejaste en OBS. None si no se mueve nada."""
    import numpy as np
    if dur <= 2.0:
        return None
    x, y, w, h = (float(v) for v in caja)
    ancho = 160
    alto = max(2, int(round(ancho * h / max(1.0, w) / 2)) * 2)
    perfil = np.zeros(ancho, dtype=np.float64)
    for k in range(n):
        fr = _grises(cam, (dur - 1.0) * (k + 0.5) / n, 1.0, 10, ancho, alto, (x, y, w, h))
        if len(fr) >= 2:
            d = np.abs(np.diff(fr, axis=0))
            d[d < 4.0] = 0.0
            perfil += d[:, : max(1, int(alto * 0.6)), :].sum(axis=(0, 1))
    if perfil.sum() <= 0:
        return None
    acum = np.cumsum(perfil)
    i = int(np.searchsorted(acum, acum[-1] / 2))
    return x + (i + 0.5) * w / ancho


def recorte_cara(ca: dict, ancho_out: int, alto_out: int) -> tuple[int, int, int, int]:
    """El encuadre del modo «cara» dentro de la cámara: la anchura que enseña tu recuadro (× `zoom_cara`), en la
    proporción del vídeo, con algo más de aire arriba que abajo (la cabeza no se corta). -> (x, y, w, h) pares."""
    W, H = int(ca["ancho"]), int(ca["alto"])
    x, y, w, h = ca["caja"]
    cw = min(float(W), w * max(0.5, float(ca.get("zoom_cara") or 1.0)))
    ch = cw * alto_out / ancho_out
    if ch > H:
        ch, cw = float(H), H * ancho_out / alto_out
    cx = min(max(0.0, x + w / 2 - cw / 2), W - cw)
    cy = min(max(0.0, y + (h - ch) * 0.25), H - ch)
    par = lambda v: int(round(v / 2)) * 2                  # noqa: E731
    return par(cx), par(cy), par(cw), par(ch)


def recorte_marco(ca: dict) -> tuple[int, int, int, int]:
    """«marco»: la parte de la cámara que va en el recuadro — toda la altura de tu recuadro de OBS (el mismo plano que
    ya tenías: no acerca más) y recortada por los lados a `marco_aspecto` (1 = cuadrado: se ve menos cuarto), centrada
    en ti (`centro_x`) sin salirse de lo que enseña tu recuadro. -> (x, y, w, h) pares, en píxeles de la cámara."""
    W, H = int(ca["ancho"]), int(ca["alto"])
    x, y, w, h = (float(v) for v in ca["caja"])
    asp = min(3.0, max(0.3, float(ca.get("marco_aspecto") or 1.0)))
    ch = min(float(H), h)
    cw = ch * asp
    if cw > W:
        cw, ch = float(W), W / asp
    centro = ca.get("centro_x")
    c = float(centro) if centro is not None else x + w / 2
    izq = c - cw / 2
    if cw <= w:
        izq = min(max(izq, x), x + w - cw)
    else:
        izq = x + w / 2 - cw / 2
    izq = min(max(0.0, izq), W - cw)
    cy = min(max(0.0, y + (h - ch) / 2), H - ch)
    par = lambda v: int(round(v / 2)) * 2                  # noqa: E731
    return par(izq), par(cy), par(cw), par(ch)


def caja_marco(ca: dict, ancho_out: int, alto_out: int) -> tuple[int, int, int, int, int, int]:
    """«marco»: dónde va el recuadro en el vídeo -> (x, y, ancho, alto, contorno, sombra) en píxeles: `marco_alto` del
    alto, `marco_aspecto` de proporción, centrado contando su sombra (abajo-derecha)."""
    par = lambda v: max(2, int(round(v / 2)) * 2)          # noqa: E731
    alto_f = min(0.95, max(0.4, float(ca.get("marco_alto") or 0.8)))
    asp = min(3.0, max(0.3, float(ca.get("marco_aspecto") or 1.0)))
    b = max(4, round(alto_out * 0.007))                    # contorno de tinta
    s = max(6, round(alto_out * 0.016))                    # sombra dura
    bh = par(alto_out * alto_f)
    bw = par(bh * asp)
    if bw + 2 * b + s > ancho_out * 0.95:
        bw = par(ancho_out * 0.95 - 2 * b - s)
        bh = par(bw / asp)
    return par((ancho_out - bw - s) / 2), par((alto_out - bh - s) / 2), bw, bh, b, s


def dibuja_marco(ca: dict, marca: dict, ancho_out: int, alto_out: int, carpeta: Path) -> dict:
    """El fondo de «marco» (un PNG por vídeo, con caché por contenido): el naranja de la marca con su trama de puntos
    (`escena_completa.fondo`, el de las escenas a pantalla completa), la sombra dura y el contorno de tinta del recuadro,
    y un HUECO transparente del tamaño del recuadro: el render pone la cámara DEBAJO (su ritmo, tal cual) y esto encima.
    -> {"marco_png", "marco_caja": [x, y, ancho, alto]}."""
    import hashlib
    from PIL import ImageDraw
    from .escena_completa import fondo
    from .ilustraciones import paleta
    bx, by, bw, bh, b, s = caja_marco(ca, ancho_out, alto_out)
    pal = paleta(marca)
    clave = hashlib.sha1(json.dumps([_MARCO_V, ancho_out, alto_out, bx, by, bw, bh, b, s, pal.clave()]).encode("utf-8"))
    png = Path(carpeta) / f"marco_{clave.hexdigest()[:12]}.png"
    if not png.is_file():
        png.parent.mkdir(parents=True, exist_ok=True)
        im = fondo(ancho_out, alto_out, pal, "naranja").convert("RGBA")
        d = ImageDraw.Draw(im)
        tinta = tuple(pal.tinta[:3]) + (255,)
        d.rectangle((bx - b + s, by - b + s, bx + bw + b - 1 + s, by + bh + b - 1 + s), fill=tinta)
        d.rectangle((bx - b, by - b, bx + bw + b - 1, by + bh + b - 1), fill=tinta)
        d.rectangle((bx, by, bx + bw - 1, by + bh - 1), fill=(0, 0, 0, 0))      # el hueco: ahí se ve la cámara
        tmp = png.with_name(png.stem + "_tmp.png")
        im.save(tmp)
        tmp.replace(png)
    return {"marco_png": str(png), "marco_caja": [bx, by, bw, bh]}


def recorte_explicativa(ca: dict, ancho_tercio: int, alto_out: int) -> tuple[int, int, int, int]:
    """El tercio derecho de «explicativa»: una franja vertical de la cámara centrada en tu recuadro, con toda su
    altura. -> (x, y, w, h) pares."""
    W, H = int(ca["ancho"]), int(ca["alto"])
    x, y, w, h = ca["caja"]
    ch = min(float(H), max(h, H * 0.6))
    cw = ch * ancho_tercio / alto_out
    if cw > W:
        cw, ch = float(W), W * alto_out / ancho_tercio
    cx = min(max(0.0, x + w / 2 - cw / 2), W - cw)
    cy = min(max(0.0, y + (h - ch) / 2), H - ch)
    par = lambda v: int(round(v / 2)) * 2                  # noqa: E731
    return par(cx), par(cy), par(cw), par(ch)


# ------------------------------------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    """Con vídeos SINTÉTICOS hechos con ffmpeg (una «cámara» con movimiento irregular y una «grabación» que la enseña
    pequeña, recortada y en espejo, empezando 0,4 s más tarde): encontrar, sincronizar y encuadrar."""
    import shutil
    import tempfile
    filas: list[dict] = []

    def r(caso, ok, detalle=""):
        filas.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)[:500]})

    r("el nombre que deja el panel («_», «-2») casa con el de OBS",
      _clave("2026-09-28_14-52-06") == _clave("2026-09-28 14-52-06") == _clave("2026-09-28_14-52-06-2"))
    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-camara-"))
    try:
        cam = tmp / "2026-09-28 14-52-06 camara.mkv"
        src = tmp / "2026-09-28 14-52-06.mkv"
        # la «cámara»: un fondo fijo con textura y un «tú» (una elipse) que se mueve de forma irregular (sumas de
        # senos de frecuencias que no se repiten), como una persona delante de la cámara; 640×360 a 30 fps
        from PIL import Image, ImageDraw
        import numpy as np
        rng = np.random.default_rng(7)
        # un «cuarto»: degradado de izquierda a derecha, una ventana clara a un lado y un mueble oscuro al otro (nada
        # simétrico, como una habitación de verdad) y una textura suave
        xs = np.linspace(0, 1, 640)[None, :, None]
        base = 40 + 150 * xs * np.array([0.6, 0.8, 1.0])[None, None, :] + rng.random((360, 640, 3)) * 25
        fondo = Image.fromarray(np.clip(base, 0, 255).astype("uint8"))
        d_ = ImageDraw.Draw(fondo)
        d_.rectangle((90, 150, 230, 360), fill=(25, 20, 18))
        d_.ellipse((470, 20, 600, 150), fill=(250, 250, 235))
        fondo.save(tmp / "fondo.png")
        yo = Image.new("RGBA", (120, 160), (0, 0, 0, 0))
        ImageDraw.Draw(yo).ellipse((0, 0, 119, 159), fill=(240, 200, 120, 255))
        ImageDraw.Draw(yo).rectangle((70, 40, 110, 64), fill=(40, 30, 30, 255))      # un rasgo a UN lado
        yo.save(tmp / "yo.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(tmp / "fondo.png"),
                        "-loop", "1", "-framerate", "30", "-i", str(tmp / "yo.png"), "-filter_complex",
                        "[0:v][1:v]overlay=x='230+110*sin(t*1.3)+50*sin(t*3.7+1)':y='90+60*sin(t*1.7+2)+25*sin(t*5.3)',"
                        "format=yuv420p", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", str(cam)],
                       check=True)
        # la «grabación»: fondo, y la cámara recortada (x 60..540), en ESPEJO, a 0,4 de tamaño, abajo a la derecha,
        # EMPEZANDO 0,4 s más tarde (lo que en la grabación pasa en t, en la cámara pasa en t + 0,4)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=#e8e0d0:s=1280x720:r=30:d=9.6",
                        "-ss", "0.4", "-i", str(cam), "-f", "lavfi", "-i", "sine=frequency=220:duration=9.6",
                        "-filter_complex",
                        "[1:v]crop=480:360:60:0,hflip,scale=192:144,setpts=PTS-STARTPTS[c];[0:v][c]overlay=1040:540[v]",
                        "-map", "[v]", "-map", "2:a", "-t", "9.6", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
                        "-c:a", "aac", str(src)], check=True)                # (con sonido, como una grabación de verdad)
        subida = tmp / "inbox"
        subida.mkdir()
        shutil.copy2(src, subida / "2026-09-28_14-52-06.mkv")
        encontrada = busca(subida / "2026-09-28_14-52-06.mkv", None, carpetas=[subida, tmp])
        r("encuentra el archivo «camara» de la grabación subida (en la carpeta de OBS)", encontrada == cam, encontrada)
        # Source Record arranca unas décimas tarde: si en medio cambia el segundo, su nombre dice un segundo más
        tarde = tmp / "obs_tarde"
        tarde.mkdir()
        shutil.copy2(cam, tarde / "2026-09-28 14-52-07 camara.mkv")
        shutil.copy2(cam, tarde / "2026-09-28 14-52-36 camara.mkv")         # la de OTRA grabación, 30 s después
        r("encuentra la cámara aunque su nombre diga un segundo más (arrancó unas décimas tarde)",
          busca(subida / "2026-09-28_14-52-06.mkv", None, carpetas=[subida, tarde])
          == tarde / "2026-09-28 14-52-07 camara.mkv")
        (tarde / "2026-09-28 14-52-07 camara.mkv").unlink()
        r("no confunde la cámara de OTRA grabación (30 s después): sin la suya, como siempre",
          busca(subida / "2026-09-28_14-52-06.mkv", None, carpetas=[subida, tarde]) is None)
        pd_ = parametros(None)
        r("por defecto: encendida y en «marco» (la versión 3 que eligió el autor)",
          pd_["activo"] is True and pd_["cara"] == "marco", pd_)
        caja = (1040, 540, 192, 144)
        e, s2 = _encuadra_y_sincroniza(src, cam, caja, 9.6, 640, 360, desde_s=1.0)
        r("sincroniza comparando las imágenes de la misma zona: el desfase exacto (+0,4 s, ±1 fotograma), con un "
          "parecido alto", s2 is not None and abs(s2[0] - 0.4) <= 0.035 and s2[1] > 0.8, s2)
        r("encuentra tu recuadro dentro de la cámara, y que va en espejo",
          e is not None and e["espejo"] and abs(e["caja"][0] - 60) <= 16 and abs(e["caja"][2] - 480) <= 30
          and abs(e["caja"][1]) <= 16 and abs(e["caja"][3] - 360) <= 24, e)
        # el render: un tramo «cara» sale de la CÁMARA (sincronizada, en espejo, a su resolución), no del recuadro
        if e and s2:
            from . import config as _config, ingest as _ingest, render as _render
            cfg_ = _config.load()
            meta_ = _ingest.probe(src)
            rect_ = {"x": 1040 / 1280, "y": 540 / 720, "w": 192 / 1280, "h": 144 / 720, "zoom_cara": 1.7}
            cam_ap = {"ruta": str(cam), "desfase": s2[0], "espejo": e["espejo"], "caja": e["caja"], "ancho": 640,
                      "alto": 360, "duracion": 10.0, "zoom_cara": 1.0}
            salida = tmp / "cara.mp4"
            _render.render_merged(src, [(2.0, 4.0)], cfg_, meta_, None, salida, modos=["cara"], camara_rect=rect_,
                                  camara_aparte=cam_ap)
            import numpy as np
            fin = _grises(salida, 1.0, 0.05, 30, 96, 54)
            x_, y_, w_, h_ = recorte_cara({**cam_ap}, 1920, 1080)
            esperado = _grises(cam, 3.0 + s2[0], 0.05, 30, 96, 54, (x_, y_, w_, h_), e["espejo"])
            parecido = float(np.corrcoef(fin[0].ravel(), esperado[0].ravel())[0, 1]) if len(fin) and len(esperado) else 0.0
            r("el render del modo «cara» sale de la cámara aparte (sincronizada y en espejo), no del recuadro ampliado",
              parecido > 0.8, parecido)

            # la DESPEDIDA (28-sep-2026): la cámara para unas décimas ANTES que la grabación y el último tramo, que
            # acaba con ella, se salía del archivo -> salía del recuadro ampliado. Una cámara que acaba 0,3 s antes
            # de lo que pide el tramo: la usa igual (último fotograma quieto ese trocito), dura LO MISMO que su
            # sonido, y lo de antes de ese trocito sigue sincronizado
            corta = tmp / "camara_corta.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(cam), "-t", "9.7", "-c", "copy", str(corta)],
                           check=True)
            d_corta = _duracion(corta) or 9.7
            fin_rec = d_corta - s2[0] + 0.3              # el tramo acaba 0,3 s después de que acabe la cámara
            ini_rec = fin_rec - 1.6
            salida2 = tmp / "despedida.mp4"
            _render.render_merged(src, [(ini_rec, fin_rec)], cfg_, meta_, None, salida2, modos=["cara"],
                                  camara_rect=rect_, camara_aparte={**cam_ap, "ruta": str(corta), "duracion": d_corta})
            def _dur_de(ruta, flujo):
                p_ = subprocess.run(["ffprobe", "-v", "error", "-select_streams", flujo, "-show_entries",
                                     "stream=duration", "-of", "default=nw=1:nk=1", str(ruta)],
                                    capture_output=True, text=True)
                try:
                    return float(p_.stdout.strip().splitlines()[0])
                except (ValueError, IndexError):
                    return 0.0
            dv, da = _dur_de(salida2, "v:0"), _dur_de(salida2, "a:0")
            fr2 = _grises(salida2, 0.6, 0.05, 30, 96, 54)
            esp2 = _grises(corta, ini_rec + 0.6 + s2[0], 0.05, 30, 96, 54, (x_, y_, w_, h_), e["espejo"])
            par2 = float(np.corrcoef(fr2[0].ravel(), esp2[0].ravel())[0, 1]) if len(fr2) and len(esp2) else 0.0
            r("la despedida: si la cámara acaba unas décimas antes que el tramo, la usa igual (sincronizada) y la "
              "imagen dura lo mismo que el sonido", par2 > 0.8 and abs(dv - 1.6) <= 0.05 and abs(dv - da) <= 0.05,
              f"parecido {par2:.2f}, vídeo {dv:.3f} s, sonido {da:.3f} s")
            # y si al tramo le falta mucho más archivo de cámara que eso (Source Record empezó tarde), sale del
            # recuadro ampliado, como siempre (sin romper el render)
            salida3 = tmp / "sin_camara.mp4"
            _render.render_merged(src, [(1.0, 3.0)], cfg_, meta_, None, salida3, modos=["cara"], camara_rect=rect_,
                                  camara_aparte={**cam_ap, "desfase": s2[0] - 2.5})
            salida4 = tmp / "como_siempre.mp4"
            _render.render_merged(src, [(1.0, 3.0)], cfg_, meta_, None, salida4, modos=["cara"], camara_rect=rect_)
            fr3 = _grises(salida3, 1.0, 0.05, 30, 96, 54)
            fr4 = _grises(salida4, 1.0, 0.05, 30, 96, 54)
            par3 = float(np.corrcoef(fr3[0].ravel(), fr4[0].ravel())[0, 1]) if len(fr3) and len(fr4) else 0.0
            r("un tramo al que le falta más de 1 s de cámara sale del recuadro ampliado, como siempre",
              par3 > 0.98 and abs(_dur_de(salida3, "v:0") - 2.0) <= 0.05, f"parecido con el de siempre {par3:.3f}")

            # «marco» (el autor, 28-sep noche): tu cámara en un CUADRADO al 80 % del alto sobre el naranja de la marca
            # (contorno de tinta y sombra dura), centrado en TI — la elipse que se mueve tiene su centro en x≈290
            cx_yo = centro_persona(cam, 10.0, e["caja"])
            r("«marco»: encuentra dónde estás tú dentro de tu recuadro (el centro del movimiento, no el del recuadro)",
              cx_yo is not None and abs(cx_yo - 290) <= 45, cx_yo)
            marca_ = dict(cfg_.get_path("videos.marca") or {})
            ca_m = {**cam_ap, "cara": "marco", "marco_alto": 0.8, "marco_aspecto": 1.0,
                    "centro_x": cx_yo if cx_yo is not None else 290.0}
            dm = dibuja_marco(ca_m, marca_, 1920, 1080, tmp / "marco")
            t_png = Path(dm["marco_png"]).stat().st_mtime_ns
            r("«marco»: el fondo se dibuja una vez y se reusa (misma marca y mismo tamaño -> mismo PNG)",
              dibuja_marco(ca_m, marca_, 1920, 1080, tmp / "marco") == dm
              and Path(dm["marco_png"]).stat().st_mtime_ns == t_png, dm)
            ca_m.update(dm)
            salida5 = tmp / "marco.mp4"
            _render.render_merged(src, [(2.0, 4.0)], cfg_, meta_, None, salida5, modos=["cara"], camara_rect=rect_,
                                  camara_aparte=ca_m)
            bx_, by_, bw_, bh_ = ca_m["marco_caja"]
            fr5 = _grises(salida5, 1.0, 0.05, 30, 64, 64, (bx_, by_, bw_, bh_))
            esp5 = _grises(cam, 3.0 + s2[0], 0.05, 30, 64, 64, recorte_marco(ca_m), e["espejo"])
            par5 = float(np.corrcoef(fr5[0].ravel(), esp5[0].ravel())[0, 1]) if len(fr5) and len(esp5) else 0.0
            p5 = tmp / "marco.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.0", "-i", str(salida5), "-frames:v", "1", str(p5)],
                           check=True)
            im5 = np.asarray(Image.open(p5).convert("RGB")).astype(np.float32)
            margen = np.median(im5[300:780, 40:240].reshape(-1, 3), axis=0)
            hx = str(marca_.get("naranja") or "#FE330A").lstrip("#")
            nar = np.array([int(hx[k:k + 2], 16) for k in (0, 2, 4)], dtype=np.float32)
            contorno = im5[by_ - 3, bx_ + bw_ // 2]
            # el RITMO, fotograma a fotograma: cada fotograma del recuadro tiene que ser EL MISMO de la cámara (ni el
            # anterior ni el siguiente). Pegando la cámara encima del PNG en bucle, `overlay` elegía por marca de
            # tiempo y las del .mkv van al milisegundo: salían fotogramas repetidos y saltados (visto en la versión 3)
            fps_s = int(round(float(meta_.get("fps") or 30)))
            fr6 = _grises(salida5, 0.5, 0.7, fps_s, 48, 48, (bx_, by_, bw_, bh_))
            cam6 = _grises(cam, 2.5 + s2[0] - 1.0 / fps_s, 0.7 + 2.0 / fps_s, fps_s, 48, 48, recorte_marco(ca_m),
                           e["espejo"])
            fuera_de_sitio = []
            for k_ in range(min(len(fr6), len(cam6) - 2)):
                cs_ = [float(np.corrcoef(fr6[k_].ravel(), cam6[k_ + j_].ravel())[0, 1]) for j_ in range(3)]
                if cs_[1] < max(cs_) - 0.002:          # (dos fotogramas iguales de la cámara empatan: vale)
                    fuera_de_sitio.append(k_)
            r("«marco»: cada fotograma de tu cámara sale en su sitio (ni repetidos ni saltados: el ritmo de la cámara)",
              len(fr6) >= 15 and not fuera_de_sitio, f"{len(fr6)} fotogramas; fuera de sitio: {fuera_de_sitio}")
            r("«marco»: tu cámara (sincronizada, en espejo) dentro del recuadro, tu naranja alrededor con el contorno de "
              "tinta, y el tramo dura lo mismo",
              par5 > 0.8 and bool(np.all(np.abs(margen - nar) <= 30)) and float(contorno.sum()) < 150
              and abs(_dur_de(salida5, "v:0") - 2.0) <= 0.05,
              f"parecido {par5:.2f}, recuadro {ca_m['marco_caja']}, margen {margen.tolist()} (naranja {nar.tolist()}), "
              f"contorno {contorno.tolist()}")
        ca = {"ancho": 1920, "alto": 1080, "caja": [54, 0, 1420, 1061], "zoom_cara": 1.0}
        rc = recorte_cara(ca, 1920, 1080)
        r("modo «cara»: el ancho de tu recuadro en 16:9, sin salirse de la cámara (sin ampliar de más)",
          rc[2] == 1420 and rc[3] == 798 and 0 <= rc[0] <= 1920 - rc[2] and 0 <= rc[1] <= 1080 - rc[3], rc)
        re_ = recorte_explicativa(ca, 640, 1080)
        r("explicativa: una franja vertical de la cámara de toda su altura, centrada en tu recuadro",
          re_[3] == 1060 and abs(re_[2] - 628) <= 2 and abs((re_[0] + re_[2] / 2) - (54 + 710)) <= 4, re_)
        cm = recorte_marco({"ancho": 1920, "alto": 1080, "caja": [17.2, 0, 1477, 1080], "marco_aspecto": 1.0})
        r("«marco»: un CUADRADO de toda la altura de tu recuadro (el mismo plano), centrado en él si no sé dónde estás",
          cm[2] == cm[3] == 1080 and abs((cm[0] + cm[2] / 2) - (17.2 + 1477 / 2)) <= 2, cm)
        cm2 = recorte_marco({"ancho": 1920, "alto": 1080, "caja": [17.2, 0, 1477, 1080], "marco_aspecto": 1.0,
                             "centro_x": 1450.0})
        r("«marco»: centrado en ti, pero sin salirse de lo que enseña tu recuadro de OBS",
          cm2[2] == 1080 and cm2[0] >= 16 and cm2[0] + cm2[2] <= 17.2 + 1477 + 2, cm2)
        cj = caja_marco({"marco_alto": 0.8, "marco_aspecto": 1.0}, 1920, 1080)
        r("«marco»: el recuadro ocupa el 80 % del alto, cuadrado, centrado contando su sombra, en píxeles pares",
          cj[2] == cj[3] == 864 and all(v % 2 == 0 for v in cj[:4])
          and abs(cj[0] + (cj[2] + cj[5]) / 2 - 960) <= 2 and abs(cj[1] + (cj[3] + cj[5]) / 2 - 540) <= 2, cj)
        r("sin archivo de cámara, nada (el vídeo sale como siempre)",
          busca(tmp / "otra.mkv", None, carpetas=[tmp]) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return filas


if __name__ == "__main__":
    import sys
    f_ = prueba()
    for x_ in f_:
        print(("OK   " if x_["ok"] else "FALLA") + " " + x_["caso"] + ("" if x_["ok"] else f"  -> {x_['detalle']}"))
    sys.exit(1 if any(not x_["ok"] for x_ in f_) else 0)
