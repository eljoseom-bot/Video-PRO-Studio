"""💾 ESPACIO de los vídeos (el autor, 2026-10-04: «estoy sacando un contenido diario… en 90 días van a haber 90 videos… ¿cómo va a ser la
sostenibilidad?», «solo queda el esqueleto: las muletillas, el título que utilicé, la miniatura que se coge de la API… no debería haber
nada grabado ahí»; sobre el 🗑 de «Mis vídeos»: «si está programado pasa al Historial y si no, simplemente se borra… ese borrar debe
borrar todo lo de ese vídeo, desde mi PC» y, aclarando: «todo debería borrarse excepto las que están en mi disco D, Videos: esas déjalas
ahí, yo las borro… las dejo como respaldo por si acaso»).

Medido con SUS carpetas (un vídeo de ~10 min): el Historial pesa ~0,6 MB por vídeo (ya es solo ese esqueleto); lo que pesa es lo que queda del
vídeo en el PC: la grabación de OBS y su cámara aparte (~0,85 GB, en `D:/Videos`), la copia de la grabación en `inbox/` (~0,2 GB), el vídeo final
con sus Shorts (~0,4 GB) y las cachés de trabajo (~0,4 GB). Sin limpiar nada: ~2 GB por vídeo (60 GB al mes, 180 GB en 90 días).

Qué hay aquí (todo con sus dependencias a la vista: así se prueba sin tocar nada de verdad):
  · TUS GRABACIONES DE OBS (`D:/Videos`) NO SE TOCAN NUNCA: ni con el 🗑 ni en la limpieza automática (son tu respaldo). `obs_de` solo las ENCUENTRA, para
    decir en el aviso que se quedan y para medir cuánto crece esa carpeta.
  · `borra_rutas`: borra DE VERDAD (sin Papelera) todo lo de un vídeo, solo si cuelga de `videos/`, `work/` o `inbox/`. `borra_caches`: lo mismo, solo para las
    cachés pesadas REGENERABLES de `work/<vídeo>--pro/` (`escenas/`, `ilustraciones/` y `musica/`: el siguiente render las dibuja solas).
  · `plan` / `ejecuta`: la limpieza por EDAD (solo si el autor la enciende: «🧹 Hacerlo solo»): las cachés de lo ya publicado y de los vídeos que ya no
    están en «Mis vídeos», y borrar ENTEROS (con el mismo ✖ de «Mis vídeos») los vídeos publicados hace más de `dias` días. NUNCA un vídeo sin publicar, ni uno que
    YouTube ya no devuelve (la copia del PC podría ser la única), ni uno en marcha o en la cola de subida, ni una caché tocada hace < 10 min.
  · `medida`: lo que enseña «💾 Espacio»: disco, qué pesa cada cosa, cada vídeo, cuánto se podría liberar y a qué ritmo se llena el disco (con y sin OBS).
"""
from __future__ import annotations

import math
import os
import re
import shutil
import statistics
import stat
import sys
import time
from pathlib import Path

CACHES_PESADAS = ("escenas", "ilustraciones", "musica")
MIN_EDAD_S = 600.0                      # una caché tocada hace menos de 10 min podría estar en uso: no se toca
DIAS_DEF = 7                            # un vídeo publicado se puede borrar entero pasados estos días (da tiempo a artículo, redes y Shorts)
DIAS_OPCIONES = (3, 7, 14, 30)
PC_DEF = int(1.15 * 1024 ** 3)          # sin ningún vídeo que medir, lo que se midió con los del autor: lo que se lleva el ✖ de un vídeo de ~10 min…
OBS_DEF = int(0.85 * 1024 ** 3)         # …y lo que pesan sus grabaciones de OBS (que el programa NO toca)
UMBRAL_OBS = 100 * 1024 * 1024          # de la carpeta de OBS solo cuentan los vídeos de verdad (no las pruebas de unos segundos)
_RE_TRABAJO = re.compile(r"^(?P<base>.+)--pro(?:-v(?P<v>\d+))?$")
_EXT_VIDEO = (".mkv", ".mp4", ".mov", ".webm")
_FECHA = re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2}-\d{2}-\d{2})")
_YA_EN_YOUTUBE = ("publicado", "privado", "oculto", None)     # "programado" aún no ha salido; "no_esta" (YouTube ya no lo devuelve): nunca


# ------------------------------------------------------------------ tamaños
def pesa(ruta, vistos: set | None = None) -> int:
    """Bytes de un archivo o una carpeta; un archivo con varios nombres (enlace duro) cuenta UNA vez (si se pasa `vistos`)."""
    vistos = vistos if vistos is not None else set()
    ruta = Path(ruta)
    total = 0
    try:
        if ruta.is_file():
            st = ruta.stat()
            clave = (st.st_dev, st.st_ino)
            if clave in vistos:
                return 0
            vistos.add(clave)
            return st.st_size
        for dp, _dn, fn in os.walk(ruta):
            for f in fn:
                try:
                    st = (Path(dp) / f).stat()
                except OSError:
                    continue
                clave = (st.st_dev, st.st_ino)
                if clave in vistos:
                    continue
                vistos.add(clave)
                total += st.st_size
    except OSError:
        pass
    return total


def _pesa_y_edad(ruta) -> tuple[int, float]:
    """(bytes, instante de la modificación más reciente de lo que hay dentro; 0 si no hay nada)"""
    total, ultimo = 0, 0.0
    vistos: set = set()
    for dp, _dn, fn in os.walk(ruta):
        for f in fn:
            try:
                st = (Path(dp) / f).stat()
            except OSError:
                continue
            ultimo = max(ultimo, st.st_mtime)
            clave = (st.st_dev, st.st_ino)
            if clave not in vistos:
                vistos.add(clave)
                total += st.st_size
    return total, ultimo


def pesa_lista(rutas) -> int:
    vistos: set = set()
    return sum(pesa(r, vistos) for r in rutas)


