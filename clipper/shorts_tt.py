"""«Shorts · TikTok» (Tools): un Short vertical MUY editado a partir de un vídeo ya hablado (el autor, 16-oct-2026: «una herramienta de shorts… video de un minuto, dos minutos o menos…
meterle toda una edición bien monstruosa… cortar todos los silencios… un cambio, una animación, algo que pase cada dos segundos… la IA es el orquestador»).

DOS fuentes: (1) subes un vídeo (pantalla + cámara, o ya vertical) o (2) «enviar» el trozo de un Short que salió de un vídeo horizontal (se toma ESE trozo del vídeo ORIGINAL).
Es un producto APARTE de Vídeos PRO (no importa su director, su render ni sus tarjetas: copias propias `st_*`); solo comparte lo que es infraestructura (config, ffmpeg, Whisper, la cadena
de modelos de Gemini, el registro de uso).

Flujo: audio → Whisper → todos los silencios fuera (`st_voz`) → UNA petición a Gemini (`st_plan`: textos para YouTube/TikTok/Instagram + eventos de significado) → eventos anclados a lo
que dices, verificados y rellenados para que pase algo cada ≤ 2 s → actividad de la pantalla → montaje vertical (`st_render`: cámara arriba, pantalla en medio SIN la cámara, subtítulos
abajo, tarjetas/escenas/ilustraciones/palabras clave, sonido en cada corte y cada animación) → `shorts_tt/<id>/short.mp4` + `estado.json` con los textos.
Todo cuelga de `shorts_tt/<id>/` (fuera de `videos/` y `work/`: no sale en «Mis vídeos» ni lo toca su 🗑).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from . import config, st_ilus, st_plan, st_render, st_tarjetas as T, st_voz
from .util import log

_ID = re.compile(r"st-\d{8}-\d{6}-[0-9a-f]{4}")
EXT_OK = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
MAX_ORIGEN_S = 600           # de un trozo de más de 10 min no se hace un Short: hay que elegir un trozo
AVISO_LARGO_S = 180          # un Short de YouTube dura como mucho 3 min


def raiz(dir_raiz=None) -> Path:
    p = Path(dir_raiz) if dir_raiz else config.ROOT / "shorts_tt"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _id_nuevo() -> str:
    return f"st-{time.strftime('%Y%m%d-%H%M%S')}-{hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:4]}"


def carpeta(sid: str, dir_raiz=None) -> Path:
    if not _ID.fullmatch(sid or ""):
        raise ValueError("id raro")
    return raiz(dir_raiz) / sid


def lee(sid: str, dir_raiz=None) -> dict | None:
    try:
        return json.loads((carpeta(sid, dir_raiz) / "estado.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def guarda(sid: str, est: dict, dir_raiz=None) -> None:
    from .util import reemplaza
    p = carpeta(sid, dir_raiz) / "estado.json"
    tmp = p.with_suffix(f".{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    reemplaza(tmp, p)


def lista(dir_raiz=None) -> list[dict]:
    out = []
    for d in sorted(raiz(dir_raiz).iterdir(), reverse=True):
        if d.is_dir() and _ID.fullmatch(d.name):
            e = lee(d.name, dir_raiz)
            if e:
                out.append(e)
    return out


def borra(sid: str, dir_raiz=None) -> bool:
    """Borra el Short (su carpeta entera). Nunca toca el vídeo de origen si era uno tuyo («enviar»): solo lo que está dentro de su carpeta."""
    try:
        d = carpeta(sid, dir_raiz)
    except ValueError:
        return False
    if not d.is_dir():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


# ------------------------------------------------------------------ el canal
def contexto_canal(cfg, canal: str) -> dict:
    info = (cfg.get_path(f"videos.canales.{canal}") or {}) if cfg is not None and canal else {}
    marca = {**(dict(cfg.get_path("videos.marca") or {}) if cfg is not None else {}), **{k: v for k, v in (info.get("marca") or {}).items() if v}}
    web = str((info.get("enlaces") or {}).get("web") or "")
    try:
        from urllib.parse import urlparse
        host = (urlparse(web).hostname or "").removeprefix("www.")
    except ValueError:
        host = ""
    cam = info.get("camara") if isinstance(info.get("camara"), dict) else None
    return {"info": info, "marca": marca, "nombre": str(info.get("nombre") or canal or ""), "web": host, "camara": cam, "contexto": str(info.get("contexto") or "")}


def canales(cfg) -> list[dict]:
    """Los canales entre los que elegir: [{id, nombre, camara (si tiene medida)}]."""
    out = []
    from .config import canales_visibles
    for k, v in canales_visibles(cfg).items():
        if isinstance(v, dict):
            out.append({"id": k, "nombre": str(v.get("nombre") or k), "camara": bool(isinstance(v.get("camara"), dict))})
    return out


# ------------------------------------------------------------------ crear
def nuevo(canal: str, origen: dict, dir_raiz=None) -> str:
    """Crea la carpeta del Short y su `estado.json` en «en cola». `origen`: {tipo: subida|video, archivo (nombre), ruta (el vídeo), desde, hasta, jid, titulo_fuente, pista}."""
    sid = _id_nuevo()
    carpeta(sid, dir_raiz).mkdir(parents=True, exist_ok=True)
    guarda(sid, {"id": sid, "ts": time.time(), "canal": canal, "origen": origen, "estado": "cola", "paso": "En cola…", "pct": 0.0, "error": "", "avisos": []}, dir_raiz)
    return sid


def rango_en_original(entrada: dict, corto: dict) -> tuple[float, float]:
    """El tramo de la grabación ORIGINAL que corresponde a un Short de un vídeo ya montado (reloj del montaje → reloj del original, con los cortes de `segments`)."""
    from .pipeline import tiempo_original
    segs = [(float(a), float(b)) for a, b in (entrada.get("segments") or [])]
    ini = float(corto.get("desde_min") or 0.0)
    fin = ini + float(corto.get("duracion") or 0.0)
    if not segs:
        return ini, fin
    return round(tiempo_original(ini, segs, "inicio"), 3), round(tiempo_original(fin, segs, "fin"), 3)


def _extrae_audio(src: Path, destino: Path, desde: float, dur: float) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + (["-ss", f"{desde:.3f}"] if desde > 0 else []) + (["-t", f"{dur:.3f}"] if dur > 0 else []) + ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destino)]
    r = subprocess.run(cmd, capture_output=True, timeout=900)
    if r.returncode != 0 or not destino.is_file():
        raise RuntimeError("No pude sacar el audio del vídeo (¿tiene sonido?).")


def _director(cfg, prompt: str, canal: str, valida, _llama=None):
    """UNA petición a Gemini con la cadena de modelos de siempre (el mejor primero). -> (resultado validado, modelo)."""
    if _llama is not None:
        return _llama(prompt, valida)
    from . import modelos as _m
    from . import util as _util
    from .director import _gemini as _abre
    g = _abre(cfg)
    if not g:
        raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso».")
    client, types = g[:2]
    res, modelo, _u = _util.call_gemini_cadena(client, types, _m.cadena(cfg, client), prompt, system=st_plan.SYSTEM.format(canal=canal or "tu canal"), valida=valida, reintento_s=60.0, pasadas=1, timeout_s=180.0,
                                               esquema=st_plan.esquema(), etiqueta="short_tt")
    return res, modelo


def _json_de(texto: str):
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1] if i >= 0 and j > i else t)


class _SinLlamar(Exception):
    """(marca interna: se reusa el plan guardado, no se llama a Gemini)"""


def _tonos_y_numeros(eventos: list[dict]) -> list[dict]:
    """Las escenas alternan fondo (la 1.ª NARANJA, la 2.ª crema…) y los «punto» se numeran 1, 2, 3…"""
    n, k = 0, 0
    out = []
    for e in sorted(eventos, key=lambda z: z["t"]):
        e = dict(e)
        if e["tipo"] == "tarjeta" and e.get("kind") == "punto":
            n += 1
            e["n"] = n
        if e["tipo"] == "escena":
            e["tono"] = "naranja" if k % 2 == 0 else "crema"
            k += 1
        out.append(e)
    return out


def t_like(eventos: list[dict], dur: float) -> float | None:
    """Un «dale like» a mitad del Short, en un hueco donde no pise una escena ni el CTA final. None si no cabe."""
    ocupados = [(e["t"] - 0.3, e["t"] + e["dur"] + 0.3) for e in eventos if e["tipo"] == "escena"] + [(dur - st_plan.T_CTA_FINAL - 0.4, dur)]
    for frac in (0.42, 0.34, 0.5, 0.58, 0.26):
        t = round(dur * frac, 2)
        if t < st_plan.T_GANCHO + 0.5:
            continue
        if not any(a <= t <= b or a <= t + 2.2 <= b for a, b in ocupados):
            return t
    return None


def procesa(sid: str, cfg=None, dir_raiz=None, progreso=None, _llama=None, _transcribe=None, _movimiento=None, forzar: bool = False, sin_ia: bool = False) -> dict:
    """Hace el Short entero (`sin_ia`: vuelve a MONTAR con el plan que la IA ya hizo —sus textos y eventos—, sin gastar ninguna petición: para ver un cambio de diseño). NUNCA lanza: deja `estado.json` en «listo» o «error» (con qué hacer). `progreso(frac, texto)` avisa del avance (la barra del panel)."""
    cfg = cfg or config.load()
    est = lee(sid, dir_raiz) or {}
    d = carpeta(sid, dir_raiz)

    def avanza(frac: float, texto: str) -> None:
        est.update(estado="creando", paso=texto, pct=round(frac * 100, 1))
        if progreso:
            progreso(frac, texto)
        if int(frac * 100) % 3 == 0 or frac >= 0.99:
            try:
                guarda(sid, est, dir_raiz)
            except OSError:
                pass

    try:
        o = est.get("origen") or {}
        src = Path(o.get("ruta") or "")
        if not src.is_file():
            raise RuntimeError("No encuentro el vídeo de origen (" + (src.name or "sin nombre") + "): sube el archivo otra vez.")
        ctx = contexto_canal(cfg, est.get("canal") or "")
        avisos: list[str] = []
        avanza(0.02, "Mirando el vídeo…")
        from .ingest import probe
        meta_v = probe(src)
        desde = float(o.get("desde") or 0.0)
        hasta = float(o.get("hasta") or meta_v["duration"])
        hasta = min(hasta, float(meta_v["duration"]))
        dur_origen = max(0.0, hasta - desde)
        if dur_origen < 3.0:
            raise RuntimeError("El trozo dura menos de 3 segundos: no hay un Short ahí.")
        if dur_origen > MAX_ORIGEN_S:
            raise RuntimeError(f"El vídeo dura {dur_origen / 60:.0f} min: un Short sale de un trozo de {MAX_ORIGEN_S // 60} min como mucho. Recórtalo antes de subirlo.")
        wav = d / "audio16.wav"
        if forzar or not wav.is_file():
            avanza(0.04, "Sacando el audio…")
            _extrae_audio(src, wav, desde, dur_origen if (desde > 0 or hasta < meta_v["duration"] - 0.5) else 0.0)
        avanza(0.08, "Transcribiendo lo que dices (Whisper)…")
        if forzar:
            (d / "words.json").unlink(missing_ok=True)
        if _transcribe is not None:
            words = _transcribe(wav, cfg, d)
        else:
            from .transcribe import transcribe
            words = transcribe(wav, cfg, d)
        words = [w for w in words if str(w.get("word", "")).strip()]
        if len(words) < 4:
            raise RuntimeError("Casi no se oye voz en el vídeo (Whisper no sacó palabras): comprueba que tiene audio.")
        avanza(0.30, "Quitando todos los silencios…")
        corte = st_voz.corta(wav, st_render.FPS)
        segs = corte["segmentos"]
        words_out = st_voz.palabras_a_salida(words, corte["mapa"])
        dur = float(corte["dur_short"])
        if dur < 6.0:
            raise RuntimeError(f"Sin los silencios solo quedan {dur:.0f} s: un Short necesita al menos unos 10 s de habla.")
        if dur > AVISO_LARGO_S:
            avisos.append(f"Dura {dur:.0f} s: YouTube Shorts admite hasta 3 minutos; TikTok e Instagram, más. Si quieres uno más corto, recorta el vídeo de origen.")
        avanza(0.34, "La IA lee lo que dices y orquesta la edición…")
        pista = f"Este trozo ya se había elegido como Short por esta idea: {o['pista']}" if o.get("pista") else ""
        prompt = st_plan.arma_prompt(words_out, dur, ctx["nombre"] or "tu canal", ctx["contexto"], st_ilus.catalogo_para_prompt(), pista)
        modelo = ""
        crudo: dict = {}
        previo = None
        if sin_ia:
            try:
                previo = json.loads((d / "plan.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previo = None
        try:
            if previo is not None:
                crudo = {"eventos": [e for e in previo.get("eventos", []) if e.get("origen") == "ia"]}
                modelo = str(est.get("modelo") or "")
                raise _SinLlamar()
            from . import uso as _uso
            _uso.contexto(job=sid)
            try:
                def valida(texto):
                    x = _json_de(texto)
                    if not isinstance(x, dict):
                        raise ValueError("la respuesta no es un plan")
                    return x
                crudo, modelo = _director(cfg, prompt, ctx["nombre"], valida, _llama)
            finally:
                _uso.contexto(job=None)
        except _SinLlamar:
            pass
        except Exception as exc:  # noqa: BLE001 — sin IA el Short sale igual (textos por código, ritmo por código)
            log(f"Short {sid}: la IA no respondió ({str(exc)[:200]}); sigo con lo que se puede hacer por código.")
            avisos.append("Gemini no respondió: los textos de publicación y las tarjetas salen por código (más sencillos). Pulsa «↻ Rehacer» cuando esté libre para que la IA oriente la edición.")
        meta = previo["meta"] if previo is not None and previo.get("meta") else st_plan.completa_meta(st_plan.parsea_meta(crudo or {}), words_out, ctx["nombre"])
        ilus_ok = set(st_ilus.CATALOGO)
        ev_ia, descartes = st_plan.valida_eventos(crudo.get("eventos") if isinstance(crudo, dict) else [], words_out, dur, ilus_ok)
        ev_ia = st_plan.mas_escenas(ev_ia, words_out, dur)
        ev_ok, motivos = st_plan.convivencia(ev_ia, dur)
        cortes = [float(v) for v in np.cumsum([b - a for a, b in segs])[:-1]]
        antes = st_plan.hueco_mayor(ev_ok, cortes, dur)
        eventos = st_plan.rellena(_tonos_y_numeros(ev_ok), cortes, dur, words_out)
        despues = st_plan.hueco_mayor(eventos, cortes, dur)
        avanza(0.45, "Midiendo dónde pasa algo en la pantalla…")
        geo = st_render.geometria(int(meta_v["width"]), int(meta_v["height"]), ctx["camara"])
        mov = (_movimiento or st_render.movimiento)(src, geo, dur_origen, desde=desde) if _movimiento is None else _movimiento(src, geo, dur_origen)
        marca = ctx["marca"]
        est_t = T.Est(marca, ctx["nombre"], ctx["web"])
        out = d / "short.mp4"
        mini = d / "miniatura.jpg"
        avanza(0.50, "Montando el Short…")
        r = st_render.renderiza(src, out, segs, words_out, eventos, meta, est_t, geo, mov, progreso=lambda f, s: avanza(0.50 + 0.48 * f, s), miniatura=mini, t_like=t_like(eventos, dur), desfase=desde, dur_origen=dur_origen)
        cuenta: dict = {}
        for e in eventos:
            cuenta[e["tipo"]] = cuenta.get(e["tipo"], 0) + 1
        if geo["modo"] == "solo":
            avisos.append("No hay una cámara medida para este canal: arriba sale la marca del canal en vez de tu cara.")
        est.update(estado="listo", paso="Listo", pct=100.0, error="", avisos=avisos, dur=r["dur"], dur_origen=round(dur_origen, 1), quitado_s=corte["quitado_s"], n_cortes=corte["n_cortes"], meta=meta, modelo=modelo,
                   eventos={"n": len(eventos), "por_tipo": cuenta, "hueco_max_antes": round(antes, 2), "hueco_max": round(despues, 2), "descartes": (descartes + motivos)[:30]}, modo=geo["modo"],
                   render_s=r["tiempo_s"], archivo="short.mp4", miniatura="miniatura.jpg" if mini.is_file() else "", terminado=time.time(), version=1)
        (d / "plan.json").write_text(json.dumps({"meta": meta, "eventos": eventos, "crudo_ia": (crudo.get("eventos") if isinstance(crudo, dict) else None), "segmentos": segs, "words": words_out}, ensure_ascii=False), encoding="utf-8")
        guarda(sid, est, dir_raiz)
        log(f"Short {sid}: {r['dur']:.0f} s, {len(eventos)} eventos (hueco máx. {despues:.1f} s), montado en {r['tiempo_s']:.0f} s.")
    except Exception as exc:  # noqa: BLE001
        log(f"Short {sid}: falló ({exc}).")
        est.update(estado="error", paso="Falló", error=_error_claro(exc), pct=est.get("pct", 0.0))
        try:
            guarda(sid, est, dir_raiz)
        except OSError:
            pass
    return est


def _error_claro(exc: Exception) -> str:
    s = str(exc)
    if "No hay clave" in s or "Casi no se oye" in s or "No encuentro" in s or "Sin los silencios" in s or "dura menos" in s or "un Short sale" in s or "No pude sacar" in s:
        return s
    return f"No se pudo hacer el Short ({s[:200]}). Prueba otra vez; si se repite, mira work/panel.log."


def textos(est: dict) -> dict:
    """Los textos listos para copiar, por red."""
    m = est.get("meta") or {}
    yt_desc = (m.get("descripcion_yt") or "").strip()
    return {"youtube": {"titulo": m.get("titulo_yt", ""), "descripcion": (yt_desc + "\n\n#Shorts").strip(), "tags": ", ".join(m.get("tags_yt") or [])},
            "tiktok": {"texto": (m.get("titulo_tt", "") + " " + " ".join(m.get("hashtags_tt") or [])).strip()},
            "instagram": {"texto": (m.get("titulo_ig", "") + "\n\n" + " ".join(m.get("hashtags_ig") or [])).strip()}}


# ------------------------------------------------------------------ subir a YouTube (solo con tu clic, privado por defecto)
COSTE_UNIDADES = 1600
_SUBIENDO: dict = {}


def sube_yt(sid: str, edits: dict | None = None, privacidad: str = "private", dir_raiz=None, _upload=None) -> dict:
    """Sube el Short a YouTube con los textos (los de la IA o los que el autor dejó en los cuadros). Cuesta ~1.600 unidades de cuota; nunca se sube dos veces el mismo; PRIVADO por defecto.
    Bloquea hasta acabar (el panel lo llama desde un hilo). -> {"ok", "url"?, "error"?}. Nunca lanza."""
    est = lee(sid, dir_raiz)
    if not est or est.get("estado") != "listo":
        return {"ok": False, "error": "Ese Short todavía no está listo."}
    if privacidad not in ("private", "unlisted", "public"):
        privacidad = "private"
    if sid in _SUBIENDO:
        return {"ok": False, "error": "Ese Short ya se está subiendo."}
    previo = est.get("yt") or {}
    if previo.get("estado") == "subido" and previo.get("video_id"):
        return {"ok": True, "ya": True, "url": previo.get("url"), "video_id": previo["video_id"]}
    t = textos(est)["youtube"]
    e = edits or {}
    titulo = re.sub(r"\s+", " ", str(e.get("titulo") or t["titulo"])).strip()[:100]
    desc = str(e.get("descripcion") if "descripcion" in e else t["descripcion"]).strip()[:4500]
    tags = [x.strip() for x in str(e.get("tags") if "tags" in e else t["tags"]).split(",") if x.strip()][:15]
    _SUBIENDO[sid] = 0
    try:
        if len(titulo) < 3:
            raise ValueError("Escribe un título para el Short (mínimo 3 letras).")
        f = carpeta(sid, dir_raiz) / "short.mp4"
        if not f.is_file():
            raise RuntimeError("No encuentro el vídeo del Short.")
        if _upload is None:
            from . import youtube
            if not youtube.available() or not youtube.is_connected():
                raise RuntimeError("YouTube no está conectado: usa «Conectar YouTube» arriba a la derecha.")
            _upload = youtube.upload

        def _pct(p):
            _SUBIENDO[sid] = int(p)
        r = _upload(str(f), titulo, desc, tags, None, False, _pct, privacidad=privacidad)
        est = lee(sid, dir_raiz) or est
        est["yt"] = {"estado": "subido", "video_id": r["video_id"], "url": r["url"], "studio": r.get("studio", ""), "titulo": titulo, "privacidad": privacidad}
        guarda(sid, est, dir_raiz)
        return {"ok": True, "url": r["url"], "video_id": r["video_id"]}
    except Exception as exc:  # noqa: BLE001
        txt = str(exc)
        if "quota" in txt.lower() or "cuota" in txt.lower():
            txt = "Se acabó la cuota de YouTube de hoy (cada subida gasta ~1.600 de 10.000): súbelo mañana."
        est = lee(sid, dir_raiz) or est
        est["yt"] = {"estado": "error", "error": txt[:300]}
        try:
            guarda(sid, est, dir_raiz)
        except OSError:
            pass
        return {"ok": False, "error": txt[:300]}
    finally:
        _SUBIENDO.pop(sid, None)


def pct_subida(sid: str) -> int | None:
    return _SUBIENDO.get(sid)


# ------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)[:500]})

    cfg = config.load()
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        src = t / "origen.mp4"
        st_render._fuente_sintetica(src)
        raiz_t = t / "shorts_tt"
        pals = "Google elige solo y a veces se equivoca con tu pagina principal cuando hay dos paginas parecidas".split()

        def transcribe_falso(wav, cfg_, d_):
            words = []
            for a0, b0 in [(0.5, 3.0), (3.9, 6.0), (8.0, 10.5)]:
                n = max(2, int((b0 - a0) / 0.35))
                for k in range(n):
                    words.append({"word": pals[(len(words)) % len(pals)], "start": round(a0 + k * (b0 - a0) / n, 2), "end": round(a0 + (k + 0.8) * (b0 - a0) / n, 2)})
            return words
        pedidos: list = []

        def llama(prompt, valida):
            pedidos.append(prompt)
            ws = transcribe_falso(None, None, None)
            cita = " ".join(w["word"] for w in ws[2:6])
            resp = {"titulo_yt": "Google elige tu página y se equivoca", "descripcion_yt": "Cómo evitarlo.\nSuscríbete.", "tags_yt": ["seo", "canonical"], "titulo_tt": "Google se equivoca con tu página", "hashtags_tt": ["#seo", "#google"],
                    "titulo_ig": "Que Google no elija por ti\nGuárdalo", "hashtags_ig": ["#seo"], "gancho": "GOOGLE ELIGE MAL", "cta_final": "SÍGUEME PARA MÁS SEO",
                    "eventos": [{"tipo": "palabra", "t": 3, "cita": cita, "texto": "ELIGE SOLO"}, {"tipo": "ilus", "t": 5, "ilus": "lupa", "texto": "BUSCAR", "cita": " ".join(w["word"] for w in ws[8:12])}]}
            return valida(json.dumps(resp)), "gemini-falso"
        # mismo canal que la config real; sin cámara medida en el sintético → se le pasa la suya
        canal = next(iter((cfg.get_path("videos.canales") or {"ejemplo": {}}).keys()))

        class CfgCam:
            def get_path(self, k, d=None):
                if k == f"videos.canales.{canal}":
                    return {**(cfg.get_path(k) or {}), "camara": {"x": 0.70, "y": 0.62, "w": 0.28, "h": 0.34}}
                return cfg.get_path(k, d)
        sid = nuevo(canal, {"tipo": "subida", "archivo": "origen.mp4", "ruta": str(src)}, raiz_t)
        e = procesa(sid, CfgCam(), raiz_t, _llama=llama, _transcribe=transcribe_falso, _movimiento=lambda s_, g_, d_: [(i * 0.25, 0.3 + 0.01 * i, 0.5) for i in range(60)])
        d = carpeta(sid, raiz_t)
        caso("de punta a punta: queda «listo», con el vídeo vertical, su miniatura y su plan", e["estado"] == "listo" and (d / "short.mp4").is_file() and (d / "miniatura.jpg").is_file() and (d / "plan.json").is_file(), e.get("error") or e.get("paso"))
        caso("UNA sola petición a Gemini por Short, y el vídeo dura menos que el origen (los silencios fuera)", len(pedidos) == 1 and e["dur"] < 8.0 and e["quitado_s"] > 3.0, (len(pedidos), e.get("dur"), e.get("quitado_s")))
        caso("la regla de ritmo se cumple: ningún hueco largo (RITMO_MAX_S) sin que pase algo", e["eventos"]["hueco_max"] <= st_plan.RITMO_MAX_S + 1e-6 and e["eventos"]["n"] >= 3, e["eventos"])
        caso("los textos para YouTube, TikTok e Instagram quedan listos (título, tags, hashtags, sin URLs)", textos(e)["youtube"]["titulo"] and "#Shorts" in textos(e)["youtube"]["descripcion"] and "#seo" in textos(e)["tiktok"]["texto"] and "http" not in json.dumps(textos(e)))
        from .ingest import probe
        info = probe(d / "short.mp4")
        caso("el archivo es un Short de verdad: 1080×1920, 30 fps y con audio", (info["width"], info["height"]) == (1080, 1920) and info["has_audio"] and abs(info["fps"] - 30) < 0.5)
        caso("lista() lo ve y borra() solo borra su carpeta (el vídeo de origen se queda)", any(x["id"] == sid for x in lista(raiz_t)) and borra(sid, raiz_t) and not d.exists() and src.is_file() and not borra("../x", raiz_t))
        # la IA no responde → sale igual, con aviso
        sid2 = nuevo(canal, {"tipo": "subida", "archivo": "origen.mp4", "ruta": str(src)}, raiz_t)

        def cae(prompt, valida):
            raise RuntimeError("Gemini saturado")
        e2 = procesa(sid2, CfgCam(), raiz_t, _llama=cae, _transcribe=transcribe_falso, _movimiento=lambda s_, g_, d_: [])
        caso("si Gemini no responde el Short sale igual (textos y ritmo por código) y lo avisa", e2["estado"] == "listo" and e2["meta"]["titulo_yt"] and e2["avisos"] and e2["eventos"]["hueco_max"] <= st_plan.RITMO_MAX_S + 1e-6, e2.get("error") or e2.get("avisos"))
        # errores claros
        sid3 = nuevo(canal, {"tipo": "subida", "archivo": "x.mp4", "ruta": str(t / "no_existe.mp4")}, raiz_t)
        e3 = procesa(sid3, CfgCam(), raiz_t)
        caso("un vídeo que no está da un error que dice qué hacer", e3["estado"] == "error" and "sube el archivo" in e3["error"], e3.get("error"))
        sid4 = nuevo(canal, {"tipo": "subida", "archivo": "origen.mp4", "ruta": str(src)}, raiz_t)
        e4 = procesa(sid4, CfgCam(), raiz_t, _llama=llama, _transcribe=lambda *a: [])
        caso("sin voz (Whisper no saca palabras) también lo explica", e4["estado"] == "error" and "voz" in e4["error"], e4.get("error"))
    with tempfile.TemporaryDirectory() as tmp2:
        t2 = Path(tmp2)
        raiz2 = t2 / "st"
        sidu = nuevo("ejemplo", {"tipo": "subida", "archivo": "x.mp4", "ruta": "x"}, raiz2)
        (carpeta(sidu, raiz2) / "short.mp4").write_bytes(b"0")
        eu = lee(sidu, raiz2)
        eu.update(estado="listo", meta={"titulo_yt": "Mi título", "descripcion_yt": "Desc", "tags_yt": ["a", "b"]})
        guarda(sidu, eu, raiz2)
        subidas: list = []

        def sube_falso(f_, titulo, desc, tags, pa, kids, cb, privacidad="public"):
            subidas.append((titulo, desc, tags, privacidad))
            return {"video_id": "abc123", "url": "https://youtu.be/abc123"}
        r1 = sube_yt(sidu, {"titulo": "Otro título"}, "private", raiz2, _upload=sube_falso)
        r2 = sube_yt(sidu, None, "private", raiz2, _upload=sube_falso)
        caso("subir a YouTube: con el título que dejaste, PRIVADO, con #Shorts y los tags; la 2.ª vez no duplica", r1["ok"] and len(subidas) == 1 and subidas[0][0] == "Otro título" and subidas[0][3] == "private" and "#Shorts" in subidas[0][1]
             and subidas[0][2] == ["a", "b"] and r2.get("ya") and (lee(sidu, raiz2)["yt"]["video_id"] == "abc123"), (r1, r2))
        sidv = nuevo("ejemplo", {"tipo": "subida", "archivo": "x.mp4", "ruta": "x"}, raiz2)
        rv = sube_yt(sidv, None, "private", raiz2, _upload=sube_falso)
        caso("un Short que aún no está listo no se sube", not rv["ok"] and len(subidas) == 1)

        def sin_cuota(*a, **k):
            raise RuntimeError("quotaExceeded")
        eu["yt"] = {}
        guarda(sidu, eu, raiz2)
        rq = sube_yt(sidu, None, "private", raiz2, _upload=sin_cuota)
        caso("si se acaba la cuota de YouTube lo dice en claro («súbelo mañana») y deja el Short listo para reintentar", not rq["ok"] and "mañana" in rq["error"] and lee(sidu, raiz2)["yt"]["estado"] == "error")
    ent = {"segments": [(10.0, 20.0), (25.0, 40.0)]}
    r0, r1 = rango_en_original(ent, {"desde_min": 8.0, "duracion": 6.0})
    caso("«enviar» desde un vídeo montado: el trozo se traduce a la grabación ORIGINAL saltando los cortes (8-14 s del montaje = 18 s a 29 s del original: cruza el corte 20→25)", abs(r0 - 18.0) < 1e-6 and abs(r1 - 29.0) < 1e-6, (r0, r1))
    ev = [{"tipo": "escena", "t": 6.0, "dur": 3.0}]
    tl = t_like(ev, 20.0)
    caso("el «dale like» cae a mitad pero nunca encima de una escena ni del CTA final", tl is not None and not (5.7 <= tl <= 9.3) and tl + 2.2 < 20 - st_plan.T_CTA_FINAL, tl)
    ev2 = _tonos_y_numeros([{"tipo": "escena", "t": 9, "dur": 2}, {"tipo": "tarjeta", "kind": "punto", "t": 3, "dur": 2}, {"tipo": "escena", "t": 5, "dur": 2}, {"tipo": "tarjeta", "kind": "punto", "t": 7, "dur": 2}])
    caso("las escenas alternan fondo (la 1.ª naranja) y los puntos se numeran en orden", [e["tono"] for e in ev2 if e["tipo"] == "escena"] == ["naranja", "crema"] and [e["n"] for e in ev2 if e["tipo"] == "tarjeta"] == [1, 2])
    return filas
