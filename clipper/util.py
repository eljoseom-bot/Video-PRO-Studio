"""Utilidades compartidas."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path

_T0 = time.time()

# Windows no deja lanzar un comando de más de 32.767 caracteres: CreateProcess falla con WinError 206, que Python
# convierte en FileNotFoundError — y el programa lo contaba como «No se encontró ffmpeg». Pasó de verdad (2026-09-23):
# el render de los vídeos -3 y -6 del autor falló 4 veces así; su comando medía 36.000-47.866 caracteres (el filtro de
# un vídeo de 23 min con ~20 tramos de movimiento suave, cuyas expresiones de `perspective` ocupan ~750 cada una).
_LINEA_LARGA = 8000                # a partir de aquí el `-filter_complex` va en un archivo
_OPCION_FILTRO: str | None = None


def _opcion_filtro_archivo() -> str:
    """Cómo se le pasa a ESTE ffmpeg un filtro guardado en un archivo: `-/filter_complex <archivo>` (ffmpeg 7 en
    adelante; en el 9 que usa el autor el viejo `-filter_complex_script` ya ni existe: «Unrecognized option»)."""
    global _OPCION_FILTRO
    if _OPCION_FILTRO is None:
        _OPCION_FILTRO = "-/filter_complex"
        try:
            v = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, timeout=20).stdout
            m = re.search(r"ffmpeg version n?(\d+)\.", v or "")
            if m and int(m.group(1)) < 7:
                _OPCION_FILTRO = "-filter_complex_script"
        except Exception:  # noqa: BLE001 — sin poder preguntar, la forma de las versiones actuales
            pass
    return _OPCION_FILTRO


def filtro_a_archivo(cmd: list[str], siempre: bool = False) -> tuple[list[str], Path | None]:
    """Si `cmd` lleva un `-filter_complex` y la línea de comandos es larga (o `siempre`), el filtro se escribe en un
    archivo temporal y se le pasa a ffmpeg por ahí: así no hay límite de longitud. Devuelve (comando, archivo que hay
    que borrar cuando ffmpeg termine, o None). El contenido es EXACTAMENTE el mismo texto que iría en la línea de
    comandos (ffmpeg lo lee tal cual, en UTF-8)."""
    if "-filter_complex" not in cmd:
        return cmd, None
    if not siempre and len(subprocess.list2cmdline(cmd)) < _LINEA_LARGA:
        return cmd, None
    i = cmd.index("-filter_complex")
    fd, ruta = tempfile.mkstemp(prefix="ffmpeg_filtro_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(cmd[i + 1])
    return cmd[:i] + [_opcion_filtro_archivo(), ruta] + cmd[i + 2:], Path(ruta)


def error_de_arranque(cmd: list[str], exc: BaseException) -> str | None:
    """Si un proceso no arrancó porque el COMANDO es demasiado largo (WinError 206), el mensaje para el log: eso no es
    un tropiezo pasajero (reintentar no sirve) ni que falte ffmpeg. None si es otra cosa."""
    if getattr(exc, "winerror", None) == 206:
        return (f"el comando de {cmd[0]} mide {len(subprocess.list2cmdline(cmd)):,} caracteres y Windows no deja pasar "
                f"de 32.767 (WinError 206): no es que falte {cmd[0]}. Avisa para revisarlo.")
    return None


def log(msg: str) -> None:
    elapsed = time.time() - _T0
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}  +{elapsed:6.1f}s] {msg}", flush=True)


def run(cmd: list[str], capture: bool = False, reintentos: int = 4) -> str:
    """Ejecuta un comando. Lanza excepción con stderr útil si falla. Si el SISTEMA no logra ni
    arrancar el proceso (`FileNotFoundError`/`PermissionError` — un antivirus escaneando el .exe
    justo en ese instante es el sospechoso más común, aunque el programa SÍ esté instalado)
    reintenta solo, unas pocas veces con espera creciente, antes de rendirse — el autor lo pidió
    (2026-09-23): quiere que TODO el proceso corra sin que un tropiezo pasajero le exija hacer
    clic en «Reintentar» a mano. Un comando que SÍ arrancó pero falló de verdad (código de salida
    distinto de 0) no se reintenta aquí — eso ya lo maneja quien llama. Un `-filter_complex` largo va por archivo
    (`filtro_a_archivo`): Windows no deja lanzar comandos de más de 32.767 caracteres."""
    cmd, tmp = filtro_a_archivo(cmd)
    try:
        ultimo: Exception | None = None
        for intento in range(reintentos):
            try:
                proc = subprocess.run(
                    cmd,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE if capture else None,
                    stderr=subprocess.PIPE,
                )
                return proc.stdout or ""
            except (FileNotFoundError, PermissionError) as exc:
                largo = error_de_arranque(cmd, exc)
                if largo:
                    raise RuntimeError(largo) from exc
                ultimo = exc
                if intento + 1 >= reintentos:
                    break
                espera = min(20.0, 3.0 * (2 ** intento))       # 3, 6, 12, 20s...
                log(f"  {cmd[0]} no arrancó (¿algo lo bloqueó un instante?); "
                    f"reintento {intento + 2}/{reintentos} en {espera:.0f}s")
                time.sleep(espera)
            except subprocess.CalledProcessError as exc:
                tail = (exc.stderr or "")[-2000:]
                raise RuntimeError(
                    f"Falló el comando:\n  {' '.join(cmd[:6])} ...\n\n{tail}"
                ) from exc
        raise RuntimeError(
            f"No se encontró el ejecutable '{cmd[0]}' tras {reintentos} intentos. ¿Está instalado y en el PATH?"
        ) from ultimo
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """minúsculas, sin acentos, sin puntuación — para matchear el léxico."""
    text = strip_accents(text.lower())
    return re.sub(r"[^\w\s]", " ", text)


def reemplaza(tmp, destino, intentos: int = 8) -> None:
    """`tmp` -> `destino` (el «guardar atómico»: se escribe un temporal y se cambia de nombre) CON REINTENTOS: en Windows un antivirus, el indexador o una lectura al mismo
    tiempo dejan el archivo «en uso» unos milisegundos y `replace` da «acceso denegado» (WinError 5). Pasó de verdad al marcar ideas (5-oct-2026, 2 veces: el clic no hacía
    nada). Si no cede tras los reintentos, se escribe directo (no atómico, pero lo que se quería guardar no se pierde)."""
    for k in range(intentos):
        try:
            tmp.replace(destino)
            return
        except PermissionError:
            time.sleep(0.05 * (k + 1))
    destino.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
    tmp.unlink(missing_ok=True)


def ass_time(seconds: float) -> str:
    """Formato de tiempo de ASS: H:MM:SS.cc"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def hhmmss(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def safe_slug(text: str, maxlen: int = 48) -> str:
    text = strip_accents(text.lower())
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:maxlen] or "clip"


