"""Subida y programación de vídeos a YouTube (Data API v3).

OAuth de "app de escritorio": la primera vez abre el navegador para autorizar
y guarda el token en work/youtube_token.json. Este token es SEPARADO del de
otros programas (cada proyecto vive en su propia carpeta, con su propio
client_secret.json y su propio work/youtube_token.json) — conectar aquí no
toca ni depende de la conexión del generador de Shorts. Cada subida es
~1.600 unidades de cuota (tope diario 10.000).

Programar = subir con privacyStatus=private + publishAt=<fecha ISO>. YouTube
lo hace público solo a esa hora.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .config import ROOT
from .util import log

# Google devuelve los scopes en otro orden / añade "openid" -> sin esto,
# oauthlib lanza "Scope has changed" y revienta el fetch_token.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN_FILE = ROOT / "work" / "youtube_token.json"
ISSUED_FILE = ROOT / "work" / "youtube_token.issued"   # cuándo se autorizó
REAUTH_DAYS = 7   # el token en modo "Testing" caduca a los 7 días
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",   # update, delete, thumbnails, playlists, statistics
    # RETENCION: cuanto aguanta la gente cada Short. Las visitas dicen cuanto
    # te empujo YouTube; la retencion dice si el clip es bueno. Es de solo
    # lectura y gratis, pero es un permiso APARTE: al anadirlo hay que
    # reconectar una vez (Google pide el consentimiento otra vez).
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
CATEGORY_EDUCATION = "27"


def libs_ok() -> bool:
    """¿Están instaladas las librerías de Google (requirements.txt)?"""
    try:
        import google_auth_oauthlib  # noqa: F401
        import googleapiclient  # noqa: F401
        return True
    except ImportError:
        return False


def has_client_secret() -> bool:
    return CLIENT_SECRET.exists()


def available() -> bool:
    """¿Está el client_secret.json y las librerías?"""
    return has_client_secret() and libs_ok()


def client_secret_de_ms() -> Path | None:
    """El client_secret.json del proyecto hermano (otros programas), si existe: es un cliente OAuth
    de Google Cloud, no una sesión — sirve para el otro canal. Ojo: compartirlo comparte también
    la cuota diaria de la API (10.000 unidades por proyecto de Google Cloud)."""
    p = ROOT.parent / "otros programas" / "client_secret.json"
    return p if p.is_file() else None


def guarda_client_secret(texto: str) -> dict:
    """Valida el JSON que descargó de Google Cloud y lo guarda como client_secret.json.
    Tiene que ser un cliente de tipo «Aplicación de escritorio» ("installed"): con el tipo
    «Web» Google rechaza el redirect a localhost con un puerto libre."""
    try:
        data = json.loads(texto)
    except (ValueError, TypeError):
        return {"ok": False, "error": "Ese archivo no es un JSON válido. Descarga el JSON de "
                                      "credenciales de Google Cloud y súbelo tal cual."}
    if not isinstance(data, dict):
        return {"ok": False, "error": "El JSON no tiene el formato de credenciales de Google."}
    if "web" in data and "installed" not in data:
        return {"ok": False, "error": "Ese cliente es de tipo «Aplicación web». Crea otro con tipo "
                                      "«Aplicación de escritorio» (Desktop app) y descarga su JSON."}
    cli = data.get("installed")
    if not isinstance(cli, dict) or not all(cli.get(k) for k in ("client_id", "client_secret", "auth_uri", "token_uri")):
        return {"ok": False, "error": "Faltan datos en el JSON (client_id / client_secret). "
                                      "¿Descargaste el archivo de credenciales OAuth correcto?"}
    CLIENT_SECRET.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _drop_token()   # cliente nuevo: un token de otro cliente ya no vale
    return {"ok": True, "cliente": str(cli["client_id"])[:18] + "…"}


def copia_client_secret_de_ms() -> dict:
    p = client_secret_de_ms()
    if not p:
        return {"ok": False, "error": "No encuentro el client_secret.json de otros programas."}
    return guarda_client_secret(p.read_text(encoding="utf-8"))


def is_connected() -> bool:
    return TOKEN_FILE.exists()


def _mark_issued() -> None:
    try:
        ISSUED_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def days_left() -> int | None:
    """Días que quedan hasta que haya que reconectar (o None si no aplica)."""
    if not TOKEN_FILE.exists() or not ISSUED_FILE.exists():
        return None
    try:
        issued = float(ISSUED_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    left = REAUTH_DAYS - (time.time() - issued) / 86400
    return max(0, int(left))


# --------------------------------------------------------------- credenciales
def _drop_token() -> None:
    """Borra el token muerto para que la UI muestre 'Conectar' limpio en vez
    de un estado a medias."""
    TOKEN_FILE.unlink(missing_ok=True)
    ISSUED_FILE.unlink(missing_ok=True)


def _load_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not TOKEN_FILE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    except (ValueError, OSError) as exc:
        log(f"YouTube: token ilegible ({exc}). Hay que reconectar.")
        _drop_token()
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log(f"YouTube: el token no se pudo refrescar ({exc}). Hay que reconectar.")
            _drop_token()          # token muerto: fuera, así 'Conectar' sale limpio
            return None
    return creds if creds and creds.valid else None


_CONNECT: dict = {}   # estado del flujo OAuth en curso


def connect_start() -> str:
    """Prepara el flujo OAuth y devuelve la URL a la que el usuario debe ir.
    Un mini-servidor local espera el redirect con el código."""
    import socket
    from google_auth_oauthlib.flow import Flow

    if not CLIENT_SECRET.exists():
        raise RuntimeError("Falta client_secret.json en la carpeta del proyecto.")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _drop_token()   # empieza de cero: un token viejo con otros permisos estorba

    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET), SCOPES,
        redirect_uri=f"http://localhost:{port}/",
    )
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent",
                                        include_granted_scopes="true")
    _CONNECT.clear()
    _CONNECT.update(flow=flow, port=port, auth_url=auth_url, done=False, error=None)

    import threading
    threading.Thread(target=_await_redirect, args=(port,), daemon=True).start()
    return auth_url


def _await_redirect(port: int) -> None:
    import http.server
    from urllib.parse import urlparse, parse_qs

    holder: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            holder["code"] = (q.get("code") or [None])[0]
            holder["error"] = (q.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>Autorizado. Ya puedes cerrar esta pestaña y volver a la app.</h2>"
                .encode("utf-8"))

        def log_message(self, *a):                          # silencio
            pass

    srv = http.server.HTTPServer(("localhost", port), H)
    srv.timeout = 300
    try:
        srv.handle_request()
        if holder.get("error"):
            raise RuntimeError(holder["error"])
        if not holder.get("code"):
            raise RuntimeError("No llegó el código de autorización (¿se cerró la ventana?).")
        _CONNECT["flow"].fetch_token(code=holder["code"])
        TOKEN_FILE.write_text(_CONNECT["flow"].credentials.to_json(), encoding="utf-8")
        _mark_issued()
        _CONNECT["done"] = True
    except Exception as exc:                                # noqa: BLE001
        msg = str(exc)
        if "invalid_scope" in msg or "access_denied" in msg or "denied" in msg.lower():
            msg += ("  —  En Google Cloud → 'Pantalla de consentimiento de OAuth' "
                    "→ 'Acceso a los datos', añade los permisos de YouTube "
                    "(youtube.upload y youtube), y comprueba que tu correo está "
                    "en 'Usuarios de prueba'. Luego vuelve a darle a Conectar.")
        _CONNECT["error"] = msg
        _CONNECT["done"] = True
    finally:
        srv.server_close()


def connect_state() -> dict:
    return {
        "auth_url": _CONNECT.get("auth_url"),
        "in_progress": bool(_CONNECT) and not _CONNECT.get("done"),
        "done": _CONNECT.get("done", False),
        "error": _CONNECT.get("error"),
    }


def disconnect() -> None:
    _CANAL.update(ts=0.0, marca=None, val=None)
    TOKEN_FILE.unlink(missing_ok=True)
    ISSUED_FILE.unlink(missing_ok=True)


def _service():
    from googleapiclient.discovery import build

    creds = _load_creds()
    if creds is None:
        raise RuntimeError("YouTube no está conectado (o el token caducó).")
    from . import cuota_yt                              # cuenta lo que gasta el programa de los 10.000 de hoy (para el indicador de la cabecera)
    return cuota_yt.contador(build("youtube", "v3", credentials=creds, cache_discovery=False))


_CANAL: dict = {"ts": 0.0, "marca": None, "val": None}
_CANAL_TTL_S = 600.0


def channel_info(_ahora=None) -> dict:
    """El canal conectado (nombre e id). Cuesta 1 unidad de cuota por llamada y el chip de la cabecera de CADA pestaña abierta lo pregunta cada 30 s: sin guardarlo, una sola pestaña abierta todo el día
    gastaba ~2.900 de las 10.000 unidades solo para leer «MI CANAL» (lo vio la cuenta de `cuota_yt`, 12-oct-2026). Ahora se guarda `_CANAL_TTL_S` (10 min) y se invalida al reconectar o desconectar
    (la marca es la fecha del archivo del token). Solo se guarda un resultado bueno: un error se vuelve a preguntar."""
    ahora = _ahora if _ahora is not None else time.time()
    try:
        marca = TOKEN_FILE.stat().st_mtime
    except OSError:
        marca = None
    if _CANAL["val"] is not None and _CANAL["marca"] == marca and ahora - _CANAL["ts"] < _CANAL_TTL_S:
        return dict(_CANAL["val"])
    r = _channel_info()
    _CANAL.update(ts=ahora, marca=marca, val=dict(r) if r.get("connected") else None)
    return r


def _channel_info() -> dict:
    try:
        yt = _service()
        r = yt.channels().list(part="snippet,contentDetails", mine=True).execute()
        items = r.get("items", [])
        if not items:
            return {"connected": True, "channel": None, "channel_id": None}
        it = items[0]
        return {"connected": True, "channel": it["snippet"]["title"],
                "channel_id": it["id"]}
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "channel": None, "error": str(exc)}


def _es_transitorio(exc: Exception) -> bool:
    """Solo lo que de verdad merece la pena reintentar: un corte de red, un
    5xx de YouTube a mitad de subida. NO cuota agotada, NO un vídeo mal
    formado, NO un 4xx de verdad — reintentar eso no arregla nada y solo
    retrasa el aviso real."""
    import http.client

    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            return getattr(exc.resp, "status", 0) in (500, 502, 503, 504)
    except ImportError:
        pass
    return isinstance(exc, (OSError, http.client.HTTPException))


def _subir_con_reintento(req, progress_cb, reintento_s: float) -> dict:
    """El bucle de `next_chunk()` de una subida resumible, pero que no se
    rinde a la primera desconexión: cada chunk ya trae su propio reintento
    (`num_retries`), y si aun así uno falla, se reintenta el chunk siguiente
    mientras SIGA fallando por menos de `reintento_s` seguidos — la sesión
    resumible de Google recuerda por dónde iba, así que reintentar no
    empieza la subida desde cero. El reloj se reinicia en cada chunk que sí
    avanza: lo que importa es que no lleve MUCHO rato sin progresar, no el
    tiempo total de una subida larga."""
    resp = None
    t_ultimo_ok = time.monotonic()
    intento = 0
    while resp is None:
        try:
            prog, resp = req.next_chunk(num_retries=5)
        except Exception as exc:                            # noqa: BLE001
            if not _es_transitorio(exc):
                raise
            intento += 1
            espera = min(20.0, 3.0 * (2 ** intento))
            if time.monotonic() - t_ultimo_ok + espera >= reintento_s:
                raise
            log(f"YouTube: fallo transitorio subiendo (intento {intento}): "
                f"{type(exc).__name__}: {exc}. Reintento en {espera:.0f}s.")
            time.sleep(espera)
            continue
        t_ultimo_ok = time.monotonic()
        intento = 0
        if prog and progress_cb:
            progress_cb(int(prog.progress() * 100))
    return resp


# ------------------------------------------------------------------- subida
def upload(file_path: str, title: str, description: str = "",
           tags: list[str] | None = None, publish_at: str | None = None,
           made_for_kids: bool = False, progress_cb=None,
           reintento_s: float = 180.0, privacidad: str = "public") -> dict:
    """Sube un vídeo horizontal. Si publish_at (ISO 8601, p.ej.
    2026-09-05T18:00:00-05:00) va, queda PRIVADO y YouTube lo publica solo
    a esa hora. Un corte de red a mitad de subida NO deja el vídeo en
    "error" para que lo reintentes a mano: se resiste hasta `reintento_s`
    segundos seguidos sin avanzar antes de rendirse de verdad."""
    from googleapiclient.http import MediaFileUpload

    yt = _service()
    status = {"privacyStatus": "private" if publish_at else (privacidad if privacidad in ("public", "unlisted", "private") else "public"),
              "selfDeclaredMadeForKids": bool(made_for_kids)}
    if publish_at:
        status["publishAt"] = publish_at

    desc = (description or "").rstrip()[:4900]
    tg = [t for t in (tags or []) if t.lower() != "shorts"][:30]
    body = {
        "snippet": {
            "title": title[:100],
            "description": desc,
            "tags": tg,
            "categoryId": CATEGORY_EDUCATION,
        },
        "status": status,
    }
    media = MediaFileUpload(file_path, chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = _subir_con_reintento(req, progress_cb, reintento_s)
    vid = resp["id"]
    return {"video_id": vid, "url": f"https://youtu.be/{vid}",
            "studio": f"https://studio.youtube.com/video/{vid}/edit"}


def update_video(video_id: str, title: str | None = None,
                 description: str | None = None, tags: list[str] | None = None,
                 publish_at: str | None = None) -> dict:
    """Cambia título/descripción/tags/fecha de un vídeo ya subido."""
    yt = _service()
    cur = yt.videos().list(part="snippet,status", id=video_id).execute()
    items = cur.get("items", [])
    if not items:
        raise RuntimeError("Ese vídeo ya no está en YouTube.")
    snip = items[0]["snippet"]
    stat = items[0]["status"]
    if title is not None:
        snip["title"] = title[:100]
    if description is not None:
        snip["description"] = description[:4900]
    if tags is not None:
        snip["tags"] = tags[:30]
    if not snip.get("categoryId"):
        snip["categoryId"] = CATEGORY_EDUCATION
    if publish_at is not None:
        stat["privacyStatus"] = "private"
        stat["publishAt"] = publish_at
    yt.videos().update(part="snippet,status",
                       body={"id": video_id, "snippet": snip, "status": stat}).execute()
    return {"ok": True}


class CuotaAgotada(RuntimeError):
    """YouTube avisa de que se acabó la cuota diaria de la API (10.000 unidades por proyecto;
    cada cambio de descripción cuesta 50). Se renueva a medianoche (hora del Pacífico)."""


def _es_cuota(exc: Exception) -> bool:
    m = str(exc)
    return any(t in m for t in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                                "userRateLimitExceeded"))


def lista_videos_canal() -> list[dict]:
    """TODOS los vídeos subidos al canal (los más nuevos primero) con su descripción
    completa, estado y duración. Cuesta ~2 unidades de cuota por cada 50 vídeos."""
    yt = _service()
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    items = ch.get("items", [])
    pl = items[0]["contentDetails"]["relatedPlaylists"].get("uploads") if items else None
    if not pl:
        return []
    ids: list[str] = []
    tok = None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=50,
                                    pageToken=tok).execute()
        for it in r.get("items", []):
            vid = it.get("contentDetails", {}).get("videoId")
            if vid and vid not in ids:
                ids.append(vid)
        tok = r.get("nextPageToken")
        if not tok:
            break
    out: list[dict] = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,contentDetails,status", id=",".join(ids[i:i + 50])).execute()
        por_id = {it["id"]: it for it in r.get("items", [])}
        for vid in ids[i:i + 50]:
            it = por_id.get(vid)
            if not it:
                continue
            sn = it.get("snippet", {})
            out.append({
                "video_id": vid, "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "published_at": sn.get("publishedAt"),
                "privacy": it.get("status", {}).get("privacyStatus"),
                "duration_s": _iso_dur_s(it.get("contentDetails", {}).get("duration", "")),
            })
    return out


def get_snippet(video_id: str) -> dict:
    """El snippet ACTUAL de un vídeo (título, descripción, tags, categoría...)."""
    r = _service().videos().list(part="snippet", id=video_id).execute()
    items = r.get("items", [])
    if not items:
        raise RuntimeError("Ese vídeo ya no está en YouTube.")
    return items[0]["snippet"]


def set_description(video_id: str, snippet: dict, description: str) -> dict:
    """Cambia SOLO la descripción. `snippet` es el actual (de `get_snippet`): YouTube borra
    lo que no se le devuelve (tags, categoría...), así que se reenvía entero. Solo la parte
    `snippet` (no toca privacidad ni fecha de publicación). Lanza CuotaAgotada si se acabó
    la cuota del día."""
    from googleapiclient.errors import HttpError

    sn = dict(snippet)
    for solo_lectura in ("thumbnails", "channelId", "channelTitle", "publishedAt",
                         "liveBroadcastContent", "localized"):
        sn.pop(solo_lectura, None)
    if not sn.get("categoryId"):
        sn["categoryId"] = CATEGORY_EDUCATION
    yt = _service()
    texto = description[:4900]
    for intento in range(2):
        sn["description"] = texto
        try:
            yt.videos().update(part="snippet", body={"id": video_id, "snippet": sn}).execute()
            return {"ok": True}
        except HttpError as exc:
            if _es_cuota(exc):
                raise CuotaAgotada("Se acabó la cuota diaria de YouTube.") from exc
            if intento == 0 and "invalidDescription" in str(exc):
                texto = texto.replace("<", "‹").replace(">", "›")     # YouTube no admite < >
                continue
            raise


def delete_video(video_id: str) -> dict:
    _service().videos().delete(id=video_id).execute()
    return {"ok": True}


def set_thumbnail(video_id: str, image_path: str) -> dict:
    """Pone la miniatura y DEVUELVE la URL que YouTube le asigna, para poder
    comprobar con un clic que de verdad quedo puesta (y no adivinar)."""
    from googleapiclient.http import MediaFileUpload

    r = _service().thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(image_path, mimetype="image/jpeg"),
    ).execute(num_retries=3)
    url = ""
    try:
        th = (r.get("items") or [{}])[0] or {}
        for k in ("maxres", "standard", "high", "medium", "default"):
            u = (th.get(k) or {}).get("url")
            if u:
                url = u
                break
    except (IndexError, AttributeError, TypeError):
        pass
    return {"ok": True, "url": url}


# ------------------------------------------------------------------- listas de reproducción
def _razon(exc: Exception) -> str:
    """El «reason» que da YouTube en un HttpError («playlistNotFound», «videoAlreadyInPlaylist»…), o «» si no es de ese tipo."""
    try:
        d = json.loads(exc.content.decode("utf-8", "replace"))      # type: ignore[attr-defined]
        return str(((d.get("error") or {}).get("errors") or [{}])[0].get("reason") or "")
    except Exception:  # noqa: BLE001
        return ""


def listas_mias(maximo: int = 200) -> list[dict]:
    """TUS listas de reproducción: [{"id", "titulo", "n" (vídeos), "privacidad"}]. 1 unidad de cuota por cada 50 listas."""
    yt = _service()
    out: list[dict] = []
    token = None
    while len(out) < maximo:
        r = yt.playlists().list(part="snippet,contentDetails,status", mine=True, maxResults=50,
                                **({"pageToken": token} if token else {})).execute(num_retries=3)
        for it in r.get("items", []):
            out.append({"id": it["id"], "titulo": (it.get("snippet") or {}).get("title") or it["id"],
                        "n": int((it.get("contentDetails") or {}).get("itemCount") or 0),
                        "privacidad": (it.get("status") or {}).get("privacyStatus") or ""})
        token = r.get("nextPageToken")
        if not token:
            break
    return out[:maximo]


def anade_a_lista(video_id: str, lista_id: str, posicion: str = "final") -> dict:
    """Añade un vídeo a una de TUS listas (playlistItems.insert: 50 unidades de cuota, con el permiso «youtube» que el panel ya tiene). `posicion`: «final» (lo normal) o
    «inicio». NO duplica: YouTube deja añadir el mismo vídeo dos veces a una lista (comprobado de verdad el 5-oct-2026: no da error, mete otra copia), así que mira antes si ya
    está (1 unidad más). -> {"ok": True, "ya": False, "item": id del elemento en la lista (sirve para deshacerlo)}; si ya estaba, {"ok": True, "ya": True, "item": …}. Lanza
    CuotaAgotada si se acabó la cuota del día; cualquier otro error, tal cual (`listas_yt.explica` lo traduce)."""
    from googleapiclient.errors import HttpError
    snippet = {"playlistId": lista_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}
    if posicion == "inicio":
        snippet["position"] = 0
    try:
        items = _service().playlistItems()
        previo = items.list(part="id", playlistId=lista_id, videoId=video_id, maxResults=1).execute(num_retries=3)
        if previo.get("items"):
            return {"ok": True, "ya": True, "item": previo["items"][0].get("id")}
        r = items.insert(part="snippet", body={"snippet": snippet}).execute(num_retries=3)
    except HttpError as exc:
        if _es_cuota(exc):
            raise CuotaAgotada(str(exc)) from exc
        if getattr(exc.resp, "status", 0) == 409 and "already" in _razon(exc).lower():
            return {"ok": True, "ya": True}
        raise
    return {"ok": True, "ya": False, "item": (r or {}).get("id")}


def list_channel_uploads(limit: int = 300) -> list[dict]:
    """Lista las últimas subidas REALES del canal (id + título + fecha).
    Sirve para re-emparejar clips cuyo video_id guardado quedó obsoleto
    (re-subida por Reintentar) o vacío, o que renombraste en Studio."""
    yt = _service()
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    items = ch.get("items", [])
    if not items:
        return []
    pl = items[0]["contentDetails"]["relatedPlaylists"].get("uploads")
    if not pl:
        return []
    out: list[dict] = []
    vistos: set[str] = set()          # la playlist de uploads puede repetir un
    tok = None                        # vídeo entre páginas si se está moviendo
    while len(out) < limit:
        r = yt.playlistItems().list(part="contentDetails,snippet", playlistId=pl,
                                    maxResults=50, pageToken=tok).execute()
        for it in r.get("items", []):
            cd, sn = it.get("contentDetails", {}), it.get("snippet", {})
            vid = cd.get("videoId")
            if not vid or vid in vistos:
                continue
            vistos.add(vid)
            out.append({
                "video_id": vid,
                "title": sn.get("title", ""),
                # fecha de subida (para vídeos programados suele venir en snippet)
                "published_at": cd.get("videoPublishedAt") or sn.get("publishedAt"),
            })
        tok = r.get("nextPageToken")
        if not tok:
            break
    return out[:limit]


def video_stats(video_ids: list[str]) -> dict[str, dict]:
    """Vistas / likes / comentarios (de por vida) de varios vídeos.
    Usa videos.list(statistics) — no necesita la Analytics API aparte."""
    if not video_ids:
        return {}
    yt = _service()
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        r = yt.videos().list(part="statistics,snippet,contentDetails,status",
                             id=",".join(chunk)).execute()
        for it in r.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {
                "views": int(s.get("viewCount", 0) or 0),
                "likes": int(s.get("likeCount", 0) or 0),
                "comments": int(s.get("commentCount", 0) or 0),
                "published_at": it.get("snippet", {}).get("publishedAt"),
                "title": it.get("snippet", {}).get("title", ""),
                "duration_s": _iso_dur_s(it.get("contentDetails", {}).get("duration", "")),
                "privacy": it.get("status", {}).get("privacyStatus"),
            }
    return out


_TAMANOS_MINIATURA = ("maxres", "standard", "high", "medium", "default")   # de mejor a peor calidad


def mejor_miniatura(thumbnails: dict | None) -> str | None:
    """La URL de la miniatura más grande que YouTube tenga de un vídeo (`snippet.thumbnails`)."""
    for k in _TAMANOS_MINIATURA:
        url = ((thumbnails or {}).get(k) or {}).get("url")
        if url:
            return url
    return None


def info_videos(video_ids: list[str]) -> dict[str, dict]:
    """Lo que YouTube dice AHORA de cada vídeo, para el Historial (el autor, 2026-09-26: «sale como publicado pero está
    programado», «lo publiqué el 25 y sale el 24… que las fechas estén tomadas 100 % de YouTube», y la miniatura que de
    verdad usó): estado (`privacyStatus` + `publishAt` si está programado), fechas, título y la miniatura puesta.
    videos.list gasta 1 unidad de cuota por cada 50 vídeos (de 10.000 al día). Con un A/B de miniaturas en marcha,
    YouTube devuelve UNA (la principal): esa vale. `publicado_en` (snippet.publishedAt): en uno público, cuándo se hizo
    público; en uno privado o programado leído por su dueño, cuándo se subió (así lo documenta la API). Un vídeo que ya
    no existe (o no es de este canal) simplemente no sale en el resultado."""
    if not video_ids:
        return {}
    yt = _service()
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        r = yt.videos().list(part="snippet,status", id=",".join(chunk)).execute()
        for it in r.get("items", []):
            sn, st = it.get("snippet", {}), it.get("status", {})
            out[it["id"]] = {"url": mejor_miniatura(sn.get("thumbnails")),
                             "titulo": sn.get("title") or "",
                             "privacidad": st.get("privacyStatus"),
                             "publicado_en": sn.get("publishedAt"),
                             "programado_para": st.get("publishAt")}
    return out


# ------------------------------------------------------------------ datos para las IDEAS de vídeos (clipper/ideas.py)
# Todo de SOLO LECTURA y con la API oficial. Coste de cuota (de 10.000 al día; una subida gasta 1.600): lo dice cada una.
def _analytics():
    """El servicio de YouTube Analytics, o una excepción clara (sin token o sin el permiso de estadísticas)."""
    from googleapiclient.discovery import build
    creds = _load_creds()
    if creds is None:
        raise RuntimeError("YouTube no está conectado (o el permiso caducó).")
    otorgados = set(getattr(creds, "scopes", None) or [])
    if otorgados and SCOPES[-1] not in otorgados:
        raise RuntimeError("tu permiso de YouTube es anterior a las estadísticas: reconecta una vez.")
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def trafico_detalle(tipo: str, dias: int = 90, maximo: int = 25) -> list[dict]:
    """Del tráfico de TU canal en los últimos `dias`, el detalle de un tipo de fuente, de más a menos vistas:
    «YT_SEARCH» = lo que la gente escribió en el buscador de YouTube para llegar a tus vídeos; «RELATED_VIDEO» = los
    vídeos (de otros) junto a los que YouTube recomendó los tuyos. YouTube Analytics: no gasta la cuota de 10.000.
    -> [{"detalle": término o id de vídeo, "vistas": n}]"""
    from datetime import date, timedelta
    hoy = date.today()
    r = _analytics().reports().query(
        ids="channel==MINE", startDate=(hoy - timedelta(days=int(dias))).isoformat(), endDate=hoy.isoformat(),
        metrics="views", dimensions="insightTrafficSourceDetail", filters=f"insightTrafficSourceType=={tipo}",
        maxResults=max(1, min(25, int(maximo))), sort="-views").execute()
    return [{"detalle": str(f[0]), "vistas": int(f[1] or 0)} for f in (r.get("rows") or []) if len(f) >= 2 and f[0]]


def videos_con_canal(video_ids: list[str]) -> dict[str, dict]:
    """Título, canal y vistas de vídeos de CUALQUIER canal (1 unidad de cuota por cada 50).
    -> {video_id: {"titulo", "canal", "canal_id", "vistas", "publicado", "duracion_s", "idioma"}}   (`idioma`: el que declara
    el vídeo —audio o por defecto— o «»; ya viene en `snippet`: no cuesta cuota)"""
    ids = [v for v in dict.fromkeys(video_ids or []) if v]
    if not ids:
        return {}
    yt = _service()
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            sn = it.get("snippet", {})
            out[it["id"]] = {"titulo": sn.get("title", ""), "canal": sn.get("channelTitle", ""),
                             "canal_id": sn.get("channelId", ""),
                             "vistas": int((it.get("statistics") or {}).get("viewCount", 0) or 0),
                             "publicado": sn.get("publishedAt"),
                             "duracion_s": _iso_dur_s((it.get("contentDetails") or {}).get("duration", "")),
                             "idioma": str(sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "")}
    return out


def subidas_de_canal(channel_id: str, n: int = 20) -> dict:
    """Los últimos `n` vídeos de OTRO canal con sus vistas (3 unidades de cuota).
    -> {"canal": nombre, "videos": [{"video_id", "titulo", "vistas", "publicado", "duracion_s"}]}"""
    yt = _service()
    ch = yt.channels().list(part="snippet,contentDetails", id=channel_id).execute()
    items = ch.get("items", [])
    pl = items[0]["contentDetails"]["relatedPlaylists"].get("uploads") if items else None
    if not pl:
        return {"canal": "", "videos": []}
    r = yt.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=max(1, min(50, int(n)))).execute()
    ids = [it["contentDetails"]["videoId"] for it in r.get("items", []) if (it.get("contentDetails") or {}).get("videoId")]
    det = videos_con_canal(ids)
    return {"canal": items[0]["snippet"].get("title", ""),
            "videos": [{"video_id": v, "titulo": det[v]["titulo"], "vistas": det[v]["vistas"],
                        "publicado": det[v]["publicado"], "duracion_s": det[v]["duracion_s"],
                        "idioma": det[v].get("idioma", "")} for v in ids if v in det]}


def region_de(idioma: str, region: str | None = None) -> str | None:
    """El MERCADO de una búsqueda de «lo más visto»: el que se pide o, si no, el del idioma. Inglés = Estados Unidos (el autor, 2026-10-05: «el mercado de habla
    hispana y el de Estados Unidos»); español = sin país (todo el que habla español)."""
    return region or {"en": "US"}.get(str(idioma or "").lower()) or None


def busca_populares(q: str, dias: int = 30, maximo: int = 15, idioma: str = "es", region: str | None = None) -> list[dict]:
    """Lo MÁS VISTO de los últimos `dias` en YouTube para una búsqueda: search.list (100 unidades de cuota) + sus vistas
    (1 unidad). En inglés, el mercado de Estados Unidos (`region_de`). -> [{"video_id", "titulo", "canal", "canal_id", "vistas", "publicado", "duracion_s"}]"""
    from datetime import datetime, timedelta, timezone
    desde = (datetime.now(timezone.utc) - timedelta(days=int(dias))).strftime("%Y-%m-%dT%H:%M:%SZ")
    pais = region_de(idioma, region)
    r = _service().search().list(part="snippet", q=q, type="video", order="viewCount", publishedAfter=desde,
                                 relevanceLanguage=idioma, maxResults=max(1, min(50, int(maximo))),
                                 **({"regionCode": pais} if pais else {})).execute()
    ids = [it["id"]["videoId"] for it in r.get("items", []) if (it.get("id") or {}).get("videoId")]
    det = videos_con_canal(ids)
    return [{"video_id": v, **det[v]} for v in ids if v in det]


def comentarios_del_canal(maximo: int = 100) -> list[dict]:
    """Los comentarios más recientes en TUS vídeos, sin los tuyos (commentThreads: 1 unidad de cuota; +1 para saber tu
    canal). -> [{"texto", "video_id", "likes"}]"""
    # commentThreads.list SOLO acepta el permiso «youtube.force-ssl» (lo dice su ficha en la librería de Google): con el
    # token de siempre YouTube responde 403 (pasó en la 1.ª tanda real de ideas, 26-sep). Sin ese permiso, ni se llama.
    creds = _load_creds()
    otorgados = set(getattr(creds, "scopes", None) or []) if creds is not None else set()
    if "https://www.googleapis.com/auth/youtube.force-ssl" not in otorgados:
        raise RuntimeError("YouTube pide un permiso aparte para leer comentarios y el panel no lo tiene")
    yt = _service()
    ch = yt.channels().list(part="id", mine=True).execute()
    items = ch.get("items", [])
    if not items:
        return []
    mio = items[0]["id"]
    r = yt.commentThreads().list(part="snippet", allThreadsRelatedToChannelId=mio, maxResults=max(1, min(100, int(maximo))),
                                 order="time", textFormat="plainText").execute()
    out: list[dict] = []
    for it in r.get("items", []):
        sn = it.get("snippet") or {}
        top = ((sn.get("topLevelComment") or {}).get("snippet")) or {}
        texto = str(top.get("textDisplay") or "").strip()
        if texto and ((top.get("authorChannelId") or {}).get("value") != mio):
            out.append({"texto": texto[:500], "video_id": sn.get("videoId"), "likes": int(top.get("likeCount") or 0)})
    return out


def filas_retencion(r: dict) -> dict[str, dict]:
    """La respuesta de `reports.query` (dimensión video) -> {video_id: {"ret_pct", "ret_seg", "views_an", "minutos",
    "suscriptores"}}. «minutos» y «suscriptores» son None si la consulta no los trajo."""
    cols = [c.get("name") for c in r.get("columnHeaders", [])]
    out: dict[str, dict] = {}
    for fila in r.get("rows", []) or []:
        d = dict(zip(cols, fila))
        vid = d.get("video")
        if not vid:
            continue
        out[vid] = {
            "ret_pct": float(d.get("averageViewPercentage", 0) or 0),
            "ret_seg": float(d.get("averageViewDuration", 0) or 0),
            "views_an": int(d.get("views", 0) or 0),
            "minutos": int(d["estimatedMinutesWatched"] or 0) if "estimatedMinutesWatched" in d else None,
            "suscriptores": int(d["subscribersGained"] or 0) if "subscribersGained" in d else None,
        }
    return out


def video_retention(video_ids: list[str]) -> dict[str, dict]:
    """Retencion real de cada video, via la API de YouTube Analytics.

    Devuelve {video_id: {"ret_pct": 0-100+, "ret_seg": segundos, "views_an": n}}

    - ret_pct  = averageViewPercentage: el % medio del video que ve la gente.
                 En Shorts puede pasar de 100 porque cuentan los bucles.
    - ret_seg  = averageViewDuration: segundos medios vistos.

    Es la senal de CALIDAD: un Short con pocas visitas pero 90% de retencion
    es un buen clip que tuvo mala suerte; uno con muchas visitas y 30% de
    retencion enganchó al entrar y no sostuvo.

    Devuelve {} y avisa (sin reventar) si la API no esta habilitada o si el
    token todavia no tiene el permiso nuevo. Los datos tardan 1-2 dias en
    aparecer, y un video recien publicado puede no salir todavia.
    """
    if not video_ids:
        return {}
    from datetime import date

    try:
        from googleapiclient.discovery import build
    except ImportError:
        return {}
    creds = _load_creds()
    if creds is None:
        return {}

    otorgados = set(getattr(creds, "scopes", None) or [])
    if otorgados and SCOPES[-1] not in otorgados:
        log("Retencion: tu permiso actual es anterior a esta funcion. "
            "Reconecta YouTube una vez (boton del panel) y ya se leera sola.")
        return {}

    try:
        an = build("youtubeAnalytics", "v2", credentials=creds,
                   cache_discovery=False)
    except Exception as exc:                                # noqa: BLE001
        log(f"Retencion: no se pudo abrir la API de analitica ({exc}).")
        return {}

    hoy = date.today().isoformat()
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 200):        # el filtro admite 500; voy holgado
        chunk = [v for v in video_ids[i:i + 200] if v]
        if not chunk:
            continue

        def _consulta(metricas: str):
            return an.reports().query(
                ids="channel==MINE",
                startDate="2020-01-01", endDate=hoy,
                metrics=metricas,
                dimensions="video",
                filters="video==" + ",".join(chunk),
                maxResults=500,
            ).execute()

        try:
            try:
                # + tiempo visto y suscriptores ganados (el autor, 2026-09-28: 6 métricas en el Historial), misma consulta
                r = _consulta("views,averageViewDuration,averageViewPercentage,estimatedMinutesWatched,subscribersGained")
            except Exception as exc_ampliada:               # noqa: BLE001 — si no la acepta, la de siempre
                if "badrequest" not in type(exc_ampliada).__name__.lower() and "400" not in str(exc_ampliada):
                    raise
                log(f"Retencion: YouTube no aceptó tiempo visto/suscriptores ({str(exc_ampliada)[:120]}); sigo sin ellos.")
                r = _consulta("views,averageViewDuration,averageViewPercentage")
        except Exception as exc:                            # noqa: BLE001
            msg = str(exc)
            if "has not been used" in msg or "SERVICE_DISABLED" in msg:
                log("Retencion: falta habilitar la API 'YouTube Analytics' en tu "
                    "proyecto de Google Cloud. Es gratis y es un clic: "
                    "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com")
            elif "insufficient" in msg.lower() or "forbidden" in msg.lower():
                log("Retencion: el token no tiene el permiso de analitica. "
                    "Reconecta YouTube una vez desde el panel.")
            else:
                log(f"Retencion: no se pudo leer ({msg[:160]}).")
            return out

        out.update(filas_retencion(r))
    if out:
        med = sorted(v["ret_pct"] for v in out.values())[len(out) // 2]
        log(f"Retencion: leida de {len(out)} video(s). Mediana {med:.0f}% visto.")
    return out


def video_retention_curva(video_id: str) -> list[dict]:
    """La curva de retencion de UN video: en que punto exacto se te va la audiencia (pedido
    de Jose, 2026-09-22: "cruzar la retencion con el video para ver donde hubo menos pico").

    A diferencia de `video_retention` (un promedio, y varios videos a la vez), esto pide la
    dimension `elapsedVideoTimeRatio` de la API de YouTube Analytics, que SOLO admite UN video
    por peticion (no se puede pedir en lote). Devuelve unos ~100 puntos:
        [{"ratio": 0.0-1.0 (fraccion del video), "audiencia": 0-100+ (% que seguia viendo)}, ...]
    ("audiencia" puede pasar de 100 si hay repeticiones/bucles, igual que en `video_retention`).

    Devuelve [] (sin reventar) si la API no esta habilitada, el token no tiene el permiso nuevo,
    o el video es muy reciente (los datos tardan 1-2 dias en aparecer)."""
    if not video_id:
        return []
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return []
    creds = _load_creds()
    if creds is None:
        return []
    otorgados = set(getattr(creds, "scopes", None) or [])
    if otorgados and SCOPES[-1] not in otorgados:
        log("Retencion: tu permiso actual es anterior a esta funcion. Reconecta YouTube "
            "una vez (boton del panel) y ya se leera sola.")
        return []
    try:
        an = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    except Exception as exc:                             # noqa: BLE001
        log(f"Retencion: no se pudo abrir la API de analitica ({exc}).")
        return []

    from datetime import date
    try:
        r = an.reports().query(
            ids="channel==MINE",
            startDate="2020-01-01", endDate=date.today().isoformat(),
            metrics="audienceWatchRatio",
            dimensions="elapsedVideoTimeRatio",
            filters="video==" + video_id,
            maxResults=200,
        ).execute()
    except Exception as exc:                             # noqa: BLE001
        msg = str(exc)
        if "has not been used" in msg or "SERVICE_DISABLED" in msg:
            log("Retencion: falta habilitar la API 'YouTube Analytics' en tu proyecto de "
                "Google Cloud (gratis, un clic): "
                "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com")
        elif "insufficient" in msg.lower() or "forbidden" in msg.lower():
            log("Retencion: el token no tiene el permiso de analitica. Reconecta YouTube "
                "una vez desde el panel.")
        else:
            log(f"Retencion: no se pudo leer la curva de {video_id} ({msg[:160]}).")
        return []

    filas = [{"ratio": float(f[0]), "audiencia": round(float(f[1]), 1)}
             for f in (r.get("rows") or []) if len(f) >= 2]
    filas.sort(key=lambda x: x["ratio"])
    return filas


def _iso_dur_s(iso: str) -> int:
    """PT#H#M#S -> segundos."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


