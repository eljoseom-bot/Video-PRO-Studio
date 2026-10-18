"""Que la IA VEA el vídeo: una opción del panel (⚙️ Opciones, como GPU/CPU; `videos.ia_ve_video`), pedida por el autor el
2026-09-24 para COMPARAR: va a generar el mismo vídeo con ella y sin ella, y según lo que vea se queda fija o se quita.

Cómo (lo eligió el autor después de ver los costes):
1. Se hace una COPIA pequeña de la grabación: 640 px de ancho, sin audio (la voz ya va en la transcripción) y con los
   fotogramas CLAVE de OBS (uno cada 2 s: se sacan en 11 s de una grabación de 23 min; leerla entera tardaba 9,6 min —
   ver `hace_copia`). Google no acepta más de 2 GB por archivo en el plan gratis y las grabaciones pesan ~4,5 GB; la
   copia de 23 min ocupa 2,4 MB.
2. Gemini FLASH-LITE (500 peticiones al día: NO toca las 20 de los Flash buenos) la mira en resolución baja y devuelve,
   por tramos, qué se ve: qué hay en pantalla, qué se hace (clics, escribir, bajar por la página), si se señala algo
   pequeño que convendría ampliar y dónde, textos o cifras importantes, y lo interesante que es mirarlo.
3. Eso va en TEXTO al director (`texto_prompt` → `director.analiza(vision=…)`): el mejor Flash, en la llamada de
   siempre. Así decide la cámara, las tarjetas y las imágenes sabiendo lo que se ve, y no solo lo que se dice.
4. UNA sola vez por grabación: se guarda en `work/<vídeo>--pro/vision.json` y la reusan el Reshuffle, las mejoras y
   «Reintentar» (no se vuelve a mandar el vídeo). El archivo subido se borra de Google en cuanto responde.

Coste (plan gratis, SIN facturación): 0. Tokens (documentación de Google, 2026-09): ~66 por fotograma en resolución baja
→ ~2.000 por minuto de vídeo a 1 fotograma cada 2 s (≈ 46 mil para 23 min), más la transcripción. Cada modelo admite
250 mil tokens por minuto: `fps_para` baja los fotogramas en los vídeos muy largos para caber en una petición.
Privacidad: en el plan gratis Google puede usar lo que se le manda (la grabación ENTERA, también lo que luego se corta)
para mejorar sus productos.

Nunca rompe un vídeo: sin clave, con Flash-Lite saturado, si ffmpeg falla… se avisa en el log y el director trabaja
como siempre, solo con la transcripción. El plan apunta si la IA vio el vídeo (`plan["vision"]`), para comparar.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .util import call_gemini_cadena, log, nombre_modelo, run

VERSION = 2                        # sube si cambia lo que se pide (la caché de vision.json deja de valer); 2: tramos ≤ 30 s
TOKENS_FOTOGRAMA = 66              # resolución baja (ai.google.dev/gemini-api/docs/video-understanding, 2026-09)
PRESUPUESTO = 170_000              # tokens de vídeo por petición: el tope es 250 mil/min y la transcripción también cuenta
FPS_MAX, FPS_MIN = 1.0, 0.1
ANCHO_COPIA = 640
MAX_TRAMOS = 400


def activo(cfg) -> bool:
    """`videos.ia_ve_video` (⚙️ Opciones del panel). Apagada por defecto: es un experimento."""
    try:
        return bool(cfg.get_path("videos.ia_ve_video", False))
    except Exception:  # noqa: BLE001
        return False


def fps_para(dur: float, presupuesto: int = PRESUPUESTO) -> float:
    """Cuántos fotogramas por segundo caben en UNA petición gratis: 1 hasta ~40 min de vídeo; más largo, menos (60 min →
    0,71; 3 h → 0,23). Nunca menos de `FPS_MIN`."""
    if dur <= 0:
        return FPS_MAX
    return max(FPS_MIN, min(FPS_MAX, math.floor(presupuesto / (TOKENS_FOTOGRAMA * dur) * 100) / 100))


def intervalo_clave(video: Path, mira_s: float = 60.0) -> float | None:
    """Cada cuántos segundos guarda la grabación un fotograma CLAVE (uno completo; los demás son diferencias). OBS lo
    tiene en 2 s en las grabaciones del autor (medido el 2026-09-24). None si no se puede saber."""
    import subprocess
    try:
        sal = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", f"%+{mira_s:g}",
                              "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(video)],
                             capture_output=True, text=True, timeout=120).stdout
        claves = sorted(float(p.split(",")[0]) for p in sal.splitlines()
                        if "," in p and "K" in p.split(",")[1] and p.split(",")[0] not in ("", "N/A"))
    except Exception:  # noqa: BLE001
        return None
    pasos = sorted(b - a for a, b in zip(claves, claves[1:]) if b - a > 0.01)
    return round(pasos[len(pasos) // 2], 3) if len(pasos) >= 3 else None


def _dimensiones(video: Path) -> tuple[int, int] | None:
    import subprocess
    try:
        sal = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=60).stdout.strip()
        w, h = (int(x) for x in sal.split(",")[:2])
        return (w, h) if w > 0 and h > 0 else None
    except Exception:  # noqa: BLE001
        return None


def hace_copia(video: Path, destino: Path, fps: float, ancho: int = ANCHO_COPIA) -> tuple[Path, float]:
    """La copia pequeña que se sube: como mucho `fps` fotogramas por segundo, `ancho` px, sin audio. Devuelve (copia,
    fotogramas por segundo que lleva DE VERDAD: es lo que se le dice a Gemini).

    Leer la grabación ENTERA es lo que tarda (AV1 a 1440p60: 9,6 min con la CPU recibiendo cada fotograma, 4,6 min en la
    GPU, para 23 min de vídeo). Si la grabación guarda un fotograma clave cada ≤ 2,5 s (OBS: cada 2 s), se decodifican
    SOLO esos (`-skip_frame nokey`): 11 s para esos 23 min, y caen justo en su segundo (el filtro de 1 por segundo coge
    la imagen ~0,5 s tarde). Así la IA ve 1 fotograma cada 2 s: de sobra para saber qué hay en pantalla en cada tramo."""
    from .movimiento import _cuda_disponible
    destino = Path(destino)
    paso = intervalo_clave(video)
    solo_claves = paso is not None and 0.2 <= paso <= 2.5
    if solo_claves:
        fps = min(fps, 1.0 / paso)
    dims = _dimensiones(video)
    alto = max(2, int(round(ancho * dims[1] / dims[0] / 2)) * 2) if dims else None

    def cmd(gpu: bool) -> list[str]:
        if gpu and alto:                                   # los fotogramas no salen de la GPU hasta estar ya pequeños
            entrada = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
            filtro = f"fps={fps:g},scale_cuda={ancho}:{alto},hwdownload,format=nv12"
        else:
            entrada = []
            filtro = f"fps={fps:g},scale={ancho}:-2"
        return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *(["-skip_frame", "nokey"] if solo_claves else []),
                *entrada, "-i", str(video), "-an", "-sn", "-dn", "-vf", filtro,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(destino)]
    try:
        run(cmd(_cuda_disponible()))
    except Exception:  # noqa: BLE001 — la GPU falló (driver, formato raro): por CPU
        run(cmd(False))
    if not destino.is_file() or destino.stat().st_size < 1000:
        raise RuntimeError("ffmpeg no dejó la copia pequeña del vídeo")
    return destino, round(fps, 3)


def _mmss(t: float) -> str:
    m, s = divmod(int(max(0.0, t)), 60)
    return f"{m}:{s:02d}"


def transcripcion_compacta(words: list[dict], cada: float = 10.0) -> str:
    """La transcripción en líneas de ~`cada` segundos con su segundo de inicio («[83s] …»), para que la IA que mira el
    vídeo sepa de qué se habla en cada momento sin gastar tantos tokens como la transcripción del director."""
    lineas, actual, t0 = [], [], None
    for w in words:
        txt = str(w.get("word", "")).strip()
        if not txt:
            continue
        t = float(w.get("start", 0))
        if t0 is None:
            t0 = t
        if t - t0 >= cada and actual:
            lineas.append(f"[{t0:.0f}s] " + " ".join(actual))
            actual, t0 = [], t
        actual.append(txt)
    if actual:
        lineas.append(f"[{t0:.0f}s] " + " ".join(actual))
    return "\n".join(lineas)


_PROMPT = """Eres los OJOS del editor de vídeo del canal de YouTube «{canal}». Te paso la GRABACIÓN de un vídeo (sin
audio; {fps_txt}; dura {dur_txt}) y su transcripción con el segundo en que empieza cada línea. Otra IA va a editarlo —
qué encuadre mostrar en cada momento (la CARA del presentador, la PANTALLA de su PC, o un ZOOM a una parte de la
pantalla), dónde poner tarjetas con datos, imágenes e ilustraciones, qué silencios cortar— pero NO puede ver el vídeo:
solo leerá lo que tú le cuentes.

