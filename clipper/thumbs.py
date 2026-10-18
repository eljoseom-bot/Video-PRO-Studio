"""Miniatura por clip: frame del Short + título en grande.
Vertical 1080x1920 (dimensiones de Short, no de vídeo horizontal).
100% ffmpeg, $0.
"""
from __future__ import annotations

import subprocess
import tempfile
import textwrap
from pathlib import Path

from .util import log, run

_WIN_FONTS = Path("C:/Windows/Fonts")


def _font(cfg) -> str:
    want = str(cfg.get_path("thumbnail.font", "impact.ttf"))
    for p in (Path(want), _WIN_FONTS / want):
        if p.is_file():
            return str(p)
    for fb in ("impact.ttf", "seguibl.ttf", "arialbd.ttf"):
        if (_WIN_FONTS / fb).is_file():
            return str(_WIN_FONTS / fb)
    return "sans"


def _esc_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", r"\:")


def _esc_txt(s: str) -> str:
    return (s.replace("\\", r"\\").replace("'", "’")
            .replace(":", r"\:").replace("%", r"\%"))


def _strip_emoji(s: str) -> str:
    """Impact / Segoe Black / Arial Bold NO tienen glifos de emoji: saldría un
    cuadrito. En la miniatura (y en la portada quemada) el título va sin emoji."""
    out = []
    for ch in (s or ""):
        o = ord(ch)
        if (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF
                or 0x2190 <= o <= 0x21FF or 0x1F1E6 <= o <= 0x1F1FF
                or 0x2300 <= o <= 0x23FF or o in (0x2B50, 0x2B55, 0xFE0F,
                0x200D, 0x203C, 0x2049, 0x2122, 0x2139, 0x24C2, 0x3030, 0x303D)):
            continue
        out.append(ch)
    return "".join(out).strip()


def make_thumbnail(clip_mp4: Path, at_seconds: float, title: str,
                   out_jpg: Path, cfg, tag: str = "", layout: str = "") -> Path | None:
    # El lienzo sale de la CONFIG, no clavado: los Shorts son 1080x1920 y los
    # videos horizontales 1920x1080. Antes estaba fijo a vertical, asi que un
    # video horizontal se llevaba una miniatura vertical (mal).
    W = int(cfg.get_path("output.width", 1080))
    H = int(cfg.get_path("output.height", 1920))

    # RED DE SEGURIDAD: la miniatura tiene que tener la MISMA orientación que
    # el vídeo, pase lo que pase. Si a alguien se le olvida pasar la config
    # correcta, aquí se corrige mirando el propio .mp4 en vez de sacar una
    # miniatura vertical para un vídeo horizontal (ya pasó, y se subió así).
    try:
        _p = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0:s=x", str(clip_mp4)],
            capture_output=True, text=True).stdout.strip()
        _vw, _vh = (int(x) for x in _p.split("x")[:2])
        if _vw > 0 and _vh > 0 and (_vw > _vh) != (W > H):
            log(f"miniatura: el vídeo es {_vw}x{_vh} pero el lienzo era {W}x{H}"
                f" -> lo giro a {H}x{W}.")
            W, H = H, W
    except Exception:  # noqa: BLE001
        pass
    font = _esc_path(_font(cfg))
    accent = str(cfg.get_path("thumbnail.accent", "#ff2d2d"))
    up = bool(cfg.get_path("thumbnail.uppercase", True))

    txt = _strip_emoji((title or "").strip())
    if up:
        txt = txt.upper()
    lines = textwrap.wrap(txt, width=16)[:4] or [""]

    # Todo escala con el ANCHO del lienzo: 1.0 en Shorts (1080) y 1.78 en los
    # vídeos horizontales (1920), así el texto pesa lo mismo en la imagen.
    esc = W / 1080.0
    fs = int((108 if len(lines) <= 2 else (92 if len(lines) == 3 else 78)) * esc)
    line_h = int(fs * 1.14)
    block_h = line_h * len(lines)
    # Vertical: el título va por ENCIMA de la banda de subtítulos quemados del
    # clip (empieza ~y=1610), de ahí el 0.8125 (= 1560 sobre 1920: idéntico a
    # antes). Horizontal: no hay banda de subtítulos, así que baja más.
    # OJO: esto estaba clavado a 1560 y en un lienzo de 1080 de alto el título
    # se dibujaba FUERA de la imagen.
    y_bottom = int(cfg.get_path("thumbnail.titulo_y_bottom", 0)
                   or (H * 0.8125 if H >= W else H * 0.90))
    y0 = max(int(20 * esc), y_bottom - block_h)

    sat = float(cfg.get_path("thumbnail.saturacion", 1.45))
    border = int(cfg.get_path("thumbnail.borde_px", 8))
    chain = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1[bg];"
        # scrim oscuro detrás del texto
        f"[bg]drawbox=x=0:y={y0 - 40}:w={W}:h={block_h + 70}:"
        f"color=black@0.5:t=fill,"
        f"drawbox=x=0:y={H - int(12 * esc)}:w={W}:h={int(12 * esc)}:color={accent}:t=fill[base]"
    )
    # drawtext con textfile= (evita TODO el infierno de escapado del filtergraph:
    # comas, corchetes, comillas, ':' en el título ya no rompen nada).
    tmpdir = Path(tempfile.mkdtemp(prefix="thumbtxt_"))
    txt_files: list[Path] = []

    def _txtfile(s: str) -> str:
        f = tmpdir / f"t{len(txt_files)}.txt"
        f.write_text(s, encoding="utf-8")
        txt_files.append(f)
        return _esc_path(str(f))

    cur = "[base]"
    for i, ln in enumerate(lines):
        y = y0 + i * line_h
        nxt = f"[t{i}]"
        chain += (
            f";{cur}drawtext=fontfile='{font}':textfile='{_txtfile(ln)}':expansion=none:"
            f"fontcolor=white:fontsize={fs}:borderw={max(4, int(8 * esc))}:bordercolor=black:"
            f"x=(w-text_w)/2:y={y}{nxt}"
        )
        cur = nxt
    if tag:
        chain += (
            f";{cur}drawtext=fontfile='{font}':textfile='{_txtfile(tag.upper())}':expansion=none:"
            f"fontcolor=white:fontsize={int(46 * esc)}:borderw={max(3, int(5 * esc))}:bordercolor=black:"
            f"box=1:boxcolor={accent}@0.92:boxborderw={int(18 * esc)}:x={int(44 * esc)}:y={int(54 * esc)}[final]"
        )
        cur = "[final]"

    # filtro final: subida de saturación + marco rojo de marca (recordación)
    chain += (
        f";{cur}eq=saturation={sat}:contrast=1.06:brightness=0.02,vibrance=0.3,"
        f"drawbox=x=0:y=0:w={W}:h={H}:color={accent}:t={max(0, int(border * esc))}[out]"
    )
    fc = chain
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    try:
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(clip_mp4),
            "-frames:v", "1", "-filter_complex", fc, "-map", "[out]",
            "-q:v", "3", str(out_jpg),
        ])
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo generar la miniatura de {clip_mp4.name} ({exc}).")
        return None
    finally:
        for f in txt_files:
            f.unlink(missing_ok=True)
        try:
            tmpdir.rmdir()
        except OSError:
            pass
    return out_jpg if out_jpg.is_file() else None
