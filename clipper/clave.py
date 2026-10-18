"""Cambiar la clave de Gemini de ESTE programa (los vídeos) sin tocar la de los Shorts.

La escribe el autor en un cuadro del panel (esta herramienta nunca escribe claves por su cuenta); aquí solo se comprueba con Google
(1 petición mínima) y, SI FUNCIONA, se guarda en `.env` (que no va a Git) y en el panel en marcha, así que los vídeos siguientes ya
la usan sin reiniciar nada. La clave NUNCA se imprime: ni en el registro, ni en un error, ni en la respuesta del panel (solo sus 4
últimas letras, las mismas que enseña AI Studio en su lista de claves).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

RE_CLAVE = re.compile(r"^[A-Za-z0-9._\-]{30,120}$")              # las claves de Google son ASCII, sin espacios
MODELOS_PRUEBA = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash")     # primero los Lite: 500 al día, no gastan lo escaso


def limpia(k) -> str:
    return str(k or "").strip().strip('"').strip("'").strip()


def ultimos4(k: str) -> str:
    return (k or "")[-4:]


def _ruta_env(ruta=None) -> Path:
    if ruta:
        return Path(ruta)
    from . import config
    return config.ROOT / ".env"


def escribe_env(clave: str, ruta=None) -> None:
    """Pone `GEMINI_API_KEY=<clave>` en el .env sin tocar el resto (comentarios, GROQ…, saltos de línea). Escritura atómica."""
    ruta = _ruta_env(ruta)
    try:
        texto = ruta.read_bytes().decode("utf-8")
    except OSError:
        texto = ""
    nl = "\r\n" if "\r\n" in texto else "\n"
    lineas = texto.splitlines()
    hecho = False
    for i, linea in enumerate(lineas):
        if re.match(r"^\s*GEMINI_API_KEY\s*=", linea):           # (una línea comentada empieza por # y no entra aquí)
            lineas[i] = f"GEMINI_API_KEY={clave}"
            hecho = True
    if not hecho:
        lineas.append(f"GEMINI_API_KEY={clave}")
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_bytes((nl.join(lineas) + nl).encode("utf-8"))
    os.replace(tmp, ruta)


def _llama_real(clave: str, modelo: str) -> None:
    """Una petición mínima con ESA clave (y no con la del entorno)."""
    from google import genai
    from google.genai import types
    from .util import _uso_ok
    t0 = time.time()
    cliente = genai.Client(api_key=clave)      # (hay que guardarlo en una variable: si no, Python lo recoge y el SDK cierra la conexión antes de enviar)
    resp = cliente.models.generate_content(
        model=modelo, contents="Responde solo con la palabra OK.",
        config=types.GenerateContentConfig(max_output_tokens=64, temperature=0, http_options=types.HttpOptions(timeout=25000)))
    _uso_ok(modelo, resp, time.time() - t0, "prueba")            # gasta una petición de la cuota nueva: se apunta en «Uso»


def prueba_clave(clave: str, modelos=None, _llama=None) -> dict:
    """{ok, estado, modelo, mensaje}. estado: ok | cuota (la clave vale, ese modelo está sin cuota ahora) | invalida | sin_permiso | saturado | error."""
    llama = _llama or _llama_real
    if modelos is None:                                                # los Flash-Lite primero (500 al día): la cadena sale sola de clipper/modelos.py
        try:
            from . import config, modelos as _m
            modelos = _m.para_prueba(config.load())
        except Exception:                                               # noqa: BLE001
            modelos = MODELOS_PRUEBA
    for m in modelos:
        try:
            llama(clave, m)
            return {"ok": True, "estado": "ok", "modelo": m, "mensaje": ""}
        except Exception as exc:                                    # noqa: BLE001
            msg = str(exc).replace(clave, "***") if clave else str(exc)
            bajo = msg.lower()
            if any(t in bajo for t in ("api key not valid", "api_key_invalid", "invalid api key", "api key expired", "401", "unauthenticated",
                                       "invalid authentication credentials", "no api key was provided")):
                return {"ok": False, "estado": "invalida", "modelo": m, "mensaje":
                        "Google dice que esa clave no es válida. Cópiala otra vez, entera y sin espacios, desde AI Studio → API keys "
                        "(con la cuenta de Google con la que la creaste)."}
            if "permission_denied" in bajo or "403" in bajo:
                return {"ok": False, "estado": "sin_permiso", "modelo": m, "mensaje":
                        "La clave existe, pero Google le niega el permiso (403). Mira en AI Studio que el proyecto de esa clave no esté suspendido ni tenga restricciones; "
                        "si lo está, crea una clave nueva en otro proyecto."}
            if "429" in bajo or "resource_exhausted" in bajo:
                return {"ok": True, "estado": "cuota", "modelo": m, "mensaje": "La clave vale; ese modelo está sin cuota ahora mismo."}
            if any(t in bajo for t in ("404", "not_found", "not found", "503", "504", "500", "unavailable", "overloaded", "high demand",
                                       "deadline", "timed out", "timeout", "connection")):
                continue                                             # ese modelo no responde: se prueba el siguiente
            return {"ok": False, "estado": "error", "modelo": m, "mensaje": "Google devolvió un error que no reconozco: " + msg.replace("\n", " ")[:160]}
    return {"ok": False, "estado": "saturado", "modelo": "", "mensaje":
            "Gemini no respondió ahora mismo (está saturado), así que no pude comprobar la clave. Inténtalo otra vez en unos minutos: no he cambiado nada."}


def cambia_clave(nueva, aparte: bool = True, ruta_env=None, dir_work=None, _llama=None, modelos=None) -> dict:
    """Comprueba la clave con Google y, solo si funciona, la guarda. Devuelve {ok, ...} sin incluir NUNCA la clave entera."""
    k = limpia(nueva)
    if not RE_CLAVE.match(k):
        return {"ok": False, "error": "Eso no parece una clave de Gemini: son letras y números seguidos, sin espacios, de unos 40 a 60 caracteres. "
                                      "Cópiala entera desde AI Studio → API keys."}
    actual = os.environ.get("GEMINI_API_KEY", "")
    if k == actual:
        return {"ok": False, "error": f"Es la MISMA clave que ya usa este programa (termina en …{ultimos4(k)}). Pega una distinta."}
    r = prueba_clave(k, modelos=modelos, _llama=_llama)
    if not r["ok"]:
        return {"ok": False, "error": r["mensaje"], "estado": r["estado"]}
    escribe_env(k, ruta_env)
    os.environ["GEMINI_API_KEY"] = k                                 # el panel en marcha (y los trabajos que lance) ya usan la nueva
    try:
        from . import uso
        uso.guarda_limites({"clave_aparte": bool(aparte), "clave_desde": time.time()}, dir_work)   # desde ahora la cuenta es de la cuota nueva
    except Exception:                                               # noqa: BLE001 — guardar la clave es lo importante
        pass
    return {"ok": True, "ultimos4": ultimos4(k), "antes": ultimos4(actual), "modelo": r["modelo"], "estado": r["estado"], "nota": r["mensaje"]}


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    K1, K2 = "AQ.ClaveFalsaDePruebaNumeroUno_0000000000000000-zzzzzzzzzz", "AIzaSyD-nueva_clave_de_prueba_1234567890abcd"
    caso("el formato: letras, números, punto, guion y guion bajo; sin espacios ni comillas dentro", bool(RE_CLAVE.match(K1)) and bool(RE_CLAVE.match(K2))
         and not RE_CLAVE.match("corta") and not RE_CLAVE.match("con espacios " + K1) and limpia(f' "{K2}" \n') == K2)
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_bytes(b"# Claves\r\nGEMINI_API_KEY=vieja1234567890\r\n\r\n# GROQ_API_KEY=otra\r\nGROQ_API_KEY=groq123\r\n")
        escribe_env(K2, env)
        t = env.read_bytes().decode("utf-8")
        caso("escribir la clave en .env cambia SOLO esa línea y conserva comentarios, otras claves y los saltos de línea",
             t == f"# Claves\r\nGEMINI_API_KEY={K2}\r\n\r\n# GROQ_API_KEY=otra\r\nGROQ_API_KEY=groq123\r\n" and not (Path(tmp) / ".env.tmp").exists(), repr(t[:120]))
        vacio = Path(tmp) / "nuevo.env"
        escribe_env(K2, vacio)
        caso("si no hay .env (o no trae la línea) la crea", vacio.read_text(encoding="utf-8").strip() == f"GEMINI_API_KEY={K2}")
        previo = os.environ.get("GEMINI_API_KEY")
        try:
            os.environ["GEMINI_API_KEY"] = K1
            caso("la MISMA clave que ya se usa se rechaza sin llamar a Google", not cambia_clave(K1, _llama=lambda *a: 1 / 0)["ok"]
                 and "MISMA" in cambia_clave(K1, _llama=lambda *a: 1 / 0)["error"])
            malas = {"API key not valid. Please pass a valid API key. [API_KEY_INVALID]": "invalida", "403 PERMISSION_DENIED": "sin_permiso",
                     "401 UNAUTHENTICATED. Request had invalid authentication credentials.": "invalida"}
            for texto, estado in malas.items():
                def _mala(c, m, texto=texto):
                    raise RuntimeError(f"400 INVALID_ARGUMENT. {texto} key={c}")
                r = cambia_clave(K2, ruta_env=env, dir_work=tmp, _llama=_mala, modelos=("m1",))
                caso(f"una clave que Google rechaza ({estado}) NO se guarda y el error no lleva la clave",
                     not r["ok"] and r["estado"] == estado and K2 not in str(r) and env.read_text(encoding="utf-8").count(K2) == 1 and os.environ["GEMINI_API_KEY"] == K1, r)
            llamados: list[str] = []

            def _seguido(c, m):
                llamados.append(m)
                if len(llamados) < 3:
                    raise RuntimeError("503 UNAVAILABLE. high demand")
            env.write_bytes(b"GEMINI_API_KEY=vieja\n")
            r = cambia_clave(K2, aparte=True, ruta_env=env, dir_work=tmp, _llama=_seguido, modelos=("m1", "m2", "m3"))
            caso("si un modelo está saturado prueba el siguiente; con la clave buena la guarda en .env, en el proceso y anota «clave aparte»",
                 r["ok"] and r["modelo"] == "m3" and r["ultimos4"] == K2[-4:] and K2 not in str({k: v for k, v in r.items()})
                 and env.read_text(encoding="utf-8") == f"GEMINI_API_KEY={K2}\n" and os.environ["GEMINI_API_KEY"] == K2, (r, llamados))
            from . import uso
            lim = uso.carga_limites(tmp)
            caso("al cambiar la clave la cuenta de «Uso» empieza de cero (clave_desde) y se marca la clave aparte",
                 lim["clave_aparte"] is True and lim["clave_desde"] and abs(lim["clave_desde"] - time.time()) < 5, lim)

            def _todo_saturado(c, m):
                raise RuntimeError("503 UNAVAILABLE")
            os.environ["GEMINI_API_KEY"] = K1
            env.write_bytes(b"GEMINI_API_KEY=vieja\n")
            r = cambia_clave(K2, ruta_env=env, dir_work=tmp, _llama=_todo_saturado, modelos=("m1", "m2"))
            caso("si Gemini está saturado y no se puede comprobar, no se cambia nada", not r["ok"] and r["estado"] == "saturado"
                 and env.read_text(encoding="utf-8") == "GEMINI_API_KEY=vieja\n" and os.environ["GEMINI_API_KEY"] == K1, r)
        finally:
            if previo is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previo
    return filas
