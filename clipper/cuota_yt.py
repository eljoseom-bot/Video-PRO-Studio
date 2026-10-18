"""▶ Cuánta cuota de la API de YouTube gasta ESTE programa hoy (el autor, 12-oct-2026: «siempre ponme algo visual para saber cuántas peticiones me quedan… en todo»).

YouTube no deja preguntar «cuánto me queda» por API: la cuota (10.000 unidades al día por proyecto de Google Cloud; el día se reinicia a medianoche del Pacífico) solo se ve en la consola de Google Cloud.
Aquí se CUENTA lo que el programa gasta (lo que cuesta cada llamada es público): cada petición que sale por el cliente de `youtube._service()` pasa por `contador()` y suma su coste en `work/youtube_cuota.json`.
Es una ESTIMACIÓN —no incluye lo que gaste otro programa con el mismo proyecto de Google Cloud (p. ej. los Shorts de otros programas) ni la API de Analytics, que tiene su propia cuota—; el panel lo dice así.
Nunca rompe una llamada: si contar falla, la petición sigue.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TOPE = 10_000
SUBIDA = 1_600                       # videos.insert (la subida de un vídeo, largo o Short)
_LOCK = threading.RLock()

# lo que cuesta cada llamada (documentación de YouTube Data API v3). Clave: (recurso, método HTTP) -> unidades. Lo que no está en la tabla: 1 (una lectura).
_COSTE = {("search", "GET"): 100,
          ("videos", "POST"): SUBIDA, ("videos", "PUT"): 50, ("videos", "DELETE"): 50,
          ("thumbnails", "POST"): 50,
          ("playlistItems", "POST"): 50, ("playlistItems", "PUT"): 50, ("playlistItems", "DELETE"): 50,
          ("playlists", "POST"): 50, ("playlists", "PUT"): 50, ("playlists", "DELETE"): 50,
          ("comments", "POST"): 50, ("commentThreads", "POST"): 50,
          ("captions", "POST"): 400, ("captions", "PUT"): 450, ("captions", "DELETE"): 50}
_NOMBRES = {"videos": "subir/editar vídeos", "thumbnails": "miniaturas", "playlistItems": "listas de reproducción", "playlists": "listas de reproducción",
            "search": "búsquedas", "captions": "subtítulos"}


def _ruta(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work) / "youtube_cuota.json"
    from . import config
    return config.paths()["work"] / "youtube_cuota.json"


def _dia(ts: float) -> str:
    from .uso import dia_cuota
    return dia_cuota(ts)


def coste(uri: str, metodo: str) -> tuple[str, int] | None:
    """(recurso, unidades) de una petición a la API de YouTube, o None si no es la Data API (p. ej. el trozo de una subida que ya se contó, o Analytics)."""
    u = urlparse(str(uri))
    m = re.search(r"/youtube/v3/([A-Za-z]+)", u.path)
    if not m or "analytics" in u.netloc.lower():
        return None
    recurso = m.group(1)
    if recurso == "thumbnails":
        recurso = "thumbnails"
    metodo = str(metodo or "GET").upper()
    if "upload_id" in parse_qs(u.query):                 # un trozo (PUT) de una subida resumible: la subida entera ya se contó al empezarla
        return None
    if "/upload/" in u.path and metodo not in ("POST",):
        return None
    return recurso, _COSTE.get((recurso, metodo), 1)


def apunta(uri: str, metodo: str, dir_work=None, ahora: float | None = None) -> int:
    """Suma lo que cuesta esta petición al día de hoy (hora del Pacífico). -> las unidades sumadas (0 si no cuenta)."""
    c = coste(uri, metodo)
    if not c:
        return 0
    recurso, unidades = c
    ahora = ahora if ahora is not None else time.time()
    with _LOCK:
        p = _ruta(dir_work)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        dia = _dia(ahora)
        if d.get("dia") != dia:
            d = {"dia": dia, "unidades": 0, "por": {}}
        d["unidades"] = int(d.get("unidades") or 0) + unidades
        por = d.setdefault("por", {})
        nombre = _NOMBRES.get(recurso, "lecturas") if unidades > 1 else "lecturas"            # una lectura (1 unidad) va a «lecturas», no a «subir vídeos»
        por[nombre] = int(por.get(nombre, 0)) + unidades
        try:
            from .util import reemplaza
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f"{p.name}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            reemplaza(tmp, p)
        except OSError:
            pass
    return unidades


def hoy(dir_work=None, ahora: float | None = None) -> dict:
    """Lo gastado hoy: {"unidades", "quedan", "subidas" (cuántas subidas de 1.600 caben aún), "por": {...}, "tope"}."""
    ahora = ahora if ahora is not None else time.time()
    try:
        d = json.loads(_ruta(dir_work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if d.get("dia") != _dia(ahora):
        d = {"unidades": 0, "por": {}}
    u = int(d.get("unidades") or 0)
    quedan = max(0, TOPE - u)
    return {"unidades": u, "quedan": quedan, "tope": TOPE, "subidas": quedan // SUBIDA, "por": d.get("por") or {}}


def contador(servicio, dir_work=None):
    """Hace que el cliente de YouTube cuente lo que gasta (envuelve su `request` HTTP). Devuelve el mismo servicio. Contar nunca rompe una llamada."""
    try:
        http = servicio._http
        original = http.request

        def contando(uri, method="GET", *a, **k):
            try:
                apunta(uri, method, dir_work)
            except Exception:                                       # noqa: BLE001 — contar es un extra
                pass
            return original(uri, method, *a, **k)

        http.request = contando
    except Exception:                                               # noqa: BLE001
        pass
    return servicio


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:300]})

    base = "https://youtube.googleapis.com/youtube/v3/"
    caso("lo que cuesta cada llamada: leer 1, buscar 100, subir un vídeo 1.600, miniatura/lista/editar 50",
         coste(base + "videos?part=snippet&id=x", "GET") == ("videos", 1) and coste(base + "search?q=x", "GET") == ("search", 100)
         and coste("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet", "POST") == ("videos", 1600)
         and coste("https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=x", "POST") == ("thumbnails", 50)
         and coste(base + "playlistItems?part=snippet", "POST") == ("playlistItems", 50) and coste(base + "videos?part=snippet", "PUT") == ("videos", 50)
         and coste(base + "playlists?mine=true", "GET") == ("playlists", 1), "")
    caso("los trozos de una subida resumible (con upload_id) NO se cuentan otra vez, ni Analytics, ni lo que no es de YouTube",
         coste("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=AB12", "PUT") is None
         and coste("https://youtubeanalytics.googleapis.com/v2/reports?ids=x", "GET") is None and coste("https://ejemplo.com/otra", "GET") is None)
    with tempfile.TemporaryDirectory() as t:
        t0 = 1_800_000_000.0
        apunta("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable", "POST", t, t0)
        apunta(base + "videos?id=x", "GET", t, t0 + 5)
        apunta(base + "search?q=x", "GET", t, t0 + 9)
        h = hoy(t, t0 + 10)
        caso("suma por día: una subida + una lectura + una búsqueda = 1.701 unidades; quedan 8.299 (aún caben 5 subidas) y el desglose dice en qué",
             h["unidades"] == 1701 and h["quedan"] == 8299 and h["subidas"] == 5 and h["por"].get("subir/editar vídeos") == 1600 and h["por"].get("búsquedas") == 100, h)
        caso("al día siguiente (medianoche del Pacífico) vuelve a empezar de cero", hoy(t, t0 + 86400 * 1.5)["unidades"] == 0 and hoy(t, t0 + 86400 * 1.5)["subidas"] == 6)
        llamadas: list = []

        class Http:
            def request(self, uri, method="GET", *a, **k):
                llamadas.append((uri, method))
                return ("resp", "cuerpo")

        class Servicio:
            _http = Http()

        s = contador(Servicio(), t)
        r = s._http.request(base + "channels?mine=true", "GET")
        caso("el cliente de YouTube envuelto cuenta lo que gasta y devuelve lo mismo que antes (no cambia la llamada)",
             r == ("resp", "cuerpo") and llamadas == [(base + "channels?mine=true", "GET")] and hoy(t)["unidades"] >= 1, (r, llamadas))
        apunta_real = apunta

        def rota(*a, **k):
            raise RuntimeError("disco lleno")
        globals()["apunta"] = rota
        try:
            r2 = s._http.request(base + "videos?id=y", "GET")
        finally:
            globals()["apunta"] = apunta_real
        caso("si contar falla, la petición sigue igual (contar nunca rompe nada)", r2 == ("resp", "cuerpo"), r2)
        caso("un servicio raro (sin _http) vuelve tal cual, sin reventar", contador(object()) is not None)
    return filas