Describe lo que se VE, en tramos seguidos que cubran el vídeo de principio a fin, con los segundos desde el inicio de la
grabación (los mismos de la transcripción). Empieza un tramo nuevo cada vez que cambia lo que se ve, y NUNCA más de 30 s
por tramo: si la misma pantalla sigue más tiempo, pártelo cada 30 s como mucho y di en cada parte qué se hace o se señala
en ese momento (un tramo de 1-2 min con «interés 2» no le dice al editor CUÁNDO hay algo que mirar). Para cada tramo:
- "pantalla": qué hay en la pantalla del PC, concreto (programa o web y qué parte: «Google Search Console, informe de
  rendimiento», «editor de código con un archivo Python», «escritorio vacío»). Si solo se ve al presentador: "".
- "accion": qué pasa: «quieto», «mueve el ratón», «hace clic en…», «escribe…», «baja por la página», «carga una
  página», «reproduce un vídeo»…
- "detalle": si se señala o se ve algo PEQUEÑO que convendría AMPLIAR (una cifra, un botón, un campo, un menú): qué es y
  dónde está (arriba/abajo, izquierda/derecha/centro). Si no, "".
- "texto": un texto o una cifra IMPORTANTE que se lea en pantalla (un titular, un resultado), tal cual. Si no, "".
- "presentador": solo si pasa algo notable en su cámara (se ríe, señala la pantalla, enseña un objeto, no está). Si
  no, "".