_GEMINI_TRANSIENT = ("429", "500", "502", "503", "504", "resource_exhausted",
                     "unavailable", "overloaded", "high demand", "deadline",
                     "timed out", "timeout", "connection", "reset by peer", "try again")


class ProyectoBloqueado(RuntimeError):
    """Google bloqueó el PROYECTO de la clave de Gemini (403 «Your project has been denied access»). Probar más modelos no sirve: se corta al primero."""


MENSAJE_PROYECTO_BLOQUEADO = (
    "Google ha bloqueado el proyecto de tu clave de Gemini (403 PERMISSION_DENIED «Your project has been denied access»): ningún modelo puede responder, "
    "así que no se probó el resto. «Reshuffle» no lo arregla. Qué hacer: (1) mira en AI Studio → Projects si el proyecto sale «Restricted» y pide una "
    "revisión manual a Google (foro de AI Studio o soporte); (2) mientras tanto, pega una clave de OTRO proyecto o cuenta en «Uso» (cuadro «La clave de "
    "Gemini»), sin activar facturación. Tu grabación y su transcripción se conservan: al tener clave, dale «Reintentar».")


def _uso_ok(modelo: str, resp, seg: float, etiqueta: str) -> dict:
    """Apunta una petición que respondió (pestaña «Uso») y devuelve sus tokens. `salida` incluye el pensamiento, que Google cuenta."""
    um = getattr(resp, "usage_metadata", None)
    pens = int(getattr(um, "thoughts_token_count", 0) or 0)
    uso = {"entrada": int(getattr(um, "prompt_token_count", 0) or 0),
           "salida": int(getattr(um, "candidates_token_count", 0) or 0) + pens, "pensamiento": pens}
    try:
        from . import uso as _uso
        _uso.registra(modelo, uso["entrada"], uso["salida"], pens, seg, etiqueta)
    except Exception:                                   # noqa: BLE001 — contar nunca rompe la llamada
        pass
    return uso


