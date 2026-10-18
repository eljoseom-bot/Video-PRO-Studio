#!/usr/bin/env python3
"""UI web local para el editor de vídeo (Mi canal y futuros canales de vídeo).

    python webapp.py            ->  http://127.0.0.1:8010

Dos formas de sacar vídeo horizontal para YouTube: "Generador" (un directo
entero -> varios vídeos por temática) y "Vídeos PRO" (un archivo ya grabado
-> un vídeo editado de punta a punta, con tarjetas y zoom de cámara). Ves el
log en vivo y, al terminar, una galería para revisar, editar y publicar.

Todo esto NO usa nada de Claude: es Python + ffmpeg + tu GPU + tu key de
Gemini. Proyecto SEPARADO de otros programas (el generador de Shorts): carpeta
propia, config.yaml propio, y su propia conexión de YouTube (client_secret.json
y work/youtube_token.json de aquí, no los de otros programas) — pensado para un
canal distinto.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path

from flask import (
    Flask, Response, abort, after_this_request, jsonify, redirect,
    render_template, request, send_file, send_from_directory, url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from clipper import config, historial, listas_yt, miniaturas_custom, pipeline, taste_videos, thumbs, youtube
from clipper import selftest
from clipper.util import log as clipper_log, safe_slug

# La salida del panel va a un FICHERO (work/panel.log). En Windows, un stdout
# redirigido usa cp1252, y cualquier emoji revienta con UnicodeEncodeError.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
P = config.paths()
JOBS_FILE = P["work"] / "webui_jobs.json"
PUB_FILE = P["work"] / "youtube_publish.json"
LAST_BATCH_FILE = P["work"] / "publish-last-batch.json"
PORT = int(os.environ.get("VIDEOS_UI_PORT", "8010"))
TIEMPOS_FILE = P["work"] / "tiempos-render.json"
NEWLINE = chr(10)


# ------------------------------------------------- UNA sola instancia del panel
def _panel_ya_abierto() -> bool:
    """True si YA hay un panel escuchando en el puerto."""
    s = socket.socket()
    s.settimeout(0.7)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


# Abrir DOS paneles a la vez era la causa de "a veces me detecta, a veces no":
# el segundo lee webui_jobs.json, marca como "Interrumpido" el vídeo que el
# primero esta procesando, borra sus banderas y arranca un segundo Publisher
# (que podia subir el mismo vídeo dos veces). Todo eso pasaba ANTES de fallar al
# coger el puerto. Ahora se corta aqui, antes de tocar nada.
if __name__ == "__main__" and _panel_ya_abierto():
    print("")
    print(f"  YA HAY UN PANEL ABIERTO  ->  http://127.0.0.1:{PORT}")
    print("  No abro otro: dos paneles se pisan el estado de los trabajos.")
    print("  Usa la ventana que ya tienes abierta (o cierrala antes de abrir esta).")
    print("")
    time.sleep(6)
    raise SystemExit(0)


# ---------------------------------------- tiempos APRENDIDOS de cada fase
def _tiempos_cargar() -> dict:
    try:
        return json.loads(TIEMPOS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tiempo_medio(clave: str, defecto: float) -> float:
    v = (_tiempos_cargar().get(clave) or {})
    try:
        return float(v.get("media") or defecto)
    except (TypeError, ValueError):
        return defecto


def _tiempo_registrar(clave: str, segs: float) -> None:
    """Media movil: cada trabajo afina la estimacion del siguiente."""
    if segs <= 0:
        return
    d = _tiempos_cargar()
    v = d.get(clave) or {}
    try:
        prev, n = float(v.get("media") or 0), int(v.get("n") or 0)
    except (TypeError, ValueError):
        prev, n = 0.0, 0
    media = segs if n == 0 else prev * 0.7 + segs * 0.3
    d[clave] = {"media": round(media, 2), "n": n + 1}
    try:
        TIEMPOS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    except OSError:
        pass


def _pid_vivo(pid: int) -> bool:
    """El proceso sigue corriendo? (Windows, sin dependencias extra)."""
    if not pid:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    except Exception:                                   # noqa: BLE001
        return False

# Werkzeug escribe UNA LINEA POR PETICION. El panel consulta cada 5 s (jobs),
# cada 10 s (subidas) y cada 45 s (historial) -> ~20 lineas/minuto sin parar.
# Ese chorro constante a la consola era el que se bloqueaba si hacias clic
# dentro de la ventana (modo QuickEdit de Windows) y congelaba TODO el panel.
# Se silencian las peticiones normales; los errores 4xx/5xx si se ven.
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
# Subir un tutorial grabado (pantalla+cámara) facil pasa de 1-2 GB; sin este
# limite generoso Flask corta la subida a medias con un 413 feo.
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024 * 1024


@app.errorhandler(RequestEntityTooLarge)
def _archivo_muy_grande(_exc):
    limite = app.config.get("MAX_CONTENT_LENGTH") or 0
    return jsonify({"ok": False,
                    "error": f"El archivo pesa mas de {limite / 1024**3:.0f} GB, "
                             "el limite actual. Si de verdad lo necesitas, sube "
                             "MAX_CONTENT_LENGTH en webapp.py."}), 413
app.jinja_env.auto_reload = True


@app.context_processor
def _ui_globales():
    """Versión (fecha) de la hoja de estilos del panel: el navegador nunca se queda con una vieja."""
    try:
        v = int((Path(app.static_folder) / "panel.css").stat().st_mtime)
    except OSError:
        v = 0
    return {"ui_v": v}


@app.errorhandler(404)
def _no_encontrado(_exc):
    """Una página que no existe: la API responde JSON, y el resto una pantalla amable (no el error pelado)."""
    if request.path.startswith(("/api/", "/job/", "/salida/", "/static/", "/fonts/")) and \
            request.headers.get("Accept", "").find("text/html") < 0:
        return jsonify({"ok": False, "error": "no encontrado"}), 404
    return render_template("error.html"), 404


@app.get("/fonts/<path:nombre>")
def fuente_panel(nombre: str):
    """Rubik (la fuente de la marca) desde la carpeta fonts/ del proyecto: el panel se ve igual sin internet."""
    if not nombre.lower().endswith(".ttf"):
        abort(404)
    return send_from_directory(config.ROOT / "fonts", nombre, conditional=True)


# --- protección con contraseña (solo si se expone fuera de este PC) -----------
# PANEL_PASS en el entorno -> se exige usuario/clave HTTP Basic a TODO lo que NO
# venga de localhost. El panel puede subir a tu YouTube y borrar ficheros, así
# que NUNCA lo publiques sin esto. El túnel (panel-siempre) la genera solo.
_PANEL_PASS = os.environ.get("PANEL_PASS", "").strip()
_PANEL_USER = os.environ.get("PANEL_USER", "jose").strip() or "jose"


@app.before_request
def _gate():
    if not _PANEL_PASS:
        return None                                   # sin clave configurada: abierto (uso local)
    # cloudflared/proxy conecta desde localhost pero añade estas cabeceras;
    # si vienen, NO es acceso directo desde el PC -> se exige clave.
    proxied = bool(request.headers.get("CF-Connecting-IP")
                   or request.headers.get("X-Forwarded-For")
                   or request.headers.get("CF-Ray"))
    if not proxied and request.remote_addr in ("127.0.0.1", "::1"):
        return None                                   # acceso directo desde el propio PC
    auth = request.authorization
    if auth and auth.username == _PANEL_USER and auth.password == _PANEL_PASS:
        return None
    return Response("Panel privado.", 401,
                    {"WWW-Authenticate": 'Basic realm="video-edit-pro"'})


# ============================================================ modelo de trabajo
@dataclass
class Job:
    jid: str
    source: str
    label: str
    opts: dict
    state: str = "queued"           # queued | running | done | error | cancelled
    stage: str = "En cola…"
    pct: int = 0
    clips_total: int = 0            # nº de vídeos (modo "Generador"; PRO es siempre 1)
    clips_done: int = 0
    n_clips_result: int = 0
    created: float = field(default_factory=time.time)
    started: float | None = None
    render_started: float | None = None   # cuando arranca el 1er render
    source_duration_s: float = 0.0        # duración del vídeo FUENTE (Vídeo PRO): la barra y el ETA se escalan con esto
    render_pct_ffmpeg: int = 0            # el -progress REAL de ffmpeg (0-100) del render en curso, para extrapolar el ETA
    eta_seconds: int = 0                   # estimado de lo que falta (0 = sin dato)
    clips_fin: float | None = None        # cuando acabo el ultimo vídeo suelto
    fase: str = ""                        # clips | fin
    pid: int = 0                          # pid de run_video*.py (para re-enganchar)
    finished: float | None = None
    returncode: int | None = None
    error: str | None = None

    @property
    def log_path(self) -> Path:
        return P["work"] / self.jid / "webui.log"

    @property
    def out_dir(self) -> Path:
        return P["outbox"] / self.jid

    def _refresca_nota(self) -> None:
        """«Terminado (las miniaturas B y C siguen haciéndose)» era una FOTO del momento en que salió el vídeo y nadie la actualizaba: el autor veía la
        tarjeta así con las miniaturas ya hechas (11-oct-2026). Cada vez que se enseña un trabajo PRO ya terminado con una nota, se recalcula con
        lo que hay AHORA (`_nota_miniaturas`); sin nota (o con el estado ya definitivo) no cuesta nada."""
        if self.state != "done" or "--pro" not in self.jid or not str(self.stage or "").startswith("Terminado ("):
            return
        try:
            nota = _nota_miniaturas(self.jid)
        except Exception:                                           # noqa: BLE001 — la nota es un adorno: jamás rompe el panel
            return
        self.stage = "Terminado" + (f" ({nota})" if nota else "")

    def to_public(self) -> dict:
        self._refresca_nota()
        d = asdict(self)
        d["has_output"] = _salida_ok(self.out_dir, bool(self.opts.get("solo_analisis")))
        d["analisis"] = _lee_analisis(self.out_dir)
        d["elapsed"] = round((self.finished or time.time()) - (self.started or self.created))
        d["eta_seconds"] = self.eta()
        d["eta_texto"] = _fmt_eta(d["eta_seconds"])
        nombre = _nombre_video(self.jid) if "--pro" in self.jid else ""
        d["nombre"] = nombre + _sufijo_version(self.label) if nombre else ""
        return d

    # ---------------------------------------------------------------- ETA
    def coste_clip(self) -> float:
        """Segundos que cuesta UN vídeo suelto (modo "Generador")."""
        if self.render_started and self.clips_done:
            return (time.time() - self.render_started) / self.clips_done
        return _tiempo_medio("clip", 55.0)

    def eta(self) -> int:
        if self.state not in ("running", "paused"):
            return 0
        if self.opts.get("modo") == "video_pro":
            return self._eta_pro()
        c = self.coste_clip()
        falta = 0.0
        if self.clips_total:
            falta += max(0, self.clips_total - self.clips_done) * c
        else:
            # aún no se sabe cuántos vídeos saldrán: se usa lo APRENDIDO de trabajos anteriores.
            falta += _tiempo_medio("cola", max(120.0, c * 0.35))
        return int(max(0, falta))

    def _eta_pro(self) -> int:
        """Vídeo PRO (siempre una sola pieza, sin "clips"): mientras RENDERIZA, extrapola del
        avance REAL de ffmpeg (`render_pct_ffmpeg`) — se afina solo cuanto más avanza, igual que
        cualquier barra de progreso con datos de verdad. Antes de eso (descarga, transcripción,
        el director de Gemini...) usa una estimación aprendida de trabajos anteriores: segundos
        de render por segundo de vídeo FUENTE, más lo que suele tardar llegar hasta ahí. Esa
        parte de antes del render es inevitablemente aproximada si Gemini está saturado — eso no
        se puede predecir, ver `call_gemini_cadena` — pero ya no es la estática "2 minutos" fija
        de antes, que el autor pilló (2026-09-23): nunca se actualizaba para Vídeo PRO."""
        ahora = time.time()
        if self.render_started and self.render_pct_ffmpeg > 0:
            transcurrido = ahora - self.render_started
            total_est = transcurrido / (self.render_pct_ffmpeg / 100)
            return int(max(0, total_est - transcurrido))
        ratio = _tiempo_medio("pro_render_ratio", 1.0)
        margen = _tiempo_medio("pro_analisis_seg", 90.0)
        total_est = margen + self.source_duration_s * ratio
        transcurrido = ahora - (self.started or self.created)
        return int(max(0, total_est - transcurrido))


def _fmt_eta(secs: int) -> str:
    """segundos -> '3m 20s' / '45s' / '1h 05m'. 0 o negativo -> ''."""
    secs = int(secs or 0)
    if secs <= 0:
        return ""
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


# ------------------------------------------------------------ parseo de etapas
# Vídeo PRO: el RENDER (el ffmpeg largo, del tamaño de la duración del vídeo fuente) es con
# diferencia lo que más tarda — antes estas etapas de ANTES del render ocupaban hasta 78 de
# los 100 puntos de la barra, dejando solo 80-98 para el render en sí (18 puntos para la parte
# que más tarda). el autor lo pilló (2026-09-23): la barra parecía "casi terminada" apenas arrancaba
# a renderizar. Ahora estas etapas van 0-19 y el render (ver `_apply_stage`) ocupa 20-99, en
# proporción a lo que de verdad tardan.
_STAGES: list[tuple[str, str, int]] = [
    ("Descargando con yt-dlp", "Descargando el vídeo…", 2),
    ("has already been downloaded", "Descarga lista (estaba en caché)", 4),
    ("Usando archivo local", "Archivo local listo", 4),
    ("Descargado:", "Descarga lista", 4),
    ("Extrayendo audio", "Extrayendo audio…", 5),
    ("Transcripción encontrada en caché", "Transcripción (caché)", 12),
    ("Whisper: modelo", "Transcribiendo…", 6),
    ("Transcripción lista", "Transcripción lista", 12),
    ("VIDEOS LARGOS", "Arrancando el generador de vídeos…", 3),
    ("se parte en", "Leyendo el directo entero…", 62),
    ("temáticas:", "Detectando de qué hablas…", 66),
    ("Temáticas en caché", "Temáticas (caché)", 70),
    # «La IA ve el vídeo» (clipper/vision.py, opción de ⚙️ Opciones): antes del director
    ("La IA va a ver el vídeo", "👁 Preparando una copia pequeña de tu vídeo para que la IA la mire…", 12),
    ("la subo a Gemini", "👁 Subiendo la copia a Gemini…", 12),
    ("(Flash-Lite: no gasta", "👁 Gemini Flash-Lite está mirando tu vídeo…", 12),
    ("La IA vio el vídeo", "👁 La IA ya vio el vídeo", 13),
    ("La IA ya vio este vídeo antes", "👁 La IA ya lo había visto: reuso lo que vio", 13),
    ("la IA no pudo ver el vídeo", "La IA no pudo ver el vídeo: sigo solo con la transcripción", 13),
    ("PROGRESO director_inicio", "El director está viendo el vídeo (IA)…", 13),
    # plan de contingencia de Gemini (clipper/util.py: call_gemini_cadena) — puede tardar
    # bastante si hay que insistir en varios modelos saturados; no hay forma de predecir
    # CUÁNTO (ver `call_gemini_cadena`), así que la barra se queda aquí sin fingir precisión.
    ("no sirve ahora", "Un modelo de Gemini no sirve — probando el siguiente…", 13),
    ("Ningún modelo respondió", "Gemini saturado — esperando para reintentar…", 13),
    ("reintento automático", "Gemini saturado — lo reintento solo más tarde…", 13),
    (" saturado (", "Gemini saturado — probando otro modelo…", 13),
    ("PROGRESO director_fin", "Plan del director listo", 17),
    ("Tarjetas:", "Dibujando las tarjetas…", 18),
    ("Imágenes ilustrativas:", "Buscando imágenes ilustrativas…", 19),
    ("PROGRESO shorts_inicio", "🎬 Buscando Shorts en el vídeo…", 99),
    ("Shorts: montando", "🎬 Montando un Short…", 99),
    ("LISTO —", "Terminado", 100),
]

_RENDER_PCT_INI, _RENDER_PCT_FIN = 20, 99   # el hueco que le toca al render dentro de la barra


def _fin_clips(job: Job) -> None:
    """Marca el final de la tanda de vídeos sueltos y aprende cuanto costo cada uno."""
    if job.clips_fin or not job.render_started or not job.clips_done:
        return
    job.clips_fin = time.time()
    _tiempo_registrar("clip", (job.clips_fin - job.render_started) / job.clips_done)


def _stage_progreso(job: Job) -> None:
    """Texto + barra + ETA segun la FASE real."""
    job.eta_seconds = job.eta()
    faltan = _fmt_eta(job.eta_seconds)
    cola = f" · ~{faltan} para terminar" if faltan else ""
    if job.fase == "fin":
        return
    if job.clips_total:
        job.stage = f"Renderizando vídeos… ({job.clips_done}/{job.clips_total}){cola}"
        job.pct = min(88, 75 + int(13 * job.clips_done / job.clips_total))


def _apply_stage(job: Job, line: str) -> None:
    for needle, text, pct in _STAGES:
        if needle in line:
            job.stage = text
            job.pct = max(job.pct, pct)
            break

    # «Gemini sigue saturado en todos los modelos: reintento automático 1 de 3 a las 14:25…» (pipeline._con_reintento_auto)
    m_re = re.search(r"reintento automático (\d+) de (\d+) a las (\d{1,2}:\d{2})", line)
    if m_re:
        job.stage = (f"Gemini saturado: lo vuelvo a intentar solo a las {m_re.group(3)} "
                     f"({m_re.group(1)} de {m_re.group(2)}) — no hace falta que hagas nada")

    # "... transcribiendo, voy en 45 min"
    if "transcribiendo, voy en" in line:
        try:
            mins = int(line.split("voy en")[1].split("min")[0].strip())
            job.stage = f"Transcribiendo… (minuto {mins} del vídeo)"
        except (IndexError, ValueError):
            pass

    # "Vídeo: 2560x1440 @ 60fps — duración 00:23:11" (Vídeo PRO, justo tras el probe): la
    # duración del vídeo FUENTE es la base para escalar la barra y el ETA del render — ver
    # `Job._eta_pro` y el reparto 20-99% de más abajo.
    m = re.search(r"duraci[oó]n (\d+):(\d{2}):(\d{2})", line)
    if m:
        h, mi, s = (int(x) for x in m.groups())
        job.source_duration_s = h * 3600 + mi * 60 + s

    # ---- marcadores de fase que emite el pipeline ----
    if "PROGRESO videos_total=" in line:
        try:
            job.clips_total = int(line.split("PROGRESO videos_total=")[1].split()[0])
        except (IndexError, ValueError):
            pass
        job.render_started = job.render_started or time.time()
        job.fase = "clips"
    elif "PROGRESO render_pct=" in line:
        # Vídeo PRO: un solo ffmpeg de varios minutos (a menudo el tramo que MÁS tarda de
        # todo el trabajo — del orden de la duración del vídeo fuente) sin esto se quedaba
        # CLAVADO en "Plan del director listo" hasta terminar — parecía colgado aunque
        # siguiera trabajando. Esto reporta el avance real del propio ffmpeg (-progress),
        # mapeado a _RENDER_PCT_INI-_RENDER_PCT_FIN (el hueco grande que le toca: es la
        # etapa más larga, no unos pocos puntos al final — el autor lo pilló, 2026-09-23: la
        # barra parecía "casi terminada" apenas arrancaba el render de verdad).
        job.render_started = job.render_started or time.time()
        try:
            p = int(line.split("PROGRESO render_pct=")[1].split()[0])
            job.render_pct_ffmpeg = p
            job.pct = max(job.pct, _RENDER_PCT_INI + int(p * (_RENDER_PCT_FIN - _RENDER_PCT_INI) / 100))
            job.stage = f"Renderizando el vídeo… ({p}%)"
        except (IndexError, ValueError):
            pass

    stripped = line.strip()
    if stripped.startswith("[vid ") and "]" in stripped:
        try:
            k = int(stripped.split("[vid")[1].split("]")[0].strip())
            job.clips_done = max(job.clips_done, k)
        except (IndexError, ValueError):
            job.clips_done += 1
        job.fase = "clips"

    # fin de TODO: se aprenden los tiempos para que el proximo trabajo acierte
    if line.startswith("LISTO —"):
        _fin_clips(job)
        if job.clips_fin:
            _tiempo_registrar("cola", max(0.0, time.time() - job.clips_fin))
        elif job.opts.get("modo") == "video_pro" and job.render_started and job.source_duration_s > 0:
            # Vídeo PRO: segundos de render por segundo de vídeo FUENTE, y lo que tardó
            # todo lo de ANTES del render (descarga/transcripción/el director...) — con
            # esto, el próximo Vídeo PRO ya arranca con un ETA de verdad en vez del fijo
            # "2 minutos" de siempre que el autor pilló (nunca se actualizaba para este modo).
            ahora = time.time()
            _tiempo_registrar("pro_render_ratio", (ahora - job.render_started) / job.source_duration_s)
            _tiempo_registrar("pro_analisis_seg", max(0.0, job.render_started - (job.started or job.created)))
        job.fase = "fin"

    _stage_progreso(job)

    low = line.lower()
    if line.startswith("ERROR:") or "error:" in low and "loglevel" not in low:
        job.error = line.split("ERROR:", 1)[-1].strip() or line.strip()


def prueba() -> list[dict]:
    """Sin Flask, sin tocar los tiempos aprendidos de verdad: la barra de Vídeo PRO debe
    escalar con el render de VERDAD (la etapa que más tarda) y no quedarse "casi terminada"
    nada más arrancar el encode — y el ETA debe salir de datos reales, no de un "2 minutos"
    fijo que nunca cambiaba — el autor lo pilló (2026-09-23)."""
    global TIEMPOS_FILE
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": detalle})

    def job_pro(**kw) -> Job:
        base = dict(jid="zz-prueba--pro", source="x.mkv", label="x", opts={"modo": "video_pro"},
                   state="running", started=time.time())
        base.update(kw)
        return Job(**base)

    # 🎨 las miniaturas con IA arrancan en cuanto el log dice «miniaturas_listas» (el vídeo ya está), NO al acabar los Shorts (que dependen de
    # Gemini y pueden tardar minutos): una sola vez, y nunca al re-engancharse a un trabajo viejo ni en «solo analizar» (8-oct-2026)
    _real_arranca = globals()["_miniaturas_ia_arranca"]
    _viejo_work, _viejo_save = P["work"], MGR._save
    MGR._save = lambda *a, **k: None                 # (que la prueba no guarde la lista REAL de trabajos)
    try:
        with tempfile.TemporaryDirectory() as tmp0:
            P["work"] = Path(tmp0)
            llamadas: list[str] = []
            globals()["_miniaturas_ia_arranca"] = lambda jid, *a, **k: (llamadas.append(jid), (True, ""))[1]
            for caso, extra, jid_, espera in (("al decirlo el log", {}, "zz-mini-a--pro", 1), ("un re-enganche no las repite", {"_replay": True}, "zz-mini-b--pro", 0),
                                              ("«solo analizar» no genera miniaturas", {"solo": True}, "zz-mini-c--pro", 0)):
                llamadas.clear()
                (P["work"] / jid_).mkdir(parents=True)
                (P["work"] / jid_ / "webui.log").write_text("[10:00:00] render\nPROGRESO miniaturas_listas\nPROGRESO shorts_inicio\n", encoding="utf-8")
                j0 = job_pro(jid=jid_, opts={"modo": "video_pro", **({"solo_analisis": True} if extra.get("solo") else {})})
                if extra.get("_replay"):
                    j0._replay = True
                MGR._seguir_log(j0, lambda: False)
                r(f"las miniaturas con IA: {caso}", len(llamadas) == espera, f"llamadas={llamadas}")
    finally:
        globals()["_miniaturas_ia_arranca"] = _real_arranca
        P["work"], MGR._save = _viejo_work, _viejo_save

    # 🎨 «Terminado» = el vídeo + AL MENOS UNA miniatura (11-oct-2026; el 9-oct eran las 3): sale en cuanto hay una, las otras siguen aparte y un Cloudflare caído no retiene el vídeo
    from clipper import miniaturas_ia as _mia
    _d, _l, _a, _c, _v = _mia.disponible, _mia.lee, globals()["_miniaturas_ia_arranca"], _mia.cloudflare_caido, _mia.vigentes
    try:
        hechas = {"A": True, "B": True, "C": False}
        _mia.disponible = lambda cfg=None: (True, "")
        _mia.lee = lambda jid, *a, **k: {"items": {l: {"ok": ok} for l, ok in hechas.items()}}
        _mia.vigentes = lambda jid, *a, **k: {}                      # (todas del concepto de ahora)
        _mia.cloudflare_caido = lambda *a, **k: False
        globals()["_miniaturas_ia_arranca"] = lambda jid, *a, **k: (True, "")

        def hilo_vivo(seg: float) -> None:
            _MINIS["hilos"]["zz-espera--pro"] = threading.Thread(target=lambda: time.sleep(seg))
            _MINIS["hilos"]["zz-espera--pro"].start()
        hilo_vivo(2.0)
        j1 = job_pro(jid="zz-espera--pro")
        t0 = time.time()
        nota = MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1)
        r("con una miniatura ya hecha el vídeo NO espera al resto: sale ya y dice que la que falta sigue haciéndose",
          time.time() - t0 < 0.5 and nota == "la miniatura C sigue haciéndose", nota)
        hechas.update(A=False, B=False, C=False)
        threading.Timer(0.4, lambda: hechas.update(A=True)).start()
        t0 = time.time()
        nota2 = MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1)
        r("sin ninguna, espera SOLO a la primera: en cuanto sale una el vídeo sale y las otras siguen aparte",
          0.3 < time.time() - t0 < 1.4 and nota2 == "las miniaturas B y C siguen haciéndose", (nota2, time.time() - t0))
        hechas.update(A=True, B=True, C=True)
        r("con las 3 miniaturas hechas no hay nada que decir", MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1) == "")
        _mia.disponible = lambda cfg=None: (False, "sin Cloudflare")
        t0 = time.time()
        r("sin Cloudflare configurado no espera nada", MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1) == "" and time.time() - t0 < 1)
        _mia.disponible = lambda cfg=None: (True, "")
        hechas.update(A=False, B=False, C=False)
        hilo_vivo(4.0)
        t0 = time.time()
        nota3 = MGR._espera_miniaturas(j1, tope_s=0.5, cada_s=0.1)
        r("si ninguna llega en el tope no se queda colgado: sale igual y dice que se siguen haciendo aparte",
          0.4 < time.time() - t0 < 2.0 and nota3 == "las miniaturas con IA se siguen haciendo aparte (Cloudflare tarda)", (nota3, time.time() - t0))
        _mia.cloudflare_caido = lambda *a, **k: True
        t0 = time.time()
        nota4 = MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1)
        r("con Cloudflare CAÍDO (falló del todo hace poco) el vídeo ni espera: sale al momento, y las miniaturas se siguen intentando aparte",
          time.time() - t0 < 0.5 and nota4 == "las miniaturas con IA se siguen haciendo aparte (Cloudflare falló hace poco)", (nota4, time.time() - t0))
        _mia.cloudflare_caido = lambda *a, **k: False
        hilo_vivo(0.01)
        time.sleep(0.1)
        t0 = time.time()
        nota5 = MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1)
        r("si el hilo acabó sin ninguna (Cloudflare no respondió ni tras el reintento), sale al momento diciéndolo",
          time.time() - t0 < 0.5 and nota5 == "sin miniaturas con IA: Cloudflare no respondió", nota5)
        hechas.update(A=True, B=False, C=False)
        _mia.vigentes = lambda jid, *a, **k: {"A": False}             # la A es de un concepto anterior (tras el «↻» de su prompt): no cuenta
        r("una miniatura de un concepto anterior no cuenta como «hecha»", MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1) == "sin miniaturas con IA: Cloudflare no respondió")
        _mia.vigentes = lambda jid, *a, **k: {}
        hilo_vivo(3.0)
        hechas.update(A=False)
        j1.state = "cancelled"
        t0 = time.time()
        r("si cancelas mientras espera, deja de esperar", MGR._espera_miniaturas(j1, tope_s=10, cada_s=0.1) == "" and time.time() - t0 < 0.5)
        r("«las miniaturas B y C» / «la miniatura B»", _cuales(["B", "C"]) == "las miniaturas B y C" and _cuales(["B"]) == "la miniatura B"
          and _cuales(["A", "B", "C"]) == "las miniaturas A, B y C")

        # 🔄 la nota de «Terminado (…)» se pone al día sola (el autor, 11-oct-2026: la tarjeta seguía en «las miniaturas B y C siguen haciéndose» con las 3 hechas)
        hechas.update(A=True, B=False, C=False)
        hilo_vivo(3.0)
        r("la nota EN VIVO: con la A hecha y el hilo vivo, «las miniaturas B y C siguen haciéndose»",
          _nota_miniaturas("zz-espera--pro") == "las miniaturas B y C siguen haciéndose", _nota_miniaturas("zz-espera--pro"))
        j2 = job_pro(jid="zz-espera--pro")
        j2.state, j2.stage = "done", "Terminado (las miniaturas B y C siguen haciéndose)"
        r("…y una tarjeta con esa nota sigue igual mientras es verdad", j2.to_public()["stage"] == "Terminado (las miniaturas B y C siguen haciéndose)", j2.stage)
        hechas.update(A=True, B=True, C=True)
        r("…en cuanto están las 3 hechas la tarjeta pasa sola a «Terminado», sin la nota", j2.to_public()["stage"] == "Terminado" and j2.stage == "Terminado", j2.stage)
        hechas.update(A=True, B=True, C=False)
        _MINIS["hilos"].pop("zz-espera--pro", None)               # (el hilo ya no existe: p. ej. tras reiniciar el panel)
        j2.stage = "Terminado (la miniatura C sigue haciéndose)"
        r("…y si el hilo ya no existe y falta una (panel reiniciado), dice «sin la miniatura C» en vez de prometer que sigue",
          j2.to_public()["stage"] == "Terminado (sin la miniatura C)", j2.stage)
        j3 = job_pro(jid="zz-espera--pro")
        j3.state, j3.stage = "done", "Terminado"
        _mia.lee = lambda jid, *a, **k: (_ for _ in ()).throw(RuntimeError("no debería mirar las miniaturas"))
        r("un «Terminado» sin nota no cuesta nada (no mira las miniaturas)", j3.to_public()["stage"] == "Terminado")
        j4 = job_pro(jid="zz-espera--pro")
        j4.state, j4.stage = "done", "Terminado (la miniatura C sigue haciéndose)"
        r("y si mirar las miniaturas falla, la tarjeta se queda como estaba (la nota es un adorno)",
          j4.to_public()["stage"] == "Terminado (la miniatura C sigue haciéndose)")
    finally:
        _mia.disponible, _mia.lee, globals()["_miniaturas_ia_arranca"], _mia.cloudflare_caido, _mia.vigentes = _d, _l, _a, _c, _v
        _MINIS["hilos"].pop("zz-espera--pro", None)

    # 🎨 imagen destacada con IA (11-oct-2026): las rutas
    from clipper import imagen_articulo as _ia
    _ge, _el = _ia.genera, _ia.elegir
    try:
        pasado = {}
        _ia.genera = lambda jid, cfg, nueva, **k: (pasado.update(jid=jid, nueva=nueva) or {"ok": True, "estado": {"aviso": ""}})
        cli2 = app.test_client()
        rr = cli2.post("/api/articulo/zz-hero--pro/hero/generar", json={"nueva_idea": True})
        for _ in range(50):
            h_ = _HEROES["hilos"].get("zz-hero--pro")
            if h_ is None or not h_.is_alive():
                break
            time.sleep(0.05)
        r("la ruta de dibujar arranca el hilo y pasa «nueva idea» al módulo", rr.status_code == 200 and rr.get_json()["ok"] and pasado.get("nueva") is True and pasado.get("jid") == "zz-hero--pro", (rr.status_code, pasado))
        _ia.elegir = lambda jid, archivo: archivo == "hero-1.jpg"
        r("elegir la destacada: acepta hero-N.jpg, rechaza nombres raros (400) y una imagen que ya no existe (404)",
          cli2.post("/api/articulo/zz-hero--pro/hero/elegir", json={"archivo": "hero-1.jpg"}).status_code == 200
          and cli2.post("/api/articulo/zz-hero--pro/hero/elegir", json={"archivo": "../../x.jpg"}).status_code == 400
          and cli2.post("/api/articulo/zz-hero--pro/hero/elegir", json={"archivo": "hero-7.jpg"}).status_code == 404)
    finally:
        _ia.genera, _ia.elegir = _ge, _el
        _HEROES["hilos"].pop("zz-hero--pro", None)
        _HEROES["paso"].pop("zz-hero--pro", None)
        _HEROES["aviso"].pop("zz-hero--pro", None)
        _HEROES["error"].pop("zz-hero--pro", None)

    # 📤 «Mandar a tu web» (11-oct-2026): las rutas del artículo hacia tu proyecto de Astro (el módulo se prueba aparte, en clipper/sitio_astro.py)
    from clipper import sitio_astro as _sa
    _ex, _ab, _inf = _sa.exporta, _sa.abre, _sa.info
    try:
        visto = {}
        _sa.exporta = lambda jid, cfg, canal, **k: (visto.update(k, jid=jid, canal=canal) or {"ok": True, "relativo": "src/content/blog/google/x.md", "borrador": True, "imagenes": [], "video": False})
        _sa.info = lambda cfg, canal, jid="", dir_outbox=None: {"configurado": True, "categorias": ["google"], "categoria_defecto": "google", "borrador": True, "carpeta": "X"}
        cli = app.test_client()
        rr = cli.post("/api/articulo/zz-sitio--pro/sitio", json={"categoria": "video", "borrador": False, "slug": "hola"})
        r("la ruta «Mandar a tu web» pasa categoría, borrador y slug al módulo y devuelve el resultado con el estado",
          rr.status_code == 200 and rr.get_json()["ok"] and visto.get("categoria") == "video" and visto.get("borrador") is False and visto.get("slug") == "hola"
          and rr.get_json()["sitio"]["configurado"], (rr.status_code, visto))
        _sa.exporta = lambda *a, **k: {"ok": False, "error": "Ya hay un artículo con ese slug"}
        r("…si el módulo se niega (p. ej. el slug ya existe) responde 400 con el motivo", cli.post("/api/articulo/zz-sitio--pro/sitio", json={}).status_code == 400)
        r("…un id de vídeo con rutas raras no llega al módulo (404)", cli.post("/api/articulo/..%2Fetc/sitio", json={}).status_code == 404)
        _sa.abre = lambda jid, *a, **k: {"ok": True, "con": "Visual Studio Code"}
        r("«Abrir en Visual Studio Code» responde 200", cli.post("/api/articulo/zz-sitio--pro/sitio/abrir").status_code == 200)
        _sa.abre = lambda jid, *a, **k: {"ok": False, "error": "no"}
        r("…y 400 si no hay nada que abrir", cli.post("/api/articulo/zz-sitio--pro/sitio/abrir").status_code == 400)
    finally:
        _sa.exporta, _sa.abre, _sa.info = _ex, _ab, _inf

    # 🎨 Tools → Miniaturas (8-oct-2026): el menú «Tools» (Ideas, Descripciones, Miniaturas, en ese orden), la página y sus rutas; nada se genera de verdad
    with app.test_client() as cli:
        html_u = cli.get("/uso").get_data(as_text=True)
        pag_m = cli.get("/miniaturas")
        api_m = cli.get("/api/miniaturas-sueltas").get_json()
        corto = cli.post("/api/miniaturas-sueltas/generar", json={"texto": "hola"})
        raro = cli.get("/miniaturas-sueltas/..%2Festado.jpg")
        sin_imagen = cli.get("/miniaturas-sueltas/m-no-existe.jpg")
    barra = html_u[html_u.find('<nav class="nav"'):html_u.find("</nav>")]              # (solo la barra de arriba: la página «Uso» tiene sus propios botones hacia Descripciones)
    with app.test_client() as cli_a:
        pag_a = cli_a.get("/miniaturas-articulos")
        api_a = cli_a.get("/api/articulos-sueltos").get_json()
        corto_a = cli_a.post("/api/articulos-sueltos/generar", json={"texto": "hola"})
        raro_a = cli_a.get("/articulos-sueltos/..%2Fx/hero-1.jpg")
        nada_a = cli_a.get("/articulos-sueltos/a-20260101-000000-abcdef/hero-1.jpg")
        sitio_a = cli_a.get("/api/articulos-sueltos/sitio").get_json()
        raro_s = cli_a.get("/api/articulos-sueltos/sitio/google/..%2F..%2Fx")
    pestanas = [x in pag_a.get_data(as_text=True) for x in ('href="/miniaturas"', 'href="/miniaturas-articulos"', "Imágenes destacadas")]
    r("Tools → Miniaturas → «Imágenes destacadas» (artículos): la página (con sus pestañas), su API (items, cupo, límites), un texto corto se rechaza, rutas raras y sin imagen dan 404 y la lista de tus artículos de la web responde",
      pag_a.status_code == 200 and all(pestanas) and isinstance(api_a.get("items"), list) and "cupo" in api_a and api_a.get("max_texto", 0) >= 3000
      and corto_a.status_code == 400 and raro_a.status_code == 404 and nada_a.status_code == 404 and "articulos" in sitio_a and raro_s.status_code == 404,
      (pag_a.status_code, pestanas, list(api_a)[:6], corto_a.status_code, raro_a.status_code, nada_a.status_code, raro_s.status_code))
    with app.test_client() as cli_c:
        api_c = cli_c.get("/api/cuotas").get_json()
        pag_c = cli_c.get("/uso").get_data(as_text=True)
    ids_c = [m.get("id") for m in api_c.get("medidores", [])]
    r("La franja «Te queda hoy» (en la cabecera de todas las páginas) y su API: Flash, Flash-Lite, Cloudflare y YouTube, cada uno con lo que queda, el total, su estado y el texto con la hora de reinicio",
      pag_c.count('id="cuotas"') == 1 and "cuotasPinta" in pag_c and ids_c[:2] == ["flash", "lite"] and "cf" in ids_c and "yt" in ids_c
      and all(m["total"] >= m["quedan"] >= 0 and m["estado"] in ("ok", "aviso", "mal") and "Se reinicia a las" in m["detalle"] for m in api_c["medidores"]), (ids_c, pag_c.count('id="cuotas"')))
    orden = [barra.find(x) for x in ('href="/ideas"', 'href="/miniaturas"')]
    r("el menú «Tools» (desplegable) trae Ideas y Miniaturas, en ese orden, y «Ideas» ya no está suelto en la barra",
      'id="nav-tools"' in barra and all(o > 0 for o in orden) and orden == sorted(orden) and barra.count('href="/ideas"') == 1
      and barra.find('id="nav-tools"') < orden[0], orden)
    r("la página de miniaturas carga con su cuadro de texto (con el dictado de siempre), las composiciones y el botón de generar",
      pag_m.status_code == 200 and all(x in pag_m.get_data(as_text=True) for x in ("mej-txt-miniatura", "dictadoToggle('miniatura')", "mini-generar", "mini-formatos")), pag_m.status_code)
    r("la API de miniaturas sueltas da las 3 composiciones, el cupo de 15 llamadas y la batería; un texto corto se rechaza sin empezar nada; un id raro o inexistente da 404",
      [f["id"] for f in api_m["formatos"]] == ["derecha", "centro", "minimalista"] and api_m["cupo"]["max"] == 15 and "bateria" in api_m["cupo"]
      and corto.status_code == 400 and raro.status_code == 404 and sin_imagen.status_code == 404, (api_m.get("cupo"), corto.status_code, raro.status_code))

    # 🎬 Shorts (9-oct-2026): título/tags guardados + subir a YouTube; las rutas solo leen, y subir rechaza lo raro SIN tocar YouTube
    from clipper import shorts_meta as _smeta
    _viejo_out_s = P["outbox"]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            P["outbox"] = Path(tmp)
            (Path(tmp) / "zz-shorts--pro").mkdir()
            (Path(tmp) / "zz-shorts--pro" / "short_1.mp4").write_bytes(b"x")
            _smeta.guarda(Path(tmp) / "zz-shorts--pro", {"short_1.mp4": {"yt": {"titulo": "T"}, "origen": "ia"}})
            with app.test_client() as cli:
                a_sh = cli.get("/api/shorts/zz-shorts--pro").get_json()
                raro_sh = cli.post("/api/shorts/zz-shorts--pro/subir", json={"archivo": "../x.mp4"})
                falta_sh = cli.post("/api/shorts/zz-shorts--pro/subir", json={"archivo": "short_7.mp4"})
                mal_id = cli.get("/api/shorts/..%2Fx")
        finally:
            P["outbox"] = _viejo_out_s
    r("las rutas de Shorts dan lo guardado y el coste; subir un Short que no existe o con ruta rara se rechaza sin llamar a YouTube",
      a_sh["metas"]["short_1.mp4"]["yt"]["titulo"] == "T" and a_sh["coste"] == 1600 and raro_sh.status_code == 404 and falta_sh.status_code == 404 and mal_id.status_code == 404,
      (a_sh, raro_sh.status_code, falta_sh.status_code))

    # 🎛️ el asistente de canal trae el catálogo de ediciones con sus valores de fábrica y los presets (10-oct-2026)
    with app.test_client() as cli:
        op_c = cli.get("/api/canales").get_json()
    ids_cat = [i["id"] for g in op_c.get("ediciones", []) for i in g["items"]]
    r("el asistente de canal recibe el catálogo de ediciones (con el valor de fábrica de cada una) y los presets",
      "shorts" in ids_cat and "estilo_grafica" in ids_cat and len(ids_cat) >= 20 and [p["id"] for p in op_c.get("presets", [])] == ["todo", "equilibrado", "esencial"],
      (len(ids_cat), [p.get("id") for p in op_c.get("presets", [])]))

    # 🔗 carruseles por enlace (10-oct-2026): la página, la lista, y un enlace que no es de YouTube se rechaza sin empezar nada
    with app.test_client() as cli:
        pag_c = cli.get("/carruseles")
        lis_c = cli.get("/api/carruseles").get_json()
        mal_c = cli.post("/api/carruseles/enlace", json={"url": "https://vimeo.com/123"})
        raro_c = cli.post("/api/carruseles/2026-10-09-x--pro/borrar")
    r("la página de carruseles por enlace carga, la lista responde, un enlace que no es de YouTube da 400 y no se puede borrar un vídeo PRO normal desde ahí",
      pag_c.status_code == 200 and "car-crear" in pag_c.get_data(as_text=True) and isinstance(lis_c.get("items"), list) and mal_c.status_code == 400 and raro_c.status_code == 404,
      (pag_c.status_code, mal_c.status_code, raro_c.status_code))
    # 📝 carruseles desde un artículo o un texto (13-oct-2026): la página trae la 2.ª pestaña; sin texto suficiente, o con el enlace de OTRO sitio, se rechaza sin preparar nada
    with app.test_client() as cli:
        pag_t = cli.get("/carruseles").get_data(as_text=True)
        corto_t = cli.post("/api/carruseles/texto", json={"texto": "Muy corto."})
        ajeno_t = cli.post("/api/carruseles/texto", json={"url": "https://otra-web.com/google/algo/"})
        raro_t = cli.get("/texto/texto-abc--pro/fuente/../../config.yaml")
        mal_f = cli.get("/texto/texto-abc--pro/fuente/x.jpg")
        no_t = cli.post("/api/carruseles/texto-abc--pro/borrar")
    r("carruseles desde un artículo o un texto: la página trae su pestaña y las 4 formas de elegir las imágenes; un texto corto o el enlace de otro sitio dan 400 (sin preparar nada) y las rutas de las imágenes del artículo son seguras",
      all(x in pag_t for x in ('id="panel-tx"', "tx-modos", 'value="simbolo"', "/api/carruseles/texto")) and corto_t.status_code == 400 and ajeno_t.status_code == 400
      and "tu web" in (ajeno_t.get_json() or {}).get("error", "") and raro_t.status_code == 404 and mal_f.status_code == 404 and no_t.status_code == 200,
      (corto_t.status_code, ajeno_t.status_code, raro_t.status_code, mal_f.status_code, no_t.status_code))

    # 💡 lo «Por grabar» no se repite abajo y lleva su etiqueta de tipo (el autor, 13-oct-2026)
    with app.test_client() as cli:
        pag_id = cli.get("/ideas").get_data(as_text=True)
    r("💡 «Por grabar» no se repite en la tanda ni en tus favoritas (sinGrabar), cada idea de arriba lleva su etiqueta de tipo, el recuento por tipo y un «↩ Quitar»",
      all(x in pag_id for x in ("sinGrabar(t.ideas)", "sinGrabar(d.semillas)", "resumenTipos(pg)", "tipoNombre(i.tipo)", "↩ Quitar")), len(pag_id))
    with app.test_client() as cli:
        j_pr = cli.get("/api/ideas").get_json()
    r("💡 «Dame ideas» enseña una BARRA de progreso (no solo «1-2 minutos»): la página la pinta con las etapas y la API da cuándo empezó y desde cuándo está en el paso",
      all(x in pag_id for x in ("barraIdeas", "ideas-pbar", "pctIdeas")) and "inicio" in j_pr and "paso_ts" in j_pr and "ahora" in j_pr, sorted(j_pr)[:5])

    # 📣 Posts (13-oct-2026): la página, la API, el menú de Tools y que sin fuente válida no se empieza nada ni se sirven imágenes raras
    with app.test_client() as cli:
        pag_po = cli.get("/posts").get_data(as_text=True)
        api_po = cli.get("/api/posts").get_json()
        nada_po = cli.post("/api/posts/crear", json={})
        ajeno_po = cli.post("/api/posts/crear", json={"url": "https://otra-web.com/google/algo/"})
        yt_po = cli.post("/api/posts/crear", json={"youtube": "https://vimeo.com/1"})
        raro_po = cli.post("/api/posts/crear", json={"jid": "../x"})
        img_po = cli.get("/posts/no-existe--pro/p-20261013-000000-abcd/ig/post-01.jpg")
        pag_ba = cli.get("/carruseles").get_data(as_text=True)
    r("Tools → Posts: la página (con sus 4 fuentes), la API (lista, estado, vídeos tuyos), el menú de Tools la trae, y sin fuente válida (nada, enlace de otro sitio, enlace que no es de YouTube, jid raro) no se empieza nada; las imágenes raras dan 404",
      all(x in pag_po for x in ("pp-video", "pp-art", "pp-texto", "pp-yt", "posts.js")) and api_po.get("estado") in ("libre", "creando") and isinstance(api_po.get("items"), list) and isinstance(api_po.get("fuentes"), list)
      and nada_po.status_code == 400 and ajeno_po.status_code == 400 and yt_po.status_code == 400 and raro_po.status_code == 404 and img_po.status_code == 404 and 'href="/posts"' in pag_ba,
      (nada_po.status_code, ajeno_po.status_code, yt_po.status_code, raro_po.status_code, img_po.status_code))

    # 🎬 Shorts · TikTok (16-oct-2026): la página, la API, el menú de Tools (al final), y que sin vídeo válido / con ids raros no se empieza ni se sirve nada
    with app.test_client() as cli:
        pag_st = cli.get("/shorts-tiktok").get_data(as_text=True)
        api_st = cli.get("/api/shorts-tt").get_json()
        sin_st = cli.post("/api/shorts-tt/subir")
        mal_st = cli.post("/api/shorts-tt/subir", data={"video": (__import__("io").BytesIO(b"x"), "cosa.txt")}, content_type="multipart/form-data")
        raro_st = cli.get("/shorts-tt/st-00000000-000000-abcd/short.mp4")
        raro2_st = cli.post("/api/shorts-tt/../x/borrar")
        envia_st = cli.post("/api/shorts-tt/desde-video", json={"jid": "no-existe--pro", "archivo": "short_1.mp4"})
        envia2_st = cli.post("/api/shorts-tt/desde-video", json={"jid": "x", "archivo": "../../etc/passwd"})
        pag_ba2 = cli.get("/posts").get_data(as_text=True)
    r("Tools → Shorts · TikTok: la página (subir un vídeo / de un vídeo mío, barra de progreso), la API (lista, estado, canales, fuentes), el menú de Tools la trae y sin vídeo válido, con un formato raro, con un id raro o con un Short que no existe no se empieza nada",
      all(x in pag_st for x in ("stt-archivo", "stt-p-video", "shorts_tt.js", "stt-estado")) and api_st.get("estado") in ("libre", "creando") and isinstance(api_st.get("items"), list) and isinstance(api_st.get("canales"), list)
      and isinstance(api_st.get("fuentes"), list) and sin_st.status_code == 400 and mal_st.status_code == 400 and raro_st.status_code == 404 and raro2_st.status_code == 404 and envia_st.status_code == 404 and envia2_st.status_code == 404
      and 'href="/shorts-tiktok"' in pag_ba2 and pag_ba2.index('href="/shorts-tiktok"') > pag_ba2.index('href="/posts"'),
      (sin_st.status_code, mal_st.status_code, raro_st.status_code, raro2_st.status_code, envia_st.status_code, envia2_st.status_code))

    # el título que eliges («usar este título» o editándolo) se guarda también en el PLAN de esa versión: «Reintentar» rehace
    # el manifest desde el plan y, sin esto, volvía a poner el 🥇 de la IA (el autor, 2026-09-25)
    from clipper import director as _dir
    viejo_out, viejo_work = P["outbox"], P["work"]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            P["outbox"], P["work"] = Path(tmp) / "videos", Path(tmp) / "work"
            carpeta = P["outbox"] / "zz-prueba-titulo--pro-v2"
            carpeta.mkdir(parents=True)
            (carpeta / "pro.mp4").write_bytes(b"x")
            (carpeta / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "titulo": "El 1 de la IA"}]),
                                                   encoding="utf-8")
            (P["work"] / "zz-prueba-titulo--pro").mkdir(parents=True)
            _dir.guardar(P["work"] / "zz-prueba-titulo--pro", "pro-v2", {"titulos_sugeridos": ["El 1 de la IA", "B"]})
            with app.test_client() as cli:
                resp = cli.post("/job/zz-prueba-titulo--pro-v2/titulo", json={"archivo": "pro.mp4", "titulo": "El que elegí"})
            plan_t = _dir.cargar(P["work"] / "zz-prueba-titulo--pro", "pro-v2") or {}
            r("el título que eliges queda en el manifest Y en el plan de esa versión (así «Reintentar» lo conserva)",
              resp.status_code == 200 and plan_t.get("titulo_elegido") == "El que elegí"
              and json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))[0]["titulo"] == "El que elegí",
              (resp.status_code, plan_t))
        finally:
            P["outbox"], P["work"] = viejo_out, viejo_work

    # el 🗑 de «Mis vídeos» (el autor, 2026-09-26): todo a la PAPELERA (aquí una falsa: la de verdad no se toca), la grabación
    # solo si ningún otro vídeo la usa, el Historial solo guarda lo publicado, y nada si algo está en marcha
    from clipper import papelera as _pap
    viejo_inbox, real_pap, real_carga, real_elimina = P["inbox"], _pap.a_la_papelera, historial.carga, historial.elimina
    # sin esto, el borrado guardaba la lista de trabajos REAL (work/webui_jobs.json) con los de mentira dentro: pasó al
    # escribir esta prueba (2026-09-26) y dos «zz-…» salieron en «Mis vídeos» hasta quitarlos
    real_save = MGR._save
    MGR._save = lambda: None
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        mandadas: list = []
        eliminadas: list = []
        publicados: dict = {}
        falsos: list[str] = []
        try:
            P["outbox"], P["work"], P["inbox"] = t / "videos", t / "work", t / "inbox"
            P["inbox"].mkdir(parents=True)
            _pap.a_la_papelera = lambda rutas, _op=None: (mandadas.append(sorted(Path(r).name for r in rutas
                                                                                 if Path(r).exists())), (True, "", []))[1]
            historial.carga = lambda dir_work=None: publicados
            historial.elimina = lambda base, dir_work=None: eliminadas.append(base) or True
            grab = P["inbox"] / "zz-grab.mkv"
            grab.write_bytes(b"g" * 2048)
            compartida = P["inbox"] / "zz-compartida.mkv"
            compartida.write_bytes(b"c" * 10)
            for jid, src in (("zz-borra--pro", grab), ("zz-borra--pro-v2", grab), ("zz-otra--pro", compartida),
                             ("zz-comparte--pro", compartida), ("zz-marcha--pro", compartida)):
                (P["outbox"] / jid).mkdir(parents=True)
                (P["outbox"] / jid / "pro.mp4").write_bytes(b"v" * 100)
                (P["work"] / jid).mkdir(parents=True)
                MGR.jobs[jid] = Job(jid=jid, source=str(src), label=src.name, opts={"modo": "video_pro"},
                                    state="running" if jid == "zz-marcha--pro" else "done")
                falsos.append(jid)
            (P["work"] / "zz-borra--pro").mkdir(parents=True, exist_ok=True)
            with app.test_client() as cli:
                info = cli.get("/api/familia/zz-borra/borrado").get_json()
                resp = cli.post("/api/familia/zz-borra/borrar", json={"papelera": True}).get_json()       # (el ✖ borra del todo por defecto)
                resp_otra = cli.post("/api/familia/zz-otra/borrar", json={"papelera": True}).get_json()
                publicados["zz-comparte"] = {"youtube": {"video_id": "abc123"}}
                info_pub = cli.get("/api/familia/zz-comparte/borrado").get_json()
                resp_marcha = cli.post("/api/familia/zz-marcha/borrar")
                # uno PUBLICADO: su transcripción se guarda aparte antes de ir a la Papelera, y sus muletillas se siguen
                # viendo aunque su carpeta ya no esté (el autor, 2026-09-28)
                palabras = [{"word": w, "start": k * 0.35, "end": k * 0.35 + 0.3}
                            for k, w in enumerate(("bueno o sea esto es así " * 20).split())]
                (P["work"] / "zz-comparte--pro" / "words.json").write_text(json.dumps(palabras), encoding="utf-8")
                resp_pub = cli.post("/api/familia/zz-comparte/borrar", json={"papelera": True}).get_json()
                shutil.rmtree(P["work"] / "zz-comparte--pro")        # lo que haría la Papelera de verdad
                mul_pub = cli.get("/api/muletillas/zz-comparte").get_json()
                mul_nada = cli.get("/api/muletillas/zz-nunca").get_json()
            r("🗑: sus dos versiones, su caché y la grabación (que nadie más usa) van a la Papelera; sale de la lista y del "
              "Historial (no estaba publicado)",
              info.get("ok") and info.get("versiones") == 2 and info.get("grabacion") and not info.get("publicado")
              and resp.get("ok") and mandadas and mandadas[0] == sorted(["zz-borra--pro", "zz-borra--pro", "zz-borra--pro-v2",
                                                                          "zz-borra--pro-v2", "zz-grab.mkv"])
              and "zz-borra--pro" not in MGR.jobs and eliminadas[:1] == ["zz-borra"], (info, resp, mandadas, eliminadas))
            r("🗑: la grabación que usa OTRO vídeo (una subida repetida) no se toca",
              resp_otra.get("ok") and len(mandadas) > 1 and "zz-compartida.mkv" not in mandadas[1], mandadas)
            r("🗑: uno publicado dice que sigue en el Historial; uno en marcha no se deja borrar",
              info_pub.get("publicado") and resp_marcha.status_code == 409 and "zz-marcha--pro" in MGR.jobs,
              (info_pub, resp_marcha.status_code))
            r("🗑 de uno publicado: su transcripción se guarda aparte y «🗣️ Tus muletillas» sigue funcionando sin su carpeta; "
              "si de verdad no hay transcripción, lo dice claro (no «aún no tiene»)",
              resp_pub.get("ok") and (P["work"] / "historial_transcripciones" / "zz-comparte" / "words.json").is_file()
              and mul_pub.get("ok") and mul_pub.get("total", 0) > 0
              and not mul_nada.get("ok") and "se borró" in str(mul_nada.get("error")), (resp_pub, mul_pub.get("error"), mul_nada))
        finally:
            P["outbox"], P["work"], P["inbox"] = viejo_out, viejo_work, viejo_inbox
            _pap.a_la_papelera, historial.carga, historial.elimina = real_pap, real_carga, real_elimina
            for jid in falsos:
                MGR.jobs.pop(jid, None)
            MGR._save = real_save

    # 💾 Espacio y el ✖ completo (el autor, 2026-10-04): el ✖ borra DEL TODO todo lo de ese vídeo (versiones, Shorts, caché, la copia de la grabación) MENOS tus
    # grabaciones de OBS (D:/Videos: «esas déjalas ahí, son mi respaldo»); con «papelera» va a la Papelera. «💾 Espacio» mide, libera lo ya publicado y guarda sus
    # opciones; la limpieza automática solo actúa encendida y nunca toca un vídeo sin publicar ni OBS. Todo de mentira: Papelera, Historial, OBS y config.
    from clipper import camara_aparte as _cam, espacio as _esp
    reales_e = (historial.carga, historial.elimina, historial.lista, _pap.a_la_papelera, _cam.parametros, _cam.busca, _esp.guarda_opciones, _esp.opciones,
                MGR._save, (P["outbox"], P["work"], P["inbox"]))
    MGR._save = lambda: None
    falsos_e: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        bin_e: list = []
        elim_e: list = []
        guardadas: list = []
        filas_e: list = []
        ahora_e = time.time()
        try:
            P["outbox"], P["work"], P["inbox"] = t / "videos", t / "work", t / "inbox"
            P["inbox"].mkdir(parents=True)
            P["work"].mkdir(parents=True)
            obs_dir = t / "obs"
            obs_dir.mkdir()
            _pap.a_la_papelera = lambda rutas, _op=None: (bin_e.append(sorted(Path(x).name for x in rutas if Path(x).exists())), (True, "", []))[1]
            historial.carga = lambda dir_work=None: {f["base"]: f for f in filas_e}
            historial.elimina = lambda base, dir_work=None: elim_e.append(base) or True
            historial.lista = lambda *a, **k: filas_e
            _cam.parametros = lambda cfg=None: {"carpeta": str(obs_dir), "sufijo": "camara"}
            _cam.busca = lambda src, cfg=None, carpetas=None: next(iter(obs_dir.glob(f"{Path(src).stem.replace('_', ' ')}*camara*")), None)
            _esp.guarda_opciones = lambda auto, dias, ruta=None: guardadas.append((auto, dias)) or True

            def video(base: str, grab: str, publicado_hace=None, cuerpo: bytes = b"v" * 3000) -> None:
                jid = f"{base}--pro"
                (P["outbox"] / jid).mkdir(parents=True)
                (P["outbox"] / jid / "pro.mp4").write_bytes(b"v" * 500)
                (P["outbox"] / jid / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "titulo": base}]), encoding="utf-8")
                w = P["work"] / jid
                for nombre in ("escenas", "ilustraciones", "musica"):
                    (w / nombre).mkdir(parents=True)
                    (w / nombre / "a.bin").write_bytes(b"c" * 2000)
                    os.utime(w / nombre / "a.bin", (ahora_e - 7200, ahora_e - 7200))
                (w / "words.json").write_text("[]", encoding="utf-8")
                src = P["inbox"] / grab
                src.write_bytes(cuerpo)
                MGR.jobs[jid] = Job(jid=jid, source=str(src), label=grab, opts={"modo": "video_pro"}, state="done")
                falsos_e.append(jid)
                if publicado_hace is not None:
                    filas_e.append({"base": base, "titulo": base, "youtube": {"video_id": f"id_{base}", "publicado": ahora_e - publicado_hace * 86400},
                                    "estado_yt": "publicado", "fecha_yt": ahora_e - publicado_hace * 86400})

            cuerpo1, cuerpo2 = bytes(range(256)) * 300, bytes(range(255, -1, -1)) * 300
            for nombre, cuerpo in (("2026-10-03 10-00-00", cuerpo1), ("2026-10-03 11-00-00", cuerpo2)):
                (obs_dir / f"{nombre}.mkv").write_bytes(cuerpo)
                (obs_dir / f"{nombre} camara.mkv").write_bytes(b"c" * 4000)
            video("zz-o1", "2026-10-03_10-00-00.mkv", None, cuerpo1)
            video("zz-o2", "2026-10-03_11-00-00.mkv", None, cuerpo2)
            for k in (1, 2):                                      # lo que trae el vídeo y quizá aún no usaste: Shorts, artículo y publicaciones para redes
                (P["outbox"] / "zz-o1--pro" / f"short_{k}.mp4").write_bytes(b"s" * 100)
            (P["outbox"] / "zz-o1--pro" / "articulo").mkdir()
            (P["outbox"] / "zz-o1--pro" / "articulo" / "articulo.json").write_text("{}", encoding="utf-8")
            (P["outbox"] / "zz-o1--pro" / "redes.json").write_text("{}", encoding="utf-8")
            (P["outbox"] / "zz-o1--pro" / "carrusel").mkdir()
            (P["outbox"] / "zz-o1--pro" / "carrusel" / "carrusel.json").write_text("{}", encoding="utf-8")
            with app.test_client() as cli:
                info_o = cli.get("/api/familia/zz-o1/borrado").get_json()
                r_o1 = cli.post("/api/familia/zz-o1/borrar", json={"obs": True}).get_json()       # (una página vieja podía pedir las de OBS: se ignora)
                estado_o1 = (not (P["outbox"] / "zz-o1--pro").exists(), not (P["work"] / "zz-o1--pro").exists(),
                             not (P["inbox"] / "2026-10-03_10-00-00.mkv").exists(), "zz-o1--pro" not in MGR.jobs, len(bin_e) == 0,
                             all((obs_dir / n).is_file() for n in ("2026-10-03 10-00-00.mkv", "2026-10-03 10-00-00 camara.mkv",
                                                                   "2026-10-03 11-00-00.mkv", "2026-10-03 11-00-00 camara.mkv")))
                r_o2 = cli.post("/api/familia/zz-o2/borrar", json={"papelera": True}).get_json()
            r("✖: borra DEL TODO todo lo de ese vídeo (versiones, Shorts, caché y la copia de la grabación) y tus grabaciones de OBS se quedan donde están (el autor: «todo "
              "debería borrarse excepto las que están en mi disco D, Videos»); sin pasar por la Papelera",
              info_o.get("ok") and r_o1.get("ok") and r_o1["a_papelera"] is False and r_o1["papelera"] == 0 and r_o1["liberado"] > 6000 and all(estado_o1),
              (r_o1, estado_o1, bin_e))
            r("✖: el aviso nombra las grabaciones de OBS (para decir que se quedan), cuántas cachés se borran del todo y lo que trae el vídeo sin usar (2 Shorts, "
              "artículo y publicaciones para redes)",
              sorted(a["nombre"] for a in info_o["obs"]["archivos"]) == ["2026-10-03 10-00-00 camara.mkv", "2026-10-03 10-00-00.mkv"]
              and info_o["obs"]["bytes"] == len(cuerpo1) + 4000 and info_o["cache_bytes"] == 6000
              and info_o["extras"] == {"shorts": 2, "articulo": True, "redes": True, "carrusel": True}, info_o)
            r("✖ con «Papelera»: va a la Papelera de reciclaje (sus versiones y la copia de la grabación, NO las de OBS) y lo regenerable se borra de verdad igual",
              r_o2.get("ok") and r_o2["a_papelera"] is True and r_o2["papelera"] > 0 and len(bin_e) == 1 and "zz-o2--pro" in bin_e[0]
              and "2026-10-03_11-00-00.mkv" in bin_e[0] and not any(n.startswith("2026-10-03 11-00-00") for n in bin_e[0])
              and not (P["work"] / "zz-o2--pro" / "musica").exists() and (obs_dir / "2026-10-03 11-00-00.mkv").is_file(), (r_o2, bin_e))

            # si Windows no deja borrar algo (otro programa lo tiene abierto): lo dice claro y el vídeo sigue en la lista para volver a intentarlo
            video("zz-f1", "zz-f1.mkv", None)
            borra_real = _esp.borra_rutas
            _esp.borra_rutas = lambda rutas, permitidas: {"bytes": 0, "borradas": 0, "errores": ["pro.mp4: [WinError 32] lo está usando otro programa"]}
            with app.test_client() as cli:
                r_f = cli.post("/api/familia/zz-f1/borrar")
            _esp.borra_rutas = borra_real
            r("✖: si Windows no deja borrar algo (otro programa lo tiene abierto) lo dice claro, y el vídeo sigue en la lista para volver a intentarlo",
              r_f.status_code == 500 and "otro programa" in (r_f.get_json() or {}).get("error", "") and "zz-f1--pro" in MGR.jobs and "zz-f1" not in elim_e, r_f.status_code)

            # buscar en OBS cuesta unos ffprobe por vídeo: se hace UNA vez y se recuerda mientras no cambie nada (no una por cada visita a la página)
            (obs_dir / "2026-10-03 13-00-00.mkv").write_bytes(cuerpo1)
            (obs_dir / "2026-10-03 13-00-00 camara.mkv").write_bytes(b"c" * 4000)
            (P["inbox"] / "2026-10-03_13-00-00.mkv").write_bytes(cuerpo1)
            g_obs = [P["inbox"] / "2026-10-03_13-00-00.mkv"]
            busca_antes, busquedas = _cam.busca, []
            _cam.busca = lambda src, cfg=None, carpetas=None: busquedas.append(1) or busca_antes(src, cfg, carpetas)
            a1 = _obs_de_grabaciones(g_obs, set())
            a2 = _obs_de_grabaciones(g_obs, set())
            n_dos = len(busquedas)
            (obs_dir / "otra.mkv").write_bytes(b"x")
            os.utime(obs_dir, (ahora_e + 5, ahora_e + 5))
            a3 = _obs_de_grabaciones(g_obs, set())
            _cam.busca = busca_antes
            r("✖: buscar las grabaciones de OBS se hace UNA vez y se recuerda mientras no cambie nada (la grabación, la carpeta de OBS o los demás vídeos)",
              len(a1["archivos"]) == 2 and a2 is a1 and n_dos == 1 and len(busquedas) == 2 and len(a3["archivos"]) == 2, (n_dos, len(busquedas)))

            video("zz-e1", "zz-e1.mkv", 10)             # publicado hace 10 días: se puede borrar entero
            video("zz-e2", "zz-e2.mkv", None)           # sin publicar: nunca
            video("zz-e4", "zz-e4.mkv", 2)              # publicado hace 2 días: solo sus cachés
            (P["work"] / "zz-orf--pro" / "escenas").mkdir(parents=True)
            (P["work"] / "zz-orf--pro" / "escenas" / "a.bin").write_bytes(b"o" * 2000)
            os.utime(P["work"] / "zz-orf--pro" / "escenas" / "a.bin", (ahora_e - 7200, ahora_e - 7200))
            with app.test_client() as cli:
                med = cli.get("/api/espacio?forzar=1").get_json()
                antes_c = len(bin_e)
                lib_c = cli.post("/api/espacio/liberar", json={"que": "caches"}).get_json()
                estado_c = (len(bin_e) == antes_c, not (P["work"] / "zz-e4--pro" / "escenas").exists(), not (P["work"] / "zz-orf--pro" / "escenas").exists(),
                            (P["work"] / "zz-e2--pro" / "escenas").is_dir(), (P["work"] / "zz-e1--pro" / "escenas").is_dir())
                lib_mal = cli.post("/api/espacio/liberar", json={"que": "todo"})
                lib_t = cli.post("/api/espacio/liberar", json={"que": "completos"}).get_json()
                estado_t = (not (P["outbox"] / "zz-e1--pro").exists(), not (P["inbox"] / "zz-e1.mkv").exists(), "zz-e1--pro" not in MGR.jobs,
                            "zz-e1" not in elim_e, "zz-e2--pro" in MGR.jobs, "zz-e4--pro" in MGR.jobs, (P["outbox"] / "zz-e2--pro").is_dir(), len(bin_e) == antes_c)
            vs = {v["base"]: v for v in med.get("videos", [])}
            r("💾 Espacio: mide (disco, carpetas, cada vídeo con lo que pesa y los días que le faltan, el ritmo) y dice qué se puede liberar: el vídeo publicado "
              "hace 10 días, y las cachés del de hace 2 días y del resto de un vídeo que ya no está",
              med.get("ok") and med["disco"]["libre"] > 0 and med["ritmo"]["dias_90"] > 0 and med["auto"] in (True, False)
              and vs["zz-e1"]["listo"] and not vs["zz-e2"]["publicado"] and vs["zz-e4"]["faltan_dias"] == 5
              and med["liberable"]["n_completos"] == 1 and med["liberable"]["lista"][0]["base"] == "zz-e1"
              and med["liberable"]["caches"] == 3 * 2000 + 2000 and med["liberable"]["huerfanos"] == 2000, (med.get("liberable"), vs.get("zz-e4")))
            r("💾 «🧹 Liberar cachés»: borra de verdad las cachés del publicado de hace 2 días y del resto huérfano, sin mandar nada a la Papelera, "
              "y no toca ni el sin publicar ni el que aún se puede borrar entero",
              lib_c.get("ok") and lib_c["cache"] == 8000 and all(estado_c) and lib_mal.status_code == 400, (lib_c, estado_c, lib_mal.status_code))
            r("💾 «🗑 Borrar los publicados»: el de hace 10 días se va con el mismo ✖ (del todo, y sigue en el Historial); el sin publicar y el de hace 2 días se quedan",
              lib_t.get("ok") and lib_t["bases"] == ["zz-e1"] and all(estado_t), (lib_t, estado_t))
            video("zz-e6", "zz-e6.mkv", 15)
            with app.test_client() as cli:
                lib_p = cli.post("/api/espacio/liberar", json={"que": "completos", "papelera": True}).get_json()
            r("💾 «🗑 Borrar los publicados» con «Papelera»: va a la Papelera en vez de borrarse", lib_p.get("ok") and lib_p["bases"] == ["zz-e6"]
              and lib_p["papelera"] > 0 and len(bin_e) == antes_c + 1 and "zz-e6--pro" in bin_e[-1], (lib_p, bin_e[-1:]))

            # la limpieza automática: apagada no hace nada; encendida borra lo publicado hace más de N días con el mismo ✖ y nunca toca OBS ni lo sin publicar
            video("zz-e5", "2026-10-03_12-00-00.mkv", 20)
            (obs_dir / "2026-10-03 12-00-00.mkv").write_bytes(b"v" * 3000)
            (obs_dir / "2026-10-03 12-00-00 camara.mkv").write_bytes(b"c" * 4000)
            antes_bin = len(bin_e)
            _esp.opciones = lambda cfg: (False, 7)
            _barrido_espacio()
            quieto = len(bin_e) == antes_bin and "zz-e5--pro" in MGR.jobs and (P["outbox"] / "zz-e5--pro").is_dir()
            _esp.opciones = lambda cfg: (True, 7)
            _barrido_espacio()
            r("💾 Limpieza automática: apagada no hace nada; encendida borra del todo lo publicado hace más de N días (queda en el Historial), sin tus grabaciones "
              "de OBS y sin tocar lo que no está publicado",
              quieto and "zz-e5--pro" not in MGR.jobs and not (P["outbox"] / "zz-e5--pro").exists() and not (P["inbox"] / "2026-10-03_12-00-00.mkv").exists()
              and len(bin_e) == antes_bin and (obs_dir / "2026-10-03 12-00-00.mkv").is_file() and (obs_dir / "2026-10-03 12-00-00 camara.mkv").is_file()
              and "zz-e2--pro" in MGR.jobs and (P["outbox"] / "zz-e2--pro").is_dir() and "zz-e5" not in elim_e, (quieto, bin_e[-1:]))
            with app.test_client() as cli:
                ok_o = cli.post("/api/espacio/opciones", json={"auto": True, "dias": 14}).get_json()
                mal_a = cli.post("/api/espacio/opciones", json={"auto": "si", "dias": 14})
                mal_d = cli.post("/api/espacio/opciones", json={"auto": True, "dias": 5})
            r("💾 Opciones: se guardan «automática» y los días (3, 7, 14 o 30); un valor raro se rechaza", ok_o.get("ok") and guardadas == [(True, 14)]
              and mal_a.status_code == 400 and mal_d.status_code == 400 and len(guardadas) == 1, (ok_o, guardadas))
        finally:
            (historial.carga, historial.elimina, historial.lista, _pap.a_la_papelera, _cam.parametros, _cam.busca, _esp.guarda_opciones, _esp.opciones,
             MGR._save, (P["outbox"], P["work"], P["inbox"])) = reales_e
            for jid in falsos_e:
                MGR.jobs.pop(jid, None)
            _espacio_olvida()

    # el Historial pregunta a YouTube (estado, fechas, título, miniatura) en un hilo aparte, solo con YouTube conectado y
    # si a algún vídeo le toca; la página no espera. Aquí YouTube y el historial son de mentira: nada sale a la red
    reales = (youtube.is_connected, historial.pendientes_yt, historial.actualiza_desde_youtube, historial.lista)
    llamadas: list = []
    try:
        historial.lista = lambda *a, **k: [{"base": "zz-h", "youtube": {"video_id": "v1"}}, {"base": "zz-sin"}]
        historial.actualiza_desde_youtube = lambda *a, **k: llamadas.append(1) or {"puestas": 0}
        youtube.is_connected = lambda: False
        historial.pendientes_yt = lambda *a, **k: ["zz-h"]
        with app.test_client() as cli:
            j_desc = cli.get("/api/historial").get_json()
            youtube.is_connected = lambda: True
            j_con = cli.get("/api/historial").get_json()
            if _HIST_YT["hilo"] is not None:
                _HIST_YT["hilo"].join(5)
            historial.pendientes_yt = lambda *a, **k: []
            j_nada = cli.get("/api/historial").get_json()
        r("Historial: con YouTube conectado y algún vídeo al que le toca, se pregunta a YouTube en segundo plano; sin "
          "conexión, o sin nada pendiente, ni se intenta — y la lista sale igual (solo lo subido a YouTube)",
          j_desc.get("actualizando_youtube") is False and j_con.get("actualizando_youtube") is True
          and j_nada.get("actualizando_youtube") is False and llamadas == [1]
          and [f["base"] for f in j_con.get("filas", [])] == ["zz-h"], (j_desc, j_con, j_nada, llamadas))
    finally:
        youtube.is_connected, historial.pendientes_yt, historial.actualiza_desde_youtube, historial.lista = reales
        _HIST_YT["hilo"] = None

    # los 4 cuadritos de métricas del Historial y su ↻ (el autor, 2026-09-28): la lista trae lo guardado SIN preguntar a
    # YouTube; el ↻ pasa por todos, uno detrás de otro, en un hilo aparte (YouTube y las métricas, de mentira)
    from clipper import metricas as _met
    reales_m = (youtube.is_connected, historial.lista, _met.refresca_boton, _met.resumen)
    pedidas_m: list = []
    try:
        youtube.is_connected = lambda: False
        historial.lista = lambda *a, **k: [{"base": "zz-m1", "youtube": {"video_id": "v1"}, "duracion_s": 60},
                                           {"base": "zz-m2", "youtube": {"video_id": "v2"}}, {"base": "zz-sin"}]
        _met.resumen = lambda base, dir_work=None: {"vistas": 10, "likes": 2, "comentarios": 1, "ret_pct": 40.0,
                                                    "ret_seg": 30.0, "leido_en": 1.0} if base == "zz-m1" else None
        _met.refresca_boton = lambda base, vid, duracion_s=None, **k: pedidas_m.append((base, vid, True)) or {"ok": True}
        with app.test_client() as cli:
            lista_m = cli.get("/api/historial").get_json()
            antes_m = len(pedidas_m)
            ref = cli.post("/api/historial/metricas/refrescar").get_json()
            if _HIST_MET["hilo"] is not None:
                _HIST_MET["hilo"].join(5)
            est_m = cli.get("/api/historial/metricas/estado").get_json()
        r("Historial: cada vídeo trae sus 4 métricas GUARDADAS (sin preguntar a YouTube al abrir la página)",
          [f.get("metricas", {}) and f["metricas"].get("vistas") for f in lista_m["filas"]] == [10, None]
          and antes_m == 0, (lista_m, antes_m))
        tot_m = lista_m.get("totales") or {}
        r("Historial: trae la SUMA de todos los vídeos para los 6 cuadros grandes (los sin lectura no suman ni rompen)",
          tot_m.get("vistas") == 10 and tot_m.get("likes") == 2 and tot_m.get("videos") == 2 and tot_m.get("con_datos") == 1
          and tot_m.get("leido_en") == 1.0 and tot_m.get("cambio") is None, tot_m)
        r("Historial ↻: actualiza las métricas de TODOS los vídeos subidos, uno detrás de otro (y dice cuántos lleva)",
          ref.get("ok") and pedidas_m == [("zz-m1", "v1", True), ("zz-m2", "v2", True)] and est_m["hechos"] == 2
          and est_m["total"] == 2 and not est_m["en_marcha"] and est_m["fin"], (ref, pedidas_m, est_m))
    finally:
        youtube.is_connected, historial.lista, _met.refresca_boton, _met.resumen = reales_m
        _HIST_MET.update(hilo=None, hechos=0, total=0, fallidos=0, fin=None)

    # si el autor borró la grabación de inbox/ (2026-09-26), «Reintentar» y «Reshuffle» lo dicen claro en vez de encolar un
    # trabajo que falla. Nada se encola ni se guarda: `submit` y `_save` son de mentira
    real_submit, real_save = MGR.submit, MGR._save
    encolados: list = []
    try:
        MGR.submit = lambda *a, **k: encolados.append(a) or None
        MGR._save = lambda: None
        MGR.jobs["zz-sin-grab--pro"] = Job(jid="zz-sin-grab--pro", source=str(Path(tempfile.gettempdir()) / "zz-no-existe.mkv"),
                                           label="x", opts={"modo": "video_pro"}, state="done")
        with app.test_client() as cli:
            r_re = cli.post("/api/job/zz-sin-grab--pro/retry")
            r_sh = cli.post("/api/job/zz-sin-grab--pro/reshuffle")
        r("sin la grabación original, «Reintentar» y «Reshuffle» avisan claro y no encolan nada",
          r_re.status_code == 400 and r_sh.status_code == 400 and "ya no está" in (r_re.get_json() or {}).get("error", "")
          and "ya no está" in (r_sh.get_json() or {}).get("error", "") and not encolados,
          (r_re.status_code, r_sh.status_code, r_re.get_json(), encolados))
    finally:
        MGR.jobs.pop("zz-sin-grab--pro", None)
        MGR.submit, MGR._save = real_submit, real_save

    # 💡 ideas y 📝 artículo (el autor, 2026-09-26): las rutas responden sin gastar nada, y elegir la idea al subir la grabación
    # deja `idea.json` para el director y la marca como grabada (las ideas, de mentira)
    from clipper import ideas as _idm
    reales_i = (_idm.idea_por_id, _idm.marca, P["work"])
    marcadas: list = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            P["work"] = Path(tmp)
            reales_d0 = _idm._dir                                  # (TODAS las rutas de ideas de esta prueba trabajan en una carpeta temporal: nunca sobre tus ideas de verdad)
            _idm._dir = lambda dir_work=None: Path(tmp) / "ideas"
            _idm.idea_por_id = lambda iid, dir_work=None: ({"id": iid, "titulo": "SEO para restaurantes", "puntos": ["a"]}
                                                           if iid == "20260926-1-01" else None)
            _idm.marca = lambda iid, nuevo, dir_work=None, **k: marcadas.append((iid, nuevo, k.get("jid"))) or True
            with app.test_client() as cli:
                j_art = cli.get("/api/articulo/zz-no-existe--pro").get_json()
                r_mal = cli.post("/api/ideas/<b>/marca")
                j_ideas = cli.get("/api/ideas").get_json()
            with app.test_client() as cli_v:
                pag_inicio = cli_v.get("/").get_data(as_text=True)
            r("💡 al subir la grabación YA NO se elige «¿Sale de una de tus ideas?» (el autor, 12-oct-2026): ni el selector en la página ni el enlace en el servidor",
              "Sale de una de tus ideas" not in pag_inicio and 'name="idea"' not in pag_inicio and "_vincula_idea" not in globals(), pag_inicio.count("idea"))
            r("💡/📝 las rutas nuevas responden sin gastar nada (artículo aún no creado, ideas con su freno, su tope de refrescos y "
              "el reparto de la lista)",
              j_art.get("estado") == "no" and r_mal.status_code in (400, 404) and "puede" in j_ideas and "tandas" in j_ideas
              and "refrescar" in j_ideas and "max" in j_ideas["refrescar"] and isinstance(j_ideas.get("total"), int)
              and "general" in (j_ideas.get("mezcla") or {}) and "negocio" in j_ideas.get("nombres_tipo", {})
              and j_ideas.get("canal") in [c["slug"] for c in j_ideas.get("canales") or []] and isinstance(j_ideas.get("necesita_perfil"), bool)
              and j_ideas.get("tipos") and j_ideas["tipos"][-1]["id"] == "general" and isinstance(j_ideas.get("semillas"), list),
              (j_art, r_mal.status_code, sorted(j_ideas)))
            # 💡 la ✖ de cada tanda: borra todo menos lo «Por grabar» (que sigue con su copia); «🗑 Eliminar» una idea; «Borrar todas las anteriores»
            reales_d = _idm._dir
            _idm._dir = lambda dir_work=None: Path(tmp) / "ideas"
            try:
                for tid_, tit_, ts_ in (("20270115-120000", "Idea cero", 1_799_999_000.0), ("20270115-130000", "Idea uno", 1_800_000_000.0), ("20270115-131000", "Idea dos", 1_800_000_100.0),
                                        ("20270115-132000", "Idea tres", 1_800_000_200.0)):
                    _idm._guarda(Path(tmp) / "ideas" / f"tanda_{tid_}.json", {"id": tid_, "ts": ts_, "version": 2, "canal": "ejemplo",
                                                                            "ideas": [{"id": f"{tid_}-01", "titulo": tit_, "tipo": "general"}],
                                                                            "ideas_anteriores": []})
                _idm._guarda(Path(tmp) / "ideas" / "estado.json", {"20270115-131000-01": {"estado": "grabar", "ts": 1.0}})
                with app.test_client() as cli:
                    r_x_ok = cli.post("/api/ideas/tanda/20270115-130000/borrar")
                    r_x_otra = cli.post("/api/ideas/tanda/20270115-130000/borrar")
                    r_x_grabar = cli.post("/api/ideas/tanda/20270115-131000/borrar")
                    r_x_raro = cli.post("/api/ideas/tanda/nada/borrar")
                    j_x_grabar = r_x_grabar.get_json() or {}
                    r_el = cli.post("/api/ideas/20270115-131000-01/eliminar")
                    r_el2 = cli.post("/api/ideas/20270115-131000-01/eliminar")
                    r_el_raro = cli.post("/api/ideas/<b>/eliminar")
                    r_ant = cli.post("/api/ideas/tandas/anteriores/borrar", json={"canal": "ejemplo"})
                r("💡 la ✖ de una tanda borra la tanda (200; ya no está = 404) y, con una idea «Por grabar», YA NO se niega: solo esa idea se queda; un id raro no pasa",
                  r_x_ok.status_code == 200 and r_x_ok.get_json().get("ok") and not (Path(tmp) / "ideas" / "tanda_20270115-130000.json").exists()
                  and r_x_otra.status_code == 404 and r_x_grabar.status_code == 200 and j_x_grabar.get("conservadas") == 1
                  and [i["titulo"] for i in j_x_grabar.get("por_grabar", [])] == ["Idea dos"] and not (Path(tmp) / "ideas" / "tanda_20270115-131000.json").exists()
                  and r_x_raro.status_code == 404, (r_x_ok.status_code, r_x_otra.status_code, r_x_grabar.status_code, j_x_grabar, r_x_raro.status_code))
                r("💡 «🗑 Eliminar» una idea: la saca de «Por grabar» y del disco (200); si ya no estaba, 404; un id raro, 404",
                  r_el.status_code == 200 and r_el.get_json().get("por_grabar") == [] and r_el2.status_code == 404 and r_el_raro.status_code == 404,
                  (r_el.status_code, r_el2.status_code, r_el_raro.status_code))
                r("💡 «🗑 Borrar todas las anteriores»: solo queda la tanda más reciente",
                  r_ant.status_code == 200 and r_ant.get_json().get("ok") and not list((Path(tmp) / "ideas").glob("tanda_*.json")) == [] and
                  [f.name for f in (Path(tmp) / "ideas").glob("tanda_*.json")] == ["tanda_20270115-132000.json"] and r_ant.get_json().get("tandas") == 1,
                  (r_ant.status_code, r_ant.get_json(), [f.name for f in (Path(tmp) / "ideas").glob("tanda_*.json")]))
            finally:
                _idm._dir = reales_d
            # 💡 la configuración de las ideas de un canal (4-oct-2026): las rutas del asistente, con la IA y el guardado de mentira
            from clipper import ideas_perfil as _pf, youtube as _ytm
            reales_pf = (_pf.propone, _pf.guarda, _ytm.is_connected)
            vistos_pf: list = []
            try:
                _pf.propone = lambda canal, resp, cfg, *a, **k: vistos_pf.append((canal, resp.get("tema"))) or {
                    "ok": True, "perfil": {"tema": "x", "total": 20}, "aviso": "", "modelo": "Gemini X"}
                _pf.guarda = lambda canal, d, dir_work=None: (({"tema": d.get("tema"), "canal": canal}, [])
                                                              if d.get("tema") else (None, ["Cuéntame de qué trata tu canal"]))
                _ytm.is_connected = lambda: False                  # (sin preguntarle nada a YouTube)
                canal_a = j_ideas["canal"]
                with app.test_client() as cli:
                    j_base = cli.get("/api/ideas/perfil").get_json()
                    j_prop = cli.post("/api/ideas/perfil/proponer", json={"canal": canal_a, "tema": "Cocina vegana fácil"}).get_json()
                    r_g_ok = cli.post("/api/ideas/perfil", json={"canal": canal_a, "perfil": {"tema": "Cocina vegana fácil"}})
                    r_g_mal = cli.post("/api/ideas/perfil", json={"canal": canal_a, "perfil": {}})
                    j_otro = cli.get("/api/ideas/perfil?canal=no-existe-zz").get_json()
                r("💡 el asistente de la primera vez: las rutas dan lo que ya se sabe del canal, proponen con UNA petición (de mentira), guardan y "
                  "rechazan lo incompleto; un canal que no existe cae en el de siempre",
                  j_base.get("canal") == canal_a and "objetivos" in j_base and "base" in j_base and "datos_propios_sugerido" in j_base
                  and j_prop.get("ok") and vistos_pf == [(canal_a, "Cocina vegana fácil")] and r_g_ok.status_code == 200
                  and r_g_ok.get_json()["perfil"]["tema"] == "Cocina vegana fácil" and r_g_mal.status_code == 400
                  and r_g_mal.get_json()["errores"] and j_otro.get("canal") == canal_a,
                  (j_base.get("canal"), vistos_pf, r_g_ok.status_code, r_g_mal.status_code, j_otro.get("canal")))
            finally:
                _pf.propone, _pf.guarda, _ytm.is_connected = reales_pf
            # 💡 «↻» de una idea (4-oct-2026): la ruta devuelve la idea nueva, el tope o que ya no está (la IA, de mentira)
            reales_ref = (_idm.refresca, _idm.puede_refrescar)
            try:
                def _refresca_falso(iid, cfg, *a, **k):
                    if iid == "20261004-1-03":
                        return {"ok": True, "idea": {"id": iid + "-r01", "titulo": "Nueva"}, "hoy": 1, "max": 30}
                    if iid == "tope-1":
                        return {"ok": False, "codigo": "tope", "error": "Hoy ya refrescaste 30 ideas"}
                    if iid == "ia-1":
                        return {"ok": False, "codigo": "ia", "error": "Gemini está saturado"}
                    return {"ok": False, "codigo": "no_esta", "error": "Esa idea ya no está en la lista"}
                _idm.refresca = _refresca_falso
                _idm.puede_refrescar = lambda cfg, *a, **k: {"ok": True, "motivo": "", "hoy": 1, "max": 30}
                with app.test_client() as cli:
                    r_ok = cli.post("/api/ideas/20261004-1-03/refrescar")
                    r_tope = cli.post("/api/ideas/tope-1/refrescar")
                    r_ia = cli.post("/api/ideas/ia-1/refrescar")
                    r_no = cli.post("/api/ideas/no-existe/refrescar")
                    r_raro = cli.post("/api/ideas/<b>/refrescar")
                j_ok = r_ok.get_json() or {}
                r("💡 «↻» de una idea: la ruta devuelve la idea nueva (200), el tope (409), que la IA no respondió (502) o que ya no "
                  "está (404), y siempre cómo va el tope",
                  r_ok.status_code == 200 and j_ok.get("idea", {}).get("titulo") == "Nueva" and j_ok.get("refrescar", {}).get("max") == 30
                  and r_tope.status_code == 409 and "30" in (r_tope.get_json() or {}).get("error", "") and r_ia.status_code == 502
                  and r_no.status_code == 404 and r_raro.status_code == 404,
                  (r_ok.status_code, r_tope.status_code, r_ia.status_code, r_no.status_code, r_raro.status_code))
            finally:
                _idm.refresca, _idm.puede_refrescar = reales_ref
        finally:
            _idm.idea_por_id, _idm.marca, P["work"] = reales_i
            _idm._dir = reales_d0

    real_tiempos = TIEMPOS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        TIEMPOS_FILE = Path(tmp) / "tiempos-render.json"
        try:
            # --- la barra: pre-render se queda BAJA, el render ocupa el hueco grande ---
            j = job_pro()
            for linea in ("Vídeo: 2560x1440 @ 60fps — duración 00:23:11",
                          "Transcripción encontrada en caché, la reuso.",
                          "PROGRESO director_inicio", "PROGRESO director_fin",
                          "Tarjetas:", "Imágenes ilustrativas:"):
                _apply_stage(j, linea)
            r("la duración del vídeo fuente se lee del log", j.source_duration_s == 23 * 60 + 11, j.source_duration_s)
            r("antes de renderizar, la barra se queda BAJA (no casi llena)", j.pct <= 19, j.pct)

            _apply_stage(j, "PROGRESO render_pct=1")
            pct_al_1pc = j.pct
            r("el render arranca justo donde acabó el análisis (sin saltar para atrás)",
              pct_al_1pc >= _RENDER_PCT_INI, pct_al_1pc)
            r("al 1% del render la barra NO parece casi terminada", pct_al_1pc < 30, pct_al_1pc)

            _apply_stage(j, "PROGRESO render_pct=51")
            r("a la mitad del render de verdad, la barra va a medio camino de su tramo (no casi al final)",
              45 <= j.pct <= 65, j.pct)

            _apply_stage(j, "PROGRESO render_pct=100")
            r("el render completo deja la barra cerca del final de su tramo",
              j.pct >= _RENDER_PCT_FIN - 1, j.pct)

            j2 = job_pro()
            _apply_stage(j2, "PROGRESO render_pct=0")
            r("la barra nunca RETROCEDE (empezar el render en 0% no baja el % ya alcanzado)",
              j2.pct >= 0, j2.pct)

            # --- el ETA: antes del render, aprendido; durante, extrapolado del avance REAL ---
            _tiempo_registrar("pro_render_ratio", 1.0)          # 1s de render por cada 1s de vídeo fuente
            _tiempo_registrar("pro_analisis_seg", 60.0)
            j3 = job_pro(source_duration_s=600.0, started=time.time() - 30)
            eta_antes = j3._eta_pro()
            r("antes de renderizar, el ETA sale de lo aprendido (no un '2 minutos' fijo que nunca cambia)",
              eta_antes > 0 and abs(eta_antes - (60 + 600 - 30)) <= 2, eta_antes)

            j4 = job_pro(render_started=time.time() - 100, render_pct_ffmpeg=50)
            eta_render = j4._eta_pro()
            r("a la mitad del render (50% en 100s), el ETA extrapola ~100s más, no lo aprendido",
              90 <= eta_render <= 110, eta_render)

            j5 = job_pro(state="done")
            r("un trabajo que ya terminó no tiene ETA (0, no un número viejo)", j5.eta() == 0)

            # --- se aprende de un trabajo Vídeo PRO real al terminar (antes solo pasaba en "Generador") ---
            TIEMPOS_FILE.write_text("{}", encoding="utf-8")     # limpio lo registrado arriba
            j6 = job_pro(source_duration_s=200.0)
            j6.render_started = time.time() - 40
            _apply_stage(j6, "LISTO — vídeo PRO en 1 min")
            aprendido = json.loads(TIEMPOS_FILE.read_text(encoding="utf-8"))
            r("al terminar un Vídeo PRO se aprende el ratio de render (segs de render / segs de vídeo fuente)",
              "pro_render_ratio" in aprendido and abs(aprendido["pro_render_ratio"]["media"] - 0.2) < 0.05,
              aprendido.get("pro_render_ratio"))

            # --- elegir miniatura propia ("A/B/C") al publicar: usa la que el autor subió, si la hay ---
            jid_thumb, arch_thumb = "zz-prueba--thumb", "clip.mp4"
            try:
                r("sin elegir nada, se usa la automática (None)",
                  _resolver_thumb_override(jid_thumb, arch_thumb, None) is None)
                r("eligiendo 'auto' explícitamente también usa la automática",
                  _resolver_thumb_override(jid_thumb, arch_thumb, "auto") is None)
                r("un id que no existe cae a la automática (no revienta el publicar)",
                  _resolver_thumb_override(jid_thumb, arch_thumb, "9999999999") is None)

                import io as _io
                from PIL import Image
                buf = _io.BytesIO()
                Image.new("RGB", (1280, 720), (10, 90, 200)).save(buf, format="JPEG")
                subida = miniaturas_custom.guardar(jid_thumb, arch_thumb, buf.getvalue())
                override = _resolver_thumb_override(jid_thumb, arch_thumb, subida.get("id"))
                r("con una miniatura propia subida, esa es la que se usa al publicar",
                  override is not None and Path(override).is_file(), override)
            finally:
                shutil.rmtree(P["outbox"] / jid_thumb, ignore_errors=True)
        finally:
            TIEMPOS_FILE = real_tiempos

    # «Mis vídeos» se actualiza sola (el autor, 2026-09-27: «la barra… se queda en 90%… cuando actualizo la página ahí sí
    # cambia»): cada tarjeta en marcha lleva su jid y su estado, y la página la consulta cada 3 s (tarjetasVivas). Se pinta
    # la plantilla con trabajos inventados: no toca la lista de trabajos de verdad.
    import re as _re

    def fila(jid, st, pct=0):
        return {"jid": jid, "state": st, "pct": pct, "stage": "Renderizando el vídeo… (68%)", "tipo": "pro", "thumb": None,
                "nombre": jid, "label": jid, "source": jid}
    with app.test_request_context("/"):
        html = render_template("videos.html", jobs=[fila("zz-vivo-a--pro", "running", 68), fila("zz-vivo-b--pro", "queued"),
                                                    fila("zz-vivo-c--pro", "done", 100)],
                               busy=True, canales=[], mejoras_max=3, motor_pedido="auto", motor_activo="cpu",
                               hay_gpu=False, ia_ve_video=False)

    def tarjeta(jid):
        m = _re.search(r'<a class="jobcard" data-jid="' + _re.escape(jid) + r'".*?</a>', html, _re.S)
        return m.group(0) if m else ""
    ta, tb, tc = tarjeta("zz-vivo-a--pro"), tarjeta("zz-vivo-b--pro"), tarjeta("zz-vivo-c--pro")
    r("«Mis vídeos»: la tarjeta que se está procesando lleva su barra viva (68%) y su estado, para actualizarse sola",
      'data-state="running"' in ta and 'class="bar vivo" style="margin:2px 0"' in ta and "width:68%" in ta
      and "data-estado" in ta, ta[:400])
    r("«Mis vídeos»: la que está en cola lleva la barra escondida (sale al empezar) y la terminada no lleva ninguna",
      'data-state="queued"' in tb and "display:none" in tb and 'data-state="done"' in tc and "bar vivo" not in tc,
      (tb[:300], tc[:300]))
    r("«Mis vídeos»: la página pregunta sola por lo que está en marcha, con los estados en español",
      "tarjetasVivas();" in html and bool(_re.search(r'"running":\s*"Procesando"', html)))

    # 📚 listas de reproducción al publicar (el autor, 2026-10-05): las que marcas se recuerdan y cada vídeo se añade a ellas al subirse; un fallo no tumba la subida (YouTube de mentira)
    LA, LB = "PLaaaaaaaaaaaa", "PLbbbbbbbbbbbb"
    reales_ly = (youtube.is_connected, youtube.channel_info, youtube.listas_mias, youtube.anade_a_lista, listas_yt._ruta, PUB._save, dict(_LISTAS_CACHE), dict(_YT_CANAL))
    with tempfile.TemporaryDirectory() as tmp_ly:
        try:
            youtube.is_connected = lambda: True
            youtube.channel_info = lambda: {"connected": True, "channel": "MI CANAL", "channel_id": "UCprueba"}
            youtube.listas_mias = lambda: [{"id": LB, "titulo": "Cursos", "n": 3, "privacidad": "private"}, {"id": LA, "titulo": "SEO/GOOGLE", "n": 92, "privacidad": "public"}]
            listas_yt._ruta = lambda dir_work=None: Path(tmp_ly) / "youtube_listas.json"
            PUB._save = lambda: None
            _LISTAS_CACHE.update(ts=0.0, listas=[])
            _YT_CANAL.update(ts=0.0, id=None)
            with app.test_client() as cli:
                j0 = cli.get("/api/youtube/listas").get_json()                      # la 1.ª vez: sin haber elegido nada
                j1 = cli.get("/api/youtube/listas").get_json()
                r_ning = cli.post("/api/youtube/listas/elegidas", json={"listas": []})      # el usuario desmarca todas
                j2 = cli.get("/api/youtube/listas").get_json()
                r_g = cli.post("/api/youtube/listas/elegidas", json={"listas": [LB]})
                j3 = cli.get("/api/youtube/listas").get_json()
                r_mal = cli.post("/api/youtube/listas/elegidas", json={"listas": ["PLzzzzzzzzzzzz"]})
            r("📚 las listas: se leen de YouTube de MÁS a MENOS vídeos (id, título, nº) y la PRIMERA vez se marca por defecto la que tiene más (SEO/GOOGLE, 92), que se recuerda sola",
              j0.get("ok") and [x["id"] for x in j0["listas"]] == [LA, LB] and j0["elegidas"] == [LA] and j0["por_defecto"] is True
              and j1["elegidas"] == [LA] and j1["por_defecto"] is False and listas_yt.elegidas("UCprueba")["definida"], (j0, j1))
            r("📚 si desmarcas todas se respeta («ninguna» es una elección: no se vuelve a poner la de por defecto), lo que marcas se recuerda y una lista que no es de tu canal se rechaza",
              r_ning.status_code == 200 and j2["elegidas"] == [] and j2["por_defecto"] is False and r_g.status_code == 200 and j3["elegidas"] == [LB]
              and r_mal.status_code == 400 and listas_yt.elegidas("UCprueba")["listas"] == [LB], (j2, j3, r_mal.status_code))
            llam: list = []
            falla = {LB: 1}

            def anade_falso(video, lista, pos):
                llam.append((video, lista, pos))
                if falla.get(lista, 0) > 0:
                    falla[lista] -= 1
                    raise ValueError("lista de mentira rota")
                return {"ok": True, "ya": False}
            youtube.anade_a_lista = anade_falso
            it_l = {"playlists": [LA, LB], "playlists_pos": "inicio"}
            PUB._listas(it_l, "VID")
            res1 = list(it_l["listas"])
            llam.clear()
            PUB._listas(it_l, "VID")
            r("📚 al subirse el vídeo se añade a sus listas; si una falla se dice cuál y por qué (la otra queda bien) y el reintento toca SOLO la que falló (cada petición cuesta cuota)",
              [x["ok"] for x in res1] == [True, False] and "lista de mentira rota" in res1[1]["error"] and llam == [("VID", LB, "inicio")]
              and [x["ok"] for x in it_l["listas"]] == [True, True], (res1, llam, it_l["listas"]))
            # `enqueue` aplica la elección: la 1.ª vez (nunca elegida) va a la lista con más vídeos; «ninguna» se respeta; una fila que pide otra lista manda. Con un Publisher SIN hilo de subida
            # (el de verdad subiría el clip a YouTube) y un clip de mentira en una salida temporal
            pub_x = Publisher.__new__(Publisher)
            pub_x.items, pub_x.queue, pub_x.lock, pub_x._save = [], deque(), threading.Lock(), (lambda: None)
            salida_real = P["outbox"]
            P["outbox"] = Path(tmp_ly) / "salida"
            try:
                (P["outbox"] / "zz-listas--pro").mkdir(parents=True)
                (P["outbox"] / "zz-listas--pro" / "clip.mp4").write_bytes(b"x")
                fila_l = {"archivo": "clip.mp4", "title": "t"}
                listas_yt._ruta().unlink(missing_ok=True)                           # (nunca ha elegido nada)
                a1 = pub_x.enqueue("zz-listas--pro", [dict(fila_l)])[0]
                listas_yt.guarda("UCprueba", [], dir_work=tmp_ly)                    # (elige «ninguna»)
                a2 = pub_x.enqueue("zz-listas--pro", [dict(fila_l)])[0]
                a3 = pub_x.enqueue("zz-listas--pro", [{**fila_l, "playlists": [LB]}])[0]
            finally:
                P["outbox"] = salida_real
            r("📚 al encolar un vídeo: la 1.ª vez (nunca has elegido) va a la lista con más vídeos, si eliges «ninguna» a ninguna, y una fila que pide otra lista manda sobre lo marcado",
              a1["playlists"] == [LA] and a2["playlists"] == [] and a3["playlists"] == [LB] and a1["playlists_pos"] == "final" and len(pub_x.items) == 3,
              (a1["playlists"], a2["playlists"], a3["playlists"]))
            listas_yt.guarda("UCprueba", [], dir_work=tmp_ly)
            PUB.items.append({"id": "zz-listas-1", "jid": "zz-listas--pro", "archivo": "clip.mp4", "title": "t", "state": "done", "video_id": "VID2", "playlists": [],
                              "created": time.time()})
            PUB.items.append({"id": "zz-listas-2", "jid": "zz-listas--pro", "archivo": "otro.mp4", "title": "t", "state": "queued", "created": time.time()})
            try:
                llam.clear()
                with app.test_client() as cli:
                    sin_marcar = cli.post("/publish-item/zz-listas-1/listas")
                    con_listas = cli.post("/publish-item/zz-listas-1/listas", json={"listas": [LB]})
                    sin_subir = cli.post("/publish-item/zz-listas-2/listas")
                r("📚 «↻ Listas»: sin ninguna marcada lo dice (no gasta cuota), con una elegida la añade al vídeo ya subido y un vídeo que aún no se subió se rechaza",
                  sin_marcar.status_code == 400 and "ninguna lista" in sin_marcar.get_json()["error"] and con_listas.status_code == 200 and con_listas.get_json()["ok"]
                  and llam == [("VID2", LB, "final")] and sin_subir.status_code == 400, (sin_marcar.get_json(), con_listas.get_json(), llam))
            finally:
                PUB.items[:] = [x for x in PUB.items if not str(x.get("id", "")).startswith("zz-listas-")]
        finally:
            (youtube.is_connected, youtube.channel_info, youtube.listas_mias, youtube.anade_a_lista, listas_yt._ruta, PUB._save, _lc, _yc) = reales_ly
            _LISTAS_CACHE.clear()
            _LISTAS_CACHE.update(_lc)
            _YT_CANAL.clear()
            _YT_CANAL.update(_yc)

    r("«⚡ Programar rápido»: las etiquetas del vídeo llegan como LISTA (del manifest) o como texto, y las dos valen",
      _tags_lista(["seo", " google ", ""]) == ["seo", "google"] and _tags_lista("seo, google,") == ["seo", "google"]
      and _tags_lista(None) == [])

    # 🎨 miniaturas con IA (el autor, 2026-09-28): estado, imagen y «Usar esta» -> ya elegida al programar. Una carpeta de
    # prueba «zz-…» en la salida real (como la de las miniaturas propias de arriba), borrada al acabar; sin Cloudflare
    from clipper import miniaturas_ia as _mia
    jid_ia = "zz-prueba-mia--pro"
    try:
        from PIL import Image as _Im
        carpeta_ia = P["outbox"] / jid_ia / "miniaturas_ia"
        carpeta_ia.mkdir(parents=True, exist_ok=True)
        (P["outbox"] / jid_ia / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4"}]), encoding="utf-8")
        _Im.new("RGB", (1280, 720), (254, 51, 10)).save(carpeta_ia / "A.jpg", quality=85)
        (carpeta_ia / "estado.json").write_text(json.dumps({"estado": "listo", "items": {
            "A": {"ok": True, "hecha": 1790000000, "cara": "cara feliz"},
            "B": {"ok": False, "error": "El filtro de Cloudflare marcó dos dibujos seguidos"}}}), encoding="utf-8")
        with app.test_client() as cli:
            e_ia = cli.get(f"/api/miniaturas-ia/{jid_ia}").get_json()
            img_ia = cli.get(f"/miniaturas-ia/{jid_ia}/A.jpg")
            img_ia.close()                                          # (Windows: si no, el .jpg queda abierto y no se borra)
            usar = cli.post(f"/api/miniaturas-ia/{jid_ia}/usar/A").get_json()
            cands = cli.get(f"/job/{jid_ia}/thumb-candidatos?archivo=pro.mp4").get_json()
            e_ia2 = cli.get(f"/api/miniaturas-ia/{jid_ia}").get_json()
            nada = cli.post(f"/api/miniaturas-ia/{jid_ia}/usar/C").status_code
        r("🎨 miniaturas con IA: el panel ve su estado, la imagen de cada una y la batería de Cloudflare",
          e_ia.get("estado") == "listo" and str(e_ia["items"]["A"].get("url", "")).startswith(f"/miniaturas-ia/{jid_ia}/A.jpg")
          and "url" not in e_ia["items"]["B"] and img_ia.status_code == 200 and "quedan_miniaturas" in e_ia.get("bateria", {}),
          (e_ia, img_ia.status_code))
        r("🎨 «Usar esta»: pasa a tus miniaturas del vídeo y sale YA ELEGIDA al programar (y la A queda «en uso»)",
          usar.get("ok") and any(c["id"] == usar.get("id") and c["preferida"] for c in cands)
          and e_ia2["items"]["A"].get("usada") == usar.get("id") and nada == 404, (usar, cands))
    finally:
        shutil.rmtree(P["outbox"] / jid_ia, ignore_errors=True)

    # 📌 reglas permanentes del director (el autor, 2026-10-03): las rutas de la tarjeta de mejora y la casilla «Recordar esto…». El
    # archivo de reglas va a una carpeta temporal (nunca el de verdad)
    from clipper import reglas as _rg
    jid_rg = "zz-prueba-reglas--pro"
    dw_real = _rg._dir_work
    tmp_rg = Path(tempfile.mkdtemp(prefix="zz-prueba-reglas-web-"))
    try:
        _rg._dir_work = lambda dir_work=None: tmp_rg
        (P["outbox"] / jid_rg).mkdir(parents=True, exist_ok=True)
        (P["outbox"] / jid_rg / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "tema": "zz-canal-prueba"}]),
                                                           encoding="utf-8")
        with app.test_client() as cli:
            vacia = cli.get(f"/api/job/{jid_rg}/reglas").get_json()
            nueva = cli.post(f"/api/job/{jid_rg}/reglas", json={"texto": "No cortes mis risas, las dejo a propósito"})
            igual = cli.post(f"/api/job/{jid_rg}/reglas", json={"texto": "NO cortes mis RISAS: las dejo a propósito"})
            corta = cli.post(f"/api/job/{jid_rg}/reglas", json={"texto": "no"})
            rid = (nueva.get_json() or {}).get("regla", {}).get("id", "r0000000")
            pausa = cli.post(f"/api/job/{jid_rg}/reglas/{rid}", json={"activa": False}).get_json()
            edita = cli.post(f"/api/job/{jid_rg}/reglas/{rid}", json={"texto": "Deja siempre mis risas, son parte del estilo"}).get_json()
            mala = cli.post(f"/api/job/{jid_rg}/reglas/{rid}", json={"texto": "x"}).status_code
            raro = cli.post(f"/api/job/{jid_rg}/reglas/../borrar").status_code
            quita = cli.post(f"/api/job/{jid_rg}/reglas/{rid}/borrar").get_json()
        r("📌 reglas: la lista del canal del vídeo empieza vacía, una regla nueva entra (con el canal del manifest), una repetida "
          "(409) o corta (400) se rechaza con su motivo", vacia["ok"] and vacia["reglas"] == [] and vacia["canal"] == "zz-canal-prueba"
          and nueva.status_code == 200 and nueva.get_json()["reglas"][0]["activa"] is True and igual.status_code == 409
          and "casi igual" in igual.get_json()["error"] and corta.status_code == 400, (vacia, igual.get_json()))
        r("📌 reglas: pausar, editar (una edición inválida se rechaza) y borrar (queda guardada en «borradas»); un id raro, 404",
          pausa["reglas"][0]["activa"] is False and edita["reglas"][0]["texto"].startswith("Deja siempre mis risas") and mala == 400
          and raro == 404 and quita["reglas"] == [] and _rg.lee()["borradas"][0]["id"] == rid, (pausa, quita))
        # la casilla de la tarjeta de mejora: guarda la regla (la que el autor confirmó o, si no, el comentario) antes de lanzar
        with app.app_context():                                  # (_regla_de_mejora arma respuestas de Flask)
            info1, malo1 = _regla_de_mejora(jid_rg, "Corta el silencio del 3:20 y no cortes mis risas", "No cortes mis risas nunca, son mi estilo")
            info2, malo2 = _regla_de_mejora(jid_rg, "x" * 10, "No cortes mis risas nunca, son mi estilo")
            info3, malo3 = _regla_de_mejora(jid_rg, "Corta mucho más relleno siempre", "")
        r("📌 la casilla de «Mejorar»: guarda la regla confirmada (o el comentario si no la cambió); una repetida NO frena la "
          "mejora y lo avisa; una que no se puede guardar sí frena",
          info1 and info1["ok"] and info1["texto"] == "No cortes mis risas nunca, son mi estilo" and malo1 is None
          and info2 and not info2["ok"] and info2["duplicada"] and malo2 is None
          and info3 and info3["ok"] and info3["texto"] == "Corta mucho más relleno siempre"
          and [x["canal"] for x in _rg.lista("zz-canal-prueba")] == ["zz-canal-prueba"] * 2, (info1, info2, info3))
        for w_ in ("menos relleno", "más zoom", "títulos cortos", "tarjetas grandes", "pausas largas", "cortes secos",
                   "ritmo rápido", "voz clara", "música suave", "finales breves", "saludos cortos", "datos claros"):
            _rg.agrega("zz-canal-prueba", f"Quiero {w_} en todos mis vídeos")
        with app.app_context():
            info4, malo4 = _regla_de_mejora(jid_rg, "Otra regla más que ya no cabe en este canal", "")
            error4 = malo4[0].get_json()["error"] if malo4 else ""
        r("📌 con el tope de reglas lleno la mejora NO sigue (400) y dice qué hacer",
          info4 is None and malo4 is not None and malo4[1] == 400 and "borra o cambia" in error4, (info4, error4))
    finally:
        _rg._dir_work = dw_real
        shutil.rmtree(tmp_rg, ignore_errors=True)
        shutil.rmtree(P["outbox"] / jid_rg, ignore_errors=True)

    # 🛡 nombres de lo que sale en pantalla (el autor, 2026-10-03; clipper/nombres.py): la página de versiones enseña lo corregido y
    # lo quitado, y avisa si no se pudo revisar con la IA
    def _pagina(nombres):
        v = {"jid": "zz-prueba-nombres--pro", "version": 1, "state": "done", "sin_cambios": False, "tipo_v": "primera",
             "duracion": 90.0, "titulo": "T", "tiene_video": False, "reglas": [], "nombres": nombres, "estado": "done"}
        with app.test_request_context():
            return render_template("versiones.html", base="zz-prueba-nombres", versiones=[v], maximo=3, puede={}, usadas=0,
                                   restantes=3, siguiente=2, en_marcha=False, ultima="", nombre="Prueba")
    h1 = _pagina({"revisado": True, "corregidas": [{"antes": "Pulso Wev", "despues": "Pulso Web", "tipo": "tarjeta · herramienta",
                                                   "t": 65.0}],
                  "quitadas": [{"texto": "MediNator 5000", "tipo": "tarjeta · herramienta", "t": 121.0,
                                "motivo": "no se pudo confirmar el nombre"}]})
    h2 = _pagina({"revisado": False, "corregidas": [], "quitadas": [], "motivo": "503 UNAVAILABLE"})
    h3 = _pagina({"revisado": True, "corregidas": [], "quitadas": []})
    r("🛡 la página de versiones enseña los nombres corregidos y los quitados (con su minuto y por qué), y cómo pedir uno que sea real",
      "Pulso Wev" in h1 and "Pulso Web" in h1 and "MediNator 5000" in h1 and "1:05" in h1 and "2:01" in h1
      and "no se pudo confirmar el nombre" in h1 and "pon la tarjeta de" in h1, h1[h1.find("Nombres de lo que sale"):][:300])
    r("🛡 si no se pudieron revisar con la IA lo dice; y si todo estaba bien, no enseña nada",
      "no se pudieron revisar con la IA" in h2 and "503" in h2 and "Nombres de lo que sale" not in h3)

    # 🎨 «↻» de un concepto de miniatura (el autor, 2026-10-03): la imagen de un concepto anterior queda marcada, el titular de cada
    # letra llega al panel, y la ruta pasa lo justo a clipper/conceptos_miniatura.py (con la IA FALSA: no gasta Gemini)
    from clipper import conceptos_miniatura as _cm
    jid_cm = "zz-prueba-concepto--pro-v2"
    real_renueva = _cm.renueva
    try:
        from PIL import Image as _Im2
        carpeta_cm = P["outbox"] / jid_cm / "miniaturas_ia"
        carpeta_cm.mkdir(parents=True, exist_ok=True)
        _Im2.new("RGB", (1280, 720), (254, 51, 10)).save(carpeta_cm / "A.jpg", quality=85)
        _Im2.new("RGB", (1280, 720), (20, 16, 15)).save(carpeta_cm / "B.jpg", quality=85)
        (P["outbox"] / jid_cm / "manifest.json").write_text(json.dumps([{
            "archivo": "pro.mp4", "miniatura_prompts": ["Concepto A original", "Concepto B NUEVO"],
            "miniatura_titulares": ["TITULAR A", "TITULAR B"]}]), encoding="utf-8")
        (carpeta_cm / "estado.json").write_text(json.dumps({"estado": "listo", "items": {
            "A": {"ok": True, "hecha": 1790000000, "prompt_f": _mia._firma(["Concepto A original"])},
            "B": {"ok": True, "hecha": 1790000000, "prompt_f": _mia._firma(["Concepto B viejo"])}}}), encoding="utf-8")
        visto_cm: list = []

        def _falsa(folder, work, slug, cfg, indice, jid="", **kw):
            visto_cm.append((Path(folder).name, Path(work).name, slug, indice, jid))
            return {"ok": True, "indice": indice, "prompts": ["a", "n"], "titulares": ["", "NUEVO"]}

        _cm.renueva = _falsa
        with app.test_client() as cli:
            e_cm = cli.get(f"/api/miniaturas-ia/{jid_cm}").get_json()
            ok_cm = cli.post(f"/job/{jid_cm}/miniatura-nueva", json={"indice": 1})
            sin_cm = cli.post(f"/job/{jid_cm}/miniatura-nueva", json={})
            no_cm = cli.post("/job/zz-no-existe--pro/miniatura-nueva", json={"indice": 0})
        r("🎨 la imagen de un concepto ANTERIOR sale marcada (la A, al día, no) y el panel recibe el titular de cada letra",
          e_cm["items"]["A"].get("vigente") is True and e_cm["items"]["B"].get("vigente") is False
          and e_cm.get("titulares") == ["TITULAR A", "TITULAR B"], e_cm)
        r("🎨 «↻» de un concepto: la ruta pasa la carpeta, el trabajo de ESA versión, su plan y la letra; sin letra o sin vídeo, "
          "lo dice", ok_cm.status_code == 200 and visto_cm == [(jid_cm, "zz-prueba-concepto--pro", "pro-v2", 1, jid_cm)]
          and sin_cm.status_code == 400 and no_cm.status_code == 404, (ok_cm.get_json(), visto_cm, sin_cm.status_code))
    finally:
        _cm.renueva = real_renueva
        shutil.rmtree(P["outbox"] / jid_cm, ignore_errors=True)
    return out


# ---------------------------------------------------------------- gestor de cola
class Manager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.queue: deque[str] = deque()
        self.current: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self._load()
        threading.Thread(target=self._worker, daemon=True).start()

    # -- persistencia -------------------------------------------------
    def _load(self) -> None:
        if not JOBS_FILE.exists():
            return
        try:
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        reenganchar: list[Job] = []
        for d in data:
            d.pop("has_output", None)
            d.pop("elapsed", None)
            job = Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
            if job.state in ("queued", "running", "paused"):
                pid = job.pid or _pid_leer(job.jid)
                if pid and _pid_vivo(pid):
                    # El pipeline SIGUE CORRIENDO (sobrevive al cierre del
                    # panel). Antes lo dabamos por muerto y la UI mentia: por
                    # eso "a veces me detecta y a veces no". Ahora nos
                    # re-enganchamos a su log.
                    job.state, job.pid = "running", pid
                    self.jobs[job.jid] = job
                    reenganchar.append(job)
                    continue
                job.state = "error"
                job.error = job.error or "Interrumpido (se cerró el panel)."
                _clear_flags(job.jid)
            self.jobs[job.jid] = job
        # los hilos arrancan al FINAL: si arrancan dentro del bucle, _save()
        # itera self.jobs mientras se sigue llenando y revienta.
        for job in reenganchar:
            threading.Thread(target=self._reenganchar, args=(job,),
                             daemon=True).start()

    def _save(self) -> None:
        try:
            JOBS_FILE.write_text(
                json.dumps([j.to_public() for j in self.jobs.values()],
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- API --------------------------------------------------------
    def submit(self, source: str, opts: dict) -> Job:
        jid = pipeline.job_id(source)
        if opts.get("modo") == "video":
            jid = f"{jid}--videos"
        elif opts.get("modo") == "video_pro":
            # el archivo subido es SUYO (namespace propio): nunca comparte
            # carpeta con otro trabajo, aunque por casualidad fuera el mismo hash.
            # version>1 ("reshuffle"): mismo nombre base + "-vN", para que
            # las distintas corridas queden una al lado de la otra sin pisarse.
            v = max(1, int(opts.get("version") or 1))
            jid = f"{jid}--pro" if v == 1 else f"{jid}--pro-v{v}"
        with self.lock:
            existing = self.jobs.get(jid)
            if existing and existing.state in ("queued", "running"):
                return existing
            label = source if pipeline.ingest.is_url(source) else Path(source).name
            if opts.get("modo") == "video_pro" and int(opts.get("version") or 1) > 1:
                label = (f"{label} (v{int(opts['version'])} ✂ editado)" if opts.get("cortes")
                         else f"{label} (v{int(opts['version'])} ✨ mejorado)" if opts.get("mejora")
                         else f"{label} (v{int(opts['version'])})")
            job = Job(jid=jid, source=source, label=label, opts=opts)
            job.log_path.parent.mkdir(parents=True, exist_ok=True)
            job.log_path.write_text("", encoding="utf-8")
            self.jobs[jid] = job
            self.queue.append(jid)
            self._save()
            return job

    def cancel(self, jid: str) -> bool:
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                return False
            if job.state == "queued":
                if jid in self.queue:
                    self.queue.remove(jid)
                job.state = "cancelled"
                job.stage = "Cancelado"
                self._save()
                return True
            if job.state in ("running", "paused") and self.proc and self.current == jid:
                _clear_flags(jid)
                _kill_tree(self.proc.pid)
                job.state = "cancelled"
                job.stage = "Cancelado"
                return True
        return False

    def pause(self, jid: str) -> bool:
        """Pausa cooperativa: el proceso sigue vivo pero se queda esperando
        entre vídeo y vídeo. Deja revisar/programar los que ya están listos."""
        with self.lock:
            job = self.jobs.get(jid)
            if not job or job.state != "running" or self.current != jid:
                return False
            try:
                (P["work"] / jid / "PAUSE").write_text("1", encoding="utf-8")
            except OSError:
                return False
            job.state = "paused"
            job.stage = "En pausa — puedes revisar y programar los vídeos de abajo"
            self._save()
            return True

    def resume(self, jid: str) -> bool:
        with self.lock:
            job = self.jobs.get(jid)
            if not job or job.state != "paused":
                return False
            try:
                (P["work"] / jid / "PAUSE").unlink()
            except OSError:
                pass
            job.state = "running"
            job.stage = "Reanudando…"
            self._save()
            return True

    def retry(self, jid: str) -> Job | None:
        old = self.jobs.get(jid)
        if not old:
            return None
        return self.submit(old.source, old.opts)

    # -- worker ---------------------------------------------------
    def _worker(self) -> None:
        while True:
            jid = None
            with self.lock:
                if self.queue:
                    jid = self.queue.popleft()
                    self.current = jid
            if jid is None:
                time.sleep(1)
                continue
            job = self.jobs[jid]
            try:
                self._run(job)
            except Exception as exc:                    # noqa: BLE001
                job.state = "error"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                with self.lock:
                    self.current = None
                    self.proc = None
                self._save()

    def _run(self, job: Job) -> None:
        job.state = "running"
        job.started = time.time()
        job.stage = "Arrancando…"
        self._save()

        if job.opts.get("modo") == "video_pro":
            cmd = [sys.executable, "-u", "run_video_pro.py", job.source]
            if job.opts.get("canal"):
                cmd += ["--canal", job.opts["canal"]]
            if job.opts.get("solo_analisis"):
                cmd += ["--analizar"]
            if int(job.opts.get("version") or 1) > 1:
                cmd += ["--version", str(int(job.opts["version"]))]
            if job.opts.get("plan_de") and job.opts.get("mejora"):
                # el comentario va en un archivo (no en la línea de comandos: puede ser largo y llevar comillas)
                base_m = re.sub(r"--pro(-v\d+)?$", "", job.jid)
                f_m = P["work"] / f"{base_m}--pro" / f"mejora_v{int(job.opts.get('version') or 2)}.txt"
                f_m.parent.mkdir(parents=True, exist_ok=True)
                f_m.write_text(str(job.opts["mejora"]), encoding="utf-8")
                cmd += ["--plan-de", str(job.opts["plan_de"]), "--mejora-archivo", str(f_m)]
            elif job.opts.get("plan_de") and job.opts.get("cortes"):
                cmd += ["--plan-de", str(job.opts["plan_de"]), "--cortes",
                        ",".join(f"{float(a):.4f}-{float(b):.4f}" for a, b in job.opts["cortes"])]
            return self._lanzar(job, cmd)

        # el único otro modo en este proyecto: "video" (Generador por temática)
        cmd = [sys.executable, "-u", "run_video.py", job.source]
        if job.opts.get("contexto"):
            cmd += ["--contexto", job.opts["contexto"]]
        if job.opts.get("max"):
            cmd += ["--max", str(job.opts["max"])]
        if job.opts.get("min_min"):
            cmd += ["--min-min", str(job.opts["min_min"])]
        if job.opts.get("max_min"):
            cmd += ["--max-min", str(job.opts["max_min"])]
        if job.opts.get("subs"):
            cmd += ["--subs"]
        if job.opts.get("solo_analisis"):
            cmd += ["--analizar"]
        return self._lanzar(job, cmd)

    def _lanzar(self, job: Job, cmd: list[str]) -> None:
        env = _child_env()
        # run_video*.py escribe DIRECTO al fichero de log, no a una tuberia.
        # Antes iba por tuberia y el panel era el unico lector: si cerrabas la
        # ventana del panel, el pipeline seguia vivo pero se BLOQUEABA para
        # siempre en su siguiente print() al llenarse el buffer.
        fh = open(job.log_path, "a", encoding="utf-8", buffering=1)
        try:
            print("$ " + " ".join(cmd), file=fh)
            print("", file=fh)
            fh.flush()
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT), env=env,
                stdout=fh, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        finally:
            fh.close()          # el hijo ya tiene su copia del descriptor
        with self.lock:
            self.proc = proc
        job.pid = proc.pid
        _pid_guardar(job.jid, proc.pid)
        self._save()

        self._seguir_log(job, lambda: proc.poll() is None)
        proc.wait()
        job.returncode = proc.returncode
        self._finalizar(job)

    # ------------------------------------------------ seguir el log en vivo
    def _seguir_log(self, job: Job, sigue_vivo, desde: int = 0) -> None:
        """Va leyendo el fichero de log y actualizando la etapa/ETA.
        Sirve tanto para el proceso que lanzamos nosotros como para
        RE-ENGANCHARSE a uno que ya venia corriendo de antes."""
        pos, buf, ultimo = desde, "", 0.0
        while True:
            try:
                vivo = bool(sigue_vivo())
            except Exception:                            # noqa: BLE001
                vivo = False
            trozo = ""
            try:
                with open(job.log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    trozo = f.read()
                    pos = f.tell()
            except OSError:
                pass
            if trozo:
                buf += trozo
                partes = buf.split(NEWLINE)
                buf = partes.pop()
                for ln in partes:
                    _apply_stage(job, ln.rstrip())
                    if "PROGRESO miniaturas_listas" in ln and not getattr(job, "_replay", False) and "--pro" in job.jid and not job.opts.get("solo_analisis"):
                        try:                                    # 🎨 el vídeo ya está: las miniaturas con IA arrancan YA, en paralelo con los Shorts
                            _miniaturas_ia_arranca(job.jid)
                        except Exception as exc:                # noqa: BLE001
                            clipper_log(f"AVISO: no pude arrancar las miniaturas con IA de {job.jid} ({exc}).")
                ahora = time.time()
                if ahora - ultimo > 2.0:
                    self._save()
                    ultimo = ahora
            elif not vivo:
                break
            else:
                time.sleep(0.4)
        self._save()

    # ------------------------------------------- re-engancharse a un trabajo
    def _reenganchar(self, job: Job) -> None:
        """El panel se cerro pero el pipeline siguio vivo: se vuelve a leer su
        log desde el principio para reconstruir el progreso REAL."""
        clipper_log(f"Re-enganchado al trabajo en curso: {job.jid} (pid {job.pid}).")
        with self.lock:
            self.current = job.jid
        job.clips_done = 0
        job.fase, job.clips_fin = "", None
        job._replay = True
        try:
            self._seguir_log(job, lambda: _pid_vivo(job.pid))
        finally:
            job._replay = False
        job.returncode = 0 if _salida_ok(job.out_dir, bool(job.opts.get("solo_analisis"))) else 1
        self._finalizar(job)
        with self.lock:
            self.current = None
        self._save()

    # ------------------------------------------------------ cierre del job
    def _finalizar(self, job: Job) -> None:
        _clear_flags(job.jid)
        try:
            (P["work"] / job.jid / "run.pid").unlink()
        except OSError:
            pass
        if job.state == "cancelled":
            return
        if job.state == "paused":                 # el proceso murió estando en pausa
            job.state = "error"
            job.error = job.error or "El proceso terminó mientras estaba en pausa."
        if job.returncode == 0 and _salida_ok(job.out_dir, bool(job.opts.get("solo_analisis"))):
            nota = ""
            if "--pro" in job.jid and not job.opts.get("solo_analisis"):
                nota = self._espera_miniaturas(job)                 # 🎨 «Terminado» = el vídeo + AL MENOS UNA miniatura con IA (el autor, 11-oct-2026)
                if job.state == "cancelled":
                    return
            job.state = "done"
            job.stage = "Terminado" + (f" ({nota})" if nota else "")
            job.pct = 100
            job.eta_seconds = 0
            job.finished = job.finished or time.time()
            job.n_clips_result = _n_resultado(job.out_dir)
        else:
            job.state = "error"
            job.stage = "Falló"
            if not job.error:
                tail = job.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
                job.error = NEWLINE.join(tail) or f"el proceso salió con código {job.returncode}"
        job.finished = job.finished or time.time()

    def _espera_miniaturas(self, job: Job, tope_s: float | None = None, cada_s: float = 2.0) -> str:
        """«Terminado» = el vídeo + AL MENOS UNA miniatura con IA (el autor, 11-oct-2026: «sí o sí al menos una hecha; las otras dos que se terminen de pronto en el proceso… lo más importante es
        el vídeo»; el 9-oct pedía las 3, pero un Cloudflare caído retenía cada vídeo). Arranca las miniaturas si aún no lo hicieron (normalmente ya van en paralelo desde `miniaturas_listas`) y
        espera SOLO a la primera: en cuanto hay una, el vídeo sale y las otras siguen haciéndose aparte (la página las va pintando). No se queda colgado: sale igual si pasan
        `miniaturas_ia.espera_max_min` (10 min), si el hilo acaba sin ninguna, o si Cloudflare falló del todo hace poco y no ha vuelto (`cloudflare_caido`: entonces ni espera).
        Una miniatura de un concepto anterior (tras el «↻» de su prompt) no cuenta. Devuelve la nota para «Terminado (…)»: '' si están las 3."""
        from clipper import miniaturas_ia as mia
        try:
            cfg = config.load()
            ok, _motivo = mia.disponible(cfg)
            from clipper import ediciones_canal as _edc
            if ok and not _edc.permite(cfg, _canal_de(job.jid), "miniaturas_ia"):
                ok = False                                          # el canal apagó las miniaturas con IA: nada que esperar
            if not ok:
                return ""                                           # sin Cloudflare no hay miniaturas con IA que esperar (el flujo manual de siempre)
            if job.jid not in _MINIS["hilos"]:
                _miniaturas_ia_arranca(job.jid)
            tope = float(tope_s) if tope_s is not None else float(mia.parametros(cfg)["espera_max_min"]) * 60

            def hechas() -> list[str]:                              # las de ESTE concepto que ya están
                items = mia.lee(job.jid).get("items") or {}
                try:
                    vig = mia.vigentes(job.jid)
                except Exception:                                   # noqa: BLE001
                    vig = {}
                return [l for l in mia.LETRAS if (items.get(l) or {}).get("ok") and vig.get(l, True)]

            def viva() -> bool:
                h = _MINIS["hilos"].get(job.jid)
                return h is not None and h.is_alive()

            t0 = time.time()
            while time.time() - t0 < tope:
                if job.state == "cancelled":
                    return ""
                if hechas() or not viva() or mia.cloudflare_caido(cfg):
                    break                                           # ya hay una · el hilo acabó · o Cloudflare está caído: el vídeo sale
                job.stage, job.pct = "Generando la primera miniatura…", 99
                time.sleep(cada_s)
            if job.state == "cancelled":
                return ""
            return _nota_miniaturas(job.jid, cfg, avisa=True)
        except Exception as exc:                                    # noqa: BLE001 — esto nunca puede dejar un vídeo colgado
            clipper_log(f"AVISO: no pude esperar las miniaturas de {job.jid} ({exc}).")
            return ""


def _nota_miniaturas(jid: str, cfg=None, avisa: bool = False) -> str:
    """La nota de «Terminado (…)» según cómo van las miniaturas con IA AHORA: '' si están las 3 (de este concepto); si no, qué pasa con las que faltan.
    Se calcula al soltar el vídeo (`_espera_miniaturas`, con `avisa` para dejar el AVISO en el registro) y se vuelve a calcular cada vez que se
    enseña el trabajo (`Job._refresca_nota`): sin eso la tarjeta se quedaba en «las miniaturas B y C siguen haciéndose» aunque ya estuvieran hechas
    (el autor, 11-oct-2026)."""
    from clipper import miniaturas_ia as mia
    cfg = cfg if cfg is not None else config.load()
    items = mia.lee(jid).get("items") or {}
    try:
        vig = mia.vigentes(jid)
    except Exception:                                               # noqa: BLE001
        vig = {}
    listas = [l for l in mia.LETRAS if (items.get(l) or {}).get("ok") and vig.get(l, True)]
    sin = [l for l in mia.LETRAS if l not in listas]
    h = _MINIS["hilos"].get(jid)
    viva = h is not None and h.is_alive()
    if not sin:
        return ""
    if listas:
        if viva:
            return f"{_cuales(sin)} {'sigue' if len(sin) == 1 else 'siguen'} haciéndose"
        if avisa:
            clipper_log(f"AVISO: {jid} sale sin {_cuales(sin)}: Cloudflare no respondió. Se pueden pedir con «↻ Otra» en la página del vídeo.")
        return f"sin {_cuales(sin)}"
    if viva:                                                        # ninguna todavía, pero el hilo sigue: se sigue intentando aparte
        motivo = "Cloudflare falló hace poco" if mia.cloudflare_caido(cfg) else "Cloudflare tarda"
        if avisa:
            clipper_log(f"AVISO: {jid} sale SIN miniaturas con IA todavía ({motivo}): se siguen intentando aparte y aparecerán en la página del vídeo.")
        return f"las miniaturas con IA se siguen haciendo aparte ({motivo})"
    if avisa:
        clipper_log(f"AVISO: {jid} sale sin miniaturas con IA: Cloudflare no respondió. Se pueden pedir con «↻ Otra» en la página del vídeo.")
    return "sin miniaturas con IA: Cloudflare no respondió"


def _cuales(letras: list[str]) -> str:
    """['B'] -> «la miniatura B»; ['B', 'C'] -> «las miniaturas B y C»; ['A', 'B', 'C'] -> «las miniaturas A, B y C»."""
    if len(letras) == 1:
        return f"la miniatura {letras[0]}"
    return "las miniaturas " + ", ".join(letras[:-1]) + " y " + letras[-1]


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)
    except OSError:
        pass


def _salida_ok(out_dir: Path, solo_analisis: bool = False) -> bool:
    """¿El trabajo dejó resultado? El Generador escribe videos.json (con
    clave "videos") y con eso ya vale. Un Vídeo PRO en modo "solo análisis"
    también solo deja videos.json (sin manifest.json) A PROPÓSITO — eso YA
    es el resultado esperado ahí. Pero un Vídeo PRO que sí intentó
    renderizar escribe videos.json ANTES de renderizar (con el plan del
    director) y manifest.json solo si el render terminó bien de verdad: si
    ffmpeg revienta a mitad, pipeline.py atrapa la excepción, loguea "FALLÓ
    el render" y el proceso sigue saliendo con código 0 sin manifest.json —
    sin esta distinción ese caso se veía como "Terminado" en el panel
    aunque el vídeo nunca salió."""
    if (out_dir / "manifest.json").exists():
        return True
    vj = out_dir / "videos.json"
    if not vj.exists():
        return False
    if solo_analisis:
        return True
    try:
        d = json.loads(vj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and "videos" in d


_NOMBRES: dict[str, tuple[float, str]] = {}


def _nombre_video(jid: str) -> str:
    """El NOMBRE legible de un vídeo PRO ya renderizado (el autor, 2026-09-26): «26-sep-2026 · Search Console · IAs en inglés»
    en vez del nombre del archivo de OBS (que trae la fecha de GRABACIÓN y no dice de qué va). El del manifest
    (`pipeline.nombre_video`) o, en un vídeo de antes, calculado igual: la fecha del .mp4 (cuándo se renderizó) + sus dos
    primeras etiquetas SEO (o el título). Caché por fecha del manifest. '' si aún no hay vídeo (se usa el del archivo)."""
    out = P["outbox"] / jid
    man_p = out / "manifest.json"
    try:
        mt = man_p.stat().st_mtime
    except OSError:
        return ""
    cache = _NOMBRES.get(jid)
    if cache and cache[0] == mt:
        return cache[1]
    nombre = ""
    try:
        man = json.loads(man_p.read_text(encoding="utf-8"))
        e = man[0] if isinstance(man, list) and man else {}
        nombre = str(e.get("nombre") or "")
        if not nombre and e.get("pro"):
            mp4 = out / (e.get("archivo") or "pro.mp4")
            fecha = e.get("renderizado") or (mp4.stat().st_mtime if mp4.is_file() else mt)
            nombre = pipeline.nombre_video(fecha, e.get("tags"), e.get("titulo") or "")
    except (OSError, ValueError, AttributeError):
        nombre = ""
    _NOMBRES[jid] = (mt, nombre)
    return nombre


def _sufijo_version(label: str) -> str:
    """« (v2 ✨ mejorado)» de la etiqueta de una versión, para ponerlo también detrás de su nombre."""
    m = re.search(r" \(v\d+[^)]*\)$", str(label or ""))
    return m.group(0) if m else ""


def _lee_analisis(out_dir: Path) -> dict | None:
    """Estado del análisis de IA de un vídeo PRO (analisis_ia.json, lo escribe
    pipeline.py): {"ok": true, "nombre": "Gemini 3.5 Flash-Lite", ...} si Gemini
    lo analizó, {"ok": false, "motivo": ...} si ningún modelo respondió. None
    si el vídeo es anterior a este aviso o no es un PRO."""
    try:
        d = json.loads((out_dir / "analisis_ia.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _n_resultado(out_dir: Path) -> int:
    """Cuántos vídeos dejó. manifest.json primero: existe en Generador Y en
    PRO ya renderizados, y es la lista real de lo que se generó. videos.json
    de un PRO no tiene clave "videos" (trae recortes/títulos/etc.) — mirarlo
    primero ahí siempre daba 0 en un PRO terminado, aunque sí hubiera vídeo."""
    try:
        return len(json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    try:
        d = json.loads((out_dir / "videos.json").read_text(encoding="utf-8"))
        return len(d.get("videos", []))
    except (OSError, ValueError, AttributeError):
        return 0


def _pid_guardar(jid: str, pid: int) -> None:
    try:
        (P["work"] / jid).mkdir(parents=True, exist_ok=True)
        (P["work"] / jid / "run.pid").write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _pid_leer(jid: str) -> int:
    try:
        return int((P["work"] / jid / "run.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _clear_flags(jid: str) -> None:
    for name in ("PAUSE", "STOP"):
        try:
            (P["work"] / jid / name).unlink()
        except OSError:
            pass


def _child_env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    parts = [str(Path(sys.executable).parent)]           # venv\Scripts -> yt-dlp
    ff = shutil.which("ffmpeg")
    if ff:
        parts.append(str(Path(ff).parent))
    else:
        for cand in Path(os.environ.get("LOCALAPPDATA", "")).glob(
            "Microsoft/WinGet/Packages/Gyan.FFmpeg*/**/bin"
        ):
            parts.append(str(cand))
            break
    env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
    return env


# ffmpeg/ffprobe/yt-dlp disponibles para TODA llamada in-process (miniaturas,
# re-render...), sea cual sea el shell que lanzó la app.
os.environ["PATH"] = _child_env()["PATH"]


MGR = Manager()


# ============================================ publicador de YouTube (cola aparte)
def _job_manifest(jid: str) -> dict[str, dict]:
    """{archivo: {tag, layout, ...}} del manifest.json de un trabajo."""
    try:
        man = json.loads((P["outbox"] / jid / "manifest.json").read_text(encoding="utf-8"))
        return {m["archivo"]: m for m in man if "archivo" in m}
    except (OSError, ValueError, KeyError):
        return {}


def _sync_seleccion(jid: str) -> None:
    """La señal principal del aprendizaje del modo "Generador": lo que
    PROGRAMAS de un trabajo = te gustó; el resto = no. Se llama al programar y
    al des-programar, así no depende de que borres nada.

    Un Vídeo PRO es 1 archivo subido -> 1 salida: no hay "elegidos entre
    varios" que aprender."""
    if not jid.endswith("--videos"):
        return
    meta = _job_manifest(jid)
    if not meta:
        return
    todos_v = list(meta.keys())
    eleg_v = [it.get("archivo") for it in PUB.for_job(jid)
              if it.get("state") != "error" and it.get("archivo") in meta]
    juego = next((e.get("tema") for e in meta.values() if e.get("tema")), "general")
    try:
        taste_videos.record_seleccion(juego, jid, eleg_v, todos_v, meta)
    except Exception as exc:                                # noqa: BLE001
        clipper_log(f"taste_videos.record_seleccion: {exc}")


def _tags_lista(v) -> list[str]:
    """Las etiquetas de YouTube como lista, lleguen como texto («a, b, c»: la fila de programar a mano) o como LISTA (el
    manifest de un vídeo PRO: «⚡ Programar rápido» las pasaba tal cual y reventaba con AttributeError, 27-sep 17:51)."""
    trozos = v if isinstance(v, (list, tuple)) else str(v or "").split(",")
    return [str(t).strip() for t in trozos if str(t or "").strip()]


def _probe_dur(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, env=_child_env(),
        ).stdout.strip()
        return float(out)
    except (ValueError, OSError):
        return 40.0


def _reapply_thumb_later(video_id: str, thumb_path: str,
                         delays=(120, 600)) -> None:
    """Re-sube la miniatura pasados unos minutos: YouTube a veces la pierde al
    terminar de procesar el vídeo."""
    def _run():
        for d in delays:
            time.sleep(d)
            try:
                youtube.set_thumbnail(video_id, thumb_path)
                clipper_log(f"Publisher: miniatura re-aplicada en {video_id} (+{d}s).")
            except Exception as exc:                        # noqa: BLE001
                if "403" in str(exc) and "thumbnail" in str(exc).lower():
                    clipper_log(f"Publisher: YouTube no deja RE-poner la miniatura de {video_id} (403 «forbidden»: pasa con vídeos recién subidos o programados). "
                                "No es un error tuyo: la que se puso al subir el vídeo es la que vale; compruébala en YouTube Studio y, si falta, ponla a mano ahí. "
                                "No vuelvo a intentarlo.")
                    break                                   # el segundo intento daría lo mismo
                clipper_log(f"Publisher: re-aplicar miniatura {video_id} falló: {exc}")
    threading.Thread(target=_run, daemon=True).start()


def _resolver_thumb_override(jid: str, archivo: str, cand_id) -> str | None:
    """El path local de la miniatura que el autor eligió a mano para publicar (el
    "A/B/C" de miniaturas), o None si toca usar la automática de siempre —
    tanto porque no eligió ninguna como porque el id que llegó ya no existe
    (nunca revienta el publicar por una candidata que desapareció)."""
    cid = str(cand_id or "").strip()
    if not cid or cid == "auto":
        return None
    p = miniaturas_custom.ruta(jid, archivo, cid)
    return str(p) if p else None


# 📚 listas de reproducción al publicar (el autor, 2026-10-05). El canal de YouTube conectado y sus listas se recuerdan 10 min: cada vídeo que se encola no pregunta a YouTube.
_YT_CANAL: dict = {"ts": 0.0, "id": None}
_LISTAS_CACHE: dict = {"ts": 0.0, "listas": []}
_LISTAS_TTL = 600.0


def _canal_yt_id() -> str | None:
    """El id del canal de YouTube conectado (None si no se puede saber)."""
    if _YT_CANAL["id"] and time.time() - _YT_CANAL["ts"] < _LISTAS_TTL:
        return _YT_CANAL["id"]
    try:
        info = youtube.channel_info() or {}
    except Exception:                                          # noqa: BLE001
        info = {}
    _YT_CANAL.update(ts=time.time(), id=info.get("channel_id"))
    return _YT_CANAL["id"]


def _listas_yt(refrescar: bool = False) -> list[dict]:
    """TUS listas de reproducción (1 unidad de cuota; se recuerdan 10 min)."""
    if not refrescar and _LISTAS_CACHE["listas"] and time.time() - _LISTAS_CACHE["ts"] < _LISTAS_TTL:
        return _LISTAS_CACHE["listas"]
    listas = listas_yt.ordena(youtube.listas_mias())           # de MÁS a MENOS vídeos: la primera es la que se marca por defecto (el autor, 2026-10-05)
    _LISTAS_CACHE.update(ts=time.time(), listas=listas)
    return listas


def _listas_elegidas(cid) -> tuple[dict, bool]:
    """Las listas marcadas para ese canal -> (elección, «se acaba de marcar la de por defecto»). La PRIMERA vez (nunca has elegido nada) se marca por defecto la que tiene MÁS vídeos y se
    recuerda como cualquier elección (el autor, 2026-10-05: «por defecto, la que tenga más videos, por ejemplo SEO/Google»): desmarcarla es elegir «ninguna», que se respeta y no vuelve a ponerse
    sola. Si no se pueden leer tus listas (sin red…) no se marca ni se guarda nada, y se vuelve a intentar la próxima vez."""
    e = listas_yt.elegidas(cid)
    if e["definida"]:
        return e, False
    try:
        d = listas_yt.por_defecto(_listas_yt())
    except Exception:                                          # noqa: BLE001
        return e, False
    if not d:
        return e, False
    return listas_yt.guarda(cid, d), True


class Publisher:
    def __init__(self) -> None:
        self.items: list[dict] = []          # {id,jid,archivo,title,...,state,url,pct,error}
        self.queue: deque[str] = deque()
        self.lock = threading.Lock()
        self.connecting = False
        self._bok = 0            # subidas OK del lote en curso
        self._berr = 0           # subidas con error del lote en curso
        try:
            self.last_batch = json.loads(LAST_BATCH_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.last_batch = None   # {"ts","ok","err"} del último lote terminado
        self._load()
        threading.Thread(target=self._worker, daemon=True).start()
        # NADA en segundo plano: las vistas de YouTube NO se refrescan solas.
        # Solo al pulsar "refrescar" (POST /refrescar-vistas).

    def _load(self) -> None:
        try:
            self.items = json.loads(PUB_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []
        for it in self.items:
            if it.get("state") in ("queued", "uploading"):
                it["state"] = "error"
                it["error"] = it.get("error") or "Interrumpido (se reinició la UI)."

    def _save(self) -> None:
        try:
            PUB_FILE.write_text(json.dumps(self.items, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        except OSError:
            pass

    def enqueue(self, jid: str, rows: list[dict]) -> list[dict]:
        added = []
        meta = _job_manifest(jid)
        cid = _canal_yt_id()
        pos = _listas_elegidas(cid)[0]["posicion"]             # (la 1.ª vez marca por defecto la de más vídeos y la recuerda: así ya vale para ESTE vídeo)
        with self.lock:
            for r in rows:
                arch = Path(r.get("archivo", "")).name
                if not arch.endswith(".mp4") or not (P["outbox"] / jid / arch).is_file():
                    continue
                m = meta.get(arch, {})
                it = {
                    "id": f"{int(time.time()*1000)}_{len(self.items)}",
                    "jid": jid, "archivo": arch,
                    "title": (r.get("title") or arch)[:100],
                    "description": r.get("description") or "",
                    "tags": _tags_lista(r.get("tags")),
                    "publish_at": r.get("publish_at") or None,
                    "tag": m.get("tag", ""), "layout": m.get("layout", ""),
                    "tema": m.get("tema", ""),
                    "thumb_override": _resolver_thumb_override(jid, arch, r.get("thumb_candidato")),
                    "playlists": listas_yt.para_publicar(r.get("playlists"), cid), "playlists_pos": pos,
                    "state": "queued", "pct": 0, "url": None, "error": None,
                    "created": time.time(),
                }
                self.items.append(it)
                self.queue.append(it["id"])
                added.append(it)
            self._save()
        return added

    def for_job(self, jid: str) -> list[dict]:
        return [it for it in self.items if it["jid"] == jid]

    def _thumb_path(self, it: dict) -> Path:
        return P["outbox"] / it["jid"] / (Path(it["archivo"]).stem + ".jpg")

    def _cleanup(self, it: dict) -> None:
        if not config.load().get_path("cleanup.borrar_tras_subir", True):
            return
        for f in (P["outbox"] / it["jid"] / it["archivo"], self._thumb_path(it)):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        it["local_deleted"] = True

    def _worker(self) -> None:
        while True:
            iid = None
            with self.lock:
                if self.queue:
                    iid = self.queue.popleft()
            if iid is None:
                time.sleep(1)
                continue
            it = next((x for x in self.items if x["id"] == iid), None)
            if not it:
                continue
            it["state"] = "uploading"
            self._save()
            cfg = config.load()
            os.environ["PATH"] = _child_env()["PATH"]   # ffmpeg para make_thumbnail
            clip = P["outbox"] / it["jid"] / it["archivo"]

            # si el autor eligió una miniatura propia (el "A/B/C") se usa esa; si no,
            # la automática, que ya se generó al renderizar el vídeo (junto al
            # .mp4) y se reutiliza — solo se regenera si de verdad falta.
            thumb = Path(it["thumb_override"]) if it.get("thumb_override") else self._thumb_path(it)
            if cfg.get_path("thumbnail.enabled", True) and not thumb.is_file() and clip.is_file():
                try:
                    thumb = thumbs.make_thumbnail(
                        clip, _probe_dur(clip) * 0.42, it["title"], self._thumb_path(it),
                        pipeline._cfg_horizontal(cfg),
                        it.get("tag", ""), it.get("layout", "")) or self._thumb_path(it)
                except Exception as exc:                    # noqa: BLE001
                    clipper_log(f"Publisher: miniatura de {it['archivo']} falló: {exc}")

            try:
                res = youtube.upload(
                    str(clip), it["title"], it["description"], it["tags"],
                    it["publish_at"],
                    progress_cb=lambda p, _it=it: (_it.__setitem__("pct", p), self._save()),
                    reintento_s=float(cfg.get_path("publish.upload_reintento_seg", 180)),
                )
                it.update(state="done", pct=100, url=res["url"],
                          studio=res.get("studio"), video_id=res["video_id"])
                historial.registra_publicado(it["jid"], res["video_id"], res["url"])   # sobrevive aunque borres el .mp4 después
                if cfg.get_path("thumbnail.enabled", True):
                    if thumb and thumb.is_file():
                        # ESPERA DELIBERADA antes de poner la miniatura: YouTube
                        # acaba de recibir el video y lo esta procesando. Si la
                        # miniatura llega justo en ese hueco, a veces la acepta
                        # (devuelve 200) pero luego la DESCARTA al terminar de
                        # procesar. Dandole unos segundos, cuaja mucho mejor.
                        espera = float(cfg.get_path("publish.espera_miniatura_seg", 5) or 0)
                        if espera > 0:
                            clipper_log(f"Publisher: espero {espera:.0f}s a que YouTube "
                                        f"procese el video antes de poner la miniatura.")
                            time.sleep(espera)
                        ok, err, turl = False, "", ""
                        for intento in range(3):
                            try:
                                _r = youtube.set_thumbnail(res["video_id"], str(thumb))
                                turl = _r.get("url", "")
                                ok = True
                                break
                            except Exception as exc:        # noqa: BLE001
                                err = f"{type(exc).__name__}: {exc}"
                                time.sleep(4)
                        it["thumb_set"] = ok
                        it["thumb_url"] = turl
                        if ok:
                            clipper_log(f"Publisher: miniatura puesta en {res['video_id']}."
                                        + (f" -> {turl}" if turl else ""))
                            # YouTube a veces DESCARTA la miniatura durante el
                            # procesado del vídeo -> se re-aplica un par de veces.
                            _reapply_thumb_later(res["video_id"], str(thumb))
                        else:
                            it["thumb_error"] = err
                            clipper_log(f"Publisher: set_thumbnail falló ({err}).")
                    else:
                        it["thumb_set"] = False
                        it["thumb_error"] = "no había .jpg de la miniatura junto al clip"
                        clipper_log(f"Publisher: sin miniatura para {it['archivo']} "
                                    f"(no existe {thumb.name}).")
                self._listas(it, res["video_id"])
                try:
                    if str(it.get("jid", "")).endswith("--videos"):
                        _m = _job_manifest(it["jid"]) or {}
                        _j = next((e.get("tema") for e in _m.values() if e.get("tema")),
                                  "general")
                        taste_videos.record_upload(_j, it["archivo"], res["video_id"])
                    # un Vídeo PRO (--pro) no alimenta ningún aprendizaje: es 1
                    # archivo -> 1 salida, no hay "elegido entre varios".
                except Exception as exc:                    # noqa: BLE001
                    clipper_log(f"Publisher: taste_videos.record_upload falló: {exc}")
                self._cleanup(it)
            except Exception as exc:                        # noqa: BLE001
                # Un error al subir NO cuenta para el aprendizaje (ni +1 ni −1):
                # si lo elegiste para subir es que te gustó; el fallo suele ser
                # cuota/red. Si luego lo borras, ESA acción sí lo marca −1.
                it.update(state="error", error=f"{type(exc).__name__}: {exc}")
            if it.get("state") == "done":
                self._bok += 1
            elif it.get("state") == "error":
                self._berr += 1
            self._save()
            # ¿se vació la cola? -> lote terminado: deja la marca para avisar
            if not self.queue and (self._bok or self._berr):
                self._finish_batch()
            time.sleep(2)

    def _listas(self, it: dict, video_id: str) -> None:
        """Mete el vídeo en sus listas de reproducción (las marcadas al encolarlo). Nunca lanza: un fallo aquí no tumba la publicación (el vídeo ya está en YouTube) y queda dicho en
        `it["listas"]` con el motivo. Las que ya entraron no se vuelven a pedir (cada petición cuesta 50 unidades): «↻ Listas» solo reintenta las que fallaron."""
        ids = listas_yt.limpia(it.get("playlists"))
        if not ids:
            return
        bien = {x["id"]: x for x in it.get("listas") or [] if x.get("ok")}
        pend = [i for i in ids if i not in bien]
        try:
            nuevos = {x["id"]: x for x in listas_yt.anade_todas(video_id, pend, youtube.anade_a_lista, it.get("playlists_pos") or "final")}
        except Exception as exc:                               # noqa: BLE001
            nuevos = {i: {"id": i, "ok": False, "ya": False, "error": f"{type(exc).__name__}: {exc}"} for i in pend}
        it["listas"] = [bien.get(i) or nuevos[i] for i in ids]
        titulos = {x["id"]: x["titulo"] for x in _LISTAS_CACHE["listas"]}
        clipper_log(f"Publisher: listas de {video_id}: " + listas_yt.texto_resultado(it["listas"], titulos))

    def _finish_batch(self) -> None:
        self.last_batch = {"ts": time.time(), "ok": self._bok, "err": self._berr}
        self._bok = self._berr = 0
        try:
            LAST_BATCH_FILE.write_text(
                json.dumps(self.last_batch, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        clipper_log(f"Publisher: lote terminado - {self.last_batch['ok']} subido(s)"
                    + (f", {self.last_batch['err']} con error"
                       if self.last_batch["err"] else "") + ".")


PUB = Publisher()


# =================================================================== histórico
def _discovered_jobs() -> list[dict]:
    """Trabajos ya renderizados que estén en la carpeta de salida aunque el
    gestor no los conozca (p. ej. si se limpió webui_jobs.json)."""
    out = []
    informes = list(P["outbox"].glob("*/videos.json"))
    for cj in sorted(informes, key=lambda p: -p.stat().st_mtime):
        jid = cj.parent.name
        if jid.startswith("zz-selftest-"):        # el clip de "Probar el sistema": no es un trabajo tuyo
            continue
        if jid in MGR.jobs or any(d["jid"] == jid for d in out):
            continue
        try:
            rep = json.loads(cj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # videos.json de un PRO no trae clave "videos" (Generador sí) — el
        # manifest.json real, si lo hay, es la cuenta que de verdad importa.
        manifest = cj.parent / "manifest.json"
        if manifest.exists():
            try:
                n_result = len(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                n_result = 0
        elif isinstance(rep, dict) and "videos" in rep:
            n_result = len(rep.get("videos") or [])
        else:
            # PRO sin manifest.json: o fue "solo análisis" (a propósito, sin
            # vídeo) o el render reventó a mitad — ninguno de los dos es un
            # "trabajo ya renderizado" de verdad, así que no se muestra aquí
            # como si lo fuera (antes esto se veía como "Terminado" igual).
            continue
        out.append({
            "jid": jid, "state": "done", "stage": "Terminado", "pct": 100,
            "label": rep.get("source", jid), "source": rep.get("source", ""),
            "n_clips_result": n_result, "has_output": True,
            "analisis": _lee_analisis(cj.parent),
            "created": cj.stat().st_mtime, "finished": cj.stat().st_mtime,
            "elapsed": 0, "opts": {},
        })
    return out


def _all_jobs_public() -> list[dict]:
    seen = {j.jid for j in MGR.jobs.values()}
    rows = [j.to_public() for j in MGR.jobs.values()]
    rows += [d for d in _discovered_jobs() if d["jid"] not in seen]
    rows.sort(key=lambda d: -(d.get("finished") or d.get("created") or 0))
    for d in rows:                       # solo para pintar la lista: miniatura y tipo (PRO / Generador)
        jid = d["jid"]
        d["thumb"] = f"/salida/{jid}/pro.jpg" if (P["outbox"] / jid / "pro.jpg").is_file() else None
        d["tipo"] = "pro" if "--pro" in jid else ("gen" if "--videos" in jid else "")
        if "nombre" not in d:            # (los trabajos encontrados solo en disco no pasan por Job.to_public)
            d["nombre"] = _nombre_video(jid) if d["tipo"] == "pro" else ""
    return rows


# ===================================================================== rutas
@app.get("/")
def videos_page():
    """Generador de vídeos: "Generador" (por temática) + "Vídeos PRO"."""
    if not os.environ.get("GEMINI_API_KEY", "").strip() and not request.cookies.get("inicio_omitido"):
        return redirect("/inicio")                    # primera vez, sin clave de Gemini: pantalla de primeros pasos (ver clipper/inicio.py)
    from clipper import canales as ch, render, versiones, vision
    cfg = config.load()
    return render_template("videos.html", jobs=versiones.mis_videos(_all_jobs_public()),
                           busy=MGR.current is not None, canales=ch.lista(),
                           mejoras_max=int(cfg.get_path("videos.mejoras_max", 3)),
                           motor_pedido=str(cfg.get_path("output.motor", "auto")).lower(),
                           motor_activo=render.motor_activo(cfg), hay_gpu=render._nvenc_disponible(),
                           ia_ve_video=vision.activo(cfg))


@app.post("/api/opciones/motor")
def api_opciones_motor():
    """«Opciones» de Mis vídeos: renderizar por CPU o GPU (pedido del autor, 2026-09-22, pensando
    en abrir esto a gente sin GPU). Se escribe en config.yaml (solo esa línea) y se aplica desde
    el PRÓXIMO vídeo que se genere o mejore — no hace falta reiniciar el panel."""
    valor = str((request.get_json(silent=True) or {}).get("motor") or "").strip().lower()
    if not config.guarda_motor(valor):
        return jsonify({"ok": False, "error": "Valor inválido (auto, gpu o cpu)."}), 400
    from clipper import render
    cfg = config.load()
    return jsonify({"ok": True, "motor_pedido": valor, "motor_activo": render.motor_activo(cfg), "hay_gpu": render._nvenc_disponible()})


@app.post("/api/opciones/vision")
def api_opciones_vision():
    """«Opciones» de Mis vídeos: que la IA VEA el vídeo (Gemini Flash-Lite mira una copia pequeña y se lo cuenta al
    director) o no. Experimento del autor (2026-09-24) para comparar el mismo vídeo con y sin la pasada. Como GPU/CPU: se
    escribe en config.yaml (solo esa línea) y vale desde el PRÓXIMO análisis, sin reiniciar el panel."""
    dato = (request.get_json(silent=True) or {}).get("activo")
    if not isinstance(dato, bool):
        return jsonify({"ok": False, "error": "Falta «activo» (true o false)."}), 400
    if not config.guarda_vision(dato):
        return jsonify({"ok": False, "error": "No encuentro la línea «ia_ve_video» en config.yaml."}), 500
    from clipper import vision
    return jsonify({"ok": True, "activo": vision.activo(config.load())})


@app.post("/videos/generar")
def videos_generar():
    src = (request.form.get("source") or "").strip()
    if not src:
        return redirect(url_for("videos_page"))
    opts = {
        "modo": "video",
        "contexto": (request.form.get("contexto") or "").strip()[:200],
        "max": request.form.get("max") or None,
        "min_min": request.form.get("min_min") or None,
        "max_min": request.form.get("max_min") or None,
        "subs": bool(request.form.get("subs")),
        "solo_analisis": bool(request.form.get("solo_analisis")),
    }
    job = MGR.submit(src, opts)
    return redirect(url_for("job_page", jid=job.jid))


# Extensiones de video que ffprobe/ffmpeg leen sin problema. No es una lista de
# seguridad (esto es un panel local, sin usuarios ajenos): es para que un
# archivo con la extension cambiada por error no llegue disfrazado hasta el
# pipeline y falle 40 minutos despues, a medio transcribir.
_EXT_VIDEO_OK = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"}


@app.post("/videos/subir")
def videos_subir():
    """Sube un archivo de video (tu tutorial grabado) a inbox/, en vez de
    tener que pegar una URL o escribir la ruta a mano."""
    f = request.files.get("archivo")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No llego ningun archivo."}), 400
    nombre = secure_filename(f.filename) or "video.mp4"
    ext = Path(nombre).suffix.lower()
    if ext not in _EXT_VIDEO_OK:
        return jsonify({"ok": False, "error": f"'{ext}' no es un formato de video "
                        f"que reconozca (uso: {', '.join(sorted(_EXT_VIDEO_OK))})."}), 400

    P["inbox"].mkdir(parents=True, exist_ok=True)
    destino = P["inbox"] / nombre
    # si ya existe un archivo con ese nombre, no lo piso: le pongo -2, -3...
    base, suf = destino.stem, destino.suffix
    i = 2
    while destino.exists():
        destino = P["inbox"] / f"{base}-{i}{suf}"
        i += 1
    tmp = destino.with_suffix(destino.suffix + ".subiendo")
    try:
        # FileStorage.save() escribe en trozos (no carga el archivo entero en
        # RAM), asi que un tutorial de varios GB no revienta la memoria.
        f.save(tmp)
        tmp.replace(destino)
    except Exception as exc:                                # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return jsonify({"ok": False, "error": f"No se pudo guardar: {exc}"}), 500

    tam = destino.stat().st_size
    clipper_log(f"Video subido para el generador de videos: {destino.name} "
                f"({tam / 1024**2:.0f} MB).")
    return jsonify({"ok": True, "path": str(destino), "nombre": destino.name,
                    "bytes": tam})


@app.post("/videos/generar-pro")
def videos_generar_pro():
    src = (request.form.get("source") or "").strip()
    canal = (request.form.get("canal") or "").strip()
    if not src or not canal or canal not in (config.load().get_path("videos.canales") or {}):
        return redirect(url_for("videos_page"))       # el HTML ya lo exige; esto es solo el respaldo
    opts = {
        "modo": "video_pro", "canal": canal,
        "solo_analisis": bool(request.form.get("solo_analisis")),
    }
    job = MGR.submit(src, opts)                        # (el autor, 12-oct-2026: ya no se enlaza una idea a la grabación: el director confía en lo que DICE el vídeo)
    return redirect(url_for("job_page", jid=job.jid))


@app.get("/videos/aprendizaje")
def videos_aprendizaje():
    """Qué temáticas mueven la aguja (aprendizaje propio del modo "Generador")."""
    cfg = config.load()
    juego = (request.args.get("juego") or "").strip()
    if not juego:
        ctx = (request.args.get("contexto") or "").strip()
        juego = safe_slug(ctx, 24) if ctx else "general"
    try:
        prof = taste_videos.profile(cfg, juego)
        prof["sugerencias"] = taste_videos.sugerencias(cfg, juego)
        return jsonify(prof)
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"listo": False, "error": str(exc)})


def _motivo_no_mejorable(jid: str, base: str) -> str:
    """'' si esta versión se puede mejorar; si no, el motivo en claro (necesita el original y el plan guardado de la IA)."""
    from clipper import director
    job = MGR.jobs.get(jid)
    if not job or job.opts.get("modo") != "video_pro":
        return "Solo se puede mejorar un vídeo que se generó desde este panel."
    if not Path(job.source).is_file():
        return f"El archivo ORIGINAL ya no está ({Path(job.source).name}): la mejora se hace sobre el original."
    if director.cargar(P["work"] / f"{base}--pro", _slug_plan(jid)) is None:
        return ("Esta versión no tiene guardado el plan de la IA (es muy antigua o salió sin analizar): "
                "dale «Reshuffle» y mejora esa.")
    return ""


def _mejoras_info(jid: str) -> dict:
    """Cuántas versiones tiene este vídeo y cuántas mejoras con IA quedan (para el botón «¿Quieres mejorarle algo?»).
    `puede`/`motivo`: si ESTA versión se puede mejorar de verdad."""
    from clipper import versiones
    p = versiones.parse(jid)
    if not p:
        return {}
    fam = versiones.familia(_all_jobs_public(), p[0])
    maximo = int(config.load().get_path("videos.mejoras_max", 3))
    yo = next((f for f in fam if f["jid"] == jid), {})
    motivo = _motivo_no_mejorable(jid, p[0])
    return {"base": p[0], "versiones": len(fam), "siguiente": versiones.siguiente(fam),
            "usadas": versiones.mejoras_usadas(fam), "maximo": maximo,
            "restantes": versiones.restantes(fam, maximo), "en_marcha": versiones.en_marcha(fam),
            "estado": yo.get("state", ""), "puede": not motivo, "motivo": motivo}


@app.get("/api/job/<jid>/mejoras")
def api_mejoras_info(jid: str):
    return jsonify(_mejoras_info(jid))


# --------------------------------------------- 📌 reglas permanentes del director (clipper/reglas.py)
def _canal_del_trabajo(jid: str) -> str:
    """El canal de esa versión: el del trabajo y, si el panel ya no lo tiene, el que quedó en su manifest."""
    job = MGR.jobs.get(jid)
    if job is not None:
        return str(job.opts.get("canal") or "")
    try:
        return str(json.loads((P["outbox"] / jid / "manifest.json").read_text(encoding="utf-8"))[0].get("tema") or "")
    except (OSError, ValueError, IndexError, AttributeError):
        return ""


def _reglas_de(jid: str) -> dict:
    from clipper import reglas as reglas_mod
    canal = _canal_del_trabajo(jid)
    return {"ok": True, "canal": canal, "reglas": reglas_mod.lista(canal), "max": reglas_mod.maximo(config.load())}


@app.get("/api/job/<jid>/reglas")
def api_reglas(jid: str):
    """Las reglas permanentes del director de ESTE canal (activas y en pausa), para la lista de la tarjeta de mejora."""
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    return jsonify(_reglas_de(jid))


@app.post("/api/job/<jid>/reglas")
def api_reglas_agrega(jid: str):
    """Una regla nueva SIN crear versión: `{"texto": "No cortes mis risas"}`. Vale para todos los vídeos siguientes del canal."""
    from clipper import reglas as reglas_mod
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    r = reglas_mod.agrega(_canal_del_trabajo(jid), (request.get_json(silent=True) or {}).get("texto"),
                          origen="manual", cfg=config.load())
    if not r["ok"]:
        return jsonify(r), (409 if r.get("duplicada") else 400)
    return jsonify({**_reglas_de(jid), "regla": r["regla"]})


@app.post("/api/job/<jid>/reglas/<rid>")
def api_reglas_edita(jid: str, rid: str):
    """Cambia el texto (`{"texto"}`) y/o pausa-reanuda (`{"activa": false}`) una regla."""
    from clipper import reglas as reglas_mod
    if not re.fullmatch(r"[\w.\-]+", jid or "") or not re.fullmatch(r"r[0-9a-f]{4,12}", rid or ""):
        abort(404)
    d = request.get_json(silent=True) or {}
    r = reglas_mod.edita(rid, texto=d.get("texto") if "texto" in d else None,
                         activa=bool(d["activa"]) if "activa" in d else None)
    if not r["ok"]:
        return jsonify(r), 400
    return jsonify(_reglas_de(jid))


@app.post("/api/job/<jid>/reglas/<rid>/borrar")
def api_reglas_borra(jid: str, rid: str):
    """Quita una regla (pasa a «borradas» dentro de work/reglas_director.json: nada se destruye)."""
    from clipper import reglas as reglas_mod
    if not re.fullmatch(r"[\w.\-]+", jid or "") or not re.fullmatch(r"r[0-9a-f]{4,12}", rid or ""):
        abort(404)
    r = reglas_mod.borra(rid)
    if not r["ok"]:
        return jsonify(r), 400
    return jsonify(_reglas_de(jid))


def _regla_de_mejora(jid: str, texto: str, regla: str) -> tuple[dict | None, tuple | None]:
    """La casilla «📌 Recordar esto para todos mis próximos vídeos» de «¿Quieres mejorarle algo?»: guarda la regla (`regla` =
    lo que el autor confirmó en el aviso; si viene vacío, el propio comentario) ANTES de lanzar la versión. -> (lo que se le
    cuenta a la página, o None; una respuesta de error si NO debe seguir). Una regla repetida no frena la mejora."""
    from clipper import reglas as reglas_mod
    r = reglas_mod.agrega(_canal_del_trabajo(jid), regla or texto, origen=f"mejora:{jid}", cfg=config.load())
    if r["ok"]:
        return {"ok": True, "texto": r["regla"]["texto"], "id": r["regla"]["id"]}, None
    if r.get("duplicada"):
        return {"ok": False, "duplicada": True, "error": r["error"]}, None
    return None, (jsonify({"ok": False, "error": r["error"]}), 400)


@app.get("/job/<jid>")
def job_page(jid: str):
    job = MGR.jobs.get(jid)
    if not job:
        disc = {d["jid"]: d for d in _discovered_jobs()}.get(jid)
        if not disc:
            abort(404)
        return render_template("job.html", job=disc, discovered=True)
    return render_template("job.html", job=job.to_public(), discovered=False)


@app.get("/job/<jid>/descargar")
def job_descargar(jid: str):
    """Descarga el vídeo editado de esta versión con un nombre legible (el del título, no «pro.mp4»)."""
    from clipper import versiones
    p = versiones.parse(jid)
    if not p:                                   # solo los vídeos PRO (`<base>--pro[-vN]`); nada de rutas raras
        abort(404)
    out = P["outbox"] / jid
    try:
        entrada = json.loads((out / "manifest.json").read_text(encoding="utf-8"))[0]
    except (OSError, ValueError, IndexError):
        abort(404)
    mp4 = out / entrada.get("archivo", "pro.mp4")
    if not mp4.is_file():
        abort(404)
    # el nombre del vídeo (fecha del render + dos palabras clave, `_nombre_video`); sin él, el título
    base_nombre = _nombre_video(jid) or str(entrada.get("titulo") or (p[0] if p else jid))
    titulo = re.sub(r'[\\/:*?"<>|\r\n]+', " ", base_nombre).strip()[:90] or jid
    nombre = f"{titulo}{' - v' + str(p[1]) if p and p[1] > 1 else ''}.mp4"
    return send_file(mp4, as_attachment=True, download_name=nombre, conditional=True)


@app.post("/api/job/<jid>/mejorar")
def api_mejorar(jid: str):
    """«¿Quieres mejorarle algo?»: el autor escribe qué no le cuadra y la IA revisa el plan de ESTA versión con ese
    comentario (ver director.analiza(revision=…)). Sale una versión nueva y aparte; las anteriores no se tocan."""
    from clipper import versiones
    datos, error = _datos_editor(jid)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    texto = re.sub(r"[ \t]+", " ", str((request.get_json(silent=True) or {}).get("texto") or "")).strip()
    if len(texto) < 6:
        return jsonify({"ok": False, "error": "Cuéntale con unas palabras qué quieres cambiar."}), 400
    if len(texto) > 2000:
        return jsonify({"ok": False, "error": "Es muy largo (máximo 2000 letras): resúmelo un poco."}), 400
    job = MGR.jobs[jid]
    base, _v = versiones.parse(jid)
    fam = versiones.familia(_all_jobs_public(), base)
    maximo = int(config.load().get_path("videos.mejoras_max", 3))
    if versiones.restantes(fam, maximo) <= 0:
        return jsonify({"ok": False, "error": f"Ya usaste las {maximo} mejoras de este vídeo. Para retoques pequeños usa "
                        "«Editar / cortar el vídeo» (no gasta IA), o sube el vídeo otra vez para un análisis nuevo."}), 400
    if versiones.en_marcha(fam):
        return jsonify({"ok": False, "error": "Ya hay una versión de este vídeo procesándose: espera a que termine."}), 409
    cuerpo = request.get_json(silent=True) or {}
    regla_info = None
    if cuerpo.get("recordar"):                # 📌 «Recordar esto para todos mis próximos vídeos» (el aviso ya lo confirmó)
        regla_info, malo = _regla_de_mejora(jid, texto, re.sub(r"[ \t]+", " ", str(cuerpo.get("regla") or "")).strip())
        if malo is not None:
            return malo
    siguiente = versiones.siguiente(fam)
    opts = {k: v for k, v in job.opts.items() if k not in ("plan_de", "cortes", "editado_de", "mejora", "mejora_de")}
    opts.update({"version": siguiente, "plan_de": _slug_plan(jid), "mejora": texto, "mejora_de": jid})
    nuevo = MGR.submit(job.source, opts)
    return jsonify({"ok": True, "jid": nuevo.jid, "version": siguiente,
                    "restantes": versiones.restantes(fam, maximo) - 1, "regla": regla_info,
                    "url": url_for("versiones_page", base=base) + f"#v{siguiente}"})


@app.get("/video/<base>")
def versiones_page(base: str):
    """Todas las versiones de un vídeo PRO, una debajo de otra, cada una con su botón para mejorarla."""
    from clipper import versiones
    fam = versiones.familia(_all_jobs_public(), base)
    if not fam:
        abort(404)
    for f in fam:
        out = P["outbox"] / f["jid"]
        an = _lee_analisis(out) or {}
        mej = an.get("mejora") or {}
        try:
            man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))[0]
        except (OSError, ValueError, IndexError):
            man = {}
        archivo = man.get("archivo") or "pro.mp4"
        f["tiene_video"] = (out / archivo).is_file()
        f["video_url"] = f"/salida/{f['jid']}/{archivo}"
        f["poster"] = f"/salida/{f['jid']}/" + (man.get("miniatura") or "pro.jpg")
        f["archivo"] = archivo
        f["duracion"] = man.get("duracion")
        f["titulo"] = man.get("titulo") or ""
        f["titulos"] = man.get("titulos_sugeridos") or []
        f["titulos_anteriores"] = man.get("titulos_anteriores") or []      # los sets de antes del «↻ Otros 3»
        # lo mismo que antes solo salía en "Detalles" (pedido del autor, 2026-09-23: verlo aquí mismo, sin ese clic)
        f["eventos_visuales"] = man.get("eventos_visuales") or []
        f["ritmo"] = man.get("ritmo")                # el tramo más largo sin cambios, antes/después de las palabras clave
        f["miniatura_prompts"] = man.get("miniatura_prompts") or []
        f["miniatura_titulares"] = man.get("miniatura_titulares") or []      # lo que escribe el programa con Rubik (no la IA)
        if "shorts" in man:                          # (una versión de antes de los Shorts no los tiene: no enseña nada)
            f["shorts"], f["shorts_motivo"] = man.get("shorts") or [], man.get("shorts_motivo") or ""
        f["descripcion_yt"] = man.get("descripcion") or ""
        opts_f = f.get("opts") or {}          # (una mejora que falló no llegó a escribir analisis_ia.json: su comentario está en las opciones)
        f["tipo_v"] = "mejora" if (mej or opts_f.get("mejora")) else "cortes" if opts_f.get("cortes") else "primera" if f["version"] == 1 else "nuevo"
        f["comentario"] = mej.get("texto") or str(opts_f.get("mejora") or "")
        f["sin_cambios"] = bool(mej.get("sin_cambios"))
        f["nota"] = mej.get("nota") or ""
        f["tokens"] = (an.get("tokens_entrada"), an.get("tokens_salida"))
        f["vision"] = an.get("vision")         # si la IA VIO el vídeo en ese análisis (para comparar con y sin)
        f["reglas"] = an.get("reglas") or []   # 📌 las reglas permanentes del autor que leyó el director en ese análisis
        f["nombres"] = man.get("nombres")      # 🛡 los nombres de lo que sale en pantalla que se corrigieron o se quitaron
    maximo = int(config.load().get_path("videos.mejoras_max", 3))
    hechas = [f for f in fam if f["state"] == "done" and f["tiene_video"]]
    puede = {f["jid"]: _motivo_no_mejorable(f["jid"], base) for f in hechas}
    return render_template("versiones.html", base=base, versiones=fam, maximo=maximo, puede=puede,
                           usadas=versiones.mejoras_usadas(fam), restantes=versiones.restantes(fam, maximo),
                           siguiente=versiones.siguiente(fam),
                           en_marcha=versiones.en_marcha(fam), ultima=(hechas[-1]["jid"] if hechas else ""),
                           nombre=next((n for n in (_nombre_video(f["jid"]) for f in reversed(fam)) if n),
                                       Path(fam[-1].get("source") or base).name))


@app.get("/api/muletillas/<base>")
def api_muletillas(base: str):
    """«🗣️ Tus muletillas» de `/video/<base>` (pedido del autor, 2026-09-24: mejorar cómo habla). Por código, sobre la
    transcripción que ya existe: gratis, sin Gemini (`clipper/muletillas.py`)."""
    from clipper import muletillas
    if not re.fullmatch(r"[\w.\-]+", base or ""):
        abort(404)
    work = muletillas.carpeta(P["work"], base)          # su carpeta de trabajo, o la copia del historial si lo borraste
    datos = muletillas.informe(P["work"], base, pausas=muletillas.pausas_de(work, config.load()))
    if datos is None:
        if (P["work"] / f"{base}--pro").is_dir():
            return jsonify({"ok": False, "error": "Este vídeo aún no tiene transcripción."})
        return jsonify({"ok": False, "error": "La transcripción de este vídeo se borró del PC junto con él, así que ya no "
                                              "puedo contar sus muletillas. Desde ahora, al publicar un vídeo o borrarlo, "
                                              "su transcripción se guarda aparte."})
    return jsonify({"ok": True, **datos})


@app.get("/api/jobs")
def api_jobs():
    return jsonify(_all_jobs_public())


@app.get("/api/job/<jid>")
def api_job(jid: str):
    job = MGR.jobs.get(jid)
    if job:
        return jsonify(job.to_public())
    disc = {d["jid"]: d for d in _discovered_jobs()}.get(jid)
    return jsonify(disc) if disc else (abort(404))


@app.post("/api/job/<jid>/cancel")
def api_cancel(jid: str):
    return jsonify({"ok": MGR.cancel(jid)})


@app.post("/api/job/<jid>/pause")
def api_pause(jid: str):
    return jsonify({"ok": MGR.pause(jid)})


@app.post("/api/job/<jid>/resume")
def api_resume(jid: str):
    return jsonify({"ok": MGR.resume(jid)})


def _original_que_falta(job) -> str:
    """'' si la grabación original de este Vídeo PRO sigue en el PC; si no, el aviso en claro. el autor borra grabaciones de
    `inbox/` para liberar espacio (2026-09-26): sin esto, «Reintentar» o «Reshuffle» encolaban un trabajo que fallaba con
    un error de ffprobe. Los Shorts no pasan por aquí (su flujo no se toca)."""
    if job is None or job.opts.get("modo") != "video_pro" or Path(job.source).is_file():
        return ""
    return (f"La grabación original ya no está en el PC ({Path(job.source).name}): sin ella no se puede volver a analizar "
            "ni a renderizar este vídeo. El vídeo terminado y el Historial siguen igual.")


@app.post("/api/job/<jid>/retry")
def api_retry(jid: str):
    falta = _original_que_falta(MGR.jobs.get(jid))
    if falta:
        return jsonify({"ok": False, "error": falta}), 400
    job = MGR.retry(jid)
    return jsonify({"ok": bool(job), "jid": job.jid if job else None})


@app.post("/api/job/<jid>/reshuffle")
def api_reshuffle(jid: str):
    """'Reshuffle': para un Vídeo PRO ya terminado, vuelve a mandarle el
    MISMO archivo al director (Gemini puede decidir distinto la segunda
    vez) y renderiza en una carpeta aparte (v2, v3...) que NO pisa la
    anterior — para poder comparar versiones. Reusa la transcripción
    (lo caro) pero el plan y el vídeo son de cero."""
    job = MGR.jobs.get(jid)
    if not job or job.opts.get("modo") != "video_pro":
        return jsonify({"ok": False, "error": "Esto solo existe para un Vídeo PRO."}), 400
    falta = _original_que_falta(job)
    if falta:
        return jsonify({"ok": False, "error": falta}), 400
    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    siguiente = 1
    for j in MGR.jobs:
        if j != f"{base}--pro" and not re.match(rf"^{re.escape(base)}--pro-v\d+$", j):
            continue
        m = re.search(r"-v(\d+)$", j)
        siguiente = max(siguiente, (int(m.group(1)) if m else 1) + 1)
    # un reshuffle es un análisis NUEVO de Gemini: no arrastra el plan ni los
    # cortes manuales de una versión editada (eso lo hace el editor de cortes)
    opts = {k: v for k, v in job.opts.items() if k not in ("plan_de", "cortes", "editado_de", "mejora", "mejora_de")}
    opts["version"] = siguiente
    nuevo = MGR.submit(job.source, opts)
    return jsonify({"ok": True, "jid": nuevo.jid})


# ------------------------------------------------------ editor de cortes
def _slug_plan(jid: str) -> str:
    """'<base>--pro' -> 'pro'; '<base>--pro-v7' -> 'pro-v7' (el nombre del plan
    del director cacheado en work/<base>--pro/director_<slug>.json)."""
    m = re.search(r"--pro(-v\d+)?$", jid)
    return "pro" + (m.group(1) or "") if m else "pro"


def _datos_editor(jid: str) -> tuple[dict | None, str | None]:
    """Todo lo que el editor necesita de un vídeo PRO terminado, o el motivo
    (en claro) de por qué todavía no se puede editar."""
    job = MGR.jobs.get(jid)
    if not job or job.opts.get("modo") != "video_pro":
        return None, "El editor de cortes solo existe para un Vídeo PRO."
    if job.state != "done":
        return None, "El vídeo todavía no terminó: cuando esté listo podrás editarlo."
    try:
        entrada = json.loads((job.out_dir / "manifest.json").read_text(encoding="utf-8"))[0]
    except (OSError, ValueError, IndexError):
        return None, "No encuentro el manifest.json de este vídeo (¿lo borraste?)."
    mp4 = job.out_dir / entrada["archivo"]
    if not mp4.is_file():
        return None, f"No encuentro el vídeo {entrada['archivo']} en su carpeta."
    if not Path(job.source).is_file():
        return None, ("El archivo ORIGINAL ya no está (" + Path(job.source).name + "): el corte "
                      "se aplica sobre el original y no se puede sin él.")
    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    from clipper import director
    if director.cargar(P["work"] / f"{base}--pro", _slug_plan(jid)) is None:
        return None, ("Esta versión no tiene el plan de Gemini guardado (salió sin analizar o es "
                      "muy antigua). Dale «Reshuffle» primero y edita esa versión.")
    try:
        meta = pipeline.ingest.probe(mp4)
    except Exception as exc:                                  # noqa: BLE001
        return None, f"No pude leer el vídeo ({exc})."
    mver = re.search(r"-v(\d+)$", jid)
    return {
        "jid": jid, "archivo": entrada["archivo"], "titulo": entrada.get("titulo") or job.label,
        "duracion": float(meta["duration"]), "fps": round(float(meta.get("fps") or 60)),
        "eventos": entrada.get("eventos_visuales") or [],
        "url": f"/salida/{jid}/{entrada['archivo']}",
        "poster": f"/salida/{jid}/" + (entrada.get("miniatura") or "pro.jpg"),
        "version": int(mver.group(1)) if mver else 1,
        "segments": entrada.get("segments") or [],
    }, None


@app.get("/job/<jid>/editar")
def editor_page(jid: str):
    datos, error = _datos_editor(jid)
    job = MGR.jobs.get(jid)
    if error or not job:
        return render_template("editor.html",
                               job=job.to_public() if job else {"jid": jid, "label": jid},
                               datos=None, error=error or "No conozco este trabajo."), (200 if job else 404)
    datos = {k: v for k, v in datos.items() if k != "segments"}     # el navegador no lo necesita
    return render_template("editor.html", job=job.to_public(), datos=datos, error=None)


@app.post("/api/job/<jid>/editar")
def api_editar(jid: str):
    """Editor de cortes: recibe los cortes en segundos del VÍDEO FINAL que ves
    en el editor, los pasa a segundos del ORIGINAL y crea una versión NUEVA
    (v13, v14...) que parte del plan de Gemini de esta versión + tus cortes."""
    datos, error = _datos_editor(jid)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    job = MGR.jobs[jid]
    body = request.get_json(silent=True) or {}
    segs = [(float(a), float(b)) for a, b in datos["segments"]]
    dur_final = sum(b - a for a, b in segs)

    crudos = body.get("cortes")
    if not isinstance(crudos, list) or not crudos:
        return jsonify({"ok": False, "error": "No llegó ningún corte."}), 400
    if len(crudos) > 80:
        return jsonify({"ok": False, "error": "Demasiados cortes de una vez (máximo 80)."}), 400
    cortes_f: list[tuple[float, float]] = []
    for c in crudos:
        try:
            a, b = float(c["a"]), float(c["b"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "Un corte viene mal formado (hace falta inicio y fin)."}), 400
        if not (0 <= a < b <= dur_final + 0.1):
            return jsonify({"ok": False, "error": f"El corte {a:.2f}–{b:.2f} s queda fuera del vídeo "
                            f"(dura {dur_final:.2f} s) o el inicio no es menor que el fin."}), 400
        cortes_f.append((max(0.0, a), min(dur_final, b)))
    cortes_f = pipeline._fusiona_cortes(cortes_f)
    quitado = sum(b - a for a, b in cortes_f)
    if dur_final - quitado < 5.0:
        return jsonify({"ok": False, "error": "Con esos cortes el vídeo quedaría de menos de 5 segundos."}), 400

    try:
        fps_src = float(pipeline.ingest.probe(Path(job.source)).get("fps") or 60)
    except Exception:                                          # noqa: BLE001
        fps_src = 60.0
    cortes_s: list[list[float]] = []
    for a_f, b_f in cortes_f:
        a_s = pipeline.tiempo_original(a_f, segs, "inicio")
        b_s = pipeline.tiempo_original(b_f, segs, "fin")
        a_s, b_s = round(a_s * fps_src) / fps_src, round(b_s * fps_src) / fps_src   # rejilla de frames del original
        if b_s - a_s >= 0.1:
            cortes_s.append([round(a_s, 4), round(b_s, 4)])
    if not cortes_s:
        return jsonify({"ok": False, "error": "Esos cortes son demasiado cortos (menos de 0.1 s)."}), 400

    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    siguiente = 1
    for j in MGR.jobs:
        if j != f"{base}--pro" and not re.match(rf"^{re.escape(base)}--pro-v\d+$", j):
            continue
        m = re.search(r"-v(\d+)$", j)
        siguiente = max(siguiente, (int(m.group(1)) if m else 1) + 1)
    opts = {k: v for k, v in job.opts.items() if k not in ("plan_de", "cortes", "editado_de", "mejora", "mejora_de")}
    opts.update({"version": siguiente, "plan_de": _slug_plan(jid), "cortes": cortes_s, "editado_de": jid})
    nuevo = MGR.submit(job.source, opts)
    return jsonify({"ok": True, "jid": nuevo.jid, "cortes": len(cortes_s), "segundos": round(quitado, 2)})


@app.post("/api/job/<jid>/abrir-carpeta")
def api_open_folder(jid: str):
    folder = P["outbox"] / jid
    if folder.is_dir() and hasattr(os, "startfile"):
        os.startfile(str(folder))                        # noqa: S606  (server local)
        return jsonify({"ok": True})
    return jsonify({"ok": False})


@app.get("/job/<jid>/stream")
def job_stream(jid: str):
    job = MGR.jobs.get(jid)
    log_path = (job.log_path if job else P["work"] / jid / "webui.log")

    def gen():
        if job is None and not log_path.exists():
            yield "data: (este trabajo se procesó fuera de la UI; no hay registro en vivo)\n\n"
            yield "event: done\ndata: done\n\n"
            return
        pos = 0
        idle = 0
        primera = True
        # Un vídeo largo deja miles de lineas de transcripcion. Nadie las lee,
        # y pintarlas le cuesta al navegador mas que todo lo demas junto.
        TOPE_LOG = int(config.load().get_path("ui.log_lineas_max", 800) or 800)
        while True:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
            except OSError:
                chunk = ""
            if chunk:
                # UN SOLO evento con todo el trozo, no uno por linea.
                lineas = chunk.splitlines()
                if primera and len(lineas) > TOPE_LOG:
                    ocultas = len(lineas) - TOPE_LOG
                    lineas = ([f"(... {ocultas} lineas anteriores omitidas: el"
                               f" registro completo sigue en"
                               f" work/{jid}/webui.log ...)", ""]
                              + lineas[-TOPE_LOG:])
                primera = False
                if lineas:
                    yield "".join(f"data: {ln}\n" for ln in lineas) + "\n"
                idle = 0
            else:
                idle += 1
            j = MGR.jobs.get(jid)
            if j and j.state == "paused":                # en pausa: no cierres el stream
                idle = 0
            if j and j.state in ("done", "error", "cancelled"):
                yield f"event: done\ndata: {j.state}\n\n"
                return
            if not j and log_path.exists():              # job descubierto, ya estático
                yield "event: done\ndata: done\n\n"
                return
            if idle > 1200:                              # ~10 min sin nada
                yield "event: done\ndata: timeout\n\n"
                return
            time.sleep(0.5)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/job/<jid>/inventario")
def job_inventario(jid: str):
    """Que queda de verdad en el disco de este trabajo. Solo cuenta ficheros,
    no abre ninguno: es instantaneo."""
    carpeta = P["outbox"] / jid
    if not carpeta.is_dir():
        return jsonify({"carpeta": False, "manifest": 0, "generados": 0,
                        "mp4": 0, "jpg": 0})

    def _n(fichero, clave=None):
        try:
            d = json.loads((carpeta / fichero).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if isinstance(d, list):
            return len(d)
        if isinstance(d, dict) and clave:
            v = d.get(clave)
            return len(v) if isinstance(v, list) else 0
        return 0

    sueltos = sorted(f.name for f in carpeta.glob("*.mp4"))
    return jsonify({
        "carpeta": True,
        "manifest": _n("manifest.json"),
        "generados": _n("videos.json", "videos"),
        "mp4": len(sueltos),
        "jpg": sum(1 for _ in carpeta.glob("*.jpg")),
        # los nombres, para poder ofrecerte borrarlos de un clic sin tener que
        # abrir el explorador de Windows a buscarlos
        "sueltos": sueltos[:400],
        "bytes": sum(f.stat().st_size for f in carpeta.glob("*.mp4")
                     if f.is_file()),
    })


@app.get("/salida/<path:rel>")
def salida_file(rel: str):
    return send_from_directory(P["outbox"], rel, conditional=True)


@app.get("/historial-miniatura/<path:rel>")
def historial_miniatura(rel: str):
    """La copia chica de la miniatura que guarda `clipper/historial.py` — sigue existiendo
    aunque hayas borrado `videos/<jid>/` con el vídeo grande."""
    return send_from_directory(historial.dir_miniaturas(), rel, conditional=True)


@app.get("/api/historial")
def api_historial():
    """La sección «Historial» de Mis vídeos: los vídeos SUBIDOS a YouTube (publicados o programados), sigan o no en el PC,
    con sus métricas (el autor, 2026-09-26: «subí ayer un vídeo y no está saliendo en historial… para ver las métricas… que
    solamente quede en el historial los que sí se subieron»). Estado, fechas, título y miniatura, los de YouTube
    (`historial.actualiza_desde_youtube`). `actualizando_youtube`: se está preguntando a YouTube ahora mismo -> la página
    vuelve a pedir la lista en unos segundos."""
    from clipper import metricas
    actualizando = _youtube_en_segundo_plano()
    filas = [f for f in historial.lista() if (f.get("youtube") or {}).get("video_id")]
    for f in filas:                              # los 4 cuadritos: lo último que se leyó (nunca se pregunta a YouTube aquí)
        f["metricas"] = metricas.resumen(f["base"])
    # los 6 cuadros grandes de arriba = la SUMA de todos los vídeos, de todas las páginas (el autor, 2026-10-11)
    return jsonify({"filas": filas, "totales": metricas.totales([f["metricas"] for f in filas]),
                    "actualizando_youtube": actualizando, "metricas_refresco": _estado_refresco_metricas()})


# ↻ de «Historial» (el autor, 2026-09-28: «que no sea algo automático… cada vez que le dé ahí, que cargue… las métricas de
# cada uno de los videos»): una vuelta por todos los vídeos del Historial, uno detrás de otro, en un hilo aparte
_HIST_MET: dict = {"hilo": None, "lock": threading.Lock(), "hechos": 0, "total": 0, "fallidos": 0, "fin": None}


def _estado_refresco_metricas() -> dict:
    h = _HIST_MET["hilo"]
    return {"en_marcha": h is not None and h.is_alive(), "hechos": _HIST_MET["hechos"], "total": _HIST_MET["total"],
            "fallidos": _HIST_MET["fallidos"], "fin": _HIST_MET["fin"]}


@app.post("/api/historial/metricas/refrescar")
def api_historial_metricas_refrescar():
    from clipper import metricas
    with _HIST_MET["lock"]:
        h = _HIST_MET["hilo"]
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True, **_estado_refresco_metricas()})
        filas = [f for f in historial.lista() if (f.get("youtube") or {}).get("video_id")]
        _HIST_MET.update(hechos=0, total=len(filas), fallidos=0, fin=None)

        def _run():
            for f in filas:
                try:
                    # (guarda también con qué se compara: el cambio desde el ↻ anterior, el autor 2026-09-28)
                    r = metricas.refresca_boton(f["base"], f["youtube"]["video_id"], duracion_s=f.get("duracion_s"))
                    if not r.get("ok"):
                        _HIST_MET["fallidos"] += 1
                except Exception as exc:                            # noqa: BLE001
                    _HIST_MET["fallidos"] += 1
                    clipper_log(f"AVISO: métricas de {f['base']}: {exc}")
                _HIST_MET["hechos"] += 1
            _HIST_MET["fin"] = time.time()
            clipper_log(f"Historial: métricas actualizadas de {len(filas)} vídeo(s)"
                        + (f" ({_HIST_MET['fallidos']} sin respuesta de YouTube)." if _HIST_MET["fallidos"] else "."))

        _HIST_MET["hilo"] = threading.Thread(target=_run, daemon=True)
        _HIST_MET["hilo"].start()
    return jsonify({"ok": True, **_estado_refresco_metricas()})


@app.get("/api/historial/metricas/estado")
def api_historial_metricas_estado():
    return jsonify(_estado_refresco_metricas())


_HIST_YT: dict = {"hilo": None, "lock": threading.Lock()}


def _youtube_en_segundo_plano() -> bool:
    """Pregunta a YouTube (en un hilo aparte, para no hacer esperar a la página) el estado, las fechas y el título de los
    vídeos del Historial a los que les toca — como mucho cada 15 min, o en cuanto a un programado le llega su hora — y,
    una vez por vídeo, su miniatura (el autor, 2026-09-26: «sale como publicado pero está programado», «las fechas… 100 % de
    YouTube»). Una petición por cada 50 vídeos (1 unidad de cuota). -> True si hay una lectura en marcha."""
    with _HIST_YT["lock"]:
        h = _HIST_YT["hilo"]
        if h is not None and h.is_alive():
            return True
        if not youtube.is_connected() or not historial.pendientes_yt():
            return False

        def _run():
            r = historial.actualiza_desde_youtube()
            if r.get("puestas"):
                clipper_log(f"Historial: {r['puestas']} miniatura(s) traída(s) de YouTube.")

        _HIST_YT["hilo"] = threading.Thread(target=_run, daemon=True)
        _HIST_YT["hilo"].start()
        return True


# ===================================================================== 💡 IDEAS DE VÍDEOS (clipper/ideas.py)
# La PRE-producción (el autor, 2026-09-26): 10 ideas con datos reales de tu canal y de YouTube, UNA petición al mejor Flash
# (solo con tu clic, y nunca con menos de 8 peticiones de los mejores modelos hoy). Se piden en un hilo aparte.
_IDEAS: dict = {"hilo": None, "lock": threading.Lock(), "paso": "", "error": "", "inicio": 0.0, "paso_ts": 0.0}     # (inicio / paso_ts: para la barra de progreso de la página)


@app.get("/ideas")
def ideas_page():
    return render_template("ideas.html")


def _canal_ideas(cfg, pedido=None) -> str:
    """El canal de la pestaña «Ideas»: el que se pide (si existe) o, si no, el de siempre (Mi canal) o el primero."""
    from clipper import canales as ch, ideas as idm
    slugs = [c["slug"] for c in ch.lista(cfg)]
    if pedido in slugs:
        return pedido
    defecto = idm._canal_por_defecto(cfg)
    return defecto if defecto in slugs else (slugs[0] if slugs else "")


@app.get("/api/ideas")
def api_ideas():
    from clipper import canales as ch, ideas as idm
    cfg = config.load()
    canal = _canal_ideas(cfg, request.args.get("canal"))
    p = idm.parametros(cfg, canal)
    try:
        idm.limpia_descartadas(cfg=cfg)                     # lo que antes se marcó «No me interesa» se elimina del todo (ahora existe «Eliminar»)
    except Exception as exc:                                # noqa: BLE001
        clipper_log(f"AVISO: no se pudieron limpiar las ideas descartadas ({exc}).")
    est = idm.estado()
    h = _IDEAS["hilo"]
    return jsonify({"canal": canal, "canales": [{"slug": c["slug"], "nombre": c["nombre"]} for c in ch.lista(cfg)],
                    "necesita_perfil": p["necesita_perfil"], "perfil_origen": p["perfil"].get("origen"), "tipos": p["tipos"],
                    "puede": idm.puede_generar(cfg, canal=canal), "refrescar": idm.puede_refrescar(cfg),
                    "en_marcha": bool(h is not None and h.is_alive()),
                    "paso": _IDEAS["paso"], "error": _IDEAS["error"], "nombres_tipo": idm.NOMBRE_TIPO,
                    "inicio": _IDEAS["inicio"], "paso_ts": _IDEAS["paso_ts"], "ahora": time.time(),
                    "descripcion_tipo": idm.DESCRIPCION_TIPO, "mezcla": p["mezcla"], "total": p["total"],
                    "tandas": [{**t, "ideas": [idm._con_estado(i, est) for i in t.get("ideas") or []]}
                               for t in idm.tandas(canal=canal)[:6]],
                    "tandas_total": len(idm.tandas(canal=canal)),
                    "semillas": idm.semillas(cfg, canal),          # TUS ideas favoritas (config.yaml): fijas arriba hasta que las grabes o las descartes
                    "por_grabar": idm.por_grabar(cfg=cfg)})


@app.post("/api/ideas/generar")
def api_ideas_generar():
    from clipper import ideas as idm
    cfg0 = config.load()
    canal = _canal_ideas(cfg0, (request.get_json(silent=True) or {}).get("canal"))
    with _IDEAS["lock"]:
        h = _IDEAS["hilo"]
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True})
        fr = idm.puede_generar(cfg0, canal=canal)
        if not fr["ok"]:
            return jsonify({"ok": False, "error": fr["motivo"], "necesita_perfil": bool(fr.get("necesita_perfil"))}), 409
        _IDEAS.update(paso="Arrancando…", error="", inicio=time.time(), paso_ts=time.time())

        def _paso(t):
            _IDEAS.update(paso=t, paso_ts=time.time())

        def _run():
            r = idm.genera(config.load(), progreso=_paso, canal=canal)
            _IDEAS.update(paso="", error="" if r.get("ok") else str(r.get("error") or ""))

        _IDEAS["hilo"] = threading.Thread(target=_run, daemon=True)
        _IDEAS["hilo"].start()
    return jsonify({"ok": True})


@app.post("/api/ideas/<idea_id>/marca")
def api_ideas_marca(idea_id: str):
    from clipper import ideas as idm
    if not re.fullmatch(r"[\w\-]+", idea_id or ""):
        abort(404)
    ok = idm.marca(idea_id, str((request.get_json(silent=True) or {}).get("estado") or ""))
    return jsonify({"ok": ok, "por_grabar": idm.por_grabar()}), (200 if ok else 400)


@app.post("/api/ideas/<idea_id>/guion")
def api_ideas_guion(idea_id: str):
    """«📝 Guion» de una idea: UNA petición a Flash-Lite (no toca las 20 de Flash)."""
    from clipper import ideas as idm
    if not re.fullmatch(r"[\w\-]+", idea_id or ""):
        abort(404)
    r = idm.guion(idea_id, config.load())
    return jsonify(r), (200 if r.get("ok") else 502)


_IDEAS_REFRESCO_LOCK = threading.Lock()


@app.post("/api/ideas/<idea_id>/refrescar")
def api_ideas_refrescar(idea_id: str):
    """«↻» de una idea de la lista (el autor, 2026-10-04: «que todos se puedan refrescar»): la cambia por otra del MISMO tipo con UNA
    petición a Flash-Lite (no toca las 20 diarias de los Flash), con los datos guardados de esa tanda y con su tope por día
    (`ideas.refrescos_max_dia`). Ver `clipper/ideas.py` → `refresca`. 200 = idea nueva; 409 = no se puede (tope, «Por grabar»…);
    404 = esa idea ya no está en la lista; 502 = la IA no respondió."""
    from clipper import ideas as idm
    if not re.fullmatch(r"[\w\-]+", idea_id or ""):
        abort(404)
    h = _IDEAS["hilo"]
    if h is not None and h.is_alive():
        return jsonify({"ok": False, "error": "Se está pidiendo una tanda nueva: cuando termine, refresca."}), 409
    cfg = config.load()
    with _IDEAS_REFRESCO_LOCK:                 # varios «↻» seguidos se atienden de uno en uno
        r = idm.refresca(idea_id, cfg)
    if r.get("ok"):
        http = 200
    elif r.get("codigo") == "no_esta":
        http = 404
    elif r.get("codigo") == "ia":
        http = 502
    else:
        http = 409
    return jsonify({**r, "refrescar": idm.puede_refrescar(cfg)}), http


@app.post("/api/ideas/tanda/<tid>/borrar")
def api_ideas_tanda_borrar(tid: str):
    """La ✖ junto a la fecha de cada tanda (el autor, 2026-10-05; 12-oct-2026: «se eliminan todas menos las que están por grabar»): la borra con sus ideas, guiones y datos, y SOLO se
    quedan las que están en «Por grabar». 200 = borrada; 404 = esa tanda ya no está. Ver `clipper/ideas.py` → `borra_tanda`."""
    from clipper import ideas as idm
    if not re.fullmatch(r"\d{8}-\d{6}", tid or ""):
        abort(404)
    with _IDEAS_REFRESCO_LOCK:                 # que un «↻» en marcha no la reescriba mientras se borra
        r = idm.borra_tanda(tid)
    http = 200 if r.get("ok") else 404 if r.get("codigo") == "no_esta" else 500
    return jsonify({**r, "por_grabar": idm.por_grabar()}), http


@app.post("/api/ideas/tandas/anteriores/borrar")
def api_ideas_tandas_anteriores_borrar():
    """«🗑 Borrar todas las anteriores» (el autor, 12-oct-2026): borra todas las tandas del canal menos la más reciente; solo sobrevive lo que está en «Por grabar»."""
    from clipper import ideas as idm
    cfg = config.load()
    canal = _canal_ideas(cfg, (request.get_json(silent=True) or {}).get("canal"))
    with _IDEAS_REFRESCO_LOCK:
        r = idm.borra_anteriores(canal)
    return jsonify({**r, "por_grabar": idm.por_grabar()})


@app.post("/api/ideas/<idea_id>/eliminar")
def api_ideas_eliminar(idea_id: str):
    """«🗑 Eliminar» una idea (el autor, 12-oct-2026): desaparece del todo (de su tanda, su guion y «Por grabar»). 200 = eliminada; 404 = ya no estaba."""
    from clipper import ideas as idm
    if not re.fullmatch(r"[\w\-]+", idea_id or ""):
        abort(404)
    with _IDEAS_REFRESCO_LOCK:
        r = idm.elimina_idea(idea_id, cfg=config.load())
    return jsonify({**r, "por_grabar": idm.por_grabar()}), (200 if r.get("ok") else 404)


# --- la configuración de las ideas de un canal: la primera vez que abre «Ideas» (el autor, 2026-10-04) y para editarla después
_IDEAS_PERFIL_LOCK = threading.Lock()


@app.get("/api/ideas/perfil")
def api_ideas_perfil():
    """Lo que necesita el asistente: lo que ya se sabe del canal (del asistente de «Nuevo canal»), su perfil si ya lo tiene, las
    opciones de «qué quieres lograr» y si este canal es el que está conectado a YouTube (para usar o no SUS datos)."""
    from clipper import ideas_perfil as pf, youtube as yt
    cfg = config.load()
    canal = _canal_ideas(cfg, request.args.get("canal"))
    try:
        canal_yt = ((yt.channel_info() or {}).get("channel") if yt.is_connected() else None)
    except Exception:                                          # noqa: BLE001
        canal_yt = None
    return jsonify({"canal": canal, "base": pf.base_del_canal(canal, cfg), "perfil": pf.de_canal(canal, cfg),
                    "objetivos": pf.OBJETIVOS, "canal_yt": canal_yt, "datos_propios_sugerido": pf.sugiere_datos_propios(canal, cfg, canal_yt),
                    "maximo_publicos": pf.MAX_VERTIENTES, "total_min": pf.TOTAL_MIN, "total_max": pf.TOTAL_MAX})


@app.post("/api/ideas/perfil/proponer")
def api_ideas_perfil_proponer():
    """«✨ Proponer mi configuración»: UNA petición a Flash-Lite con lo que contó la persona (o un punto de partida por código si la IA
    no responde). No guarda nada: la persona lo ajusta y lo guarda."""
    from clipper import ideas_perfil as pf
    cfg = config.load()
    d = request.get_json(silent=True) or {}
    canal = _canal_ideas(cfg, d.get("canal"))
    with _IDEAS_PERFIL_LOCK:
        r = pf.propone(canal, d, cfg)
    return jsonify(r)


@app.post("/api/ideas/perfil")
def api_ideas_perfil_guardar():
    from clipper import ideas_perfil as pf
    cfg = config.load()
    d = request.get_json(silent=True) or {}
    canal = _canal_ideas(cfg, d.get("canal"))
    perfil, err = pf.guarda(canal, d.get("perfil") or {})
    if err:
        return jsonify({"ok": False, "errores": err}), 400
    clipper_log(f"Configuración de las ideas guardada para el canal «{canal}».")
    return jsonify({"ok": True, "perfil": perfil, "canal": canal})


# ===================================================================== 📝 ARTÍCULO PARA TU WEB (clipper/articulo.py)
# Un vídeo terminado -> borrador de artículo SEO con capturas (el autor, 2026-09-26). UNA petición a Flash-Lite, en un hilo.
_ARTICULOS: dict = {"lock": threading.Lock(), "hilos": {}, "paso": {}, "error": {}}


def _articulo_estado(jid: str) -> dict:
    from clipper import articulo as artm
    h = _ARTICULOS["hilos"].get(jid)
    if h is not None and h.is_alive():
        return {"estado": "creando", "paso": _ARTICULOS["paso"].get(jid, "")}
    a = artm.lee(jid)
    err = _ARTICULOS["error"].get(jid, "")
    if a:
        return {"estado": "listo", "error": err,
                "articulo": {k: a.get(k) for k in ("title_seo", "meta_descripcion", "slug", "keyword", "h1", "markdown",
                                                   "html", "imagenes", "palabras", "avisos", "nombre_modelo", "ts")},
                "capturas": sorted(p.name for p in artm.ruta(jid).glob("img-*.jpg")),
                "sitio": _sitio_info(jid), "hero": _hero_estado(jid)}
    return {"estado": "error" if err else "no", "error": err}


# 🎨 Imagen destacada del artículo dibujada con la IA de Cloudflare, en blanco y negro y estilo cómic de tu web (clipper/imagen_articulo.py; el autor, 11-oct-2026)
_HEROES: dict = {"lock": threading.Lock(), "hilos": {}, "paso": {}, "error": {}, "aviso": {}}


def _hero_estado(jid: str) -> dict:
    try:
        from clipper import imagen_articulo as ia
        from clipper import miniaturas_ia as mia
        cfg = config.load()
        ok, motivo = mia.disponible(cfg)
        h = _HEROES["hilos"].get(jid)
        vivo = h is not None and h.is_alive()
        return {"disponible": ok, "motivo": "" if ok else motivo, "en_marcha": vivo, "paso": _HEROES["paso"].get(jid, "") if vivo else "",
                "error": _HEROES["error"].get(jid, ""), "aviso": _HEROES["aviso"].get(jid, ""), **ia.estado(jid),
                "cupo": {"hoy": ia.intentos_hoy(), "max": int(mia.parametros(cfg).get("articulo_max_dia", 10))}}
    except Exception as exc:                                     # noqa: BLE001 — un adorno: nunca rompe la pantalla del artículo
        return {"disponible": False, "motivo": str(exc)[:120], "en_marcha": False, "imagenes": [], "elegida": None}


@app.post("/api/articulo/<jid>/hero/generar")
def api_articulo_hero_generar(jid: str):
    """Dibuja UNA imagen más (la misma escena con otra semilla, o `nueva_idea`: escena nueva con Flash-Lite). En un hilo; la página lo sigue con el estado."""
    from clipper import imagen_articulo as ia
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    nueva = bool((request.get_json(silent=True) or {}).get("nueva_idea"))
    with _HEROES["lock"]:
        h = _HEROES["hilos"].get(jid)
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True})
        _HEROES["error"].pop(jid, None)
        _HEROES["aviso"].pop(jid, None)
        _HEROES["paso"][jid] = "Empezando…"

        def _run():
            try:
                r = ia.genera(jid, config.load(), nueva, progreso=lambda t: _HEROES["paso"].__setitem__(jid, t), canal=_canal_de(jid))
                if not r.get("ok"):
                    _HEROES["error"][jid] = str(r.get("error") or "")
                else:
                    _HEROES["aviso"][jid] = str((r.get("estado") or {}).get("aviso") or "")
            except Exception as exc:                             # noqa: BLE001
                _HEROES["error"][jid] = f"No se pudo dibujar ({str(exc)[:140]})."
            finally:
                _HEROES["paso"].pop(jid, None)

        _HEROES["hilos"][jid] = threading.Thread(target=_run, daemon=True)
        _HEROES["hilos"][jid].start()
    return jsonify({"ok": True})


@app.post("/api/articulo/<jid>/hero/elegir")
def api_articulo_hero_elegir(jid: str):
    """«⭐ Usar como destacada» (o `archivo: null` para quitarla): es la que «Mandar a tu web» pone como `heroImage`."""
    from clipper import imagen_articulo as ia
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    archivo = (request.get_json(silent=True) or {}).get("archivo")
    if archivo is not None and not re.fullmatch(r"hero-\d+\.jpg", str(archivo)):
        abort(400)
    if not ia.elegir(jid, archivo):
        return jsonify({"ok": False, "error": "Esa imagen ya no existe."}), 404
    return jsonify({"ok": True, "hero": _hero_estado(jid)})


def _sitio_info(jid: str) -> dict:
    """«📤 A tu web»: si el canal tiene su proyecto de Astro configurado (config.yaml `sitio`) y si este artículo ya se mandó (clipper/sitio_astro.py)."""
    try:
        from clipper import sitio_astro as sa
        return sa.info(config.load(), _canal_de(jid), jid)
    except Exception as exc:                                     # noqa: BLE001 — un adorno: nunca rompe la pantalla del artículo
        return {"configurado": False, "motivo": str(exc)[:120]}


@app.post("/api/articulo/<jid>/sitio")
def api_articulo_sitio(jid: str):
    """Escribe el artículo (con su cabecera, capturas y vídeo) en TU proyecto de Astro. Nunca pisa nada; nace como borrador."""
    from clipper import sitio_astro as sa
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    d = request.get_json(silent=True) or {}
    r = sa.exporta(jid, config.load(), _canal_de(jid), categoria=d.get("categoria"), borrador=d.get("borrador"), slug=d.get("slug") or None)
    return jsonify({**r, "sitio": _sitio_info(jid)}), (200 if r.get("ok") else 400)


@app.post("/api/articulo/<jid>/sitio/abrir")
def api_articulo_sitio_abrir(jid: str):
    """Abre en Visual Studio Code el último artículo mandado a tu web (la ruta sale de lo apuntado, no de la página)."""
    from clipper import sitio_astro as sa
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    r = sa.abre(jid)
    return jsonify(r), (200 if r.get("ok") else 400)


@app.get("/api/articulo/<jid>")
def api_articulo(jid: str):
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    return jsonify(_articulo_estado(jid))


@app.post("/api/articulo/<jid>/crear")
def api_articulo_crear(jid: str):
    from clipper import articulo as artm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    job = MGR.jobs.get(jid)
    if job and job.state in ("queued", "running", "paused"):
        return jsonify({"ok": False, "error": "Este vídeo se está procesando: cuando termine, crea el artículo."}), 409
    with _ARTICULOS["lock"]:
        h = _ARTICULOS["hilos"].get(jid)
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True})
        _ARTICULOS["error"].pop(jid, None)
        _ARTICULOS["paso"][jid] = "Leyendo el vídeo…"

        def _run():
            r = artm.crea(jid, config.load(), progreso=lambda t: _ARTICULOS["paso"].__setitem__(jid, t))
            if not r.get("ok"):
                _ARTICULOS["error"][jid] = str(r.get("error") or "")
            _ARTICULOS["paso"].pop(jid, None)

        _ARTICULOS["hilos"][jid] = threading.Thread(target=_run, daemon=True)
        _ARTICULOS["hilos"][jid].start()
    return jsonify({"ok": True})


@app.get("/api/articulo/<jid>/zip")
def api_articulo_zip(jid: str):
    """El artículo (Markdown, vista previa HTML y capturas) en un .zip, para llevarlo a tu web."""
    import io
    from clipper import articulo as artm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    carpeta = artm.ruta(jid)
    if not (carpeta / "articulo.md").is_file():
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(carpeta.iterdir()):
            if f.is_file() and f.suffix.lower() in (".md", ".html", ".jpg"):
                z.write(f, f.name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"articulo-{(artm.lee(jid) or {}).get('slug') or jid}.zip")


# ===================================================================== 🖼️ CARRUSEL PARA INSTAGRAM Y TIKTOK (clipper/carrusel.py)
# Un vídeo terminado -> diapositivas (Instagram 4:5, TikTok 9:16, PDF) con capturas del propio vídeo (el autor, 2026-10-05). UNA petición a Flash-Lite;
# «✏ Editar» las vuelve a dibujar con tus cambios SIN IA. Cada trabajo va en un hilo aparte y la página lo sigue con el estado de aquí.
_CARRUSELES: dict = {"lock": threading.Lock(), "hilos": {}, "paso": {}, "error": {}, "aviso": {}}
_CARRUSEL_IMG = re.compile(r"[\w.\-]+\.jpg")


def _carrusel_estado(jid: str) -> dict:
    from clipper import carrusel as cm
    h = _CARRUSELES["hilos"].get(jid)
    if h is not None and h.is_alive():
        return {"estado": "creando", "paso": _CARRUSELES["paso"].get(jid, "")}
    c = cm.lee(jid)
    err = _CARRUSELES["error"].get(jid, "")
    if c:
        out = {"estado": "listo", "error": err,
               "carrusel": {**{k: c.get(k) for k in ("portada", "pasos", "cierre", "archivos", "avisos", "nombre_modelo", "ts", "ts_edicion", "diapositivas",
                                                     "tiktok_titulo", "duracion", "canal", "texto_mode", "modo_imagen")},
                            "texto_instagram": cm.texto_instagram(c), "texto_tiktok": cm.texto_tiktok(c),
                            "pie_instagram": c.get("pie_instagram", ""), "tiktok_texto": c.get("tiktok_texto", ""),
                            "hay_pdf": (cm.ruta(jid) / "carrusel.pdf").is_file()}}
        if c.get("texto_mode"):                                # de un artículo/texto: las imágenes del artículo y los símbolos que se pueden elegir en «✏ Editar»
            from clipper import carrusel_texto as ct
            from clipper.ilus_escenas import CATALOGO
            try:
                imgs = [{"archivo": i["archivo"], "alt": i.get("alt", ""), "url": f"/texto/{jid}/fuente/{i['archivo']}"} for i in ct.datos(jid)["imagenes"]]
            except Exception:  # noqa: BLE001 — sin la fuente, solo se pueden elegir símbolos
                imgs = []
            out["imagenes_fuente"] = imgs
            out["simbolos"] = [{"clave": k, "nombre": getattr(v, "nombre", k)} for k, v in CATALOGO.items()]
        return out
    return {"estado": "error" if err else "no", "error": err}


@app.get("/api/carrusel/<jid>")
def api_carrusel(jid: str):
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    return jsonify(_carrusel_estado(jid))


def _carrusel_lanza(jid: str, trabajo, paso_inicial: str):
    """Lanza `trabajo(progreso) -> {ok, error?}` en un hilo (uno por vídeo). -> la respuesta JSON del panel."""
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    job = MGR.jobs.get(jid)
    if job and job.state in ("queued", "running", "paused"):
        return jsonify({"ok": False, "error": "Este vídeo se está procesando: cuando termine, crea el carrusel."}), 409
    with _CARRUSELES["lock"]:
        h = _CARRUSELES["hilos"].get(jid)
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True})
        _CARRUSELES["error"].pop(jid, None)
        _CARRUSELES["paso"][jid] = paso_inicial

        def _run():
            try:
                r = trabajo(lambda t: _CARRUSELES["paso"].__setitem__(jid, t))
                if not r.get("ok"):
                    _CARRUSELES["error"][jid] = str(r.get("error") or "No se pudo hacer el carrusel.")
            except Exception as exc:                                # noqa: BLE001 — un fallo aquí nunca tumba el panel
                clipper_log(f"AVISO: el carrusel de {jid} falló ({exc}).")
                _CARRUSELES["error"][jid] = f"El carrusel falló ({str(exc)[:160]}). Prueba otra vez; si se repite, mira work/panel.log."
            finally:
                _CARRUSELES["paso"].pop(jid, None)

        _CARRUSELES["hilos"][jid] = threading.Thread(target=_run, daemon=True)
        _CARRUSELES["hilos"][jid].start()
    return jsonify({"ok": True})


@app.post("/api/carrusel/<jid>/crear")
def api_carrusel_crear(jid: str):
    from clipper import carrusel as cm
    from clipper import carrusel_texto as ct
    if ct.es_texto(jid):                                       # «↻ Otro carrusel» de un texto: con la misma forma de elegir las imágenes (o la que pidas)
        modo = str((request.get_json(silent=True) or {}).get("modo_imagen") or (cm.lee(jid) or {}).get("modo_imagen") or "auto")
        return _carrusel_lanza(jid, lambda prog: cm.crea(jid, config.load(), progreso=prog, modo_imagen=modo), "Leyendo el texto…")
    return _carrusel_lanza(jid, lambda prog: cm.crea(jid, config.load(), progreso=prog), "Leyendo el vídeo…")


@app.post("/api/carrusel/<jid>/rehacer")
def api_carrusel_rehacer(jid: str):
    """Vuelve a dibujar con lo que cambiaste a mano (textos, qué pasos quedan, el momento de cada captura): SIN llamar a la IA."""
    from clipper import carrusel as cm
    cambios = request.get_json(silent=True) or {}
    return _carrusel_lanza(jid, lambda prog: cm.rehaz(jid, cambios, config.load(), progreso=prog), "Dibujando con tus cambios…")


@app.get("/carruseles")
def carruseles_page():
    return render_template("carruseles.html")


@app.get("/api/carruseles")
def api_carruseles():
    from clipper import carrusel_enlace as ce
    from clipper import carrusel_texto as ct
    items = [{**i, "tipo": "enlace"} for i in ce.lista()] + [{**i, "tipo": "texto"} for i in ct.lista() if i.get("hay_carrusel") or (_CARRUSELES["hilos"].get(i["jid"]) is not None and _CARRUSELES["hilos"][i["jid"]].is_alive())]
    items.sort(key=lambda i: i.get("ts") or 0, reverse=True)
    vistos = {i["jid"] for i in items}
    for k, h in list(_CARRUSELES["hilos"].items()):       # los que se están preparando (aún sin manifest) también salen: si no, la tarjeta desaparece mientras descarga/transcribe
        if ce.es_enlace(k) and k not in vistos and h.is_alive():
            vid = k[len(ce.PREFIJO):-len("--pro")]
            items.insert(0, {"jid": k, "titulo": "Preparando el vídeo…", "enlace": f"https://youtu.be/{vid}", "canal_origen": "", "hay_carrusel": False, "ts": time.time()})
    for k in list(_CARRUSELES["error"]):                   # los que fallaron (p. ej. un vídeo privado) también se ven, con su motivo, hasta que los quites con 🗑
        if ce.es_enlace(k) and k not in {i["jid"] for i in items}:
            vid = k[len(ce.PREFIJO):-len("--pro")]
            items.insert(0, {"jid": k, "titulo": "No se pudo hacer este carrusel", "enlace": f"https://youtu.be/{vid}", "canal_origen": "", "hay_carrusel": False, "ts": time.time(), "tipo": "enlace"})
    return jsonify({"items": items})


@app.post("/api/carruseles/texto")
def api_carruseles_texto():
    """Un carrusel desde un ARTÍCULO de TU web (uno de la lista, o su URL: se lee de tu proyecto de Astro, nunca se descarga nada de otros sitios) o desde un TEXTO que pegas.
    NO es automático: solo con este botón. `modo_imagen`: auto / articulo / simbolo / texto. Uno a la vez; no necesita la tarjeta gráfica (pero sí 2-4 peticiones de Flash-Lite)."""
    from clipper import carrusel as cm
    from clipper import carrusel_texto as ct
    from clipper import sitio_astro
    origen = request.headers.get("Origin") or ""
    if origen and not origen.startswith(("http://127.0.0.1", "http://localhost")):
        return jsonify({"ok": False, "error": "Petición de otro sitio: no se acepta."}), 403
    d = request.get_json(silent=True) or {}
    cfg, canal = config.load(), _canal_sitio()
    art_id = str(d.get("articulo_id") or "").strip()
    url = str(d.get("url") or "").strip()
    texto = str(d.get("texto") or "")
    titulo = " ".join(str(d.get("titulo") or "").split())[:150]
    modo = str(d.get("modo_imagen") or "auto")
    if modo not in ct.MODOS:
        modo = "auto"
    if url and not art_id:
        art_id = sitio_astro.resuelve_url(cfg, canal, url) or ""
        if not art_id:
            return jsonify({"ok": False, "error": "Ese enlace no es de un artículo de tu web (o no está en tu proyecto de Astro). Solo leo artículos de tu propio sitio; si es otro texto, pégalo en el cuadro."}), 400
    if not art_id and len(" ".join(texto.split())) < 400:
        return jsonify({"ok": False, "error": "Elige un artículo de tu web, pega su enlace o pega un texto de al menos unas 100 palabras."}), 400
    if any(h.is_alive() for k, h in _CARRUSELES["hilos"].items() if ct.es_texto(k)):
        return jsonify({"ok": False, "error": "Ya se está haciendo otro carrusel desde un texto: espera a que termine."}), 409
    prep = ct.prepara(texto, titulo, art_id, cfg, canal=canal)
    if not prep.get("ok"):
        return jsonify({"ok": False, "error": prep.get("error") or "No se pudo preparar el texto."}), 400
    jid = prep["jid"]

    def trabajo(prog):
        return cm.crea(jid, config.load(), progreso=prog, modo_imagen=modo)

    resp = _carrusel_lanza(jid, trabajo, "Empezando…")
    body = resp[0] if isinstance(resp, tuple) else resp
    data = body.get_json() or {}
    data["jid"] = jid
    return jsonify(data), (resp[1] if isinstance(resp, tuple) else 200)


@app.get("/texto/<jid>/fuente/<archivo>")
def carrusel_texto_fuente(jid: str, archivo: str):
    """Una imagen del artículo (copiada al preparar el carrusel) para elegirla en «✏ Editar»."""
    from clipper import carrusel_texto as ct
    if not ct.es_texto(jid) or not re.fullmatch(r"[\w.\-]+", jid or "") or not re.fullmatch(r"(?:img-\d{2}|hero)\.jpg", archivo or ""):
        abort(404)
    p = ct._dir_out(jid) / "fuente" / archivo
    if not p.is_file():
        abort(404)
    r = send_file(p, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.post("/api/carruseles/enlace")
def api_carruseles_enlace():
    """Un carrusel desde el ENLACE de un vídeo de YouTube: descarga + transcribe en el PC (Whisper) y luego el mismo carrusel de siempre. Uno a la vez y nunca con un vídeo procesándose
    (comparten la GPU)."""
    from clipper import carrusel as cm
    from clipper import carrusel_enlace as ce
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    vid = ce.video_id_de(url)
    if not vid:
        return jsonify({"ok": False, "error": "Eso no parece un enlace de un vídeo de YouTube (youtube.com/watch?v=… o youtu.be/…)."}), 400
    jid = ce.jid_de(vid)
    if any(j.state in ("queued", "running", "paused") for j in MGR.jobs.values()):
        return jsonify({"ok": False, "error": "Hay un vídeo procesándose: espera a que termine (comparte la tarjeta gráfica con la transcripción)."}), 409
    if any(h.is_alive() for k, h in _CARRUSELES["hilos"].items() if ce.es_enlace(k) and k != jid):
        return jsonify({"ok": False, "error": "Ya se está haciendo otro carrusel desde un enlace: espera a que termine."}), 409

    def trabajo(prog):
        r = ce.prepara(url, config.load(), progreso=prog)
        if not r.get("ok"):
            return r
        return cm.crea(jid, config.load(), progreso=prog)

    resp = _carrusel_lanza(jid, trabajo, "Empezando…")
    body = resp[0] if isinstance(resp, tuple) else resp
    data = body.get_json() or {}
    data["jid"] = jid
    return jsonify(data), (resp[1] if isinstance(resp, tuple) else 200)


@app.post("/api/carruseles/<jid>/borrar")
def api_carruseles_borrar(jid: str):
    from clipper import carrusel_enlace as ce
    from clipper import carrusel_texto as ct
    h = _CARRUSELES["hilos"].get(jid)
    if h is not None and h.is_alive():
        return jsonify({"ok": False, "error": "Se está creando: espera a que termine."}), 409
    if not (ce.es_enlace(jid) or ct.es_texto(jid)) or not re.fullmatch(r"[\w.\-]+", jid or ""):
        return jsonify({"ok": False, "error": "Ese carrusel no se puede borrar desde aquí."}), 404
    (ct.borra if ct.es_texto(jid) else ce.borra)(jid)
    _CARRUSELES["error"].pop(jid, None)
    return jsonify({"ok": True})


@app.get("/carrusel/<jid>/<clave>/<archivo>")
def carrusel_imagen(jid: str, clave: str, archivo: str):
    """Una diapositiva (`ig`, `tiktok`), una captura o un fotograma de previa. Solo .jpg de esas cuatro carpetas del carrusel de ese vídeo."""
    from clipper import carrusel as cm
    if not re.fullmatch(r"[\w.\-]+", jid or "") or clave not in ("ig", "tiktok", "capturas", "previas") or not _CARRUSEL_IMG.fullmatch(archivo or ""):
        abort(404)
    p = cm.ruta(jid) / clave / archivo
    if not p.is_file():
        abort(404)
    r = send_file(p, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/api/carrusel/<jid>/previa")
def api_carrusel_previa(jid: str):
    """Un fotograma pequeño del vídeo en el segundo `t` (para elegir otra captura sin dibujar nada)."""
    from clipper import carrusel as cm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    try:
        t = float(request.args.get("t", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "El segundo tiene que ser un número."}), 400
    p = cm.captura_previa(jid, t)
    if p is None:
        return jsonify({"ok": False, "error": "No pude sacar ese fotograma (¿sigue el vídeo en el PC?)."}), 404
    return jsonify({"ok": True, "url": f"/carrusel/{jid}/previas/{p.name}"})


@app.get("/api/carrusel/<jid>/zip/<formato>")
def api_carrusel_zip(jid: str, formato: str):
    """Las diapositivas de `ig`, `tiktok` o `todo` (con el PDF y los textos de las publicaciones) en un .zip."""
    import io
    from clipper import carrusel as cm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    z = cm.zip_de(jid, formato)
    if z is None:
        abort(404)
    datos, nombre = z
    return send_file(io.BytesIO(datos), mimetype="application/zip", as_attachment=True, download_name=nombre)


@app.get("/api/carrusel/<jid>/pdf")
def api_carrusel_pdf(jid: str):
    from clipper import carrusel as cm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    p = cm.ruta(jid) / "carrusel.pdf"
    if not p.is_file():
        abort(404)
    return send_file(p, mimetype="application/pdf", as_attachment=True, download_name=f"carrusel-{jid}.pdf")


# ===================================================================== 📣 POSTS: Tools → Posts (clipper/posts.py)
# 1 o 2 imágenes sobre UNA idea (no un carrusel) de un vídeo tuyo, un artículo de tu web, un texto o un enlace de YouTube (el autor, 13-oct-2026). Un solo diseño en 2 tamaños (4:5 y 16:9).
# 1 petición a Flash-Lite por post; un solo post a la vez; «✏ Editar» y «↻ Otra portada» redibujan SIN IA.
_POSTS: dict = {"lock": threading.Lock(), "hilo": None, "paso": "", "error": "", "jid": ""}
_POST_IMG = re.compile(r"post-\d{2}\.jpg")


def _posts_enlace(jid: str) -> str:
    """El enlace que va en «{ENLACE}»: el del vídeo si ya está en YouTube, el del artículo de tu web, o el del vídeo de un enlace de YouTube. '' si no hay (un texto pegado)."""
    from clipper import carrusel_enlace as ce
    from clipper import carrusel_texto as ct
    from clipper import sitio_astro
    if ce.es_enlace(jid):
        return f"https://youtu.be/{jid[len(ce.PREFIJO):-len('--pro')]}"
    if ct.es_texto(jid):
        f = ct._lee_fuente(jid) or {}
        if str(f.get("origen", "")).startswith("web:"):
            try:
                cfg, canal = config.load(), _canal_sitio()
                web = str(((cfg.get_path(f"videos.canales.{canal}") or {}).get("enlaces") or {}).get("web") or "").rstrip("/")
                art_id = str(f["origen"])[4:]
                a = next((x for x in sitio_astro.lista_articulos(cfg, canal)["articulos"] if x["id"] == art_id), None)
                if web and a:
                    return f"{web}/{a['categoria']}/{a.get('url_slug') or art_id.split('/')[1]}/"
            except Exception:  # noqa: BLE001
                return ""
        return ""
    return _shorts_enlace(jid)


def _posts_fuentes() -> list[dict]:
    """Tus vídeos PRO terminados (los más nuevos primero): de aquí se puede sacar un post sin gastar nada de descarga ni transcripción."""
    out = []
    for d in P["outbox"].glob("*--pro*"):
        n = d.name
        if n.startswith(("texto-", "enlace-", "zz-")) or not (d / "pro.mp4").is_file():
            continue
        try:
            mt = (d / "manifest.json").stat().st_mtime
        except OSError:
            continue
        titulo = _nombre_video(n)
        if not titulo:
            try:
                man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                titulo = str((man[0] if isinstance(man, list) else man).get("titulo") or n)
            except (OSError, ValueError, IndexError, TypeError):
                titulo = n
        out.append({"jid": n, "titulo": titulo, "ts": mt})
    return sorted(out, key=lambda x: -x["ts"])[:60]


def _estilos_de(p: dict) -> list:
    """Los estilos que se pueden hacer con este post (para el selector «Estilo»)."""
    from clipper import posts_estilos as pe
    try:
        return [{"id": e, "nombre": pe.NOMBRE[e]} for e in pe.posibles(p)]
    except Exception:  # noqa: BLE001
        return []


def _post_publico(p: dict) -> dict:
    from clipper import posts as pm
    t = pm.textos({**p, "enlace": p.get("enlace") or _posts_enlace(p["jid"])})
    v = int(float(p.get("ts_edicion") or p.get("ts") or 0))
    return {**{k: p.get(k) for k in ("id", "jid", "ts", "ts_edicion", "tipo", "idea", "gancho", "subtitulo", "puntos", "portada", "cara_gesto", "imagenes", "archivos", "avisos", "fuente_titulo",
                                     "origen", "nombre_modelo", "icono", "pie_instagram", "texto_corto", "estilo", "dims")}, "textos": t,
            "estilos": _estilos_de(p),
            "base": f"/posts/{p['jid']}/{p['id']}", "v": v}


@app.get("/posts")
def posts_page():
    return render_template("posts.html")


@app.get("/api/posts")
def api_posts():
    from clipper import posts as pm
    jid = request.args.get("jid") or None
    if jid and not re.fullmatch(r"[\w.\-]+", jid):
        abort(404)
    h = _POSTS["hilo"]
    en_marcha = h is not None and h.is_alive()
    return jsonify({"estado": "creando" if en_marcha else "libre", "paso": _POSTS["paso"] if en_marcha else "", "error": "" if en_marcha else _POSTS["error"], "jid_en_marcha": _POSTS["jid"] if en_marcha else "",
                    "inicio": _POSTS.get("inicio", 0) if en_marcha else 0, "paso_ts": _POSTS.get("paso_ts", 0) if en_marcha else 0, "ahora": time.time(),
                    "items": [_post_publico(p) for p in pm.lista(jid)[:40]], "fuentes": [] if jid else _posts_fuentes()})


@app.post("/api/posts/crear")
def api_posts_crear():
    """Un post de: `jid` (un vídeo tuyo terminado, o la fuente de uno anterior), `articulo_id` / `url` (un artículo de TU web), `texto` (+`titulo`) o `youtube` (el enlace de un vídeo). `otro: true` =
    «↻ Otro post» de esa fuente (con las ideas ya usadas apartadas)."""
    from clipper import carrusel_enlace as ce
    from clipper import carrusel_texto as ct
    from clipper import posts as pm
    from clipper import sitio_astro
    origen = request.headers.get("Origin") or ""
    if origen and not origen.startswith(("http://127.0.0.1", "http://localhost")):
        return jsonify({"ok": False, "error": "Petición de otro sitio: no se acepta."}), 403
    d = request.get_json(silent=True) or {}
    cfg, canal = config.load(), _canal_sitio()
    jid = str(d.get("jid") or "").strip()
    art_id, url, texto, yt = str(d.get("articulo_id") or "").strip(), str(d.get("url") or "").strip(), str(d.get("texto") or ""), str(d.get("youtube") or "").strip()
    titulo = " ".join(str(d.get("titulo") or "").split())[:150]
    if jid:
        if not re.fullmatch(r"[\w.\-]+", jid) or not (P["outbox"] / jid).is_dir():
            return jsonify({"ok": False, "error": "No encuentro esa fuente en el PC."}), 404
        job = MGR.jobs.get(jid)
        if job and job.state in ("queued", "running", "paused"):
            return jsonify({"ok": False, "error": "Este vídeo se está procesando: cuando termine, crea el post."}), 409
    elif yt:
        vid = ce.video_id_de(yt)
        if not vid:
            return jsonify({"ok": False, "error": "Eso no parece un enlace de un vídeo de YouTube (youtube.com/watch?v=… o youtu.be/…)."}), 400
        jid = ce.jid_de(vid)
        if any(j.state in ("queued", "running", "paused") for j in MGR.jobs.values()):
            return jsonify({"ok": False, "error": "Hay un vídeo procesándose: espera a que termine (comparte la tarjeta gráfica con la transcripción)."}), 409
    else:
        if url and not art_id:
            art_id = sitio_astro.resuelve_url(cfg, canal, url) or ""
            if not art_id:
                return jsonify({"ok": False, "error": "Ese enlace no es de un artículo de tu web (o no está en tu proyecto de Astro). Solo leo artículos de tu propio sitio; si es otro texto, pégalo en el cuadro."}), 400
        if not art_id and len(" ".join(texto.split())) < 400:
            return jsonify({"ok": False, "error": "Elige un vídeo o un artículo de tu web, pega un enlace de YouTube o pega un texto de al menos unas 100 palabras."}), 400
    with _POSTS["lock"]:
        h = _POSTS["hilo"]
        if h is not None and h.is_alive():
            return jsonify({"ok": False, "error": "Ya se está haciendo otro post: espera a que termine."}), 409
        if not jid and not yt:
            prep = ct.prepara(texto, titulo, art_id, cfg, canal=canal)
            if not prep.get("ok"):
                return jsonify({"ok": False, "error": prep.get("error") or "No se pudo preparar el texto."}), 400
            jid = prep["jid"]
        _POSTS["error"], _POSTS["paso"], _POSTS["jid"] = "", "Empezando…", jid
        _POSTS["inicio"] = _POSTS["paso_ts"] = time.time()
        evita = [x.get("idea", "") for x in pm.lista(jid)] if d.get("otro") else None

        def _run():
            try:
                def prog(t):
                    _POSTS["paso"], _POSTS["paso_ts"] = t, time.time()
                if yt:
                    r = ce.prepara(yt, config.load(), progreso=prog)
                    if not r.get("ok"):
                        _POSTS["error"] = str(r.get("error") or "No se pudo preparar el vídeo.")
                        return
                r = pm.crea(jid, config.load(), progreso=prog, evita=evita, enlace=_posts_enlace(jid))
                if not r.get("ok"):
                    _POSTS["error"] = str(r.get("error") or "No se pudo hacer el post.")
            except Exception as exc:  # noqa: BLE001 — un fallo aquí nunca tumba el panel
                clipper_log(f"AVISO: el post de {jid} falló ({exc}).")
                _POSTS["error"] = f"El post falló ({str(exc)[:160]}). Prueba otra vez; si se repite, mira work/panel.log."
            finally:
                _POSTS["paso"] = ""
        _POSTS["hilo"] = threading.Thread(target=_run, daemon=True)
        _POSTS["hilo"].start()
    return jsonify({"ok": True, "jid": jid})


def _post_o_404(jid: str, pid: str):
    from clipper import posts as pm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    p = pm.lee(jid, pid)
    if not p:
        abort(404)
    return p


@app.post("/api/posts/<jid>/<pid>/rehacer")
def api_posts_rehacer(jid: str, pid: str):
    """Vuelve a dibujar con lo que cambiaste a mano (gancho, subtítulo, idea, puntos, textos y/o la portada: cara / logo / comic / azar): SIN IA."""
    from clipper import posts as pm
    _post_o_404(jid, pid)
    cambios = request.get_json(silent=True) or {}
    r = pm.rehaz(jid, pid, cambios, config.load())
    if not r.get("ok"):
        return jsonify(r), 400
    return jsonify({"ok": True, "post": _post_publico(r["post"])})


@app.post("/api/posts/<jid>/<pid>/borrar")
def api_posts_borrar(jid: str, pid: str):
    from clipper import posts as pm
    _post_o_404(jid, pid)
    return jsonify({"ok": pm.borra(jid, pid)})


@app.get("/posts/<jid>/<pid>/<clave>/<archivo>")
def posts_imagen(jid: str, pid: str, clave: str, archivo: str):
    from clipper import posts as pm
    _post_o_404(jid, pid)
    if clave not in pm.CLAVES or not _POST_IMG.fullmatch(archivo or ""):
        abort(404)
    p = pm.ruta(jid, pid) / clave / archivo
    if not p.is_file():
        abort(404)
    r = send_file(p, mimetype="image/jpeg", as_attachment=request.args.get("descargar") == "1", download_name=f"{jid[:20]}-{clave}-{archivo}")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.post("/api/posts/<jid>/<pid>/carpeta")
def api_posts_carpeta(jid: str, pid: str):
    """«📂 Guardar en una carpeta»: lo mismo que «Todo (.zip)» pero YA descomprimido: una carpeta por red en Descargas/Posts Video PRO/<post>/, que se abre sola en el Explorador."""
    import io
    from clipper import posts as pm
    _post_o_404(jid, pid)
    z = pm.zip_de(jid, pid, "todo")
    if z is None:
        return jsonify({"ok": False, "error": "No encuentro ese post."}), 404
    base = Path.home() / "Downloads" / "Posts Video PRO"
    nombre = re.sub(r"\.zip$", "", z[1])
    destino, n = base / nombre, 2
    while destino.exists():
        destino, n = base / f"{nombre}-{n}", n + 1
    try:
        destino.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(z[0])) as zf:
            zf.extractall(destino)
    except OSError as exc:
        return jsonify({"ok": False, "error": f"No pude escribir en {destino} ({str(exc)[:120]})."}), 500
    try:
        os.startfile(str(destino))                 # noqa: S606 — el Explorador de Windows, en el PC del autor (el panel es local)
    except Exception:  # noqa: BLE001
        pass
    return jsonify({"ok": True, "ruta": str(destino)})


@app.get("/api/posts/<jid>/<pid>/zip/<formato>")
def api_posts_zip(jid: str, pid: str, formato: str):
    import io
    from clipper import posts as pm
    p = _post_o_404(jid, pid)
    z = pm.zip_de(jid, pid, formato)
    if z is None:
        abort(404)
    return send_file(io.BytesIO(z[0]), mimetype="application/zip", as_attachment=True, download_name=z[1])


# ===================================================================== 🎬 SHORTS · TIKTOK: Tools → Shorts · TikTok (clipper/shorts_tt.py)
# Un Short vertical MUY editado desde un vídeo que subes o desde el trozo de un Short de un vídeo horizontal. Uno a la vez (cuesta tarjeta gráfica y tiempo) y nunca con un vídeo PRO procesándose.
_ST: dict = {"lock": threading.Lock(), "hilo": None, "sid": "", "paso": "", "pct": 0.0, "inicio": 0.0}


def _st_en_marcha() -> bool:
    h = _ST["hilo"]
    return h is not None and h.is_alive()


def _st_publico(e: dict) -> dict:
    from clipper import shorts_tt as stt
    o = e.get("origen") or {}
    sid = e["id"]
    vivo = _st_en_marcha() and _ST["sid"] == sid
    sub = stt.pct_subida(sid)
    return {"id": sid, "ts": e.get("ts"), "canal": e.get("canal"), "estado": "creando" if vivo else ("error" if e.get("estado") == "creando" else e.get("estado")), "paso": _ST["paso"] if vivo else e.get("paso", ""),
            "pct": _ST["pct"] if vivo else e.get("pct", 0), "error": e.get("error") if not vivo else "", "avisos": e.get("avisos") or [], "dur": e.get("dur"), "dur_origen": e.get("dur_origen"), "quitado_s": e.get("quitado_s"),
            "n_cortes": e.get("n_cortes"), "meta": e.get("meta") or {}, "textos": stt.textos(e) if e.get("meta") else {}, "eventos": e.get("eventos") or {}, "modo": e.get("modo"), "modelo": e.get("modelo"),
            "origen": {"tipo": o.get("tipo"), "archivo": o.get("archivo"), "titulo_fuente": o.get("titulo_fuente"), "pista": o.get("pista")}, "render_s": e.get("render_s"), "yt": e.get("yt") or {}, "subiendo": sub,
            "url": f"/shorts-tt/{sid}/short.mp4" if e.get("archivo") and e.get("estado") == "listo" else "", "poster": f"/shorts-tt/{sid}/miniatura.jpg" if e.get("miniatura") else "",
            "v": int(float(e.get("terminado") or e.get("ts") or 0))}


def _st_fuentes() -> list[dict]:
    """Los Shorts que ya salieron de tus vídeos horizontales (con su grabación original todavía en el PC): para «enviarlos» a esta herramienta."""
    out = []
    for jid, job in list(MGR.jobs.items()):
        if job.opts.get("modo") != "video_pro" or job.state != "done":
            continue
        try:
            ent = json.loads((job.out_dir / "manifest.json").read_text(encoding="utf-8"))[0]
        except (OSError, ValueError, IndexError):
            continue
        for sh in ent.get("shorts") or []:
            out.append({"jid": jid, "archivo": sh.get("archivo"), "titulo": sh.get("titulo"), "duracion": sh.get("duracion"), "video": ent.get("titulo") or job.label, "original": Path(job.source).is_file(),
                        "ts": job.finished_at if hasattr(job, "finished_at") and job.finished_at else 0})
    return out[:80]


def _st_lanza(sid: str, forzar: bool = False):
    with _ST["lock"]:
        if _st_en_marcha():
            return jsonify({"ok": False, "error": "Ya se está haciendo otro Short: espera a que termine."}), 409
        if any(j.state in ("queued", "running", "paused") for j in MGR.jobs.values()):
            return jsonify({"ok": False, "error": "Hay un vídeo PRO procesándose: espera a que termine (comparten la tarjeta gráfica con la transcripción y el montaje)."}), 409
        _ST.update(sid=sid, paso="Empezando…", pct=0.0, inicio=time.time())

        def _run():
            from clipper import shorts_tt as stt

            def prog(f, t):
                _ST["pct"], _ST["paso"] = round(f * 100, 1), t
            try:
                stt.procesa(sid, config.load(), progreso=prog, forzar=forzar)
            except Exception as exc:  # noqa: BLE001 — un fallo aquí nunca tumba el panel
                clipper_log(f"AVISO: el Short {sid} falló ({exc}).")
        _ST["hilo"] = threading.Thread(target=_run, daemon=True)
        _ST["hilo"].start()
    return jsonify({"ok": True, "id": sid})


def _st_canal(pedido: str) -> str:
    from clipper import shorts_tt as stt
    ids = [c["id"] for c in stt.canales(config.load())]
    if pedido in ids:
        return pedido
    return ids[0] if ids else ""


@app.get("/shorts-tiktok")
def shorts_tt_page():
    return render_template("shorts_tt.html")


@app.get("/api/shorts-tt")
def api_shorts_tt():
    from clipper import shorts_tt as stt
    cfg = config.load()
    return jsonify({"estado": "creando" if _st_en_marcha() else "libre", "paso": _ST["paso"] if _st_en_marcha() else "", "pct": _ST["pct"] if _st_en_marcha() else 0, "inicio": _ST["inicio"] if _st_en_marcha() else 0,
                    "ahora": time.time(), "sid": _ST["sid"] if _st_en_marcha() else "", "items": [_st_publico(e) for e in stt.lista()[:40]], "canales": stt.canales(cfg), "canal_defecto": _st_canal(""),
                    "fuentes": _st_fuentes(), "coste_yt": stt.COSTE_UNIDADES})


def _st_local_o_403():
    origen = request.headers.get("Origin") or ""
    if origen and not origen.startswith(("http://127.0.0.1", "http://localhost")):
        return jsonify({"ok": False, "error": "Petición de otro sitio: no se acepta."}), 403
    return None


@app.post("/api/shorts-tt/subir")
def api_shorts_tt_subir():
    """Sube el vídeo (multipart: `video`, `canal`) a `shorts_tt/<id>/` y empieza el Short."""
    from clipper import shorts_tt as stt
    mal = _st_local_o_403()
    if mal:
        return mal
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No llegó ningún vídeo."}), 400
    nombre = secure_filename(f.filename) or "video.mp4"
    ext = Path(nombre).suffix.lower()
    if ext not in stt.EXT_OK:
        return jsonify({"ok": False, "error": f"«{ext}» no es un formato de vídeo que reconozca (uso: {', '.join(stt.EXT_OK)})."}), 400
    if _st_en_marcha():
        return jsonify({"ok": False, "error": "Ya se está haciendo otro Short: espera a que termine."}), 409
    canal = _st_canal(str(request.form.get("canal") or ""))
    sid = stt.nuevo(canal, {"tipo": "subida", "archivo": nombre, "ruta": ""})
    destino = stt.carpeta(sid) / f"origen{ext}"
    try:
        f.save(destino)
    except Exception as exc:  # noqa: BLE001
        stt.borra(sid)
        return jsonify({"ok": False, "error": f"No se pudo guardar el vídeo: {exc}"}), 500
    e = stt.lee(sid) or {}
    e["origen"] = {**(e.get("origen") or {}), "ruta": str(destino), "titulo_fuente": Path(nombre).stem}
    stt.guarda(sid, e)
    r = _st_lanza(sid)
    if isinstance(r, tuple) and r[1] >= 400:
        stt.borra(sid)
    return r


@app.post("/api/shorts-tt/desde-video")
def api_shorts_tt_desde_video():
    """«Enviar» un Short de un vídeo horizontal: se toma ESE trozo de su grabación ORIGINAL (no el vertical ya montado)."""
    from clipper import shorts_tt as stt
    mal = _st_local_o_403()
    if mal:
        return mal
    d = request.get_json(silent=True) or {}
    jid, archivo = str(d.get("jid") or ""), str(d.get("archivo") or "")
    job = MGR.jobs.get(jid)
    if not job or job.opts.get("modo") != "video_pro" or job.state != "done" or not re.fullmatch(r"short_\d+\.mp4", archivo):
        return jsonify({"ok": False, "error": "Ese Short no existe."}), 404
    try:
        ent = json.loads((job.out_dir / "manifest.json").read_text(encoding="utf-8"))[0]
    except (OSError, ValueError, IndexError):
        return jsonify({"ok": False, "error": "No encuentro el manifest.json de ese vídeo."}), 404
    corto = next((s for s in ent.get("shorts") or [] if s.get("archivo") == archivo), None)
    if not corto:
        return jsonify({"ok": False, "error": "Ese Short no está en el vídeo."}), 404
    if not Path(job.source).is_file():
        return jsonify({"ok": False, "error": "La grabación ORIGINAL ya no está en el PC (" + Path(job.source).name + "): sin ella no se puede rehacer ese trozo. Súbela de nuevo con «Subir un vídeo»."}), 400
    if _st_en_marcha():
        return jsonify({"ok": False, "error": "Ya se está haciendo otro Short: espera a que termine."}), 409
    a, b = stt.rango_en_original(ent, corto)
    canal = _st_canal(str(d.get("canal") or job.opts.get("canal") or ""))
    sid = stt.nuevo(canal, {"tipo": "video", "archivo": Path(job.source).name, "ruta": str(job.source), "desde": max(0.0, a - 0.2), "hasta": b + 0.4, "jid": jid, "titulo_fuente": corto.get("titulo") or ent.get("titulo"),
                            "pista": str(corto.get("idea") or corto.get("titulo") or "")[:240]})
    r = _st_lanza(sid)
    if isinstance(r, tuple) and r[1] >= 400:
        stt.borra(sid)
    return r


def _st_o_404(sid: str) -> dict:
    from clipper import shorts_tt as stt
    try:
        e = stt.lee(sid)
    except ValueError:
        abort(404)
    if not e:
        abort(404)
    return e


@app.post("/api/shorts-tt/<sid>/rehacer")
def api_shorts_tt_rehacer(sid: str):
    """Otra edición del MISMO vídeo (otra petición a la IA, otro montaje). Reusa el audio y la transcripción."""
    mal = _st_local_o_403()
    if mal:
        return mal
    _st_o_404(sid)
    return _st_lanza(sid)


@app.post("/api/shorts-tt/<sid>/borrar")
def api_shorts_tt_borrar(sid: str):
    from clipper import shorts_tt as stt
    _st_o_404(sid)
    if _st_en_marcha() and _ST["sid"] == sid:
        return jsonify({"ok": False, "error": "Se está haciendo: espera a que termine."}), 409
    return jsonify({"ok": stt.borra(sid)})


@app.get("/shorts-tt/<sid>/<archivo>")
def shorts_tt_archivo(sid: str, archivo: str):
    from clipper import shorts_tt as stt
    e = _st_o_404(sid)
    if archivo not in ("short.mp4", "miniatura.jpg"):
        abort(404)
    p = stt.carpeta(sid) / archivo
    if not p.is_file():
        abort(404)
    titulo = re.sub(r"[^\w\- ]+", "", str((e.get("meta") or {}).get("titulo_yt") or sid))[:60].strip() or sid
    r = send_file(p, mimetype="video/mp4" if archivo.endswith(".mp4") else "image/jpeg", as_attachment=request.args.get("descargar") == "1", download_name=f"{titulo}{Path(archivo).suffix}", conditional=True)
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.post("/api/shorts-tt/<sid>/subir-yt")
def api_shorts_tt_subir_yt(sid: str):
    """⬆ Subir el Short a YouTube (solo con tu clic; privado por defecto; ~1.600 unidades de cuota)."""
    from clipper import shorts_tt as stt
    mal = _st_local_o_403()
    if mal:
        return mal
    _st_o_404(sid)
    d = request.get_json(silent=True) or {}
    edits = {k: d[k] for k in ("titulo", "descripcion", "tags") if k in d}
    priv = str(d.get("privacidad") or "private")

    def _run():
        stt.sube_yt(sid, edits, priv)
    if stt.pct_subida(sid) is not None:
        return jsonify({"ok": False, "error": "Ya se está subiendo."}), 409
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


# ===================================================================== 🎨 MINIATURAS SUELTAS: Tools → Miniaturas (clipper/miniaturas_sueltas.py)
# Una miniatura SIN vídeo (p. ej. para un directo): lo que el autor escribe o dicta + su cara y su marca. Una a la vez; tope propio de 15 llamadas a Cloudflare al día.
_SUELTAS: dict = {"lock": threading.Lock(), "hilo": None, "paso": "", "error": ""}


def _suelta_publica(it: dict) -> dict:
    return {**{k: it.get(k) for k in ("id", "ts", "texto", "formato", "gesto", "titular", "titular_pedido", "sin_titular", "idea", "cara", "segundos", "misma_idea", "avisos",
                                      "nombre_modelo", "con_texto")},
            "url": f"/miniaturas-sueltas/{it['id']}.jpg?v={int(float(it.get('ts') or 0))}", "descarga": f"/miniaturas-sueltas/{it['id']}.jpg?descargar=1"}


@app.get("/miniaturas")
def miniaturas_sueltas_page():
    return render_template("miniaturas.html")


@app.get("/api/miniaturas-sueltas")
def api_miniaturas_sueltas():
    from clipper import miniaturas_ia as mia, miniaturas_sueltas as ms
    cfg = config.load()
    h = _SUELTAS["hilo"]
    en_marcha = h is not None and h.is_alive()
    ok, motivo = mia.disponible(cfg)
    try:
        gestos = mia.gestos(mia.caras(cfg=cfg), mia.parametros(cfg)["cara_defecto"])
    except Exception:  # noqa: BLE001 — sin fotos: la miniatura sale sin cara y se avisa
        gestos = []
    return jsonify({"estado": "generando" if en_marcha else "libre", "paso": _SUELTAS["paso"] if en_marcha else "", "error": "" if en_marcha else _SUELTAS["error"],
                    "items": [_suelta_publica(x) for x in ms.items()], "cupo": ms.cupo(cfg), "gestos": gestos, "defecto": mia.parametros(cfg)["cara_defecto"],
                    "formatos": [{"id": f, "nombre": ms.NOMBRES[f], "ayuda": ms.AYUDA[f]} for f in ms.FORMATOS], "disponible": ok, "motivo": motivo,
                    "min_texto": ms.MIN_TEXTO, "max_texto": ms.MAX_TEXTO})


@app.post("/api/miniaturas-sueltas/generar")
def api_miniaturas_sueltas_generar():
    """«Generar» y «↻»: lo del formulario (+ `de`, la miniatura desde la que se pulsó ↻). Una a la vez; el resultado y los errores los lee la página en GET /api/miniaturas-sueltas."""
    from clipper import miniaturas_sueltas as ms
    origen = request.headers.get("Origin") or ""
    if origen and not origen.startswith(("http://127.0.0.1", "http://localhost")):
        return jsonify({"ok": False, "error": "Petición de otro sitio: no se acepta."}), 403
    d = request.get_json(silent=True) or {}
    texto = " ".join(str(d.get("texto") or "").split())
    if len(texto) < ms.MIN_TEXTO:
        return jsonify({"ok": False, "error": "Cuéntale con unas palabras qué quieres en la miniatura (de qué va, qué elementos, qué contraste)."}), 400
    with _SUELTAS["lock"]:
        h = _SUELTAS["hilo"]
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True})
        _SUELTAS["error"], _SUELTAS["paso"] = "", "Empezando…"
        cfg = config.load()

        def _run():
            try:
                r = ms.genera(texto, str(d.get("formato") or "derecha"), str(d.get("gesto") or ""), str(d.get("titular") or ""), bool(d.get("sin_titular")), cfg,
                              de=str(d.get("de") or "") or None, progreso=lambda t: _SUELTAS.__setitem__("paso", t))
                if not r.get("ok"):
                    _SUELTAS["error"] = str(r.get("error") or "No se pudo hacer la miniatura.")
            except Exception as exc:                                # noqa: BLE001 — un fallo aquí nunca tumba el panel
                clipper_log(f"AVISO: la miniatura suelta falló ({exc}).")
                _SUELTAS["error"] = f"La miniatura falló ({str(exc)[:160]}). Prueba otra vez; si se repite, mira work/panel.log."
            finally:
                _SUELTAS["paso"] = ""

        _SUELTAS["hilo"] = threading.Thread(target=_run, daemon=True)
        _SUELTAS["hilo"].start()
    return jsonify({"ok": True})


@app.get("/miniaturas-sueltas/<item_id>.jpg")
def miniatura_suelta_imagen(item_id: str):
    """La imagen de una miniatura suelta (con `?descargar=1`, como archivo con un nombre claro)."""
    from clipper import miniaturas_sueltas as ms
    p = ms.ruta_imagen(item_id)
    if p is None:
        abort(404)
    if request.args.get("descargar"):
        it = ms.item(item_id) or {}
        base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", str(it.get("titular") or it.get("idea") or "miniatura").lower()).encode("ascii", "ignore").decode()).strip("-")[:40] or "miniatura"
        return send_file(p, mimetype="image/jpeg", as_attachment=True, download_name=f"miniatura-{base}-{item_id[-10:]}.jpg")
    r = send_file(p, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.post("/api/miniaturas-sueltas/<item_id>/borrar")
def api_miniatura_suelta_borrar(item_id: str):
    from clipper import miniaturas_sueltas as ms
    return jsonify({"ok": ms.quita(item_id)})


# ===================================================================== 📝 IMÁGENES DESTACADAS DE ARTÍCULOS: Tools → Miniaturas → pestaña «Imágenes destacadas» (clipper/articulos_sueltos.py)
# La imagen destacada de un artículo SIN vídeo (el autor, 11-oct-2026): lo que escribe, dicta o elige de sus artículos publicados. Una a la vez; comparte el cupo de «imágenes de artículo» con las destacadas.
_ARTSUELTOS: dict = {"lock": threading.Lock(), "hilo": None, "paso": "", "error": ""}


def _artsuelto_publico(it: dict) -> dict:
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", str(it.get("titulo") or it.get("tema") or "articulo").lower()).encode("ascii", "ignore").decode()).strip("-")[:50] or "articulo"
    imgs = []
    for i in it.get("imagenes") or []:
        n = (re.search(r"(\d+)", str(i.get("archivo", ""))) or [None, "1"])[1]
        imgs.append({"archivo": i["archivo"], "url": f"/articulos-sueltos/{it['id']}/{i['archivo']}?v={int(float(i.get('ts') or 0))}",
                     "descarga": f"/articulos-sueltos/{it['id']}/{i['archivo']}?descargar=1", "nombre": f"{base}-destacada-{n}.jpg", "con_texto": bool(i.get("con_texto")),
                     "segundos": i.get("segundos"), "ts": i.get("ts")})
    return {**{k: it.get(k) for k in ("id", "ts", "texto", "titulo", "tema", "objetos", "escena", "alt", "nombre_modelo")}, "imagenes": imgs}


def _canal_sitio() -> str:
    return config.canal_defecto(config.load())


@app.get("/miniaturas-articulos")
def miniaturas_articulos_page():
    return render_template("miniaturas_articulos.html")


@app.get("/api/articulos-sueltos")
def api_articulos_sueltos():
    from clipper import articulos_sueltos as asu, miniaturas_ia as mia, sitio_astro
    cfg = config.load()
    h = _ARTSUELTOS["hilo"]
    en_marcha = h is not None and h.is_alive()
    ok, motivo = mia.disponible(cfg)
    return jsonify({"estado": "generando" if en_marcha else "libre", "paso": _ARTSUELTOS["paso"] if en_marcha else "", "error": "" if en_marcha else _ARTSUELTOS["error"],
                    "items": [_artsuelto_publico(x) for x in asu.items()], "cupo": asu.cupo(cfg), "disponible": ok, "motivo": motivo,
                    "min_texto": asu.MIN_TEXTO, "max_texto": asu.MAX_TEXTO, "sitio": bool(sitio_astro.config_sitio(cfg, _canal_sitio()).get("configurado"))})


@app.get("/api/articulos-sueltos/sitio")
def api_articulos_sueltos_sitio():
    """Tus artículos ya publicados en tu proyecto de Astro (solo lectura), para elegir uno y ponerle una imagen nueva."""
    from clipper import sitio_astro
    return jsonify(sitio_astro.lista_articulos(config.load(), _canal_sitio()))


@app.get("/api/articulos-sueltos/sitio/<cat>/<slug>")
def api_articulos_sueltos_sitio_uno(cat: str, slug: str):
    from clipper import sitio_astro
    a = sitio_astro.lee_articulo(config.load(), _canal_sitio(), f"{cat}/{slug}")
    if not a:
        return jsonify({"ok": False, "error": "No encuentro ese artículo en tu proyecto."}), 404
    return jsonify({"ok": True, **a})


@app.post("/api/articulos-sueltos/generar")
def api_articulos_sueltos_generar():
    """«Generar», «↻ Otra» y «💡 Otra idea»: lo del formulario (+ `de`, la idea desde la que se pulsó). Una a la vez; el resultado y los errores los lee la página en GET /api/articulos-sueltos."""
    from clipper import articulos_sueltos as asu
    origen = request.headers.get("Origin") or ""
    if origen and not origen.startswith(("http://127.0.0.1", "http://localhost")):
        return jsonify({"ok": False, "error": "Petición de otro sitio: no se acepta."}), 403
    d = request.get_json(silent=True) or {}
    texto = " ".join(str(d.get("texto") or "").split())
    if len(texto) < asu.MIN_TEXTO:
        return jsonify({"ok": False, "error": "Cuéntale con unas palabras de qué va el artículo (el tema, o pega su texto, o elige uno de tu web)."}), 400
    with _ARTSUELTOS["lock"]:
        h = _ARTSUELTOS["hilo"]
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True})
        _ARTSUELTOS["error"], _ARTSUELTOS["paso"] = "", "Empezando…"
        cfg = config.load()

        def _run():
            try:
                r = asu.genera(texto, str(d.get("titulo") or ""), de=str(d.get("de") or "") or None, nueva_idea=bool(d.get("nueva_idea")), cfg=cfg,
                               progreso=lambda t: _ARTSUELTOS.__setitem__("paso", t))
                if not r.get("ok"):
                    _ARTSUELTOS["error"] = str(r.get("error") or "No se pudo hacer la imagen.")
            except Exception as exc:                                # noqa: BLE001 — un fallo aquí nunca tumba el panel
                clipper_log(f"AVISO: la imagen de artículo suelta falló ({exc}).")
                _ARTSUELTOS["error"] = f"La imagen falló ({str(exc)[:160]}). Prueba otra vez; si se repite, mira work/panel.log."
            finally:
                _ARTSUELTOS["paso"] = ""

        _ARTSUELTOS["hilo"] = threading.Thread(target=_run, daemon=True)
        _ARTSUELTOS["hilo"].start()
    return jsonify({"ok": True})


@app.get("/articulos-sueltos/<item_id>/<archivo>")
def articulo_suelto_imagen(item_id: str, archivo: str):
    """La imagen de una idea (con `?descargar=1`, como archivo con el nombre del artículo)."""
    from clipper import articulos_sueltos as asu
    p = asu.ruta_imagen(item_id, archivo)
    if p is None:
        abort(404)
    if request.args.get("descargar"):
        pub = _artsuelto_publico(asu.item(item_id) or {"id": item_id, "imagenes": [{"archivo": archivo}]})
        nombre = next((i["nombre"] for i in pub["imagenes"] if i["archivo"] == archivo), "articulo-destacada.jpg")
        return send_file(p, mimetype="image/jpeg", as_attachment=True, download_name=nombre)
    r = send_file(p, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.post("/api/articulos-sueltos/<item_id>/borrar")
def api_articulo_suelto_borrar(item_id: str):
    from clipper import articulos_sueltos as asu
    return jsonify({"ok": asu.quita(item_id)})


# ===================================================================== 📣 REDES SOCIALES (clipper/redes.py)
# Una publicación por red para copiar y pegar (el autor, 2026-09-27; paso 1: nada se publica solo). UNA petición a Flash-Lite.
_REDES: dict = {"lock": threading.Lock(), "hilos": {}, "error": {}}


@app.get("/api/redes/<jid>")
def api_redes(jid: str):
    """Las publicaciones de este vídeo + el enlace de YouTube que se pone en «{ENLACE}» (el del vídeo publicado de la
    familia, si ya está subido) y, si está programado, cuándo sale."""
    from clipper import redes as redm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    h = _REDES["hilos"].get(jid)
    if h is not None and h.is_alive():
        return jsonify({"estado": "creando"})
    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    fila = next((f for f in historial.lista() if f.get("base") == base), None) or {}
    vid = (fila.get("youtube") or {}).get("video_id")
    d = redm.lee(jid)
    err = _REDES["error"].get(jid, "")
    return jsonify({"estado": "listo" if d else ("error" if err else "no"), "error": err, "datos": d,
                    "enlace": f"https://youtu.be/{vid}" if vid else "",
                    "programado": fila.get("fecha_yt") if fila.get("estado_yt") == "programado" else None})


@app.post("/api/redes/<jid>/crear")
def api_redes_crear(jid: str):
    from clipper import redes as redm
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    job = MGR.jobs.get(jid)
    if job and job.state in ("queued", "running", "paused"):
        return jsonify({"ok": False, "error": "Este vídeo se está procesando: cuando termine, crea las publicaciones."}), 409
    with _REDES["lock"]:
        h = _REDES["hilos"].get(jid)
        if h is not None and h.is_alive():
            return jsonify({"ok": True, "ya": True})
        _REDES["error"].pop(jid, None)

        def _run():
            r = redm.crea(jid, config.load())
            if not r.get("ok"):
                _REDES["error"][jid] = str(r.get("error") or "")

        _REDES["hilos"][jid] = threading.Thread(target=_run, daemon=True)
        _REDES["hilos"][jid].start()
    return jsonify({"ok": True})


# ===================================================================== 🎬 SHORTS: título/tags y subida a YouTube (clipper/shorts_meta.py, shorts_yt.py)
# el autor (2026-10-09): por cada Short, título + tags para YouTube y para TikTok (como el carrusel) y subirlo a YouTube de una, dentro del flujo.
def _shorts_enlace(jid: str) -> str:
    """El enlace del vídeo completo si ya está subido a YouTube (lo pone el código en la descripción del Short; nunca la IA)."""
    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    vid = historial.video_id_de(base)
    return f"https://youtu.be/{vid}" if vid else ""


@app.get("/api/shorts/<jid>")
def api_shorts(jid: str):
    from clipper import shorts_meta, shorts_yt
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    d = P["outbox"] / jid
    return jsonify({"metas": shorts_meta.lee(d), "subidos": shorts_yt.estado(jid, d), "enlace": _shorts_enlace(jid),
                    "coste": shorts_yt.COSTE_UNIDADES, "conectado": bool(youtube.available() and youtube.is_connected())})


@app.post("/api/shorts/<jid>/subir")
def api_shorts_subir(jid: str):
    """Sube UN Short a YouTube con el título/descripción/tags del cuadro. Solo con un clic (y su confirmación en el panel): cuesta ≈1.600 de la cuota diaria."""
    from clipper import shorts_yt
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    b = request.get_json(silent=True) or {}
    archivo = str(b.get("archivo") or "")
    d = P["outbox"] / jid
    job = MGR.jobs.get(jid)
    if job and job.state in ("queued", "running", "paused"):
        return jsonify({"ok": False, "error": "Este vídeo se está procesando: cuando termine, sube los Shorts."}), 409
    if not (d / archivo).is_file() or not re.fullmatch(r"short_\d+\.mp4", archivo):
        return jsonify({"ok": False, "error": "Ese Short no existe."}), 404
    if not (youtube.available() and youtube.is_connected()):
        return jsonify({"ok": False, "error": "YouTube no está conectado: usa «Conectar YouTube» arriba a la derecha."}), 400
    edits = {k: b[k] for k in ("titulo", "descripcion", "tags") if k in b}
    if not shorts_yt.lanza(jid, archivo, d, edits, str(b.get("privacidad") or "private"), _shorts_enlace(jid)):
        return jsonify({"ok": True, "ya": True})
    return jsonify({"ok": True})


# ===================================================================== 🎨 MINIATURAS CON IA (clipper/miniaturas_ia.py)
# el autor (2026-09-28): de los prompts A/B/C del director, 3 miniaturas con FLUX.2 [klein] 4B de Cloudflare (gratis) + su
# cara real y el titular por código. Solas al terminar cada vídeo PRO, una detrás de otra, en un hilo aparte; la batería
# de Cloudflare va en ESA sección (no en la cabecera: esa API es solo para las miniaturas).
_MINIS: dict = {"lock": threading.Lock(), "hilos": {}}


def _canal_de(jid: str) -> str:
    """El canal de un vídeo ya hecho (su manifest guarda `tema`), o ''."""
    try:
        m = json.loads((P["outbox"] / jid / "manifest.json").read_text(encoding="utf-8"))
        return str((m[0] if isinstance(m, list) and m else m).get("tema") or "")
    except (OSError, ValueError, AttributeError, IndexError):
        return ""


def _miniaturas_ia_arranca(jid: str, letras=None, otra: bool = False) -> tuple[bool, str]:
    from clipper import ediciones_canal as _edc
    from clipper import miniaturas_ia as mia
    cfg = config.load()
    ok, motivo = mia.disponible(cfg)
    if not ok:
        return False, motivo
    if not _edc.permite(cfg, _canal_de(jid), "miniaturas_ia"):
        return False, "Las miniaturas con IA están apagadas para este canal (✏️ Editar canal → Ediciones)."
    with _MINIS["lock"]:
        h = _MINIS["hilos"].get(jid)
        if h is not None and h.is_alive():
            return False, "Ya se están generando las miniaturas de este vídeo: espera a que terminen."

        def _run():
            try:
                mia.crea(jid, cfg, letras=letras, otra=otra)
            except Exception as exc:                                # noqa: BLE001
                clipper_log(f"AVISO: las miniaturas con IA de {jid} fallaron ({exc}).")

        mia.marca_arranque(jid, letras)                             # «Arrancando…» ya, no el mensaje de antes
        _MINIS["hilos"][jid] = threading.Thread(target=_run, daemon=True)
        _MINIS["hilos"][jid].start()
    return True, ""


def _archivo_pro(jid: str) -> str:
    try:
        m = json.loads((P["outbox"] / jid / "manifest.json").read_text(encoding="utf-8"))
        return Path(str((m[0] if isinstance(m, list) and m else m).get("archivo") or "pro.mp4")).name
    except (OSError, ValueError, AttributeError):
        return "pro.mp4"


@app.get("/api/miniaturas-ia/<jid>")
def api_miniaturas_ia(jid: str):
    """Cómo van las miniaturas con IA de este vídeo + la batería de Cloudflare de hoy."""
    from clipper import miniaturas_ia as mia
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    cfg = config.load()
    est = mia.lee(jid)
    h = _MINIS["hilos"].get(jid)
    en_marcha = h is not None and h.is_alive()
    ok, motivo = mia.disponible(cfg)
    items = {}
    vig = mia.vigentes(jid)                       # ¿la imagen es del concepto de AHORA? (tras el «↻» del prompt, no)
    for letra, it in (est.get("items") or {}).items():
        it = dict(it or {})
        if it.get("ok") and mia.ruta_imagen(jid, letra):
            it["url"] = f"/miniaturas-ia/{jid}/{letra}.jpg?v={int(float(it.get('hecha') or 0))}"
            it["vigente"] = bool(vig.get(letra, True))
        items[letra] = it
    estado = est.get("estado") or "no"
    if estado in ("generando", "esperando") and not en_marcha:
        estado = "parado"                                           # el panel se reinició a mitad: se puede relanzar
    return jsonify({"estado": estado, "en_marcha": en_marcha, "mensaje": est.get("mensaje") or "", "items": items,
                    "disponible": ok, "motivo": motivo, "bateria": mia.bateria(cfg),
                    "titulares": mia.titulares_de(jid, cfg)})        # lo que se escribirá con Rubik en cada letra ('' = nada)


@app.post("/api/miniaturas-ia/<jid>/generar")
def api_miniaturas_ia_generar(jid: str):
    """Las que faltan (o, con {"letra": "B", "otra": true}, otra versión de esa)."""
    from clipper import miniaturas_ia as mia
    if not re.fullmatch(r"[\w.\-]+", jid or "") or not (P["outbox"] / jid / "manifest.json").is_file():
        return jsonify({"ok": False, "error": "Ese vídeo no está en el PC."}), 404
    job = MGR.jobs.get(jid)
    if job and job.state in ("queued", "running", "paused"):
        return jsonify({"ok": False, "error": "Este vídeo se está procesando: las miniaturas salen solas al terminar."}), 409
    d = request.get_json(silent=True) or {}
    letra = str(d.get("letra") or "").upper()
    letras = [letra] if letra in mia.LETRAS else None
    ok, error = _miniaturas_ia_arranca(jid, letras=letras, otra=bool(d.get("otra")) and bool(letras))
    return jsonify({"ok": ok, "error": error}), (200 if ok else 409)


@app.get("/miniaturas-ia/<jid>/<letra>.jpg")
def miniaturas_ia_imagen(jid: str, letra: str):
    from clipper import miniaturas_ia as mia
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    f = mia.ruta_imagen(jid, letra.upper())
    if not f:
        abort(404)
    return send_file(f, mimetype="image/jpeg", max_age=0, download_name=f"miniatura-{letra.upper()}-{jid}.jpg")


@app.post("/api/miniaturas-ia/<jid>/usar/<letra>")
def api_miniaturas_ia_usar(jid: str, letra: str):
    """«Usar esta»: pasa a las miniaturas propias del vídeo y queda YA ELEGIDA al programar (sin descargar ni subir)."""
    from clipper import miniaturas_ia as mia
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    f = mia.ruta_imagen(jid, letra.upper())
    if not f:
        return jsonify({"ok": False, "error": "Esa miniatura no existe (todavía)."}), 404
    arch = _archivo_pro(jid)
    r = miniaturas_custom.guardar(jid, arch, f.read_bytes())
    if not r.get("ok"):
        return jsonify(r), 400
    miniaturas_custom.marca_preferida(jid, arch, r["id"])
    mia.marca_usada(jid, letra.upper(), r["id"])
    clipper_log(f"Miniatura con IA {letra.upper()} de {jid}: elegida para publicar (id {r['id']}).")
    return jsonify({"ok": True, "id": r["id"]})


@app.post("/api/historial/<base>/borrar")
def api_historial_borrar(base: str):
    """Quita una fila del historial (el autor lo pide a mano). NO toca YouTube ni el PC — solo
    dejas de verla en la lista."""
    return jsonify({"ok": historial.elimina(base)})


@app.get("/api/metricas/<base>")
def api_metricas(base: str):
    """Vistas, likes y la curva de retención real de YouTube para este vídeo (pedido del autor,
    2026-09-22) — funciona aunque ya hayas borrado el .mp4, porque solo necesita el video_id
    que guardó `clipper/historial.py` al publicar."""
    from clipper import metricas
    fila = historial.carga().get(base) or {}
    video_id = ((fila.get("youtube") or {}).get("video_id"))
    if not video_id:
        return jsonify({"ok": False, "error": "Este vídeo todavía no está publicado en YouTube (o se publicó "
                        "antes de que existiera esta función: solo se guarda el enlace de lo publicado desde ahora)."})
    if request.args.get("cache"):                # desde los cuadritos del Historial: lo guardado, sin preguntar a YouTube
        r = metricas.lee_cache(base) or {"ok": False, "error": "Todavía no hay métricas de este vídeo: pulsa ↻ junto a "
                                                              "«Historial» para traerlas de YouTube."}
    else:
        r = metricas.obten(base, video_id, duracion_s=fila.get("duracion_s"), forzar=bool(request.args.get("refresh")))
    r["url"] = (fila.get("youtube") or {}).get("url") or f"https://youtu.be/{video_id}"
    return jsonify(r)


# ------------------------------------------------------------------ YouTube
@app.get("/youtube/status")
def yt_status():
    if not youtube.available():
        tiene_cs, libs = youtube.has_client_secret(), youtube.libs_ok()
        motivo = ("Faltan las librerías de Google (pip install -r requirements.txt)."
                  if not libs else "Falta el archivo client_secret.json de Google Cloud.")
        return jsonify({"available": False, "reason": motivo, "libs_ok": libs,
                        "has_client_secret": tiene_cs,
                        "ms_disponible": bool(youtube.client_secret_de_ms())})
    st = youtube.connect_state()
    if youtube.is_connected() and not st.get("in_progress"):
        info = youtube.channel_info()
        if info.get("connected"):
            return jsonify({"available": True, "days_left": youtube.days_left(), **info})
        # el token estaba muerto; channel_info() ya lo limpió -> pide reconectar claro
        return jsonify({"available": True, "connected": False, "needs_reconnect": True,
                        "error": "Tu acceso a YouTube caducó o cambiaron los permisos. "
                                 "Reconecta (es 1 clic)."})
    return jsonify({"available": True, "connected": False,
                    "connecting": st.get("in_progress", False),
                    "auth_url": st.get("auth_url"),
                    "error": st.get("error")})


@app.post("/youtube/connect")
def yt_connect():
    if not youtube.available():
        return jsonify({"ok": False, "error": "Falta client_secret.json."})
    try:
        url = youtube.connect_start()
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)})
    return jsonify({"ok": True, "auth_url": url})


@app.post("/youtube/client-secret")
def yt_client_secret():
    """Guarda el client_secret.json de Google Cloud desde el panel: sube el archivo (campo
    «archivo») o, con {"copiar_de_ms": true}, reutiliza el del proyecto hermano."""
    if request.is_json and (request.get_json(silent=True) or {}).get("copiar_de_ms"):
        return jsonify(youtube.copia_client_secret_de_ms())
    f = request.files.get("archivo")
    if f is None:
        return jsonify({"ok": False, "error": "No llegó ningún archivo."})
    try:
        texto = f.read(200_000).decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "El archivo no es texto (JSON)."})
    return jsonify(youtube.guarda_client_secret(texto))


# ------------------------------------------------------------ ilustraciones animadas
# ===================================================================== canales de Vídeos PRO
@app.get("/api/canales")
def api_canales():
    """Lo que necesita el asistente de «Nuevo canal»: canales, colores, letras, redes, ediciones y vídeos ya subidos."""
    from clipper import canales as ch
    from clipper import ediciones_canal as ed_mod
    videos = sorted((f.name for f in P["inbox"].glob("*") if f.suffix.lower() in _EXT_VIDEO_OK),
                    key=lambda n: -(P["inbox"] / n).stat().st_mtime)
    return jsonify({"canales": ch.lista(), "fuentes": ch.fuentes_disponibles(),
                    "redes": [{"clave": k, "nombre": n} for k, n in ch.REDES],
                    "colores": [{"clave": k, "nombre": n, "defecto": d} for k, n, d in ch.COLORES],
                    "tonos": list(ch.TONOS), "inbox": videos[:40],
                    "ediciones": ed_mod.catalogo_para_panel(config.load()),
                    "presets": [{"id": k, "nombre": v["nombre"], "ayuda": v["ayuda"], "valores": v["valores"]} for k, v in ed_mod.PRESETS.items()]})


@app.get("/api/canales/<slug>")
def api_canal(slug: str):
    """La configuración de un canal PROPIO (para editarlo)."""
    from clipper import canales as ch
    info = ch.carga(slug)
    if not info:
        return jsonify({"ok": False, "error": "Ese canal no se puede editar desde aquí."}), 404
    return jsonify({"ok": True, "slug": slug, "canal": info})


@app.post("/api/canales")
def api_canal_guardar():
    from clipper import canales as ch
    slug, err = ch.guarda(request.get_json(silent=True) or {})
    if err:
        return jsonify({"ok": False, "errores": err}), 400
    clipper_log(f"Canal guardado desde el panel: {slug}")
    return jsonify({"ok": True, "slug": slug, "canales": ch.lista()})


@app.post("/api/canales/<slug>/borrar")
def api_canal_borrar(slug: str):
    from clipper import canales as ch
    if not ch.borra(slug):
        return jsonify({"ok": False, "error": "Ese canal no se puede borrar desde aquí."}), 404
    return jsonify({"ok": True, "canales": ch.lista()})


# ------------------------------------------------------------ tu marca personal: retrato, logo y caras (clipper/mi_marca.py)
@app.get("/api/mi-marca")
def api_mi_marca():
    from clipper import mi_marca
    return jsonify(mi_marca.estado())


@app.post("/api/mi-marca/imagen")
def api_mi_marca_imagen():
    from clipper import mi_marca
    f = request.files.get("archivo")
    ok, msg = mi_marca.guarda_imagen(str(request.form.get("tipo") or ""), f.read() if f else b"")
    return jsonify({"ok": ok, "mensaje": msg, "estado": mi_marca.estado()}), (200 if ok else 400)


@app.post("/api/mi-marca/cara")
def api_mi_marca_cara():
    from clipper import mi_marca
    f = request.files.get("archivo")
    ok, msg = mi_marca.guarda_cara(str(request.form.get("gesto") or ""), str(request.form.get("mira") or ""), f.read() if f else b"")
    return jsonify({"ok": ok, "mensaje": msg, "estado": mi_marca.estado()}), (200 if ok else 400)


@app.post("/api/mi-marca/cara/borrar")
def api_mi_marca_cara_borrar():
    from clipper import mi_marca
    ok = mi_marca.borra_cara(str((request.get_json(silent=True) or {}).get("archivo") or ""))
    return jsonify({"ok": ok, "estado": mi_marca.estado()}), (200 if ok else 404)


@app.get("/mi-marca/<carpeta>/<path:archivo>")
def mi_marca_archivo(carpeta: str, archivo: str):
    from clipper import mi_marca
    f = mi_marca.ruta_publica(carpeta, archivo)
    if f is None:
        abort(404)
    return send_file(f, conditional=True, max_age=0)


@app.post("/api/canales/contexto")
def api_canal_contexto():
    """El texto que leerá Gemini, armado con lo que se ha rellenado hasta ahora."""
    from clipper import canales as ch
    d = request.get_json(silent=True) or {}
    return jsonify({"contexto": ch.compone_contexto(d.get("perfil") or {}, str(d.get("nombre") or ""),
                                                     str((d.get("enlaces") or {}).get("web") or ""))})


@app.post("/api/canales/tarjetas")
def api_canal_tarjetas():
    """Vista previa REAL de las tarjetas con los colores y letras elegidos (el mismo dibujo que el render)."""
    from clipper import canales as ch
    d = request.get_json(silent=True) or {}
    try:
        return Response(ch.png_tarjetas(d.get("marca") or {}, d.get("suscribete") or {}), mimetype="image/png")
    except Exception as exc:                                    # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/canales/frame")
def api_canal_frame():
    """Un fotograma de un vídeo ya subido (inbox/) para marcar dónde cae tu cámara."""
    from clipper import canales as ch
    nombre = secure_filename(request.form.get("inbox") or "")
    ruta = P["inbox"] / nombre
    if not nombre or not ruta.is_file():
        return jsonify({"ok": False, "error": "Elige uno de los vídeos que ya has subido."}), 400
    try:
        seg = float(request.form.get("seg") or 10)
        return Response(ch.frame_jpeg(ruta, seg), mimetype="image/jpeg")
    except Exception as exc:                                    # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


_EJ_HILO: dict = {"t": None}


def _calienta_ejemplos() -> None:
    """Al abrir «Ediciones» por primera vez, dibuja en segundo plano las muestras que falten."""
    from clipper import ejemplos as ej
    t = _EJ_HILO["t"]
    if ej.faltan() and (t is None or not t.is_alive()):
        _EJ_HILO["t"] = threading.Thread(target=ej.precalienta, daemon=True)
        _EJ_HILO["t"].start()


@app.get("/ediciones")
def ediciones_page():
    """Qué edita el panel: resumen con las cuatro ediciones (escenas, tarjetas, suscríbete, ilustraciones)."""
    from clipper import ejemplos as ej
    _calienta_ejemplos()
    return render_template("ediciones.html", secciones=ej.SECCIONES, activa="resumen")


@app.get("/ediciones/<seccion>")
def ediciones_seccion(seccion: str):
    from clipper import ejemplos as ej
    if seccion not in ej.SECCIONES:
        abort(404)
    _calienta_ejemplos()
    titulo, icono, texto = ej.SECCIONES[seccion]
    return render_template("ediciones_seccion.html", seccion=seccion, titulo=titulo, icono=icono, texto=texto,
                           items=[e for e in ej.CATALOGO.values() if e.seccion == seccion], activa=seccion)


@app.get("/ediciones/previa/<clave>.webp")
def ediciones_previa(clave: str):
    """WebP animado de una muestra (se dibuja la primera vez y queda en caché)."""
    from clipper import ejemplos as ej
    if clave not in ej.CATALOGO:
        abort(404)
    try:
        return send_file(ej.previa(clave), mimetype="image/webp", max_age=3600)
    except Exception:                                           # noqa: BLE001
        abort(500)


@app.get("/ediciones/poster/<clave>.webp")
def ediciones_poster(clave: str):
    """El fotograma QUIETO de una muestra: lo que enseña la galería de «Cambios de escena» hasta que se le da clic
    (la animación solo se descarga y se reproduce entonces)."""
    from clipper import ejemplos as ej
    if clave not in ej.CATALOGO:
        abort(404)
    try:
        return send_file(ej.poster(clave), mimetype="image/webp", max_age=3600)
    except Exception:                                           # noqa: BLE001
        abort(500)


@app.get("/ilustraciones")
def ilustraciones_page():
    from clipper import ilustraciones as ilus
    cfg = config.load()
    return render_template("ilustraciones.html", activa="ilustraciones", escenas=list(ilus.CATALOGO.values()),
                           maxpm=cfg.get_path("videos.ilustraciones.max_por_minuto", 2),
                           sep=cfg.get_path("videos.ilustraciones.separacion_min_seg", 12))


@app.get("/ilustraciones/previa/<clave>.webp")
def ilustraciones_previa(clave: str):
    """WebP animado de una escena (se dibuja la primera vez y queda en caché)."""
    from clipper import ilustraciones as ilus
    if clave not in ilus.CATALOGO:
        abort(404)
    try:
        return send_file(ilus.previa(clave), mimetype="image/webp", max_age=3600)
    except Exception:                                           # noqa: BLE001
        abort(500)


@app.post("/api/ilustraciones/prueba")
def api_ilustraciones_prueba():
    from clipper import ilustraciones as ilus
    return jsonify({"filas": ilus.prueba(rapido=True)})


# ------------------------------------------------------------------ uso de Gemini (pestaña «Uso»)
@app.get("/uso")
def uso_page():
    return render_template("uso.html")


@app.get("/api/uso")
def api_uso():
    """Lo que este programa (vídeos horizontales) ha gastado de Gemini + sus límites (ver clipper/uso.py)."""
    from clipper import modelos, uso
    cfg = config.load()
    r = uso.resumen(cfg)
    r["modelos_info"] = modelos.info(cfg)                    # cuándo se buscaron modelos por última vez, cuántos hay y si alguno no se sabe ordenar
    return jsonify(r)


@app.get("/api/cuotas")
def api_cuotas():
    """Lo que te queda HOY de cada cosa gratis (Gemini Flash, Flash-Lite, Cloudflare y YouTube): la franja de la cabecera de todas las páginas (clipper/cuotas.py)."""
    from clipper import cuotas
    return jsonify({"medidores": cuotas.resumen(config.load()), "ts": time.time()})


@app.post("/api/modelos/refrescar")
def api_modelos_refrescar():
    """«Buscar modelos nuevos»: le pregunta a Google AHORA qué modelos ofrece esta clave y rehace la cadena (la lista de modelos no gasta peticiones).
    Esto mismo pasa solo cada 12 h y al analizar un vídeo con la búsqueda caducada (clipper/modelos.py): el botón es para mirar sin esperar."""
    import re as _re
    from clipper import director, modelos, uso
    cfg = config.load()
    g = director._gemini(cfg)
    if not g:
        return jsonify({"ok": False, "error": "No hay clave de Gemini (o la IA está apagada): pega la clave en el cuadro «La clave de Gemini de los vídeos»."}), 400
    antes = modelos.cadena_base(cfg, None)
    try:
        ids = modelos.descubre(g[0])
    except Exception as exc:  # noqa: BLE001
        txt = _re.sub(r"AIza[\w\-]{20,}|AQ\.[\w.\-]{20,}", "[clave]", " ".join(str(exc).split()))[:300]
        return jsonify({"ok": False, "error": f"Google no contestó ({txt}). Se sigue con la última búsqueda."}), 502
    modelos.guarda_cache(ids)
    despues = modelos.cadena_base(cfg, None)
    r = uso.resumen(cfg)
    r["modelos_info"] = modelos.info(cfg)
    return jsonify({"ok": True, "nuevos": [m for m in despues if m not in antes], "quitados": [m for m in antes if m not in despues],
                    "cadena": despues, "resumen": r})


@app.get("/api/gemini/clave")
def api_gemini_clave_estado():
    """Solo las 4 últimas letras de la clave que usa este programa (las que enseña AI Studio en su lista): nunca la clave entera."""
    k = os.environ.get("GEMINI_API_KEY", "")
    return jsonify({"hay": bool(k), "ultimos4": k[-4:] if k else ""})


@app.post("/api/gemini/clave")
def api_gemini_clave_cambia():
    """el autor pega aquí la clave NUEVA (la de su proyecto «Video Pro»): se prueba con Google y, solo si funciona, se guarda en .env y en el
    panel en marcha. Esta herramienta nunca escribe claves por su cuenta (ver clipper/clave.py). No se registra el cuerpo de la petición."""
    from clipper import clave
    origen = request.headers.get("Origin") or ""
    if origen and not origen.startswith(("http://127.0.0.1", "http://localhost")):
        return jsonify({"ok": False, "error": "Petición de otro sitio: no se acepta."}), 403
    d = request.get_json(silent=True) or {}
    r = clave.cambia_clave(d.get("clave"), aparte=bool(d.get("aparte", True)))
    return jsonify(r), (200 if r.get("ok") else 400)


@app.get("/inicio")
def inicio_page():
    """Primeros pasos: las claves de Gemini (obligatoria) y Cloudflare (opcional, miniaturas con IA)."""
    return render_template("inicio.html")


@app.get("/inicio/omitir")
def inicio_omitir():
    """«Entrar sin configurar»: deja de desviar a /inicio en este navegador (sin Gemini no habrá análisis, pero se puede mirar el panel)."""
    r = redirect("/")
    r.set_cookie("inicio_omitido", "1", max_age=60 * 60 * 24 * 30, samesite="Lax")
    return r


@app.get("/api/inicio/estado")
def api_inicio_estado():
    from clipper import inicio
    return jsonify(inicio.estado())


@app.post("/api/inicio/cloudflare")
def api_inicio_cloudflare():
    """Pega ID de cuenta + token de Cloudflare: se comprueban con Cloudflare y, solo si valen, se guardan en .env. No se registra el cuerpo."""
    from clipper import inicio
    origen = request.headers.get("Origin") or ""
    if origen and not origen.startswith(("http://127.0.0.1", "http://localhost")):
        return jsonify({"ok": False, "error": "Petición de otro sitio: no se acepta."}), 403
    d = request.get_json(silent=True) or {}
    r = inicio.guarda_cloudflare(d.get("cuenta"), d.get("token"))
    return jsonify(r), (200 if r.get("ok") else 400)


@app.post("/api/uso/limites")
def api_uso_limites():
    from clipper import uso
    try:
        uso.guarda_limites(request.get_json(silent=True) or {})
    except OSError as exc:
        return jsonify({"ok": False, "error": f"No pude guardar los límites: {exc}"}), 500
    return jsonify({"ok": True, "resumen": uso.resumen(config.load())})


# ------------------------------------------------------------------ sistema
_SELFTEST: dict = {"corriendo": False, "checks": [], "inicio": None, "fin": None, "estado": None}
_SELFTEST_LOCK = threading.Lock()


@app.get("/sistema")
def sistema_page():
    return render_template("sistema.html")


@app.get("/api/sistema/estado")
def api_sistema_estado():
    with _SELFTEST_LOCK:
        s = dict(_SELFTEST, checks=list(_SELFTEST["checks"]))
    if not s["corriendo"] and not s["checks"]:
        ult = selftest.ultimo()          # la última prueba, guardada en disco
        if ult:
            s.update(checks=ult["checks"], estado=ult["estado"], inicio=ult["inicio"], fin=ult["fin"],
                     con_render=ult.get("con_render"), de_disco=True)
    return jsonify(s)


@app.post("/api/sistema/probar")
def api_sistema_probar():
    con_render = bool((request.get_json(silent=True) or {}).get("render", True))
    if con_render and any(getattr(j, "state", "") in ("running", "queued") for j in MGR.jobs.values()):
        return jsonify({"ok": False, "error": "Hay un vídeo en marcha: la prueba completa espera a que termine "
                                              "(las comprobaciones rápidas sí se pueden hacer ya)."})
    with _SELFTEST_LOCK:
        if _SELFTEST["corriendo"]:
            return jsonify({"ok": False, "error": "Ya hay una prueba en marcha."})
        _SELFTEST.update(corriendo=True, checks=[], inicio=time.time(), fin=None, estado=None, con_render=con_render)

    def _run():
        try:
            res = selftest.correr(config.load(), con_render=con_render,
                                  al_terminar_uno=lambda r: _SELFTEST["checks"].append(r))
            _SELFTEST.update(estado=res["estado"], fin=res["fin"])
        except Exception as exc:                                # noqa: BLE001
            _SELFTEST.update(estado="error", fin=time.time())
            _SELFTEST["checks"].append({"id": "fallo", "nombre": "La prueba", "estado": "error",
                                        "detalle": f"Se rompió la propia prueba: {exc}", "lineas": [], "seg": None})
        finally:
            _SELFTEST["corriendo"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/sistema/backup")
def api_sistema_backup():
    """Copia de seguridad de lo que no va a Git (ver backup.py)."""
    import backup as bk
    cfgb = bk._config_backup()
    destino = Path(str(cfgb.get("carpeta") or "")).expanduser() if cfgb.get("carpeta") else bk.carpeta_por_defecto()
    try:
        r = bk.haz_copia(destino, int(cfgb.get("conservar") or 8))
    except OSError as exc:
        return jsonify({"ok": False, "error": f"No pude escribir la copia en {destino}: {exc}"})
    return jsonify({"ok": True, **r})


def _yt_conectado_o_error():
    if not youtube.available():
        return jsonify({"ok": False, "error": "Falta configurar YouTube (botón rojo de arriba a la derecha)."}), 400
    if not youtube.is_connected():
        return jsonify({"ok": False, "error": "Conecta YouTube primero (botón rojo de arriba a la derecha)."}), 400
    return None


@app.post("/youtube/disconnect")
def yt_disconnect():
    youtube.disconnect()
    _YT_CANAL.update(ts=0.0, id=None)
    _LISTAS_CACHE.update(ts=0.0, listas=[])
    return jsonify({"ok": True})


@app.post("/job/<jid>/publish")
def job_publish(jid: str):
    if not youtube.is_connected():
        return jsonify({"ok": False, "error": "Conecta YouTube primero."}), 400
    rows = (request.get_json(silent=True) or {}).get("rows") or []
    added = PUB.enqueue(jid, rows)
    _sync_seleccion(jid)          # aprende: programados = te gustan, resto = no
    return jsonify({"ok": True, "encolados": len(added)})


@app.post("/job/<jid>/publish-rapido")
def job_publish_rapido(jid: str):
    """Programa los vídeos seleccionados SIN que el usuario elija fecha/hora:
    N por día en sus horas (rendimiento -> costumbre -> default), en días
    seguidos, empezando después de lo que ya tenga programado.
    `?preview=1` (o {"preview":true}) devuelve el plan sin encolar nada."""
    from datetime import datetime, timedelta, timezone
    if not youtube.is_connected():
        return jsonify({"ok": False, "error": "Conecta YouTube primero."}), 400
    b = request.get_json(silent=True) or {}
    solo_preview = bool(b.get("preview") or request.args.get("preview"))
    archivos = [Path(str(a)).name for a in (b.get("archivos") or [])]
    meta = _job_manifest(jid)
    # orden cronológico del trabajo
    sel = [a for a in archivos if a in meta] or list(meta.keys())
    sel.sort(key=lambda a: float(meta[a].get("start", 0) or 0))
    if not sel:
        return jsonify({"ok": False, "error": "No hay vídeos que programar."}), 400

    cfg = config.load()
    off = int(cfg.get_path("publish.zona_offset_horas", -5))
    tz = timezone(timedelta(hours=off))

    vp = (cfg.get("videos") or {}).get("publicar") or {}
    perday = max(1, min(3, int(vp.get("por_dia", 1))))
    dias_entre = max(1, int(vp.get("dias_entre", 1)))
    hs_info = taste_videos.horas_sugeridas(cfg, perday)
    horas = hs_info["horas"]

    now_local = datetime.now(tz)
    min_adel = float(cfg.get_path("publish.min_adelanto_horas", 6))
    piso = now_local + timedelta(hours=min_adel)

    # qué huecos están ya cogidos
    ya = []
    for it in PUB.items:
        pa = it.get("publish_at")
        if not pa:
            continue
        try:
            ya.append(datetime.fromisoformat(pa.replace("Z", "+00:00")).astimezone(tz))
        except ValueError:
            pass
    usados = {d.replace(second=0, microsecond=0) for d in ya}

    # HUECOS DESDE HOY. Se aprovecha el día de hoy si todavía queda alguna
    # hora por delante (respetando el margen mínimo de aviso a YouTube). Los
    # huecos ya ocupados se saltan, así que programar dos veces seguidas se
    # APILA en vez de pisarse.
    def huecos():
        dia = now_local.date()
        vueltas = 0
        while vueltas < 400:
            for h in horas[:perday]:
                try:
                    hh, mm = (int(x) for x in str(h).split(":")[:2])
                except ValueError:
                    continue
                cuando = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=tz)
                if cuando < piso:
                    continue                       # esa hora de hoy ya pasó
                if cuando.replace(second=0, microsecond=0) in usados:
                    continue                       # hueco ya ocupado
                yield cuando
            dia += timedelta(days=dias_entre)
            vueltas += 1

    plan = []
    hueco = huecos()
    for arch in sel:
        try:
            when = next(hueco)
        except StopIteration:
            break
        usados.add(when.replace(second=0, microsecond=0))
        e = meta.get(arch, {})
        plan.append({
            "archivo": arch,
            "titulo": e.get("titulo") or arch,
            "publish_at": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "local": when.strftime("%a %d-%b %H:%M"),
        })
    if not plan:
        return jsonify({"ok": False, "error": "No quedan huecos libres en el calendario."}), 400

    resumen = {
        "n": len(plan), "por_dia": perday,
        "horas": horas, "fuente": hs_info["fuente"],
        "dias_entre": dias_entre,
        "desde": plan[0]["local"], "hasta": plan[-1]["local"], "plan": plan,
    }
    if solo_preview:
        return jsonify({"ok": True, **resumen})

    rows = []
    for p in plan:
        e = meta.get(p["archivo"], {})
        rows.append({
            "archivo": p["archivo"],
            "title": p["titulo"],
            "description": e.get("descripcion") or "",
            "tags": e.get("tags") or "",
            "publish_at": p["publish_at"],
        })
    added = PUB.enqueue(jid, rows)
    _sync_seleccion(jid)
    return jsonify({"ok": True, "encolados": len(added), **resumen})


@app.get("/job/<jid>/publish-status")
def job_publish_status(jid: str):
    return jsonify(PUB.for_job(jid))


@app.get("/api/publish/estado")
def api_publish_estado():
    """Para el aviso de 'subida completa': cuántas quedan, si hay una subiendo
    y la marca del último lote terminado."""
    pend = sum(1 for it in PUB.items if it.get("state") in ("queued", "uploading"))
    subiendo = any(it.get("state") == "uploading" for it in PUB.items)
    return jsonify({"pendientes": pend, "subiendo": subiendo,
                    "last_batch": PUB.last_batch})


@app.post("/reiniciar")
def reiniciar_panel():
    """Reinicia el panel: se cierra y se vuelve a abrir con el código nuevo.

    Se relanza él mismo abriendo "ABRIR PANEL.cmd" en una ventana NUEVA y
    visible — el mismo arranque de siempre, nada oculto.
    """
    activos = [j for j in MGR.jobs.values()
               if getattr(j, "state", "") in ("running", "queued", "paused")]
    if activos or MGR.current is not None:
        return jsonify({"ok": False,
                        "error": "Hay un trabajo en marcha; espera a que termine."}), 409

    # Abierto OCULTO desde el acceso directo del escritorio (abrir.pyw) → se reabre igual, sin ventana negra.
    oculto = ROOT / "abrir.pyw"
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if os.environ.get("PANEL_OCULTO") == "1" and oculto.is_file() and pyw.is_file():
        try:
            subprocess.Popen([str(pyw), str(oculto), "--reinicio"], cwd=str(ROOT),
                             creationflags=0x00000008 | 0x00000200, close_fds=True)
        except Exception as exc:                            # noqa: BLE001
            return jsonify({"ok": False, "error": f"No pude programar la reapertura: {exc}"}), 500

        @after_this_request
        def _bye_oculto(resp):                              # noqa: ANN001
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return resp

        clipper_log("Panel reiniciado por el usuario: vuelve en unos segundos.")
        return jsonify({"ok": True, "reabre_en": 4})

    lanzador = ROOT / "ABRIR PANEL.cmd"
    if not lanzador.is_file():
        return jsonify({"ok": False, "error": "No encuentro 'ABRIR PANEL.cmd', "
                        "así que no puedo volver a abrirlo. Ciérralo y ábrelo "
                        "tú para no quedarte sin panel."}), 500

    try:
        subprocess.Popen(
            ["cmd", "/c", "timeout /t 3 >nul & start \"\" \"%s\"" % lanzador],
            cwd=str(ROOT), creationflags=0x00000010 | 0x00000200,
            close_fds=True)
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": f"No pude programar la reapertura: {exc}"}), 500

    @after_this_request
    def _bye(resp):                                         # noqa: ANN001
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return resp

    clipper_log("Panel reiniciado por el usuario: vuelve en unos segundos.")
    return jsonify({"ok": True, "reabre_en": 4})


@app.post("/apagar")
def apagar_panel():
    """Cierra el panel del todo (no lo relanza nadie). Botón 'Cerrar panel'."""
    activos = [j for j in MGR.jobs.values()
               if getattr(j, "state", "") in ("running", "queued", "paused")]
    subiendo = any(it.get("state") in ("queued", "uploading") for it in PUB.items)
    if activos or MGR.current is not None or subiendo:
        return jsonify({"ok": False, "error": "Hay un trabajo procesándose o "
                        "subidas en cola. Espera a que terminen."}), 409

    @after_this_request
    def _bye(resp):                                         # noqa: ANN001
        threading.Timer(0.4, lambda: os._exit(0)).start()
        return resp

    clipper_log("Panel apagado por el usuario.")
    return jsonify({"ok": True})


@app.post("/refrescar-vistas")
def refrescar_vistas():
    """Fuerza el refresco de vistas AHORA y cura de paso los video_id que
    quedaron obsoletos o vacíos."""
    if not youtube.is_connected():
        return jsonify({"ok": False, "needs_reconnect": True,
                        "error": "YouTube no está conectado."}), 400
    try:
        n_v = taste_videos.refresh_stats(youtube.video_stats)
    except Exception as exc:                                # noqa: BLE001
        txt = f"{type(exc).__name__}: {exc}"
        recon = any(k in txt.lower() for k in
                    ("invalid_grant", "expired", "revoked", "unauthorized",
                     "invalid_client", "refresh"))
        return jsonify({"ok": False, "needs_reconnect": recon, "error": txt}), 500
    if n_v:
        clipper_log(f"Vistas actualizadas en {n_v} vídeo(s).")
    return jsonify({"ok": True, "n": n_v or 0})


@app.get("/job/<jid>/thumb")
def job_thumb(jid: str):
    """Genera (o reusa) una miniatura de vista previa para un vídeo + título."""
    arch = Path(request.args.get("archivo", "")).name
    clip = P["outbox"] / jid / arch
    if not arch.endswith(".mp4") or not clip.is_file():
        abort(404)
    title = (request.args.get("title") or "")[:120]
    tag = request.args.get("tag", "")
    layout = request.args.get("layout", "")
    import hashlib
    key = hashlib.sha1(f"{jid}|{arch}|{title}|{tag}|{layout}".encode()).hexdigest()[:16]
    cache = P["work"] / "thumbcache"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{key}.jpg"
    if not out.is_file():
        cfg = pipeline._cfg_horizontal(config.load())
        dur = _probe_dur(clip)
        r = thumbs.make_thumbnail(clip, dur * 0.42, title, out, cfg, tag, layout)
        if not r:
            abort(500)
    return send_file(out, mimetype="image/jpeg", max_age=0)


@app.get("/job/<jid>/thumb-candidatos")
def job_thumb_candidatos(jid: str):
    """Las miniaturas propias que el autor ya subió para este clip (el "A/B/C" de
    miniaturas), más nuevas primero."""
    arch = Path(request.args.get("archivo", "")).name
    cands = miniaturas_custom.listar(jid, arch)
    pref = miniaturas_custom.preferida(jid, arch)                   # «Usar esta» (miniaturas con IA): ya elegida
    return jsonify([{"id": c["id"], "preferida": c["id"] == pref,
                     "url": url_for("job_thumb_candidato", jid=jid, archivo=arch, id=c["id"])}
                    for c in cands])


@app.get("/job/<jid>/thumb-candidato")
def job_thumb_candidato(jid: str):
    arch = Path(request.args.get("archivo", "")).name
    p = miniaturas_custom.ruta(jid, arch, request.args.get("id", ""))
    if not p:
        abort(404)
    return send_file(p, mimetype="image/jpeg", max_age=0)


@app.post("/job/<jid>/thumb-upload")
def job_thumb_upload(jid: str):
    """Sube una miniatura propia (el autor la retoca a mano en Photoshop) como
    candidata nueva, junto a la que se genera sola."""
    arch = Path(request.form.get("archivo", "")).name
    if not arch.endswith(".mp4") or not (P["outbox"] / jid / arch).is_file():
        return jsonify({"ok": False, "error": "Ese vídeo no existe en este trabajo."}), 400
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No llegó ninguna imagen."}), 400
    try:
        datos = f.read()
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": f"No se pudo leer el archivo: {exc}"}), 400
    r = miniaturas_custom.guardar(jid, arch, datos)
    if not r.get("ok"):
        return jsonify(r), 400
    clipper_log(f"Publisher: miniatura propia añadida para {arch} (id {r['id']}).")
    return jsonify({"ok": True, "id": r["id"],
                    "url": url_for("job_thumb_candidato", jid=jid, archivo=arch, id=r["id"])})


@app.post("/publish-item/<iid>/edit")
def pub_item_edit(iid: str):
    it = next((x for x in PUB.items if x["id"] == iid), None)
    if not it:
        abort(404)
    b = request.get_json(silent=True) or {}
    if "title" in b:
        it["title"] = (b["title"] or "")[:100]
    if "description" in b:
        it["description"] = b["description"] or ""
    if "tags" in b:
        it["tags"] = [t.strip() for t in (b["tags"] or "").split(",") if t.strip()]
    if "publish_at" in b:
        it["publish_at"] = b["publish_at"] or None
    err = None
    if it.get("state") == "done" and it.get("video_id"):
        try:
            youtube.update_video(it["video_id"], it["title"], it["description"],
                                 it["tags"], it["publish_at"])
        except Exception as exc:                            # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
    PUB._save()
    return jsonify({"ok": err is None, "error": err})


@app.post("/publish-item/<iid>/rethumb")
def pub_item_rethumb(iid: str):
    """Re-sube (o genera y sube) la miniatura de un vídeo ya subido."""
    it = next((x for x in PUB.items if x["id"] == iid), None)
    if not it or not it.get("video_id"):
        return jsonify({"ok": False, "error": "Vídeo no subido todavía."}), 400
    folder = P["outbox"] / it["jid"]
    clip = folder / it["archivo"]
    jpg = clip.with_suffix(".jpg")
    cfg = pipeline._cfg_horizontal(config.load())
    os.environ["PATH"] = _child_env()["PATH"]
    if clip.is_file():
        try:
            # se REHACE siempre: si estaba mal, reusarla dejaría el error para
            # siempre
            thumbs.make_thumbnail(clip, _probe_dur(clip) * 0.42, it["title"], jpg,
                                  cfg, it.get("tag", ""), it.get("layout", ""))
        except Exception as exc:                            # noqa: BLE001
            return jsonify({"ok": False, "error": f"miniatura: {exc}"}), 500
    if not jpg.is_file():
        return jsonify({"ok": False, "error": "No hay .jpg y el clip ya no está "
                        "en el PC para regenerarla."}), 400
    try:
        _r = youtube.set_thumbnail(it["video_id"], str(jpg))
        it["thumb_set"] = True
        it["thumb_url"] = _r.get("url", "")
        it.pop("thumb_error", None)
        PUB._save()
        clipper_log(f"Publisher: miniatura re-subida a mano en {it['video_id']}.")
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"ok": True})


@app.get("/api/youtube/listas")
def api_yt_listas():
    """TUS listas de reproducción de YouTube, de MÁS a MENOS vídeos, y cuáles están marcadas (las que se recuerdan para los próximos vídeos; la primera vez, la de más vídeos: `por_defecto`
    avisa de que se acaba de marcar). `?refrescar=1` vuelve a leerlas de YouTube."""
    if not youtube.is_connected():
        return jsonify({"ok": False, "error": "Conecta YouTube primero."}), 400
    try:
        listas = _listas_yt(bool(request.args.get("refrescar")))
    except Exception as exc:                                   # noqa: BLE001
        return jsonify({"ok": False, "error": f"No pude leer tus listas de YouTube ({type(exc).__name__}: {str(exc)[:120]})."}), 502
    e, recien = _listas_elegidas(_canal_yt_id())
    ids = {x["id"] for x in listas}
    return jsonify({"ok": True, "listas": listas, "elegidas": [i for i in e["listas"] if i in ids],
                    "olvidadas": [i for i in e["listas"] if i not in ids], "posicion": e["posicion"], "por_defecto": recien})


@app.post("/api/youtube/listas/elegidas")
def api_yt_listas_elegidas():
    """Recuerda las listas marcadas: cada vídeo que se publique (rápido o a mano) se añade a ellas al subirse. Una lista que no es de tu canal se rechaza."""
    if not youtube.is_connected():
        return jsonify({"ok": False, "error": "Conecta YouTube primero."}), 400
    b = request.get_json(silent=True) or {}
    ids = listas_yt.limpia(b.get("listas"))
    conocidas = {x["id"] for x in _LISTAS_CACHE["listas"]}
    if conocidas and any(i not in conocidas for i in ids):
        return jsonify({"ok": False, "error": "Alguna de esas listas no está en tu canal: recarga la página."}), 400
    return jsonify({"ok": True, **listas_yt.guarda(_canal_yt_id(), ids, posicion=b.get("posicion"))})


@app.post("/publish-item/<iid>/listas")
def pub_item_listas(iid: str):
    """«↻ Listas»: añade un vídeo YA subido a sus listas (las de cuando se encoló o, si no tenía, las marcadas ahora; o las del cuerpo `{"listas": [...]}`). Reintenta solo las que
    fallaron y sirve para meter en una lista un vídeo publicado antes. 200 con `ok` falso si alguna no entró (con el motivo en `listas`)."""
    it = next((x for x in PUB.items if x["id"] == iid), None)
    if not it or not it.get("video_id"):
        return jsonify({"ok": False, "error": "Ese vídeo aún no está subido a YouTube."}), 400
    if not youtube.is_connected():
        return jsonify({"ok": False, "error": "Conecta YouTube primero."}), 400
    b = request.get_json(silent=True) or {}
    cid = _canal_yt_id()
    e, _recien = _listas_elegidas(cid)
    ids = listas_yt.limpia(b.get("listas")) if isinstance(b.get("listas"), list) else (listas_yt.limpia(it.get("playlists")) or e["listas"])
    if not ids:
        return jsonify({"ok": False, "error": "No hay ninguna lista marcada: marca una arriba («Listas de reproducción») y vuelve a probar."}), 400
    it["playlists"], it["playlists_pos"] = ids, it.get("playlists_pos") or e["posicion"]
    PUB._listas(it, it["video_id"])
    PUB._save()
    return jsonify({"ok": all(x["ok"] for x in it["listas"]), "listas": it["listas"]})


@app.post("/publish-item/<iid>/delete")
def pub_item_delete(iid: str):
    it = next((x for x in PUB.items if x["id"] == iid), None)
    if not it:
        abort(404)
    err = None
    if it.get("state") == "done" and it.get("video_id"):
        try:
            youtube.delete_video(it["video_id"])
        except Exception as exc:                            # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
    # el clip local NO se toca: se borra solo con el botón "Borrar del PC".
    jid_it = it.get("jid")
    with PUB.lock:
        PUB.items = [x for x in PUB.items if x["id"] != iid]
        if iid in PUB.queue:
            PUB.queue.remove(iid)
        PUB._save()
    if jid_it:
        _sync_seleccion(jid_it)   # se arrepintió: ese vídeo vuelve a "no elegido"
    return jsonify({"ok": err is None, "error": err, "borrado_local": False})


_OBS_CACHE: dict = {}                                           # buscar en OBS cuesta unos ffprobe por vídeo: se recuerda mientras nada cambie


def _obs_de_grabaciones(grabaciones, otras) -> dict:
    """Las grabaciones ORIGINALES de OBS (y su cámara aparte) de esas grabaciones de `inbox/` (lo que sube el panel es una copia). SOLO para decirlo en el
    aviso del ✖ y para medir cuánto crece esa carpeta: NO se borran nunca (el autor, 2026-10-04: «todo debería borrarse excepto las que están en mi disco D,
    Videos: esas déjalas ahí, yo las borro… las dejo como respaldo»). Se recuerda mientras no cambie la grabación, la carpeta de OBS ni los demás
    vídeos. Nunca lanza."""
    try:
        from clipper import camara_aparte, espacio
        cfg = config.load()
        pa = camara_aparte.parametros(cfg)
        clave = (tuple((str(g), Path(g).stat().st_size, int(Path(g).stat().st_mtime)) for g in grabaciones), str(pa["carpeta"]),
                 Path(pa["carpeta"]).stat().st_mtime if Path(pa["carpeta"]).is_dir() else 0, frozenset(otras))
        hit = _OBS_CACHE.get(clave)
        if hit is not None and all(Path(f).exists() for f in hit["archivos"]):
            return hit
        res = espacio.obs_de(grabaciones, pa["carpeta"], otras_fuentes=otras, busca_camara=lambda g: camara_aparte.busca(g, cfg),
                             sufijo=pa["sufijo"])
        if len(_OBS_CACHE) > 200:
            _OBS_CACHE.clear()
        _OBS_CACHE[clave] = res
        return res
    except Exception as exc:                                    # noqa: BLE001 — un extra: nunca frena el borrado
        clipper_log(f"AVISO: no pude buscar las grabaciones de OBS ({exc}).")
        return {"archivos": [], "bytes": 0, "compartida": False, "carpeta": ""}


def _borrado_familia(base: str):
    """Lo que se llevaría el ✖ de «Mis vídeos» (el autor, 2026-09-26: «siempre haya la opción de borrar… que se borre también
    en mi PC… y que en el historial solo queden los que sí se subieron»). `info["obs"]` = sus grabaciones originales de OBS (NO se llevan: solo para
    decirlo) e `info["extras"]` = lo que trae el vídeo y quizá aún no usaste (Shorts, artículo, publicaciones para redes).
    -> (familia, rutas, info) o (None, None, error)."""
    from clipper import versiones
    todos = _all_jobs_public()
    fam = versiones.familia(todos, base)
    if not fam:
        return None, None, ("No encuentro ese vídeo.", 404)
    if versiones.en_marcha(fam):
        return None, None, ("Ese vídeo se está procesando: cancélalo o espera a que termine.", 409)
    jids = {f["jid"] for f in fam}
    with PUB.lock:
        pendiente = [it for it in PUB.items if it.get("jid") in jids and it.get("state") not in ("done", "error", "cancelled")]
    if pendiente:
        return None, None, ("Ese vídeo está en la cola para subir a YouTube: espera a que suba (o quítalo de la cola).", 409)
    rutas = []
    for f in fam:
        rutas += [P["outbox"] / f["jid"], P["work"] / f["jid"]]
    rutas.append(P["work"] / f"{base}--pro")                        # la caché común (transcripción, planes, audio…)
    # la grabación original (inbox/): solo si ningún trabajo de OTRO vídeo la usa (una subida repetida es otro nombre)
    otras = {str(Path(j.get("source") or "")).lower() for j in todos if j["jid"] not in jids and j.get("source")}
    inbox = P["inbox"].resolve()
    grabaciones = []
    for src in {str(Path(f.get("source"))) for f in fam if f.get("source")}:
        p = Path(src)
        if p.is_file() and inbox in p.resolve().parents and src.lower() not in otras:
            grabaciones.append(p)
    rutas += grabaciones
    unicas: dict[str, Path] = {}                                   # la v1 usa `work/<base>--pro` también de caché común
    for r in rutas:
        unicas.setdefault(str(Path(r).resolve()).lower(), Path(r))
    rutas = list(unicas.values())
    fila = historial.carga().get(base) or {}
    yt = fila.get("youtube") or {}
    extras = {"shorts": 0, "articulo": False, "redes": False, "carrusel": False}
    for f in fam:
        carpeta = P["outbox"] / f["jid"]
        extras["shorts"] += len(list(carpeta.glob("short_*.mp4")))
        extras["articulo"] = extras["articulo"] or (carpeta / "articulo" / "articulo.json").is_file()
        extras["redes"] = extras["redes"] or (carpeta / "redes.json").is_file()
        extras["carrusel"] = extras["carrusel"] or (carpeta / "carrusel" / "carrusel.json").is_file()
    return fam, rutas, {"versiones": len(fam), "grabacion": bool(grabaciones), "obs": _obs_de_grabaciones(grabaciones, otras), "extras": extras,
                        "publicado": {"url": yt.get("url") or f"https://youtu.be/{yt['video_id']}"} if yt.get("video_id") else None}


_BORRADO_LOCK = threading.Lock()                                    # un borrado a la vez (a mano o el automático)


@app.get("/api/familia/<base>/borrado")
def api_familia_borrado(base: str):
    """Qué se llevaría el ✖ (para que el aviso diga cuánto libera, cuánto son cachés, qué trae el vídeo —Shorts, artículo, redes— y que sus grabaciones de OBS
    se quedan) — no borra nada."""
    from clipper import espacio, papelera
    fam, rutas, info = _borrado_familia(base)
    if fam is None:
        return jsonify({"ok": False, "error": info[0]}), info[1]
    obs = info.pop("obs", None) or {}
    archivos = []
    for f in obs.get("archivos", []):
        try:
            archivos.append({"nombre": f.name, "bytes": f.stat().st_size})
        except OSError:
            pass
    return jsonify({"ok": True, **info, "bytes": papelera.tamano(rutas),
                    "cache_bytes": espacio.pesa_lista(espacio.caches_de(P["work"], base)),
                    "obs": {"bytes": int(obs.get("bytes") or 0), "carpeta": obs.get("carpeta", ""),
                            "compartida": bool(obs.get("compartida")), "archivos": archivos}})


def _borra_familia(base: str, a_papelera: bool = False):
    """El ✖ de «Mis vídeos» (el autor, 2026-09-26; y el 4-oct: «si está programado pasa al Historial y si no, simplemente se borra… ese borrar debería borrar
    todo lo de ese video, desde mi PC… todo debería borrarse excepto las que están en mi disco D, Videos»). Lo llaman el botón y la limpieza de «💾 Espacio».
    Borra DEL TODO todo lo de ese vídeo en tu PC — sus versiones, los Shorts, el artículo, la caché de análisis y la copia de la grabación de inbox/ si ningún
    otro vídeo la usa—; con `a_papelera` (la casilla del aviso) lo manda a la Papelera de reciclaje de Windows en vez de borrarlo (recuperable, pero el
    espacio no se libera hasta que la vacíes). Tus grabaciones de OBS (D:/Videos) NUNCA se tocan: son tu respaldo. Si estaba PUBLICADO (o programado) se
    queda en el Historial con su esqueleto (título, miniatura de YouTube, métricas y muletillas); si no, sale también del Historial. Nunca toca YouTube.
    Se niega si alguna versión está en marcha o en la cola de subida; si Windows no deja borrar algo (otro programa lo tiene abierto), lo dice y el vídeo
    sigue en la lista. -> (resultado, None) o (None, (mensaje, código http))."""
    from clipper import espacio, papelera
    with _BORRADO_LOCK:
        fam, rutas, info = _borrado_familia(base)
        if fam is None:
            return None, info
        if info["publicado"]:
            # sigue en el Historial: su transcripción se guarda aparte para «🗣️ Tus muletillas» (el autor, 2026-09-28), con las
            # pausas de duda medidas ANTES (necesitan el audio, que se va)
            try:
                from clipper import muletillas
                muletillas.pausas_de(P["work"] / f"{base}--pro", config.load())
            except Exception as exc:                                # noqa: BLE001 — un extra: nunca frena el borrado
                clipper_log(f"AVISO: no pude medir las pausas de duda de {base} antes de borrarlo ({exc}).")
            historial.conserva_habla(base, P["work"])
        ya = espacio.borra_caches(espacio.caches_de(P["work"], base), P["work"], min_edad_s=0)       # lo regenerable: de verdad, ya
        if ya["errores"]:
            clipper_log(f"AVISO: no pude borrar del todo las cachés de {base}: {'; '.join(ya['errores'][:2])}")
        if a_papelera:
            en_papelera = papelera.tamano(rutas)
            ok, error, mandadas = papelera.a_la_papelera(rutas)
            if not ok:
                return None, (error or "No se pudo mandar a la Papelera.", 500)
            libre_ya, n_rutas = ya["bytes"], len(mandadas)
        else:
            r = espacio.borra_rutas(rutas, (P["outbox"], P["work"], P["inbox"]))
            if r["errores"]:
                clipper_log(f"AVISO: no pude borrar del todo {base}: {'; '.join(r['errores'][:3])}")
                return None, (f"No pude borrar del todo ese vídeo: {r['errores'][0]}. Cierra lo que lo tenga abierto (el navegador, un reproductor) y "
                              "vuelve a intentarlo.", 500)
            en_papelera, libre_ya, n_rutas = 0, ya["bytes"] + r["bytes"], r["borradas"]
        with MGR.lock:
            for f in fam:
                MGR.jobs.pop(f["jid"], None)
                if f["jid"] in MGR.queue:
                    MGR.queue.remove(f["jid"])
            MGR._save()
        if not info["publicado"]:
            historial.elimina(base)                                # el Historial es solo de lo que se subió a YouTube
        clipper_log(f"Borrado desde «Mis vídeos» ({'a la Papelera de reciclaje' if a_papelera else 'del todo'}): {base} ({len(fam)} versión(es), "
                    f"{(libre_ya + en_papelera) / 1073741824:.2f} GB, {n_rutas} ruta(s); tus grabaciones de OBS no se tocan).")
        return {"versiones": len(fam), "liberado": libre_ya, "ya_libre": ya["bytes"], "papelera": en_papelera, "a_papelera": bool(a_papelera),
                "publicado": bool(info["publicado"])}, None


@app.post("/api/familia/<base>/borrar")
def api_familia_borrar(base: str):
    """El ✖ de «Mis vídeos»: ver `_borra_familia`. Cuerpo opcional {"papelera": true} = a la Papelera de reciclaje en vez de borrar del todo (la casilla del
    aviso). Tus grabaciones de OBS NO se tocan nunca (un cuerpo viejo con «obs» se ignora). Se niega si alguna versión está en marcha o en la cola de
    subida a YouTube."""
    dato = request.get_json(silent=True) or {}
    r, err = _borra_familia(base, a_papelera=bool(dato.get("papelera")))
    if r is None:
        return jsonify({"ok": False, "error": err[0]}), err[1]
    _espacio_olvida()
    return jsonify({"ok": True, **r})


# ===================================================================== 💾 ESPACIO (clipper/espacio.py)
# el autor (2026-10-04): «estoy sacando un contenido diario… en 90 días van a haber 90 videos… ¿cómo va a ser la sostenibilidad?». Mide lo que ocupa
# cada vídeo (y cuánto crece la carpeta de OBS, que no se toca), cuánto tardaría en llenarse el disco, y deja liberar con un clic lo que ya está en YouTube;
# con `videos.limpieza_auto` (apagado por defecto) lo hace solo, cada día, con el MISMO ✖ de «Mis vídeos» (nunca un vídeo sin publicar ni tus grabaciones de OBS).
_ESPACIO: dict = {"medida": None, "cuando": 0.0, "lock": threading.Lock(), "libera": threading.Lock()}
_ESPACIO_CADA_S = 120.0


def _espacio_olvida() -> None:
    _ESPACIO["medida"] = None


def _familias_pro() -> list[dict]:
    """Los vídeos PRO de «Mis vídeos»: {"base", "titulo"}."""
    from clipper import versiones
    return [{"base": j["base"], "titulo": j.get("nombre") or j.get("label") or j["base"]}
            for j in versiones.mis_videos(_all_jobs_public()) if j.get("base")]


def _bases_ocupadas() -> set:
    """Los vídeos con algo en marcha (procesándose, en cola o en la cola de subida a YouTube): a esos no se les toca nada."""
    from clipper import versiones
    out: set = set()
    for j in _all_jobs_public():
        p = versiones.parse(j.get("jid", ""))
        if p and j.get("state") in ("queued", "running", "paused"):
            out.add(p[0])
    with PUB.lock:
        for it in PUB.items:
            p = versiones.parse(it.get("jid") or "")
            if p and it.get("state") not in ("done", "error", "cancelled"):
                out.add(p[0])
    return out


def _detalle_espacio(base: str) -> dict:
    """Lo que se llevaría el ✖ de ese vídeo (todo MENOS OBS) y lo que pesan sus grabaciones de OBS (que se quedan)."""
    from clipper import papelera
    fam, rutas, info = _borrado_familia(base)
    if fam is None:
        return {}
    return {"pc": papelera.tamano(rutas), "obs": int((info.get("obs") or {}).get("bytes") or 0)}


def _espacio_medida(forzar: bool = False) -> dict:
    from clipper import camara_aparte, espacio
    with _ESPACIO["lock"]:
        if not forzar and _ESPACIO["medida"] is not None and time.time() - _ESPACIO["cuando"] < _ESPACIO_CADA_S:
            return _ESPACIO["medida"]
        cfg = config.load()
        auto, dias = espacio.opciones(cfg)
        m = espacio.medida(dir_work=P["work"], outbox=P["outbox"], inbox=P["inbox"], filas=historial.lista(P["work"], P["outbox"]),
                           familias=_familias_pro(), detalle_de=_detalle_espacio, en_marcha=_bases_ocupadas(), dias=dias,
                           obs_carpeta=camara_aparte.parametros(cfg)["carpeta"])
        m["auto"] = auto
        m["ultimo"] = espacio.lee_ultimo(P["work"])
        _ESPACIO["medida"], _ESPACIO["cuando"] = m, time.time()
        return m


def _espacio_libera(que: str, a_papelera: bool = False, origen: str = "a mano") -> dict:
    """«🧹 Liberar» (a mano) y la limpieza automática: `que` = "caches" (las cachés regenerables de lo ya publicado y los restos de vídeos que ya no están
    en «Mis vídeos», borradas de verdad), "completos" (borra ENTEROS, con el mismo ✖, los publicados hace más de N días) o "todo". Una a la vez. Tus
    grabaciones de OBS no se tocan."""
    from clipper import espacio
    if not _ESPACIO["libera"].acquire(blocking=False):
        return {"ok": False, "error": "Ya se está liberando espacio: espera a que termine."}
    try:
        _auto, dias = espacio.opciones(config.load())
        ocupadas = _bases_ocupadas()
        p = espacio.plan(dir_work=P["work"], filas=historial.lista(P["work"], P["outbox"]), familias=_familias_pro(), en_marcha=ocupadas, dias=dias)
        if que == "caches":
            r = espacio.ejecuta(p, dir_work=P["work"], en_marcha=ocupadas, solo_caches=True)
        else:
            if que == "completos":
                p = {**p, "caches": []}

            def _borra(base: str) -> dict:
                res, err = _borra_familia(base, a_papelera=a_papelera)
                return {"ok": False, "error": err[0]} if res is None else {"ok": True, **res}

            r = espacio.ejecuta(p, dir_work=P["work"], borra_completo=_borra, en_marcha=ocupadas)
        total = r["cache"] + r["liberado_completos"]
        if total or r["errores"] or r["completos"] or r["papelera"]:
            espacio.guarda_ultimo(P["work"], {"origen": origen, "cache": r["cache"], "completos": r["completos"], "liberado": total,
                                              "papelera": r["papelera"], "errores": r["errores"][:5]})
            clipper_log(f"Espacio ({origen}): {total / 1073741824:.2f} GB liberados — {r['cache'] / 1073741824:.2f} GB de cachés y "
                        f"{r['completos']} vídeo(s) borrado(s) ({', '.join(r['bases']) or 'ninguno'})"
                        + (f"; {r['papelera'] / 1073741824:.2f} GB fueron a la Papelera" if r["papelera"] else "")
                        + (f"; {len(r['errores'])} aviso(s): {'; '.join(r['errores'][:2])}" if r["errores"] else "."))
        _espacio_olvida()
        return {"ok": True, **r, "liberado": total}
    finally:
        _ESPACIO["libera"].release()


def _barrido_espacio() -> None:
    """Una pasada de la limpieza AUTOMÁTICA: solo si el autor la encendió en «💾 Espacio» (`videos.limpieza_auto`). Nunca toca tus grabaciones de OBS."""
    from clipper import espacio
    auto, _dias = espacio.opciones(config.load())
    if auto:
        _espacio_libera("todo", origen="automático")


def _bucle_espacio() -> None:
    time.sleep(90)                                          # recién arrancado el panel, sin prisa
    while True:
        try:
            _barrido_espacio()
        except Exception as exc:                            # noqa: BLE001 — la limpieza es un extra: nunca tumba el panel
            clipper_log(f"AVISO: la limpieza automática de espacio falló ({exc}).")
        time.sleep(6 * 3600)


@app.get("/api/espacio")
def api_espacio():
    """Lo que enseña «💾 Espacio» de Mis vídeos (con 2 min de caché; `?forzar=1` mide otra vez)."""
    try:
        return jsonify({"ok": True, **_espacio_medida(request.args.get("forzar") == "1")})
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": f"No pude medir el espacio: {type(exc).__name__}: {exc}"}), 500


@app.post("/api/espacio/liberar")
def api_espacio_liberar():
    """«🧹 Liberar» de «💾 Espacio»: `que` = "caches" o "completos" (con `papelera: true`, a la Papelera en vez de borrar del todo). Nunca un vídeo sin publicar
    ni uno en marcha, ni tus grabaciones de OBS."""
    dato = request.get_json(silent=True) or {}
    que = dato.get("que")
    if que not in ("caches", "completos"):
        return jsonify({"ok": False, "error": "Falta «que» (caches o completos)."}), 400
    r = _espacio_libera(que, a_papelera=bool(dato.get("papelera")), origen="a mano")
    return jsonify(r), (200 if r.get("ok") else 409)


@app.post("/api/espacio/opciones")
def api_espacio_opciones():
    """La limpieza automática: encendida o no y a los cuántos días de publicado (se escribe en config.yaml, solo esas dos líneas)."""
    from clipper import espacio
    dato = request.get_json(silent=True) or {}
    auto, dias = dato.get("auto"), dato.get("dias")
    if not isinstance(auto, bool) or dias not in espacio.DIAS_OPCIONES:
        return jsonify({"ok": False, "error": "Faltan «auto» (true o false) y «dias» (3, 7, 14 o 30)."}), 400
    if not espacio.guarda_opciones(auto, int(dias)):
        return jsonify({"ok": False, "error": "No encuentro «limpieza_auto» y «limpieza_dias» en config.yaml."}), 500
    _espacio_olvida()
    return jsonify({"ok": True, "auto": auto, "dias": int(dias)})


@app.post("/job/<jid>/borrar-directo")
def job_delete_all(jid: str):
    """Borra del PC TODO lo de un trabajo: la carpeta de salida entera
    (vídeos, miniaturas, json) y, si ?cache=1, también la caché de análisis
    work/<jid_real>/. NUNCA toca el aprendizaje (work/taste_videos/) ni los
    vídeos ya subidos a YouTube."""
    j = MGR.jobs.get(jid)
    if j and j.state in ("running", "queued", "paused") and MGR.current == jid:
        return jsonify({"ok": False, "error": "Ese trabajo se está procesando; "
                        "cancélalo primero."}), 409

    out = P["outbox"] / jid
    borrado = []
    real_jid = None
    if out.is_dir():
        try:
            rep = json.loads((out / "videos.json").read_text(encoding="utf-8"))
            real_jid = pipeline.job_id(rep.get("source", ""))
        except (OSError, ValueError):
            real_jid = None
        # "Borrar todo del PC" (esta vista) = LIMPIEZA de disco, NO enseña nada
        # al modelo. El aprendizaje ya ocurrió al PROGRAMAR (elegidos = +1, el
        # resto = -1, vía _sync_seleccion). Borrar aquí NO marca negativos.
        shutil.rmtree(out, ignore_errors=True)
        borrado.append("vídeos y miniaturas")

    if request.args.get("cache") == "1":
        for cand in {real_jid, jid}:
            if cand and (P["work"] / cand).is_dir():
                shutil.rmtree(P["work"] / cand, ignore_errors=True)
                borrado.append("caché de análisis")

    with MGR.lock:
        MGR.jobs.pop(jid, None)
        if jid in MGR.queue:
            MGR.queue.remove(jid)
        MGR._save()
    return jsonify({"ok": True, "borrado": borrado})


@app.post("/job/<jid>/titulo")
def job_set_titulo(jid: str):
    """Cambia el título de un vídeo: actualiza manifest.json y regenera la
    miniatura con el título nuevo."""
    b = request.get_json(silent=True) or {}
    arch = Path(b.get("archivo", "")).name
    titulo = re.sub(r"\s+", " ", (b.get("titulo") or "")).strip()[:100]
    folder = P["outbox"] / jid
    mpath = folder / "manifest.json"
    if not titulo or not (folder / arch).is_file() or not mpath.exists():
        return jsonify({"ok": False, "error": "Datos inválidos."}), 400
    try:
        man = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return jsonify({"ok": False, "error": "manifest ilegible."}), 500
    entry = next((m for m in man if m.get("archivo") == arch), None)
    if entry is None:
        return jsonify({"ok": False, "error": "vídeo no está en el manifest."}), 404
    entry["titulo"] = titulo
    mpath.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    # …y en el PLAN de esa versión (Vídeo PRO): «Reintentar» rehace el manifest desde el plan y, sin esto, volvía a
    # poner el 🥇 de la IA en vez del título que elegiste (el autor, 2026-09-25)
    if re.search(r"--pro(-v\d+)?$", jid):
        try:
            from clipper import director
            base = re.sub(r"--pro(-v\d+)?$", "", jid)
            plan = director.cargar(P["work"] / f"{base}--pro", _slug_plan(jid))
            if plan is not None:
                plan["titulo_elegido"] = titulo
                director.guardar(P["work"] / f"{base}--pro", _slug_plan(jid), plan)
        except Exception as exc:                        # noqa: BLE001 — el título ya quedó en el manifest
            clipper_log(f"titulo: no se pudo guardar en el plan: {exc}")

    # regenera la miniatura con el título nuevo (frame + texto)
    mini = None
    cfg = config.load()
    if cfg.get_path("thumbnail.enabled", True):
        clip = folder / arch
        jpg = clip.with_suffix(".jpg")
        try:
            os.environ["PATH"] = _child_env()["PATH"]
            r = thumbs.make_thumbnail(clip, _probe_dur(clip) * 0.42, titulo, jpg,
                                      pipeline._cfg_horizontal(cfg),
                                      entry.get("tag", ""), entry.get("layout", ""))
            if r:
                mini = jpg.name
        except Exception as exc:                        # noqa: BLE001
            clipper_log(f"titulo: miniatura falló: {exc}")
    return jsonify({"ok": True, "miniatura": mini or arch.rsplit(".", 1)[0] + ".jpg"})


_TITULOS_LOCK = threading.Lock()


@app.post("/job/<jid>/titulos-nuevos")
def job_titulos_nuevos(jid: str):
    """«↻» junto a cada título sugerido (el autor, 2026-09-25): cambia SOLO ese título (`{"indice": 0|1|2}`) por uno nuevo,
    con UNA petición a Flash-Lite (no toca las 20 diarias de los Flash que analizan los vídeos). El sustituido no se
    pierde: queda en «títulos anteriores». Ver clipper/titulos.py."""
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    indice = (request.get_json(silent=True) or {}).get("indice")
    if isinstance(indice, bool) or not isinstance(indice, int):
        return jsonify({"ok": False, "error": "Falta qué título cambiar."}), 400
    folder = P["outbox"] / jid
    if not (folder / "manifest.json").is_file():
        return jsonify({"ok": False, "error": "No encuentro este vídeo en el PC (¿lo borraste?)."}), 404
    job = MGR.jobs.get(jid)
    if job and job.state in ("queued", "running", "paused"):
        return jsonify({"ok": False, "error": "Este vídeo se está procesando ahora mismo: cuando termine, pide los "
                                              "títulos nuevos."}), 409
    from clipper import titulos as titulos_mod
    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    with _TITULOS_LOCK:                       # dos clics seguidos no se pisan el manifest
        r = titulos_mod.renueva(folder, P["work"] / f"{base}--pro", _slug_plan(jid), config.load(), indice, jid=jid)
    return jsonify(r), (200 if r.get("ok") else 502)


_CONCEPTOS_LOCK = threading.Lock()


@app.post("/job/<jid>/miniatura-nueva")
def job_miniatura_nueva(jid: str):
    """«↻» junto a cada prompt de miniatura (el autor, 2026-10-03): cambia SOLO ese concepto (`{"indice": 0|1|2}` = A, B, C: su
    prompt, su titular y su cara) por una idea nueva, con UNA petición a Flash-Lite (no toca las 20 diarias de los Flash). El
    sustituido no se pierde: queda en `miniatura_prompts_anteriores`. La imagen de Cloudflare que ya había NO se toca: con «↻ Otra»
    en esa letra se dibuja la nueva. Ver clipper/conceptos_miniatura.py."""
    if not re.fullmatch(r"[\w.\-]+", jid or ""):
        abort(404)
    indice = (request.get_json(silent=True) or {}).get("indice")
    if isinstance(indice, bool) or not isinstance(indice, int):
        return jsonify({"ok": False, "error": "Falta qué miniatura cambiar."}), 400
    folder = P["outbox"] / jid
    if not (folder / "manifest.json").is_file():
        return jsonify({"ok": False, "error": "No encuentro este vídeo en el PC (¿lo borraste?)."}), 404
    job = MGR.jobs.get(jid)
    if job and job.state in ("queued", "running", "paused"):
        return jsonify({"ok": False, "error": "Este vídeo se está procesando ahora mismo: cuando termine, pide el concepto "
                                              "nuevo."}), 409
    from clipper import conceptos_miniatura as conceptos_mod
    base = re.sub(r"--pro(-v\d+)?$", "", jid)
    with _CONCEPTOS_LOCK:                     # dos clics seguidos no se pisan el manifest
        r = conceptos_mod.renueva(folder, P["work"] / f"{base}--pro", _slug_plan(jid), config.load(), indice, jid=jid)
    return jsonify(r), (200 if r.get("ok") else 502)


@app.post("/job/<jid>/borrar-local")
def job_delete_local(jid: str):
    """Borra del PC los .mp4 (+ su .jpg) marcados y los quita del manifest.
    NUNCA toca YouTube."""
    folder = P["outbox"] / jid
    if not folder.is_dir():
        abort(404)
    wanted = set()
    for name in (request.get_json(silent=True) or {}).get("archivos", []):
        n = Path(str(name)).name
        if n.lower().endswith(".mp4") and n == str(name):
            wanted.add(n)
    if not wanted:
        return jsonify({"ok": False, "error": "Nada válido que borrar."}), 400

    borrados = []
    for n in wanted:
        f = folder / n
        if f.is_file():
            for g in (f, folder / (Path(n).stem + ".jpg")):
                try:
                    g.unlink(missing_ok=True)
                except OSError:
                    pass
            borrados.append(n)

    # saca esas entradas del manifest.json para que dejen de aparecer
    mpath = folder / "manifest.json"
    if mpath.exists():
        try:
            man = json.loads(mpath.read_text(encoding="utf-8"))
            man = [m for m in man if m.get("archivo") not in wanted]
            mpath.write_text(json.dumps(man, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        except (OSError, ValueError):
            pass

    return jsonify({"ok": True, "borrados": len(borrados)})


@app.get("/job/<jid>/zip")
def job_zip(jid: str):
    """Descarga en un .zip solo los vídeos que el usuario marcó.
    Uso: /job/<jid>/zip?f=vid01_x.mp4&f=vid02_y.mp4 ..."""
    folder = P["outbox"] / jid
    if not folder.is_dir():
        abort(404)
    picked = []
    for name in request.args.getlist("f"):
        if name != Path(name).name or not name.lower().endswith(".mp4"):
            continue
        f = folder / name
        if f.is_file():
            picked.append(f)
    if not picked:
        abort(400, "No hay vídeos seleccionados válidos.")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as z:
        for f in picked:
            z.write(f, arcname=f.name)

    @after_this_request
    def _cleanup(resp):                          # noqa: ANN001
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return resp

    return send_file(tmp.name, as_attachment=True, mimetype="application/zip",
                     download_name=f"videos_{jid[:32]}_{len(picked)}.zip")


if __name__ == "__main__":
    # 127.0.0.1 por defecto (solo este PC). VIDEOS_UI_HOST=0.0.0.0 para la LAN
    # (móvil en el mismo wifi) — en ese caso pon también PANEL_PASS.
    host = os.environ.get("VIDEOS_UI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    shown = "127.0.0.1" if host in ("127.0.0.1", "localhost") else host
    guard = " [con contraseña]" if _PANEL_PASS else ""
    print(f"\n  UI de Vídeos (Mi canal) en  http://{shown}:{PORT}{guard}\n")
    # use_reloader=False: sin el vigilante de ficheros de Werkzeug, que re-lanza
    # webapp.py como proceso hijo (causaba "dos instancias" y peleas por el puerto,
    # y podía reiniciar la app a mitad de un render).
    threading.Thread(target=_bucle_espacio, daemon=True).start()       # (apagada por defecto: ver «💾 Espacio»)
    app.run(host=host, port=PORT, threaded=True, debug=False, use_reloader=False)
