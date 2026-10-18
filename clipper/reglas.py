"""📌 Reglas permanentes del director (el autor, 2026-10-03: «versiones para mejorar… una casilla que diga recordar esto para
todos mis próximos vídeos… feedback súper general que se lo tome la directiva»).

Una regla es un comentario del autor que NO es de un solo vídeo («no cortes mis risas», «tarjetas más cortas», «empieza directo
al grano»): se guarda y el director la lee en TODOS los análisis y mejoras siguientes de ese canal, dentro de su llamada de
siempre (0 peticiones más: gratis y sin quitarle cuota a los Flash). Se crean con la casilla «📌 Recordar esto para todos mis
próximos vídeos» de la tarjeta «¿Quieres mejorarle algo?» (siempre tras un aviso que pide confirmar) o a mano, sin crear versión;
se pueden ver, editar, pausar y borrar desde esa misma tarjeta.

- Viven en `work/reglas_director.json` (sin Git; van en la copia de seguridad), por CANAL: lo que sirve para el canal de SEO puede
  no servir para otro. Cada una: id, texto, activa, creada, origen («mejora:<trabajo>» o «manual»).
- Límites contra el «ruido»: 8-400 caracteres, como mucho `videos.reglas_max` (12) por canal (activas + en pausa) y ninguna que se
  parezca (≥ 75 % de palabras en común) a otra que ya esté.
- Borrar no destruye: la regla pasa a «borradas» (las últimas 30) dentro del mismo archivo.
- Las lee `pipeline._contexto_director` (solo el DIRECTOR; los Shorts siguen recibiendo el contexto de siempre) y cada versión
  apunta cuáles aplicó (`analisis_ia.json` → «reglas»): `/video/<base>` lo enseña como «📌 N reglas».
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from .util import log, normalize

MIN_CHARS = 8
MAX_CHARS = 400
REGLAS_MAX = 12              # por canal; `videos.reglas_max` en config.yaml
PARECIDO_MAX = 0.75          # una regla nueva no puede parecerse a otra que ya esté más que esto (palabras en común)
BORRADAS_MAX = 30
_LOCK = threading.Lock()
# un comentario que habla de UN momento del vídeo no sirve de regla permanente («en el 3:20», «el minuto 5»): se avisa
_MOMENTO_RE = re.compile(r"\b\d{1,2}:\d{2}\b|\bminutos?\s+\d|\bsegundos?\s+\d|\ben el \d+\b|\bal minuto\b", re.I)


# ============================================================================================ el archivo
def _dir_work(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work)
    from . import config
    return config.paths()["work"]


def ruta(dir_work=None) -> Path:
    return _dir_work(dir_work) / "reglas_director.json"


def _vacio() -> dict:
    return {"version": 1, "reglas": [], "borradas": []}


def lee(dir_work=None) -> dict:
    """Todo el archivo ({"reglas": [...], "borradas": [...]}). Nunca lanza: sin archivo o roto, vacío."""
    try:
        d = json.loads(ruta(dir_work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _vacio()
    if not isinstance(d, dict):
        return _vacio()
    d["reglas"] = [r for r in (d.get("reglas") or []) if isinstance(r, dict) and r.get("id") and r.get("texto")]
    d["borradas"] = [r for r in (d.get("borradas") or []) if isinstance(r, dict)]
    return d


def _guarda(d: dict, dir_work=None) -> None:
    """Atómico: nunca deja el archivo a medias (si se corta la luz, queda el de antes)."""
    p = ruta(dir_work)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def maximo(cfg=None) -> int:
    try:
        return max(1, int(cfg.get_path("videos.reglas_max", REGLAS_MAX))) if cfg is not None else REGLAS_MAX
    except (TypeError, ValueError, AttributeError):
        return REGLAS_MAX


# ============================================================================================ consultas
def lista(canal: str = "", dir_work=None) -> list[dict]:
    """Las reglas de ese canal (activas y en pausa), en el orden en que se crearon."""
    return [r for r in lee(dir_work)["reglas"] if (r.get("canal") or "") == (canal or "")]


def activas(canal: str = "", dir_work=None) -> list[dict]:
    return [r for r in lista(canal, dir_work) if r.get("activa", True)]


def _palabras(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", normalize(t))}


def parecido(a: str, b: str) -> float:
    x, y = _palabras(a), _palabras(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def habla_de_un_momento(texto: str) -> bool:
    """¿El texto habla de UN momento concreto del vídeo («en el 3:20»)? Una regla permanente tiene que ser general."""
    return bool(_MOMENTO_RE.search(str(texto or "")))


def _limpia(texto) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", " ", str(texto or ""))).strip()


def _valida(texto: str, existentes: list[dict], salvo_id: str = "") -> tuple[str, str]:
    """-> (texto limpio, error). `existentes` = las reglas de ese canal (para no repetir)."""
    t = _limpia(texto)
    if len(t) < MIN_CHARS:
        return t, "La regla es muy corta: cuéntale al director, con unas palabras, qué quieres siempre."
    if len(t) > MAX_CHARS:
        return t, f"La regla es muy larga ({len(t)} letras; máximo {MAX_CHARS}): resúmela. Una regla vale para TODOS tus vídeos."
    for r in existentes:
        if r["id"] != salvo_id and (normalize(r["texto"]) == normalize(t) or parecido(r["texto"], t) >= PARECIDO_MAX):
            return t, f"Ya tienes una regla casi igual: «{r['texto'][:120]}»."
    return t, ""


# ============================================================================================ cambios
def agrega(canal: str, texto: str, origen: str = "manual", cfg=None, dir_work=None, ahora: float | None = None) -> dict:
    """Guarda una regla nueva (activa) para ese canal. -> {"ok": True, "regla": {...}} o {"ok": False, "error": …,
    "duplicada": bool}. Nunca lanza."""
    with _LOCK:
        d = lee(dir_work)
        mias = [r for r in d["reglas"] if (r.get("canal") or "") == (canal or "")]
        t, error = _valida(texto, mias)
        if error:
            return {"ok": False, "error": error, "duplicada": error.startswith("Ya tienes")}
        tope = maximo(cfg)
        if len(mias) >= tope:
            return {"ok": False, "duplicada": False, "error": f"Ya tienes {tope} reglas: borra o cambia alguna antes de "
                    "añadir otra (con demasiadas, el director deja de hacerles caso a todas)."}
        regla = {"id": "r" + uuid.uuid4().hex[:7], "canal": canal or "", "texto": t, "activa": True,
                 "creada": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ahora if ahora is not None else time.time())),
                 "origen": str(origen or "manual")[:80]}
        d["reglas"].append(regla)
        try:
            _guarda(d, dir_work)
        except OSError as exc:
            return {"ok": False, "duplicada": False, "error": f"No se pudo guardar la regla ({exc})."}
    log(f"📌 Regla nueva para el director ({canal or 'sin canal'}): «{t[:120]}»")
    return {"ok": True, "regla": regla}


def edita(id_: str, texto: str | None = None, activa: bool | None = None, dir_work=None) -> dict:
    """Cambia el texto y/o pausa-reanuda una regla. -> {"ok": True, "regla"} o {"ok": False, "error"}."""
    with _LOCK:
        d = lee(dir_work)
        r = next((x for x in d["reglas"] if x["id"] == id_), None)
        if r is None:
            return {"ok": False, "error": "Esa regla ya no existe: recarga la lista."}
        if texto is not None:
            mias = [x for x in d["reglas"] if (x.get("canal") or "") == (r.get("canal") or "")]
            t, error = _valida(texto, mias, salvo_id=id_)
            if error:
                return {"ok": False, "error": error}
            r["texto"] = t
            r["editada"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if activa is not None:
            r["activa"] = bool(activa)
        try:
            _guarda(d, dir_work)
        except OSError as exc:
            return {"ok": False, "error": f"No se pudo guardar el cambio ({exc})."}
    return {"ok": True, "regla": r}


def borra(id_: str, dir_work=None) -> dict:
    """La regla deja de valer y pasa a «borradas» (las últimas 30): nada se destruye."""
    with _LOCK:
        d = lee(dir_work)
        r = next((x for x in d["reglas"] if x["id"] == id_), None)
        if r is None:
            return {"ok": False, "error": "Esa regla ya no existe: recarga la lista."}
        d["reglas"] = [x for x in d["reglas"] if x["id"] != id_]
        d["borradas"] = ([{**r, "borrada": time.strftime("%Y-%m-%d %H:%M:%S")}] + d["borradas"])[:BORRADAS_MAX]
        try:
            _guarda(d, dir_work)
        except OSError as exc:
            return {"ok": False, "error": f"No se pudo borrar ({exc})."}
    log(f"📌 Regla borrada: «{r['texto'][:120]}»")
    return {"ok": True}


# ============================================================================================ lo que lee el director
def texto_prompt(reglas: list[dict] | None, revision: bool = False) -> str:
    """El bloque que va en el contexto del DIRECTOR ('' si no hay reglas). En una REVISIÓN (el autor comenta una versión) las
    reglas valen solo en lo que el comentario hace tocar: «cambia SOLO lo que pide el autor» sigue mandando."""
    items = [_limpia(r.get("texto")) for r in (reglas or []) if _limpia(r.get("texto"))]
    if not items:
        return ""
    cab = ("REGLAS PERMANENTES DEL AUTOR — las dio él al revisar vídeos anteriores y valen para TODOS sus vídeos: aplícalas "
           "siempre, en lo que toque cada una (cortes, cámara, tarjetas, imágenes, ilustraciones, palabras clave, títulos, "
           "miniaturas, descripción…). Si una choca con una instrucción fija de más abajo, manda la del autor, salvo que "
           "rompa el formato del JSON.")
    if revision:
        cab += (" En esta REVISIÓN aplícalas solo en lo que el comentario del autor te hace tocar: no rehagas por ellas lo que "
                "él no pidió cambiar.")
    return cab + "\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1))


def resumen_plan(reglas: list[dict] | None) -> list[dict]:
    """Lo que se apunta en el plan y en `analisis_ia.json`: qué reglas leyó el director en ESE análisis."""
    return [{"id": r.get("id"), "texto": _limpia(r.get("texto"))} for r in (reglas or []) if _limpia(r.get("texto"))]


# ============================================================================================ pruebas
def prueba() -> list[dict]:
    """Archivos de mentira en una carpeta temporal: crear, validar (corta, larga, repetida, tope), por canal, editar, pausar,
    borrar sin destruir, el bloque del director (también en una revisión) y que un archivo roto no revienta nada."""
    import shutil
    import tempfile
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    class _Cfg:                                              # (un config de mentira con un tope chico)
        def get_path(self, k, d=None):
            return {"videos.reglas_max": 4}.get(k, d)

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-reglas-"))
    try:
        r("sin archivo no hay reglas (y no revienta)", lista("ejemplo", tmp) == [] and activas("ejemplo", tmp) == []
          and texto_prompt(activas("ejemplo", tmp)) == "")
        a = agrega("ejemplo", "  No cortes mis risas:\n las dejo a propósito  ", "mejora:abc--pro", dir_work=tmp, ahora=1790000000)
        r("una regla nueva se guarda limpia (sin saltos de línea ni espacios de más), activa, con su id y su origen",
          a["ok"] and a["regla"]["texto"] == "No cortes mis risas: las dejo a propósito" and a["regla"]["activa"]
          and a["regla"]["id"].startswith("r") and a["regla"]["origen"] == "mejora:abc--pro", a)
        r("persiste en work/reglas_director.json (sobrevive a leerla otra vez)", ruta(tmp).is_file()
          and [x["texto"] for x in activas("ejemplo", tmp)] == ["No cortes mis risas: las dejo a propósito"])
        r("demasiado corta o larga: se rechaza con el motivo",
          not agrega("ejemplo", "corta", dir_work=tmp)["ok"] and "muy corta" in agrega("ejemplo", "corta", dir_work=tmp)["error"]
          and "muy larga" in agrega("ejemplo", "x" * 401, dir_work=tmp)["error"])
        rep = agrega("ejemplo", "NO cortes mis RISAS, las dejo a propósito!", dir_work=tmp)
        r("una regla igual o casi igual a otra (aunque cambien mayúsculas o signos) no se repite", not rep["ok"] and rep["duplicada"], rep)
        r("…pero una distinta sí entra", agrega("ejemplo", "Tarjetas más cortas: que no pasen de 3 segundos", dir_work=tmp)["ok"])
        r("cada CANAL tiene las suyas", agrega("otro", "Habla siempre de juegos y no de SEO nunca", dir_work=tmp)["ok"]
          and len(lista("ejemplo", tmp)) == 2 and len(lista("otro", tmp)) == 1 and activas("sin-canal", tmp) == [])
        agrega("ejemplo", "Empieza siempre directo al grano, sin saludos largos", cfg=_Cfg(), dir_work=tmp)
        agrega("ejemplo", "Los títulos sin signos de exclamación nunca jamás", cfg=_Cfg(), dir_work=tmp)
        tope = agrega("ejemplo", "Una regla más de las que caben en este canal", cfg=_Cfg(), dir_work=tmp)
        r("el tope por canal (`videos.reglas_max`) frena y dice qué hacer", not tope["ok"] and "borra o cambia" in tope["error"], tope)

        rid = lista("ejemplo", tmp)[1]["id"]
        e = edita(rid, texto="Tarjetas más cortas: máximo 2 segundos cada una", dir_work=tmp)
        r("editar cambia el texto y queda la fecha de edición", e["ok"] and e["regla"]["texto"].endswith("2 segundos cada una")
          and "editada" in e["regla"], e)
        r("editar una regla para que quede igual a OTRA se rechaza (a sí misma no cuenta)",
          not edita(rid, texto="No cortes mis risas, las dejo a propósito", dir_work=tmp)["ok"]
          and edita(rid, texto="Tarjetas más cortas: máximo 2 segundos cada una", dir_work=tmp)["ok"])
        edita(rid, activa=False, dir_work=tmp)
        r("pausar: sigue en la lista pero el director NO la lee", len(lista("ejemplo", tmp)) == 4
          and rid not in [x["id"] for x in activas("ejemplo", tmp)] and len(activas("ejemplo", tmp)) == 3)
        edita(rid, activa=True, dir_work=tmp)
        r("reanudar la vuelve a poner", rid in [x["id"] for x in activas("ejemplo", tmp)])
        b = borra(rid, dir_work=tmp)
        d = lee(tmp)
        r("borrar no destruye: sale de la lista y queda en «borradas» con su fecha", b["ok"]
          and rid not in [x["id"] for x in d["reglas"]] and d["borradas"][0]["id"] == rid and "borrada" in d["borradas"][0], d["borradas"])
        r("una regla que ya no existe se dice (no revienta)", not borra("r000000", dir_work=tmp)["ok"]
          and not edita("r000000", activa=False, dir_work=tmp)["ok"])

        act = activas("ejemplo", tmp)
        t1, t2 = texto_prompt(act), texto_prompt(act, revision=True)
        r("lo que lee el director: la cabecera y cada regla numerada, solo las activas",
          t1.startswith("REGLAS PERMANENTES DEL AUTOR") and "1. No cortes mis risas" in t1 and f"{len(act)}. " in t1
          and "Tarjetas más cortas" not in t1 and "REVISIÓN" not in t1, t1[:300])
        r("en una REVISIÓN dice que solo valen en lo que el comentario toca (no rehace lo demás)",
          "En esta REVISIÓN aplícalas solo en lo que el comentario del autor te hace tocar" in t2)
        r("resumen para el plan: id y texto de las que leyó", [x["id"] for x in resumen_plan(act)] == [x["id"] for x in act]
          and resumen_plan([]) == [] and resumen_plan(None) == [])
        r("un comentario de UN momento concreto se detecta (para avisar antes de hacerlo regla permanente)",
          habla_de_un_momento("Corta el silencio del 3:20") and habla_de_un_momento("quita lo del minuto 5")
          and not habla_de_un_momento("No cortes mis risas nunca"))

        ruta(tmp).write_text("{esto no es json", encoding="utf-8")
        r("un archivo roto no revienta: es como no tener reglas", lista("ejemplo", tmp) == [] and lee(tmp)["reglas"] == [])
        r("…y se puede volver a empezar encima", agrega("ejemplo", "Una regla nueva sobre un archivo roto", dir_work=tmp)["ok"]
          and len(lista("ejemplo", tmp)) == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    import sys
    filas_ = prueba()
    for f_ in filas_:
        print(("OK   " if f_["ok"] else "FALLA") + " " + f_["caso"] + ("" if f_["ok"] else f"  -> {f_['detalle']}"))
    sys.exit(1 if any(not f_["ok"] for f_ in filas_) else 0)
