"""⬆ Subir un Short a YouTube desde el panel, con el título, la descripción y los tags que ya salieron (`shorts_meta`), sin salir del flujo.

Reglas (abogado del diablo, dicho al autor antes de construir):
- Cuesta ≈ 1.600 unidades de la cuota de la API (de 10.000 al día; un vídeo largo cuesta lo mismo): el panel pide confirmación y lo dice. Los Shorts NO se suben solos.
- Nunca se sube dos veces el mismo Short (`shorts_yt.json` apunta cuál ya está): un doble clic o un «Reintentar» no duplica.
- La descripción lleva el enlace al vídeo completo SOLO si ese vídeo ya está subido (lo pone el CÓDIGO, nunca la IA) y «#Shorts»; el título es el que el autor deje en el cuadro.
- Privacidad a elegir: público / no listado / privado (por defecto PRIVADO: se mira en Studio y se publica con un clic).
- Se sube en un hilo aparte, con el progreso a la vista; un fallo deja el motivo en claro y el Short listo para reintentar.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import shorts_meta as _meta
from . import util as _util
from .util import log

PRIVACIDADES = ("private", "unlisted", "public")
COSTE_UNIDADES = 1600
_LOCK = threading.Lock()
_ESTADO: dict = {}        # (jid, archivo) -> {"estado": "subiendo", "pct": 0-100}  (solo mientras sube)


def _ruta(out_dir) -> Path:
    return Path(out_dir) / "shorts_yt.json"


def lee(out_dir) -> dict:
    try:
        d = json.loads(_ruta(out_dir).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _guarda(out_dir, d: dict) -> None:
    p = _ruta(out_dir)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    _util.reemplaza(tmp, p)


def estado(jid: str, out_dir) -> dict:
    """{archivo: {estado: subido|subiendo|error, url, video_id, error, pct, privacidad}} de los Shorts de esta versión (lo subido + lo que sube ahora)."""
    d = lee(out_dir)
    with _LOCK:
        vivos = {a: dict(v) for (j, a), v in _ESTADO.items() if j == jid}
    for a, v in vivos.items():
        d[a] = {**d.get(a, {}), **v}
    return d


def prepara(archivo: str, out_dir, edits: dict | None, enlace: str = "") -> tuple[str, str, list[str]]:
    """(título, descripción, tags) listos para subir: lo guardado de la IA, con lo que el autor cambió en el cuadro por encima."""
    meta = (_meta.lee(out_dir).get(archivo) or {})
    yt = dict(meta.get("yt") or {})
    e = edits or {}
    if str(e.get("titulo") or "").strip():
        yt["titulo"] = _meta.limpia_titulo(e["titulo"], 100)
    if "descripcion" in e:
        yt["descripcion"] = _meta._sin_urls(str(e.get("descripcion") or "")).strip()[:3000]
    if e.get("tags") is not None:
        yt["tags"] = _meta.limpia_tags(e["tags"])
    meta = {**meta, "yt": yt}
    titulo = (yt.get("titulo") or "").strip()
    return titulo, _meta.arma_descripcion(meta, enlace), list(yt.get("tags") or [])


def sube(jid: str, archivo: str, out_dir, edits: dict | None = None, privacidad: str = "private", enlace: str = "", _upload=None) -> dict:
    """Sube UN Short (bloquea hasta acabar; el panel lo llama desde un hilo). -> {"ok", "url"?, "video_id"?, "error"?}. Nunca lanza."""
    out_dir = Path(out_dir)
    f = out_dir / archivo
    if archivo != Path(archivo).name or not archivo.startswith("short_") or not archivo.endswith(".mp4") or not f.is_file():
        return {"ok": False, "error": "Ese Short no existe en esta versión."}
    if privacidad not in PRIVACIDADES:
        privacidad = "private"
    with _LOCK:
        if (jid, archivo) in _ESTADO:
            return {"ok": False, "error": "Ese Short ya se está subiendo."}
        previo = lee(out_dir).get(archivo) or {}
        if previo.get("estado") == "subido" and previo.get("video_id"):
            return {"ok": True, "ya": True, "url": previo.get("url"), "video_id": previo["video_id"]}
        _ESTADO[(jid, archivo)] = {"estado": "subiendo", "pct": 0}
    try:
        titulo, desc, tags = prepara(archivo, out_dir, edits, enlace)
        if len(titulo) < 3:
            raise ValueError("Escribe un título para el Short (mínimo 3 letras).")
        if _upload is None:
            from . import youtube
            if not youtube.available() or not youtube.is_connected():
                raise RuntimeError("YouTube no está conectado: usa «Conectar YouTube» arriba a la derecha.")
            _upload = youtube.upload

        def _pct(p):
            with _LOCK:
                if (jid, archivo) in _ESTADO:
                    _ESTADO[(jid, archivo)]["pct"] = int(p)

        log(f"Short {archivo}: subiendo a YouTube como «{titulo}» ({privacidad})…")
        r = _upload(str(f), titulo, desc, tags, None, False, _pct, privacidad=privacidad)
        d = lee(out_dir)
        d[archivo] = {"estado": "subido", "video_id": r["video_id"], "url": r["url"], "studio": r.get("studio", ""), "titulo": titulo, "privacidad": privacidad}
        _guarda(out_dir, d)
        log(f"Short {archivo}: subido -> {r['url']}")
        return {"ok": True, "url": r["url"], "video_id": r["video_id"], "studio": r.get("studio", "")}
    except Exception as exc:  # noqa: BLE001
        txt = _explica(exc)
        d = lee(out_dir)
        d[archivo] = {**{k: v for k, v in (d.get(archivo) or {}).items() if k in ("titulo",)}, "estado": "error", "error": txt}
        try:
            _guarda(out_dir, d)
        except OSError:
            pass
        log(f"Short {archivo}: no se subió ({txt[:160]})")
        return {"ok": False, "error": txt}
    finally:
        with _LOCK:
            _ESTADO.pop((jid, archivo), None)


def _explica(exc: Exception) -> str:
    n = type(exc).__name__
    s = str(exc)
    if n == "CuotaAgotada" or "quota" in s.lower():
        return "Se acabó la cuota diaria de YouTube (se renueva a las 2:00 a. m. de Colombia). El Short queda listo: súbelo mañana."
    return s[:300] or n


def lanza(jid: str, archivo: str, out_dir, edits=None, privacidad="private", enlace="", _upload=None) -> bool:
    """Sube en un hilo aparte. False si ese Short ya se está subiendo."""
    with _LOCK:
        if (jid, archivo) in _ESTADO:
            return False
    threading.Thread(target=sube, args=(jid, archivo, out_dir, edits, privacidad, enlace, _upload), daemon=True).start()
    return True


# ------------------------------------------------------------------ pruebas (YouTube de mentira: no se sube nada)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:400]})

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "short_1.mp4").write_bytes(b"x")
        _meta.guarda(out, {"short_1.mp4": {"yt": {"titulo": "Este error te saca de Google", "descripcion": "Una etiqueta olvidada.\\nSe revisa en 10 s.", "tags": ["SEO", "noindex", "Google"]},
                                          "tiktok": {"titulo": "x", "hashtags": []}, "origen": "ia"}})
        llamadas: list = []

        def falso(ruta, titulo, desc, tags, publish_at, kids, cb, privacidad="public"):
            llamadas.append((titulo, desc, tags, publish_at, privacidad))
            cb(50)
            return {"video_id": "abc123", "url": "https://youtu.be/abc123", "studio": "https://studio.youtube.com/video/abc123/edit"}

        r = sube("j", "short_1.mp4", out, enlace="https://youtu.be/LARGO", _upload=falso)
        t, d, tg, pa, pv = llamadas[0]
        caso("sube con el título y los tags guardados, privado por defecto y sin fecha de publicación", r["ok"] and t == "Este error te saca de Google" and tg == ["SEO", "noindex", "Google"] and pa is None and pv == "private", llamadas)
        caso("la descripción lleva el enlace al vídeo completo (el que pone el código) y #Shorts", "Vídeo completo: https://youtu.be/LARGO" in d and "#Shorts" in d, d)
        caso("queda apuntado como subido, con su enlace", lee(out)["short_1.mp4"]["video_id"] == "abc123" and estado("j", out)["short_1.mp4"]["estado"] == "subido")
        r2 = sube("j", "short_1.mp4", out, _upload=falso)
        caso("un segundo intento NO lo sube otra vez (no duplica)", len(llamadas) == 1 and r2.get("ya") is True, len(llamadas))

        (out / "short_2.mp4").write_bytes(b"x")
        r3 = sube("j", "short_2.mp4", out, edits={"titulo": "Mi título editado", "tags": ["a", "#b", "Shorts"]}, privacidad="unlisted", _upload=falso)
        t, d, tg, pa, pv = llamadas[-1]
        caso("lo que el autor cambia en el cuadro manda sobre lo de la IA, y elige la privacidad", r3["ok"] and t == "Mi título editado" and tg == ["a", "b"] and pv == "unlisted", llamadas[-1])

        (out / "short_3.mp4").write_bytes(b"x")

        def roto(*a, **k):
            raise RuntimeError("503 backend error")

        r4 = sube("j", "short_3.mp4", out, edits={"titulo": "Algo"}, _upload=roto)
        caso("un fallo de YouTube deja el motivo, no rompe y se puede reintentar", not r4["ok"] and lee(out)["short_3.mp4"]["estado"] == "error" and "503" in lee(out)["short_3.mp4"]["error"])
        r5 = sube("j", "short_3.mp4", out, edits={"titulo": "Algo"}, _upload=falso)
        caso("tras el fallo, el reintento sí sube", r5["ok"] and lee(out)["short_3.mp4"]["estado"] == "subido")

        class CuotaAgotada(Exception):
            pass

        def sin_cuota(*a, **k):
            raise CuotaAgotada("quotaExceeded")

        (out / "short_4.mp4").write_bytes(b"x")
        r6 = sube("j", "short_4.mp4", out, edits={"titulo": "Otro"}, _upload=sin_cuota)
        caso("cuota agotada: lo explica en claro", "cuota" in r6["error"].lower() and "mañana" in r6["error"], r6)
        (out / "short_5.mp4").write_bytes(b"x")
        _meta.guarda(out, {**_meta.lee(out), "short_5.mp4": {"yt": {"titulo": "", "descripcion": "", "tags": []}}})
        n0 = len(llamadas)
        r7 = sube("j", "short_5.mp4", out, edits={"titulo": " "}, _upload=falso)
        caso("sin un título válido no sube nada y lo dice", not r7["ok"] and "título" in r7["error"] and len(llamadas) == n0, r7)
        caso("rutas raras (../) o inexistentes se rechazan sin tocar el disco", not sube("j", "../x.mp4", out, _upload=falso)["ok"] and not sube("j", "short_9.mp4", out, _upload=falso)["ok"])
    return filas
