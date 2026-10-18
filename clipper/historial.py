"""Historial permanente de tus vídeos PRO — sobrevive aunque borres los .mp4 del PC.

el autor pidió (2026-09-22): "si yo llegase a eliminar esos videos dentro de mi PC, yo quisiera
que se mantuviera el video... en esa UI" — para llevar su historial completo aunque libere
disco borrando los .mp4 viejos.

Por qué un archivo APARTE de manifest.json: manifest.json vive DENTRO de la carpeta de salida
(`videos/<jid>/`) — si borras esa carpeta, se borra con él. Este historial vive en `work/`
(que el autor no toca a mano) y guarda solo lo LIGERO: título, duración, canal, una copia chica
de la miniatura (nunca el vídeo) y, si se publicó, el `video_id` de YouTube — con eso basta
para seguir pidiendo métricas y retención (ver `uso_metricas.py`) aunque el .mp4 ya no exista.

Se guarda por FAMILIA (el `base` de `versiones.parse`): una mejora (v2, v3…) ACTUALIZA la
misma fila, no crea una aparte — igual que "Mis vídeos" enseña una sola tarjeta por vídeo.
Nunca rompe un render ni una publicación si algo aquí falla: es un extra, no el camino
principal.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .util import log


def _dir_work(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work)
    from . import config
    return config.paths()["work"]


def ruta(dir_work=None) -> Path:
    return _dir_work(dir_work) / "historial.json"


def dir_miniaturas(dir_work=None) -> Path:
    return _dir_work(dir_work) / "historial_miniaturas"


def dir_transcripciones(dir_work=None) -> Path:
    return _dir_work(dir_work) / "historial_transcripciones"


# lo LIGERO de cómo hablas en ese vídeo: la transcripción (~60 KB) y lo ya medido (pausas de duda, muletillas)
_HABLA = ("words.json", "pausas_duda.json", "muletillas.json")


def conserva_habla(base: str, dir_work=None) -> bool:
    """Guarda aparte la transcripción de ese vídeo (y lo ya medido de cómo hablas), para que «🗣️ Tus muletillas» siga
    funcionando aunque borres el vídeo del PC: antes decía «aún no tiene transcripción» en cuanto lo borrabas (el autor,
    2026-09-28, con sus 5 últimos vídeos). Nunca el audio ni el vídeo. Se copia de `work/<base>--pro/` al publicar
    (`registra_publicado`) y justo antes del 🗑 (el panel mide antes las pausas de duda, que necesitan el audio). Nunca
    lanza. -> True si quedó guardada."""
    try:
        origen = _dir_work(dir_work) / f"{base}--pro"
        if not (origen / "words.json").is_file():
            return False
        destino = dir_transcripciones(dir_work) / base
        destino.mkdir(parents=True, exist_ok=True)
        for nombre in _HABLA:
            if (origen / nombre).is_file():
                shutil.copy2(origen / nombre, destino / nombre)
        return True
    except OSError as exc:
        log(f"AVISO: no se pudo guardar la transcripción de {base} en el historial ({exc}).")
        return False


def carga(dir_work=None) -> dict:
    try:
        d = json.loads(ruta(dir_work).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _guarda(d: dict, dir_work=None) -> None:
    try:
        p = ruta(dir_work)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        log(f"AVISO: no se pudo guardar el historial ({exc}).")


def _base(jid: str) -> str:
    from . import versiones
    p = versiones.parse(jid)
    return p[0] if p else jid


def registra_render(jid: str, out_dir, dir_work=None) -> None:
    """Se llama justo al terminar un render PRO de verdad (`pipeline.py`, justo tras escribir
    manifest.json): lee lo que YA se escribió ahí (una lista con 1 entrada) y guarda una copia
    LIGERA + la miniatura en `work/` — así sobrevive aunque borres `videos/<jid>/`. El render de
    prueba de «Probar el sistema» (`zz-…`) NO se apunta: se colaba en el Historial del autor como
    «Prueba uno» (2026-09-24)."""
    if str(jid).startswith("zz-"):
        return
    try:
        out_dir = Path(out_dir)
        entradas = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        it = entradas[-1] if isinstance(entradas, list) and entradas else None
        if not it:
            return
        from . import versiones
        base = _base(jid)
        num = (versiones.parse(jid) or (base, 1))[1]
        d = carga(dir_work)
        fila = d.get(base, {})
        miniatura = fila.get("miniatura")
        origen_jpg = out_dir / str(it.get("miniatura") or "")
        if origen_jpg.is_file():
            carpeta = dir_miniaturas(dir_work)
            carpeta.mkdir(parents=True, exist_ok=True)
            destino = carpeta / f"{base}.jpg"
            try:
                shutil.copyfile(origen_jpg, destino)
                miniatura = destino.name
            except OSError:
                pass
        fila.update(
            base=base, jid=jid, version=num,
            titulo=it.get("titulo") or fila.get("titulo") or base,
            canal=it.get("tema") or fila.get("canal") or "",
            duracion_s=it.get("duracion", fila.get("duracion_s")),
            miniatura=miniatura,
            actualizado=time.time(),
            creado=fila.get("creado") or time.time(),
        )
        d[base] = fila
        _guarda(d, dir_work)
    except Exception as exc:                                   # noqa: BLE001 — el historial es un extra: nunca tumba el render
        log(f"AVISO: no se pudo apuntar en el historial ({exc}).")


def registra_publicado(jid: str, video_id: str, url: str = "", dir_work=None) -> None:
    """Se llama cuando el Publisher confirma que YouTube aceptó el vídeo: a partir de aquí el
    historial ya puede pedir métricas y retención (`uso_metricas.py`) aunque se borre el .mp4."""
    try:
        base = _base(jid)
        d = carga(dir_work)
        fila = d.get(base, {"base": base, "jid": jid, "creado": time.time()})
        if (fila.get("youtube") or {}).get("video_id") not in (None, video_id):
            _olvida_lo_de_youtube(fila, dir_work)              # otro vídeo de YouTube: su estado y miniatura se releen
        fila["youtube"] = {"video_id": video_id, "url": url, "publicado": time.time()}
        d[base] = fila
        _guarda(d, dir_work)
        conserva_habla(base, dir_work)                         # sus muletillas siguen aunque luego lo borres del PC
    except Exception as exc:                                   # noqa: BLE001
        log(f"AVISO: no se pudo apuntar la publicación en el historial ({exc}).")


def video_id_de(base: str, dir_work=None) -> str | None:
    """El video_id de YouTube de esta familia, si se publicó."""
    return ((carga(dir_work).get(base) or {}).get("youtube") or {}).get("video_id") or None


def lista(dir_work=None, outbox_dir=None, ahora: float | None = None) -> list[dict]:
    """Todo el historial, del más reciente al más viejo (por la fecha de YouTube: los programados, arriba), con `local`
    calculado AL VUELO (no guardado: si borras la carpeta después de que esto se escribió, se entera solo) y el estado,
    la fecha y el título tal como los dijo YouTube (`_como_se_ve`).
    `outbox_dir` = `videos/` (por defecto, el de este proyecto; las pruebas pasan uno de mentira)."""
    if outbox_dir is None:
        from . import config
        outbox_dir = config.paths()["outbox"]
    outbox_dir = Path(outbox_dir)
    ahora = time.time() if ahora is None else ahora
    out = []
    for fila in carga(dir_work).values():
        jid = fila.get("jid") or fila.get("base")
        if str(fila.get("base") or jid or "").startswith("zz-"):
            continue                                    # restos del render de prueba de «Probar el sistema»
        miniatura = fila.get("miniatura")
        if fila.get("miniatura_yt"):                    # la que está puesta en YouTube gana a la automática del render
            url_min = f"/historial-miniatura/{fila['miniatura_yt']}?v={int(fila.get('miniatura_yt_leida') or 0)}"
        else:
            url_min = f"/historial-miniatura/{miniatura}" if miniatura else None
        out.append({**fila,
                    "local": bool(jid) and (outbox_dir / jid).is_dir(),
                    "miniatura_url": url_min,
                    "miniatura_de_youtube": bool(fila.get("miniatura_yt")),
                    "miniatura_provisional": bool(fila.get("miniatura_yt") and fila.get("miniatura_yt_provisional")),
                    **_como_se_ve(fila, ahora)})
    out.sort(key=lambda f: -(f.get("fecha_yt") or f.get("actualizado") or f.get("creado") or 0))
    return out


def _como_se_ve(fila: dict, ahora: float) -> dict:
    """Estado, fecha y título como los dijo YouTube la última vez que se le preguntó (`actualiza_desde_youtube`).
    - publicado -> la fecha en que se hizo PÚBLICO; programado -> cuándo sale; privado/oculto -> cuándo se subió.
    - Nunca se pudo preguntar (YouTube sin conectar): estado None y la fecha en que el panel lo SUBIÓ — que NO es la de
      salida: por enseñar esa como «Publicado el…», los programados salían un día antes (el autor, 2026-09-26).
    - Un programado al que ya le llegó su hora y aún no se pudo volver a preguntar se enseña publicado a esa hora (es lo
      que hace YouTube), marcado `estado_supuesto` hasta la próxima lectura."""
    yt = fila.get("yt") or {}
    subido = (fila.get("youtube") or {}).get("publicado")
    estado, fecha, supuesto = yt.get("estado"), None, False
    prog = yt.get("programado_para")
    if estado == "programado" and prog is not None and float(prog) <= ahora:
        estado, fecha, supuesto = "publicado", prog, True
    elif estado == "programado":
        fecha = prog
    elif estado in ("publicado", "privado", "oculto", "no_esta"):
        fecha = yt.get("publicado_en")
    return {"estado_yt": estado, "estado_supuesto": supuesto, "fecha_yt": fecha or subido,
            "titulo_yt": yt.get("titulo") or None}


def elimina(base: str, dir_work=None) -> bool:
    """Quita una entrada del historial. el autor lo pide a mano desde el panel; nunca automático
    (esto NO borra nada de YouTube ni del PC, solo el registro del historial)."""
    d = carga(dir_work)
    fila = d.pop(base, None)
    if fila is None:
        return False
    for m in (fila.get("miniatura"), fila.get("miniatura_yt")):
        if m:
            try:
                (dir_miniaturas(dir_work) / m).unlink(missing_ok=True)
            except OSError:
                pass
    _guarda(d, dir_work)
    return True


# ------------------------------------------------------------------ lo que dice YOUTUBE de cada vídeo subido
# el autor (2026-09-26), tres pedidos del mismo día:
#  · «en el historial se están quedando las miniaturas que yo no estuve utilizando… que jale la miniatura que está en
#    YouTube… solo una… solo que jale una vez» -> la miniatura puesta en YouTube, traída UNA vez por vídeo.
#  · «sale como publicado pero está programado… que salga scheduled… y al darle clic, el día y la hora en que va a salir».
#  · «lo publiqué el 25 y abajo sale publicado el 24… que las fechas estén tomadas 100 % de YouTube»: `youtube.publicado`
#    es cuándo el panel lo SUBIÓ, no cuándo salió (sus 3 primeros estaban programados: salían un día antes).
# Y el 28-sep: «si está programado, también salga la miniatura… la que yo seteé en YouTube», pero «que no me detecte
# constantemente… serían las dos únicas verificaciones»: la miniatura se mira SOLO 15 min después de subirlo y 15 min
# después de que se publique (ESPERA_MIN), y nunca más.
# Así que estado (publicado / programado / privado / oculto / no está), fechas y título se leen de YouTube: una petición
# por cada 50 vídeos (1 unidad de cuota, de 10.000 al día), como mucho cada ESTADO_CADA_MIN mientras miras «Mis vídeos»,
# y enseguida cuando a un programado le llega su hora. Bajar el .jpg de la miniatura (i.ytimg.com) no gasta cuota.
ESTADO_CADA_MIN = 15.0     # estado, fechas y título se releen como mucho cada 15 min (si cambias algo en Studio, se ve)
ESPERA_MIN = 15.0          # las DOS miradas a la miniatura: 15 min después de subirlo y 15 min después de publicarse
REINTENTO_H = 3.0          # si bajar la miniatura falla, se reintenta pasadas 3 h (no en cada visita)
_MIN_BYTES = 2500          # el «no hay miniatura» de YouTube es un gris de 120x90 (~1 KB): eso no se guarda
_CAMPOS_YT = ("yt", "miniatura_yt", "miniatura_yt_de", "miniatura_yt_leida", "miniatura_yt_proximo",
              "miniatura_yt_provisional", "miniatura_yt_tras_subir", "miniatura_yt_tras_publicar")


def _olvida_lo_de_youtube(fila: dict, dir_work=None) -> None:
    """La familia se publicó como OTRO vídeo de YouTube: su estado y su miniatura se vuelven a leer, del nuevo."""
    m = fila.get("miniatura_yt")
    if m:
        try:
            (dir_miniaturas(dir_work) / m).unlink(missing_ok=True)
        except OSError:
            pass
    for k in _CAMPOS_YT:
        fila.pop(k, None)


def _ts(iso) -> float | None:
    """'2026-09-25T23:00:00Z' (lo que da la API) -> segundos epoch, o None."""
    if not iso:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def estado_de(i: dict | None) -> str:
    """Lo que devuelve `youtube.info_videos` de un vídeo -> 'publicado' | 'programado' | 'privado' | 'oculto' |
    'no_esta' (YouTube ya no lo devuelve: se borró, o el YouTube conectado es de otra cuenta)."""
    if not i:
        return "no_esta"
    p = i.get("privacidad")
    if p == "public":
        return "publicado"
    if p == "unlisted":
        return "oculto"
    if p == "private":
        return "programado" if i.get("programado_para") else "privado"
    return "desconocido"


def _toca_leer(fila: dict, ahora: float) -> bool:
    yt = fila.get("yt") or {}
    ultimo = max(float(yt.get("leido") or 0), float(yt.get("intento") or 0))
    if ahora - ultimo >= ESTADO_CADA_MIN * 60:
        return True
    prog = yt.get("programado_para")                   # a un programado le acaba de llegar su hora: ¿ya salió?
    return yt.get("estado") == "programado" and prog is not None and ultimo < float(prog) <= ahora


def pendientes_yt(dir_work=None, ahora: float | None = None) -> list[str]:
    """Las familias subidas a YouTube a las que ya les toca preguntar (cada 15 min, o su hora de salida acaba de pasar)."""
    ahora = time.time() if ahora is None else ahora
    return [base for base, fila in carga(dir_work).items()
            if isinstance(fila, dict) and (fila.get("youtube") or {}).get("video_id")
            and not str(base).startswith("zz-") and _toca_leer(fila, ahora)]


def _baja(url: str, timeout: float = 15.0) -> bytes:
    """Baja la imagen de la miniatura. Solo de los servidores de imágenes de YouTube (la URL la da la API)."""
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen
    u = urlparse(url)
    if u.scheme != "https" or not (u.hostname or "").endswith(".ytimg.com"):
        raise ValueError(f"URL de miniatura inesperada: {url[:80]}")
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout) as r:   # noqa: S310
        return r.read(8 * 1024 * 1024)


def _miniatura_de_youtube(base: str, vid: str, i: dict, fila: dict, ahora: float, baja, dir_work=None) -> dict:
    """La miniatura puesta en YouTube, SOLO en dos momentos (el autor, 2026-09-28: «que no me detecte constantemente… serían
    las dos únicas verificaciones»): ESPERA_MIN después de SUBIRLO (la que pusiste al subir, aunque esté programado) y
    ESPERA_MIN después de que se PUBLIQUE (la definitiva: si la cambiaste en Studio mientras estaba programado, sale
    ahí). Después, nunca más, aunque la cambies o haya un A/B («que jale una vez», 26-sep). Se reescribe solo si cambió
    (y solo entonces cambia su «?v=», para que el navegador pida la nueva). -> campos a guardar."""
    if not i.get("url") or float(fila.get("miniatura_yt_proximo") or 0) > ahora:
        return {}
    if fila.get("miniatura_yt_tras_publicar") or (
            fila.get("miniatura_yt") and not fila.get("miniatura_yt_provisional") and not fila.get("miniatura_yt_tras_subir")):
        return {}                                               # la definitiva ya está (o es de antes de esta regla)
    espera = ESPERA_MIN * 60
    publicado = _ts(i.get("publicado_en")) if estado_de(i) in ("publicado", "oculto") else None
    subido = (fila.get("youtube") or {}).get("publicado")
    if publicado is not None and ahora >= publicado + espera:
        momento = "tras_publicar"
    elif not fila.get("miniatura_yt_tras_subir") and subido and ahora >= float(subido) + espera:
        momento = "tras_subir"
    else:
        return {}
    provisional = momento == "tras_subir"
    try:
        datos = baja(i["url"])
        if datos[:2] != b"\xff\xd8" or len(datos) < _MIN_BYTES:
            raise ValueError(f"no es una miniatura válida ({len(datos)} bytes)")
        carpeta = dir_miniaturas(dir_work)
        carpeta.mkdir(parents=True, exist_ok=True)
        nombre = f"{base}-yt.jpg"
        destino = carpeta / nombre
        marca = {f"miniatura_yt_{momento}": ahora, "miniatura_yt_provisional": provisional}
        if fila.get("miniatura_yt") == nombre and destino.is_file() and destino.read_bytes() == datos:
            return marca                                        # la misma de antes: no se reescribe
        destino.write_bytes(datos)
        return {"miniatura_yt": nombre, "miniatura_yt_de": i["url"], "miniatura_yt_leida": ahora, **marca}
    except Exception as exc:                                    # noqa: BLE001
        log(f"AVISO: no se pudo bajar la miniatura de YouTube de {vid} ({exc}); se vuelve a intentar en {REINTENTO_H:g} h.")
        return {"miniatura_yt_proximo": ahora + REINTENTO_H * 3600}


def actualiza_desde_youtube(dir_work=None, ahora: float | None = None, _info_fn=None, _baja_fn=None) -> dict:
    """Pregunta a YouTube por los vídeos a los que les toca (`pendientes_yt`, todos en una petición) y guarda su estado,
    fechas y título; y, una vez por vídeo, su miniatura. Si YouTube no responde, se queda lo último que se supo y se
    vuelve a intentar en ESTADO_CADA_MIN. Nunca lanza: el historial es un extra. `_info_fn`/`_baja_fn` son solo para las
    pruebas (sin red); en producción, `youtube.info_videos` y `_baja`. -> {"leidos": n, "puestas": m, "error": …}"""
    ahora = time.time() if ahora is None else ahora
    res = {"leidos": 0, "puestas": 0, "error": None}
    try:
        d = carga(dir_work)
        ids = {d[b]["youtube"]["video_id"]: b for b in pendientes_yt(dir_work, ahora)}
        if not ids:
            return res
        if _info_fn is None:
            from . import youtube
            _info_fn = youtube.info_videos
        try:
            info = _info_fn(list(ids)) or {}
        except Exception as exc:                               # noqa: BLE001 — sin conexión, token caducado…
            res["error"] = str(exc)[:200]
            log(f"AVISO: no se pudo preguntar a YouTube por los vídeos del historial ({res['error']}); se vuelve a "
                f"intentar en {ESTADO_CADA_MIN:g} min (mientras, se enseña lo último que se supo).")
            info = None
        cambios: dict[str, dict] = {}
        for vid, base in ids.items():
            if info is None:
                cambios[base] = {"yt": {"intento": ahora}}
                continue
            i = info.get(vid)
            viejo = d[base].get("yt") or {}
            if i:
                yt = {"estado": estado_de(i), "leido": ahora, "publicado_en": _ts(i.get("publicado_en")),
                      "programado_para": _ts(i.get("programado_para")), "titulo": i.get("titulo") or None}
                c = _miniatura_de_youtube(base, vid, i, d[base], ahora, _baja_fn or _baja, dir_work)
            else:                                               # ya no está: se guarda el último título y fecha que se supo
                yt = {"estado": "no_esta", "leido": ahora, "titulo": viejo.get("titulo"),
                      "publicado_en": viejo.get("publicado_en")}
                c = {}
            cambios[base] = {"yt": yt, **c}
            res["leidos"] += 1
            res["puestas"] += "miniatura_yt" in c
        # se relee JUSTO antes de guardar y solo se tocan estos campos: si otro proceso apuntó un render o una publicación
        # mientras se hablaba con YouTube, no se pisa
        d = carga(dir_work)
        for base, c in cambios.items():
            fila = d.get(base)
            if not isinstance(fila, dict) or ids.get((fila.get("youtube") or {}).get("video_id")) != base:
                continue                                        # se borró, o ya es otro vídeo de YouTube
            c = dict(c)
            yt = c.pop("yt")
            fila["yt"] = {**(fila.get("yt") or {}), **yt} if "intento" in yt else yt
            fila.update(c)
            if "miniatura_yt" in c:
                fila.pop("miniatura_yt_proximo", None)
        _guarda(d, dir_work)
    except Exception as exc:                                   # noqa: BLE001
        res["error"] = str(exc)[:200]
        log(f"AVISO: no se pudo actualizar el historial con lo de YouTube ({exc}).")
    return res


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    with tempfile.TemporaryDirectory() as tmp:
        salida = Path(tmp) / "salida"                                    # hace de `videos/` (el outbox real) en esta prueba
        out_dir = salida / "2026-01-01-x--pro"
        out_dir.mkdir(parents=True)
        (out_dir / "pro.jpg").write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")
        (out_dir / "manifest.json").write_text(json.dumps([
            {"archivo": "pro.mp4", "miniatura": "pro.jpg", "titulo": "Cómo hacer X",
             "tema": "ejemplo", "duracion": 812.3}]), encoding="utf-8")
        registra_render("2026-01-01-x--pro", out_dir, dir_work=tmp)
        d = carga(tmp)
        caso("un render PRO deja una fila ligera en el historial (título, canal, duración) y copia la miniatura aparte",
             d.get("2026-01-01-x", {}).get("titulo") == "Cómo hacer X" and d["2026-01-01-x"]["duracion_s"] == 812.3
             and (Path(tmp) / "historial_miniaturas" / "2026-01-01-x.jpg").is_file(), d.get("2026-01-01-x"))

        trabajo_x = Path(tmp) / "2026-01-01-x--pro"                        # su carpeta de trabajo (work/<base>--pro)
        trabajo_x.mkdir(exist_ok=True)
        (trabajo_x / "words.json").write_text('[{"word": "bueno", "start": 0.0, "end": 0.3}]', encoding="utf-8")
        (trabajo_x / "pausas_duda.json").write_text('{"n": 2, "seg": 1.4, "firma": "x"}', encoding="utf-8")
        (trabajo_x / "audio.wav").write_bytes(b"RIFF" + b"a" * 100)
        registra_publicado("2026-01-01-x--pro", "abc123XYZ", "https://youtu.be/abc123XYZ", dir_work=tmp)
        caso("al publicar se guarda el video_id, sin perder lo que ya había",
             video_id_de("2026-01-01-x", tmp) == "abc123XYZ" and carga(tmp)["2026-01-01-x"]["titulo"] == "Cómo hacer X")
        guardada = dir_transcripciones(tmp) / "2026-01-01-x"
        caso("al publicar, su transcripción y sus pausas medidas se guardan aparte (sus muletillas siguen aunque lo borres "
             "del PC: el autor, 2026-09-28), nunca el audio",
             (guardada / "words.json").read_bytes() == (trabajo_x / "words.json").read_bytes()
             and (guardada / "pausas_duda.json").is_file() and not (guardada / "audio.wav").exists())
        caso("sin transcripción no se guarda nada (y no revienta)",
             conserva_habla("no-existe", tmp) is False and not (dir_transcripciones(tmp) / "no-existe").exists())

        out_dir2 = salida / "2026-01-01-x--pro-v2"                       # una mejora (v2): misma familia
        out_dir2.mkdir()
        (out_dir2 / "pro.jpg").write_bytes(b"\xff\xd8\xff\xe0OTRO")
        (out_dir2 / "manifest.json").write_text(json.dumps([
            {"archivo": "pro.mp4", "miniatura": "pro.jpg", "titulo": "Cómo hacer X (mejorado)",
             "tema": "ejemplo", "duracion": 799.0}]), encoding="utf-8")
        registra_render("2026-01-01-x--pro-v2", out_dir2, dir_work=tmp)
        caso("una mejora (v2) ACTUALIZA la misma familia (no crea una fila aparte) y conserva el video_id ya publicado",
             len(carga(tmp)) == 1 and carga(tmp)["2026-01-01-x"]["titulo"] == "Cómo hacer X (mejorado)"
             and carga(tmp)["2026-01-01-x"]["version"] == 2 and video_id_de("2026-01-01-x", tmp) == "abc123XYZ")

        filas_l = lista(tmp, outbox_dir=salida)
        caso("`lista` calcula `local` mirando si la carpeta de salida de la versión sigue en disco",
             filas_l[0]["local"] is True and filas_l[0]["miniatura_url"] == "/historial-miniatura/2026-01-01-x.jpg")

        shutil.rmtree(out_dir2)
        caso("si se borra la carpeta de esa versión, `local` pasa a False pero la fila NO desaparece (el pedido del autor)",
             lista(tmp, outbox_dir=salida)[0]["local"] is False and lista(tmp, outbox_dir=salida)[0]["titulo"] == "Cómo hacer X (mejorado)")

        antes = carga(tmp)
        registra_render("no-existe--pro", salida / "no-existe", dir_work=tmp)
        caso("un manifest.json ausente o roto no revienta nada ni añade una fila falsa (el render ya terminó bien; esto es un extra)",
             carga(tmp) == antes)

        caso("borrar del historial (a mano) quita la fila y su miniatura, sin tocar YouTube ni el PC",
             elimina("2026-01-01-x", tmp) and carga(tmp) == {}
             and not (Path(tmp) / "historial_miniaturas" / "2026-01-01-x.jpg").is_file())

        out_z = salida / "zz-selftest-abc--pro"
        out_z.mkdir()
        (out_z / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "titulo": "Prueba uno"}]),
                                             encoding="utf-8")
        registra_render("zz-selftest-abc--pro", out_z, dir_work=tmp)
        caso("el render de prueba de «Probar el sistema» (zz-…) no se apunta en el historial del autor",
             carga(tmp) == {} and lista(tmp, outbox_dir=salida) == [])

    # --- lo que dice YOUTUBE (el autor, 2026-09-26): estado, fechas y título de YouTube; la miniatura, una sola vez
    from datetime import datetime, timezone

    def iso(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    caso("el estado sale de lo que dice YouTube: público, programado (privado con hora de salida), privado, oculto, "
         "o ya no está",
         [estado_de(x) for x in ({"privacidad": "public"}, {"privacidad": "private", "programado_para": "2026-09-26T16:00:00Z"},
                                 {"privacidad": "private"}, {"privacidad": "unlisted"}, None)]
         == ["publicado", "programado", "privado", "oculto", "no_esta"])

    with tempfile.TemporaryDirectory() as tmp:
        t0 = 1_800_000_000.0
        h = 3600.0
        jpg = b"\xff\xd8\xff\xe0" + b"J" * 5000
        pedidas: list = []
        bajadas: list = []
        estado: dict = {}

        def info_fake(ids):
            pedidas.append(sorted(ids))
            return {v: dict(estado[v]) for v in ids if v in estado}

        def baja_fake(url):
            bajadas.append(url)
            return jpg

        def lee(ahora, info=info_fake, baja=baja_fake):
            return actualiza_desde_youtube(tmp, ahora=ahora, _info_fn=info, _baja_fn=baja)

        def ver(ahora):
            return {f["base"]: f for f in lista(tmp, outbox_dir=tmp, ahora=ahora)}

        for b, vid in (("2026-02-01-a", "vidA"), ("2026-02-02-b", "vidB"), ("2026-02-03-c", "vidC")):
            registra_publicado(b + "--pro", vid, "", dir_work=tmp)
        dir_miniaturas(tmp).mkdir(parents=True, exist_ok=True)
        (dir_miniaturas(tmp) / "2026-02-01-a.jpg").write_bytes(b"AUTOMATICA")
        d = carga(tmp)
        d["2026-02-01-a"]["miniatura"] = "2026-02-01-a.jpg"
        _guarda(d, tmp)
        yt_url = "https://i.ytimg.com/vi/{}/maxresdefault.jpg".format
        estado["vidA"] = {"url": yt_url("vidA"), "privacidad": "public", "publicado_en": iso(t0 - 24 * h),
                          "titulo": "El título que puse en YouTube"}
        estado["vidB"] = {"url": yt_url("vidB"), "privacidad": "private", "publicado_en": iso(t0 - 2 * h),
                          "programado_para": iso(t0 + 5 * h), "titulo": "B"}
        estado["vidC"] = {"url": yt_url("vidC"), "privacidad": "public", "publicado_en": iso(t0 - 600), "titulo": "C"}

        r1 = lee(t0)
        v1 = ver(t0)
        a1, b1, c1 = v1["2026-02-01-a"], v1["2026-02-02-b"], v1["2026-02-03-c"]
        caso("una sola petición para todos: el publicado sale PUBLICADO con la fecha en que se hizo público y el título de "
             "YouTube (no la fecha en que el panel lo subió, que salía un día antes en los programados)",
             pedidas == [["vidA", "vidB", "vidC"]] and r1["leidos"] == 3 and a1["estado_yt"] == "publicado"
             and a1["fecha_yt"] == t0 - 24 * h and a1["titulo_yt"] == "El título que puse en YouTube"
             and a1["fecha_yt"] != a1["youtube"]["publicado"], (r1, pedidas, a1.get("estado_yt"), a1.get("fecha_yt")))
        caso("el programado sale PROGRAMADO, con el día y la hora en que sale; y arriba del todo (orden por fecha de YouTube)",
             b1["estado_yt"] == "programado" and b1["fecha_yt"] == t0 + 5 * h and not b1["estado_supuesto"]
             and [f["base"] for f in lista(tmp, outbox_dir=tmp, ahora=t0)] == ["2026-02-02-b", "2026-02-03-c", "2026-02-01-a"],
             (b1.get("estado_yt"), b1.get("fecha_yt")))
        caso("miniatura (15 min después de SUBIRLO): la del publicado se trae de YouTube y gana a la automática del "
             "render; la del PROGRAMADO también (la que pusiste al subir); la del recién publicado, la de la subida",
             r1["puestas"] == 3 and bajadas == [yt_url("vidA"), yt_url("vidB"), yt_url("vidC")]
             and (dir_miniaturas(tmp) / "2026-02-01-a-yt.jpg").read_bytes() == jpg
             and str(a1["miniatura_url"]).startswith("/historial-miniatura/2026-02-01-a-yt.jpg?v=") and a1["miniatura_de_youtube"]
             and not a1["miniatura_provisional"] and b1["miniatura_de_youtube"] and b1["miniatura_provisional"]
             and c1["miniatura_provisional"], (r1, bajadas, a1.get("miniatura_url"), b1.get("miniatura_url")))

        lee(t0 + 60)
        caso("abrir «Mis vídeos» otra vez al minuto no vuelve a preguntar a YouTube (como mucho, cada 15 min)",
             len(pedidas) == 1, pedidas)
        url_b = ver(t0)["2026-02-02-b"]["miniatura_url"]
        lee(t0 + 20 * 60)
        lee(t0 + 65 * 60)
        caso("pasados 15 min vuelve a preguntar por el estado, pero la miniatura SOLO dos veces (el autor, 28-sep): la del "
             "programado no se vuelve a bajar en cada lectura; la del recién publicado, otra vez 15 min después de "
             "publicarse (la definitiva); la de un publicado ya definitiva, nunca más",
             len(pedidas) == 3 and bajadas == [yt_url("vidA"), yt_url("vidB"), yt_url("vidC"), yt_url("vidC")]
             and ver(t0 + 65 * 60)["2026-02-02-b"]["miniatura_url"] == url_b
             and not ver(t0 + 65 * 60)["2026-02-03-c"]["miniatura_provisional"], (pedidas, bajadas))
        otra_jpg = b"\xff\xd8\xff\xe0" + b"N" * 6000

        def baja_nueva(url):
            bajadas.append(url)
            return otra_jpg if "vidB" in url else jpg

        n_bajadas = len(bajadas)
        lee(t0 + 85 * 60, baja=baja_nueva)
        caso("mientras sigue programado, no se vuelve a mirar su miniatura aunque la cambies en Studio (sale al publicarse)",
             len(bajadas) == n_bajadas and (dir_miniaturas(tmp) / "2026-02-02-b-yt.jpg").read_bytes() == jpg,
             bajadas[n_bajadas:])

        lee(t0 + 5 * h - 300)                                   # 5 min antes de que salga: sigue programado
        v_sup = ver(t0 + 5 * h + 30)["2026-02-02-b"]
        caso("si le llega su hora y aún no se pudo volver a preguntar, se enseña publicado a esa hora (lo que hace YouTube), "
             "marcado como supuesto",
             v_sup["estado_yt"] == "publicado" and v_sup["estado_supuesto"] and v_sup["fecha_yt"] == t0 + 5 * h, v_sup)
        estado["vidB"] = {**estado["vidB"], "privacidad": "public", "publicado_en": iso(t0 + 5 * h), "programado_para": None}
        n_antes = len(pedidas)
        lee(t0 + 5 * h + 60)                                    # solo 6 min después de la última lectura
        b2 = ver(t0 + 5 * h + 60)["2026-02-02-b"]
        caso("en cuanto a un programado le llega su hora, se pregunta ya (sin esperar los 15 min) y pasa a PUBLICADO de verdad",
             len(pedidas) == n_antes + 1 and "vidB" in pedidas[-1] and b2["estado_yt"] == "publicado"
             and not b2["estado_supuesto"] and b2["fecha_yt"] == t0 + 5 * h, (pedidas[-1:], b2.get("estado_yt")))
        n_bajadas = len(bajadas)
        lee(t0 + 5 * h + 17 * 60, baja=baja_nueva)
        b_pub = ver(t0 + 5 * h + 17 * 60)["2026-02-02-b"]
        caso("15 min después de publicarse se mira por segunda y última vez: sale la que pusiste en Studio (con otra "
             "dirección, para que el navegador no enseñe la vieja) y ya queda como la definitiva",
             len(bajadas) == n_bajadas + 1 and bajadas[-1] == yt_url("vidB")
             and (dir_miniaturas(tmp) / "2026-02-02-b-yt.jpg").read_bytes() == otra_jpg and b_pub["miniatura_url"] != url_b
             and not b_pub["miniatura_provisional"], bajadas[n_bajadas - 1:])
        lee(t0 + 6 * h + 300)
        n_bajadas = len(bajadas)

        for v in estado.values():
            v["url"] = str(v["url"]) + "?otra"
        estado["vidA"]["titulo"] = "Título cambiado en Studio"
        lee(t0 + 30 * 24 * h)
        caso("el estado y el título se siguen leyendo (si cambias algo en Studio, se ve), pero la miniatura de un vídeo ya "
             "PÚBLICO no se vuelve a pedir nunca, aunque la cambies o haya un A/B: «que jale una vez»",
             len(bajadas) == n_bajadas and ver(t0 + 30 * 24 * h)["2026-02-01-a"]["titulo_yt"] == "Título cambiado en Studio",
             bajadas[n_bajadas:])

        d_vieja = carga(tmp)
        d_vieja["2026-02-06-f"] = {"base": "2026-02-06-f", "jid": "2026-02-06-f--pro", "miniatura_yt": "2026-02-06-f-yt.jpg",
                                   "youtube": {"video_id": "vidF", "url": "", "publicado": t0}}
        _guarda(d_vieja, tmp)
        estado["vidF"] = {"url": yt_url("vidF"), "privacidad": "public", "publicado_en": iso(t0), "titulo": "F"}
        n_bajadas = len(bajadas)
        lee(t0 + 31 * 24 * h)
        caso("un vídeo de antes de esta regla con su miniatura ya traída no se vuelve a bajar",
             len(bajadas) == n_bajadas, bajadas[n_bajadas:])
        registra_publicado("2026-02-04-d--pro", "vidD", "", dir_work=tmp)
        caidas: list = []

        def info_caida(ids):
            caidas.append(sorted(ids))
            raise RuntimeError("caída de mentira de esta prueba")

        r5 = lee(t0 + 40 * 24 * h, info=info_caida)
        v5 = ver(t0 + 40 * 24 * h)
        n_pedidas = len(pedidas)
        lee(t0 + 40 * 24 * h + 600)
        caso("si YouTube falla (sin conexión, permiso caducado): no revienta, se queda lo último que se supo (y lo nunca "
             "leído dice «subido», con la fecha en que el panel lo subió) y no insiste en cada visita",
             r5["error"] and len(caidas) == 1 and len(pedidas) == n_pedidas and v5["2026-02-01-a"]["estado_yt"] == "publicado"
             and v5["2026-02-04-d"]["estado_yt"] is None
             and v5["2026-02-04-d"]["fecha_yt"] == carga(tmp)["2026-02-04-d"]["youtube"]["publicado"], (r5, caidas))
        lee(t0 + 40 * 24 * h + 16 * 60)
        caso("un vídeo que YouTube ya no devuelve (lo borraste de YouTube) sale como «no está»",
             ver(t0 + 40 * 24 * h + 16 * 60)["2026-02-04-d"]["estado_yt"] == "no_esta")

        estado["vidD"] = {"url": yt_url("vidD"), "privacidad": "public", "publicado_en": iso(t0), "titulo": "D"}
        grises: list = []

        def gris(url):
            grises.append(url)
            return b"\xff\xd8" + b"g" * 900

        lee(t0 + 41 * 24 * h, baja=gris)
        lee(t0 + 41 * 24 * h + 20 * 60, baja=gris)
        caso("el «sin miniatura» gris de YouTube (una imagen de ~1 KB) no se guarda, y no se reintenta hasta pasadas 3 h",
             len(grises) == 1 and not carga(tmp)["2026-02-04-d"].get("miniatura_yt"), grises)

        def info_con_carrera(ids):
            registra_publicado("2026-02-05-e--pro", "vidE", "", dir_work=tmp)    # otro proceso publica mientras tanto
            return info_fake(ids)

        lee(t0 + 41 * 24 * h + 4 * h, info=info_con_carrera)
        caso("si otro proceso apunta una publicación mientras se habla con YouTube, no se pierde",
             carga(tmp).get("2026-02-05-e", {}).get("youtube", {}).get("video_id") == "vidE"
             and carga(tmp)["2026-02-04-d"].get("miniatura_yt") == "2026-02-04-d-yt.jpg")

        registra_publicado("2026-02-01-a--pro-v2", "vidA2", "", dir_work=tmp)
        a3 = ver(t0 + 42 * 24 * h)["2026-02-01-a"]
        caso("si la familia se publica como OTRO vídeo de YouTube, se olvida lo del viejo (estado y miniatura) y se relee",
             "yt" not in carga(tmp)["2026-02-01-a"] and not (dir_miniaturas(tmp) / "2026-02-01-a-yt.jpg").exists()
             and "2026-02-01-a" in pendientes_yt(tmp, ahora=t0 + 42 * 24 * h) and a3["estado_yt"] is None
             and a3["miniatura_url"] == "/historial-miniatura/2026-02-01-a.jpg", a3)
        elimina("2026-02-02-b", tmp)
        caso("quitar una fila del Historial también borra su miniatura de YouTube",
             not (dir_miniaturas(tmp) / "2026-02-02-b-yt.jpg").exists())

    malas = []
    for url in ("https://ejemplo.com/x.jpg", "http://i.ytimg.com/vi/x/hqdefault.jpg", "file:///C:/x.jpg"):
        try:
            _baja(url)
        except ValueError:
            malas.append(url)
    caso("solo se bajan imágenes de los servidores de YouTube por https (nada de otras direcciones)", len(malas) == 3, malas)
    return filas
