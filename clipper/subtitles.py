"""Subtítulos ASS quemados, palabra por palabra.

Recordatorio sobre colores en ASS: el formato es &HAABBGGRR.
  - El orden es AZUL, VERDE, ROJO (al revés que el hex de CSS).
  - AA es transparencia donde 00 = OPACO y FF = transparente.
  - Rojo puro       = &H000000FF   (NO &H00FF0000, eso es azul)
  - Blanco          = &H00FFFFFF
  - Negro opaco     = &H00000000
  - Amarillo        = &H0000FFFF
  - Verde lima      = &H0000FF00

Con BorderStyle=3 el color de la CAJA sale de OutlineColour, no de BackColour.
El valor de Outline es el padding de esa caja.
"""
from __future__ import annotations

import re
from pathlib import Path

from .util import ass_time, normalize


def _censor_set(cfg) -> set[str]:
    if not cfg.get_path("censura.enabled", True):
        return set()
    return {normalize(w).strip() for w in (cfg.get_path("censura.palabras") or [])
            if str(w).strip()}


def _censor_word(word: str, banned: set[str]) -> str:
    """'marica' -> 'm*****' (respeta signos: '¡marica!' -> '¡m*****!')."""
    if not banned:
        return word
    core = re.sub(r"^[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+|[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+$", "", word)
    if not core or normalize(core).strip() not in banned:
        return word
    masked = core[0] + "*" * max(1, len(core) - 1)
    return word.replace(core, masked, 1)

# ------------------------------------------------------------- presets
PRESETS = {
    # Estándar de gaming: blanco grueso con contorno negro,
    # palabra activa en amarillo con escala.
    "gamer": {
        "primary": "&H00FFFFFF",
        "outline_col": "&H00000000",
        "border_style": 1,
        "outline": 7,
        "shadow": 3,
        "active_color": "&H0000E5FF",   # amarillo-naranja
        "active_scale": 112,
    },
    # El que pediste para el otro canal: rojo sobre caja negra.
    "rojo_negro": {
        "primary": "&H000000FF",
        "outline_col": "&H00000000",
        "border_style": 3,
        "outline": 14,
        "shadow": 0,
        "active_color": "&H00FFFFFF",   # palabra activa en blanco
        "active_scale": 108,
    },
    # Sobrio, sin resaltado de palabra.
    "limpio": {
        "primary": "&H00FFFFFF",
        "outline_col": "&H00000000",
        "border_style": 1,
        "outline": 5,
        "shadow": 2,
        "active_color": None,
        "active_scale": 100,
    },
}

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{size},{primary},{primary},{outline_col},&H00000000,-1,0,0,0,100,100,1,0,{border_style},{outline},{shadow},2,{pad},{pad},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def _rebased(words: list[dict], seg_start: float, seg_end: float,
             offset: float) -> list[dict]:
    """Palabras dentro de [seg_start, seg_end) con el tiempo movido a `offset`."""
    span = seg_end - seg_start
    out = []
    for w in words:
        if w["end"] <= seg_start or w["start"] >= seg_end:
            continue
        out.append({
            "word": w["word"],
            "start": offset + max(0.0, w["start"] - seg_start),
            "end": offset + min(span, w["end"] - seg_start),
        })
    return out


# ─────────────────────────────── ancho REAL de una linea de subtitulo
# Con WrapStyle 2 las lineas no se parten, asi que si 3 palabras en mayusculas
# no caben en 1080 px, el margen lateral no sirve de nada: la linea se sale por
# los dos lados y se corta. Medido: "DESPROPORCIONADAMENTE" a 92 pt ocupa 1208 px.
#
# Para saber cuanto ocupa cada linea se calibra UNA vez el ancho de cada letra
# con el MISMO motor que dibuja los subtitulos (libass dentro de ffmpeg), asi
# la medida coincide con lo que se ve. Se guarda en work/_anchos_subs.json.
_LETRAS = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ" "ÑÁÉÍÓÚÜ"
           "abcdefghijklmnopqrstuvwxyz" "ñáéíóúü"
           "0123456789" "!?.,:;'\"-()*#%&@¡¿")
_ANCHOS_MEM: dict[str, dict] = {}