def _uso_fallo(modelo: str, exc: Exception, seg: float, etiqueta: str) -> None:
    try:
        from . import uso as _uso
        _uso.registra(modelo, seg=seg, tipo=etiqueta, estado=_uso.clasifica(exc), error=str(exc)[:2000])
    except Exception:                                   # noqa: BLE001
        pass


def call_gemini(client, types, model: str, prompt: str,
                system: str | None = None, tries: int = 6, etiqueta: str = "temas") -> str:
    """Una llamada a Gemini con reintento si responde algo pasajero (sobrecarga,
    cuota, timeout). `client`/`types` son los que devuelve `genai.Client(...)` /
    `google.genai.types` — cada llamador abre su propio cliente (director.py,
    temas.py), esto solo centraliza el reintento para no repetirlo en cada uno."""
    last = None
    for attempt in range(tries):
        t0 = time.time()
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.4,
                    response_mime_type="application/json",
                    max_output_tokens=16384,
                ),
            )
            _uso_ok(model, resp, time.time() - t0, etiqueta)
            return (resp.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            _uso_fallo(model, exc, time.time() - t0, etiqueta)
            last = exc
            msg = str(exc).lower()
            transient = any(t in msg for t in _GEMINI_TRANSIENT)
            if not transient or attempt == tries - 1:
                raise
            wait = min(60, 5 * (2 ** attempt))     # 5,10,20,40,60 — aguanta picos
            log(f"  Gemini ocupado ({type(exc).__name__}); reintento en {wait}s "
                f"({attempt+1}/{tries-1})")
            time.sleep(wait)
    raise last  # type: ignore[misc]


def nombre_modelo(modelo: str) -> str:
    """'gemini-3.5-flash-lite' -> 'Gemini 3.5 Flash-Lite' (para mostrárselo a
    el autor en el panel: con qué modelo se analizó su vídeo)."""
    m = str(modelo or "").strip()
    if not m:
        return "Gemini"
    partes = [p for p in m.split("-") if p and p.lower() != "gemini"]
    version = [p for p in partes if re.fullmatch(r"\d+(\.\d+)?", p)]
    resto = [p for p in partes if p not in version]
    tier: list[str] = []
    for p in resto:
        if p.lower() in ("latest", "preview"):
            continue
        tier.append(p.capitalize())
    extra = " (latest)" if "latest" in [p.lower() for p in resto] else (
        " (preview)" if "preview" in [p.lower() for p in resto] else "")
    return " ".join(["Gemini"] + version + ["-".join(tier)] if tier else ["Gemini"] + version).strip() + extra


def _genera_con_esquema(client, types, modelo: str, prompt, system: str | None,
                        timeout_s: float, esquema, config_extra: dict | None = None):
    """Una llamada a `modelo`. Con `esquema` (una clase pydantic) Gemini está OBLIGADO a
    devolver el JSON con esa forma: se acabaron el «Extra data» y los campos que faltan.
    Si el modelo rechaza el esquema (400 «schema…»), se repite SIN esquema — el parser
    tolerante de siempre sigue ahí — en vez de perder el modelo entero por un detalle.
    `prompt` puede ser un texto o una lista de partes (un vídeo subido + el texto: `clipper/vision.py`);
    `config_extra` añade opciones a la petición (p. ej. `media_resolution` para ese vídeo)."""
    kw = dict(system_instruction=system, temperature=0.4, response_mime_type="application/json",
              # el plan de un vídeo de 23 min ya ocupó hasta 7.977 tokens de salida (con el «pensamiento»); uno de 45-60
              # min se acercaba a los 16.384 de antes y un JSON cortado a medias no sirve. Los Flash/Flash-Lite de la
              # cadena (versión 3+) admiten 65.536; escribir más no gasta cuota del plan gratis (que limita la ENTRADA)
              max_output_tokens=32768,
              # un modelo saturado a veces tarda 90-110 s en devolver el 503: tope por intento
              http_options=types.HttpOptions(timeout=int(timeout_s * 1000)), **(config_extra or {}))
    if esquema is not None:
        try:
            return client.models.generate_content(
                model=modelo, contents=prompt,
                config=types.GenerateContentConfig(response_schema=esquema, **kw))
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if any(t in msg for t in _GEMINI_TRANSIENT) or not any(
                    t in msg for t in ("schema", "invalid_argument", "400", "unsupported")):
                raise
            log(f"  {modelo} no acepta el esquema de respuesta; sigo sin esquema")
    return client.models.generate_content(model=modelo, contents=prompt,
                                          config=types.GenerateContentConfig(**kw))


def call_gemini_cadena(client, types, modelos: list[str], prompt,
                       system: str | None = None, valida=None,
                       reintento_s: float = 90.0, pasadas: int = 2,
                       timeout_s: float = 100.0, esquema=None, etiqueta: str = "analisis",
                       config_extra: dict | None = None):
    """Plan de contingencia: prueba los `modelos` EN ORDEN (el mejor primero) y
    devuelve `(resultado, modelo_que_respondió, uso)` con el primero que
    funcione. Un modelo que sale "ocupado/saturado" (429/5xx/"high demand")
    se insiste en ÉL MISMO —con espera creciente entre intentos (5, 10, 20,
    40s...)— durante `reintento_s` segundos (90 por defecto) antes de pasar
    al siguiente: el autor lo pidió (2026-09-23) porque el mejor modelo suele
    estar saturado solo un momento, y vale más esperarlo un poco que bajar
    de calidad enseguida. Un modelo que ya no existe o no sirve (404, 403,
    400…) se descarta al instante — los modelos se retiran con el tiempo, y
    a `gemini-2.5-flash` le pasó: aparecía en la lista y daba 404. Si
    NINGUNO responde, espera un poco y repite la cadena entera (hasta
    `pasadas`); si sigue igual, lanza la última excepción.

    Los intentos que fallan por saturación no consumen créditos (no llegan a
    generar nada); solo cuenta la llamada que responde. `valida(texto)` (opcional)
    convierte/valida la respuesta — si lanza, ese intento cuenta como fallo y se
    pasa al siguiente modelo (respuesta vacía o JSON roto).
    `uso` = {"entrada": tokens, "salida": tokens (incluye el pensamiento), "pensamiento": tokens}
    (0 si el SDK no lo informa)."""
    descartados: set[str] = set()
    ultimo: Exception | None = None
    for pasada in range(pasadas):
        vivos = [m for m in modelos if m not in descartados]
        if not vivos:
            break
        for modelo in vivos:
            t_inicio_modelo = time.time()
            intento = 0
            while True:
                intento += 1
                t0 = time.time()
                respondio = False
                try:
                    resp = _genera_con_esquema(client, types, modelo, prompt, system, timeout_s, esquema,
                                               **({"config_extra": config_extra} if config_extra else {}))
                    # la salida que Google COBRA (y cuenta para la cuota) incluye el «pensamiento» del modelo, que
                    # `candidates_token_count` no trae: sin sumarlo se veía mucho menos gasto del real
                    uso = _uso_ok(modelo, resp, time.time() - t0, etiqueta)      # (también lo apunta en la pestaña «Uso»)
                    respondio = True
                    texto = (resp.text or "").strip()
                    if not texto:
                        raise ValueError("respuesta vacía")
                    resultado = valida(texto) if valida else texto
                    return resultado, modelo, uso
                except Exception as exc:  # noqa: BLE001
                    if not respondio:
                        _uso_fallo(modelo, exc, time.time() - t0, etiqueta)
                    ultimo = exc
                    msg = str(exc).lower()
                    corto = str(exc).replace("\n", " ")[:110]
                    from . import uso as _u
                    if _u.es_proyecto_bloqueado(msg):         # el proyecto entero está bloqueado: los demás modelos darían lo mismo (y gastarían tiempo)
                        log(f"  {modelo}: {corto}. Es el PROYECTO de la clave, no el modelo: no pruebo los demás.")
                        raise ProyectoBloqueado(MENSAJE_PROYECTO_BLOQUEADO + " [Google dijo: " + str(exc).replace("\n", " ")[:160] + "]") from exc
                    if any(t in msg for t in _GEMINI_TRANSIENT):
                        transcurrido = time.time() - t_inicio_modelo
                        espera = min(40.0, 5.0 * (2 ** (intento - 1)))     # 5,10,20,40,40... — aguanta picos sin machacar la API
                        if transcurrido + espera < reintento_s:
                            log(f"  {modelo} saturado ({corto}); reintento en {espera:.0f}s "
                                f"({transcurrido:.0f}s de {reintento_s:.0f}s insistidos)")
                            time.sleep(espera)
                            continue
                        log(f"  {modelo} sigue saturado tras {transcurrido:.0f}s; paso al siguiente modelo")
                        break
                    log(f"  {modelo} no sirve ahora ({corto}); paso al siguiente modelo")
                    descartados.add(modelo)
                    break
        vivos = [m for m in modelos if m not in descartados]
        if vivos and pasada + 1 < pasadas:
            espera = 45 * (pasada + 1)
            log(f"  Ningún modelo respondió; espero {espera}s y repito la cadena "
                f"({pasada + 2}/{pasadas})")
            time.sleep(espera)
    raise ultimo or RuntimeError("no hay ningún modelo de Gemini configurado")


def prueba() -> list[dict]:
    """Dos cosas, sin red y sin tardar de verdad: (1) un `_genera_con_esquema` falso dispara
    errores controlados (saturado, no disponible...) y se comprueba que `call_gemini_cadena`
    insiste en EL MISMO modelo por TIEMPO (no por cuenta fija) antes de bajar al siguiente,
    respeta el backoff creciente, descarta al instante el que ya no existe, y sigue repitiendo la
    cadena entera (`pasadas`) si hace falta; (2) `run()` reintenta sola si el sistema no logra
    arrancar el comando, pero NO si el comando arrancó y falló de verdad. `time.sleep` y el
    registro de uso quedan interceptados en ambas: no tarda de verdad ni escribe en el uso real
    del autor."""
    global _genera_con_esquema
    from . import uso as _uso
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": detalle})

    class _Resp:
        def __init__(self, texto):
            self.text = texto
            self.usage_metadata = None

    real_genera, real_sleep, real_registra = _genera_con_esquema, time.sleep, _uso.registra
    _uso.registra = lambda *a, **k: None

    def con(fn):
        global _genera_con_esquema
        dormidas: list[float] = []
        time.sleep = lambda s: dormidas.append(s)
        _genera_con_esquema = fn
        return dormidas

    try:
        # 1) responde bien a la primera: ni reintenta ni duerme
        llamadas = []
        dormidas = con(lambda c, t, m, p, s, to, e: (llamadas.append(m), _Resp("ok"))[1])
        res, modelo, _ = call_gemini_cadena(None, None, ["a", "b"], "prompt", valida=lambda t: t)
        r("responde a la primera sin reintentar ni dormir", res == "ok" and modelo == "a" and llamadas == ["a"] and not dormidas,
          f"llamadas={llamadas} dormidas={dormidas}")

        # 2) saturado dos veces y luego responde: insiste en EL MISMO modelo (no baja a «b»), backoff 5,10
        llamadas = []
        def intermitente(c, t, modelo, p, s, to, e):
            llamadas.append(modelo)
            if modelo == "a" and llamadas.count("a") <= 2:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: high demand ahora mismo")
            return _Resp("ok")
        dormidas = con(intermitente)
        res, modelo, _ = call_gemini_cadena(None, None, ["a", "b"], "prompt", valida=lambda t: t, reintento_s=90.0)
        r("insiste en el MISMO modelo saturado en vez de bajar al instante",
          modelo == "a" and llamadas == ["a", "a", "a"], f"llamadas={llamadas}")
        r("espera creciente entre reintentos (5s, 10s)", dormidas == [5.0, 10.0], str(dormidas))

        # 3) saturado TODO el presupuesto de reintento: pasa al siguiente modelo (y ahí responde)
        llamadas = []
        def siempre_saturado_a(c, t, modelo, p, s, to, e):
            llamadas.append(modelo)
            if modelo == "a":
                raise RuntimeError("503 UNAVAILABLE: the model is overloaded")
            return _Resp("ok")
        dormidas = con(siempre_saturado_a)
        res, modelo, _ = call_gemini_cadena(None, None, ["a", "b"], "prompt", valida=lambda t: t, reintento_s=12.0)
        r("con el presupuesto agotado SÍ pasa al siguiente modelo",
          modelo == "b" and llamadas[0] == "a" and llamadas[-1] == "b" and llamadas.count("a") >= 2,
          f"llamadas={llamadas} dormidas={dormidas}")

        # 4) error NO transitorio (404): se descarta al instante, sin dormir ni reintentar
        llamadas = []
        def no_existe(c, t, modelo, p, s, to, e):
            llamadas.append(modelo)
            if modelo == "a":
                raise RuntimeError("404 NOT_FOUND: model not found")
            return _Resp("ok")
        dormidas = con(no_existe)
        res, modelo, _ = call_gemini_cadena(None, None, ["a", "b"], "prompt", valida=lambda t: t)
        r("un modelo que no existe (404) se descarta sin reintentar ni dormir",
          modelo == "b" and llamadas == ["a", "b"] and not dormidas, f"llamadas={llamadas}")

        # 5) TODOS saturados en la 1ª pasada: la 2ª pasada (tras la espera entre pasadas) sí responde
        llamadas = []
        def falla_una_vez_cada_uno(c, t, modelo, p, s, to, e):
            llamadas.append(modelo)
            if llamadas.count(modelo) == 1:
                raise RuntimeError("429 high demand")
            return _Resp("ok")
        dormidas = con(falla_una_vez_cada_uno)
        res, modelo, _ = call_gemini_cadena(None, None, ["a", "b"], "prompt", valida=lambda t: t,
                                            reintento_s=0.01, pasadas=2)
        r("si TODOS fallan en la 1ª pasada, la 2ª (tras esperar) sí resuelve",
          res == "ok" and 45.0 in dormidas, f"llamadas={llamadas} dormidas={dormidas}")

        # 5b) proyecto BLOQUEADO por Google (403 «denied access»): corta al primer modelo, sin dormir, sin pasadas y con el mensaje claro
        llamadas = []
        def proyecto_bloqueado(c, t, modelo, p, s, to, e):
            llamadas.append(modelo)
            raise RuntimeError("403 PERMISSION_DENIED. {'error': {'code': 403, 'message': 'Your project has been denied access. Please contact support.', 'status': 'PERMISSION_DENIED'}}")
        dormidas = con(proyecto_bloqueado)
        try:
            call_gemini_cadena(None, None, ["a", "b", "c"], "prompt", valida=lambda t: t)
            r("proyecto bloqueado: corta al primer modelo", False, "no lanzó nada")
        except ProyectoBloqueado as exc:
            r("proyecto bloqueado: corta al primer modelo (1 sola llamada, sin dormir ni repetir pasadas)", llamadas == ["a"] and not dormidas, f"llamadas={llamadas} dormidas={dormidas}")
            r("proyecto bloqueado: el mensaje dice qué hacer y no parece saturación (no se reintenta solo)",
              "Reshuffle" in str(exc) and "Uso" in str(exc) and not any(t in str(exc).lower() for t in _GEMINI_TRANSIENT), str(exc)[:120])
        except Exception as exc:  # noqa: BLE001
            r("proyecto bloqueado: corta al primer modelo", False, f"lanzó otra cosa: {type(exc).__name__}")
        # un 403 de UN modelo que no existe/no está para esa clave NO es proyecto bloqueado: se sigue con el siguiente
        llamadas = []
        def un_403_normal(c, t, modelo, p, s, to, e):
            llamadas.append(modelo)
            if modelo == "a":
                raise RuntimeError("403 PERMISSION_DENIED: model not available for this key")
            return _Resp("ok")
        con(un_403_normal)
        res, modelo, _ = call_gemini_cadena(None, None, ["a", "b"], "prompt", valida=lambda t: t)
        r("un 403 solo de un modelo no corta la cadena", modelo == "b" and llamadas == ["a", "b"], f"llamadas={llamadas}")

        # 6) ninguno responde en NINGUNA pasada: lanza la última excepción (no la traga en silencio)
        def siempre_falla(c, t, modelo, p, s, to, e):
            raise RuntimeError("429 high demand, nunca responde")
        con(siempre_falla)
        try:
            call_gemini_cadena(None, None, ["a"], "prompt", valida=lambda t: t, reintento_s=0.01, pasadas=2)
            r("si nadie responde en ninguna pasada, lanza la excepción", False, "no lanzó nada")
        except RuntimeError as exc:
            r("si nadie responde en ninguna pasada, lanza la excepción", "high demand" in str(exc), str(exc))
    finally:
        _genera_con_esquema, time.sleep, _uso.registra = real_genera, real_sleep, real_registra

    # --- run(): reintenta sola si el SISTEMA no arranca el comando (antivirus escaneando el
    # .exe, lo más común), sin pedir un clic manual — el autor lo pidió (2026-09-23). subprocess.run
    # interceptado: nada de esto arranca un proceso de verdad ni tarda de verdad.
    import subprocess as _sp

    class _R:
        def __init__(self, out_):
            self.stdout, self.stderr = out_, ""

    real_sp_run, real_sleep2 = _sp.run, time.sleep
    dormidas2: list[float] = []
    time.sleep = lambda s: dormidas2.append(s)
    llamadas2: list = []
    try:
        def falla_una_vez(cmd, **kw):
            llamadas2.append(cmd)
            if len(llamadas2) == 1:
                raise FileNotFoundError(2, "No such file or directory")
            return _R("ok")

        _sp.run = falla_una_vez
        out_txt = run(["ffmpeg", "-x"], capture=True)
        r("run(): si el sistema no arranca el comando, reintenta sola hasta que sí",
          out_txt == "ok" and len(llamadas2) == 2, f"llamadas={len(llamadas2)}")

        llamadas2.clear()
        dormidas2.clear()

        def nunca_arranca(cmd, **kw):
            llamadas2.append(cmd)
            raise FileNotFoundError(2, "No such file or directory")

        _sp.run = nunca_arranca
        try:
            run(["ffmpeg", "-x"], reintentos=3)
            r("run(): si NUNCA arranca, se rinde tras agotar los reintentos", False)
        except RuntimeError as exc:
            r("run(): si NUNCA arranca, se rinde tras agotar los reintentos",
              len(llamadas2) == 3 and "ffmpeg" in str(exc), f"llamadas={len(llamadas2)} · {exc}")

        llamadas2.clear()
        dormidas2.clear()

        def falla_de_verdad(cmd, **kw):
            llamadas2.append(cmd)
            raise _sp.CalledProcessError(1, cmd, stderr="algo de verdad salió mal")

        _sp.run = falla_de_verdad
        try:
            run(["ffmpeg", "-x"])
            r("run(): un comando que SÍ arrancó pero falló de verdad no se reintenta (1 sola llamada)", False)
        except RuntimeError as exc:
            r("run(): un comando que SÍ arrancó pero falló de verdad no se reintenta (1 sola llamada)",
              len(llamadas2) == 1 and "algo de verdad" in str(exc), f"llamadas={len(llamadas2)} · {exc}")
    finally:
        _sp.run, time.sleep = real_sp_run, real_sleep2
    return out


def snap_phrase_end(t: float, words: list[dict], max_ahead: float = 4.0) -> float:
    """Extiende `t` hasta el FINAL de la frase que está sonando: la próxima
    pausa >=0.45 s (o, si no hay, el final de la palabra en curso), sin pasarse
    de `max_ahead`. Para que el tramo no corte a mitad de frase."""
    if not words:
        return t
    limit = t + max_ahead
    best = t
    for i, w in enumerate(words):
        we = float(w.get("end", 0))
        if we < t - 0.05:
            continue
        if float(w.get("start", 0)) > limit:
            break
        best = min(limit, we)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (float(nxt["start"]) - we) if nxt else 99.0
        if gap >= 0.45:
            return min(limit, we + 0.12)
    return best


def parse_hms(txt) -> float | None:
    """'1:23:45' -> 5025.0 · '2:30' -> 150.0 · '90' -> 90.0 · '1:05.5' -> 65.5
    Devuelve None si no se puede interpretar."""
    if txt is None:
        return None
    if isinstance(txt, (int, float)):
        return float(txt)
    s = str(txt).strip().replace(",", ".")
    if not s:
        return None
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total if total >= 0 else None
