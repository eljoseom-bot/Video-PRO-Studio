"""«Probar el sistema»: comprueba lo que hoy puede fallar EN SILENCIO.

Un vídeo malo no avisa: ffmpeg cambia de versión y las tarjetas van a tirones,
una actualización deja la GPU sin usar, un modelo de Gemini deja de existir, el
disco se llena. Esto lo convierte en un semáforo (ok / aviso / error) que se puede
mirar en 1 minuto tras un reinicio o una actualización — ANTES de un vídeo real.

Dos partes:
  · comprobaciones rápidas (librerías, ffmpeg, GPU, disco, config, Gemini, YouTube)
  · render de prueba (~40 s, sin Gemini ni Whisper: cuesta 0): fabrica un clip
    sintético, lo pasa por el MISMO `run_video_pro.py` de los vídeos reales con un
    plan fijo, y mide el resultado (fps, resolución, duración, volumen, clics en los
    cortes, tarjetas, fluidez del fundido de tarjeta, música).

Cada comprobación devuelve {id, nombre, estado, detalle, lineas, seg}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config

ROOT = config.ROOT
OK, AVISO, ERROR = "ok", "aviso", "error"
_PESO = {OK: 0, AVISO: 1, ERROR: 2}
ULTIMO = ROOT / "work" / "selftest_last.json"
STEM_PRUEBA = "zz-selftest"          # el panel oculta los trabajos con este prefijo


def _r(id_: str, nombre: str, estado: str, detalle: str = "", lineas: list[str] | None = None,
       seg: float | None = None) -> dict:
    return {"id": id_, "nombre": nombre, "estado": estado, "detalle": detalle,
            "lineas": lineas or [], "seg": None if seg is None else round(seg, 1)}


def _peor(estados) -> str:
    return max(estados, key=lambda e: _PESO[e], default=OK)


def _run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


# ------------------------------------------------------------- comprobaciones rápidas
def _c_librerias(cfg) -> dict:
    import importlib
    import importlib.metadata as md

    faltan = []
    for mod in ("flask", "PIL", "yaml", "numpy", "faster_whisper", "google.genai"):
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001
            faltan.append(f"{mod} ({type(exc).__name__})")
    if faltan:
        return _r("librerias", "Librerías de Python", ERROR,
                  "No se pueden cargar: " + ", ".join(faltan),
                  ["Reinstala con:  pip install -r requirements.lock.txt"])

    fijadas: dict[str, str] = {}
    try:
        for linea in (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
            if "==" in linea and not linea.lstrip().startswith("#"):
                n, v = linea.split("==", 1)
                fijadas[n.strip()] = v.strip()
    except OSError:
        return _r("librerias", "Librerías de Python", AVISO,
                  "No encuentro requirements.lock.txt (no puedo comparar versiones).")
    distintas = []
    for nombre, v in fijadas.items():
        try:
            inst = md.version(nombre)
        except md.PackageNotFoundError:
            distintas.append(f"{nombre}: falta (probado con {v})")
            continue
        if inst != v:
            distintas.append(f"{nombre}: {inst} (probado con {v})")
    py = f"Python {sys.version.split()[0]}"
    if distintas:
        return _r("librerias", "Librerías de Python", AVISO,
                  f"{len(distintas)} librería(s) con versión distinta a la probada. {py}.",
                  distintas[:8] + (["…"] if len(distintas) > 8 else []) +
                  ["Si el render de prueba sale bien, no pasa nada; si no, vuelve a las "
                   "versiones de requirements.lock.txt."])
    return _r("librerias", "Librerías de Python", OK,
              f"{len(fijadas)} librerías idénticas a las probadas. {py}.")


def _c_ffmpeg(cfg) -> dict:
    try:
        v = _run(["ffmpeg", "-version"]).stdout.splitlines()[0]
        _run(["ffprobe", "-version"])
    except (OSError, IndexError, subprocess.SubprocessError):
        return _r("ffmpeg", "ffmpeg", ERROR, "No encuentro ffmpeg/ffprobe en el PATH.",
                  ["Instálalo con:  winget install Gyan.FFmpeg   y reinicia el panel."])
    codificadores = _run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
    filtros = _run(["ffmpeg", "-hide_banner", "-filters"]).stdout
    faltan = [c for c in ("libx264", "aac") if c not in codificadores]
    necesarios = ("alimiter", "loudnorm", "afade", "fps", "overlay", "concat", "crop", "amix",
                  "adelay", "asplit", "fade", "scale", "pad", "aformat")
    faltan += [f for f in necesarios if not re.search(rf"\b{f}\b", filtros)]
    if faltan:
        return _r("ffmpeg", "ffmpeg", ERROR, "A esta versión le faltan piezas: " + ", ".join(faltan),
                  [v[:110]])
    ver = re.search(r"version (\S+)", v)
    ver = ver.group(1) if ver else "?"
    probada = None
    try:
        m = re.search(r"ffmpeg version (\S+)",
                      (ROOT / "requirements.lock.txt").read_text(encoding="utf-8"))
        probada = m.group(1) if m else None
    except OSError:
        pass
    if probada and probada != ver:
        return _r("ffmpeg", "ffmpeg", AVISO, f"Versión {ver}; la última probada era {probada}.",
                  ["Cambió ffmpeg: el render de prueba de abajo dirá si algo se rompió."])
    return _r("ffmpeg", "ffmpeg", OK, f"Versión {ver}, con todos los filtros que usa el render.")


def _linea_motor_render(cfg) -> str:
    """Una línea para el chequeo de GPU: con qué motor se va a RENDERIZAR (no confundir con la
    GPU de Whisper, arriba: son dos cosas distintas que pueden usar la GPU por separado)."""
    from . import render
    m = render.motor_activo(cfg)
    return ("· Render de vídeo: por GPU (NVENC) — mucho más rápido que por CPU." if m == "gpu"
            else "· Render de vídeo: por CPU (libx264)" + (" — elegido en «Opciones»." if str(cfg.get_path("output.motor", "auto")).lower() == "cpu" else " (no se encontró una GPU NVIDIA que ffmpeg sepa usar)."))


def _c_gpu(cfg) -> dict:
    if cfg.get_path("transcribe.backend", "local") == "groq":
        return _r("gpu", "Transcripción", OK, "Usa Groq (no necesita GPU).", [_linea_motor_render(cfg)])
    try:
        from . import transcribe
        transcribe._register_cuda_dlls()
        hay = transcribe._cuda_available()
    except Exception as exc:  # noqa: BLE001
        return _r("gpu", "GPU para Whisper", AVISO, f"No pude comprobarla ({type(exc).__name__}).", [_linea_motor_render(cfg)])
    nombre = ""
    try:
        nombre = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], 10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    modelo = cfg.get_path("transcribe.model", "large-v3")
    if hay:
        return _r("gpu", "GPU para Whisper", OK, f"{nombre or 'GPU NVIDIA'} lista para «{modelo}».", [_linea_motor_render(cfg)])
    if cfg.get_path("transcribe.device", "auto") == "cuda":
        return _r("gpu", "GPU para Whisper", ERROR, "config.yaml pide GPU y no hay ninguna usable.",
                  ["Revisa el driver de NVIDIA o pon transcribe.device: auto.", _linea_motor_render(cfg)])
    return _r("gpu", "GPU para Whisper", AVISO,
              "No hay GPU usable: Whisper irá por CPU (mucho más lento).",
              ["Tras actualizar Windows o el driver de NVIDIA a veces se pierde: reiníciala o reinstala el driver.", _linea_motor_render(cfg)])


def _c_disco(cfg) -> dict:
    P = config.paths()
    lineas, peor = [], OK
    for nombre, carpeta in (("videos (renders)", P["outbox"]), ("work (cachés)", P["work"]),
                            ("inbox (originales)", P["inbox"])):
        try:
            libre = shutil.disk_usage(carpeta).free / 1e9
        except OSError:
            continue
        est = ERROR if libre < 15 else AVISO if libre < 50 else OK
        peor = _peor([peor, est])
        lineas.append(f"{nombre}: {libre:.0f} GB libres en {carpeta.drive or carpeta.anchor}")
    def _gb(p: Path) -> float:
        # lo que ocupa DE VERDAD: cada archivo físico una vez (las grabaciones repetidas de inbox son enlaces al
        # mismo archivo —clipper/duplicados.py— y sumarlas por nombre contaba 53 GB donde hay 9)
        from .duplicados import tamano_real
        return tamano_real(p) / 1e9
    try:
        lineas.append(f"Ocupan: videos {_gb(P['outbox']):.1f} GB · work {_gb(P['work']):.1f} GB · "
                      f"inbox {_gb(P['inbox']):.1f} GB")
    except OSError:
        pass
    det = {OK: "Espacio de sobra.", AVISO: "Queda poco espacio: abre «💾 Espacio» en Mis vídeos y libera lo que ya está publicado.",
           ERROR: "Casi sin espacio: un render puede fallar a la mitad."}[peor]
    return _r("disco", "Espacio en disco", peor, det, lineas)


def _c_config(cfg) -> dict:
    probs, avisos = [], []
    canal = cfg.get_path("videos.canales.ejemplo") or {}
    if not canal:
        probs.append("falta videos.canales.ejemplo")
    cam = canal.get("camara") or {}
    for k in ("x", "y", "w", "h"):
        try:
            if not 0.0 <= float(cam[k]) <= 1.0:
                probs.append(f"camara.{k} fuera de 0-1")
        except (KeyError, TypeError, ValueError):
            probs.append(f"falta camara.{k}")
    marca = cfg.get_path("videos.marca") or {}
    if not marca:
        probs.append("falta videos.marca (colores y fuentes de las tarjetas)")
    for k, v in marca.items():
        if str(k).startswith("fuente_") and not (ROOT / str(v)).is_file():
            probs.append(f"no existe la fuente {v}")
    whoosh = ROOT / str(cfg.get_path("transiciones.archivo", "assets/whoosh.wav"))
    if not whoosh.is_file():
        avisos.append(f"no existe {whoosh.name} (los cambios de cámara irán sin whoosh)")
    from . import musica
    carpeta = ROOT / str(cfg.get_path("videos.musica_carpeta", "musica_fondo"))
    n_musica = len([p for p in carpeta.iterdir() if p.suffix.lower() in musica._EXTENSIONES_AUDIO]) \
        if carpeta.is_dir() else 0
    if cfg.get_path("videos.musica_activo", False) and n_musica == 0:
        avisos.append("la música está activa pero musica_fondo/ no tiene canciones")
    if not os.environ.get("GEMINI_API_KEY"):
        probs.append("no hay GEMINI_API_KEY en el archivo .env")
    if not cfg.get_path("ai.modelos_auto", True) and not (cfg.get_path("ai.director_modelos") or []):
        avisos.append("ai.modelos_auto está apagado y ai.director_modelos vacío (sin cadena de modelos)")
    if probs:
        return _r("config", "Configuración", ERROR, "Hay problemas que romperían un vídeo.", probs + avisos)
    if avisos:
        return _r("config", "Configuración", AVISO, "Todo lo esencial está, con detalles.", avisos)
    return _r("config", "Configuración", OK,
              f"Canal, cámara, marca, fuentes, whoosh y {n_musica} canción(es) de música en su sitio.")


def _c_ilustraciones(cfg) -> dict:
    """La biblioteca de ilustraciones animadas: cada escena dibuja sin error, entra y sale de la nada,
    se mueve, no se corta y es determinista (mismos controles que `python -m clipper.ilustraciones --prueba`)."""
    from . import ilustraciones
    filas = ilustraciones.prueba(rapido=True)
    reglas = ilustraciones.prueba_reglas()
    mal = [f for f in filas if not f["ok"]]
    mal_r = [f for f in reglas if not f["ok"]]
    if mal or mal_r:
        return _r("ilustraciones", "Ilustraciones animadas", ERROR,
                  f"{len(mal)} escena(s) y {len(mal_r)} regla(s) con problemas.",
                  [f"{f['escena']}: {'; '.join(f['problemas'])}" for f in mal[:8]]
                  + [f"regla «{f['caso']}»: {f['detalle']}" for f in mal_r[:8]])
    return _r("ilustraciones", "Ilustraciones animadas", OK,
              f"Las {len(filas)} escenas dibujan, se mueven, entran y salen limpias y no se cortan; "
              f"{len(reglas)} reglas de selección bien.")


def _c_imagenes(cfg) -> dict:
    """Imágenes ilustrativas de Openverse: overlay + crédito CC + su propio evento «imagen» para
    «Ritmo del vídeo» cuando la búsqueda y la descarga salen bien, y que un fallo de cualquiera
    de las dos no deja overlay/crédito/evento huérfano ni rompe nada (sin red: con un Openverse falso)."""
    from . import imagenes
    filas = imagenes.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("imagenes", "Imágenes ilustrativas", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("imagenes", "Imágenes ilustrativas", OK,
              f"Los {len(filas)} casos (overlay + crédito CC + evento en «Ritmo del vídeo») salen bien.")


def _c_cadena_gemini(cfg) -> dict:
    """El reintento de la cadena de modelos (clipper/util.py, call_gemini_cadena): insiste en el
    MISMO modelo saturado por tiempo antes de bajar al siguiente, respeta el backoff, descarta al
    instante el que ya no existe, y sigue repitiendo la cadena si hace falta (sin red, sin Gemini
    de verdad: un cliente falso dispara los errores)."""
    from . import util
    filas = util.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("cadena_gemini", "Reintento de modelos saturados", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("cadena_gemini", "Reintento de modelos saturados", OK,
              f"Los {len(filas)} casos (insiste en el mejor, backoff, descarta el que no existe, repite la cadena) salen bien.")


def _c_inicio(cfg) -> dict:
    """La pantalla de primeros pasos: guardar la clave de Cloudflare solo si vale, sin tocar el resto del .env, sin enseñarla (sin red)."""
    from . import inicio
    filas = inicio.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("inicio", "Primeros pasos (claves)", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("inicio", "Primeros pasos (claves)", OK, f"Los {len(filas)} casos (formato, comprobar antes de guardar, .env intacto, nunca la clave entera) salen bien.")


def _c_carrusel(cfg) -> dict:
    """El carrusel para Instagram y TikTok (clipper/carrusel.py): de un vídeo sintético y con Gemini de mentira, comprueba las citas contra el vídeo, las medidas de las
    diapositivas, el PDF, los .zip y «✏ Editar» (que redibuja sin IA)."""
    from . import carrusel, carrusel_enlace, carrusel_texto
    filas = carrusel.prueba() + carrusel_enlace.prueba() + carrusel_texto.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("carrusel", "Carrusel para Instagram y TikTok", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("carrusel", "Carrusel para Instagram y TikTok", OK, f"Los {len(filas)} casos (citas comprobadas, 1080×1350 y 1080×1920, PDF, .zip y editar sin IA) salen bien.")


def _c_shorts_tt(cfg) -> dict:
    """Tools → Shorts · TikTok (clipper/shorts_tt.py + st_*): silencios, director de IA (uno falso), ritmo de «algo cada 2 s», tarjetas/escenas, montaje REAL con ffmpeg de un vídeo sintético
    (la cámara no sale en la pantalla), sonidos y subida a YouTube (falsa)."""
    from . import shorts_tt, st_plan, st_render, st_tarjetas, st_voz
    filas = st_voz.prueba() + st_plan.prueba() + st_tarjetas.prueba() + st_render.prueba() + shorts_tt.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("shorts_tt", "Shorts · TikTok", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("shorts_tt", "Shorts · TikTok", OK, f"Los {len(filas)} casos (silencios fuera, ritmo cada ≤ 2 s, la cámara no sale en la pantalla, 1080×1920, sonidos y textos de cada red) salen bien.")


def _c_posts(cfg) -> dict:
    """Tools → Posts (clipper/posts.py): con Gemini de mentira y un texto pegado, comprueba la portada variable (1/3 cada una), el parser (cita comprobada), las imágenes 4:5 y 16:9, editar sin IA y las descargas."""
    from . import posts, posts_estilos
    filas = posts.prueba() + posts_estilos.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("posts", "Posts para redes", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("posts", "Posts para redes", OK, f"Los {len(filas)} casos (portada con el mismo peso, cita comprobada, 4:5 y 16:9, editar sin IA y .zip) salen bien.")


def _c_miniaturas_sueltas(cfg) -> dict:
    """Tools → Miniaturas (clipper/miniaturas_sueltas.py): una miniatura SIN vídeo, con Cloudflare y Gemini de mentira: el concepto, el «↻» con la misma idea o una nueva, el titular, los
    reintentos, el tope propio de 15 llamadas al día y el historial."""
    from . import miniaturas_sueltas
    filas = miniaturas_sueltas.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("miniaturas_sueltas", "Miniaturas sueltas (Tools)", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("miniaturas_sueltas", "Miniaturas sueltas (Tools)", OK, f"Los {len(filas)} casos (concepto, «↻» misma idea o nueva, titular, reintentos, tope de 15 llamadas al día, historial) salen bien.")


def _c_shorts_publicacion(cfg) -> dict:
    """Título/tags de cada Short para YouTube y TikTok (clipper/shorts_meta.py) y subirlos a YouTube (clipper/shorts_yt.py), con Gemini y YouTube de mentira: nada se sube de verdad."""
    from . import shorts_meta, shorts_yt
    filas = shorts_meta.prueba() + shorts_yt.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("shorts_publicacion", "Shorts: título, tags y subida", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("shorts_publicacion", "Shorts: título, tags y subida", OK, f"Los {len(filas)} casos (una sola petición por versión, red de seguridad por código, subida sin duplicar, cuota agotada) salen bien.")


def _c_progreso_pro(cfg) -> dict:
    """La barra y el ETA de Vídeo PRO en el panel: el render (la etapa que más tarda) debe
    ocupar el hueco grande de la barra, no unos pocos puntos al final, y el ETA debe salir de
    datos reales en vez de un "2 minutos" fijo (sin arrancar Flask ni tocar los tiempos
    aprendidos de verdad)."""
    import webapp                                          # import diferido: webapp.py importa este módulo (evita el ciclo)
    filas = webapp.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("progreso_pro", "Barra y ETA de Vídeo PRO", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("progreso_pro", "Barra y ETA de Vídeo PRO", OK,
              f"Los {len(filas)} casos (reparto de la barra, ETA aprendido/extrapolado) salen bien.")


def _c_pipeline_zoom(cfg) -> dict:
    """Emparejar cada tramo "pantalla_zoom" con la ventana de actividad que más se solapa
    (`clipper/pipeline.py`, `_centros_pantalla_zoom`) — sin ffmpeg ni Gemini."""
    from . import pipeline
    filas = pipeline.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("pipeline_zoom", "Zoom a la pantalla y reglas del vídeo montado", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("pipeline_zoom", "Zoom a la pantalla y reglas del vídeo montado", OK,
              f"Los {len(filas)} casos (el zoom enseña TODO lo que hace el ratón o no hay zoom, suscríbete, tarjetas que ceden el sitio o repiten la escena «explicativa»…) salen bien.")


def _c_movimiento(cfg) -> dict:
    """Actividad sostenida en pantalla (mouse/scroll) para anclar «pantalla_zoom» — se distingue
    un movimiento real de una simple vibración, la caché funciona y la máscara de la cámara no
    revienta con un vídeo sintético (sin red, sin Gemini)."""
    from . import movimiento
    filas = movimiento.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("movimiento", "Actividad en pantalla", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("movimiento", "Actividad en pantalla", OK,
              f"Los {len(filas)} casos (detección, máscara de cámara, caché) salen bien.")


def _c_canales(cfg) -> dict:
    """El asistente de «Nuevo canal»: validar, guardar sin pisar a Mi canal y que el contexto, el nombre y los
    colores del canal lleguen de verdad al prompt de Gemini (con un Gemini falso: no gasta nada)."""
    from . import canales, ediciones_canal
    filas = canales.prueba() + ediciones_canal.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("canales", "Canales nuevos", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    n = len((cfg.get_path("videos.canales") or {}))
    return _r("canales", "Canales nuevos", OK, f"Los {len(filas)} casos del asistente salen bien ({n} canal(es) configurados).")


def _c_ejemplos(cfg) -> dict:
    """La galería «Ediciones» (cambios de escena, tarjetas, suscríbete): los ejemplos son fieles a los recortes y
    fundidos reales del render y no se rompen."""
    from . import ejemplos
    filas = ejemplos.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("ejemplos", "Ejemplos de ediciones", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("ejemplos", "Ejemplos de ediciones", OK,
              f"Los {len(filas)} casos (encuadres reales, fundido de las tarjetas, bucle limpio) salen bien.")


def _c_versiones(cfg) -> dict:
    """«¿Quieres mejorarle algo?»: las versiones de un vídeo (familia, tope de mejoras, tarjeta única en «Mis vídeos») y
    la mejora de la IA (dos relojes en la transcripción, plan revisado, red de seguridad, con un Gemini FALSO)."""
    from . import director, reglas, titulos, versiones
    filas = (versiones.prueba() + director.prueba_revision() + titulos.prueba()      # (+ «↻ Otros 3 títulos», Gemini falso)
             + reglas.prueba())                                                      # (+ 📌 reglas permanentes del director)
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("versiones", "Versiones, mejoras, «↻ Otros 3 títulos» y reglas permanentes", ERROR,
                  f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("versiones", "Versiones, mejoras, «↻ Otros 3 títulos» y reglas permanentes", OK,
              f"Los {len(filas)} casos salen bien (tope actual: {int(cfg.get_path('videos.mejoras_max', 3))} mejoras por vídeo).")


def _c_shorts(cfg) -> dict:
    """Los Shorts verticales (clipper/shorts.py + shorts_render.py): frases completas, reglas contra «a medias» y «no dice
    nada», selección con un Gemini FALSO (y su caché), y un render REAL de 10 s en 1080×1920 (ffmpeg, sin Gemini)."""
    from . import shorts, shorts_render
    filas = shorts.prueba() + shorts_render.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("shorts", "Shorts verticales", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    activo = cfg.get_path("videos.shorts_activo", True)
    return _r("shorts", "Shorts verticales", OK,
              f"Los {len(filas)} casos (frases completas, reglas, selección, render vertical) salen bien. "
              + ("Se crean en cada render." if activo else "Ahora están apagados (videos.shorts_activo)."))


def _c_historial(cfg) -> dict:
    """El historial permanente («Mis vídeos» sigue viéndose aunque borres el .mp4 del PC), la
    caché de métricas/retención de YouTube («Cómo le fue») y la lógica de elegir CPU o GPU para
    renderizar — nada de esto toca la red ni ffmpeg de verdad."""
    from . import historial, metricas, papelera, render
    filas = historial.prueba() + metricas.prueba() + render.prueba() + papelera.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("historial", "Historial, métricas y motor de render", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("historial", "Historial, métricas y motor de render", OK,
              f"Los {len(filas)} casos (fila que sobrevive al borrar el .mp4, caché de métricas de 6 h, estado, fecha y "
              f"miniatura sacados de YouTube, elegir CPU/GPU, el 🗑 que manda a la Papelera de reciclaje) salen bien.")


def _c_uso(cfg) -> dict:
    """La pestaña «Uso»: el día de Google (hora del Pacífico), las sumas, los porcentajes y el aviso de cerca del límite, el límite
    que trae un error 429 y que apuntar el uso jamás rompe una llamada."""
    from . import clave, cuota_yt, cuotas, modelos, uso
    filas = uso.prueba() + clave.prueba() + modelos.prueba() + cuotas.prueba() + cuota_yt.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("uso", "Pestaña «Uso» (consumo de Gemini)", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("uso", "Pestaña «Uso» (consumo de Gemini)", OK,
              f"Los {len(filas)} casos (día del Pacífico, escalera de modelos, cadena automática, límites por código, 429, cambio de clave) salen bien.")


def _clasifica_gemini(exc: Exception) -> tuple[str, str]:
    msg = str(exc)
    bajo = msg.lower()
    if any(t in bajo for t in ("404", "not_found", "not found", "no longer available")):
        return AVISO, "ya no existe (404)"
    if any(t in bajo for t in ("api key", "api_key", "permission_denied", "403", "401", "unauthenticated")):
        return ERROR, "la clave no vale (revisa GEMINI_API_KEY)"
    if any(t in bajo for t in ("503", "unavailable", "overloaded", "high demand", "429", "resource_exhausted")):
        return AVISO, "saturado ahora mismo"
    if "timeout" in bajo or "timed out" in bajo:
        return AVISO, "no contestó a tiempo"
    return AVISO, "error: " + msg.replace("\n", " ")[:90]


def _c_gemini(cfg) -> dict:
    """Un «responde OK» (unas decenas de tokens) a los modelos de la cadena, EN ORDEN y parando en el primero que responde: los Flash buenos
    solo dan 20 peticiones al día cada uno (plan gratis) y probarlos todos cada vez se comería esa cuota. Así se ve cuál respondería
    ahora al director y si el principal sigue vivo."""
    from . import director
    g = director._gemini(cfg)
    if not g:
        return _r("gemini", "Gemini (IA)", ERROR, "No hay clave de Gemini o la IA está apagada.",
                  ["Pon GEMINI_API_KEY=... en el archivo .env (se saca gratis en aistudio.google.com)."])
    client, types, _ = g
    from . import modelos as _mod
    mejor = (_mod.cadena_base(cfg, client) or [""])[0]            # el mejor de todos (pregunta a Google si la búsqueda caducó)
    modelos = director._cadena_modelos(cfg, client)               # el orden de HOY: los saturados o agotados van al final
    from .util import nombre_modelo, _uso_ok, _uso_fallo

    def _ping(m: str) -> tuple[bool, str]:
        t0 = time.time()
        try:
            r = client.models.generate_content(
                model=m, contents="Responde solo con la palabra OK.",
                config=types.GenerateContentConfig(
                    max_output_tokens=64, temperature=0,
                    http_options=types.HttpOptions(timeout=20000)))
            _uso_ok(m, r, time.time() - t0, "prueba")             # (gasta una petición de la cuota: se apunta en «Uso»)
            return True, f"✔ {nombre_modelo(m)} responde ({time.time() - t0:.1f} s)"
        except Exception as exc:  # noqa: BLE001
            _uso_fallo(m, exc, time.time() - t0, "prueba")
            est, txt = _clasifica_gemini(exc)
            return False, ("!" if est == ERROR else "") + f"✖ {nombre_modelo(m)} — {txt}"

    lineas: list[str] = []
    vivo = None
    probados = modelos[:4]                                        # los 3-4 primeros bastan para saber cómo está la cadena
    for m in probados:
        ok, linea = _ping(m)
        lineas.append(linea.lstrip("!"))
        if linea.startswith("!"):
            return _r("gemini", "Gemini (IA)", ERROR, "Google rechaza la clave.", lineas)
        if ok:
            vivo = m
            break
    if vivo and len(lineas) < len(modelos):
        lineas.append(f"· los otros {len(modelos) - len(lineas)} modelos de la cadena no se prueban (no hace falta y gastarían su cuota diaria)")
    lineas.append("· la cadena la arma el programa solo (del mejor al peor): " + " → ".join(nombre_modelo(m) for m in modelos[:6]) + (" …" if len(modelos) > 6 else ""))
    if vivo and vivo == mejor:
        return _r("gemini", "Gemini (IA)", OK, f"El mejor modelo responde ({nombre_modelo(vivo)}).", lineas)
    if vivo:
        return _r("gemini", "Gemini (IA)", AVISO, f"El mejor ({nombre_modelo(mejor)}) no está disponible ahora (saturado o sin cuota); el director usaría {nombre_modelo(vivo)}.", lineas)
    return _r("gemini", "Gemini (IA)", ERROR,
              f"Ninguno de los {len(probados)} primeros modelos responde: un vídeo saldría «NO analizado por Gemini» salvo que responda alguno de los siguientes.", lineas)


def _c_youtube(cfg) -> dict:
    try:
        from . import listas_yt, youtube

        filas = youtube.prueba() + listas_yt.prueba()
        mal = [f for f in filas if not f["ok"]]
        if mal:
            return _r("youtube", "YouTube", ERROR,
                      f"{len(mal)} de {len(filas)} casos con problemas "
                      "(reintento ante fallos transitorios al subir un vídeo, listas de reproducción).",
                      [f"{f['caso']}: {f['detalle']}" for f in mal])

        if not youtube.available():
            return _r("youtube", "YouTube", AVISO, "Sin conectar: falta client_secret.json.",
                      ["Hace falta para publicar desde el panel (Google Cloud → credenciales OAuth)."])
        if not youtube.is_connected():
            return _r("youtube", "YouTube", AVISO, "Hay credenciales pero no has conectado la cuenta.")
        d = youtube.days_left()
        if d is not None and d <= 1:
            return _r("youtube", "YouTube", AVISO, f"Conectado, pero el permiso caduca en {d} día(s).")
        return _r("youtube", "YouTube", OK, "Conectado" + (f" (caduca en {d} días)." if d is not None else "."))
    except Exception as exc:  # noqa: BLE001
        return _r("youtube", "YouTube", AVISO, f"No pude comprobarlo ({type(exc).__name__}).")


def _c_explicativa(cfg) -> dict:
    """4º modo de cámara "explicativa" (el autor, 2026-09-22): cara al tercio derecho + tarjeta de
    término+definición al resto — validación/fusión en `director.py` y el dibujo de la tarjeta en
    `tarjetas.py`. Sin ffmpeg ni Gemini (el recorte/composición de verdad se verificó aparte, con
    un render real y extracción de fotograma)."""
    from . import director, tarjetas
    filas = director.prueba_valida() + tarjetas.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("explicativa", "Escena «explicativa» (cara + término/definición)", ERROR,
                  f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("explicativa", "Escena «explicativa» (cara + término/definición)", OK,
              f"Los {len(filas)} casos (validar/degradar/fundir el plan, dibujar la tarjeta) salen bien.")


def _c_anclaje(cfg) -> dict:
    """El ANCLAJE (el autor, 2026-10-11): cada tarjeta, ilustración, imagen, palabra clave y escena sale cuando DICES lo que muestra, no 2-11 s
    antes (`clipper/anclaje.py`). Con trozos REALES de la transcripción de su vídeo; sin ffmpeg ni Gemini."""
    from . import anclaje
    filas = anclaje.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("anclaje", "Anclaje a lo que dices", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("anclaje", "Anclaje a lo que dices", OK,
              f"Los {len(filas)} casos (con tu vídeo real: «Google Developers», «Fase 1», «La vista de experto»…, por cita y por texto, "
              f"sin anclar a palabras que se cortan, idempotente) salen bien.")


def _c_ritmo(cfg) -> dict:
    """El RITMO (el autor, 2026-09-25, tras medir un vídeo de Alex Hormozi): las palabras clave que salen justo cuando las
    dices y el «metrónomo» que las reparte para que no haya tramos muertos de más de 15 s (`clipper/ritmo.py`). Sin
    ffmpeg ni Gemini (uno falso para los planes de antes); el render de prueba comprueba que una sale de verdad."""
    from . import ritmo
    filas = ritmo.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("ritmo", "Ritmo: palabras clave y metrónomo", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("ritmo", "Ritmo: palabras clave y metrónomo", OK,
              f"Los {len(filas)} casos (dónde se dice cada palabra, cifras y etiquetas, tramos muertos, nunca encima de una "
              f"tarjeta ni de una demostración, el .ass) salen bien.")


def _c_escenas(cfg) -> dict:
    """Las ESCENAS A PANTALLA COMPLETA (el autor, 2026-09-26): al menos una por vídeo, pocas, la 1.ª naranja y alternando con
    crema, nunca encima de una tarjeta ni de una demostración, el plan B de las ilustraciones y su dibujo (sin ffmpeg)."""
    from . import escena_completa, escenas_estilo
    filas = escena_completa.prueba() + escenas_estilo.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("escenas", "Escenas a pantalla completa", ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("escenas", "Escenas a pantalla completa", OK,
              f"Los {len(filas)} casos (reglas, fondos alternados, dónde no puede caer, dibujo y caché) salen bien.")


def _c_render_escena(cfg) -> dict:
    """Render REAL (ffmpeg) de una escena a pantalla completa sobre un clip sintético: entra en su segundo exacto, tapa el
    cuadro ENTERO con el fondo de la marca y se va; el audio sigue igual de largo. Sin Gemini."""
    import numpy as np
    from . import escena_completa, pipeline, render
    t0 = time.time()
    P = config.paths()
    carpeta = P["work"] / "_selftest_escena"
    shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True)
    try:
        fuente = carpeta / "fuente.mkv"
        p = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                  "color=c=0x1b2a41:s=1280x720:r=60:d=9", "-f", "lavfi", "-i", "sine=f=220:d=9:sample_rate=48000",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(fuente)], 120)
        if p.returncode != 0:
            return _r("render_escena", "Render de una escena a pantalla completa", ERROR, "No pude fabricar el clip.",
                      [(p.stderr or "")[-300:]], time.time() - t0)
        ch = pipeline._cfg_horizontal(cfg)
        W, H = int(ch.get_path("output.width", 1920)), int(ch.get_path("output.height", 1080))
        fps = min(int(ch.get_path("output.fps", 60)), 60)
        escenas = [{"t": 2.0, "tipo": "grafica_sube", "frase": "PRUEBA", "duracion": 4.5, "tono": "naranja", "fuente": "ia"}]
        capas = escena_completa.genera(escenas, dict(cfg.get_path("videos.marca") or {}), carpeta / "escenas", W, H, fps, cfg)
        salida = carpeta / "salida.mp4"
        render.render_merged(fuente, [(0.0, 9.0)], ch, {"width": 1280, "height": 720, "fps": 60.0, "duration": 9.0}, None,
                             salida, tarjetas_overlays=capas)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(salida), "-vf", "scale=64:36,format=rgb24", "-f", "rawvideo",
                              "-"], capture_output=True, timeout=120).stdout
        a = np.frombuffer(raw, np.uint8)
        n = len(a) // (64 * 36 * 3)
        f = a[: n * 64 * 36 * 3].reshape(n, 36, 64, 3).astype(int)
        nar = ((np.abs(f[..., 0] - 254) < 40) & (np.abs(f[..., 1] - 51) < 50) & (f[..., 2] < 80)).mean(axis=(1, 2))
        k = lambda s: min(n - 1, int(round(s * fps)))                                                   # noqa: E731
        lineas = [f"naranja en pantalla: {nar[k(1.0)]:.0%} antes → {nar[k(4.0)]:.0%} durante → {nar[k(7.5)]:.0%} después"]
        ok = nar[k(1.0)] < 0.05 and nar[k(4.0)] > 0.5 and nar[k(7.5)] < 0.05
        dur = float(json.loads(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                     str(salida)]).stdout)["format"]["duration"])
        ok = ok and abs(dur - 9.0) < 0.2
        lineas.append(f"dura {dur:.2f} s (esperado 9,00)")
        return _r("render_escena", "Render de una escena a pantalla completa", OK if ok else ERROR,
                  "La escena tapa el cuadro entero en su segundo y se va." if ok else "La escena no sale como debe.",
                  lineas, time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        return _r("render_escena", "Render de una escena a pantalla completa", ERROR,
                  f"La prueba falló: {type(exc).__name__}: {str(exc)[:200]}", [], time.time() - t0)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def _c_voz(cfg) -> dict:
    """Arranque y final medidos en el AUDIO (`clipper/voz.py`, el autor 2026-09-24): el vídeo empieza justo antes de tu
    primera sílaba (devuelve la palabra que un corte de «silencio» se comía por los tiempos de Whisper), acorta las
    pausas largas de los primeros segundos, quita el silencio del final y corta las tomas falsas del arranque. Con
    audios sintéticos calcados de las grabaciones reales (22-sep y 16-sep); sin Gemini ni ffmpeg. Incluye que un
    filtro de ffmpeg enorme va por archivo (el «No se encontró ffmpeg» de los vídeos -3 y -6 era la línea de
    comandos pasada de 32.767 caracteres)."""
    from . import voz
    filas = voz.prueba() + _prueba_filtro_largo()
    mal = [f for f in filas if not f["ok"]]
    nombre = "Arranque, pausas, bordes de los cortes, final y tomas falsas (medidos en el audio)"
    if mal:
        return _r("voz", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("voz", nombre, OK,
              f"Los {len(filas)} casos (empezar en tu voz, devolver la palabra comida, pausas del arranque, pausas de duda "
              "a media frase con el veto del director, bordes de los cortes sin comerse sílabas, final, tomas falsas, "
              "filtro de ffmpeg por archivo) salen bien.")


def _c_muletillas(cfg) -> dict:
    """«🗣️ Tus muletillas» (`clipper/muletillas.py`, el autor 2026-09-24) y el filtro de música estable (`musica.estable`):
    las reglas de contexto («qué bueno» no es muletilla, «Bueno,» al arrancar sí), los consejos, la tendencia sin contar
    dos veces la misma grabación, y que una canción con un «drop» no se use de fondo. Sin red, sin Gemini."""
    from . import muletillas, musica
    filas = muletillas.prueba() + musica.prueba()
    mal = [f for f in filas if not f["ok"]]
    nombre = "Muletillas y música de fondo estable"
    if mal:
        return _r("muletillas", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("muletillas", nombre, OK, f"Los {len(filas)} casos salen bien.")


def _c_ideas(cfg) -> dict:
    """💡 Ideas de vídeos y 📝 artículo para tu web (el autor, 2026-09-26): el freno (menos de 8 peticiones = no), la lista de 20
    (5 negocio + 5 aprender + 10 generales en español e inglés, 4-oct-2026), los datos con código (la IA no puede citar un dato
    que no existe), «🎬 La grabo» -> el director, el «↻» de cada idea con su tope por día, el perfil de cada canal (la primera vez
    que abre «Ideas» se configura; un canal de otro tema no recibe ideas de SEO); y el artículo de punta a punta con un vídeo
    sintético (capturas con ffmpeg de verdad). Gemini y YouTube de mentira: no gasta nada."""
    from . import articulo, articulos_sueltos, ideas, ideas_perfil, imagen_articulo, redes, sitio_astro
    filas = ideas.prueba() + ideas_perfil.prueba() + articulo.prueba() + redes.prueba() + sitio_astro.prueba() + imagen_articulo.prueba() + articulos_sueltos.prueba()
    mal = [f for f in filas if not f["ok"]]
    nombre = "Ideas de vídeos, artículo para tu web y redes sociales"
    if mal:
        return _r("ideas", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("ideas", nombre, OK, f"Los {len(filas)} casos (freno de peticiones, lista de 20 con su reparto, datos en español e "
                                   f"inglés, «↻» con su tope, el perfil de cada canal, «La grabo», artículo con capturas, publicaciones por red con su tope "
                                   f"y su enlace) salen bien.")


def _c_miniaturas_ia(cfg) -> dict:
    """🎨 Miniaturas con IA (el autor, 2026-09-28; `clipper/miniaturas_ia.py`): el prompt adaptado para FLUX (sin caras ni
    personas, sin titular, sin palabras que disparan el filtro), tu cara elegida por gesto y dirección con su contorno
    blanco, el titular que la figura no tapa, tu naranja exacto, la batería de neuronas y la regla de los 5 min con un
    Cloudflare de MENTIRA (no gasta neuronas). Y si Cloudflare está configurado en el .env."""
    from . import conceptos_miniatura, miniaturas_ia
    filas = miniaturas_ia.prueba() + conceptos_miniatura.prueba()      # (+ «↻» de cada concepto, con un Gemini FALSO)
    mal = [f for f in filas if not f["ok"]]
    nombre = "Miniaturas con IA (Cloudflare, tu cara, tu marca, sin texto de la IA)"
    if mal:
        return _r("miniaturas_ia", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    ok, motivo = miniaturas_ia.disponible(cfg)
    caras = miniaturas_ia.caras(cfg=cfg)
    if not ok or not caras:
        return _r("miniaturas_ia", nombre, AVISO, f"Los {len(filas)} casos salen bien, pero "
                  + (motivo if not ok else "no hay ninguna foto tuya en mis_caras/ (salen sin tu cara)."))
    return _r("miniaturas_ia", nombre, OK, f"Los {len(filas)} casos salen bien; Cloudflare configurado y "
                                           f"{len(caras)} fotos tuyas ({', '.join(miniaturas_ia.gestos(caras))}).")


def _c_nombres(cfg) -> dict:
    """🛡 Nombres de lo que sale en pantalla (el autor, 2026-10-03; `clipper/nombres.py`): la lista de nombres confirmados, las erratas
    claras que se corrigen por código, qué textos se revisan, el prompt y el parseo estricto, aplicar (corregir, quitar, degradar una
    escena «explicativa»), la caché, lo que escribe el autor en su comentario y qué pasa si la IA no responde — con un Gemini FALSO
    (no gasta nada)."""
    from . import nombres
    filas = nombres.prueba()
    mal = [f for f in filas if not f["ok"]]
    nombre = "Nombres de lo que sale en pantalla (confirmados antes de dibujarse)"
    if mal:
        return _r("nombres", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    canales = (cfg.get("videos") or {}).get("canales") or {}
    propios = sum(len(c.get("nombres_propios") or {}) for c in canales.values() if isinstance(c, dict))
    return _r("nombres", nombre, OK, f"Los {len(filas)} casos salen bien; {propios} nombre(s) propio(s) tuyos en la lista de confirmados.")


def _c_camara_aparte(cfg) -> dict:
    """📷 La cámara grabada aparte por OBS (el autor, 2026-09-28; `clipper/camara_aparte.py`): encontrar el archivo «camara»
    de una grabación, sincronizarlo por el movimiento y las imágenes, encontrar el recuadro dentro de la cámara (y el
    espejo) y que el render de «cara» salga de ella. Con vídeos SINTÉTICOS hechos con ffmpeg: no toca tus grabaciones."""
    from . import camara_aparte
    filas = camara_aparte.prueba()
    mal = [f for f in filas if not f["ok"]]
    nombre = "Cámara grabada aparte (Source Record de OBS)"
    if mal:
        return _r("camara_aparte", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    p = camara_aparte.parametros(cfg)
    return _r("camara_aparte", nombre, OK, f"Los {len(filas)} casos salen bien (encontrar, sincronizar, encuadrar, "
                                           f"render). " + (f"Encendida («{p['cara']}»): si una grabación trae su "
                                                           f"archivo de cámara, se usa; si no, como siempre."
                                                           if p["activo"] else "Apagada en config.yaml."))


def _c_espacio(cfg) -> dict:
    """💾 Espacio (el autor, 2026-10-04; `clipper/espacio.py`): qué se puede borrar de un vídeo ya publicado y qué NUNCA (uno sin publicar, uno que YouTube ya
    no devuelve, uno en marcha), borrar del todo lo de un vídeo SOLO dentro de videos/, work/ e inbox/ (tus grabaciones de OBS, fuera, no se tocan jamás),
    las cachés regenerables, cómo se encuentran las grabaciones de OBS (solo para decir que se quedan), lo que pesa cada vídeo y cuánto tarda en llenarse el
    disco, y las opciones de la limpieza automática. Con carpetas temporales: no toca nada tuyo."""
    from . import espacio
    filas = espacio.prueba()
    mal = [f for f in filas if not f["ok"]]
    nombre = "Espacio de los vídeos (💾 Espacio y el 🗑 completo)"
    if mal:
        return _r("espacio", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    auto, dias = espacio.opciones(cfg)
    return _r("espacio", nombre, OK, f"Los {len(filas)} casos salen bien (qué se borra y qué nunca —tus grabaciones de OBS jamás—, cachés, lo que pesa cada vídeo). "
                                     + (f"La limpieza automática está ENCENDIDA: borra lo publicado hace más de {dias} días (queda en el Historial)."
                                        if auto else "La limpieza automática está apagada: tú decides con el 🗑 de cada vídeo."))


def _c_vision(cfg) -> dict:
    """«La IA ve el vídeo» (`clipper/vision.py`, opción de ⚙️ Opciones, el autor 2026-09-24): la copia pequeña con ffmpeg de
    verdad (solo los fotogramas clave si la grabación los trae cada 2 s, como OBS), la pasada entera con un Gemini FALSO
    (sube, espera, pregunta a un Flash-Lite, guarda, reusa y borra lo subido) y la opción del panel. No gasta nada."""
    from . import vision
    filas = vision.prueba()
    mal = [f for f in filas if not f["ok"]]
    nombre = "La IA ve el vídeo (Flash-Lite, opción del panel)"
    if mal:
        return _r("vision", nombre, ERROR, f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    estado = "encendida" if vision.activo(cfg) else "apagada (se enciende en ⚙️ Opciones)"
    return _r("vision", nombre, OK, f"Los {len(filas)} casos salen bien. Ahora está {estado}.")


def _c_cifras(cfg) -> dict:
    """Las cifras con decimales de la transcripción (`transcribe.revisa_cifras`, el autor 2026-09-24: una tarjeta salía
    «36» en vez de «3,6»): unir «12» + «,4», cambiar solo cuando Whisper oye la misma cifra con decimal, y hacerlo una
    sola vez por grabación. Sin Whisper (un «oído» falso)."""
    from . import transcribe
    filas = transcribe.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("cifras", "Cifras con decimales (3,6) en la transcripción", ERROR,
                  f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("cifras", "Cifras con decimales (3,6) en la transcripción", OK,
              f"Los {len(filas)} casos (unir decimales partidos, «36» -> «3,6» solo si se oye así, una vez por "
              "grabación) salen bien.")


def _c_duplicados(cfg) -> dict:
    """Grabaciones repetidas en inbox (el autor 2026-09-24: 12 copias de 2 grabaciones, 44 GB repetidos): se enlazan al
    mismo archivo en vez de ocupar otra copia, y una subida repetida reusa la transcripción. Con archivos pequeños en
    una carpeta temporal (no toca inbox)."""
    from . import duplicados
    filas = duplicados.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("duplicados", "Grabaciones repetidas (sin ocupar espacio de nuevo)", ERROR,
                  f"{len(mal)} de {len(filas)} casos con problemas.", [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("duplicados", "Grabaciones repetidas (sin ocupar espacio de nuevo)", OK,
              f"Los {len(filas)} casos (enlazar copias idénticas, subida repetida que reusa la transcripción, un vídeo "
              "nuevo no se toca) salen bien.")


def _prueba_filtro_largo() -> list[dict]:
    """Un `-filter_complex` de ~60.000 caracteres (el doble de lo que admite Windows en una línea de comandos) pasa
    por `util.run` y ffmpeg lo ejecuta; y el comando que se lanza de verdad es corto."""
    from .util import filtro_a_archivo, run
    filas: list[dict] = []
    fc = "[0:v]" + ",".join(["null"] * 12000) + "[v]"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=64x36:d=0.2:r=10",
           "-filter_complex", fc, "-map", "[v]", "-f", "null", "-"]
    corto, tmp = filtro_a_archivo(cmd)
    try:
        filas.append({"caso": "un filtro de 60.000 caracteres se pasa por archivo y la línea de comandos queda corta",
                      "ok": tmp is not None and len(subprocess.list2cmdline(corto)) < 2000,
                      "detalle": f"{len(subprocess.list2cmdline(corto))} caracteres"})
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    try:
        run(cmd)
        filas.append({"caso": "ffmpeg ejecuta ese filtro enorme sin «WinError 206»", "ok": True, "detalle": ""})
    except Exception as exc:  # noqa: BLE001
        filas.append({"caso": "ffmpeg ejecuta ese filtro enorme sin «WinError 206»", "ok": False,
                      "detalle": str(exc)[:200]})
    return filas


def _c_miniaturas_custom(cfg) -> dict:
    """Miniaturas propias que el autor sube a mano como candidatas "A/B/C" junto a
    la automática (`clipper/miniaturas_custom.py`) — validación, tope de peso
    y que un id raro nunca resuelva una ruta fuera de su carpeta."""
    from . import miniaturas_custom
    filas = miniaturas_custom.prueba()
    mal = [f for f in filas if not f["ok"]]
    if mal:
        return _r("miniaturas_custom", "Miniaturas propias (A/B/C)", ERROR,
                  f"{len(mal)} de {len(filas)} casos con problemas.",
                  [f"{f['caso']}: {f['detalle']}" for f in mal])
    return _r("miniaturas_custom", "Miniaturas propias (A/B/C)", OK,
              f"Los {len(filas)} casos (validar, guardar, listar, proteger la ruta) salen bien.")


def _fetch_url(url: str, timeout: float = 15.0) -> tuple[int, str]:
    """(código HTTP, URL final) de una dirección; (0, "") si no responde."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (video-pro-studio)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.geturl()
    except urllib.error.HTTPError as exc:
        return int(exc.code), url
    except Exception:  # noqa: BLE001
        return 0, ""


