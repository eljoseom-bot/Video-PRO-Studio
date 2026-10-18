"""Pantalla de inicio (primera vez): las dos claves que el programa necesita y nadie más puede ponerle.

- **Gemini** (obligatoria): sin ella no hay análisis. Se pega en el panel y se guarda con `clave.cambia_clave` (se prueba con Google antes).
- **Cloudflare** (opcional): solo para las miniaturas con IA. Se pega ID de cuenta + token y se COMPRUEBA con Cloudflare (verificación
  del token: no gasta neuronas) antes de guardarlas.

Las claves van a `.env` (que no se sube a Git) y al panel en marcha: sirve desde el siguiente vídeo, sin reiniciar. NUNCA se imprimen
ni se devuelven enteras (solo las 4 últimas letras). Pensado para quien baje el proyecto de la comunidad: no hay ninguna clave mía dentro.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

RE_CUENTA = re.compile(r"^[0-9a-fA-F]{32}$")                          # el ID de cuenta de Cloudflare: 32 caracteres hexadecimales
RE_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{30,100}$")
_API = "https://api.cloudflare.com/client/v4"


def estado() -> dict:
    """Qué claves hay (sin enseñarlas). `listo` = lo imprescindible (Gemini) está."""
    g = os.environ.get("GEMINI_API_KEY", "").strip()
    cuenta, token = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip(), os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    try:
        from . import config
        propios = [str((v or {}).get("nombre") or k) for k, v in (config.load().get_path("videos.canales") or {}).items() if isinstance(v, dict) and v.get("_propio")]
    except Exception:  # noqa: BLE001
        propios = []
    try:
        from . import youtube
        yt = bool(youtube.available() and youtube.is_connected())
    except Exception:  # noqa: BLE001
        yt = False
    return {"gemini": {"hay": bool(g), "ultimos4": g[-4:] if g else ""},
            "cloudflare": {"hay": bool(cuenta and token), "ultimos4": token[-4:] if token else ""},
            "canal": {"hay": bool(propios), "nombres": propios}, "youtube": {"conectado": yt},
            "listo": bool(g)}


def escribe_env_var(nombre: str, valor: str, ruta=None) -> None:
    """Pone `NOMBRE=valor` en el .env sin tocar el resto (comentarios, otras claves, saltos de línea). Escritura atómica con reintentos."""
    from . import config
    from .util import reemplaza
    ruta = Path(ruta) if ruta else config.ROOT / ".env"
    try:
        texto = ruta.read_bytes().decode("utf-8")
    except OSError:
        texto = ""
    nl = "\r\n" if "\r\n" in texto else "\n"
    lineas = texto.splitlines()
    patron = re.compile(rf"^\s*{re.escape(nombre)}\s*=")
    hecho = False
    for i, linea in enumerate(lineas):
        if patron.match(linea):
            lineas[i] = f"{nombre}={valor}"
            hecho = True
    if not hecho:
        lineas.append(f"{nombre}={valor}")
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_bytes((nl.join(lineas) + nl).encode("utf-8"))
    reemplaza(tmp, ruta)


def _verifica_real(cuenta: str, token: str) -> tuple[bool, str]:
    """Pregunta a Cloudflare si el token vale (los dos tipos de token tienen su dirección). No gasta neuronas. -> (vale, mensaje)."""
    import requests
    cab = {"Authorization": f"Bearer {token}"}
    vio_red = False
    for url in (f"{_API}/accounts/{cuenta}/tokens/verify", f"{_API}/user/tokens/verify"):
        try:
            r = requests.get(url, headers=cab, timeout=15)
        except requests.RequestException:
            vio_red = True
            continue
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code == 200 and j.get("success") and (j.get("result") or {}).get("status", "active") == "active":
            return True, ""
    if vio_red:
        return False, "No pude hablar con Cloudflare (¿hay internet?). No he guardado nada: inténtalo otra vez."
    return False, ("Cloudflare no acepta ese token. Revisa que lo copiaste entero y que lo creaste con el permiso «Workers AI» "
                   "(plantilla «Workers AI» al crear el token).")


def guarda_cloudflare(cuenta, token, ruta_env=None, _verifica=None) -> dict:
    """Comprueba ID de cuenta + token y, solo si valen, los guarda (en .env y en el panel en marcha). Nunca devuelve el token."""
    cuenta, token = str(cuenta or "").strip().strip('"\''), str(token or "").strip().strip('"\'')
    if not RE_CUENTA.match(cuenta):
        return {"ok": False, "error": "El ID de cuenta de Cloudflare son 32 letras y números seguidos (lo ves en el panel de Cloudflare, "
                                      "en la página de Workers AI → «Usar la API REST»). Cópialo entero."}
    if not RE_TOKEN.match(token):
        return {"ok": False, "error": "Eso no parece un token de Cloudflare: son letras, números, guiones y guion bajo, sin espacios, de unos 40 caracteres."}
    vale, msg = (_verifica or _verifica_real)(cuenta, token)
    if not vale:
        return {"ok": False, "error": msg}
    escribe_env_var("CLOUDFLARE_ACCOUNT_ID", cuenta, ruta_env)
    escribe_env_var("CLOUDFLARE_API_TOKEN", token, ruta_env)
    os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_API_TOKEN"] = cuenta, token
    return {"ok": True, "ultimos4": token[-4:]}


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    CUENTA, TOKEN = "0123456789abcdef0123456789abcdef", "tokenDePruebaFalso_" + "x" * 30
    guardado = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")}
    try:
        for k in guardado:
            os.environ.pop(k, None)
        e = estado()
        caso("sin ninguna clave: no está listo y no inventa nada", not e["listo"] and not e["gemini"]["hay"] and not e["cloudflare"]["hay"], e)
        os.environ["GEMINI_API_KEY"] = "AIza" + "k" * 36
        e = estado()
        caso("con la de Gemini ya está listo (Cloudflare es opcional) y solo enseña las 4 últimas letras",
             e["listo"] and e["gemini"]["ultimos4"] == "kkkk" and "AIza" not in str(e), e)
        os.environ.pop("GEMINI_API_KEY", None)

        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            r = guarda_cloudflare("corto", TOKEN, env, _verifica=lambda c, t: (True, ""))
            caso("un ID de cuenta mal formado se rechaza sin llamar a Cloudflare ni escribir nada", not r["ok"] and not env.exists(), r)
            r = guarda_cloudflare(CUENTA, "con espacios y cosas raras", env, _verifica=lambda c, t: (True, ""))
            caso("un token mal formado se rechaza", not r["ok"] and not env.exists(), r)
            r = guarda_cloudflare(CUENTA, TOKEN, env, _verifica=lambda c, t: (False, "no vale"))
            caso("si Cloudflare dice que no vale, NO se guarda ni en .env ni en el panel",
                 not r["ok"] and not env.exists() and "CLOUDFLARE_API_TOKEN" not in os.environ, r)
            env.write_text("# Claves\r\nGEMINI_API_KEY=abc\r\nOTRA=1\r\n", encoding="utf-8")
            r = guarda_cloudflare(f" {CUENTA} ", f'"{TOKEN}"', env, _verifica=lambda c, t: (c == CUENTA and t == TOKEN, "no"))
            t = env.read_bytes().decode("utf-8")
            caso("si vale: se guarda sin tocar el resto del .env (comentario, Gemini, saltos de línea) y queda en el panel",
                 r["ok"] and f"CLOUDFLARE_ACCOUNT_ID={CUENTA}" in t and f"CLOUDFLARE_API_TOKEN={TOKEN}" in t and "GEMINI_API_KEY=abc" in t
                 and "# Claves" in t and "OTRA=1" in t and "\r\n" in t and "\n" not in t.replace("\r\n", "")
                 and os.environ.get("CLOUDFLARE_API_TOKEN") == TOKEN, t)
            caso("la respuesta nunca trae el token entero", TOKEN not in str(r) and r["ultimos4"] == TOKEN[-4:], r)
            guarda_cloudflare(CUENTA, TOKEN.replace("x", "y"), env, _verifica=lambda c, t: (True, ""))
            t = env.read_text(encoding="utf-8")
            caso("guardar otra vez REEMPLAZA la línea (no la duplica)", t.count("CLOUDFLARE_API_TOKEN=") == 1 and t.count("CLOUDFLARE_ACCOUNT_ID=") == 1, t)
            caso("no deja el .tmp atrás", not (Path(tmp) / ".env.tmp").exists())
        e = estado()
        caso("con las dos claves, Cloudflare sale como puesta", e["cloudflare"]["hay"], e)
    finally:
        for k, v in guardado.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return filas