def _anchos_letras(font: str, size: int) -> dict:
    """{letra: avance en px} para esa fuente y tamano. Se calibra una vez."""
    import json
    import subprocess
    import tempfile

    import numpy as np

    from .config import ROOT

    clave = f"{font}|{size}"
    if clave in _ANCHOS_MEM:
        return _ANCHOS_MEM[clave]
    cache = ROOT / "work" / "_anchos_subs.json"
    try:
        todo = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        todo = {}
    if clave in todo:
        _ANCHOS_MEM[clave] = todo[clave]
        return todo[clave]

    fila = int(size * 1.6)
    items = list(_LETRAS) + ["__ESPACIO__"]
    W, H = 3000, fila * (len(items) + 1)
    rep = 10
    lineas = []
    for i, c in enumerate(items):
        txt = ("H" * 5 + " " + "H" * 5) if c == "__ESPACIO__" else c * rep
        txt = txt.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        lineas.append(f"Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,"
                      f"{{\\pos(20,{i * fila + 10})}}{txt}")
    ass = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n\n"
           "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
           "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
           "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
           "MarginV, Encoding\n"
           f"Style: Main,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
           "-1,0,0,0,100,100,1,0,1,0,0,7,0,0,0,1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
           "Effect, Text\n" + "\n".join(lineas) + "\n")
    res: dict = {}
    try:
        with tempfile.TemporaryDirectory(prefix="subw_") as td:
            a = Path(td) / "cal.ass"
            a.write_text(ass, encoding="utf-8")
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                 f"color=c=black:s={W}x{H}:d=0.2", "-vf", "subtitles=cal.ass",
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                cwd=td, capture_output=True, timeout=120)
        img = np.frombuffer(r.stdout, dtype=np.uint8)
        if img.size != W * H:
            raise RuntimeError("calibracion sin imagen")
        img = img.reshape(H, W)
        medidas = {}
        for i, c in enumerate(items):
            banda = img[i * fila:(i + 1) * fila]
            cols = np.where(banda.max(axis=0) > 40)[0]
            medidas[c] = float(cols[-1] - cols[0] + 1) if len(cols) else 0.0
        h10 = medidas.get("H", 0.0)
        for c in _LETRAS:
            if medidas.get(c):
                res[c] = round(medidas[c] / rep, 2)
        if h10 and medidas.get("__ESPACIO__"):
            res[" "] = round(max(0.0, medidas["__ESPACIO__"] - h10), 2)
    except Exception as exc:                                # noqa: BLE001
        from .util import log
        log(f"subtitulos: no se pudo calibrar el ancho de letra ({exc}); "
            f"uso una estimacion.")
        res = {}
    if not res:                                   # estimacion prudente
        res = {c: size * 0.74 for c in _LETRAS}
        res[" "] = size * 0.28
    else:
        todo[clave] = res
        try:
            cache.write_text(json.dumps(todo, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    _ANCHOS_MEM[clave] = res
    return res


def _ancho(texto: str, anchos: dict, size: int) -> float:
    prom = anchos.get("M") or size * 0.74
    return sum(anchos.get(c, prom) for c in texto)


def build_ass(words: list[dict], clip_start: float, clip_end: float,
              cfg, out_path: Path) -> Path | None:
    """Genera el .ass para un clip. Los tiempos se rebasan a 0 = inicio del clip."""
    return build_ass_segments(words, [(clip_start, clip_end)], cfg, out_path)


def build_ass_segments(words: list[dict], segments: list[tuple[float, float]],
                       cfg, out_path: Path) -> Path | None:
    """.ass para un clip normal (1 segmento) o combinado (varios trozos del
    directo montados seguidos). Las palabras de cada trozo se recolocan en la
    línea de tiempo continua del clip final."""
    if not cfg.get_path("subtitles.enabled", True):
        return None

    preset_name = cfg.get_path("subtitles.preset", "gamer")
    p = PRESETS.get(preset_name, PRESETS["gamer"])

    per_line = int(cfg.get_path("subtitles.words_per_line", 3))
    upper = bool(cfg.get_path("subtitles.uppercase", True))

    inside: list[dict] = []
    offset = 0.0
    for k_seg, (seg_start, seg_end) in enumerate(segments):
        for w in _rebased(words, seg_start, seg_end, offset):
            w["seg"] = k_seg
            inside.append(w)
        offset += seg_end - seg_start
    span = offset
    if not inside:
        return None

    banned = _censor_set(cfg)          # "marica" -> "m*****" en el subtítulo
    if banned:
        for w in inside:
            w["word"] = _censor_word(w["word"], banned)

    # ANCHO: cada linea se mide y, si no cabe entre los margenes laterales, se
    # encoge SOLO esa linea (las demas se quedan a su tamano). La posicion no
    # cambia. Se cuenta el borde y el "pop" de la palabra activa, que la hace
    # mas ancha justo cuando le toca.
    W_px = int(cfg.get_path("output.width", 1080))
    pad = int(cfg.get_path("subtitles.padding_lateral", 70))
    esc_min = float(cfg.get_path("subtitles.escala_minima", 0.5))
    fsize = int(cfg.get_path("subtitles.font_size", 92))
    anchos = _anchos_letras(cfg.get_path("subtitles.font", "Montserrat ExtraBold"), fsize)
    util = max(200, W_px - 2 * pad)

    def _escala(tokens: list[str]) -> int:
        if not tokens:
            return 100
        esp = anchos.get(" ", fsize * 0.28)
        base = sum(_ancho(t, anchos, fsize) for t in tokens) + esp * (len(tokens) - 1)
        pop = max(_ancho(t, anchos, fsize) for t in tokens) * max(0.0, p["active_scale"] / 100 - 1)
        necesita = base + pop + 2 * float(p.get("outline", 0))
        if necesita <= util:
            return 100
        return int(max(esc_min, util / necesita) * 100)

    lines: list[str] = []
    n_encogidas = 0
    # GRUPOS DE PALABRAS. Antes se cortaban de 3 en 3 a ciegas, y en los
    # compilados un grupo podia empezar al final de un trozo y terminar varios
    # trozos despues: la primera palabra se quedaba en pantalla todo ese rato
    # (medido: "YA A COGER" 32 s encima de tres trozos distintos). Ahora un
    # grupo se cierra al llenarse, al cambiar de trozo o tras un silencio.
    silencio = float(cfg.get_path("subtitles.corte_silencio_seg", 1.0))
    hold = float(cfg.get_path("subtitles.max_espera_seg", 0.5))
    grupos: list[list[dict]] = []
    for w in inside:
        g = grupos[-1] if grupos else None
        if (g is None or len(g) >= per_line or w["seg"] != g[-1]["seg"]
                or w["start"] - g[-1]["end"] > silencio):
            grupos.append([w])
        else:
            g.append(w)

    for group in grupos:
        if not group:
            continue

        tokens = [w["word"].upper() if upper else w["word"] for w in group]
        F = _escala(tokens)
        if F < 100:
            n_encogidas += 1
        pre = (r"{\fscx" + str(F) + r"\fscy" + str(F) + "}") if F < 100 else ""

        if p["active_color"] is None:
            text = " ".join(_escape(t) for t in tokens)
            lines.append(
                f"Dialogue: 0,{ass_time(group[0]['start'])},"
                f"{ass_time(group[-1]['end'])},Main,,0,0,0,,{pre}{text}"
            )
            continue

        # una línea de diálogo por palabra activa
        for j, active in enumerate(group):
            parts = []
            for k, w in enumerate(group):
                token = _escape(w["word"].upper() if upper else w["word"])
                if k == j:
                    act = int(round(p["active_scale"] * F / 100))
                    parts.append(
                        r"{\c" + p["active_color"] + r"\fscx"
                        + str(act) + r"\fscy"
                        + str(act) + "}" + token
                        + r"{\c" + p["primary"] + r"\fscx" + str(F)
                        + r"\fscy" + str(F) + "}"
                    )
                else:
                    parts.append(token)

            start = active["start"]
            # una palabra no se queda iluminada durante un silencio
            end = (min(group[j + 1]["start"], active["end"] + hold)
                   if j + 1 < len(group) else active["end"])
            if end <= start:
                end = start + 0.12

            lines.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
                f"Main,,0,0,0,,{pre}{' '.join(parts)}"
            )

    margin_v = int(cfg.get_path("subtitles.margin_v", 360))
    layout = cfg.get_path("reframe.layout")
    if layout == "facecam_top":
        # este layout reserva una banda abajo para los subtítulos
        margin_v = int(cfg.get_path("subtitles.margin_v_facecam_top", 120))
    elif layout == "libre":
        # el editor por-clip fija la Y (centro de los subtítulos) en px
        H = int(cfg.get_path("output.height", 1920))
        subs_y = cfg.get_path("reframe.libre.subs.y", H - 120)
        margin_v = max(10, min(H - 60, H - int(subs_y)))

    header = HEADER.format(
        w=int(cfg.get_path("output.width", 1080)),
        h=int(cfg.get_path("output.height", 1920)),
        font=cfg.get_path("subtitles.font", "Montserrat ExtraBold"),
        size=int(cfg.get_path("subtitles.font_size", 92)),
        primary=p["primary"],
        outline_col=p["outline_col"],
        border_style=p["border_style"],
        outline=p["outline"],
        shadow=p["shadow"],
        margin_v=margin_v,
        pad=pad,
    )

    if n_encogidas:
        from .util import log
        log(f"  subtitulos: {n_encogidas} linea(s) encogidas para no salirse por "
            f"los lados (margen {pad}px).")
    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path
