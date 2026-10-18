"""🔗 Carrusel desde un ENLACE de YouTube (el autor, 10-oct-2026: «que yo le pase un enlace de un vídeo de YouTube y haga toda la transcripción y el carrusel, tal cual como sale todo»).

Reusa TODO el carrusel de los vídeos (`carrusel.crea`: la IA propone los pasos, capturas del vídeo, la coherencia imagen ↔ paso, diapositivas Instagram/TikTok, PDF, «✏ Editar»…). Lo único nuevo es
conseguir lo que ya tiene un vídeo PRO terminado: el archivo, la transcripción y un manifest mínimo. Para que las rutas, la pantalla y el editor del carrusel funcionen sin tocar nada, el vídeo se
guarda como `videos/enlace-<id>--pro/` (pro.mp4 + manifest.json) y `work/enlace-<id>--pro/words.json`: el 🗑 de esta página lo borra TODO de ahí (nunca toca YouTube).
- Descarga con yt-dlp (≤ 1080p; el mismo `ingest.fetch` que el Generador) y transcribe con Whisper en tu PC (gratis; ~8 s por minuto de vídeo, con tu GPU).
- No hay «Ritmo del vídeo» (no es un vídeo editado por el programa): no se sabe cuándo se ve la pantalla, así que se confía en la revisión de coherencia (Flash-Lite mira cada captura) para no dejar
  una cara donde iría una pantalla. En un vídeo de pura cámara saldrán pocos pasos con captura: es lo esperado.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .util import log

PREFIJO = "enlace-"
_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


def video_id_de(url: str) -> str | None:
    """El id de 11 caracteres de un enlace de YouTube (watch?v=, youtu.be/, shorts/, live/, embed/), o None si no es de YouTube."""
    from urllib.parse import parse_qs, urlparse
    try:
        u = urlparse(str(url or "").strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in _HOSTS:
        return None
    host = (u.hostname or "").lower()
    cand = None
    if host.endswith("youtu.be"):
        cand = u.path.strip("/").split("/")[0]
    else:
        v = parse_qs(u.query).get("v")
        if v:
            cand = v[0]
        else:
            m = re.match(r"/(?:shorts|live|embed|v)/([^/?#]+)", u.path)
            cand = m.group(1) if m else None
    return cand if cand and _ID.fullmatch(cand) else None


def jid_de(vid: str) -> str:
    return f"{PREFIJO}{vid}--pro"


def es_enlace(jid: str) -> bool:
    return str(jid or "").startswith(PREFIJO) and str(jid).endswith("--pro")


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def info_de(url: str) -> dict:
    """Título, etiquetas, capítulos, canal y duración (yt-dlp, sin descargar el vídeo)."""
    from .ingest import ytdlp_cmd
    from .util import run
    out = run([*ytdlp_cmd(), "--js-runtimes", "deno", "--js-runtimes", "node", "--no-playlist", "--skip-download", "--dump-single-json", url], capture=True)
    d = json.loads(out)
    caps = [f"{_hms(c.get('start_time') or 0)} {str(c.get('title') or '').strip()}" for c in (d.get("chapters") or []) if c.get("title")]
    return {"titulo": str(d.get("title") or "").strip(), "tags": [str(t) for t in (d.get("tags") or [])][:10], "capitulos": "\n".join(caps),
            "canal_origen": str(d.get("channel") or d.get("uploader") or ""), "duracion": float(d.get("duration") or 0)}


def prepara(url: str, cfg, dir_outbox=None, dir_work=None, progreso=None, _fetch=None, _transcribe=None, _info=None) -> dict:
    """Deja listo `videos/enlace-<id>--pro/` (pro.mp4 + manifest.json) y `work/enlace-<id>--pro/words.json`. -> {"ok", "jid"} o {"ok": False, "error"}. Nunca lanza.
    `_fetch/_transcribe/_info`: pruebas (sin red ni Whisper)."""
    from . import config, ingest, transcribe
    vid = video_id_de(url)
    if not vid:
        return {"ok": False, "error": "Eso no parece un enlace de un vídeo de YouTube (youtube.com/watch?v=… o youtu.be/…)."}
    jid = jid_de(vid)
    P = config.paths()
    out_dir = Path(dir_outbox or P["outbox"]) / jid
    work = Path(dir_work or P["work"]) / f"{PREFIJO}{vid}--pro"
    say = progreso or (lambda t: None)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        pro = out_dir / "pro.mp4"
        if not (out_dir / "manifest.json").is_file() or not pro.is_file():
            say("Leyendo los datos del vídeo en YouTube…")
            try:
                info = (_info or info_de)(url)
            except Exception as exc:                            # noqa: BLE001
                return {"ok": False, "error": f"No pude leer ese vídeo de YouTube (¿es privado o no existe?): {str(exc)[:140]}"}
            if info.get("duracion", 0) > 3 * 3600:
                return {"ok": False, "error": "Ese vídeo dura más de 3 horas: es demasiado largo para un carrusel."}
            say("Descargando el vídeo…")
            src = (_fetch or ingest.fetch)(url, work)
            if Path(src) != pro:
                shutil.move(str(src), str(pro))
            dur = float(info.get("duracion") or 0) or float((ingest.probe(pro) or {}).get("duration") or 0)
            say("Transcribiendo lo que se dice (en tu PC, sin gastar nada)…")
            if _transcribe is not None:
                words = _transcribe(pro, work)
            else:
                wav = ingest.extract_audio(pro, work)
                words = transcribe.transcribe(wav, cfg, work)
            (work / "words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
            from .config import canal_defecto
            canal = canal_defecto(cfg) or "canal"
            manifest = [{"archivo": "pro.mp4", "titulo": info.get("titulo") or vid, "duracion": round(dur, 2), "segments": [[0.0, round(dur, 2)]],
                         "eventos_visuales": [], "tema": canal, "tags": info.get("tags") or [], "capitulos": info.get("capitulos") or "",
                         "enlace_origen": url.strip(), "canal_origen": info.get("canal_origen") or "", "pro": True}]
            tmp = out_dir / "manifest.json.tmp"
            tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            from .util import reemplaza
            reemplaza(tmp, out_dir / "manifest.json")
        return {"ok": True, "jid": jid}
    except Exception as exc:                                    # noqa: BLE001
        log(f"Carrusel por enlace {vid}: {exc}")
        return {"ok": False, "error": f"No se pudo preparar el vídeo ({str(exc)[:160]})."}


def lista(dir_outbox=None) -> list[dict]:
    """Los carruseles hechos desde enlaces (los más recientes primero): jid, título, enlace de origen, si ya hay carrusel."""
    from . import config
    base = Path(dir_outbox or config.paths()["outbox"])
    out = []
    for d in base.glob(f"{PREFIJO}*--pro"):
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            m = m[0] if isinstance(m, list) else m
        except (OSError, ValueError):
            continue
        c = d / "carrusel" / "carrusel.json"
        out.append({"jid": d.name, "titulo": m.get("titulo", ""), "enlace": m.get("enlace_origen", ""), "canal_origen": m.get("canal_origen", ""),
                    "hay_carrusel": c.is_file(), "ts": (c if c.is_file() else d / "manifest.json").stat().st_mtime})
    return sorted(out, key=lambda x: -x["ts"])


def borra(jid: str, dir_outbox=None, dir_work=None) -> bool:
    """Borra del PC el vídeo descargado, su transcripción y su carrusel (nunca nada de YouTube). Solo carpetas `enlace-…--pro`."""
    from . import config
    if not es_enlace(jid) or not re.fullmatch(r"[\w.\-]+", jid):
        return False
    P = config.paths()
    for base in (Path(dir_outbox or P["outbox"]) / jid, Path(dir_work or P["work"]) / jid):
        shutil.rmtree(base, ignore_errors=True)
    return True


# ------------------------------------------------------------------ pruebas (sin YouTube ni Whisper)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:400]})

    caso("reconoce los enlaces de YouTube (watch, youtu.be, shorts, con extras) y rechaza lo demás",
         video_id_de("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s") == "dQw4w9WgXcQ" and video_id_de("https://youtu.be/dQw4w9WgXcQ?si=abc") == "dQw4w9WgXcQ"
         and video_id_de("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ" and video_id_de("https://vimeo.com/123") is None
         and video_id_de("https://evil.com/watch?v=dQw4w9WgXcQ") is None and video_id_de("file:///c:/x.mp4") is None and video_id_de("https://www.youtube.com/watch?v=corto") is None)
    caso("el nombre de la carpeta es seguro y se reconoce", jid_de("dQw4w9WgXcQ") == "enlace-dQw4w9WgXcQ--pro" and es_enlace("enlace-dQw4w9WgXcQ--pro") and not es_enlace("2026-10-09-x--pro")
         and not es_enlace("../x"))
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        llamadas: list = []

        def fetch(url, work):
            llamadas.append("fetch")
            p = work / "source.mp4"
            p.write_bytes(b"x" * 10)
            return p

        def transcribe_(video, work):
            llamadas.append("transcribe")
            return [{"word": "hola", "start": 0.0, "end": 0.4}]

        def info(url):
            llamadas.append("info")
            return {"titulo": "Mi vídeo", "tags": ["seo"], "capitulos": "0:00 Intro", "canal_origen": "Canal X", "duracion": 120.0}

        class Cfg:
            def get_path(self, k, d=None):
                return "ejemplo" if k == "ideas.canal" else d

        r = prepara("https://youtu.be/dQw4w9WgXcQ", Cfg(), t / "videos", t / "work", None, fetch, transcribe_, info)
        m = json.loads((t / "videos" / r.get("jid", "x") / "manifest.json").read_text(encoding="utf-8"))[0] if r.get("ok") else {}
        caso("prepara el vídeo: pro.mp4, manifest mínimo y transcripción en su sitio (info → descarga → transcribe)",
             r.get("ok") and llamadas == ["info", "fetch", "transcribe"] and (t / "videos" / r["jid"] / "pro.mp4").is_file() and m.get("titulo") == "Mi vídeo"
             and m.get("segments") == [[0.0, 120.0]] and m.get("enlace_origen") and (t / "work" / r["jid"] / "words.json").is_file(), (r, llamadas, m))
        n = len(llamadas)
        r2 = prepara("https://youtu.be/dQw4w9WgXcQ", Cfg(), t / "videos", t / "work", None, fetch, transcribe_, info)
        caso("una segunda vez con el mismo enlace reusa lo ya hecho (no descarga ni transcribe otra vez)", r2.get("ok") and len(llamadas) == n, llamadas)
        caso("un enlace que no es de YouTube se rechaza sin tocar el disco", not prepara("https://vimeo.com/1", Cfg(), t / "videos", t / "work")["ok"])

        def info_roto(url):
            raise RuntimeError("Private video")

        r3 = prepara("https://youtu.be/aaaaaaaaaaa", Cfg(), t / "videos", t / "work", None, fetch, transcribe_, info_roto)
        caso("un vídeo privado o inexistente da un mensaje claro", not r3["ok"] and "privado" in r3["error"], r3)
        lis = lista(t / "videos")
        caso("la lista trae los carruseles por enlace y 🗑 borra solo lo suyo", len(lis) >= 1 and lis[0]["jid"] in ("enlace-dQw4w9WgXcQ--pro", "enlace-aaaaaaaaaaa--pro")
             and borra("enlace-dQw4w9WgXcQ--pro", t / "videos", t / "work") and not (t / "videos" / "enlace-dQw4w9WgXcQ--pro").exists()
             and not borra("2026-10-09-x--pro", t / "videos", t / "work"))
    return filas
