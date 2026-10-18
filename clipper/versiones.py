"""Versiones de un mismo vídeo PRO: la primera (`<base>--pro`) y todo lo que salga de ella (`<base>--pro-v2`, `-v3`…).

Una versión nueva nace de tres formas: «Reshuffle» (análisis nuevo de la IA), «Editar / cortar» (tus cortes a mano) y
«Mejorar» (la IA aplica un comentario tuyo sobre una versión que ya viste). Solo la última cuesta una llamada a Gemini y
solo esa cuenta para el tope de mejoras (`videos.mejoras_max`): los cortes a mano son gratis y no cuentan.

Todo aquí son funciones puras sobre listas de trabajos (los dicts de `_all_jobs_public`): fáciles de probar y sin panel.
"""
from __future__ import annotations

import re

RE = re.compile(r"^(?P<base>.+)--pro(?:-v(?P<v>\d+))?$")
_EN_MARCHA = ("queued", "running", "paused")


def parse(jid: str) -> tuple[str, int] | None:
    """'<base>--pro' -> (base, 1); '<base>--pro-v3' -> (base, 3); cualquier otra cosa (p. ej. '--videos') -> None."""
    m = RE.match(jid or "")
    return (m["base"], int(m["v"] or 1)) if m else None


def familia(filas, base: str) -> list[dict]:
    """Todas las versiones de `base`, de la más vieja a la más nueva, cada una con su número en `version`."""
    out = []
    for f in filas:
        p = parse(f.get("jid", ""))
        if p and p[0] == base:
            out.append({**f, "version": p[1]})
    return sorted(out, key=lambda f: f["version"])


def siguiente(fam: list[dict]) -> int:
    return max([f["version"] for f in fam] or [0]) + 1


def mejoras_usadas(fam: list[dict]) -> int:
    """Cuántas versiones de esta familia son «mejoras con IA» (no cuentan las falladas ni canceladas)."""
    return sum(1 for f in fam if (f.get("opts") or {}).get("mejora") and f.get("state") not in ("error", "cancelled"))


def restantes(fam: list[dict], maximo: int) -> int:
    return max(0, int(maximo) - mejoras_usadas(fam))


def en_marcha(fam: list[dict]) -> bool:
    return any(f.get("state") in _EN_MARCHA for f in fam)


def agrupa(filas) -> list[dict]:
    """Para la lista «Mis vídeos»: una sola tarjeta por vídeo PRO (su versión más reciente que sirve o está en marcha, con
    `n_versiones` y `base`: una mejora que falló, se canceló o «no cambió nada» no tapa a la buena); lo demás (el generador
    por temáticas) pasa igual. Conserva el orden de la lista original."""
    orden: list = []
    grupos: dict[str, list[dict]] = {}
    for f in filas:
        p = parse(f.get("jid", ""))
        if not p:
            orden.append(f)
            continue
        if p[0] not in grupos:
            grupos[p[0]] = []
            orden.append(p[0])
        grupos[p[0]].append({**f, "version": p[1]})
    out = []
    for x in orden:
        if isinstance(x, dict):
            out.append(x)
        else:
            versiones = grupos[x]
            utiles = [f for f in versiones if f.get("state") == "done" or f.get("state") in _EN_MARCHA] or versiones
            rep = max(utiles, key=lambda f: f["version"])
            out.append({**rep, "base": x, "n_versiones": len(utiles)})
    return out


def mis_videos(filas) -> list[dict]:
    """Lo que enseña «Mis vídeos» (el autor, 2026-09-24: «esos vídeos ya no están, todos son fallados… que sean borrables
    fácilmente»): UNA tarjeta por vídeo PRO solo si hay algo que ver o que esperar —
    - una versión en marcha (con su barra), o
    - una versión TERMINADA cuyo vídeo sigue en el PC (`has_output`): la más nueva; al abrirla, se ve.
    Un vídeo que se terminó pero cuyo .mp4 ya borraste NO sale aquí (vive en el «Historial», con sus métricas: abrirlo
    aquí solo enseñaba una página vacía). Uno que SOLO tiene intentos fallidos o cancelados (nunca salió) sale con
    `borrable=True` (el panel le pone un 🗑). El generador por temáticas pasa igual que siempre."""
    out = []
    for c in agrupa(filas):
        if not c.get("base"):
            out.append(c)
            continue
        fam = familia(filas, c["base"])
        if en_marcha(fam):
            out.append(c)
            continue
        con_video = [f for f in fam if f.get("state") == "done" and f.get("has_output")]
        if con_video:
            rep = max(con_video, key=lambda f: f["version"])
            out.append({**rep, "base": c["base"], "n_versiones": c.get("n_versiones", 1)})
        elif not any(f.get("state") == "done" for f in fam):
            out.append({**c, "borrable": True})
        # (terminado pero con el vídeo borrado del PC: al «Historial», aquí no)
    return out


