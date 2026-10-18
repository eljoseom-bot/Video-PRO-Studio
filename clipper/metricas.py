"""Métricas y retención de YouTube por vídeo — la tarjeta «Cómo le fue» (pedido del autor,
2026-09-22): vistas, likes, % medio visto y la CURVA de retención (en qué segundo se va la
audiencia), cruzada con tu vídeo.

Con caché de `CADA_H` horas: los datos de retención de YouTube tardan 1-2 días en actualizarse
(está documentado en `clipper/youtube.py`), así que pedirlos en cada visita a la página no
tendría sentido y solo gastaría cuota de la API sin necesidad. El botón «Actualizar» del panel
fuerza una lectura nueva cuando de verdad hace falta.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .util import log

CADA_H = 6.0
# lo que se compara entre un ↻ y el anterior (y lo que enseña el Historial)
_FOTO = ("vistas", "likes", "comentarios", "ret_pct", "minutos", "suscriptores")


def _dir_work(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work)
    from . import config
    return config.paths()["work"]


def _ruta(base: str, dir_work=None) -> Path:
    return _dir_work(dir_work) / "metricas_cache" / f"{base}.json"


def lee_cache(base: str, dir_work=None) -> dict | None:
    try:
        d = json.loads(_ruta(base, dir_work).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _guarda_cache(base: str, d: dict, dir_work=None) -> None:
    try:
        p = _ruta(base, dir_work)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log(f"AVISO: no se pudo guardar la caché de métricas de {base} ({exc}).")


def _foto(d: dict) -> dict:
    return {**{k: d.get(k) for k in _FOTO}, "leido_en": d.get("leido_en")}


def resumen(base: str, dir_work=None) -> dict | None:
    """Los 6 números del Historial (vistas, me gusta, comentarios, retención media, suscriptores y tiempo visto) tal
    como se leyeron la última vez, SIN preguntar a YouTube (el autor, 2026-09-28: «que no sea algo automático… puede cargar
    el sistema»: solo el botón ↻ del Historial los actualiza), y `anterior`: los del ↻ de antes, para enseñar cuánto
    cambió cada uno. None si nunca se leyeron."""
    c = lee_cache(base, dir_work)
    if not c or not c.get("ok"):
        return None
    return {**{k: c.get(k) for k in (*_FOTO, "ret_seg", "leido_en")}, "anterior": c.get("anterior")}


_SUMAS = ("vistas", "likes", "comentarios", "minutos", "suscriptores")


def _ret_media(res: list[dict]) -> float | None:
    """La retención media de VARIOS vídeos = la media ponderada por sus vistas (un vídeo con 10 vistas no pesa como uno con
    10.000). Los que no traen retención no cuentan; sin vistas en ninguno, None (no hay audiencia que promediar)."""
    peso = sum(float(m.get("vistas") or 0) for m in res if m.get("ret_pct") is not None)
    if peso <= 0:
        return None
    return sum(float(m["ret_pct"]) * float(m.get("vistas") or 0) for m in res if m.get("ret_pct") is not None) / peso


def totales(resumenes: list) -> dict:
    """La SUMA de todos los vídeos del Historial para los 6 cuadros grandes de arriba (el autor, 2026-10-11: «una suma total de
    todos los videos… visitas, me gustas, comentarios, retención, suscriptores y tiempo visto… y con la misma lógica de que se
    compara con antes»). `resumenes` = lo que da `resumen()` por vídeo (None si nunca se leyó).
    - vistas, me gusta, comentarios, suscriptores y minutos: suma; retención: media ponderada por vistas (`_ret_media`).
    - `cambio`: cuánto cambió cada número desde el ↻ anterior, SOLO con los vídeos que tienen esa comparación (un vídeo que
      se añadió después no tiene «antes»: contarlo entero como subida inflaría el cambio); `comparado_con` = la fecha del
      ↻ anterior más reciente, `leido_en` = la de la lectura más reciente."""
    con = [m for m in resumenes if m]
    out: dict = {"videos": len(resumenes), "con_datos": len(con), "leido_en": max([m.get("leido_en") or 0 for m in con] or [0]) or None,
                 "comparado_con": None, "cambio": None}
    for k in _SUMAS:
        out[k] = sum(float(m.get(k) or 0) for m in con) if con else None
    out["ret_pct"] = _ret_media(con) if con else None
    comp = [m for m in con if m.get("anterior")]
    if comp:
        ant = [m["anterior"] for m in comp]
        out["comparado_con"] = max([a.get("leido_en") or 0 for a in ant] or [0]) or None
        cambio = {k: sum(float(m.get(k) or 0) for m in comp) - sum(float(a.get(k) or 0) for a in ant) for k in _SUMAS}
        ra, rb = _ret_media(comp), _ret_media(ant)
        cambio["ret_pct"] = (ra - rb) if ra is not None and rb is not None else None
        out["cambio"] = cambio
    return out


def refresca_boton(base: str, video_id: str, duracion_s=None, dir_work=None, **fakes) -> dict:
    """El ↻ del Historial para UN vídeo: lee de YouTube y guarda con qué se compara (el autor, 2026-09-28: «si yo hago hoy
    una, mañana hago otra, pues que se compare con la de hoy… y cuando en 15 días haga otra, se compare con el
    anterior»). `anterior` = los números del ↻ de antes (o, el primer día, lo último que había guardado); `ref_boton` =
    los de este ↻, para el siguiente. Solo esas dos fotos, nunca un historial. Si YouTube falla, no se mueve nada."""
    c0 = lee_cache(base, dir_work) or {}
    previo = c0.get("ref_boton") or (_foto(c0) if c0.get("ok") else None)
    d = obten(base, video_id, duracion_s, forzar=True, dir_work=dir_work, **fakes)
    if d.get("ok") and d.get("leido_en") != c0.get("leido_en"):
        d["anterior"] = previo
        d["ref_boton"] = _foto(d)
        _guarda_cache(base, d, dir_work)
    return d


def obten(base: str, video_id: str, duracion_s=None, ahora: float | None = None, forzar: bool = False,
          dir_work=None, _stats_fn=None, _ret_fn=None, _curva_fn=None) -> dict:
    """Vistas/likes/comentarios + retención media + la curva, con caché de `CADA_H` horas (o
    `forzar=True` para el botón «Actualizar»). `_stats_fn`/`_ret_fn`/`_curva_fn` son solo para
    las pruebas (sin red); en producción son `youtube.video_stats`/`video_retention`/`video_retention_curva`."""
    ahora = ahora if ahora is not None else time.time()
    cache = lee_cache(base, dir_work)
    if cache and cache.get("ok") and not forzar and ahora - float(cache.get("leido_en") or 0) < CADA_H * 3600:
        return cache
    from . import youtube
    stats_fn, ret_fn, curva_fn = _stats_fn or youtube.video_stats, _ret_fn or youtube.video_retention, _curva_fn or youtube.video_retention_curva
    try:
        stats = (stats_fn([video_id]) or {}).get(video_id, {})
        ret = (ret_fn([video_id]) or {}).get(video_id, {})
        curva = curva_fn(video_id) or []
    except Exception as exc:                                  # noqa: BLE001 — si Google falla, mejor la última caché buena que romper la página
        log(f"AVISO: no se pudieron leer las métricas de {video_id} ({exc}).")
        return cache or {"ok": False, "error": str(exc)}
    d = {"ok": True, "video_id": video_id, "leido_en": ahora,
         "vistas": stats.get("views"), "likes": stats.get("likes"), "comentarios": stats.get("comments"),
         "ret_pct": ret.get("ret_pct"), "ret_seg": ret.get("ret_seg"),
         "minutos": ret.get("minutos"), "suscriptores": ret.get("suscriptores"),
         "duracion_s": duracion_s if duracion_s is not None else (cache or {}).get("duracion_s"), "curva": curva}
    for k in ("anterior", "ref_boton"):              # las fotos del ↻ del Historial: una lectura de otro sitio no las mueve
        if k in (cache or {}):
            d[k] = cache[k]
    _guarda_cache(base, d, dir_work)
    return d


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    llamadas = {"n": 0}

    def stats_fake(ids):
        llamadas["n"] += 1
        return {ids[0]: {"views": 1000, "likes": 80, "comments": 5}}

    def ret_fake(ids):
        return {ids[0]: {"ret_pct": 55.0, "ret_seg": 300.0}}

    def curva_fake(vid):
        return [{"ratio": 0.0, "audiencia": 100.0}, {"ratio": 0.5, "audiencia": 60.0}, {"ratio": 1.0, "audiencia": 20.0}]

    def falla(ids):
        raise RuntimeError("sin red")

    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.time()
        r = obten("base1", "vid1", duracion_s=600, ahora=t0, dir_work=tmp, _stats_fn=stats_fake, _ret_fn=ret_fake, _curva_fn=curva_fake)
        caso("junta vistas, retención media y la curva de un vídeo, y las guarda en caché",
             r["ok"] and r["vistas"] == 1000 and r["ret_pct"] == 55.0 and len(r["curva"]) == 3 and llamadas["n"] == 1, r)
        r2 = obten("base1", "vid1", ahora=t0 + 3600, dir_work=tmp, _stats_fn=stats_fake, _ret_fn=ret_fake, _curva_fn=curva_fake)
        caso("dentro de las 6 h no vuelve a pedir nada (la retención de YouTube tarda días en cambiar)",
             llamadas["n"] == 1 and r2["vistas"] == 1000 and r2["duracion_s"] == 600)
        obten("base1", "vid1", ahora=t0 + 7 * 3600, dir_work=tmp, _stats_fn=stats_fake, _ret_fn=ret_fake, _curva_fn=curva_fake)
        caso("pasadas 6 h vuelve a pedir (por si ya hay datos nuevos)", llamadas["n"] == 2)
        obten("base1", "vid1", ahora=t0 + 7 * 3600, dir_work=tmp, forzar=True, _stats_fn=stats_fake, _ret_fn=ret_fake, _curva_fn=curva_fake)
        caso("«forzar» pide aunque la caché esté fresca (el botón «Actualizar»)", llamadas["n"] == 3)
        r5 = obten("base1", "vid1", ahora=t0 + 20 * 3600, dir_work=tmp, forzar=True, _stats_fn=falla, _ret_fn=ret_fake, _curva_fn=curva_fake)
        caso("si Google falla, se sigue con la última caché buena en vez de romper la página", r5["ok"] and r5["vistas"] == 1000)
        r6 = obten("nueva", "vidX", ahora=t0, dir_work=tmp, _stats_fn=falla, _ret_fn=ret_fake, _curva_fn=curva_fake)
        caso("si falla y NO hay caché previa, avisa con ok=False en vez de reventar", r6["ok"] is False)
        res = resumen("base1", tmp)
        caso("el resumen del Historial sale de la caché, sin pedir nada a YouTube",
             res and res["vistas"] == 1000 and res["ret_pct"] == 55.0 and res["leido_en"] == t0 + 7 * 3600
             and res["anterior"] is None and llamadas["n"] == 3 and resumen("nunca", tmp) is None, res)

        # ↻ del Historial: el cambio desde el ↻ anterior (el autor, 2026-09-28)
        numeros = {"views": 33, "likes": 2, "comments": 0, "ret_pct": 25.0, "minutos": 190, "subs": 1}

        def stats_v(ids):
            return {ids[0]: {"views": numeros["views"], "likes": numeros["likes"], "comments": numeros["comments"]}}

        def ret_v(ids):
            return {ids[0]: {"ret_pct": numeros["ret_pct"], "ret_seg": 300.0, "minutos": numeros["minutos"],
                             "suscriptores": numeros["subs"]}}

        falsos = {"_stats_fn": stats_v, "_ret_fn": ret_v, "_curva_fn": curva_fake}
        obten("b2", "v2", dir_work=tmp, ahora=t0, **falsos)                       # lo que ya había (p. ej. de las 12:06)
        numeros.update(views=35, likes=4, ret_pct=27.0, minutos=250, subs=2)
        refresca_boton("b2", "v2", dir_work=tmp, ahora=t0 + 3600, **falsos)      # 1.er ↻
        r1 = resumen("b2", tmp)
        caso("el 1.er ↻ se compara con lo que ya había guardado: +2 vistas, +2 me gusta, +2 % de retención…",
             r1["vistas"] == 35 and r1["anterior"]["vistas"] == 33 and r1["anterior"]["likes"] == 2
             and r1["anterior"]["ret_pct"] == 25.0 and r1["suscriptores"] == 2 and r1["minutos"] == 250, r1)
        numeros.update(views=36)
        obten("b2", "v2", dir_work=tmp, ahora=t0 + 8 * 3600, **falsos)            # una lectura de otro sitio (6 h)
        numeros.update(views=40)
        refresca_boton("b2", "v2", dir_work=tmp, ahora=t0 + 86400, **falsos)     # 2.º ↻, al día siguiente
        r2 = resumen("b2", tmp)
        caso("el 2.º ↻ se compara con el 1.º (no con una lectura de otro sitio entre medias), y solo se guardan esas dos",
             r2["vistas"] == 40 and r2["anterior"]["vistas"] == 35 and set(lee_cache("b2", tmp)) >= {"anterior", "ref_boton"}, r2)
        refresca_boton("b2", "v2", dir_work=tmp, ahora=t0 + 2 * 86400, _stats_fn=falla, _ret_fn=ret_v, _curva_fn=curva_fake)
        caso("si YouTube falla en un ↻, no se mueve nada (la comparación sigue siendo la de antes)",
             resumen("b2", tmp)["anterior"]["vistas"] == 35 and resumen("b2", tmp)["vistas"] == 40)

        from .youtube import filas_retencion
        fr = filas_retencion({"columnHeaders": [{"name": n} for n in ("video", "views", "averageViewDuration",
                                                                     "averageViewPercentage", "estimatedMinutesWatched",
                                                                     "subscribersGained")],
                              "rows": [["vid1", 33, 300, 25.5, 190, 1]]})
        vieja = filas_retencion({"columnHeaders": [{"name": n} for n in ("video", "views", "averageViewPercentage")],
                                 "rows": [["vid1", 33, 25.5]]})
        caso("YouTube Analytics: tiempo visto y suscriptores salen de la misma consulta; sin ellos, None (no 0)",
             fr["vid1"]["minutos"] == 190 and fr["vid1"]["suscriptores"] == 1 and fr["vid1"]["ret_pct"] == 25.5
             and vieja["vid1"]["minutos"] is None and vieja["vid1"]["suscriptores"] is None, (fr, vieja))

    # --- la suma de todos los vídeos (el autor, 2026-10-11)
    def m(v, l, c, r, mi, s, ant=None, leido=1000.0):
        return {"vistas": v, "likes": l, "comentarios": c, "ret_pct": r, "minutos": mi, "suscriptores": s, "leido_en": leido, "anterior": ant}

    a1 = {"vistas": 90, "likes": 8, "comentarios": 1, "ret_pct": 40.0, "minutos": 100, "suscriptores": 1, "leido_en": 500.0}
    a2 = {"vistas": 8, "likes": 1, "comentarios": 0, "ret_pct": 10.0, "minutos": 10, "suscriptores": 0, "leido_en": 480.0}
    v1 = m(100, 10, 2, 40.0, 120, 2, a1, 1000.0)
    v2 = m(10, 2, 0, 10.0, 12, 0, a2, 990.0)
    t = totales([v1, v2, None])
    caso("suma vistas, me gusta, comentarios, suscriptores y tiempo visto de todos los vídeos (un vídeo sin lectura no suma ni rompe)",
         t["vistas"] == 110 and t["likes"] == 12 and t["comentarios"] == 2 and t["suscriptores"] == 2 and t["minutos"] == 132
         and t["videos"] == 3 and t["con_datos"] == 2, t)
    caso("la retención total es la media PONDERADA por vistas (37,3 %, no el 25 % de promediar a ciegas)",
         abs(t["ret_pct"] - 37.272727) < 1e-5, t["ret_pct"])
    caso("el cambio es total ahora menos total del ↻ anterior (+12 vistas, +3 me gusta…) y la retención en puntos",
         t["cambio"]["vistas"] == 12 and t["cambio"]["likes"] == 3 and t["cambio"]["minutos"] == 22
         and abs(t["cambio"]["ret_pct"] - (37.2727 - 37.5510)) < 1e-3, t["cambio"])
    caso("dice cuándo se leyó (la lectura más reciente) y con qué ↻ se compara (el anterior más reciente)",
         t["leido_en"] == 1000.0 and t["comparado_con"] == 500.0, (t["leido_en"], t["comparado_con"]))
    nuevo = m(50, 5, 1, 30.0, 40, 1, None, 1000.0)                      # un vídeo añadido después: sin «antes»
    t2 = totales([v1, v2, nuevo])
    caso("un vídeo sin ↻ anterior suma al total pero NO infla el cambio (si no, parecería que ganó todo de golpe)",
         t2["vistas"] == 160 and t2["cambio"]["vistas"] == 12, t2)
    t3 = totales([nuevo])
    caso("si ningún vídeo tiene ↻ anterior no hay «cambio» (None, no ceros inventados)", t3["cambio"] is None and t3["comparado_con"] is None, t3)
    t4 = totales([None, None])
    caso("sin ninguna lectura: todo vacío, sin reventar", t4["vistas"] is None and t4["ret_pct"] is None and t4["leido_en"] is None and t4["videos"] == 2, t4)
    caso("retención con 0 vistas en todos no se inventa", totales([m(0, 0, 0, 50.0, 0, 0)])["ret_pct"] is None)
    return filas
