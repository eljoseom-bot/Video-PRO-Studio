"""📚 LISTAS DE REPRODUCCIÓN al publicar (el autor, 2026-10-05: «¿hay alguna forma de seleccionar by default las listas de reproducción?… que desde la interfaz, por API, se vayan
todas… una de las cosas que hago después de publicarlo es colocarlo en una lista»).

- La API de YouTube SÍ lo permite: `playlists.list` (1 unidad de cuota) para ver tus listas y `playlistItems.insert` (50 unidades) para añadir un vídeo a una; con el permiso «youtube» que
  el panel ya tiene (no hace falta reconectar). Nada que ver con el «Test & Compare» (el A/B de título y miniatura), que NO está en la API.
- Las listas que marques en el panel se RECUERDAN (`work/youtube_listas.json`, una elección por canal de YouTube): «⚡ Programar rápido» y «📅 a mano» añaden cada vídeo a esas listas en
  cuanto se sube. La PRIMERA vez (aún no has elegido nada) se marca por defecto la lista que tiene MÁS vídeos (el autor, 2026-10-05: «por defecto… la que tenga más videos, por ejemplo SEO/Google,
  que sea la primera que te muestro»): el desplegable las enseña de más a menos vídeos. Si desmarcas todas, se respeta: «ninguna» también es una elección y no se vuelve a poner sola.
- Un fallo al añadir (red, lista borrada, cuota) NO tumba la publicación —el vídeo ya está en YouTube—: se avisa con el motivo y «↻ Listas» lo reintenta (también sirve para meter en la
  lista un vídeo que publicaste antes).
Funciones puras + `prueba()` (con un YouTube de mentira).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from .util import log, reemplaza

PATRON = re.compile(r"[A-Za-z0-9_-]{10,64}")          # el id de una lista de YouTube («PLxxxxxxxx…»)
MAX_LISTAS = 10
MSG_CUOTA = "Se acabó la cuota diaria de YouTube (se renueva a medianoche, hora del Pacífico): añádelo mañana con «↻ Listas»."
_LOCK = threading.Lock()


def _ruta(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work) / "youtube_listas.json"
    from . import config
    return config.paths()["work"] / "youtube_listas.json"


def _lee_todo(dir_work=None) -> dict:
    try:
        d = json.loads(_ruta(dir_work).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def limpia(ids) -> list[str]:
    """Una lista de ids de listas -> solo los que tienen forma de id de YouTube, sin repetir y hasta `MAX_LISTAS`."""
    out: list[str] = []
    for x in ids if isinstance(ids, (list, tuple)) else []:
        s = str(x or "").strip()
        if PATRON.fullmatch(s) and s not in out:
            out.append(s)
    return out[:MAX_LISTAS]


def _n(x) -> int:
    try:
        return int(x.get("n") or 0)
    except (TypeError, ValueError):
        return 0


def ordena(listas) -> list[dict]:
    """Tus listas de MÁS a MENOS vídeos (a igual número, por orden alfabético; sin cifra, al final): la primera es la que se marca por defecto y la que sale arriba en el desplegable.
    Lo que no es una lista (sin id) se ignora y la original no se toca."""
    return sorted((x for x in listas or [] if isinstance(x, dict) and x.get("id")), key=lambda x: (-_n(x), str(x.get("titulo") or "").lower()))


def por_defecto(listas) -> list[str]:
    """La lista que se marca por defecto la PRIMERA vez: la que tiene MÁS vídeos ([] si no hay ninguna)."""
    return limpia([x["id"] for x in ordena(listas)[:1]])


def elegidas(canal_id: str | None, dir_work=None) -> dict:
    """Las listas marcadas para ese canal de YouTube: {"listas": [ids], "posicion": «final» | «inicio», "definida": ¿ya eligió algo alguna vez? (incluso «ninguna»)}. `definida` distingue
    «nunca ha elegido» (se marca por defecto la de más vídeos) de «eligió ninguna» (se respeta)."""
    e = ((_lee_todo(dir_work).get("canales") or {}).get(str(canal_id or "")) or {})
    return {"listas": limpia(e.get("listas")), "posicion": "inicio" if e.get("posicion") == "inicio" else "final", "definida": "listas" in e}


def guarda(canal_id: str | None, ids, dir_work=None, posicion: str | None = None) -> dict:
    """Recuerda la elección (la que se aplicará a los próximos vídeos de ese canal). -> lo guardado."""
    with _LOCK:
        todo = _lee_todo(dir_work)
        canales = dict(todo.get("canales") or {})
        ant = canales.get(str(canal_id or "")) or {}
        pos = "inicio" if (posicion or ant.get("posicion")) == "inicio" else "final"
        canales[str(canal_id or "")] = {"listas": limpia(ids), "posicion": pos, "ts": time.time()}
        ruta = _ruta(dir_work)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(f"{ruta.name}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"canales": canales}, ensure_ascii=False, indent=1), encoding="utf-8")
        reemplaza(tmp, ruta)
    return elegidas(canal_id, dir_work)


def para_publicar(pedidas, canal_id: str | None, dir_work=None) -> list[str]:
    """Las listas de UN vídeo que se encola: las que pidió la fila (aunque sea una lista vacía: «a ninguna») o, si no dijo nada, las marcadas por defecto."""
    if isinstance(pedidas, (list, tuple)):
        return limpia(pedidas)
    return elegidas(canal_id, dir_work)["listas"]


def explica(exc: Exception) -> str:
    """El error de YouTube al añadir a una lista, en claro y con qué hacer."""
    from . import youtube
    r = youtube._razon(exc).lower()
    if r == "playlistnotfound":
        return "Esa lista ya no existe en YouTube (¿la borraste?): desmárcala."
    if r == "videonotfound":
        return "YouTube aún no encuentra el vídeo (acaba de subirse): prueba «↻ Listas» en un minuto."
    if "maximumnumberofvideos" in r:
        return "La lista está llena (YouTube admite 5.000 vídeos por lista)."
    if r in ("forbidden", "insufficientpermissions", "playlistitemsnotaccessible") or "forbidden" in r:
        return "YouTube no deja añadir a esa lista (¿es de otro canal o de otra cuenta?)."
    texto = str(exc).replace("\n", " ")
    return (texto[:160] + "…") if len(texto) > 160 else texto


def anade_todas(video_id: str, ids: list[str], anade, posicion: str = "final", dormir=time.sleep, reintentos: int = 3) -> list[dict]:
    """Añade el vídeo a cada lista, una detrás de otra. NUNCA lanza: devuelve [{"id", "ok", "ya", "error"}] (`ya`: ya estaba en la lista). Un corte de red o un «el vídeo aún no
    existe» (YouTube tarda unos segundos en verlo tras la subida) se reintenta con pausa creciente; una lista que ya no existe no frena a las demás; si se acaba la cuota del
    día se deja de intentar y las que faltan lo dicen. `anade(video_id, lista_id, posicion)` es `youtube.anade_a_lista` (de mentira en las pruebas)."""
    from . import youtube
    out: list[dict] = []
    sin_cuota = False
    for lid in ids:
        if sin_cuota:
            out.append({"id": lid, "ok": False, "ya": False, "error": MSG_CUOTA})
            continue
        res = None
        for k in range(max(1, reintentos)):
            try:
                r = anade(video_id, lid, posicion) or {}
                res = {"id": lid, "ok": True, "ya": bool(r.get("ya")), "error": ""}
                break
            except youtube.CuotaAgotada:
                sin_cuota = True
                res = {"id": lid, "ok": False, "ya": False, "error": MSG_CUOTA}
                break
            except Exception as exc:                           # noqa: BLE001
                if (youtube._es_transitorio(exc) or youtube._razon(exc).lower() == "videonotfound") and k < reintentos - 1:
                    dormir(4.0 * (k + 1))
                    continue
                log(f"Listas: no se pudo añadir {video_id} a {lid}: {type(exc).__name__}: {str(exc)[:200]}")
                res = {"id": lid, "ok": False, "ya": False, "error": explica(exc)}
                break
        out.append(res)
    return out


def texto_resultado(res: list[dict], titulos: dict | None = None) -> str:
    """El resultado en una línea para el registro: «SEO para Google ✔ · Cursos ✖ (motivo)»."""
    t = titulos or {}
    return " · ".join(f"{t.get(r['id'], r['id'])} " + ("✔" + (" (ya estaba)" if r.get("ya") else "") if r["ok"] else f"✖ ({r['error']})") for r in res)


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    from . import youtube
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    try:
        import httplib2
        from googleapiclient.errors import HttpError

        def http(status, razon):
            return HttpError(httplib2.Response({"status": status}), json.dumps({"error": {"errors": [{"reason": razon}], "message": razon}}).encode("utf-8"))
    except ImportError:                                        # sin las librerías de Google: no hay con qué fabricar errores de YouTube
        caso("(se omiten las pruebas de listas: faltan las librerías de Google)", True)
        return filas

    A, B, C = "PLaaaaaaaaaaaa", "PLbbbbbbbbbbbb", "PLcccccccccccc"
    caso("los ids de listas: solo los que tienen forma de id de YouTube, sin repetir y hasta 10",
         limpia([A, A, " " + B + " ", "no vale", "", None, 7, C]) == [A, B, C] and limpia("PLxxxxxxxxxxxx") == [] and len(limpia([f"PL{k:012d}" for k in range(30)])) == MAX_LISTAS,
         limpia([A, A, B, "x"]))
    mezcla = [{"id": B, "titulo": "Cursos", "n": 3}, {"id": A, "titulo": "SEO/GOOGLE", "n": 92}, {"id": C, "titulo": "Zeta", "n": 3}, {"id": "PLdddddddddddd", "titulo": "Sin cifra"},
              {"titulo": "sin id", "n": 500}, "basura"]
    ordenadas = ordena(mezcla)
    caso("tus listas se enseñan de MÁS a MENOS vídeos (a igual número, por orden alfabético; sin cifra, al final; lo que no es una lista se ignora) y la original no se toca",
         [x["id"] for x in ordenadas] == [A, B, C, "PLdddddddddddd"] and [x["id"] for x in mezcla[:2]] == [B, A] and ordena(None) == [], [x["id"] for x in ordenadas])
    caso("por defecto se marca SOLO la que tiene más vídeos (SEO/GOOGLE, 92); si no hay listas, ninguna; en un empate, la primera",
         por_defecto(mezcla) == [A] and por_defecto([]) == [] and por_defecto(None) == [] and por_defecto([{"id": B, "n": 3}, {"id": C, "n": 3}]) == [B], por_defecto(mezcla))
    with tempfile.TemporaryDirectory() as tmp:
        caso("sin nada guardado no hay ninguna lista marcada y se sabe que NUNCA se ha elegido (definida: falso: es lo que deja marcar la de más vídeos la primera vez)",
             elegidas("UC1", tmp) == {"listas": [], "posicion": "final", "definida": False}, elegidas("UC1", tmp))
        g = guarda("UC1", [A, B, "basura"], tmp)
        guarda("UC2", [C], tmp, posicion="inicio")
        caso("la elección se recuerda POR CANAL de YouTube, y con la posición (final por defecto)",
             g == {"listas": [A, B], "posicion": "final", "definida": True} and elegidas("UC1", tmp)["listas"] == [A, B]
             and elegidas("UC2", tmp) == {"listas": [C], "posicion": "inicio", "definida": True} and elegidas("UC3", tmp)["listas"] == [], (g, elegidas("UC2", tmp)))
        guarda("UC1", [], tmp)
        caso("desmarcar todas se recuerda también: «ninguna» es una elección (definida) y NO se vuelve a poner la de por defecto",
             elegidas("UC1", tmp)["listas"] == [] and elegidas("UC1", tmp)["definida"] is True and elegidas("UC2", tmp)["listas"] == [C])
        guarda("UC1", [A], tmp)
        caso("una fila que pide listas (aunque sea ninguna) manda sobre lo marcado por defecto; si no dice nada, las de por defecto",
             para_publicar([B], "UC1", tmp) == [B] and para_publicar([], "UC1", tmp) == [] and para_publicar(None, "UC1", tmp) == [A]
             and para_publicar(None, "UC9", tmp) == [], (para_publicar([B], "UC1", tmp), para_publicar(None, "UC1", tmp)))

    # ---- añadir de verdad (con un YouTube de mentira)
    dormidas: list[float] = []
    llamadas: list[tuple] = []

    def falso(guion):
        """`guion`: lo que pasa con cada intento por (lista) -> lista de resultados o excepciones."""
        estado = {k: list(v) for k, v in guion.items()}

        def anade(video, lista, posicion):
            llamadas.append((video, lista, posicion))
            paso = estado.get(lista, [{"ok": True}]).pop(0) if estado.get(lista) else {"ok": True}
            if isinstance(paso, Exception):
                raise paso
            return paso
        return anade

    llamadas.clear()
    res = anade_todas("VID", [A, B], falso({}), dormir=dormidas.append)
    caso("añadir a varias listas: todas ok, en el orden pedido y con la posición", [r["ok"] for r in res] == [True, True] and [x[1] for x in llamadas] == [A, B]
         and all(x[2] == "final" for x in llamadas), res)
    llamadas.clear()
    res = anade_todas("VID", [A], falso({A: [{"ok": True, "ya": True}]}), dormir=dormidas.append)
    caso("si el vídeo ya estaba en la lista no es un error (se dice «ya estaba»)", res[0]["ok"] and res[0]["ya"] and "ya estaba" in texto_resultado(res), res)
    dormidas.clear()
    llamadas.clear()
    res = anade_todas("VID", [A], falso({A: [http(404, "videoNotFound"), http(503, "backendError"), {"ok": True}]}), dormir=dormidas.append)
    caso("«el vídeo aún no existe» (acaba de subirse) y un 503 se reintentan con pausa creciente y termina bien",
         res[0]["ok"] and len(llamadas) == 3 and dormidas == [4.0, 8.0], (res, dormidas))
    llamadas.clear()
    res = anade_todas("VID", [A, B, C], falso({A: [http(404, "playlistNotFound")], B: [ConnectionError("red caída")] * 3}), dormir=dormidas.append)
    caso("una lista que ya no existe NO frena a las demás y el error dice qué hacer; un fallo de red que no cede se rinde tras 3 intentos",
         [r["ok"] for r in res] == [False, False, True] and "ya no existe" in res[0]["error"] and len([x for x in llamadas if x[1] == B]) == 3
         and llamadas[-1][1] == C, res)
    llamadas.clear()
    res = anade_todas("VID", [A, B, C], falso({B: [youtube.CuotaAgotada("quotaExceeded")]}), dormir=dormidas.append)
    caso("si se acaba la cuota del día se deja de intentar (no se gasta más) y las que faltan lo dicen",
         [r["ok"] for r in res] == [True, False, False] and "cuota" in res[1]["error"] and "cuota" in res[2]["error"] and [x[1] for x in llamadas] == [A, B], res)
    res = anade_todas("VID", [A], falso({A: [http(403, "forbidden")]}), dormir=dormidas.append)
    caso("un 403 se explica («es de otro canal») y no se reintenta", not res[0]["ok"] and "otro canal" in res[0]["error"], res)
    caso("sin listas no se llama a YouTube", anade_todas("VID", [], falso({}), dormir=dormidas.append) == [])

    # ---- las dos llamadas a YouTube (con un servicio de mentira: ninguna petición real)
    import sys

    class _Ej:
        def __init__(self, f):
            self.f = f

        def execute(self, num_retries=0):
            return self.f()

    class _Svc:
        def __init__(self):
            self.log: list = []
            self.fallo = None
            self.ya_esta = False          # ¿playlistItems.list encuentra ya ese vídeo en la lista?

        def playlists(self):
            return self

        def list(self, **kw):
            self.log.append(("list", kw))
            pag = {None: {"items": [{"id": "PLaaaaaaaaaaaa", "snippet": {"title": "SEO para Google"}, "contentDetails": {"itemCount": 12}, "status": {"privacyStatus": "public"}}],
                          "nextPageToken": "p2"},
                   "p2": {"items": [{"id": "PLbbbbbbbbbbbb", "snippet": {"title": "Cursos"}, "contentDetails": {"itemCount": 3}, "status": {"privacyStatus": "unlisted"}}]}}
            return _Ej(lambda: pag[kw.get("pageToken")])

        def playlistItems(self):
            svc = self

            class _It:
                def list(self, **kw):
                    svc.log.append(("items.list", kw))
                    return _Ej(lambda: {"items": [{"id": "ITEM-QUE-YA-ESTABA"}] if svc.ya_esta else []})

                def insert(self, **kw):
                    svc.log.append(("insert", kw))

                    def f():
                        if svc.fallo:
                            raise svc.fallo
                        return {"id": "x"}
                    return _Ej(f)
            return _It()

    svc = _Svc()
    modulo = sys.modules[youtube.__name__]
    real = modulo._service
    modulo._service = lambda: svc
    try:
        mias = youtube.listas_mias()
        caso("tus listas: recorre todas las páginas y trae id, título, nº de vídeos y privacidad (solo las tuyas: mine=True)",
             mias == [{"id": "PLaaaaaaaaaaaa", "titulo": "SEO para Google", "n": 12, "privacidad": "public"},
                      {"id": "PLbbbbbbbbbbbb", "titulo": "Cursos", "n": 3, "privacidad": "unlisted"}]
             and [x[1].get("mine") for x in svc.log if x[0] == "list"] == [True, True] and svc.log[1][1].get("pageToken") == "p2", mias)
        svc.log.clear()
        nuevo = youtube.anade_a_lista("VID", "PLaaaaaaaaaaaa")
        youtube.anade_a_lista("VID", "PLaaaaaaaaaaaa", "inicio")
        ins = [x[1] for x in svc.log if x[0] == "insert"]
        c1, c2 = ins[0], ins[1]
        caso("añadir a una lista: el cuerpo lleva la lista y el vídeo; al final por defecto, y «inicio» pone position 0; devuelve el id del elemento (para deshacerlo)",
             c1["part"] == "snippet" and c1["body"]["snippet"] == {"playlistId": "PLaaaaaaaaaaaa", "resourceId": {"kind": "youtube#video", "videoId": "VID"}}
             and c2["body"]["snippet"]["position"] == 0 and nuevo == {"ok": True, "ya": False, "item": "x"}, (c1, c2, nuevo))
        svc.log.clear()
        svc.ya_esta = True
        repetido = youtube.anade_a_lista("VID", "PLaaaaaaaaaaaa")
        caso("NO duplica: YouTube deja meter el mismo vídeo dos veces en una lista (comprobado de verdad), así que antes mira si ya está y, si está, no inserta nada",
             repetido == {"ok": True, "ya": True, "item": "ITEM-QUE-YA-ESTABA"} and [x[0] for x in svc.log] == ["items.list"]
             and svc.log[0][1].get("videoId") == "VID" and svc.log[0][1].get("playlistId") == "PLaaaaaaaaaaaa", (repetido, svc.log))
        svc.ya_esta = False
        svc.fallo = http(409, "videoAlreadyInPlaylist")
        ya = youtube.anade_a_lista("VID", "PLaaaaaaaaaaaa")
        svc.fallo = http(403, "quotaExceeded")
        try:
            youtube.anade_a_lista("VID", "PLaaaaaaaaaaaa")
            cuota = False
        except youtube.CuotaAgotada:
            cuota = True
        svc.fallo = http(404, "playlistNotFound")
        try:
            youtube.anade_a_lista("VID", "PLaaaaaaaaaaaa")
            otro = None
        except Exception as exc:                               # noqa: BLE001
            otro = youtube._razon(exc)
        caso("«ya estaba en la lista» (409) no es un error; la cuota agotada se avisa aparte; cualquier otro error sube tal cual, con su razón",
             ya == {"ok": True, "ya": True} and cuota and otro == "playlistNotFound", (ya, cuota, otro))
    finally:
        modulo._service = real
    return filas
