"""Uso de Gemini de ESTE programa (vídeos horizontales): cuántas peticiones y tokens gasta, contra los límites del plan gratis.

Cómo limita Google (documentación oficial de la API de Gemini):
- Por MODELO y por PROYECTO (no por clave), en tres medidas: peticiones por minuto (RPM), tokens de ENTRADA por minuto (TPM) y
  peticiones por día (RPD). NO hay tope semanal ni mensual en el plan gratis (los topes mensuales son de gasto, del plan de pago).
- El día se reinicia a medianoche, hora del Pacífico: las 2:00 a. m. en Colombia con horario de verano de EE. UU., las 3:00 a. m. sin él.
- Google NO publica las cifras: salen en aistudio.google.com/rate-limit (y en el error 429, que las trae). Por eso el punto de partida es
  POR CÓDIGO (`LIMITES_GRATIS`, lo que enseñó AI Studio el 21-sep-2026: Flash 20/día, Flash-Lite 500/día), se corrige solo con el primer 429 que
  llegue y el autor puede poner otros (`work/uso_limites.json`, desde la pestaña «Uso»).

Cada llamada a Gemini se apunta en `work/gemini_uso.jsonl` (una línea JSON). Este registro es SOLO de este programa: los Shorts
(otros programas) llevan su propia cuenta en su carpeta. Si comparten la clave, comparten cuota (Google suma los dos): el total real
está en aistudio.google.com/usage.

Todo aquí es a prueba de fallos: apuntar el uso NUNCA puede romper una llamada a Gemini.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
_LOCK = threading.Lock()
CONTEXTO: dict = {"job": ""}          # lo rellena pipeline.process_video_pro: a qué trabajo pertenece cada llamada
AVISO_PCT = 80                        # a partir de aquí el medidor se pone ámbar (y rojo desde 95)
_TIPOS = {"analisis": "Análisis del vídeo", "mejora": "Mejora con IA", "temas": "Generador por temáticas",
          "prueba": "Prueba del sistema", "medicion": "Medición", "vision": "La IA ve el vídeo (Flash-Lite)",
          "titulos": "Títulos nuevos (Flash-Lite)", "shorts": "Shorts: elegir (el mejor Flash)",
          "shorts_revision": "Shorts: segunda opinión (Flash-Lite)", "short_tt": "🎬 Shorts · TikTok: el director (el mejor Flash)", "tags": "Etiquetas de un vídeo antiguo (Flash-Lite)",
          "palabras": "Palabras clave de un vídeo antiguo (Flash-Lite)", "ideas": "💡 Ideas de vídeos (el mejor Flash)",
          "ideas_guion": "💡 Guion de una idea (Flash-Lite)", "ideas_refresco": "💡 Refrescar una idea de la lista (Flash-Lite)",
          "ideas_perfil": "💡 Configuración de las ideas de un canal (Flash-Lite)",
          "articulo": "📝 Artículo para tu web (Flash-Lite)",
          "redes": "📣 Publicaciones para tus redes (Flash-Lite)",
          "carrusel": "🖼️ Carrusel para Instagram y TikTok (Flash-Lite)",
          "miniatura_concepto": "🎨 Concepto de miniatura nuevo (Flash-Lite)",
          "articulo_imagen": "🎨 Escena de la imagen destacada del artículo (Flash-Lite)",
          "miniatura_texto": "🎨 Revisar que la miniatura no traiga texto (Flash-Lite)",
          "nombres": "🛡 Revisar los nombres de lo que sale en pantalla (Flash-Lite)",
          "imagenes": "🖼️ Elegir las imágenes ilustrativas mirando las fotos (Flash-Lite)"}


# ------------------------------------------------------------------ rutas
def _dir_work(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work)
    from . import config
    return config.paths()["work"]


def ruta_uso(dir_work=None) -> Path:
    return _dir_work(dir_work) / "gemini_uso.jsonl"


def ruta_limites(dir_work=None) -> Path:
    return _dir_work(dir_work) / "uso_limites.json"


def contexto(**kw) -> None:
    CONTEXTO.update(kw)


# ------------------------------------------------------------------ el día de Google (hora del Pacífico, sin librerías)
def _domingo(anio: int, mes: int, n: int) -> int:
    """Día del mes del n-ésimo domingo."""
    primero = datetime(anio, mes, 1, tzinfo=UTC)
    return 1 + (6 - primero.weekday()) % 7 + 7 * (n - 1)


def offset_pacifico(ts: float) -> int:
    """-7 (horario de verano de EE. UU.: 2.º domingo de marzo → 1.er domingo de noviembre) o -8."""
    dt = datetime.fromtimestamp(ts, UTC)
    inicio = datetime(dt.year, 3, _domingo(dt.year, 3, 2), 10, 0, tzinfo=UTC)      # 2:00 a. m. PST = 10:00 UTC
    fin = datetime(dt.year, 11, _domingo(dt.year, 11, 1), 9, 0, tzinfo=UTC)        # 2:00 a. m. PDT = 09:00 UTC
    return -7 if inicio <= dt < fin else -8


def dia_cuota(ts: float) -> str:
    """El «día» de Google al que pertenece ese instante (AAAA-MM-DD, hora del Pacífico)."""
    return (datetime.fromtimestamp(ts, UTC) + timedelta(hours=offset_pacifico(ts))).date().isoformat()


def proximo_reinicio(ts: float) -> float:
    """Cuándo se reinicia el contador diario: la próxima medianoche del Pacífico (segundos desde 1970)."""
    off = offset_pacifico(ts)
    local = datetime.fromtimestamp(ts, UTC) + timedelta(hours=off)
    medianoche = datetime(local.year, local.month, local.day, tzinfo=UTC) + timedelta(days=1)
    guess = (medianoche - timedelta(hours=off)).timestamp()
    off2 = offset_pacifico(guess - 1)
    return (medianoche - timedelta(hours=off2)).timestamp()


# ------------------------------------------------------------------ apuntar
def es_proyecto_bloqueado(exc_o_texto) -> bool:
    """403 «Your project has been denied access» (o proyecto suspendido/restringido): Google bloqueó el PROYECTO de la clave, así que NINGÚN modelo va a
    responder y no es culpa de un modelo concreto (8-oct-2026: los 9 modelos de la cadena dieron lo mismo en 0,4 s cada uno)."""
    m = str(exc_o_texto or "").lower()
    return ("403" in m or "permission_denied" in m) and any(t in m for t in (
        "denied access", "project has been denied", "has been suspended", "consumer_suspended", "api access is restricted"))


def clasifica(exc_o_texto) -> str:
    """'cuota' (429: Google dice que te pasaste), 'no_disponible' (404/403), 'saturado' (503/504/timeout: no es tu cuota) o 'error'.
    Un proyecto bloqueado por Google es 'error' (no 'no_disponible'): no es culpa del modelo, y así el registro no lo aparta 24 h
    cuando Google levante la restricción (`modelos.salud` solo castiga lo saturado, agotado y no disponible)."""
    m = str(exc_o_texto or "").lower()
    if es_proyecto_bloqueado(m):
        return "error"
    if "429" in m or "resource_exhausted" in m or "quota" in m:
        if re.search(r"limit:\s*0(?!\d)", m) or re.search(r"quotavalue['\"]?\s*:\s*['\"]?0(?!\d)", m):
            return "no_disponible"                  # «limit: 0»: el plan gratis NO da cuota a ese modelo (los de imagen, los Pro…): no insistir hoy
        return "cuota"
    if any(t in m for t in ("404", "not_found", "not found", "403", "permission_denied", "is not supported for")):
        return "no_disponible"                      # ese modelo no existe o no está a disposición de esta clave: no insistir hoy
    if any(t in m for t in ("503", "504", "500", "502", "unavailable", "overloaded", "high demand", "deadline", "timed out",
                            "timeout", "connection", "reset by peer", "try again")):
        return "saturado"
    return "error"


def dimension_429(texto) -> str:
    """Qué límite fue el que Google dijo que te pasaste: 'rpd' (el del DÍA: el modelo queda agotado hasta que se reinicie), 'rpm', 'tpm' o ''."""
    t = str(texto or "")
    q = " ".join(_RE_ID.findall(t)).lower()
    if q:
        if "inputtoken" in q and "minute" in q:
            return "tpm"
        if "perday" in q:
            return "rpd"
        if "perminute" in q:
            return "rpm"
    b = t.lower()
    if "per day" in b or "perday" in b or "daily" in b:
        return "rpd"
    if "per minute" in b or "perminute" in b:
        return "rpm"
    return ""


def registra(modelo: str, entrada: int = 0, salida: int = 0, pensamiento: int = 0, seg: float = 0.0, tipo: str = "",
             estado: str = "ok", error: str = "", job: str | None = None, dir_work=None, ts: float | None = None,
             origen: str = "vivo") -> None:
    """Apunta UNA petición a Gemini. `salida` ya incluye el pensamiento (así lo cobra/cuenta Google). Nunca lanza."""
    try:
        fila = {"ts": round(ts or time.time(), 3), "modelo": str(modelo or ""), "estado": estado if estado in ("ok", "saturado", "cuota", "no_disponible", "error") else "error",
                "entrada": int(entrada or 0), "salida": int(salida or 0), "pensamiento": int(pensamiento or 0),
                "seg": round(float(seg or 0.0), 1), "tipo": str(tipo or ""), "job": str(CONTEXTO.get("job") if job is None else job or "")}
        if origen != "vivo":
            fila["origen"] = origen
        if fila["estado"] == "cuota":
            fila["limite"] = dimension_429(error)               # 'rpd' = agotado por hoy; lo demás es pasajero
        ruta = ruta_uso(dir_work)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(ruta, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")
        if estado == "cuota" and error:
            detecta_limite(error, modelo, dir_work)
    except Exception:                                   # noqa: BLE001 — contar nunca rompe una llamada
        pass


def lee(dir_work=None) -> list[dict]:
    """Todas las filas válidas, en orden de tiempo (las líneas rotas se saltan)."""
    out: list[dict] = []
    try:
        with open(ruta_uso(dir_work), encoding="utf-8", errors="replace") as f:
            for linea in f:
                try:
                    d = json.loads(linea)
                    d["ts"] = float(d["ts"])
                    d["modelo"] = str(d.get("modelo") or "")
                    d["estado"] = str(d.get("estado") or "ok")
                    for k in ("entrada", "salida", "pensamiento"):
                        d[k] = int(d.get(k) or 0)
                    out.append(d)
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        return []
    out.sort(key=lambda d: d["ts"])
    return out


# ------------------------------------------------------------------ límites: por código (estimados), los que pone el autor y los que detecta un 429
_DIMS = ("rpd", "rpm", "tpm")

# Lo que enseñó AI Studio → Rate limits (proyecto «Short Clipper», Nivel gratuito, 2026-09-21). Google NO publica estas cifras y las cambia:
# son solo el PUNTO DE PARTIDA por familia de modelo (todo sale por código, sin que nadie las teclee) y se marcan «estimado». Si el autor pone otras
# o Google dice otra en un error 429, mandan esas.
LIMITES_GRATIS = {"flash": {"rpd": 20, "rpm": 5, "tpm": 250_000}, "lite": {"rpd": 500, "rpm": 15, "tpm": 250_000}}


def familia(modelo: str) -> str:
    m = str(modelo or "").lower()
    return "lite" if "lite" in m else "flash" if "flash" in m else ""


def limite_por_defecto(modelo: str) -> dict:
    return dict(LIMITES_GRATIS.get(familia(modelo), {}))


def _entero(v):
    try:
        n = int(float(str(v).replace(".", "").replace(",", "").replace(" ", ""))) if v not in (None, "") else None
    except ValueError:
        return None
    return n if n and 0 < n <= 10_000_000_000 else None


def carga_limites(dir_work=None) -> dict:
    """{"modelos": {modelo: {rpd, rpm, tpm}} (los que puso el autor), "detectados": {...} (los que dijo Google en un 429), "rpd_general": int|None,
    "aviso_pct": 80, "clave_aparte": bool, "clave_desde": ts|None (cuándo se cambió a una clave nueva: la cuenta de cuotas empieza ahí)}"""
    base = {"modelos": {}, "detectados": {}, "rpd_general": None, "aviso_pct": AVISO_PCT, "clave_aparte": False, "clave_desde": None}
    try:
        d = json.loads(ruta_limites(dir_work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if not isinstance(d, dict):
        return base
    for clave in ("modelos", "detectados"):
        for m, v in (d.get(clave) or {}).items():
            if isinstance(v, dict):
                base[clave][str(m)] = {k: _entero(v.get(k)) for k in _DIMS}
    pct = _entero(d.get("aviso_pct"))
    base["aviso_pct"] = pct if pct and 10 <= pct <= 99 else AVISO_PCT
    base["clave_aparte"] = bool(d.get("clave_aparte"))
    base["rpd_general"] = _entero(d.get("rpd_general"))
    try:
        base["clave_desde"] = float(d["clave_desde"]) if d.get("clave_desde") else None
    except (TypeError, ValueError):
        base["clave_desde"] = None
    return base


def guarda_limites(datos: dict, dir_work=None) -> dict:
    """Valida y guarda lo que escribe el autor (los `detectados` no se tocan). Devuelve lo guardado."""
    actual = carga_limites(dir_work)
    nuevo = {"modelos": dict(actual["modelos"]), "detectados": actual["detectados"], "aviso_pct": actual["aviso_pct"],
             "clave_aparte": actual["clave_aparte"], "rpd_general": actual["rpd_general"], "clave_desde": actual["clave_desde"]}
    if "rpd_general" in datos:
        nuevo["rpd_general"] = _entero(datos.get("rpd_general"))
    if "modelos" in datos:                              # (si no viene, se conservan: el interruptor de «clave aparte» no borra los límites)
        nuevo["modelos"] = {}
        for m, v in (datos.get("modelos") or {}).items():
            if isinstance(v, dict) and re.fullmatch(r"[A-Za-z0-9._-]{3,80}", str(m)):
                fila = {k: _entero(v.get(k)) for k in _DIMS}
                if any(fila.values()):
                    nuevo["modelos"][str(m)] = fila
    if "aviso_pct" in datos:
        pct = _entero(datos.get("aviso_pct"))
        nuevo["aviso_pct"] = pct if pct and 10 <= pct <= 99 else AVISO_PCT
    if "clave_aparte" in datos:
        nuevo["clave_aparte"] = bool(datos.get("clave_aparte"))
    if "clave_desde" in datos:
        try:
            nuevo["clave_desde"] = float(datos["clave_desde"]) if datos["clave_desde"] else None
        except (TypeError, ValueError):
            pass
    ruta = ruta_limites(dir_work)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(nuevo, ensure_ascii=False, indent=1), encoding="utf-8")
    return nuevo


_RE_ID = re.compile(r"quotaId['\"]?\s*:\s*['\"]([^'\"]+)['\"]")
_RE_VALOR = re.compile(r"quotaValue['\"]?\s*:\s*['\"]?(\d+)")
_RE_MODELO = re.compile(r"['\"]model['\"]\s*:\s*['\"]([\w.\-]+)['\"]")


def detecta_limite(error: str, modelo: str = "", dir_work=None) -> dict | None:
    """El error 429 de Google trae el límite que te pasaste (quotaId + quotaValue + modelo): se guarda solo. Devuelve lo detectado."""
    try:
        texto = str(error or "")
        ids, valores = _RE_ID.findall(texto), _RE_VALOR.findall(texto)
        if not ids or not valores:
            return None
        m = (_RE_MODELO.search(texto) or [None, modelo])[1] if _RE_MODELO.search(texto) else modelo
        hallado: dict = {}
        for qid, val in zip(ids, valores):
            q = qid.lower()
            if int(val) <= 0:
                continue                                         # un límite de 0 no es un límite: es «este modelo no es gratis»
            if "inputtoken" in q and "minute" in q:
                hallado["tpm"] = int(val)
            elif "perday" in q and "request" in q:
                hallado["rpd"] = int(val)
            elif "perminute" in q and "request" in q:
                hallado["rpm"] = int(val)
        if not hallado or not m:
            return None
        actual = carga_limites(dir_work)
        actual["detectados"].setdefault(m, {k: None for k in _DIMS}).update(hallado)
        ruta = ruta_limites(dir_work)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(actual, ensure_ascii=False, indent=1), encoding="utf-8")
        return {m: hallado}
    except Exception:                                   # noqa: BLE001
        return None


# ------------------------------------------------------------------ sumas
def _picos(filas: list[dict]) -> tuple[int, int]:
    """(máx. peticiones, máx. tokens de entrada) en cualquier ventana de 60 s."""
    mp = mt = 0
    j = 0
    acum_t = 0
    for i, f in enumerate(filas):
        acum_t += f["entrada"]
        while filas[j]["ts"] <= f["ts"] - 60:
            acum_t -= filas[j]["entrada"]
            j += 1
        mp, mt = max(mp, i - j + 1), max(mt, acum_t)
    return mp, mt


def _suma(filas: list[dict]) -> dict:
    ok = [f for f in filas if f["estado"] == "ok"]
    return {"peticiones": len(ok), "intentos": len(filas), "fallidos": len(filas) - len(ok),
            "cuota": sum(1 for f in filas if f["estado"] == "cuota"),
            "entrada": sum(f["entrada"] for f in ok), "salida": sum(f["salida"] for f in ok),
            "pensamiento": sum(f["pensamiento"] for f in ok)}


def _estado_pct(pct, aviso: int) -> str:
    if pct is None:
        return "sin"
    return "mal" if pct >= 95 else "aviso" if pct >= aviso else "ok"


def resumen(cfg=None, ahora: float | None = None, dir_work=None, dias: int = 14) -> dict:
    """Todo lo que enseña la pestaña «Uso»: la ESCALERA de modelos (del mejor al de respaldo, cuánto queda de cada uno HOY), las dos
    reservas (mejores = Flash, respaldo = Flash-Lite), el medidor grande, el mapa día a día por modelo y el reinicio del día."""
    from .util import nombre_modelo
    ahora = ahora or time.time()
    todas = lee(dir_work)
    lim = carga_limites(dir_work)
    filas = [f for f in todas if f["ts"] >= (lim["clave_desde"] or 0.0)]        # lo que cuenta para las cuotas de la clave ACTUAL
    hoy = dia_cuota(ahora)
    aviso = lim["aviso_pct"]
    from . import modelos as _mod
    cadena = _mod.cadena_base(cfg, None, ahora, dir_work)          # del mejor al peor: la última búsqueda en Google (o config.yaml si no hay)
    sal = _mod.salud(dir_work, ahora, float(cfg.get_path("ai.enfriamiento_min", _mod.ENFRIAMIENTO_MIN)) if cfg is not None else _mod.ENFRIAMIENTO_MIN)
    de_hoy = [f for f in filas if dia_cuota(f["ts"]) == hoy]
    por_video = 1 + int((cfg.get_path("videos.mejoras_max", 3) if cfg is not None else 3))

    def efectivo(m: str, dim: str):
        for v, origen in (((lim["modelos"].get(m) or {}).get(dim), "puesto"), ((lim["detectados"].get(m) or {}).get(dim), "detectado"),
                          (lim["rpd_general"] if dim == "rpd" else None, "general"), (limite_por_defecto(m).get(dim), "estimado")):
            if v:
                return v, origen
        return None, None

    universo = list(dict.fromkeys(cadena + sorted({f["modelo"] for f in filas if f["modelo"]}) + sorted(lim["modelos"]) + sorted(lim["detectados"])))
    modelos: list[dict] = []
    for m in universo:
        fm = [f for f in de_hoy if f["modelo"] == m]
        s = _suma(fm)
        pm, pt = _picos(fm) if fm else (0, 0)
        ef = {d: efectivo(m, d) for d in _DIMS}
        limite = {d: ef[d][0] for d in _DIMS}
        origen = {d: ef[d][1] for d in _DIMS}
        # RPD (cuota DIARIA): solo las peticiones que de verdad respondieron cuentan — un 503 "saturado" es
        # GOOGLE diciendo que SU servidor está lleno (nunca llega a procesar nada tuyo), no gasta tu cupo del
        # día (ver la nota de call_gemini_cadena). RPM/TPM sí cuentan CUALQUIER intento (con éxito o no): esos
        # los frena la puerta de entrada de la API por VOLUMEN de peticiones, antes de saber si hay capacidad.
        # el autor lo notó (2026-09-23): con el reintento por tiempo nuevo, un vídeo saturado deja varias filas
        # "saturado" por modelo, y antes esas ENGORDABAN el % de cuota diaria gastada sin haber gastado nada.
        pct = {"rpd": round(100 * s["peticiones"] / limite["rpd"], 1) if limite["rpd"] else None,
               "rpm": round(100 * pm / limite["rpm"], 1) if limite["rpm"] else None,
               "tpm": round(100 * pt / limite["tpm"], 1) if limite["tpm"] else None}
        # el minuto de AHORA (lo que puede dar un 429 en este instante); el pico de hoy solo informa: no deja una alarma puesta todo el día
        vent = [f for f in fm if f["ts"] > ahora - 60]
        pa, ta = len(vent), sum(f["entrada"] for f in vent)
        pct_ahora = {"rpm": round(100 * pa / limite["rpm"], 1) if limite["rpm"] else None,
                     "tpm": round(100 * ta / limite["tpm"], 1) if limite["tpm"] else None}
        rest = max(0, limite["rpd"] - s["peticiones"]) if limite["rpd"] else None
        modelos.append({"modelo": m, "nombre": nombre_modelo(m), "en_cadena": m in cadena, "orden": cadena.index(m) if m in cadena else 99,
                        "tier": "respaldo" if familia(m) == "lite" else "mejor", "alias": bool((_mod.analiza(m) or {}).get("alias")),
                        "hoy": s, "pico": {"peticiones_min": pm, "entrada_min": pt}, "limite": limite, "origen": origen,
                        "puesto": {d: (lim["modelos"].get(m) or {}).get(d) for d in _DIMS}, "detectado": lim["detectados"].get(m) or {},
                        "restantes": rest, "pct": pct, "pct_ahora": pct_ahora, "estado": {d: _estado_pct(pct[d], aviso) for d in _DIMS}})
    # orden de la escalera: primero la cadena (del mejor al de respaldo), luego los que no están en ella (los mejores primero)
    fuera = [x for x in modelos if not x["en_cadena"]]
    fuera.sort(key=lambda x: x["modelo"])
    modelos = sorted([x for x in modelos if x["en_cadena"]], key=lambda x: x["orden"]) + fuera

    # el papel de cada uno HOY: el que se usa ahora (el primero con peticiones), el siguiente y los agotados
    usando = siguiente = None
    for x in modelos:
        h = (sal.get(x["modelo"]) or {}).get("estado")               # cómo está AHORA según el registro: saturado / agotado / no_disponible
        x["salud"] = h or "ok"
        x["hasta"] = (sal.get(x["modelo"]) or {}).get("hasta")        # cuándo se vuelve a probar (si está apartado)
        if not x["en_cadena"]:
            x["rol"] = "fuera"
            continue
        if h == "agotado":
            x["restantes"] = 0                                        # Google dijo que se acabó su cuota DIARIA: manda eso y no la estimación
        libre = x["limite"]["rpd"] is None or x["restantes"] > 0
        if h in ("saturado", "no_disponible"):
            x["rol"] = h
        elif not libre or h == "agotado":
            x["rol"] = "agotado"
        elif usando is None:
            x["rol"], usando = "usando", x
        elif siguiente is None:
            x["rol"], siguiente = "siguiente", x
        else:
            x["rol"] = "reserva"

    def reserva(tier: str):
        ms = [x for x in modelos if x["en_cadena"] and x["tier"] == tier and x["limite"]["rpd"] and not x["alias"]]   # el atajo «-latest» no suma: no se sabe si tiene cuota propia
        if not ms:
            return None
        limite_t = sum(x["limite"]["rpd"] for x in ms)
        usadas = sum(min(x["hoy"]["peticiones"], x["limite"]["rpd"]) for x in ms)      # solo lo que de verdad respondió (ver nota arriba)
        resto = sum(x["restantes"] for x in ms)
        pct_t = round(100 * usadas / limite_t, 1)
        return {"usadas": usadas, "limite": limite_t, "restantes": resto, "pct": pct_t, "estado": _estado_pct(pct_t, aviso),
                "modelos": len(ms), "videos": resto // por_video}
    pools = {"mejores": reserva("mejor"), "respaldo": reserva("respaldo")}

    # los últimos `dias` días (con los días sin uso a cero) y, en cada uno, cuánto se pidió a cada modelo
    dias_l: list[dict] = []
    mejores_ms = [x for x in modelos if x["en_cadena"] and x["tier"] == "mejor" and x["limite"]["rpd"] and not x["alias"]]
    for i in range(dias - 1, -1, -1):
        d = dia_cuota(ahora - i * 86400)
        fd = [f for f in filas if dia_cuota(f["ts"]) == d]
        por_modelo: dict = {}
        for f in fd:
            por_modelo[f["modelo"]] = por_modelo.get(f["modelo"], 0) + 1
        pct_d = (round(100 * sum(min(por_modelo.get(x["modelo"], 0), x["limite"]["rpd"]) for x in mejores_ms)
                       / sum(x["limite"]["rpd"] for x in mejores_ms), 1) if mejores_ms else None)
        dias_l.append({"dia": d, **_suma(fd), "modelos": sorted({f["modelo"] for f in fd if f["estado"] == "ok"}),
                       "por_modelo": por_modelo, "pct": pct_d})

    # media real por tipo de llamada (para saber cuánto cuesta cada cosa)
    tipos: dict = {}
    for t in sorted({f.get("tipo") or "" for f in todas if f["estado"] == "ok"}):
        ft = [f for f in todas if f["estado"] == "ok" and (f.get("tipo") or "") == t and f["entrada"]]
        if ft:
            tipos[t] = {"nombre": _TIPOS.get(t, t or "Otros"), "n": len(ft), "entrada_media": round(sum(f["entrada"] for f in ft) / len(ft)),
                        "salida_media": round(sum(f["salida"] for f in ft) / len(ft))}

    # EL MEDIDOR GRANDE («la pila»): cuánto has gastado de los MEJORES modelos hoy (si no hay, de los de respaldo)
    base_p = pools["mejores"] or pools["respaldo"]
    if base_p:
        hero = {"pct": base_p["pct"], "usadas": base_p["usadas"], "limite": base_p["limite"], "restantes": base_p["restantes"], "videos": base_p["videos"],
                "estado": base_p["estado"], "por_video": por_video, "tier": "mejor" if pools["mejores"] else "respaldo",
                "ahora": ({"modelo": usando["modelo"], "nombre": usando["nombre"], "restantes": usando["restantes"], "limite": usando["limite"]["rpd"]} if usando else None),
                "siguiente": ({"modelo": siguiente["modelo"], "nombre": siguiente["nombre"], "restantes": siguiente["restantes"],
                               "limite": siguiente["limite"]["rpd"]} if siguiente else None)}
    else:
        hero = {"pct": None, "usadas": len(de_hoy), "limite": None, "restantes": None, "videos": None, "estado": "sin", "por_video": por_video,
                "tier": None, "ahora": None, "siguiente": None}

    # la alarma de «este minuto»: lo que podría dar un 429 ahora mismo
    pcts = [(p, x["modelo"], d) for x in modelos for d, p in x["pct_ahora"].items() if p is not None]
    peor = max(pcts) if pcts else None
    reinicio = proximo_reinicio(ahora)
    return {"ahora": ahora, "dia": hoy, "hoy": _suma(de_hoy), "semana": _suma([f for f in filas if f["ts"] >= ahora - 7 * 86400]),
            "mes": _suma([f for f in filas if f["ts"] >= ahora - 30 * 86400]), "modelos": modelos, "dias": dias_l, "tipos": tipos,
            "pools": pools, "hero": hero, "clave_desde": lim["clave_desde"],
            "limites": {"aviso_pct": aviso, "clave_aparte": lim["clave_aparte"], "rpd_general": lim["rpd_general"]},
            "reinicio": {"ts": reinicio, "en_seg": max(0, int(reinicio - ahora)), "local": datetime.fromtimestamp(reinicio).strftime("%H:%M")},
            "peor": ({"pct": peor[0], "modelo": peor[1], "medida": peor[2], "estado": _estado_pct(peor[0], aviso)} if peor
                     else {"pct": None, "modelo": "", "medida": "", "estado": "sin"}),
            "desde": todas[0]["ts"] if todas else None, "n_registros": len(todas)}


# ------------------------------------------------------------------ pruebas (corren en «Probar el sistema»)
class _CfgPrueba:
    def __init__(self, cadena, mejoras=3):
        self._d = {"ai.director_modelos": list(cadena), "videos.mejoras_max": mejoras}

    def get_path(self, k, d=None):
        return self._d.get(k, d)


def prueba() -> list[dict]:
    import tempfile
    filas_: list[dict] = []

    def caso(n, ok, d=""):
        filas_.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    U = lambda *a: datetime(*a, tzinfo=UTC).timestamp()                                             # noqa: E731
    caso("el horario de verano del Pacífico (-7) y el de invierno (-8) cambian en las fechas de EE. UU.",
         offset_pacifico(U(2026, 9, 21, 12)) == -7 and offset_pacifico(U(2026, 12, 1, 12)) == -8
         and offset_pacifico(U(2026, 3, 8, 9, 59)) == -8 and offset_pacifico(U(2026, 3, 8, 10)) == -7
         and offset_pacifico(U(2026, 11, 1, 8, 59)) == -7 and offset_pacifico(U(2026, 11, 1, 9)) == -8)
    caso("el día de Google cambia a medianoche del Pacífico (07:00 UTC en verano, 08:00 en invierno)",
         dia_cuota(U(2026, 9, 21, 6, 59)) == "2026-09-20" and dia_cuota(U(2026, 9, 21, 7)) == "2026-09-21"
         and dia_cuota(U(2026, 12, 1, 7, 59)) == "2026-11-30" and dia_cuota(U(2026, 12, 1, 8)) == "2026-12-01")
    caso("el próximo reinicio es la siguiente medianoche del Pacífico",
         proximo_reinicio(U(2026, 9, 21, 12)) == U(2026, 9, 22, 7) and proximo_reinicio(U(2026, 12, 1, 12)) == U(2026, 12, 2, 8))
    caso("los límites por código: Flash 20/día y 5/min, Flash-Lite 500/día y 15/min, 250 mil tokens/min; un modelo desconocido no inventa nada",
         limite_por_defecto("gemini-3.8-flash") == {"rpd": 20, "rpm": 5, "tpm": 250000} and limite_por_defecto("gemini-flash-lite-latest")["rpd"] == 500
         and limite_por_defecto("gemini-3.1-flash-lite")["rpm"] == 15 and limite_por_defecto("gemini-raro") == {} and familia("gemini-3-flash-preview") == "flash")
    cadena = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
    cfg = _CfgPrueba(cadena)
    with tempfile.TemporaryDirectory() as tmp:
        base = U(2026, 9, 21, 15)                                        # 8:00 a. m. del Pacífico del 21
        registra("gemini-3.5-flash", 19000, 7000, 3000, 20, "mejora", dir_work=tmp, ts=base)
        registra("gemini-3.5-flash", 10000, 6000, 3000, 30, "analisis", dir_work=tmp, ts=base + 30)
        registra("gemini-3.5-flash", 0, 0, 0, 2, "analisis", estado="saturado", dir_work=tmp, ts=base + 200)
        registra("gemini-3.1-flash-lite", 19000, 3000, 0, 11, "mejora", dir_work=tmp, ts=base + 4000)
        registra("gemini-3.5-flash", 5000, 900, 0, 5, "analisis", dir_work=tmp, ts=base - 86400)      # ayer
        with open(ruta_uso(tmp), "a", encoding="utf-8") as f:
            f.write("esto no es json\n\n{\"ts\": \"x\"}\n")
        r = resumen(cfg, ahora=base + 5000, dir_work=tmp)
        m35 = next(x for x in r["modelos"] if x["modelo"] == "gemini-3.5-flash")
        caso("suma el día por peticiones, intentos fallidos y tokens (y salta las líneas rotas)",
             r["hoy"]["peticiones"] == 3 and r["hoy"]["fallidos"] == 1 and r["hoy"]["intentos"] == 4 and r["hoy"]["entrada"] == 48000
             and r["hoy"]["salida"] == 16000 and r["n_registros"] == 5, r["hoy"])
        caso("separa por modelo y cuenta el pico de un minuto (2 llamadas en 30 s = 29 mil tokens de entrada)",
             m35["hoy"]["peticiones"] == 2 and m35["pico"]["peticiones_min"] == 2 and m35["pico"]["entrada_min"] == 29000, m35["pico"])
        caso("los últimos días salen en orden, con el día sin uso a cero, ayer aparte y cuánto se pidió a cada modelo",
             r["dias"][-1]["dia"] == "2026-09-21" and r["dias"][-1]["peticiones"] == 3 and r["dias"][-2]["dia"] == "2026-09-20"
             and r["dias"][-2]["peticiones"] == 1 and r["dias"][-3]["peticiones"] == 0 and len(r["dias"]) == 14
             and r["dias"][-1]["por_modelo"] == {"gemini-3.5-flash": 3, "gemini-3.1-flash-lite": 1}, r["dias"][-1])
        caso("la media por tipo sale del uso real (mejora: 19 mil de entrada)",
             r["tipos"]["mejora"]["entrada_media"] == 19000 and r["tipos"]["mejora"]["n"] == 2 and r["tipos"]["analisis"]["n"] == 2, r["tipos"])
        # --- la escalera: del mejor al de respaldo, con lo que queda de cada uno
        # gemini-3.5-flash hoy: 2 peticiones que SÍ respondieron + 1 fila "saturado" (esa NO gasta cupo del
        # día) -> quedan 20-2=18, no 20-3=17 (así se pilló el bug: antes el "saturado" restaba igual que una
        # petición real, y con el reintento por tiempo nuevo eso infla mucho el % que se ve en el panel).
        caso("sin que nadie escriba nada, cada modelo ya trae su límite (por código, «estimado») y la escalera sigue el orden de la cadena "
             "(el «saturado» de arriba NO cuenta contra la cuota del día: 20-2=18, no 20-3)",
             [x["modelo"] for x in r["modelos"]] == cadena and m35["limite"]["rpd"] == 20 and m35["origen"]["rpd"] == "estimado" and m35["restantes"] == 18,
             [(x["modelo"], x["rol"]) for x in r["modelos"]])
        caso("el primero con peticiones es el «usando», el siguiente es el «siguiente»; y la reserva de los mejores suma sus límites (2 × 20)",
             [x["rol"] for x in r["modelos"][:3]] == ["usando", "siguiente", "reserva"] and r["pools"]["mejores"]["limite"] == 40
             and r["pools"]["mejores"]["usadas"] == 2 and r["pools"]["mejores"]["restantes"] == 20 - 0 + 18 and r["pools"]["respaldo"]["limite"] == 500, r["pools"])
        caso("el medidor grande es el % gastado de los MEJORES modelos, con «ahora» y «siguiente», y cuántos vídeos completos caben",
             r["hero"]["pct"] == 5.0 and r["hero"]["ahora"]["nombre"] == "Gemini 3.6 Flash" and r["hero"]["siguiente"]["modelo"] == "gemini-3.5-flash"
             and r["hero"]["restantes"] == 38 and r["hero"]["videos"] == 9 and r["hero"]["estado"] == "ok" and r["hero"]["por_video"] == 4, r["hero"])
        rl = resumen(_CfgPrueba(["gemini-3.6-flash", "gemini-flash-latest", "gemini-3.1-flash-lite"]), ahora=base + 5000, dir_work=tmp + "/sin-registro")
        caso("el atajo «…-latest» va en la lista como red de seguridad pero NO suma a la pila (no se sabe si tiene cuota propia): un Flash y su atajo = 20 peticiones, no 40",
             [x["alias"] for x in rl["modelos"]] == [False, True, False] and rl["pools"]["mejores"]["limite"] == 20 and rl["pools"]["mejores"]["modelos"] == 1
             and rl["pools"]["respaldo"]["limite"] == 500, rl["pools"])
        g = guarda_limites({"modelos": {"gemini-3.6-flash": {"rpd": "3"}, "gemini-3.5-flash": {"rpd": "20", "rpm": "5", "tpm": "250.000"}, "raro nombre!": {"rpd": 5},
                                        "gemini-3.1-flash-lite": {"rpd": "", "rpm": "abc", "tpm": -3}}, "aviso_pct": 70}, tmp)
        caso("guardar límites valida: números con puntos, descarta lo raro y lo vacío",
             g["modelos"] == {"gemini-3.6-flash": {"rpd": 3, "rpm": None, "tpm": None},
                              "gemini-3.5-flash": {"rpd": 20, "rpm": 5, "tpm": 250000}} and g["aviso_pct"] == 70, g)
        for _ in range(3):
            registra("gemini-3.6-flash", 100, 10, 0, 1, "analisis", dir_work=tmp, ts=base + 4100)
        r2 = resumen(cfg, ahora=base + 5000, dir_work=tmp)
        caso("un límite puesto por el autor manda sobre el estimado, y al agotarse un modelo la escalera pasa al siguiente (agotado → usando → siguiente)",
             [x["rol"] for x in r2["modelos"][:3]] == ["agotado", "usando", "siguiente"] and r2["modelos"][0]["origen"]["rpd"] == "puesto"
             and r2["modelos"][0]["restantes"] == 0 and r2["hero"]["ahora"]["modelo"] == "gemini-3.5-flash"
             and r2["hero"]["siguiente"]["modelo"] == "gemini-3.1-flash-lite", [(x["modelo"], x["rol"], x["restantes"]) for x in r2["modelos"][:3]])
        guarda_limites({"clave_aparte": True}, tmp)
        caso("guardar solo el interruptor de «clave aparte» no borra los límites que ya había", carga_limites(tmp)["modelos"] == g["modelos"] and carga_limites(tmp)["clave_aparte"])
        # --- alarma del minuto
        guarda_limites({"modelos": {"gemini-3.5-flash": {"rpd": 20, "rpm": 2, "tpm": 250000}}}, tmp)
        r3 = resumen(cfg, ahora=base + 5000, dir_work=tmp)
        m3 = next(x for x in r3["modelos"] if x["modelo"] == "gemini-3.5-flash")
        r3c = resumen(cfg, ahora=base + 45, dir_work=tmp)
        caso("un pico de hace horas por minuto sale en la tabla (100%) pero NO deja la alarma puesta; el minuto de AHORA sí la enciende",
             m3["pct"]["rpm"] == 100.0 and m3["estado"]["rpm"] == "mal" and r3["peor"]["estado"] == "ok" and r3c["peor"]["estado"] == "mal"
             and r3c["peor"]["medida"] == "rpm", (r3["peor"], r3c["peor"]))
        # --- clave nueva: la cuenta de cuotas empieza de cero
        guarda_limites({"clave_desde": base + 4500}, tmp)
        r4 = resumen(cfg, ahora=base + 5000, dir_work=tmp)
        caso("al cambiar de clave (otro proyecto de Google) lo anterior ya no cuenta para sus cuotas, pero sigue en el historial de tipos",
             r4["hoy"]["intentos"] == 0 and r4["hero"]["usadas"] == 0 and r4["tipos"]["mejora"]["n"] == 2 and r4["desde"] == r["desde"] and r4["clave_desde"] == base + 4500, r4["hero"])
        err = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED', 'details': [{'violations': [{'quotaMetric': "
               "'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', "
               "'quotaDimensions': {'location': 'global', 'model': 'gemini-3.6-flash'}, 'quotaValue': '25'}]}]}}")
        registra("gemini-3.6-flash", 0, 0, 0, 1, "analisis", estado="cuota", error=err, dir_work=tmp, ts=base + 4600)
        caso("un error 429 trae el límite y se guarda solo (25 peticiones/día para gemini-3.6-flash)",
             carga_limites(tmp)["detectados"].get("gemini-3.6-flash", {}).get("rpd") == 25 and clasifica(err) == "cuota"
             and clasifica("503 UNAVAILABLE high demand") == "saturado" and clasifica("400 invalid") == "error")
        # --- un solo número para todos (lo simple) y sin ninguna familia conocida
        vacio = tempfile.mkdtemp()
        registra("gemini-raro", 100, 10, 0, 1, "analisis", dir_work=vacio, ts=base)
        sin = resumen(_CfgPrueba(["gemini-raro"]), ahora=base + 60, dir_work=vacio)
        caso("un modelo de familia desconocida y sin límite no inventa un %: el medidor grande avisa que falta el dato",
             sin["hero"]["pct"] is None and sin["hero"]["estado"] == "sin" and sin["modelos"][0]["restantes"] is None and sin["modelos"][0]["rol"] == "usando", sin["hero"])
        guarda_limites({"rpd_general": "50"}, vacio)
        g1 = resumen(_CfgPrueba(["gemini-raro"]), ahora=base + 60, dir_work=vacio)
        caso("un límite diario «para todos» sirve a los modelos sin uno propio (1 de 50 = 2 %)",
             g1["hero"]["pct"] == 2.0 and g1["modelos"][0]["origen"]["rpd"] == "general", g1["hero"])
        # --- cómo está cada modelo AHORA (saturado / agotado por un 429 del día): el papel de «usando» pasa solo al siguiente
        sano = tempfile.mkdtemp()
        registra("gemini-3.6-flash", 0, 0, 0, 14, "analisis", estado="saturado", dir_work=sano, ts=base + 4900)
        registra("gemini-3.5-flash", 0, 0, 0, 1, "analisis", estado="cuota", dir_work=sano, ts=base + 4900,
                 error="429 RESOURCE_EXHAUSTED quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier' quotaValue: '20'")
        rs = resumen(cfg, ahora=base + 5000, dir_work=sano)
        caso("un modelo saturado hace un momento se marca «saturado» y uno al que Google dijo que se acabó su cuota DIARIA «agotado»: «usando» pasa al que sigue",
             [x["rol"] for x in rs["modelos"]] == ["saturado", "agotado", "usando"] and rs["modelos"][1]["restantes"] == 0
             and rs["hero"]["ahora"]["modelo"] == "gemini-3.1-flash-lite" and rs["modelos"][0]["salud"] == "saturado",
             [(x["modelo"], x["rol"], x["restantes"]) for x in rs["modelos"]])
        caso("si Google contesta «limit: 0» (el plan gratis no da cuota a ese modelo: imagen, Pro…) es «no disponible», no «cuota», y no se apunta como un límite",
             clasifica("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 0, model: gemini-3.1-flash-image") == "no_disponible"
             and clasifica("429 RESOURCE_EXHAUSTED quotaValue: '0'") == "no_disponible" and clasifica("429 RESOURCE_EXHAUSTED quotaValue: '20'") == "cuota"
             and clasifica("429 RESOURCE_EXHAUSTED. limit: 20, model: gemini-3.6-flash") == "cuota"
             and detecta_limite("429 quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier' quotaValue: '0' 'model': 'gemini-x'", "gemini-x", dir_work=sano) is None)
        rs2 = resumen(cfg, ahora=base + 4900 + 11 * 60, dir_work=sano)
        caso("a los 10 minutos el saturado vuelve a ser el primero; el agotado del día sigue fuera hasta que se reinicie",
             [x["rol"] for x in rs2["modelos"]] == ["usando", "agotado", "siguiente"], [(x["modelo"], x["rol"]) for x in rs2["modelos"]])
        caso("un 429 dice qué límite fue: el del día, el del minuto o el de tokens; y un 404 es «no disponible»",
             dimension_429("quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'") == "rpd" and dimension_429("quotaId: 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'") == "rpm"
             and dimension_429("quotaId: 'GenerateContentInputTokensPerModelPerMinute-FreeTier'") == "tpm" and dimension_429("cuota") == ""
             and clasifica("404 NOT_FOUND. models/x is not found") == "no_disponible" and clasifica("403 PERMISSION_DENIED") == "no_disponible"
             and clasifica("403 PERMISSION_DENIED. Your project has been denied access. Please contact support.") == "error"
             and es_proyecto_bloqueado("403 PERMISSION_DENIED: Your project has been denied access") and not es_proyecto_bloqueado("403 PERMISSION_DENIED")
             and not es_proyecto_bloqueado("429 quota exceeded"))
        # una ruta que no se puede escribir no rompe nada
        registra("x", dir_work=Path(tmp) / "gemini_uso.jsonl" / "no" / "existe" / "\0")
        caso("apuntar el uso jamás lanza (aunque la carpeta no exista o esté mal)", True)
    return filas_