def _c_enlaces_sitio(cfg) -> dict:
    """Los enlaces fijos que el panel pone en las descripciones (los de cada canal que hayas creado) siguen respondiendo.
    Si cambias tu web y una ruta desaparece, las descripciones nuevas llevarían un enlace roto."""
    try:
        from concurrent.futures import ThreadPoolExecutor
        canales = {k: v for k, v in (config.load().get_path("videos.canales") or {}).items() if isinstance(v, dict) and not v.get("ejemplo")}
        objetivos = [(f"{k}/{n}", u) for k, v in canales.items() for n, u in (v.get("enlaces") or {}).items()
                     if isinstance(u, str) and u.startswith("http") and n not in ("linkedin", "instagram", "tiktok")]   # esas redes responden 999/403 a los bots
        t0 = time.time()
        if not objetivos:
            return _r("enlaces", "Enlaces de tu web", AVISO, "Todavía no hay enlaces que comprobar (crea tu canal en «➕ Nuevo canal»).", seg=0.0)
        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(lambda x: _fetch_url(x[1], timeout=15), objetivos))
        mal = [f"{n}: HTTP {st or 'sin respuesta'} → {u}" for (n, u), (st, _fin) in zip(objetivos, res) if st != 200]
        seg = time.time() - t0
        if len(mal) > max(2, len(objetivos) // 4):     # muchos a la vez = sin internet, no enlaces rotos
            return _r("enlaces", "Enlaces de tu web", AVISO,
                      f"{len(mal)} de {len(objetivos)} no responden: ¿hay internet?", mal[:8], seg=seg)
        if mal:
            return _r("enlaces", "Enlaces de tu web", AVISO,
                      f"{len(mal)} enlace(s) que las descripciones usan no responden: revísalos.", mal, seg=seg)
        return _r("enlaces", "Enlaces de tu web", OK, f"Los {len(objetivos)} enlaces fijos de tus canales responden.", seg=seg)
    except Exception as exc:  # noqa: BLE001
        return _r("enlaces", "Enlaces de tu web", AVISO, f"No pude comprobarlos ({type(exc).__name__}).")


def _genera_fuente(destino: Path) -> None:
    """2560x1440 a 60 fps en MKV (como tu grabación): fondo azul oscuro liso (así cualquier
    tarjeta crema destaca), un cuadro que se mueve por la mitad de ARRIBA, y una voz
    sintética (ruido rosa con envolvente de rachas) para poder medir volumen y clics."""
    # La "voz" son tres tonos con frecuencias NO redondas y un poco de vibrato (como una vocal):
    # es suave, así que empalmarla por un punto cualquiera deja un escalón fácil de medir; con
    # frecuencias redondas dos puntos separados por un nº entero de ciclos casarían por casualidad.
    voz = ("0.12*sin(2*PI*183.7*t+2*sin(2*PI*4.6*t))+0.08*sin(2*PI*371.3*t+1.5*sin(2*PI*5.1*t))"
           "+0.05*sin(2*PI*827.9*t)")
    env = "if(lt(mod(t,3),2.2),min(1,6*sin(PI*mod(t,3)/2.2)),0)"
    fc = ("[0:v][1:v]overlay=x='mod(t*230,2200)':y='150+250*sin(t*2.1)'[v];"
          f"[2:a]volume='{env}':eval=frame,aformat=sample_rates=48000:channel_layouts=stereo[a]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"color=c=0x1b2a41:s=2560x1440:r=60:d={_DUR}",
           "-f", "lavfi", "-i", f"color=c=0xe07a5f:s=320x220:r=60:d={_DUR}",
           "-f", "lavfi", "-i", f"aevalsrc={voz}:s=48000:d={_DUR}",
           "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
           # audio SIN pérdida: con AAC el decodificador desplaza la onda ~21 ms y los valores
           # exactos de los puntos de corte (los que hacen sonar el clic) dejarían de ser los calculados
           "-c:a", "pcm_s16le", str(destino)]
    p = _run(cmd, 120)
    if p.returncode != 0 or not destino.is_file():
        raise RuntimeError("no pude fabricar el clip de prueba: " + (p.stderr or "")[-300:])


def _palabras() -> list[dict]:
    out = []
    for a, b in _RACHAS:
        t = a + 0.1
        while t + 0.25 < b:
            out.append({"word": "prueba", "start": round(t, 2), "end": round(t + 0.25, 2)})
            t += 0.3
    return out


def _pcm(path: Path, t0: float, dur: float):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
                          "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                         capture_output=True, timeout=60).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _rgb_esquina(path: Path, t0: float, dur: float, cfg):
    """Fotogramas RGB de la esquina de arriba a la derecha (donde salen las ilustraciones) reducidos
    a 130x110: [n, 110, 130, 3]. El fondo del clip de prueba es azul liso, así que el 'crema' de la
    marca solo aparece si hay una ilustración (o una tarjeta, que va abajo a la izquierda)."""
    import numpy as np
    W = int(cfg.get_path("output.width", 1920))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.4f}", "-t", f"{dur:.4f}", "-i", str(path),
                          "-an", "-vf", f"crop=520:440:{W - 520}:0,scale=130:110,format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True, timeout=90).stdout
    a = np.frombuffer(raw, dtype=np.uint8)
    n = len(a) // (130 * 110 * 3)
    return a[: n * 130 * 110 * 3].reshape(n, 110, 130, 3)