# ------------------------------------------------------------------ las cachés pesadas (lo único que se borra de verdad)
def trabajos(dir_work) -> dict[str, Path]:
    """`work/<base>--pro` de cada vídeo (la caché común de todas sus versiones; las `--pro-vN` solo llevan su registro)."""
    out: dict[str, Path] = {}
    d = Path(dir_work)
    if d.is_dir():
        for p in sorted(d.iterdir()):
            m = _RE_TRABAJO.match(p.name)
            if p.is_dir() and m and m["v"] is None and not p.name.startswith("zz-selftest"):      # (restos de «Probar el sistema»: no cuentan)
                out[m["base"]] = p
    return out


def caches_de(dir_work, base: str) -> list[Path]:
    w = Path(dir_work) / f"{base}--pro"
    return [w / n for n in CACHES_PESADAS if (w / n).is_dir()]


def _motivo_no(r: Path, dir_work, en_marcha) -> str:
    """'' si esta carpeta se puede borrar; si no, por qué no. Solo `work/<vídeo>--pro/<escenas|ilustraciones|musica>`."""
    if r.name not in CACHES_PESADAS:
        return "no es una caché pesada"
    if os.path.islink(r) or not r.is_dir():
        return "no es una carpeta normal"
    m = _RE_TRABAJO.match(r.parent.name)
    if not m or m["v"] is not None:
        return "no está en la carpeta de trabajo de un vídeo"
    if os.path.normcase(os.path.realpath(r.parent.parent)) != os.path.normcase(os.path.realpath(dir_work)):
        return "no está en work/"
    if m["base"] in en_marcha:
        return "ese vídeo se está procesando"
    return ""


def borra_caches(rutas, dir_work, *, en_marcha=(), min_edad_s: float = MIN_EDAD_S, ahora: float | None = None) -> dict:
    """Borra DE VERDAD esas cachés (nunca otra cosa: cada ruta se comprueba justo antes). Una caché tocada hace menos de `min_edad_s` se
    salta (alguien podría estar usándola). Nunca lanza. -> {"bytes", "borradas", "saltadas", "errores"}"""
    ahora = time.time() if ahora is None else ahora
    res = {"bytes": 0, "borradas": 0, "saltadas": 0, "errores": []}
    for r in rutas:
        r = Path(r)
        try:
            motivo = _motivo_no(r, dir_work, en_marcha)
            if motivo:
                res["errores"].append(f"{r.parent.name}/{r.name}: {motivo}")
                continue
            n, ultimo = _pesa_y_edad(r)
            if min_edad_s and n and ahora - ultimo < min_edad_s:
                res["saltadas"] += 1
                continue
            try:
                shutil.rmtree(r)
                res["bytes"] += n
            except OSError as exc:
                resto = pesa(r) if r.exists() else 0
                res["bytes"] += max(0, n - resto)
                res["errores"].append(f"{r.parent.name}/{r.name}: {exc}")
                continue
            res["borradas"] += 1
        except Exception as exc:                                   # noqa: BLE001
            res["errores"].append(f"{r.name}: {exc}")
    return res


# ------------------------------------------------------------------ borrar de verdad lo de un vídeo (el ✖ de «Mis vídeos»)
def _bajo_alguna(ruta: Path, raices) -> bool:
    """¿`ruta` está DENTRO de alguna de esas carpetas (y no es la carpeta misma)? Se compara lo que ya resuelve el sistema (enlaces incluidos): un enlace que
    apunte fuera no cuenta."""
    r = os.path.normcase(os.path.realpath(ruta))
    for raiz in raices:
        rr = os.path.normcase(os.path.realpath(raiz))
        if r != rr and r.startswith(rr.rstrip("\\/") + os.sep):
            return True
    return False


def _quita_solo_lectura(func, ruta, _exc) -> None:
    """Para `shutil.rmtree`: un archivo de solo lectura se vuelve escribible y se reintenta una vez."""
    os.chmod(ruta, stat.S_IWRITE)
    func(ruta)