- "interes": 1, 2 o 3 — cuánto merece la pena MIRAR la pantalla ahí (1 = nada que ver, 3 = hay que verlo sí o sí).
No inventes: si algo no se distingue, no lo pongas. Responde SOLO con el JSON {{"tramos": [...]}}.

TRANSCRIPCIÓN:
{transcripcion}
"""

_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from pydantic import BaseModel

            class Tramo(BaseModel):
                inicio: float
                fin: float
                pantalla: str = ""
                accion: str = ""
                detalle: str = ""
                texto: str = ""
                presentador: str = ""
                interes: int = 1

            class Vision(BaseModel):
                tramos: list[Tramo]

            _ESQUEMA = Vision
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQUEMA = False
    return _ESQUEMA or None


def _parsea(texto: str) -> list:
    """El JSON de la IA → la lista de tramos (acepta {"tramos": [...]} o la lista suelta, y un ```json alrededor)."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    d = json.loads(t)
    tramos = d.get("tramos") if isinstance(d, dict) else d
    if not isinstance(tramos, list):
        raise ValueError("la respuesta no trae una lista de tramos")
    return tramos


def _valida(tramos: list, dur: float) -> list[dict]:
    """Solo tramos con sentido: dentro del vídeo, en orden, sin solaparse, con textos cortos."""
    out: list[dict] = []
    for x in tramos if isinstance(tramos, list) else []:
        if not isinstance(x, dict):
            continue
        try:
            a, b = float(x.get("inicio")), float(x.get("fin"))
        except (TypeError, ValueError):
            continue
        a, b = max(0.0, min(a, dur)), max(0.0, min(b, dur))
        if b - a < 0.5:
            continue
        try:
            interes = int(round(float(x.get("interes") or 1)))
        except (TypeError, ValueError):
            interes = 1
        out.append({"inicio": round(a, 1), "fin": round(b, 1),
                    "pantalla": str(x.get("pantalla") or "").strip()[:120],
                    "accion": str(x.get("accion") or "").strip()[:100],
                    "detalle": str(x.get("detalle") or "").strip()[:120],
                    "texto": str(x.get("texto") or "").strip()[:120],
                    "presentador": str(x.get("presentador") or "").strip()[:80],
                    "interes": max(1, min(3, interes))})
    out.sort(key=lambda t: t["inicio"])
    limpio: list[dict] = []
    for t in out:
        if limpio and t["inicio"] < limpio[-1]["fin"]:
            t = {**t, "inicio": limpio[-1]["fin"]}
            if t["fin"] - t["inicio"] < 0.5:
                continue
        limpio.append(t)
    return limpio[:MAX_TRAMOS]


def texto_prompt(v: dict | None) -> str:
    """Lo que vio la IA, para el prompt del director (`{vision}` en `director._PROMPT`)."""
    if not v or not v.get("ok") or not v.get("tramos"):
        return "(no disponible en este vídeo: decide solo con la transcripción, como siempre)"
    lineas = []
    for t in v["tramos"]:
        partes = []
        if t.get("pantalla"):
            partes.append(f"pantalla: {t['pantalla']}")
        if t.get("accion"):
            partes.append(t["accion"])
        if t.get("detalle"):
            partes.append(f"detalle pequeño: {t['detalle']}")
        if t.get("texto"):
            partes.append(f"se lee «{t['texto']}»")
        if t.get("presentador"):
            partes.append(f"presentador: {t['presentador']}")
        partes.append(f"interés {t.get('interes', 1)}/3")
        lineas.append(f"[{t['inicio']:.0f}-{t['fin']:.0f}s] " + "; ".join(partes))
    return ("otra IA miró la grabación (" + (f"{v.get('fps', 1):g} fotograma por segundo" if v.get("fps", 1) >= 1
                                             else f"1 fotograma cada {1 / v['fps']:.0f} s")
            + ") y esto es lo que SE VE en cada tramo (segundos del original, los de la transcripción). Úsalo para la "
              "CÁMARA (si hay algo que mirar en la pantalla, muéstrala; si se señala un detalle pequeño, "
              "\"pantalla_zoom\"; si no hay nada que ver, la cara), para las TARJETAS (un dato que se ve), para las "
              "IMÁGENES e ILUSTRACIONES (no ilustres lo que la pantalla ya enseña) y para no cortar un silencio "
              "mientras pasa algo en pantalla. Cuando choque con lo que deduces solo del texto, manda esto:\n   "
            + "\n   ".join(lineas))


def _cadena_lite(cfg, client) -> list[str]:
    """Los Flash-Lite que sirven hoy, del mejor al peor (500 peticiones al día: no tocan la cuota de los Flash buenos)."""
    from . import modelos as _m
    return _m.cadena_lite(cfg, client)


def _espera_activo(client, f, max_s: float = 600.0, _dormir=time.sleep):
    """Google procesa el vídeo subido antes de dejar usarlo (PROCESSING → ACTIVE)."""
    t0 = time.time()
    while True:
        estado = str(getattr(getattr(f, "state", None), "name", getattr(f, "state", "")) or "")
        if estado.endswith("ACTIVE"):
            return f
        if estado.endswith("FAILED"):
            raise RuntimeError(f"Google no pudo procesar el vídeo subido ({getattr(f, 'error', '') or 'sin detalle'})")
        if time.time() - t0 > max_s:
            raise RuntimeError(f"Google tardó más de {max_s / 60:.0f} min en procesar el vídeo subido")
        _dormir(3)
        f = client.files.get(name=f.name)


def lee(work: Path) -> dict | None:
    """Lo que vio la IA de esta grabación, si ya lo miró (y con esta versión de la petición)."""
    try:
        d = json.loads((Path(work) / "vision.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("v") == VERSION and d.get("ok") and d.get("tramos") else None


def _guarda(work: Path, d: dict) -> None:
    try:
        p = Path(work) / "vision.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        log(f"AVISO: no se pudo guardar lo que vio la IA ({exc}).")


def _motivo(exc: Exception) -> str:
    try:
        from .director import _motivo_corto
        return _motivo_corto(str(exc))
    except Exception:  # noqa: BLE001
        return str(exc).replace("\n", " ")[:160]


def obten(video: Path, words: list[dict], dur: float, cfg, work: Path, canal: str = "", _gemini=None) -> dict:
    """Lo que SE VE en el vídeo, por tramos: de `vision.json` si esta grabación ya se miró; si no, se mira ahora (UNA
    petición a Flash-Lite). Devuelve {"ok": True, "modelo", "fps", "tramos": [...], "tokens_entrada", ...} o
    {"ok": False, "motivo"}: nunca lanza (el vídeo sigue sin esto). `_gemini` = (client, types) para las pruebas."""
    previo = lee(work)
    if previo:
        log(f"👁 La IA ya vio este vídeo antes ({len(previo['tramos'])} tramos): reuso lo que vio, sin otra petición.")
        return previo
    if _gemini is None:
        from .director import _gemini as _abre
        g = _abre(cfg)
        if not g:
            log("AVISO: la IA no puede ver el vídeo (no hay clave de Gemini o la IA está apagada): sigo sin eso.")
            return {"ok": False, "motivo": "no hay clave de Gemini o la IA está apagada"}
        _gemini = g[:2]
    client, types = _gemini
    copia = Path(work) / "vision_copia.mp4"
    subido = None
    try:
        log(f"👁 La IA va a ver el vídeo: preparo una copia pequeña ({ANCHO_COPIA} px, sin audio)…")
        copia, fps = hace_copia(video, copia, fps_para(dur))
        log(f"  copia lista ({copia.stat().st_size / 1e6:.1f} MB, "
            + (f"{fps:g} fotograma por segundo" if fps >= 1 else f"1 fotograma cada {1 / fps:.1f} s")
            + "); la subo a Gemini…")
        subido = client.files.upload(file=str(copia), config={"mime_type": "video/mp4",
                                                              "display_name": f"vision-{Path(work).name}"[:120]})
        subido = _espera_activo(client, subido)
        modelos = _cadena_lite(cfg, client)
        log(f"  la mira {nombre_modelo(modelos[0])} (Flash-Lite: no gasta de las 20 peticiones de los mejores modelos)…")
        archivo = types.FileData(file_uri=subido.uri, mime_type=getattr(subido, "mime_type", None) or "video/mp4")
        texto = _PROMPT.format(canal=canal or "de YouTube", dur_txt=f"{_mmss(dur)} ({dur:.0f} s)",
                               fps_txt=(f"{fps:g} fotograma por segundo" if fps >= 1
                                        else f"1 fotograma cada {1 / fps:.1f} s"),
                               transcripcion=transcripcion_compacta(words) or "(sin voz)")
        try:
            crudo, modelo, uso = call_gemini_cadena(
                client, types, modelos, [types.Part(file_data=archivo, video_metadata=types.VideoMetadata(fps=fps)), texto],
                valida=_parsea, reintento_s=0.0, pasadas=2, timeout_s=300.0, esquema=_esquema(), etiqueta="vision",
                config_extra={"media_resolution": types.MediaResolution.MEDIA_RESOLUTION_LOW})
        except Exception as exc:  # noqa: BLE001
            from .director import es_saturacion
            if es_saturacion(exc):
                raise
            # (sin haberlo probado con Google de verdad: si rechaza la resolución baja o los fotogramas por segundo,
            # se pide sin esas dos opciones — la copia ya lleva pocos fotogramas, así que sigue cabiendo)
            log(f"  Gemini no aceptó la petición con resolución baja ({_motivo(exc)}); la repito sin esas opciones…")
            crudo, modelo, uso = call_gemini_cadena(
                client, types, modelos, [types.Part(file_data=archivo), texto], valida=_parsea, reintento_s=0.0,
                pasadas=1, timeout_s=300.0, esquema=_esquema(), etiqueta="vision")
        tramos = _valida(crudo, dur)
        if not tramos:
            raise ValueError("la IA no describió ningún tramo")
        visto = sum(t["fin"] - t["inicio"] for t in tramos)
        out = {"v": VERSION, "ok": True, "modelo": modelo, "nombre": nombre_modelo(modelo), "fps": fps,
               "tramos": tramos, "cubre_pct": round(100 * visto / max(1.0, dur)),
               "tokens_entrada": uso.get("entrada"), "tokens_salida": uso.get("salida"), "creado": time.time()}
        _guarda(work, out)
        log(f"👁 La IA vio el vídeo ({out['nombre']}): {len(tramos)} tramos descritos ({out['cubre_pct']}% del vídeo) — "
            f"{uso.get('entrada')} tokens de entrada y {uso.get('salida')} de salida.")
        return out
    except Exception as exc:  # noqa: BLE001 — la pasada es un extra: el vídeo sigue sin ella
        motivo = _motivo(exc)
        log(f"AVISO: la IA no pudo ver el vídeo ({motivo}); el director trabaja solo con la transcripción, como siempre.")
        return {"ok": False, "motivo": motivo}
    finally:
        if subido is not None:
            try:
                client.files.delete(name=subido.name)               # que el vídeo no se quede en Google (caduca a las 48 h)
            except Exception:  # noqa: BLE001
                pass
        try:
            copia.unlink(missing_ok=True)
        except OSError:
            pass


def resumen(v: dict | None) -> dict | None:
    """Lo que se apunta en el plan y en `analisis_ia.json` (para comparar versiones con y sin la pasada)."""
    if not v:
        return None
    if not v.get("ok"):
        return {"ok": False, "motivo": v.get("motivo") or ""}
    return {"ok": True, "modelo": v.get("modelo"), "nombre": v.get("nombre"), "fps": v.get("fps"),
            "tramos": len(v.get("tramos") or []), "tokens_entrada": v.get("tokens_entrada")}


# ------------------------------------------------------------------ pruebas (sin Gemini de verdad: uno falso)
def prueba() -> list[dict]:
    import shutil
    import tempfile
    filas: list[dict] = []

    def r(caso, ok, detalle=""):
        filas.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    r("1 fotograma por segundo hasta ~40 min; menos en los largos (cabe en una petición gratis: 250 mil tokens/min)",
      fps_para(23 * 60) == 1.0 and fps_para(60 * 60) == 0.71 and fps_para(3 * 3600) == 0.23 and fps_para(0) == 1.0
      and TOKENS_FOTOGRAMA * fps_para(3 * 3600) * 3 * 3600 <= PRESUPUESTO, (fps_para(60 * 60), fps_para(3 * 3600)))
    ws = [{"word": f"p{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(60)]
    tc = transcripcion_compacta(ws)
    r("la transcripción para la IA que mira va en líneas de ~10 s con su segundo", tc.splitlines()[0].startswith("[0s] p0")
      and tc.splitlines()[1].startswith("[10s] p20") and len(tc.splitlines()) == 3, tc)
    crudo = [{"inicio": 5, "fin": 20, "pantalla": "Search Console", "accion": "mueve el ratón", "interes": 3},
             {"inicio": 0, "fin": 6, "pantalla": "", "accion": "quieto", "interes": "2"},
             {"inicio": 20, "fin": 20.2, "pantalla": "x"}, {"inicio": "a", "fin": 3}, "basura",
             {"inicio": 30, "fin": 999, "detalle": "la cifra de clics, arriba a la izquierda", "interes": 9}]
    v = _valida(crudo, 100.0)
    r("los tramos de la IA se ordenan, no se solapan, no se salen del vídeo y lo raro se descarta",
      [(t["inicio"], t["fin"]) for t in v] == [(0.0, 6.0), (6.0, 20.0), (30.0, 100.0)] and v[2]["interes"] == 3
      and v[0]["interes"] == 2, v)
    r("se entiende la respuesta con o sin {\"tramos\": …} y con ```json alrededor",
      _parsea('{"tramos": [{"inicio": 1, "fin": 2}]}') == [{"inicio": 1, "fin": 2}]
      and _parsea('```json\n[{"inicio": 1, "fin": 2}]\n```') == [{"inicio": 1, "fin": 2}])
    tp = texto_prompt({"ok": True, "fps": 1.0, "tramos": v})
    r("el texto para el director lleva cada tramo con sus segundos, el detalle a ampliar y el interés",
      "[30-100s]" in tp and "detalle pequeño: la cifra de clics" in tp and "interés 3/3" in tp and "CÁMARA" in tp, tp[:300])
    r("sin pasada (apagada o fallida) el director lo sabe y sigue como siempre",
      texto_prompt(None).startswith("(no disponible") and texto_prompt({"ok": False}).startswith("(no disponible"))

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-vision-"))
    try:
        # la copia pequeña, con ffmpeg de verdad: clips de 12 s con sonido (la copia sale sin él)
        import subprocess

        def flujos(p):
            sal = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,nb_frames",
                                  "-of", "json", str(p)], capture_output=True, text=True).stdout
            return json.loads(sal).get("streams") or []
        clip = tmp / "clip.mp4"
        try:
            base_cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=1280x720:rate=30:duration=12", "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]
            run(base_cmd + ["-g", "240", str(clip)])                        # un fotograma clave cada 8 s
            copia, f1 = hace_copia(clip, tmp / "copia.mp4", 1.0)
            st = flujos(copia)
            r("con fotogramas clave espaciados (8 s) se lee todo el vídeo: 640 px, sin audio, 1 fotograma por segundo",
              f1 == 1.0 and len(st) == 1 and st[0]["codec_type"] == "video" and st[0]["width"] == 640
              and 11 <= int(st[0].get("nb_frames") or 0) <= 13, (f1, st))
            clip2 = tmp / "clip_obs.mp4"
            run(base_cmd + ["-g", "60", str(clip2)])                        # cada 2 s, como graba OBS al autor
            copia2, f2 = hace_copia(clip2, tmp / "copia2.mp4", 1.0)
            st2 = flujos(copia2)
            r("con un fotograma clave cada 2 s (OBS) se leen SOLO esos (segundos en vez de minutos): 1 cada 2 s, sin audio",
              intervalo_clave(clip2) == 2.0 and f2 == 0.5 and len(st2) == 1 and st2[0]["width"] == 640
              and 5 <= int(st2[0].get("nb_frames") or 0) <= 7, (intervalo_clave(clip2), f2, st2))
        except Exception as exc:  # noqa: BLE001
            r("la copia pequeña sale bien (640 px, sin audio, los fotogramas que tocan)", False, exc)

        # la pasada entera con un Gemini FALSO: sube, espera a que esté lista, pregunta a un Flash-Lite, guarda y borra
        from google.genai import types as T  # (solo los tipos: nada va a la red)
        from . import util as _util
        pasos: list = []

        class _F:
            def __init__(self, estado):
                self.name, self.uri, self.mime_type = "files/abc", "https://x/files/abc", "video/mp4"
                self.state = type("S", (), {"name": estado})()

        class _Files:
            def upload(self, file, config=None):
                pasos.append(("sube", Path(file).name, Path(file).stat().st_size > 0))
                return _F("PROCESSING")

            def get(self, name):
                pasos.append(("mira", name))
                return _F("ACTIVE")

            def delete(self, name):
                pasos.append(("borra", name))

        cliente = type("C", (), {"files": _Files()})()
        real_cadena, real_lite, real_espera = _util.call_gemini_cadena, globals()["_cadena_lite"], globals()["_espera_activo"]
        pedido: dict = {}

        def cadena_falsa(client, types, modelos, prompt, **k):
            pedido.update(modelos=modelos, prompt=prompt, k=k)
            return (_parsea(json.dumps({"tramos": crudo[:2]})), modelos[0], {"entrada": 1234, "salida": 99})
        try:
            globals()["call_gemini_cadena"] = cadena_falsa
            globals()["_cadena_lite"] = lambda cfg, client: ["gemini-3.5-flash-lite"]
            globals()["_espera_activo"] = lambda client, f: real_espera(client, f, _dormir=lambda s: None)
            work = tmp / "work"
            work.mkdir()
            res = obten(clip, ws, 12.0, None, work, canal="Mi canal", _gemini=(cliente, T))
            vid = pedido.get("prompt", [None])[0]
            r("la pasada sube la copia, espera a que Google la procese, pregunta a un Flash-Lite en resolución baja con "
              "el vídeo y el texto, guarda lo que vio y BORRA el vídeo de Google y la copia del PC",
              res.get("ok") and res["tramos"] and pasos[0][0] == "sube" and ("mira", "files/abc") in pasos
              and pasos[-1] == ("borra", "files/abc") and pedido["modelos"] == ["gemini-3.5-flash-lite"]
              and pedido["k"].get("etiqueta") == "vision"
              and pedido["k"]["config_extra"]["media_resolution"] == T.MediaResolution.MEDIA_RESOLUTION_LOW
              and vid is not None and vid.video_metadata.fps == 1.0 and "Mi canal" in pedido["prompt"][1]
              and (work / "vision.json").is_file() and not (work / "vision_copia.mp4").exists(), (res, pasos))
            n_antes = len(pasos)
            otra = obten(clip, ws, 12.0, None, work, _gemini=(cliente, T))
            r("una sola vez por grabación: la segunda vez (Reshuffle, mejora) reusa lo que vio, sin subir ni preguntar",
              otra.get("ok") and len(pasos) == n_antes and otra["tramos"] == res["tramos"])

            intentos: list = []

            def cadena_exigente(client, types, modelos, prompt, **k):        # un Gemini que no acepta la resolución baja
                intentos.append((bool(k.get("config_extra")), prompt[0].video_metadata is not None))
                if k.get("config_extra"):
                    raise RuntimeError("400 INVALID_ARGUMENT: media_resolution is not supported for this model")
                return (_parsea(json.dumps({"tramos": crudo[:2]})), modelos[0], {"entrada": 900, "salida": 80})
            globals()["call_gemini_cadena"] = cadena_exigente
            work3 = tmp / "work3"
            work3.mkdir()
            res3 = obten(clip, ws, 12.0, None, work3, _gemini=(cliente, T))
            r("si Google rechaza la resolución baja o los fotogramas por segundo, repite la petición sin esas opciones "
              "(una vez) y la IA ve el vídeo igual", res3.get("ok") and intentos == [(True, True), (False, False)], intentos)

            def cadena_rota(*a, **k):
                intentos.append("saturado")
                raise RuntimeError("503 UNAVAILABLE: high demand")
            intentos.clear()
            globals()["call_gemini_cadena"] = cadena_rota
            work2 = tmp / "work2"
            work2.mkdir()
            mal = obten(clip, ws, 12.0, None, work2, _gemini=(cliente, T))
            r("si Flash-Lite está saturado: no revienta, lo dice, no guarda nada, no repite la petición de gusto y borra "
              "igual el vídeo subido", mal.get("ok") is False and mal.get("motivo") and not (work2 / "vision.json").exists()
              and intentos == ["saturado"] and pasos[-1] == ("borra", "files/abc")
              and resumen(mal) == {"ok": False, "motivo": mal["motivo"]}, (mal, intentos))
        finally:
            globals()["call_gemini_cadena"] = real_cadena
            globals()["_cadena_lite"] = real_lite
            globals()["_espera_activo"] = real_espera
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # la opción del panel: se escribe en config.yaml tocando solo esa línea (como GPU/CPU)
    from . import config as _config
    tmp2 = Path(tempfile.mkdtemp(prefix="zz-prueba-vision-cfg-"))
    try:
        y = tmp2 / "config.yaml"
        y.write_text("videos:\n  # comentario\n  ia_ve_video: false   # la opción\n  otra: 1\n", encoding="utf-8")
        ok1 = _config.guarda_vision(True, ruta=y)
        t1 = y.read_text(encoding="utf-8")
        ok2 = _config.guarda_vision(False, ruta=y)
        t2 = y.read_text(encoding="utf-8")
        r("la opción «La IA ve el vídeo» se guarda en config.yaml tocando SOLO esa línea (sin perder comentarios)",
          ok1 and ok2 and "  ia_ve_video: true   # la opción" in t1 and t2.count("# comentario") == 1
          and "  ia_ve_video: false   # la opción" in t2 and "  otra: 1" in t2, t1)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)
    # lo que le LLEGA de verdad al director (el autor, 2026-09-25: «verifica que cuando pone vista por IA sí lo haya visto y
    # cuando no, que no»): con la vista, el prompt lleva lo que vio la IA; sin ella, «no disponible» (Gemini falso)
    from . import director
    cap: dict = {}
    real_dir = director.call_gemini_cadena

    def falso_dir(client, types, modelos, prompt, system=None, valida=None, **k):
        cap.setdefault("p", []).append(prompt)
        plan = {"recortes": [], "camara": [], "tarjetas": [], "imagenes": [], "ilustraciones": [],
                "titulos_sugeridos": ["a", "b", "c"], "miniatura_prompts": ["x", "y", "z"], "capitulos": [],
                "gancho_descripcion": "g", "articulo_relacionado": "", "hashtag": ""}
        return plan, modelos[0], {"entrada": 1, "salida": 1}
    try:
        director.call_gemini_cadena = falso_dir
        from . import config as _config
        cfg_d = _config.load()
        palabras = [{"word": f"p{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(40)]
        vista = {"ok": True, "tramos": [{"inicio": 0.0, "fin": 20.0, "pantalla": "Search Console, informe de rendimiento",
                                         "accion": "subraya una frase", "detalle": "", "texto": "Clics 1.234",
                                         "presentador": "", "interes": 3}]}
        con = director.analiza(palabras, [(0.0, 20.0)], cfg_d, es_completo=True, vision=texto_prompt(vista))
        sin = director.analiza(palabras, [(0.0, 20.0)], cfg_d, es_completo=True, vision=None)
        if con is None or sin is None or len(cap.get("p", [])) < 2:
            r("el director recibe lo que vio la IA (omitido: no hay clave de Gemini)", True)
        else:
            p_con, p_sin = cap["p"][0], cap["p"][1]
            r("CON la vista, el prompt del director lleva lo que vio la IA (pantalla, acción, texto que se lee); SIN ella, "
              "«no disponible» y nada de lo visto",
              "Search Console, informe de rendimiento" in p_con and "subraya una frase" in p_con
              and "no disponible en este vídeo" in p_sin and "Search Console, informe de rendimiento" not in p_sin,
              p_sin[p_sin.find("LO QUE SE VE"):][:160])
    finally:
        director.call_gemini_cadena = real_dir
    return filas


if __name__ == "__main__":
    import sys
    filas_ = prueba()
    for f in filas_:
        print(("OK   " if f["ok"] else "FALLA") + " " + f["caso"] + ("" if f["ok"] else f"  -> {f['detalle']}"))
    sys.exit(1 if any(not f["ok"] for f in filas_) else 0)