def _gris_zona_tarjeta(path: Path, t0: float, dur: float, cfg):
    """Fotogramas de la zona donde entra una tarjeta (abajo-izquierda, con el mismo margen
    que usa render.py) en gris y reducidos: [n, 17, 56]. Ahí el fondo del clip de prueba es
    liso, así que su brillo medio solo cambia si hay una tarjeta encima."""
    import numpy as np
    W, H = int(cfg.get_path("output.width", 1920)), int(cfg.get_path("output.height", 1080))
    mx, my = round(W * 0.05), round(H * 0.06)
    ch = 170
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
                          "-an", "-vf", f"crop=560:{ch}:{mx}:{H - my - ch},scale=56:17,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, timeout=90).stdout
    a = np.frombuffer(raw, dtype=np.uint8)
    n = len(a) // (56 * 17)
    return a[: n * 56 * 17].reshape(n, 17, 56)


def _volumen(path: Path) -> tuple[float | None, float | None]:
    p = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-vn",
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    txt = p.stderr or ""
    i = txt.rfind("Integrated loudness")
    if i < 0:
        return None, None
    tail = txt[i:]
    mi = re.search(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", tail)
    mp = re.search(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", tail)
    return (float(mi.group(1)) if mi else None), (float(mp.group(1)) if mp else None)


def _mide_render(cfg, mp4: Path, manifest: dict, jid: str) -> tuple[list[str], str]:
    """Todas las medidas sobre el vídeo de prueba -> (líneas ✔/✖/⚠, peor estado)."""
    import numpy as np
    from . import pipeline
    lineas: list[str] = []
    estados: list[str] = []

    def _v(ok: bool, texto: str, avisar: bool = False) -> None:
        estados.append(OK if ok else (AVISO if avisar else ERROR))
        lineas.append(("✔ " if ok else ("⚠ " if avisar else "✖ ")) + texto)

    info = json.loads(_run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                            "-show_streams", str(mp4)]).stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    au = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    W, H = int(cfg.get_path("output.width", 1920)), int(cfg.get_path("output.height", 1080))
    fps_cfg = min(int(cfg.get_path("output.fps", 30)), 60)          # la fuente es de 60 fps
    num, den = (vs.get("avg_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den or 1)
    _v(abs(fps - fps_cfg) < 0.01, f"{fps:.2f} fps (config: {fps_cfg})")
    _v((int(vs["width"]), int(vs["height"])) == (W, H) and vs.get("pix_fmt") == "yuv420p",
       f"{vs['width']}×{vs['height']} {vs.get('pix_fmt')} ({vs.get('codec_name')} nivel {vs.get('level')})")
    if au is None:
        _v(False, "el vídeo salió SIN audio")
        return lineas, ERROR
    _v(au.get("codec_name") == "aac" and int(au.get("sample_rate", 0)) == 48000
       and int(au.get("channels", 0)) == 2, f"audio {au.get('codec_name')} {au.get('sample_rate')} Hz "
       f"{au.get('channels')} canales")

    dv, da = float(vs.get("duration") or 0), float(au.get("duration") or 0)
    esperada = _DUR - sum(b - a for a, b in (_CORTE_PAUSA, _CORTE_VOZ))
    _v(abs(dv - esperada) < 0.15, f"dura {dv:.2f} s (esperado {esperada:.2f} s)")
    _v(abs(dv - da) < 0.06, f"vídeo y audio sincronizados (diferencia {abs(dv - da) * 1000:.0f} ms)")

    # volumen (EBU R128) frente al objetivo del config
    objetivo = float(cfg.get_path("output.loudness_lufs", -14))
    tp = float(cfg.get_path("output.true_peak_db", -1.5))
    li, pico = _volumen(mp4)
    if li is None:
        _v(False, "no pude medir el volumen", avisar=True)
    else:
        _v(abs(li - objetivo) <= 3.0, f"volumen {li:.1f} LUFS (objetivo {objetivo:g} ±3)", avisar=True)
        _v(pico is None or pico <= tp + 0.6, f"pico {pico:.1f} dBFS (tope {tp:g})")

    # empalmes: ¿hay un «clic» en algún corte de verdad?
    segs = [tuple(s) for s in manifest.get("segments") or []]
    joins, t_acum = [], 0.0
    for i in range(len(segs) - 1):
        t_acum += segs[i][1] - segs[i][0]
        if segs[i + 1][0] - segs[i][1] > 0.02:
            joins.append(t_acum)
    x_ok, malos = 0, []
    for tj in joins:
        x = _pcm(mp4, max(0.0, tj - 1.0), 2.0)
        n = int(round(min(tj, 1.0) * 48000))
        if len(x) < n + 800 or n < 800:
            continue
        d = np.abs(np.diff(x))
        cerca = float(d[max(0, n - 150): n + 150].max())
        entorno = np.concatenate([d[max(0, n - 24000): n - 150], d[n + 150: n + 24000]])
        p99 = float(np.percentile(entorno, 99)) if len(entorno) else 1.0
        # clic = escalón grande frente a la voz de alrededor Y audible en términos absolutos
        if cerca > 2.5 * p99 and cerca > 0.05:
            malos.append(f"{tj:.2f} s (salto {cerca:.2f} = {cerca / max(p99, 1e-9):.0f}× lo normal)")
        else:
            x_ok += 1
    if not joins:
        _v(False, "no salió ningún corte en el vídeo de prueba (¿se ignoraron los recortes?)")
    else:
        _v(not malos, f"{x_ok} de {len(joins)} empalmes limpios, sin clic"
           + ("" if not malos else " — CON CLIC en: " + "; ".join(malos)))

    # música: en una pausa de la voz tiene que seguir sonando algo (2.º pase de ffmpeg)
    seg_p = pipeline._tiempo_final(14.6, segs)
    if seg_p is not None:
        y = _pcm(mp4, seg_p - 0.2, 0.4)
        rms = 20 * np.log10(float(np.sqrt(np.mean(y ** 2))) + 1e-9) if len(y) else -99.0
        _v(-70.0 < rms < -20.0, f"música presente en las pausas ({rms:.0f} dBFS)")

    # tarjetas: la de "Astro" empieza en 1.0 s y dura 3.5; debe verse y luego irse
    g = _gris_zona_tarjeta(mp4, 0.6, 4.2, cfg)              # 0.6 s … 4.8 s a 60 fps
    if len(g) >= 250:
        brillo = g.reshape(len(g), -1).mean(axis=1)
        antes, durante, despues = brillo[:20].mean(), brillo[100:160].mean(), brillo[-8:].mean()
        _v(durante - antes > 40 and abs(despues - antes) < 15,
           f"la tarjeta se ve al entrar y desaparece al salir (brillo {antes:.0f} → {durante:.0f} → {despues:.0f})")
        # fluidez del fundido: cada fotograma del fundido tiene que ser distinto del anterior.
        # Si el fps de las tarjetas no cuadra con el del vídeo, cada 3.er fotograma se repite.
        techo, base = brillo[100:160].mean(), antes
        lo, hi = base + 0.04 * (techo - base), base + 0.96 * (techo - base)
        k0 = next((k for k in range(len(brillo)) if brillo[k] > lo), None)
        k1 = next((k for k in range(k0, len(brillo)) if brillo[k] >= hi), None) if k0 is not None else None
        if k0 is not None and k1 is not None and k1 - k0 >= 6:
            pasos = np.diff(brillo[max(0, k0 - 1): k1 + 1])          # solo el fundido de ENTRADA
            iguales = int(np.sum(np.abs(pasos) < 0.3))
            _v(iguales <= 2, f"el fundido de la tarjeta es fluido ({len(pasos)} fotogramas, {iguales} repetido(s))")
        else:
            _v(False, "no pude medir el fundido de la tarjeta", avisar=True)
    else:
        _v(False, "no pude extraer los fotogramas de la tarjeta", avisar=True)

    ev = manifest.get("eventos_visuales") or []
    _v(any(str(e.get("tipo", "")).startswith("tarjeta:") for e in ev),
       f"el manifest registra {len(ev)} evento(s) visuales", avisar=True)

    # ilustración animada: aparece arriba a la derecha, entra/se mueve SIN fotogramas repetidos
    # (a los fps exactos del vídeo) y se va sin dejar rastro
    il_ev = [e for e in ev if str(e.get("tipo", "")).startswith("ilustracion:")]
    _v(len(il_ev) == 1, f"la ilustración de prueba salió en el manifest ({len(il_ev)} de 1)")
    t_ini = pipeline._tiempo_final(_PLAN["ilustraciones"][0]["t"], segs)
    if il_ev and t_ini is not None:
        t_ini = round(t_ini * fps_cfg) / fps_cfg
        dur_il = round(_PLAN["ilustraciones"][0]["duracion"] * fps_cfg) / fps_cfg
        rgb = _rgb_esquina(mp4, t_ini - 0.30, dur_il + 0.70, cfg)          # 0.3 s antes … 0.4 s después
        n0 = int(round(0.30 * fps_cfg))
        if len(rgb) >= n0 + int(dur_il * fps_cfg):
            # «crema» de la marca (#FFFBF2): el fondo del clip de prueba nunca la tiene
            crema = ((np.abs(rgb[..., 0].astype(np.int16) - 255) < 16) & (np.abs(rgb[..., 1].astype(np.int16) - 251) < 16)
                     & (np.abs(rgb[..., 2].astype(np.int16) - 242) < 16)).reshape(len(rgb), -1).mean(axis=1)
            antes, durante, despues = crema[:n0 - 2].mean(), crema[n0 + int(1.0 * fps_cfg): n0 + int(2.4 * fps_cfg)].mean(), crema[-6:].mean()
            _v(durante > 0.03 and antes < 0.004 and despues < 0.004,
               f"la ilustración aparece y se va (crema en su esquina: {antes:.1%} → {durante:.1%} → {despues:.1%})")
            gris = rgb.mean(axis=3)
            pasos = np.abs(np.diff(gris, axis=0)).mean(axis=(1, 2))
            entrada = pasos[n0 + 3: n0 + 28]                                # su entrada de 0.5 s (sin los 2 primeros)
            repetidos = int((entrada < 0.05).sum())
            _v(repetidos <= 2, f"la ilustración se mueve fluida al entrar ({len(entrada)} fotogramas, {repetidos} repetido(s))")
        else:
            _v(False, "no pude extraer los fotogramas de la ilustración", avisar=True)

    # palabra clave (clipper/ritmo.py): sale abajo en el centro, en los colores de la marca, a su segundo, y se va
    pal_ev = [e for e in ev if e.get("tipo") == "palabra"]
    _v(len(pal_ev) == 1, f"la palabra clave de prueba salió en el manifest ({len(pal_ev)} de 1)")
    if pal_ev:
        t_k = float(pal_ev[0]["t"])
        W_, H_ = int(cfg.get_path("output.width", 1920)), int(cfg.get_path("output.height", 1080))
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t_k - 0.5):.3f}", "-t", "2.6", "-i", str(mp4),
                              "-an", "-vf", f"crop=800:170:{(W_ - 800) // 2}:{H_ - 300},scale=200:42,format=rgb24",
                              "-f", "rawvideo", "-"], capture_output=True, timeout=90).stdout
        a = np.frombuffer(raw, dtype=np.uint8)
        n = len(a) // (200 * 42 * 3)
        if n >= int(2.0 * fps_cfg):
            f_ = a[: n * 200 * 42 * 3].reshape(n, 42, 200, 3).astype(np.int16)
            # el naranja (#FE330A) de la palabra: el fondo azul del clip de prueba nunca lo tiene
            nar = ((np.abs(f_[..., 0] - 254) < 40) & (np.abs(f_[..., 1] - 51) < 45) & (f_[..., 2] < 70)).reshape(n, -1).mean(axis=1)
            k0 = int(0.3 * fps_cfg)
            antes, durante, despues = nar[:k0].mean(), nar[int(0.9 * fps_cfg): int(1.6 * fps_cfg)].mean(), nar[-8:].mean()
            _v(durante > 0.02 and antes < 0.002 and despues < 0.002,
               f"la palabra clave aparece abajo en el centro y se va (naranja: {antes:.1%} → {durante:.1%} → {despues:.1%})")
        else:
            _v(False, "no pude extraer los fotogramas de la palabra clave", avisar=True)
    return lineas, _peor(estados)