def _rmtree(ruta) -> None:
    """`shutil.rmtree` que no se atasca con archivos de solo lectura (`onexc` desde Python 3.12; `onerror` antes)."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(ruta, onexc=_quita_solo_lectura)
    else:
        shutil.rmtree(ruta, onerror=_quita_solo_lectura)


def borra_rutas(rutas, permitidas) -> dict:
    """Borra DE VERDAD (sin Papelera) esas rutas — archivos o carpetas enteras —, pero SOLO las que cuelgan de una de las carpetas `permitidas` (`videos/`,
    `work/`, `inbox/`): cualquier otra se salta con su motivo (tus grabaciones de OBS, que están fuera, no pueden caer aquí ni por error). Una que ya no existe se
    ignora. Un archivo de un vídeo que otro programa tiene abierto no se borra: queda en `errores`. Nunca lanza. -> {"bytes", "borradas", "errores"}"""
    res = {"bytes": 0, "borradas": 0, "errores": []}
    for r in rutas:
        r = Path(r)
        n = 0
        try:
            if not (r.exists() or os.path.islink(r)):
                continue
            if not _bajo_alguna(r, permitidas):
                res["errores"].append(f"{r.name}: no está dentro de las carpetas del programa: no se toca")
                continue
            n = pesa(r)
            if r.is_dir() and not os.path.islink(r):
                _rmtree(r)
            else:
                try:
                    r.unlink()
                except PermissionError:
                    os.chmod(r, stat.S_IWRITE)
                    r.unlink()
            if r.exists():
                res["bytes"] += max(0, n - pesa(r))
                res["errores"].append(f"{r.name}: no se pudo borrar del todo (¿lo tiene abierto otro programa?)")
            else:
                res["bytes"] += n
                res["borradas"] += 1
        except Exception as exc:                                   # noqa: BLE001
            resto = pesa(r) if r.exists() else 0
            res["errores"].append(f"{r.name}: {exc}")
            res["bytes"] += max(0, n - resto)
    return res


# ------------------------------------------------------------------ las grabaciones originales de OBS (SOLO para encontrarlas: no se borran)
def clave_de(nombre) -> str | None:
    """«2026-10-02_13-55-17-2.mkv» y «2026-10-02 13-55-17.mkv» -> «2026-10-02 13-55-17» (el panel cambia el espacio por «_» y una subida repetida
    añade «-2»); None si el nombre no lleva la fecha y la hora de OBS."""
    m = _FECHA.search(str(nombre))
    return f"{m.group(1)} {m.group(2)}" if m else None


def _mismo_contenido(a: Path, b: Path, n: int = 65536) -> bool:
    """Mismo tamaño (ya comprobado fuera) y mismo principio y final: la misma grabación."""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            if fa.read(n) != fb.read(n):
                return False
            tam = os.path.getsize(a)
            if tam > 2 * n:
                fa.seek(tam - n)
                fb.seek(tam - n)
                return fa.read(n) == fb.read(n)
        return True
    except OSError:
        return False


def obs_de(grabaciones, carpeta_obs, otras_fuentes=(), busca_camara=None, sufijo: str = "camara") -> dict:
    """Las grabaciones ORIGINALES de OBS de estas grabaciones de `inbox/` (lo que sube el panel es una COPIA): el archivo de la grabación en la
    carpeta de OBS (mismo nombre con fecha y hora, mismo tamaño y mismo principio y final) y su cámara aparte (`busca_camara(grabación)` =
    `camara_aparte.busca`). Solo archivos que estén DIRECTAMENTE en la carpeta de OBS. Si otro vídeo (de `otras_fuentes`) viene de la misma
    grabación, no se toca (`compartida`). -> {"archivos": [Path], "bytes", "compartida", "carpeta"}"""
    res = {"archivos": [], "bytes": 0, "compartida": False, "carpeta": str(carpeta_obs or "")}
    if not carpeta_obs or not Path(carpeta_obs).is_dir():
        return res
    carpeta = Path(carpeta_obs)
    carpeta_real = os.path.normcase(os.path.realpath(carpeta))
    claves_otras = {clave_de(Path(str(s)).name) for s in otras_fuentes} - {None}
    sufijo = str(sufijo or "").lower()
    vistos: set = set()

    def _suma(f: Path) -> None:
        real = os.path.normcase(os.path.realpath(f))
        if real in vistos or os.path.normcase(os.path.realpath(f.parent)) != carpeta_real:
            return
        vistos.add(real)
        res["archivos"].append(f)
        res["bytes"] += f.stat().st_size

    for g in grabaciones:
        g = Path(g)
        k = clave_de(g.name)
        if k is None:
            continue
        if k in claves_otras:
            res["compartida"] = True
            continue
        try:
            tam = g.stat().st_size
            candidatos = [f for f in carpeta.iterdir() if f.is_file() and f.suffix.lower() in _EXT_VIDEO]
        except OSError:
            continue
        for f in candidatos:
            try:
                if (clave_de(f.name) == k and not (sufijo and sufijo in f.stem.lower()) and f.stat().st_size == tam
                        and _mismo_contenido(f, g)):
                    _suma(f)
            except OSError:
                continue
        try:
            cam = busca_camara(g) if busca_camara else None
        except Exception:                                          # noqa: BLE001 — sin cámara no pasa nada
            cam = None
        if cam:
            _suma(Path(cam))
    return res


def _obs_total(carpeta, umbral: int = UMBRAL_OBS) -> dict | None:
    """La carpeta de grabaciones de OBS (no es del programa: solo se mide). Cuenta los vídeos grandes (no las pruebas de unos segundos)."""
    if not carpeta or not Path(carpeta).is_dir():
        return None
    c = Path(carpeta)
    grandes = []
    try:
        for f in c.iterdir():
            if f.is_file() and f.suffix.lower() in _EXT_VIDEO:
                n = f.stat().st_size
                if n >= umbral:
                    grandes.append(n)
    except OSError:
        return None
    return {"carpeta": str(c), "grandes": len(grandes), "bytes": sum(grandes)}


# ------------------------------------------------------------------ qué se puede liberar
def listo(fila: dict | None, ahora: float, dias: int = DIAS_DEF) -> tuple[bool, int | None]:
    """(¿se puede borrar ENTERO ya?, días que faltan) para una fila de `historial.lista()`. Solo un vídeo que YouTube tiene (publicado, privado u
    oculto — o que nunca se pudo preguntar pero se subió bien) y desde hace `dias`; uno programado cuenta desde que SALE. Sin publicar, o uno que
    YouTube ya no devuelve (`no_esta`: la copia del PC podría ser la única), nunca."""
    yt = (fila or {}).get("youtube") or {}
    if not yt.get("video_id"):
        return False, None
    estado = fila.get("estado_yt")
    if estado not in _YA_EN_YOUTUBE and estado != "programado":
        return False, None
    desde = max(float(fila.get("fecha_yt") or 0), float(yt.get("publicado") or 0))
    faltan = math.ceil(dias - (ahora - desde) / 86400.0)
    return faltan <= 0, (None if faltan <= 0 else max(1, faltan))


def plan(*, dir_work, filas, familias, en_marcha=(), ahora: float | None = None, dias: int = DIAS_DEF) -> dict:
    """Qué se liberaría ahora (sin tocar nada). `filas` = `historial.lista()`; `familias` = los vídeos PRO de «Mis vídeos» ({"base", "titulo"}).
    · `caches`: las cachés pesadas de un vídeo ya publicado (o que sale pronto) y de los restos de vídeos que ya no están en «Mis vídeos»
      (y que nadie ha tocado en 10 min);
    · `completos`: los vídeos publicados hace `dias` días o más: se pueden borrar enteros (el Historial se queda con su esqueleto);
    · `pendientes`: los publicados que aún están dentro del plazo (con los días que faltan);
    · `sin_publicar`: lo que no se toca nunca."""
    ahora = time.time() if ahora is None else ahora
    por_base = {f.get("base"): f for f in (filas or []) if f.get("base")}
    en_panel = {f["base"] for f in familias}
    tr = trabajos(dir_work)
    caches: list[dict] = []
    completos: list[dict] = []
    pendientes: list[dict] = []
    sin_publicar: list[dict] = []

    def _caches_de(base: str, huerfano: bool) -> None:
        for r in caches_de(dir_work, base):
            n, edad = _pesa_y_edad(r)
            if n and ahora - edad >= MIN_EDAD_S:
                caches.append({"base": base, "ruta": r, "bytes": n, "huerfano": huerfano})

    for fam in familias:
        base, titulo = fam["base"], fam.get("titulo") or fam["base"]
        if base in en_marcha:
            continue
        fila = por_base.get(base)
        if not (fila and (fila.get("youtube") or {}).get("video_id")) or fila.get("estado_yt") == "no_esta":
            sin_publicar.append({"base": base, "titulo": titulo})
            continue
        ok, faltan = listo(fila, ahora, dias)
        if ok:
            completos.append({"base": base, "titulo": titulo})
        else:
            pendientes.append({"base": base, "titulo": titulo, "faltan_dias": faltan})
            if fila.get("estado_yt") != "programado":
                _caches_de(base, False)                           # ya está en YouTube: sus cachés regenerables sobran
    for base in tr:
        if base not in en_panel and base not in en_marcha:
            _caches_de(base, True)
    return {"caches": caches, "completos": completos, "pendientes": pendientes, "sin_publicar": sin_publicar}


def ejecuta(p: dict, *, dir_work, borra_completo=None, en_marcha=(), solo_caches: bool = False) -> dict:
    """Hace lo que dice el plan `p`. Nunca lanza. `borra_completo(base)` = el mismo 🗑 de «Mis vídeos» (esqueleto en el Historial, Papelera…) ->
    {"ok", "error", "liberado", "papelera"}. -> {"cache", "completos", "liberado_completos", "papelera", "bases", "errores"}"""
    res = {"cache": 0, "completos": 0, "liberado_completos": 0, "papelera": 0, "bases": [], "errores": []}
    r = borra_caches([c["ruta"] for c in p.get("caches") or []], dir_work, en_marcha=en_marcha)
    res["cache"] = r["bytes"]
    res["errores"] += r["errores"]
    if solo_caches or borra_completo is None:
        return res
    for c in p.get("completos") or []:
        base = c["base"]
        if base in en_marcha:
            continue
        try:
            x = borra_completo(base) or {}
        except Exception as exc:                                   # noqa: BLE001
            res["errores"].append(f"{base}: {exc}")
            continue
        if x.get("ok"):
            res["completos"] += 1
            res["liberado_completos"] += int(x.get("liberado") or 0)
            res["papelera"] += int(x.get("papelera") or 0)
            res["bases"].append(base)
        else:
            res["errores"].append(f"{base}: {x.get('error') or 'no se pudo borrar'}")
    return res


# ------------------------------------------------------------------ las opciones (config.yaml) y lo último que se hizo
_RE_AUTO = re.compile(r"(?m)^(  limpieza_auto:)[ \t]*\w+([ \t]*(?:#.*)?)$")
_RE_DIAS = re.compile(r"(?m)^(  limpieza_dias:)[ \t]*\d+([ \t]*(?:#.*)?)$")


def opciones(cfg) -> tuple[bool, int]:
    """(¿limpieza automática?, días) de config.yaml (`videos.limpieza_auto` / `videos.limpieza_dias`): apagada y 7 días si no están."""
    try:
        auto = bool(cfg.get_path("videos.limpieza_auto", False))
        dias = int(cfg.get_path("videos.limpieza_dias", DIAS_DEF))
    except Exception:                                              # noqa: BLE001
        auto, dias = False, DIAS_DEF
    return auto, max(1, min(365, dias))


def guarda_opciones(auto: bool, dias: int, ruta=None) -> bool:
    """Cambia `videos.limpieza_auto` y `videos.limpieza_dias` EN config.yaml tocando solo esas dos líneas (como `config.guarda_vision`): vale desde
    la siguiente pasada, sin reiniciar el panel y sin perder ni un comentario. False si el valor no vale o falta alguna de las dos líneas."""
    if not isinstance(auto, bool) or not isinstance(dias, int) or isinstance(dias, bool) or not 1 <= dias <= 365:
        return False
    if ruta is None:
        from . import config
        ruta = config.ROOT / "config.yaml"
    ruta = Path(ruta)
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError:
        return False
    t1, n1 = _RE_AUTO.subn(lambda m: f"{m.group(1)} {'true' if auto else 'false'}{m.group(2)}", texto, count=1)
    t2, n2 = _RE_DIAS.subn(lambda m: f"{m.group(1)} {dias}{m.group(2)}", t1, count=1)
    if n1 == 0 or n2 == 0:
        return False
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(t2, encoding="utf-8")
    tmp.replace(ruta)
    return True


def guarda_ultimo(dir_work, datos: dict) -> None:
    """Apunta lo último que se liberó (a mano o solo) en `work/espacio_ultimo.json`, para que «💾 Espacio» lo enseñe. Nunca lanza."""
    import json
    try:
        p = Path(dir_work) / "espacio_ultimo.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({**datos, "cuando": time.time()}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def lee_ultimo(dir_work) -> dict | None:
    import json
    try:
        d = json.loads((Path(dir_work) / "espacio_ultimo.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ medir
def medida(*, dir_work, outbox, inbox, filas, familias, detalle_de=None, en_marcha=(), ahora: float | None = None, dias: int = DIAS_DEF,
           obs_carpeta=None, por_dia: float = 1.0, umbral_obs: int = UMBRAL_OBS) -> dict:
    """Todo lo que enseña «💾 Espacio»: el disco, cuánto pesa cada carpeta y cada vídeo, el Historial, la carpeta de OBS, lo que se puede
    liberar ahora y a qué ritmo se llena el disco (con y sin borrar los vídeos a los `dias` días). `detalle_de(base)` -> {"pc": bytes que se
    llevaría el ✖ (todo lo del vídeo MENOS OBS), "obs": bytes de sus grabaciones de OBS (que se quedan)}. Ojo con la cuenta: lo que el programa
    guarda se queda fijo si se borra a los N días, pero las grabaciones de OBS no las toca y siguen creciendo."""
    ahora = time.time() if ahora is None else ahora
    dir_work, outbox, inbox = Path(dir_work), Path(outbox), Path(inbox)
    uso = shutil.disk_usage(dir_work)
    p = plan(dir_work=dir_work, filas=filas, familias=familias, en_marcha=en_marcha, ahora=ahora, dias=dias)
    vistos: set = set()
    carpetas = {"inbox": pesa(inbox, vistos), "videos": pesa(outbox, vistos), "work": pesa(dir_work, vistos)}
    h_bytes = sum(pesa(dir_work / x) for x in ("historial.json", "historial_miniaturas", "historial_transcripciones", "metricas_cache"))
    por_base = {f.get("base"): f for f in (filas or []) if f.get("base")}
    videos: list[dict] = []
    for fam in familias:
        base = fam["base"]
        fila = por_base.get(base)
        publicado = bool(fila and (fila.get("youtube") or {}).get("video_id"))
        try:
            d = (detalle_de(base) if detalle_de else None) or {}
        except Exception:                                          # noqa: BLE001
            d = {}
        ok, faltan = listo(fila, ahora, dias) if publicado else (False, None)
        videos.append({"base": base, "titulo": fam.get("titulo") or base, "publicado": publicado,
                       "estado": (fila or {}).get("estado_yt"), "pc": int(d.get("pc") or 0), "obs": int(d.get("obs") or 0),
                       "cache": pesa_lista(caches_de(dir_work, base)), "listo": ok, "faltan_dias": faltan,
                       "en_marcha": base in en_marcha})
        videos[-1]["total"] = videos[-1]["pc"] + videos[-1]["obs"]
    caches_b = sum(c["bytes"] for c in p["caches"])
    huerf_b = sum(c["bytes"] for c in p["caches"] if c["huerfano"])
    listos = [v for v in videos if v["listo"] and not v["en_marcha"]]
    pcs = [v["pc"] for v in videos if v["pc"] > 0]
    obss = [v["obs"] for v in videos if v["obs"] > 0]
    pc_video = int(statistics.median(pcs)) if pcs else PC_DEF            # lo que se lleva el ✖ de un vídeo…
    obs_video = int(statistics.median(obss)) if obss else (0 if pcs else OBS_DEF)   # …y lo que pesan sus grabaciones de OBS (sin ninguna encontrada: 0)
    por_video = pc_video + obs_video
    dia = max(1.0, por_dia * por_video)
    return {"ahora": ahora, "dias": dias, "dias_opciones": list(DIAS_OPCIONES),
            "disco": {"libre": uso.free, "total": uso.total, "usado": uso.total - uso.free},
            "carpetas": carpetas, "historial": {"bytes": h_bytes, "filas": len(filas or []),
                                                "por_fila": int(h_bytes / len(filas)) if filas else 0},
            "obs": _obs_total(obs_carpeta, umbral_obs), "videos": videos,
            "liberable": {"caches": caches_b, "n_caches": len(p["caches"]), "huerfanos": huerf_b,
                          "completos": sum(v["pc"] for v in listos), "n_completos": len(listos),
                          "lista": [{"base": v["base"], "titulo": v["titulo"], "pc": v["pc"]} for v in listos]},
            "ritmo": {"por_video": por_video, "pc_video": pc_video, "obs_video": obs_video, "estimado": not pcs, "por_dia": por_dia,
                      "dias_30": int(30 * dia), "dias_90": int(90 * dia), "dias_365": int(365 * dia),
                      "dias_para_llenar": int(uso.free / dia),
                      "estable_con_limpieza": int((dias + 1) * por_dia * pc_video),
                      "obs_30": int(30 * por_dia * obs_video), "obs_90": int(90 * por_dia * obs_video), "obs_365": int(365 * por_dia * obs_video),
                      "dias_para_llenar_obs": int(uso.free / (por_dia * obs_video)) if obs_video and por_dia > 0 else None}}


# ------------------------------------------------------------------ pruebas (sin tocar nada de verdad; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    def crea(p: Path, n: int = 1000, edad_s: float = 3600.0, contenido: bytes | None = None):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contenido if contenido is not None else b"x" * n)
        t = time.time() - edad_s
        os.utime(p, (t, t))

    ahora = time.time()
    dia = 86400.0
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        work, outbox, inbox = t / "work", t / "videos", t / "inbox"
        hist: list[dict] = []

        def video(base, estado=None, hace_dias=None, panel=True, caches=True):
            """estado: None = sin publicar; "publicado"/"programado"/"no_esta"…; hace_dias = cuándo salió (programado: dentro de cuántos)."""
            w = work / f"{base}--pro"
            crea(w / "words.json", 50)
            crea(w / "audio.wav", 4000)
            if caches:
                for nombre in CACHES_PESADAS:
                    crea(w / nombre / "a.bin", 3000)
                    crea(w / nombre / "sub" / "b.bin", 2000)
            if panel:
                crea(outbox / f"{base}--pro" / "pro.mp4", 10000)
            if estado:
                hist.append({"base": base, "titulo": f"Título {base}", "youtube": {"video_id": f"id_{base}", "publicado": ahora - 40 * dia},
                             "estado_yt": estado, "fecha_yt": ahora - (hace_dias or 0) * dia})

        video("A", "publicado", 2)                  # publicado hace 2 días: sus cachés sobran, él se queda 5 días más
        video("B", "publicado", 10)                 # publicado hace 10 días: se puede borrar entero
        video("C", "programado", -3)                # programado para dentro de 3 días: no se toca nada
        video("D")                                  # sin publicar: nada
        video("E", "no_esta", 30)                   # YouTube ya no lo devuelve: nada (la copia del PC podría ser la única)
        video("F", "publicado", 20)                 # publicado hace 20 días pero se está procesando: nada
        video("O1", None, panel=False)              # resto de un vídeo que ya no está en «Mis vídeos»: sus cachés pesadas sí
        video("O2", "publicado", 30, panel=False)   # ídem, publicado
        crea(work / "G--pro" / "escenas" / "a.bin", 3000, edad_s=60)               # una caché de hace 1 minuto
        hist.append({"base": "G", "titulo": "G", "youtube": {"video_id": "id_G", "publicado": ahora - dia / 2}, "estado_yt": "publicado", "fecha_yt": ahora - dia / 2})
        crea(outbox / "G--pro" / "pro.mp4", 10)
        crea(work / "zz-selftest--pro" / "escenas" / "a.bin", 100)                  # restos de «Probar el sistema»: no cuentan
        familias = [{"base": b, "titulo": f"Título {b}"} for b in "ABCDEFG"]
        pl = plan(dir_work=work, filas=hist, familias=familias, en_marcha={"F"}, ahora=ahora, dias=7)
        bases_c = sorted({c["base"] for c in pl["caches"]})
        caso("el plan: cachés de A (publicado, dentro del plazo) y de los restos O1 y O2; B se va entero; C (programado), D (sin publicar), E (no_esta), "
             "F (en marcha), la caché de hace 1 minuto y los zz- no se tocan",
             bases_c == ["A", "O1", "O2"] and [c["base"] for c in pl["completos"]] == ["B"]
             and sorted(x["base"] for x in pl["pendientes"]) == ["A", "C", "G"] and sorted(x["base"] for x in pl["sin_publicar"]) == ["D", "E"]
             and all(c["ruta"].name in CACHES_PESADAS for c in pl["caches"]), (bases_c, pl["completos"], pl["pendientes"], pl["sin_publicar"]))
        caso("faltan los días justos: A (2 de 7) 5; el programado cuenta desde que SALE (3 días + 7 = 10); uno publicado hace 7 días ya está listo",
             {x["base"]: x["faltan_dias"] for x in pl["pendientes"]}["A"] == 5 and {x["base"]: x["faltan_dias"] for x in pl["pendientes"]}["C"] == 10
             and listo({"youtube": {"video_id": "x"}, "estado_yt": "publicado", "fecha_yt": ahora - 7 * dia}, ahora, 7)[0]
             and not listo({"youtube": {"video_id": "x"}, "estado_yt": "publicado", "fecha_yt": ahora - 6 * dia}, ahora, 7)[0]
             and not listo({"youtube": {}, "estado_yt": "publicado", "fecha_yt": ahora - 30 * dia}, ahora, 7)[0])
        caso("con un plazo menor, A también se va entero; con uno mayor, B espera",
             sorted(c["base"] for c in plan(dir_work=work, filas=hist, familias=familias, en_marcha={"F"}, ahora=ahora, dias=1)["completos"]) == ["A", "B"]
             and plan(dir_work=work, filas=hist, familias=familias, en_marcha={"F"}, ahora=ahora, dias=20)["completos"] == [])

        llamadas: list = []

        def borra(base):
            llamadas.append(base)
            return {"ok": True, "liberado": 123, "papelera": 100}

        antes = {x: pesa(work / f"{x}--pro") for x in ("C", "D", "E", "F")}
        r1 = ejecuta(pl, dir_work=work, borra_completo=borra, en_marcha={"F"})
        caso("liberar: las cachés pesadas de A, O1 y O2 se borran DE VERDAD (y de A queda su transcripción, su audio y su vídeo); B se borra con el mismo 🗑 de "
             "«Mis vídeos»; C, D, E, F y la caché de hace 1 minuto siguen igual",
             not (work / "A--pro" / "escenas").exists() and (work / "A--pro" / "words.json").is_file() and (work / "A--pro" / "audio.wav").is_file()
             and (outbox / "A--pro" / "pro.mp4").is_file() and not (work / "O1--pro" / "musica").exists() and not (work / "O2--pro" / "ilustraciones").exists()
             and (work / "O2--pro" / "words.json").is_file() and llamadas == ["B"]
             and all(pesa(work / f"{x}--pro") == antes[x] for x in ("C", "D", "E", "F")) and (work / "G--pro" / "escenas").is_dir()
             and not r1["errores"] and r1["bases"] == ["B"] and r1["completos"] == 1, r1)
        caso("lo liberado se cuenta bien (3 vídeos × 3 cachés × 5.000 B = 45.000 B; lo de B, lo que diga el 🗑)",
             r1["cache"] == 3 * 3 * 5000 and r1["liberado_completos"] == 123 and r1["papelera"] == 100, r1)
        pl2 = plan(dir_work=work, filas=hist, familias=familias, en_marcha={"F"}, ahora=ahora, dias=7)
        r2 = ejecuta(pl2, dir_work=work, borra_completo=borra, en_marcha={"F"}, solo_caches=True)
        caso("una segunda vez no hay nada que liberar en cachés (no repite ni rompe), y «solo cachés» no borra vídeos", r2["cache"] == 0 and not r2["bases"] and llamadas == ["B"], r2)
        r3 = ejecuta({"caches": [], "completos": [{"base": "B"}, {"base": "X"}]}, dir_work=work,
                     borra_completo=lambda b: {"ok": False, "error": "Ese vídeo se está procesando"} if b == "B" else (_ for _ in ()).throw(RuntimeError("boom")),
                     en_marcha=())
        caso("si el 🗑 de un vídeo falla (o lanza), se apunta el motivo y se sigue con el siguiente", len(r3["errores"]) == 2 and not r3["bases"], r3)

        # ---- seguridad: nunca se borra algo que no sea una caché pesada
        crea(work / "H--pro" / "escenas" / "a.bin", 100)
        crea(work / "H--pro" / "words.json", 10)
        crea(t / "otro" / "escenas" / "x.bin", 100)
        crea(work / "H--pro-v2" / "escenas" / "a.bin", 100)
        crea(work / "H--pro" / "musica_fondo" / "a.bin", 100)
        malas = [work / "H--pro" / "words.json", t / "otro" / "escenas", work / "H--pro-v2" / "escenas", work / "H--pro" / "musica_fondo", work / "H--pro" / "tarjetas"]
        rm = borra_caches(malas, work, min_edad_s=0)
        caso("lo que no es `work/<vídeo>--pro/<escenas|ilustraciones|musica>` (un archivo, una carpeta de fuera, la de una versión, otra carpeta) NO se toca",
             (work / "H--pro" / "words.json").is_file() and (t / "otro" / "escenas" / "x.bin").is_file() and (work / "H--pro-v2" / "escenas" / "a.bin").is_file()
             and (work / "H--pro" / "musica_fondo" / "a.bin").is_file() and len(rm["errores"]) == 5 and rm["bytes"] == 0, rm)
        rm2 = borra_caches([work / "H--pro" / "escenas"], work, en_marcha={"H"}, min_edad_s=0)
        caso("la caché de un vídeo en marcha no se toca", (work / "H--pro" / "escenas").is_dir() and rm2["errores"], rm2)
        rm3 = borra_caches([work / "H--pro" / "escenas"], work)           # (con la edad: recién creada con 1 h de antigüedad → se borra)
        caso("con la edad por defecto una caché de hace 1 h sí se borra y una de hace 1 minuto no", rm3["borradas"] == 1
             and borra_caches([work / "G--pro" / "escenas"], work)["saltadas"] == 1 and (work / "G--pro" / "escenas").is_dir(), rm3)

        # ---- borrar de verdad lo de un vídeo (el ✖ de «Mis vídeos»): solo dentro de videos/, work/ e inbox/; nunca OBS
        raiz_b = t / "borra"
        pv, pw, pi = raiz_b / "videos", raiz_b / "work", raiz_b / "inbox"
        crea(pv / "X--pro" / "pro.mp4", 4000)
        crea(pv / "X--pro" / "sub" / "a.bin", 100)
        crea(pw / "X--pro" / "words.json", 50)
        crea(pi / "X.mkv", 6000)
        os.chmod(pi / "X.mkv", stat.S_IREAD)                                  # solo lectura (un archivo así no debe impedir borrarlo)
        obs_b = raiz_b / "obs"
        crea(obs_b / "2026-10-02 10-00-00.mkv", 7000)
        rb = borra_rutas([pv / "X--pro", pw / "X--pro", pi / "X.mkv", pw / "no-existe"], (pv, pw, pi))
        caso("borrar de verdad: se va todo lo de ese vídeo (carpetas con subcarpetas y un archivo de solo lectura), cuenta los bytes y lo que ya no existe se ignora",
             not (pv / "X--pro").exists() and not (pw / "X--pro").exists() and not (pi / "X.mkv").exists() and rb["borradas"] == 3
             and rb["bytes"] == 4000 + 100 + 50 + 6000 and not rb["errores"], rb)
        rb2 = borra_rutas([obs_b / "2026-10-02 10-00-00.mkv", pv, pw, t], (pv, pw, pi))
        caso("borrar de verdad: lo que está FUERA de videos/, work/ e inbox/ (tus grabaciones de OBS), las carpetas mismas y cualquier otra ruta NO se tocan",
             (obs_b / "2026-10-02 10-00-00.mkv").is_file() and pv.is_dir() and pw.is_dir() and t.is_dir() and len(rb2["errores"]) == 4 and rb2["bytes"] == 0, rb2)
        crea(pi / "Y.mkv", 3000)
        try:
            os.link(pi / "Y.mkv", pi / "Y-2.mkv")
            enlace = True
        except OSError:
            enlace = False
        if enlace:
            borra_rutas([pi / "Y.mkv"], (pv, pw, pi))
            caso("un archivo con otro nombre (enlace duro, como las subidas repetidas) solo pierde el nombre que se borra: el otro sigue entero",
                 not (pi / "Y.mkv").exists() and (pi / "Y-2.mkv").stat().st_size == 3000)
        crea(obs_b / "fuera.mkv", 500)
        try:
            os.symlink(obs_b, pv / "enlace-a-obs", target_is_directory=True)
            simbolico = True
        except (OSError, NotImplementedError):
            simbolico = False                                                  # (Windows sin permiso para crear enlaces simbólicos: nada que probar)
        if simbolico:
            rb3 = borra_rutas([pv / "enlace-a-obs"], (pv, pw, pi))
            caso("un enlace que apunta FUERA (a la carpeta de OBS, por ejemplo) no se sigue ni se borra su contenido", (obs_b / "fuera.mkv").is_file() and rb3["errores"], rb3)

        # ---- las grabaciones de OBS
        obs = t / "obs"
        inbox.mkdir(exist_ok=True)
        cuerpo = bytes(range(256)) * 2000                                   # 512.000 B (más de 2 × 65.536: se compara el principio y el final)
        crea(obs / "2026-10-02 13-34-40.mkv", contenido=cuerpo)
        crea(obs / "2026-10-02 13-34-40 camara.mkv", 6000)
        crea(obs / "2026-10-02 13-55-17.mkv", contenido=cuerpo[::-1])      # otra grabación del mismo tamaño pero distinta
        crea(obs / "prueba.mkv", 10)
        crea(inbox / "2026-10-02_13-34-40.mkv", contenido=cuerpo)           # la copia de inbox (el panel cambia el espacio por «_»)
        crea(inbox / "2026-10-02_13-34-40-2.mkv", contenido=cuerpo)         # una subida repetida
        camara = lambda g: obs / "2026-10-02 13-34-40 camara.mkv"           # noqa: E731 — `camara_aparte.busca` de mentira
        o = obs_de([inbox / "2026-10-02_13-34-40.mkv"], obs, busca_camara=camara)
        caso("OBS: la grabación original (mismo nombre, tamaño, principio y final) y su cámara aparte; no la de otra hora ni las pruebas",
             sorted(f.name for f in o["archivos"]) == ["2026-10-02 13-34-40 camara.mkv", "2026-10-02 13-34-40.mkv"] and o["bytes"] == len(cuerpo) + 6000
             and not o["compartida"], o)
        o2 = obs_de([inbox / "2026-10-02_13-34-40.mkv"], obs, otras_fuentes=[str(inbox / "2026-10-02_13-34-40-2.mkv")], busca_camara=camara)
        caso("OBS: si otro vídeo viene de la misma grabación (la subida repetida), no se toca nada de OBS", not o2["archivos"] and o2["compartida"], o2)
        crea(inbox / "2026-10-02_13-55-17.mkv", contenido=cuerpo)           # mismo nombre y tamaño que el de OBS, pero NO es la misma grabación
        o3 = obs_de([inbox / "2026-10-02_13-55-17.mkv"], obs, busca_camara=lambda g: None)
        caso("OBS: mismo nombre y tamaño pero otro contenido = otra grabación: no se toca", not o3["archivos"], o3)
        fuera = t / "otra-carpeta" / "2026-10-02 13-34-40 camara.mkv"
        crea(fuera, 10)
        o4 = obs_de([inbox / "2026-10-02_13-34-40.mkv"], obs, busca_camara=lambda g: fuera)
        caso("OBS: una cámara que no esté DIRECTAMENTE en la carpeta de OBS no cuenta", [f.name for f in o4["archivos"]] == ["2026-10-02 13-34-40.mkv"], o4)
        caso("OBS: sin carpeta, o sin nombre con fecha, no pasa nada", not obs_de([inbox / "x.mkv"], obs)["archivos"] and not obs_de([inbox / "2026-10-02_13-34-40.mkv"], None)["archivos"]
             and not obs_de([inbox / "2026-10-02_13-34-40.mkv"], t / "no-existe")["archivos"])

        # ---- medir
        def detalle(base):
            return {"pc": 5000 if base in "ABC" else 3000, "obs": 2000 if base in "AB" else 0}

        med = medida(dir_work=work, outbox=outbox, inbox=inbox, filas=hist, familias=familias, detalle_de=detalle, en_marcha={"F"}, ahora=ahora, dias=7,
                     obs_carpeta=obs, umbral_obs=1000)
        vs = {v["base"]: v for v in med["videos"]}
        caso("medir: el disco, las carpetas, el Historial, cada vídeo (con su caché, lo que se lleva el 🗑 y sus días), OBS (solo los grandes) y lo liberable",
             med["disco"]["libre"] > 0 and med["carpetas"]["work"] > 0 and med["historial"]["filas"] == len(hist) and set(vs) == set("ABCDEFG")
             and vs["B"]["listo"] and vs["B"]["total"] == 7000 and vs["A"]["faltan_dias"] == 5 and not vs["D"]["publicado"] and vs["F"]["en_marcha"]
             and vs["F"]["listo"] and med["obs"]["grandes"] == 3
             and med["liberable"]["n_completos"] == 1 and med["liberable"]["completos"] == 5000, (med["liberable"], vs["B"]))
        caso("medir: el ritmo (la mediana de lo que se lleva el ✖ y de lo que pesa su OBS), la proyección a 30/90/365 días, cuánto aguanta el disco, lo que se queda fijo "
             "borrando a los 7 días (sin OBS) y lo que SIGUE creciendo la carpeta de OBS",
             med["ritmo"]["dias_90"] == 3 * med["ritmo"]["dias_30"] and med["ritmo"]["dias_365"] > med["ritmo"]["dias_90"]
             and med["ritmo"]["dias_para_llenar"] > 0 and med["ritmo"]["estable_con_limpieza"] == 8 * med["ritmo"]["pc_video"] and not med["ritmo"]["estimado"]
             and med["ritmo"]["pc_video"] == 3000 and med["ritmo"]["obs_video"] == 2000 and med["ritmo"]["por_video"] == 5000
             and med["ritmo"]["obs_90"] == 3 * med["ritmo"]["obs_30"] == 180000 and med["ritmo"]["dias_para_llenar_obs"] > med["ritmo"]["dias_para_llenar"], med["ritmo"])
        vacio = medida(dir_work=work, outbox=outbox, inbox=inbox, filas=[], familias=[], ahora=ahora)
        caso("sin vídeos ni carpeta de OBS: estima con lo medido al autor y no rompe", vacio["obs"] is None and vacio["ritmo"]["estimado"]
             and vacio["ritmo"]["por_video"] == PC_DEF + OBS_DEF and vacio["ritmo"]["obs_video"] == OBS_DEF and vacio["historial"]["por_fila"] == 0)
    caso("un enlace duro (el mismo archivo con dos nombres) cuenta una vez", _prueba_enlace())
    # ---- las opciones de config.yaml
    with tempfile.TemporaryDirectory() as tmp:
        y = Path(tmp) / "config.yaml"
        y.write_text("videos:\n  ia_ve_video: true\n  limpieza_auto: false   # apagada\n  limpieza_dias: 7       # días\n  intro: true\n", encoding="utf-8")
        ok1 = guarda_opciones(True, 14, y)
        txt = y.read_text(encoding="utf-8")
        caso("guardar las opciones toca SOLO esas dos líneas y deja sus comentarios",
             ok1 and "  limpieza_auto: true   # apagada" in txt and "  limpieza_dias: 14       # días" in txt and "ia_ve_video: true" in txt and "  intro: true" in txt, txt)

        class _Cfg:
            def __init__(self, d):
                self.d = d

            def get_path(self, k, default=None):
                return self.d.get(k, default)

        antes_txt = y.read_text(encoding="utf-8")
        valores_malos = (not guarda_opciones(True, 0, y) and not guarda_opciones(True, 400, y) and not guarda_opciones("si", 7, y)
                         and not guarda_opciones(True, True, y) and y.read_text(encoding="utf-8") == antes_txt)
        y.write_text("videos:\n  limpieza_auto: false\n", encoding="utf-8")
        sin_la_otra_linea = not guarda_opciones(True, 7, y) and y.read_text(encoding="utf-8") == "videos:\n  limpieza_auto: false\n"
        caso("valores que no valen (días raros, no booleano) o una línea que falta no cambian nada", valores_malos and sin_la_otra_linea,
             (valores_malos, sin_la_otra_linea))
        caso("leer las opciones: apagada y 7 días si no están; los días se acotan",
             opciones(_Cfg({})) == (False, DIAS_DEF) and opciones(_Cfg({"videos.limpieza_auto": True, "videos.limpieza_dias": 14})) == (True, 14)
             and opciones(_Cfg({"videos.limpieza_dias": 9999}))[1] == 365 and opciones(_Cfg({"videos.limpieza_dias": "x"}))[1] == DIAS_DEF)
        guarda_ultimo(tmp, {"origen": "manual", "cache": 5})
        u = lee_ultimo(tmp)
        caso("lo último que se liberó se guarda y se lee (y sin archivo, nada)", u and u["origen"] == "manual" and u["cache"] == 5 and u["cuando"] > 0
             and lee_ultimo(Path(tmp) / "no-existe") is None, u)
    return filas


def _prueba_enlace() -> bool:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.bin"
        a.write_bytes(b"x" * 1000)
        try:
            os.link(a, Path(tmp) / "b.bin")
        except OSError:
            return True                                            # (sin enlaces duros en este disco: nada que probar)
        return pesa(tmp) == 1000
