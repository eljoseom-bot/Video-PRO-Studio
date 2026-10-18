"""El montaje VERTICAL (1080×1920) de un Short de Mi canal (`clipper/shorts.py` elige qué trozo): sale de la grabación
ORIGINAL con los mismos cortes del vídeo largo (sin muletillas, sin pausas, arrancando en la toma buena), en un pase de
ffmpeg:
- ARRIBA (1080×1080), la pantalla: un encuadre cuadrado FIJO que contiene todo lo que hace el ratón en ese Short, sin tu
  cámara (`pipeline.encuadre_zoom` con forma cuadrada: la misma regla del 100 % que el zoom del vídeo largo); si no cabe o
  no se sabe dónde está el ratón, la pantalla entera encajada en el cuadrado.
- ABAJO (1080×840), tu cara (el recuadro de la cámara de `videos.canales.<canal>.camara`).
- SUBTÍTULOS grandes palabra a palabra (en Shorts casi todo el mundo los ve sin sonido) con las letras y colores de la
  marca, sobre la unión de las dos partes; y el GANCHO escrito arriba los primeros 3 s.
- Audio con micro-fundidos en cada empalme (sin clics, como el vídeo largo) y normalizado a -14 LUFS.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .util import log, run

W, H = 1080, 1920
ALTO_PANTALLA, ALTO_CARA = 1080, 840
GANCHO_S = 3.0


def _ass_color(hexa: str, alfa: str = "00") -> str:
    """#RRGGBB -> &HAABBGGRR (ASS guarda los colores al revés)."""
    h = (hexa or "#FFFFFF").lstrip("#")
    return f"&H{alfa}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def encuadre_pantalla(cursor: list[tuple[float, float, float]], piezas: list[tuple[float, float]],
                      camara: dict | None, src_w: int, src_h: int,
                      actividad: list[dict] | None = None) -> tuple[float, float, float, float] | None:
    """El cuadrado de la pantalla (x, y del centro, w, h en fracción del lienzo) que contiene TODO lo que hace el ratón en
    las `piezas` del Short (más la última posición de antes), sin la cámara. Si en ese rato no se mueve el ratón, el
    cuadrado más grande sin la cámara centrado donde hubo actividad en pantalla (o, sin ella, en la parte central
    izquierda: la pantalla entera encajada salía pequeña, con franjas negras y tu cámara repetida — visto con su vídeo
    real). None = no cabe ninguno: la pantalla entera."""
    from .pipeline import _raton_en, encuadre_zoom
    r = src_w / src_h                                        # alto/ancho de un CUADRADO en fracciones del lienzo
    w_max = min(1.0, src_h / src_w)                          # el cuadrado más grande: todo el alto
    puntos = []
    for a, b in piezas:
        puntos += [(x, y) for _t, x, y in _raton_en(cursor, a, b)]
    if puntos:
        return encuadre_zoom(puntos, min(0.35, w_max), r, camara, w_max=w_max)
    centros = [(float(v["x"]), float(v["y"])) for v in actividad or [] if "x" in v
               and any(float(v["inicio"]) < b and float(v["fin"]) > a for a, b in piezas)]
    x0 = min(float(camara["x"]) / 2, 0.5) if camara else 0.5
    cx = sum(x for x, _y in centros) / len(centros) if centros else x0
    return encuadre_zoom([(cx, 0.5)], w_max, r, camara, margen=0.0, w_max=w_max)