def _c_render(cfg, conservar: bool = False) -> dict:
    """Fabrica un clip, lo pasa por run_video_pro.py con un plan fijo y lo mide.
    `conservar=True` deja los archivos (clip, vídeo de salida) para inspeccionarlos."""
    from . import director, pipeline
    t0 = time.time()
    P = config.paths()
    carpeta = P["work"] / "_selftest"
    shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True)
    # .mkv como las grabaciones de OBS: sus fotogramas traen marcas de tiempo en milisegundos
    # (16.667 -> 17), justo lo que desalineaba las tarjetas cuando faltaba el filtro fps.
    fuente = carpeta / f"{STEM_PRUEBA}.mkv"
    base = pipeline.job_id(str(fuente))
    work, out_dir = P["work"] / f"{base}--pro", P["outbox"] / f"{base}--pro"

    def _limpia(final: bool = False) -> None:
        if final and conservar:
            return
        for d in (work, out_dir, carpeta):
            shutil.rmtree(d, ignore_errors=True)

    _limpia()
    try:
        carpeta.mkdir(parents=True)
        _genera_fuente(fuente)
        work.mkdir(parents=True)
        (work / "words.json").write_text(json.dumps(_palabras()), encoding="utf-8")
        director.guardar(work, "pro", _PLAN)
        # los Shorts de este render de prueba, ya «elegidos» (ninguno): así el paso de Shorts corre entero SIN llamar a
        # Gemini de verdad (probar el sistema no debe gastar cuota)
        from . import shorts as _shorts
        (work / "shorts_pro.json").write_text(json.dumps({"v": _shorts.VERSION, "shorts": [], "notas": [],
                                                          "motivo": "render de prueba (sin Gemini)"}), encoding="utf-8")
        p = subprocess.run([sys.executable, "-u", str(ROOT / "run_video_pro.py"), str(fuente),
                            "--canal", "ejemplo"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=420, cwd=str(ROOT))
        mp4 = out_dir / "pro.mp4"
        if p.returncode != 0 or not mp4.is_file():
            cola = [l for l in (p.stdout + "\n" + p.stderr).splitlines() if l.strip()][-6:]
            return _r("render", "Render de prueba (clip sintético)", ERROR,
                      "El pipeline no llegó a sacar el vídeo.", cola, time.time() - t0)
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest = manifest[0] if isinstance(manifest, list) else manifest
        lineas, estado = _mide_render(cfg, mp4, manifest, base)
        # las alertas de suscríbete y like suenan con su «pop», no con el etéreo de las tarjetas (el autor, 2026-09-26): el
        # render apunta en su log lo que mezcló («sonidos: 3 etéreo (…), 2 pop (…)»)
        m_pop = re.search(r"sonidos:[^\n]*?(\d+) pop", p.stdout)
        if m_pop and int(m_pop.group(1)) >= 1:
            lineas.append(f"✔ las alertas de suscríbete y like suenan con su «pop» ({m_pop.group(1)}), no como una tarjeta")
        else:
            lineas.append("✖ las alertas de suscríbete y like no llevan su «pop» (ver «sonidos:» en el log del render)")
            estado = ERROR
    except subprocess.TimeoutExpired:
        return _r("render", "Render de prueba (clip sintético)", ERROR,
                  "Tardó más de 7 minutos: algo va muy lento o se colgó.", [], time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        return _r("render", "Render de prueba (clip sintético)", ERROR,
                  f"La prueba falló: {type(exc).__name__}: {str(exc)[:200]}", [], time.time() - t0)
    finally:
        _limpia(final=True)
    det = {OK: "Sale a 60 fps, con el volumen, los cortes, las tarjetas y la música bien.",
           AVISO: "Sale bien, con algún detalle a revisar.",
           ERROR: "Algo del render no cumple: no publiques un vídeo hasta arreglarlo."}[estado]
    return _r("render", "Render de prueba (clip sintético)", estado, det, lineas, time.time() - t0)


# ------------------------------------------------------------- orquestación
_RAPIDAS = (("librerias", _c_librerias), ("ffmpeg", _c_ffmpeg), ("gpu", _c_gpu),
            ("disco", _c_disco), ("config", _c_config), ("ilustraciones", _c_ilustraciones), ("imagenes", _c_imagenes),
            ("movimiento", _c_movimiento), ("pipeline_zoom", _c_pipeline_zoom),
            ("cadena_gemini", _c_cadena_gemini), ("inicio", _c_inicio), ("carrusel", _c_carrusel), ("posts", _c_posts), ("shorts_tt", _c_shorts_tt), ("miniaturas_sueltas", _c_miniaturas_sueltas), ("shorts_publicacion", _c_shorts_publicacion), ("progreso_pro", _c_progreso_pro),
            ("canales", _c_canales), ("ejemplos", _c_ejemplos), ("versiones", _c_versiones), ("shorts", _c_shorts), ("historial", _c_historial),
            ("uso", _c_uso), ("enlaces", _c_enlaces_sitio), ("gemini", _c_gemini),
            ("youtube", _c_youtube), ("miniaturas_custom", _c_miniaturas_custom),
            ("explicativa", _c_explicativa), ("anclaje", _c_anclaje), ("ritmo", _c_ritmo), ("escenas", _c_escenas), ("voz", _c_voz),
            ("cifras", _c_cifras),
            ("duplicados", _c_duplicados),
            ("vision", _c_vision), ("muletillas", _c_muletillas), ("ideas", _c_ideas),
            ("miniaturas_ia", _c_miniaturas_ia), ("nombres", _c_nombres), ("camara_aparte", _c_camara_aparte),
            ("espacio", _c_espacio))


def correr(cfg=None, con_render: bool = True, con_gemini: bool = True, al_terminar_uno=None) -> dict:
    """Corre todo y devuelve {"checks": [...], "estado": peor, "inicio", "fin"}.
    `al_terminar_uno(resultado)` se llama tras cada comprobación (para el panel en vivo)."""
    cfg = cfg or config.load()
    inicio = time.time()
    checks: list[dict] = []

    def _uno(id_, nombre, fn):
        t = time.time()
        try:
            r = fn(cfg)
        except Exception as exc:  # noqa: BLE001 — una comprobación rota no tumba a las demás
            r = _r(id_, nombre, ERROR, f"La propia comprobación falló: {type(exc).__name__}: {str(exc)[:160]}")
        r["seg"] = r.get("seg") if r.get("seg") is not None else round(time.time() - t, 1)
        checks.append(r)
        if al_terminar_uno:
            al_terminar_uno(r)

    for id_, fn in _RAPIDAS:
        if id_ == "gemini" and not con_gemini:
            continue
        _uno(id_, id_, fn)
    if con_render:
        _uno("render", "Render de prueba", _c_render)
        _uno("render_escena", "Render de una escena a pantalla completa", _c_render_escena)
    res = {"checks": checks, "estado": _peor([c["estado"] for c in checks]),
           "inicio": inicio, "fin": time.time(), "con_render": con_render}
    try:
        ULTIMO.parent.mkdir(parents=True, exist_ok=True)
        ULTIMO.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return res


def ultimo() -> dict | None:
    try:
        return json.loads(ULTIMO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


if __name__ == "__main__":       # python -m clipper.selftest [--rapido] [--sin-gemini]   (--sin-gemini: no gasta ni una petición de la cuota)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    icono = {OK: "✅", AVISO: "⚠️ ", ERROR: "❌"}
    r = correr(con_render="--rapido" not in sys.argv, con_gemini="--sin-gemini" not in sys.argv,
               al_terminar_uno=lambda c: print(f"{icono[c['estado']]} {c['nombre']}: {c['detalle']}  ({c['seg']} s)\n"
                                              + "".join(f"     {l}\n" for l in c["lineas"]), flush=True))
    print(f"\nRESULTADO: {icono[r['estado']]} {r['estado'].upper()}")
    sys.exit(0 if r["estado"] != ERROR else 1)