def prueba() -> list[dict]:
    """Casos de las versiones (corre en «Probar el sistema»)."""
    filas_: list[dict] = []

    def caso(n, ok, d=""):
        filas_.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    caso("reconoce la versión 1 y las siguientes",
         parse("clip-ab12--pro") == ("clip-ab12", 1) and parse("clip-ab12--pro-v12") == ("clip-ab12", 12))
    caso("un nombre con «--» dentro sigue dando su base entera", parse("a--b--pro-v2") == ("a--b", 2))
    caso("un trabajo del generador por temáticas NO es una versión", parse("directo--videos") is None and parse("") is None)
    J = lambda jid, st="done", **o: {"jid": jid, "state": st, "opts": o}                    # noqa: E731
    filas = [J("x--pro"), J("x--pro-v2", mejora="quita eso"), J("x--pro-v4", cortes=[[1, 2]]), J("y--pro"), J("z--videos"),
             J("x--pro-v3", "error", mejora="otra"), J("x--pro-v5", "cancelled", mejora="otra más")]
    fam = familia(filas, "x")
    caso("la familia junta y ordena las versiones de un vídeo", [f["version"] for f in fam] == [1, 2, 3, 4, 5], [f["version"] for f in fam])
    caso("la siguiente versión es la mayor + 1 (aunque falten números)", siguiente(fam) == 6 and siguiente([]) == 1)
    caso("solo cuentan las mejoras que salieron bien (no falladas, canceladas ni cortes a mano)", mejoras_usadas(fam) == 1, mejoras_usadas(fam))
    caso("quedan las que faltan hasta el máximo, sin bajar de cero", restantes(fam, 3) == 2 and restantes(fam, 1) == 0)
    caso("detecta cuando ya hay una versión procesándose", not en_marcha(fam) and en_marcha(fam + [J("x--pro-v6", "running")]))
    tres = [J("a--pro", mejora=None), J("a--pro-v2", mejora="uno"), J("a--pro-v3", mejora="dos")]
    caso("con 3 mejoras usadas, el tope de 3 se cumple (ya no quedan)", restantes(familia(tres, "a"), 3) == 1
         and restantes(familia(tres + [J("a--pro-v4", mejora="tres")], "a"), 3) == 0)
    g = agrupa(filas)
    ident = [(x["jid"], x.get("n_versiones")) for x in g]
    caso("«Mis vídeos» muestra UNA tarjeta por vídeo (su última versión que SIRVE: una mejora fallida o cancelada no la tapa) y deja pasar lo demás",
         len(g) == 3 and ("x--pro-v4", 3) in ident and ("y--pro", 1) in ident and ("z--videos", None) in ident, ident)
    g2 = agrupa([J("q--pro"), J("q--pro-v2", "running", mejora="algo")])
    caso("si la última versión está procesándose, la tarjeta enseña ESA (con su barra)", g2[0]["jid"] == "q--pro-v2" and g2[0]["n_versiones"] == 2, g2)

    # «Mis vídeos»: solo lo que se puede ver o esperar; lo fallido, borrable; lo terminado y borrado, al Historial
    K = lambda jid, st="done", out=True, **o: {"jid": jid, "state": st, "has_output": out, "opts": o}   # noqa: E731
    mv = mis_videos([K("ok--pro"), K("ok--pro-v2", mejora="x"),                     # terminado y en el PC
                     K("borrado--pro", out=False), K("borrado--pro-v2", out=False),   # terminado, pero lo borró del PC
                     K("mal--pro", "error", out=False), K("mal--pro-v2", "error", out=False),   # solo intentos fallidos
                     K("va--pro", out=False), K("va--pro-v2", "running", out=False),  # uno en marcha
                     K("mixto--pro", out=True), K("mixto--pro-v2", "error", out=False),
                     K("tema--videos")])
    por = {x.get("base") or x["jid"]: x for x in mv}
    caso("un vídeo terminado que sigue en el PC sale (su versión más nueva con vídeo) y NO es borrable desde aquí",
         por.get("ok", {}).get("jid") == "ok--pro-v2" and not por["ok"].get("borrable"), por.get("ok"))
    caso("un vídeo terminado cuyo .mp4 borraste ya NO sale en «Mis vídeos» (está en el Historial)", "borrado" not in por, list(por))
    caso("un vídeo con SOLO intentos fallidos sale como borrable (🗑)", por.get("mal", {}).get("borrable") is True, por.get("mal"))
    caso("uno en marcha sale aunque todavía no tenga vídeo; y uno con una mejora fallida enseña su versión buena",
         por.get("va", {}).get("jid") == "va--pro-v2" and por.get("mixto", {}).get("jid") == "mixto--pro"
         and not por["mixto"].get("borrable"), (por.get("va"), por.get("mixto")))
    caso("el generador por temáticas pasa igual que siempre", "tema--videos" in por)
    return filas_