def _ass_short(words: list[dict], piezas: list[tuple[float, float]], gancho: str, marca: dict, cfg,
               destino: Path) -> Path | None:
    """Los subtítulos (palabra a palabra, la que suena resaltada) y el gancho, con la marca: `subtitles.build_ass_segments`
    con un estilo propio y medidas de 1080×1920."""
    from . import subtitles
    subtitles.PRESETS["short_marca"] = {
        "primary": _ass_color(marca.get("crema", "#FFFBF2")), "outline_col": _ass_color(marca.get("tinta", "#14100F")),
        "border_style": 1, "outline": 8, "shadow": 0, "active_color": _ass_color(marca.get("naranja", "#FE330A")),
        "active_scale": 110}
    fuente = "Rubik ExtraBold"
    tam = 82

    class _C:
        def get_path(self, k, d=None):
            propios = {"subtitles.enabled": True, "subtitles.preset": "short_marca", "subtitles.words_per_line": 3,
                       "subtitles.uppercase": True, "subtitles.padding_lateral": 60, "subtitles.escala_minima": 0.5,
                       "subtitles.font_size": tam, "subtitles.font": fuente, "subtitles.corte_silencio_seg": 1.0,
                       "subtitles.max_espera_seg": 0.5, "output.width": W, "output.height": H,
                       "subtitles.margin_v": H - ALTO_PANTALLA - tam // 2, "reframe.layout": None}
            return propios[k] if k in propios else cfg.get_path(k, d)

    ass = subtitles.build_ass_segments(words, piezas, _C(), destino)
    if ass is None:
        return None
    texto = ass.read_text(encoding="utf-8")
    if gancho:
        estilo = (f"Style: Gancho,{fuente},64,{_ass_color(marca.get('crema', '#FFFBF2'))},"
                  f"{_ass_color(marca.get('crema', '#FFFBF2'))},{_ass_color(marca.get('naranja', '#FE330A'))},"
                  f"&H00000000,-1,0,0,0,100,100,1,0,3,18,0,8,60,60,70,1")
        texto = texto.replace("\n\n[Events]", "\n" + estilo + "\n\n[Events]", 1)
        g = gancho.replace("{", "(").replace("}", ")").replace("\\", "").upper()
        texto = texto.rstrip("\n") + f"\nDialogue: 1,0:00:00.00,0:00:{GANCHO_S:05.2f},Gancho,,0,0,0,,{g}\n"
    ass.write_text(texto, encoding="utf-8")
    return ass


def render_short(video: Path, piezas: list[tuple[float, float]], out: Path, cfg, meta: dict, camara: dict | None,
                 cursor: list[tuple[float, float, float]], words: list[dict], gancho: str, marca: dict,
                 fps: int = 30, actividad: list[dict] | None = None) -> Path:
    """Renderiza el Short: `piezas` = los tramos de la grabación ORIGINAL que lo forman (ya sin lo recortado). Lanza si
    ffmpeg falla (quien llama decide: un Short fallido nunca tumba el vídeo largo)."""
    from . import config as _config
    from .render import _codec_args, _escape_filter_path, _even
    src_w, src_h = int(meta["width"]), int(meta["height"])
    e = encuadre_pantalla(cursor, piezas, camara, src_w, src_h, actividad)
    if e:
        cx, cy, w, h = e
        pw, ph = _even(w * src_w), _even(h * src_h)
        px = _even(min(max(0.0, cx * src_w - pw / 2), src_w - pw))
        py = _even(min(max(0.0, cy * src_h - ph / 2), src_h - ph))
        pantalla = f"crop={pw}:{ph}:{px}:{py},scale={W}:{ALTO_PANTALLA}"
    else:                                                    # la pantalla entera, encajada en el cuadrado
        pantalla = (f"scale={W}:{ALTO_PANTALLA}:force_original_aspect_ratio=decrease,"
                    f"pad={W}:{ALTO_PANTALLA}:(ow-iw)/2:(oh-ih)/2:color=0x14100F")
    if camara:
        cw, ch = float(camara["w"]) * src_w, float(camara["h"]) * src_h
        asp = W / ALTO_CARA
        if cw / ch > asp:
            cw = ch * asp
        else:
            ch = cw / asp
        ccx = float(camara["x"]) * src_w + float(camara["w"]) * src_w / 2
        ccy = float(camara["y"]) * src_h + float(camara["h"]) * src_h / 2
        cara = (f"crop={_even(cw)}:{_even(ch)}:{_even(max(0.0, ccx - cw / 2))}:{_even(max(0.0, ccy - ch / 2))},"
                f"scale={W}:{ALTO_CARA}")
    else:
        cara = None
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = _ass_short(words, piezas, gancho, marca, cfg, out.with_suffix(".ass"))
    micro = 0.01
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    filtro = ""
    for i, (a, b) in enumerate(piezas):
        d = max(0.1, b - a)
        cmd += ["-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", str(video)]
        if cara:
            filtro += (f"[{i}:v]split=2[s{i}][f{i}];[s{i}]{pantalla}[sp{i}];[f{i}]{cara}[fp{i}];"
                       f"[sp{i}][fp{i}]vstack=inputs=2,fps={fps},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[v{i}];")
        else:
            filtro += (f"[{i}:v]{pantalla},pad={W}:{H}:0:(oh-ih)/2:color=0x14100F,fps={fps},format=yuv420p,setsar=1,"
                       f"setpts=PTS-STARTPTS[v{i}];")
        fx = ""
        if i > 0:
            fx += f",afade=t=in:st=0:d={micro}"
        if i < len(piezas) - 1:
            fx += f",afade=t=out:st={max(0.0, d - micro):.3f}:d={micro}"
        filtro += f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS{fx}[a{i}];"
    filtro += "".join(f"[v{i}][a{i}]" for i in range(len(piezas))) + f"concat=n={len(piezas)}:v=1:a=1[cv][ca];"
    if ass:
        fuentes = _config.ROOT / "fonts"
        filtro += f"[cv]ass='{_escape_filter_path(ass)}':fontsdir='{_escape_filter_path(fuentes)}'[vo];"
    else:
        filtro += "[cv]null[vo];"
    filtro += "[ca]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]"
    cmd += ["-filter_complex", filtro, "-map", "[vo]", "-map", "[ao]", *_codec_args(cfg),
            "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    run(cmd)
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(GANCHO_S + 0.5, 1.5):.2f}", "-i", str(out),
                        "-frames:v", "1", "-q:v", "3", str(out.with_suffix(".jpg"))], capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001 — sin miniatura, el Short sigue
        pass
    return out


