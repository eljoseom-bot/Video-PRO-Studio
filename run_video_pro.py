#!/usr/bin/env python3
"""VÍDEO PRO: un archivo ya grabado (subido, no un directo) -> un vídeo
horizontal editado de punta a punta por el director (recortes, cámara,
tarjetas, imágenes, título, miniatura).

    python run_video_pro.py "D:\\tutoriales\\seo1.mp4" --canal ejemplo
    python run_video_pro.py "D:\\tutoriales\\seo1.mp4" --canal ejemplo --analizar
"""
import sys
from pathlib import Path

import argparse

from clipper import config, pipeline
from clipper.util import log

# El panel redirige esta salida a un fichero, y en Windows eso usa cp1252 por
# defecto — un emoji cualquiera (los títulos sugeridos pueden llevarlo)
# revienta con UnicodeEncodeError.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Vídeo subido -> vídeo PRO editado")
    ap.add_argument("source", help="ruta al archivo de vídeo (mp4/mov/mkv/webm)")
    ap.add_argument("--canal", default="", help="para qué canal es "
                    "(elige el tono/contexto del director; ver videos.canales "
                    "en config.yaml)")
    ap.add_argument("--analizar", action="store_true",
                    help="solo corre el director y guarda el plan, no renderiza")
    ap.add_argument("--version", type=int, default=1, help="botón 'reshuffle' del "
                    "panel: >1 fuerza un análisis nuevo del director y un vídeo "
                    "aparte (v2, v3...) que no pisa al anterior, para comparar")
    ap.add_argument("--plan-de", default="", help="EDITOR DE CORTES del panel: en vez de "
                    "llamar a Gemini, parte del plan ya analizado de otra versión "
                    "('pro' o 'pro-vN') — junto con --cortes")
    ap.add_argument("--cortes", default="", help="cortes manuales en segundos del vídeo "
                    "ORIGINAL, 'ini-fin,ini-fin' (ej. 12.5-20.0,100-110.25)")
    ap.add_argument("--camara-aparte", action="store_true", help="los tramos «cara» y «explicativa» con la cámara "
                    "que graba OBS aparte (clipper/camara_aparte.py), aunque esté apagada en config.yaml")
    ap.add_argument("--camara-cara", default="", choices=["", "pantalla", "marco"],
                    help="con la cámara aparte, cómo sale el modo «cara»: «pantalla» (a pantalla completa) o «marco» "
                    "(en un recuadro sobre el naranja de la marca); por defecto, videos.camara_aparte.cara")
    ap.add_argument("--mejora-archivo", default="", help="MEJORAR con IA: archivo de texto con lo que el autor "
                    "comenta sobre la versión --plan-de (qué no le cuadra, qué quiere quitar…)")
    args = ap.parse_args()

    cfg = config.load()
    canales = (cfg.get("videos") or {}).get("canales") or {}
    if args.canal and args.canal not in canales:
        log(f"AVISO: '{args.canal}' no está en videos.canales de config.yaml; "
            f"sigo sin contexto de canal.")

    cortes: list[tuple[float, float]] = []
    try:
        for trozo in [t for t in args.cortes.split(",") if t.strip()]:
            a, b = trozo.split("-", 1)
            cortes.append((float(a), float(b)))
    except ValueError:
        log(f"ERROR: --cortes «{args.cortes}» no tiene el formato 'ini-fin,ini-fin' en segundos.")
        return 2
    mejora = ""
    if args.mejora_archivo:
        try:
            mejora = Path(args.mejora_archivo).read_text(encoding="utf-8").strip()
        except OSError as exc:
            log(f"ERROR: no pude leer el comentario ({exc}).")
            return 2
    if args.plan_de and not cortes and not mejora:
        log("ERROR: --plan-de necesita --cortes o --mejora-archivo (sin uno de los dos no hay nada que hacer).")
        return 2

    try:
        result = pipeline.process_video_pro(
            args.source, cfg, canal=args.canal, only_analyze=args.analizar,
            version=args.version, plan_de=args.plan_de or None, cortes=cortes or None, mejora=mejora or None,
            camara_aparte=True if args.camara_aparte else None, camara_cara=args.camara_cara or None)
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
