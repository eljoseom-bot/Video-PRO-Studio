"""La cadena de modelos de Gemini POR CÓDIGO: sin nombres a mano, para que siga funcionando cuando salga el modelo de mañana.

Cómo se arma (todo automático, nadie tiene que tocar nada):
1. Se le pregunta a Google qué modelos ofrece (`models.list`, cada 12 h y al lanzar un análisis; se guarda en `work/modelos.json`).
2. Se quedan los que sirven para el director: familia **Flash** y **Flash-Lite** (los Pro no son gratis), de la versión 3 en adelante, sin los de
   imagen/voz/audio/robótica y sin las «previews» que ya tienen versión estable.
3. Se ordenan del MEJOR al peor: primero los Flash (la versión más nueva primero), luego los Flash-Lite igual, y al final los alias `…-latest`
   como red de seguridad. Un modelo nuevo (gemini-4-flash, gemini-3.9-flash…) entra solo y se coloca donde le toca por su número; una «preview»
   compite por su número como cualquiera (así sale la generación nueva: primero en preview) y si ya existe la estable de su misma versión se
   usa la estable. `ai.incluir_preview: false` las quita todas.
4. Se aparta —sin borrarlo— al que AHORA no sirve, mirando el registro de uso: **saturado** (503/timeout: unos minutos), **agotado** (Google dijo
   que se acabó su cuota DIARIA: hasta que se reinicie el día) o **no disponible** (404/403: 24 h). Así el mejor modelo saturado no hace perder
   tiempo ni peticiones en cada vídeo, y en cuanto se recupera vuelve a ser el primero.
Si no se puede preguntar a Google (sin red o sin clave), se usa `ai.director_modelos` de config.yaml como plan B.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ORDEN_FAMILIA = {"flash": 0, "lite": 1, "pro": 2}
# de imagen, voz, audio, vídeo, robótica, agentes de ordenador, transcripción, modelos abiertos y experimentos: no sirven para el director
_EXCLUIR = ("image", "tts", "audio", "live", "transcribe", "embedding", "embed", "omni", "robotics", "computer", "customtools", "imagen", "veo",
            "aqa", "learnlm", "gemma", "vision", "exp", "thinking", "translate")
_RE_VERSION = re.compile(r"^gemini-(\d+(?:\.\d+)*)-")
ENFRIAMIENTO_MIN = 10          # cuánto se aparta a un modelo saturado antes de volver a probarlo
DESCUBRIR_CADA_H = 12


def _dir_work(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work)
    from . import config
    return config.paths()["work"]


def ruta_cache(dir_work=None) -> Path:
    return _dir_work(dir_work) / "modelos.json"


def _cfg(cfg, clave, defecto):
    try:
        v = cfg.get_path(clave, defecto) if cfg is not None else defecto
    except Exception:                                   # noqa: BLE001
        v = defecto
    return defecto if v is None else v


# ------------------------------------------------------------------ nombres → familia y versión
def analiza(model_id: str) -> dict | None:
    """{'id', 'familia': flash|lite|pro, 'version': (3, 8), 'preview': bool, 'alias': bool}; None si no es un modelo de texto de Gemini."""
    m = str(model_id or "").lower().replace("models/", "").strip()
    if not m.startswith("gemini-") or any(t in m for t in _EXCLUIR):
        return None
    if "flash-lite" in m or "flash_lite" in m:
        fam = "lite"
    elif "flash" in m:
        fam = "flash"
    elif "-pro" in m:
        fam = "pro"
    else:
        return None
    mv = _RE_VERSION.match(m)
    version = tuple(int(x) for x in mv.group(1).split(".")) if mv else ()
    return {"id": m, "familia": fam, "version": version, "preview": "preview" in m, "alias": m.endswith("-latest")}


def _tupla_version(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split(".") if x != "") or (3,)
    except ValueError:
        return (3,)


def ordena(ids, familias=("flash", "lite"), version_minima=3, incluir_preview=True) -> list[str]:
    """Del mejor al peor. Ver la cabecera del módulo para las reglas."""
    piso = _tupla_version(version_minima)
    infos = [i for i in (analiza(x) for x in ids) if i and i["familia"] in familias and (i["alias"] or i["version"] >= piso)
             and (incluir_preview or not i["preview"])]
    estables = {(i["familia"], i["version"]) for i in infos if not i["preview"] and not i["alias"]}
    infos = [i for i in infos if not (i["preview"] and (i["familia"], i["version"]) in estables)]      # una preview con hermana estable no aporta

    def clave(i):
        v = (tuple(i["version"]) + (0, 0, 0))[:3]
        return (ORDEN_FAMILIA[i["familia"]], tuple(-x for x in v), i["preview"], i["id"])       # familia, luego la versión más nueva; la preview solo desempata
    normales = sorted((i for i in infos if not i["alias"]), key=clave)
    alias = sorted((i for i in infos if i["alias"]), key=lambda i: (ORDEN_FAMILIA[i["familia"]], i["id"]))
    return list(dict.fromkeys(i["id"] for i in normales + alias))


def no_reconocidos(ids) -> list[str]:
    """Modelos «gemini-…» de texto cuya familia no sé ordenar (Google inventó un nombre nuevo, p. ej. «gemini-4-spark»): no se usan, pero se avisa
    en «Uso». Lo que ya se sabe que no sirve para el director (imagen, voz, robótica…) no cuenta."""
    fuera = set()
    for x in ids or []:
        m = str(x).lower().replace("models/", "").strip()
        if m.startswith("gemini-") and analiza(m) is None and not any(t in m for t in _EXCLUIR):
            fuera.add(m)
    return sorted(fuera)


# ------------------------------------------------------------------ preguntarle a Google (y guardarlo)
def descubre(client) -> list[str]:
    """Los modelos que Google ofrece a esta clave y que generan texto (`generateContent`)."""
    ids = []
    for m in client.models.list():
        if "generateContent" in (getattr(m, "supported_actions", None) or []):
            ids.append(str(m.name).replace("models/", ""))
    return ids


def lee_cache(dir_work=None) -> dict | None:
    try:
        d = json.loads(ruta_cache(dir_work).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("ids"), list) else None
    except (OSError, ValueError):
        return None


def guarda_cache(ids, dir_work=None, ahora=None) -> None:
    try:
        ruta = ruta_cache(dir_work)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({"ts": ahora or time.time(), "ids": list(ids)}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------ cómo está cada modelo AHORA (según el registro de uso)
def salud(dir_work=None, ahora: float | None = None, enfria_min: float = ENFRIAMIENTO_MIN) -> dict:
    """{modelo: {'estado': saturado|agotado|no_disponible, 'hasta': ts}} solo de los que hoy NO sirven; un éxito posterior lo cura."""
    from . import uso
    ahora = ahora or time.time()
    hoy = uso.dia_cuota(ahora)
    por: dict = {}
    for f in uso.lee(dir_work):
        if f["ts"] >= ahora - 26 * 3600 and f["modelo"]:
            por.setdefault(f["modelo"], []).append(f)
    out: dict = {}
    for m, filas in por.items():
        ultimo_ok = max((f["ts"] for f in filas if f["estado"] == "ok"), default=0.0)
        malas = [f for f in filas if f["estado"] != "ok" and f["ts"] > ultimo_ok]
        if not malas:
            continue
        if any(f["estado"] == "cuota" and f.get("limite") == "rpd" and uso.dia_cuota(f["ts"]) == hoy for f in malas):
            out[m] = {"estado": "agotado", "hasta": uso.proximo_reinicio(ahora)}
        elif any(f["estado"] == "no_disponible" and f["ts"] > ahora - 24 * 3600 for f in malas):
            out[m] = {"estado": "no_disponible", "hasta": max(f["ts"] for f in malas if f["estado"] == "no_disponible") + 24 * 3600}
        elif ahora - malas[-1]["ts"] < enfria_min * 60:
            out[m] = {"estado": "saturado", "hasta": malas[-1]["ts"] + enfria_min * 60}
    return out


def _por_salud(cadena: list[str], sal: dict) -> list[str]:
    """Los sanos primero (en su orden), luego los saturados, luego los agotados y al final los no disponibles: nunca queda vacía."""
    def peso(m):
        return {"saturado": 1, "agotado": 2, "no_disponible": 3}.get((sal.get(m) or {}).get("estado"), 0)
    return sorted(cadena, key=lambda m: peso(m))                # (sorted es estable: dentro de cada grupo se conserva el orden del mejor al peor)


# ------------------------------------------------------------------ LA CADENA
def cadena_base(cfg=None, client=None, ahora: float | None = None, dir_work=None, forzar: bool = False) -> list[str]:
    """La cadena ordenada del mejor al peor, sin tener en cuenta cómo está cada uno ahora."""
    semilla = _cfg(cfg, "ai.director_modelos", [])
    semilla = [semilla] if isinstance(semilla, str) else [str(m).strip() for m in semilla if str(m).strip()]
    if not _cfg(cfg, "ai.modelos_auto", True):
        return semilla or [str(_cfg(cfg, "ai.model", "gemini-3.1-flash-lite"))]
    ahora = ahora or time.time()
    cache = lee_cache(dir_work)
    ids = cache["ids"] if cache else None
    caducada = (not cache) or ahora - float(cache.get("ts") or 0) > float(_cfg(cfg, "ai.descubrir_cada_horas", DESCUBRIR_CADA_H)) * 3600
    if client is not None and (forzar or caducada):
        try:
            ids = descubre(client)
            guarda_cache(ids, dir_work, ahora)
        except Exception:                                   # noqa: BLE001 — sin red o sin clave: se sigue con lo último que se supo
            pass
    base = ordena(ids or [], tuple(_cfg(cfg, "ai.familias", ["flash", "lite"])), _cfg(cfg, "ai.version_minima", 3),
                  bool(_cfg(cfg, "ai.incluir_preview", True))) if ids else []
    return base or semilla or [str(_cfg(cfg, "ai.model", "gemini-3.1-flash-lite"))]


def cadena(cfg=None, client=None, ahora: float | None = None, dir_work=None, forzar: bool = False) -> list[str]:
    """Los modelos a probar EN ORDEN: del mejor al peor y con los que ahora no sirven al final."""
    ahora = ahora or time.time()
    base = cadena_base(cfg, client, ahora, dir_work, forzar)
    try:
        return _por_salud(base, salud(dir_work, ahora, float(_cfg(cfg, "ai.enfriamiento_min", ENFRIAMIENTO_MIN))))
    except Exception:                                       # noqa: BLE001
        return base


def barato(cfg=None, client=None, ahora: float | None = None, dir_work=None) -> str:
    """El mejor modelo de la familia BARATA (Flash-Lite: 500 peticiones al día) que ahora sirve. Para los trabajos con MUCHAS llamadas pequeñas
    (etiquetar los tramos de un directo largo en el «Generador por temáticas»): con los Flash buenos (20 al día) se comerían la cuota de los vídeos.
    Sale de la misma búsqueda automática, así que cuando salga un Flash-Lite mejor se usa solo. Con `ai.modelos_auto` apagado, `ai.model`."""
    fijo = str(_cfg(cfg, "ai.model", "gemini-3.1-flash-lite"))
    if not _cfg(cfg, "ai.modelos_auto", True):
        return fijo
    for m in cadena(cfg, client, ahora, dir_work):
        a = analiza(m)
        if a and a["familia"] == "lite" and not a["alias"]:
            return m
    return fijo


def cadena_lite(cfg=None, client=None) -> list[str]:
    """Los Flash-Lite que sirven hoy, del mejor al peor (500 peticiones al día: no tocan la cuota de los Flash buenos). Para
    lo que NO es el análisis del vídeo y no debe quitarle cuota: «La IA ve el vídeo» y «↻ Otros 3 títulos»."""
    try:
        todos = cadena(cfg, client)
    except Exception:  # noqa: BLE001
        todos = []
    lite = [m for m in todos if (analiza(m) or {}).get("familia") == "lite" and not (analiza(m) or {}).get("alias")]
    return lite or [barato(cfg, client)]


def info(cfg=None, dir_work=None) -> dict:
    """Para la pestaña «Uso»: si la búsqueda es automática y cuándo se hizo por última vez."""
    c = lee_cache(dir_work)
    return {"auto": bool(_cfg(cfg, "ai.modelos_auto", True)), "buscado": (c or {}).get("ts"), "descubiertos": len((c or {}).get("ids") or []),
            "cada_horas": float(_cfg(cfg, "ai.descubrir_cada_horas", DESCUBRIR_CADA_H)), "sin_orden": no_reconocidos((c or {}).get("ids"))}


def para_prueba(cfg=None, dir_work=None, n: int = 3) -> list[str]:
    """Modelos para comprobar una clave con UNA petición: primero los Flash-Lite (500 al día) y luego el mejor Flash."""
    base = cadena_base(cfg, None, None, dir_work)
    lite = [m for m in base if (analiza(m) or {}).get("familia") == "lite" and not (analiza(m) or {}).get("alias")]
    flash = [m for m in base if (analiza(m) or {}).get("familia") == "flash" and not (analiza(m) or {}).get("alias")]
    return (lite[:2] + flash[:1] + base)[:max(1, n)] if base else ["gemini-3.1-flash-lite"]


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    from types import SimpleNamespace
    from . import uso
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    # la lista REAL que Google devolvió a la clave del autor el 2026-09-21 (solo los que generan texto)
    REAL = ["gemini-2.5-computer-use-preview-10-2025", "gemini-2.5-flash", "gemini-2.5-flash-image", "gemini-2.5-flash-lite", "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro", "gemini-2.5-pro-preview-tts", "gemini-3-flash-preview", "gemini-3-pro-image", "gemini-3-pro-image-preview",
            "gemini-3.1-flash-image", "gemini-3.1-flash-image-preview", "gemini-3.1-flash-lite", "gemini-3.1-flash-lite-image",
            "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-tts-preview", "gemini-3.1-pro-preview", "gemini-3.1-pro-preview-customtools",
            "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.5-transcribe", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
            "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-omni-1.1-flash", "gemini-omni-flash-preview", "gemini-pro-latest",
            "gemini-robotics-er-2-preview"]
    ESPERADA = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash-preview",
                "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-flash-lite-latest"]
    orden = ordena(REAL)
    caso("con la lista REAL de Google: solo Flash y Flash-Lite de la 3 en adelante, del mejor al peor, sin imagen/voz/Pro/versión 2 ni la preview repetida",
         orden == ESPERADA, orden)
    caso("los nombres se entienden: familia, versión, preview y alias; lo que no es un modelo de texto de Gemini se descarta",
         analiza("models/gemini-3.8-flash")["version"] == (3, 8) and analiza("gemini-3.1-flash-lite-preview")["preview"] and analiza("gemini-3.1-flash-lite-preview")["familia"] == "lite"
         and analiza("gemini-flash-latest")["alias"] and analiza("gemini-3.1-pro-preview")["familia"] == "pro" and analiza("gemini-3.5-flash-lite")["familia"] == "lite"
         and all(analiza(x) is None for x in ("gemini-3.1-flash-tts-preview", "gemini-3.5-transcribe", "gemma-3-27b", "gemini-omni-1.1-flash", "text-embedding-004", "gemini-3-pro-image")))
    futuro = ordena(REAL + ["gemini-4-flash", "gemini-4.1-flash-lite", "gemini-3.9-flash", "gemini-4.2-flash-preview", "gemini-4-flash-preview", "gemini-4-pro", "gemini-5-flash-lite-preview"])
    caso("el modelo de MAÑANA entra solo y se coloca por su número, aunque salga en «preview» (así salen las generaciones nuevas): 4.2 preview, 4, 3.9, 3.8…; la preview que ya tiene estable desaparece; el Pro fuera",
         futuro[:4] == ["gemini-4.2-flash-preview", "gemini-4-flash", "gemini-3.9-flash", "gemini-3.8-flash"] and "gemini-4-flash-preview" not in futuro
         and futuro.index("gemini-3-flash-preview") == futuro.index("gemini-3.5-flash") + 1 and "gemini-4-pro" not in futuro
         and futuro.index("gemini-5-flash-lite-preview") == futuro.index("gemini-3-flash-preview") + 1
         and futuro.index("gemini-4.1-flash-lite") < futuro.index("gemini-3.5-flash-lite"), futuro)
    caso("con ai.incluir_preview apagado no entra ninguna preview (ni la nueva ni la vieja)",
         ordena(REAL + ["gemini-4-flash-preview"], incluir_preview=False)[0] == "gemini-3.8-flash"
         and not any("preview" in m for m in ordena(REAL + ["gemini-4-flash-preview", "gemini-5-flash-lite-preview"], incluir_preview=False)))
    caso("los Pro solo entran si el autor los permite (ai.familias) y la versión mínima se puede subir",
         "gemini-3.1-pro-preview" in ordena(REAL, ("flash", "lite", "pro")) and "gemini-3.5-flash" not in ordena(REAL, version_minima=3.6)
         and ordena(REAL, version_minima=3.6)[0] == "gemini-3.8-flash")

    caso("si Google inventa un nombre que no sé ordenar (gemini-4-spark) no lo uso pero lo aviso; lo que ya se sabe que no sirve (imagen, voz, robótica…) no da falsas alarmas",
         no_reconocidos(REAL + ["gemini-4-spark", "models/gemini-5-turbo"]) == ["gemini-4-spark", "gemini-5-turbo"] and no_reconocidos(REAL) == [] and no_reconocidos(None) == [])

    class Cfg:
        def __init__(self, **d):
            self.d = d

        def get_path(self, k, dflt=None):
            return self.d.get(k, dflt)

    class Cliente:
        """Un «Google» de mentira: cuenta cuántas veces se le pregunta y puede fallar."""
        def __init__(self, ids=None, falla=False):
            self.llamadas = 0
            lista, fuera = list(ids or REAL), self

            class _Modelos:
                def list(self):
                    fuera.llamadas += 1
                    if falla:
                        raise RuntimeError("sin red")
                    return [SimpleNamespace(name="models/" + i, supported_actions=["generateContent"]) for i in lista]
            self.models = _Modelos()

    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.time()
        semilla = Cfg(**{"ai.director_modelos": ["gemini-x-semilla", "gemini-y-semilla"]})
        caso("sin poder preguntar a Google (sin cliente ni caché) se usa la lista de config.yaml como plan B",
             cadena(semilla, None, t0, tmp) == ["gemini-x-semilla", "gemini-y-semilla"])
        cli = Cliente()
        c1 = cadena(semilla, cli, t0, tmp)
        c2 = cadena(semilla, cli, t0 + 3600, tmp)
        caso("pregunta a Google, guarda la respuesta y durante 12 h no vuelve a preguntar", c1 == ESPERADA and c2 == ESPERADA and cli.llamadas == 1, (cli.llamadas, c1))
        c3 = cadena(semilla, cli, t0 + 13 * 3600, tmp)
        caso("pasadas 12 h vuelve a preguntar (así se entera del modelo nuevo)", cli.llamadas == 2 and c3 == ESPERADA)
        nuevo = Cliente(REAL + ["gemini-4-flash"])
        caso("cuando Google ofrece uno nuevo, la siguiente búsqueda lo pone el primero", cadena(semilla, nuevo, t0 + 26 * 3600, tmp)[0] == "gemini-4-flash" and info(semilla, tmp)["descubiertos"] == len(REAL) + 1)
        caso("si Google no contesta se sigue con lo último que se supo", cadena(semilla, Cliente(falla=True), t0 + 60 * 3600, tmp)[0] == "gemini-4-flash")
        caso("los modelos de prueba de una clave son los Flash-Lite primero (500 al día)", para_prueba(semilla, tmp)[:2] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"] and para_prueba(semilla, tmp)[2] == "gemini-4-flash")
        # --- cómo está cada uno AHORA
        h = time.time()
        uso.registra("gemini-4-flash", 0, 0, 0, 14, "analisis", estado="saturado", dir_work=tmp, ts=h - 120)
        uso.registra("gemini-3.8-flash", 0, 0, 0, 1, "analisis", estado="cuota", error="429 quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier' quotaValue: '20'", dir_work=tmp, ts=h - 90)
        uso.registra("gemini-3.7-flash", 0, 0, 0, 1, "analisis", estado="no_disponible", error="404 NOT_FOUND", dir_work=tmp, ts=h - 60)
        uso.registra("gemini-3.6-flash", 0, 0, 0, 1, "analisis", estado="saturado", dir_work=tmp, ts=h - 3000)                 # hace 50 min: ya se recuperó
        sal = salud(tmp, h)
        cad = cadena(semilla, None, h, tmp)
        caso("el modelo saturado se aparta unos minutos, el agotado por hoy y el no disponible más; el que falló hace 50 min vuelve a su sitio",
             sal["gemini-4-flash"]["estado"] == "saturado" and sal["gemini-3.8-flash"]["estado"] == "agotado" and sal["gemini-3.7-flash"]["estado"] == "no_disponible"
             and "gemini-3.6-flash" not in sal and cad[0] == "gemini-3.6-flash" and cad[1] == "gemini-3.5-flash"
             and cad[-3:] == ["gemini-4-flash", "gemini-3.8-flash", "gemini-3.7-flash"], cad)
        uso.registra("gemini-4-flash", 1000, 100, 0, 5, "analisis", dir_work=tmp, ts=h - 30)
        caso("en cuanto el saturado vuelve a responder, es otra vez el primero", cadena(semilla, None, h, tmp)[0] == "gemini-4-flash" and "gemini-4-flash" not in salud(tmp, h))
        caso("aunque TODOS estuvieran mal, la cadena no queda vacía (se prueban igual, del sano al peor)",
             len(_por_salud(["a", "b", "c"], {"a": {"estado": "agotado"}, "b": {"estado": "saturado"}, "c": {"estado": "no_disponible"}})) == 3
             and _por_salud(["a", "b", "c"], {"a": {"estado": "agotado"}, "b": {"estado": "saturado"}, "c": {"estado": "no_disponible"}}) == ["b", "a", "c"])
        caso("el trabajo con muchas llamadas pequeñas usa el mejor Flash-Lite que sirve (no gasta los 20 al día de los Flash buenos) y, si está saturado, el siguiente",
             barato(semilla, None, h, tmp) == "gemini-3.5-flash-lite" and barato(Cfg(**{"ai.modelos_auto": False, "ai.model": "gemini-x-fijo"}), None, h, tmp) == "gemini-x-fijo"
             and barato(Cfg(**{"ai.model": "gemini-x-fijo"}), None, h, tempfile.gettempdir() + "/no-existe-modelos") == "gemini-x-fijo")
        uso.registra("gemini-3.5-flash-lite", 0, 0, 0, 9, "temas", estado="saturado", dir_work=tmp, ts=h - 20)
        caso("…y si el mejor Flash-Lite acaba de dar «saturado», usa el siguiente Lite", barato(semilla, None, h, tmp) == "gemini-3.1-flash-lite")
        caso("con ai.modelos_auto apagado manda la lista de config.yaml tal cual", cadena(Cfg(**{"ai.modelos_auto": False, "ai.director_modelos": ["m1", "m2"]}), Cliente(), h, tmp) == ["m1", "m2"])
    return filas