def render_crudo(video: Path, piezas: list[tuple[float, float]], out: Path, cfg, ancho_max: int = 1280) -> Path:
    """El Short «CRUDO» (el autor, 17-oct: «el short simplemente ahí, cero editado; después lo paso a la tool de Shorts para que lo edite»): solo el CORTE de la grabación original (pantalla + cámara tal cual,
    sin subtítulos, gancho ni recortes de estilo), con los cortes del montaje, a `ancho_max` px. Es lo que después se pasa a «Shorts · TikTok». Lanza si ffmpeg falla."""
    from .render import _codec_args, _even
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    filtro = ""
    micro = 0.01
    for i, (a, b) in enumerate(piezas):
        d = max(0.1, b - a)
        cmd += ["-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", str(video)]
        filtro += f"[{i}:v]scale='min({ancho_max},iw)':-2,fps=30,format=yuv420p,setsar=1,setpts=PTS-STARTPTS[v{i}];"
        fx = (f",afade=t=in:st=0:d={micro}" if i > 0 else "") + (f",afade=t=out:st={max(0.0, d - micro):.3f}:d={micro}" if i < len(piezas) - 1 else "")
        filtro += f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS{fx}[a{i}];"
    filtro += "".join(f"[v{i}][a{i}]" for i in range(len(piezas))) + f"concat=n={len(piezas)}:v=1:a=1[vo][ao]"
    cmd += ["-filter_complex", filtro, "-map", "[vo]", "-map", "[ao]", *_codec_args(cfg), "-r", "30", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    run(cmd)
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(out.with_suffix(".jpg"))], capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001
        pass
    _ = _even
    return out


