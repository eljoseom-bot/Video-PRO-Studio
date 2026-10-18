#!/usr/bin/env python3
"""Vídeos HORIZONTALES largos por temática, a partir de un directo entero.

    python run_video.py "https://youtube.com/watch?v=..." --contexto "SEO"
    python run_video.py mi_directo.mp4 --analizar     # solo detecta temas
"""
import sys

import argparse

from clipper import config, pipeline
from clipper.util import log

# La salida del panel va a un FICHERO (work/panel.log). En Windows, un stdout
# redirigido usa cp1252, y cualquier emoji revienta con UnicodeEncodeError.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Directo -> vídeos horizontales por temática")
    ap.add_argument("source", help="URL de YouTube/Twitch o ruta a un archivo")
    ap.add_argument("--analizar", action="store_true",
                    help="solo detecta las temáticas, no renderiza")
    ap.add_argument("--contexto", default="",
                    help="de qué va el directo")
    ap.add_argument("--max", type=int, help="nº máximo de vídeos del directo")
    ap.add_argument("--min-min", type=float, dest="min_min",
                    help="duración mínima de un vídeo, en MINUTOS")
    ap.add_argument("--max-min", type=float, dest="max_min",
                    help="duración máxima de un vídeo, en MINUTOS")
    ap.add_argument("--subs", action="store_true",
                    help="quemar subtítulos (por defecto NO en horizontal)")
    ap.add_argument("--sin-ia", action="store_true",
                    help="sin Gemini no hay temáticas: solo para depurar")
    args = ap.parse_args()

    cfg = config.load()
    cfg.setdefault("videos", {})
    if args.max:
        cfg["videos"]["max_por_directo"] = args.max
    if args.min_min:
        cfg["videos"]["duracion_min_seg"] = args.min_min * 60
    if args.max_min:
        cfg["videos"]["duracion_max_seg"] = args.max_min * 60
    if args.subs:
        cfg["videos"]["subtitulos"] = True
    if args.sin_ia:
        cfg.setdefault("ai", {})["enabled"] = False

    try:
        result = pipeline.process_video(
            args.source, cfg, contexto=args.contexto, only_analyze=args.analizar)
    except KeyboardInterrupt:
        log("Cancelado por el usuario.")
        return 130
    except Exception as exc:                              # noqa: BLE001
        log(f"ERROR: {exc}")
        return 1

    log(f"{result['videos']} vídeo(s) -> {result['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