class _FakeResp:
    """Imita lo justo de un httplib2.Response para poder construir un HttpError
    de verdad (googleapiclient.errors.HttpError) sin abrir ninguna conexión."""

    def __init__(self, status):
        self.status = status
        self.reason = "x"

    def get(self, k, default=None):
        return default


class _FakeReq:
    """Imita el objeto que devuelve `videos().insert(...)`: cada `next_chunk()`
    lanza la siguiente excepción de la lista (si queda alguna) o devuelve el
    resultado final."""

    def __init__(self, fallos: list[Exception], resultado):
        self._fallos = list(fallos)
        self._resultado = resultado
        self.llamadas = 0

    def next_chunk(self, num_retries=0):
        self.llamadas += 1
        if self._fallos:
            raise self._fallos.pop(0)
        return self._resultado


class _FakeProg:
    def __init__(self, frac):
        self._frac = frac

    def progress(self):
        return self._frac


def prueba() -> list[dict]:
    """Un corte de red a mitad de una subida NO debe dejar el vídeo en "error"
    para que el autor tenga que darle a Reintentar a mano — el autor lo pidió
    (2026-09-22) para publicar igual que ya pasa con el render. Solo lo
    realmente transitorio (red, 5xx) se reintenta; cuota agotada o un error
    de verdad (4xx) se avisa al instante, sin hacer perder el tiempo."""
    from googleapiclient.errors import HttpError

    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    r("HttpError 503 (servidor caído) se considera transitorio",
      _es_transitorio(HttpError(_FakeResp(503), b"{}")))
    r("HttpError 500 se considera transitorio",
      _es_transitorio(HttpError(_FakeResp(500), b"{}")))
    r("HttpError 403 (cuota/permiso) NO se reintenta",
      not _es_transitorio(HttpError(_FakeResp(403), b"{}")))
    th = {"default": {"url": "d.jpg"}, "high": {"url": "h.jpg"}, "maxres": {"url": "m.jpg"}}
    # el nombre del canal se guarda 10 min: sin eso, cada pestaña abierta gastaba ~2.900 unidades al día en leerlo
    real, llamadas = globals()["_channel_info"], []
    globals()["_channel_info"] = lambda: (llamadas.append(1), {"connected": True, "channel": "MI CANAL", "channel_id": "UC1"})[1]
    _CANAL.update(ts=0.0, marca=None, val=None)
    try:
        a1 = channel_info(_ahora=1000.0)
        a2 = channel_info(_ahora=1030.0)
        a3 = channel_info(_ahora=1000.0 + _CANAL_TTL_S + 1)
        r("el nombre del canal se pregunta a YouTube UNA vez cada 10 min (30 s después sale de la memoria, a los 10 min se vuelve a preguntar)",
          a1 == a2 == a3 and len(llamadas) == 2, len(llamadas))
        globals()["_channel_info"] = lambda: (llamadas.append(1), {"connected": False, "channel": None, "error": "caído"})[1]
        _CANAL.update(ts=0.0, marca=None, val=None)
        n0 = len(llamadas)
        channel_info(_ahora=2000.0)
        channel_info(_ahora=2001.0)
        r("un error NO se guarda: se vuelve a preguntar en la siguiente", len(llamadas) - n0 == 2, len(llamadas) - n0)
        globals()["_channel_info"] = lambda: (llamadas.append(1), {"connected": True, "channel": "MI CANAL", "channel_id": "UC1"})[1]
        channel_info(_ahora=3000.0)
        n1 = len(llamadas)
        _CANAL["marca"] = -1.0                                   # como si el archivo del token hubiera cambiado (reconectar)
        channel_info(_ahora=3001.0)
        r("al reconectar (cambia el archivo del token) se vuelve a preguntar al momento, sin esperar los 10 min", len(llamadas) - n1 == 1, len(llamadas) - n1)
    finally:
        globals()["_channel_info"] = real
        _CANAL.update(ts=0.0, marca=None, val=None)
    r("de las miniaturas de un vídeo se coge la más grande que exista (maxres > standard > high…)",
      mejor_miniatura(th) == "m.jpg" and mejor_miniatura({"default": {"url": "d.jpg"}}) == "d.jpg"
      and mejor_miniatura(None) is None and mejor_miniatura({}) is None)
    r("HttpError 400 (petición mal formada) NO se reintenta",
      not _es_transitorio(HttpError(_FakeResp(400), b"{}")))
    r("un corte de conexión (OSError) se considera transitorio",
      _es_transitorio(ConnectionError("red caída")))
    r("un timeout se considera transitorio", _es_transitorio(TimeoutError("timeout")))
    r("un error que no tiene nada que ver con la red NO se reintenta",
      not _es_transitorio(ValueError("el vídeo no es válido")))

    real_sleep = time.sleep
    dormidas: list[float] = []
    time.sleep = lambda s: dormidas.append(s)
    try:
        # 1) responde a la primera: ni reintenta ni duerme
        dormidas.clear()
        req = _FakeReq([], (_FakeProg(1.0), {"id": "v1"}))
        vistos = []
        resp = _subir_con_reintento(req, lambda p: vistos.append(p), reintento_s=90.0)
        r("responde a la primera sin reintentar ni dormir",
          resp == {"id": "v1"} and not dormidas and vistos == [100], f"dormidas={dormidas} pct={vistos}")

        # 2) dos cortes de red seguidos y a la tercera responde: reintenta, no se rinde
        dormidas.clear()
        req2 = _FakeReq([ConnectionError("caída 1"), ConnectionError("caída 2")],
                        (_FakeProg(1.0), {"id": "v2"}))
        resp2 = _subir_con_reintento(req2, None, reintento_s=90.0)
        r("dos cortes de red transitorios se reintentan y la subida termina bien",
          resp2 == {"id": "v2"} and dormidas == [6.0, 12.0], f"resp={resp2} dormidas={dormidas}")

        # 3) fallos transitorios sin parar: se rinde cuando el presupuesto se agota (no espera para siempre)
        dormidas.clear()
        req3 = _FakeReq([ConnectionError("caída"), ConnectionError("caída"), ConnectionError("caída")],
                        (_FakeProg(1.0), {"id": "v3"}))
        try:
            _subir_con_reintento(req3, None, reintento_s=10.0)
            r("con el presupuesto de reintento agotado, se rinde (no reintenta para siempre)", False)
        except ConnectionError:
            r("con el presupuesto de reintento agotado, se rinde (no reintenta para siempre)",
              dormidas == [6.0], f"dormidas={dormidas}")

        # 4) un error NO transitorio corta al instante, sin reintentar ni dormir
        dormidas.clear()
        req4 = _FakeReq([ValueError("vídeo corrupto")], (_FakeProg(1.0), {"id": "v4"}))
        try:
            _subir_con_reintento(req4, None, reintento_s=90.0)
            r("un error no transitorio NO se reintenta", False)
        except ValueError:
            r("un error no transitorio NO se reintenta", not dormidas and req4.llamadas == 1, f"dormidas={dormidas}")

        # 5) «lo más visto» de las ideas: el mercado de una búsqueda sale del idioma (inglés = EE. UU., español = sin país) o de lo que se pida
        r("el mercado de «lo más visto»: inglés = Estados Unidos, español = sin país, y un país pedido manda",
          region_de("en") == "US" and region_de("EN") == "US" and region_de("es") is None and region_de("") is None
          and region_de("es", "MX") == "MX" and region_de("en", "GB") == "GB",
          (region_de("en"), region_de("es"), region_de("es", "MX")))
    finally:
        time.sleep = real_sleep
    return out