def prueba() -> list[dict]:
    """Un render REAL (ffmpeg) con un clip sintético 2560×1440 como los del autor: sale vertical, con la duración de las
    piezas, subtítulos y gancho, la cara abajo y el ratón dentro del encuadre de arriba."""
    import json
    import shutil
    import tempfile

    import numpy as np

    from . import config
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)[:500]})

    cfg = config.load()
    cam = cfg.get_path("videos.canales.ejemplo.camara") or {"x": 0.7672, "y": 0.6583, "w": 0.2172, "h": 0.2847}
    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-short-"))
    try:
        clip = tmp / "clip.mkv"
        cx, cy, cw, ch = (int(cam["x"] * 2560), int(cam["y"] * 1440), int(cam["w"] * 2560), int(cam["h"] * 1440))
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=0x223344:s=2560x1440:r=30:d=12",
                        "-f", "lavfi", "-i", "color=c=0xffffff:s=24x24:r=30:d=12",
                        "-f", "lavfi", "-i", "sine=f=220:sample_rate=48000:d=12",
                        "-filter_complex", f"[0:v]drawbox=x={cx}:y={cy}:w={cw}:h={ch}:c=red:t=fill[f];"
                                           f"[f][1:v]overlay=x='600+t*40':y=500[v]",
                        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(clip)], check=True)
        cursor = [(t / 2, (600 + t / 2 * 40 + 12) / 2560, 512 / 1440) for t in range(0, 24)]
        words = [{"word": f"palabra{i}", "start": 0.3 + i * 0.5, "end": 0.7 + i * 0.5} for i in range(22)]
        marca = cfg.get_path("videos.marca") or {}
        corto = render_short(clip, [(0.0, 5.0), (6.0, 11.0)], tmp / "short.mp4", cfg,
                             {"width": 2560, "height": 1440}, cam, cursor, words, "El truco del ratón", marca)
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:"
                                          "format=duration", "-of", "json", str(corto)],
                                         capture_output=True, text=True).stdout)
        vid = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        dur = float(info["format"]["duration"])
        r("sale vertical 1080×1920 con audio y dura lo que las piezas (10 s)",
          (vid["width"], vid["height"]) == (1080, 1920) and any(s["codec_type"] == "audio" for s in info["streams"])
          and abs(dur - 10.0) < 0.3, (vid, dur))
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "4.0", "-i", str(corto), "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        f = np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)
        arriba, abajo = f[:1080], f[1080:]
        blanco = (arriba[..., 0] > 200) & (arriba[..., 1] > 200) & (arriba[..., 2] > 200)
        rojo_abajo = ((abajo[..., 0] > 170) & (abajo[..., 1] < 80) & (abajo[..., 2] < 80)).mean()
        r("arriba se ve el «ratón» (dentro del encuadre de la pantalla) y abajo la cámara (roja en el clip de prueba)",
          blanco.sum() > 50 and rojo_abajo > 0.5, (int(blanco.sum()), round(float(rojo_abajo), 2)))
        r("el .ass lleva los subtítulos y el gancho con las letras de la marca",
          "Rubik ExtraBold" in (tmp / "short.ass").read_text(encoding="utf-8")
          and "EL TRUCO DEL RATÓN" in (tmp / "short.ass").read_text(encoding="utf-8"))
        r("miniatura del Short", (tmp / "short.jpg").is_file())
        crudo = render_crudo(clip, [(0.0, 5.0), (6.0, 11.0)], tmp / "crudo.mp4", cfg)
        ic = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", str(crudo)], capture_output=True, text=True).stdout)
        vc = [x for x in ic["streams"] if x["codec_type"] == "video"][0]
        r("el Short CRUDO es solo el corte de la grabación: horizontal (1280×720), con audio, dura lo que las piezas y sin vertical ni subtítulos",
          (vc["width"], vc["height"]) == (1280, 720) and any(x["codec_type"] == "audio" for x in ic["streams"]) and abs(float(ic["format"]["duration"]) - 10.0) < 0.3 and not (tmp / "crudo.ass").exists(), (vc, ic["format"]["duration"]))
    except Exception as exc:  # noqa: BLE001
        r("render del Short", False, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
