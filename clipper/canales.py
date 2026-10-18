"""Canales de Vídeos PRO creados desde el panel («➕ Nuevo canal»).

Mi canal vive en `config.yaml`; cada canal nuevo vive en su propio archivo `canales/<nombre>.yaml` (sin claves
ni contraseñas: solo marca, enlaces y texto) y `config.load()` lo suma a `videos.canales`. Así se puede añadir
cualquier canal, aunque nadie lo conozca de antemano, sin tocar código ni `config.yaml`.

Lo que cambia con el canal (y por eso se pregunta al crearlo):
  · el CONTEXTO que lee Gemini (de qué trata, a quién le habla, tono, reglas propias): sale de las respuestas;
  · la MARCA: los 5 colores y las letras de las tarjetas, las ilustraciones, las imágenes y los prompts de miniatura;
  · la DESCRIPCIÓN de YouTube: web, redes, correo, llamado a la acción y hashtags;
  · la CÁMARA (dónde cae en tu grabación) para el zoom cara/pantalla.
Los cortes, el ritmo y la calidad de la edición son los mismos para todos los canales.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from . import config
from .util import normalize

RUTA = config.ROOT / "canales"
FUENTES = config.ROOT / "fonts"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_URL = re.compile(r"^https?://[^\s]+$", re.I)
_CORREO = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")

# (clave en `enlaces`, nombre que se ve). El orden es el de la descripción.
REDES = (("instagram", "Instagram"), ("tiktok", "TikTok"), ("discord", "Discord"), ("spotify", "Spotify"),
         ("reddit", "Reddit"), ("bluesky", "Bluesky"), ("twitter", "X (Twitter)"), ("facebook", "Facebook"),
         ("twitch", "Twitch"), ("kick", "Kick"), ("threads", "Threads"), ("telegram", "Telegram"),
         ("whatsapp", "WhatsApp"), ("patreon", "Patreon"))
# (clave, nombre, valor por defecto = los de la marca Mi canal)
COLORES = (("tinta", "Tinta (textos y contornos)", "#14100F"), ("crema", "Fondo claro (tarjetas)", "#FFFBF2"),
           ("naranja", "Color principal", "#FE330A"), ("dorado", "Color de acento", "#FFC93C"),
           ("beige", "Fondo secundario", "#F1E9DA"))
TONOS = ("cercano", "profesional", "divertido y con humor", "técnico", "inspirador", "directo, sin rodeos",
         "energético", "tranquilo")
_FUENTE_DEF = {"fuente_titulo": "Rubik-ExtraBold.ttf", "fuente_numero": "Rubik-Black.ttf", "fuente_texto": "Rubik-Medium.ttf"}


# ------------------------------------------------------------------ utilidades
def slug_de(nombre: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", normalize(nombre or "")).strip("-")
    return s[:24].strip("-") or "canal"


def fuentes_disponibles() -> list[str]:
    return sorted(p.name for p in FUENTES.glob("*.ttf")) if FUENTES.is_dir() else []


def _txt(v, n: int) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "").replace("\r", "")).strip()[:n]


def compone_contexto(p: dict, nombre: str = "", web: str = "") -> str:
    """El texto que Gemini lee como «contexto del canal», armado con lo que respondiste. Es determinista
    (no depende de la IA) y tiene la misma forma que el de Mi canal, que es el que se probó."""
    if not (str(p.get("tema") or "").strip() or nombre):
        return ""                                                    # nada que decir todavía
    tono = p.get("tono") or []
    tono = ", ".join(tono) if isinstance(tono, (list, tuple)) else _txt(tono, 120)
    partes = [f'Canal de YouTube "{nombre or p.get("nombre", "")}"' + (f" ({web})" if web else "") + ". "
              + _txt(p.get("tema"), 700).rstrip(".") + "."]
    if p.get("audiencia"):
        partes.append(f"Audiencia: {_txt(p['audiencia'], 300).rstrip('.')}.")
    if p.get("tipo"):
        partes.append(f"Tipo de vídeos: {_txt(p['tipo'], 200).rstrip('.')}.")
    if tono:
        partes.append(f"Tono y estilo: {tono}.")
    if p.get("idioma"):
        partes.append(f"Idioma y acento: {_txt(p['idioma'], 80).rstrip('.')}.")
    if p.get("presentador"):
        partes.append(f"El presentador es {_txt(p['presentador'], 80).rstrip('.')}.")
    if p.get("formato"):
        partes.append(f"Cómo se graba: {_txt(p['formato'], 300).rstrip('.')}.")
    if p.get("reglas"):
        partes.append("Reglas de edición propias de este canal (respétalas por encima de cualquier otra pauta "
                      f"genérica): {_txt(p['reglas'], 600).rstrip('.')}.")
    return "\n".join(partes)


# ------------------------------------------------------------------ validar y armar el canal
def valida(d: dict) -> tuple[dict, list[str]]:
    """Datos del formulario -> (configuración del canal, errores). Nada se guarda si hay errores."""
    err: list[str] = []
    nombre = _txt(d.get("nombre"), 40)
    if len(nombre) < 2:
        err.append("Ponle un nombre al canal.")
    perfil = {k: (d.get("perfil") or {}).get(k) for k in ("tema", "audiencia", "tipo", "idioma", "presentador", "formato",
                                                            "reglas", "tono")}
    if isinstance(perfil.get("tono"), str):
        perfil["tono"] = [t.strip() for t in perfil["tono"].split(",") if t.strip()]
    perfil["tono"] = [_txt(t, 40) for t in (perfil.get("tono") or [])][:6]
    for k in ("tema", "audiencia", "tipo", "idioma", "presentador", "formato", "reglas"):
        perfil[k] = _txt(perfil.get(k), 700 if k in ("tema", "reglas") else 300)
    if len(perfil["tema"]) < 15:
        err.append("Cuéntale a la IA de qué trata el canal (al menos una frase).")

    marca: dict = {}
    for clave, _n, defecto in COLORES:
        v = _txt((d.get("marca") or {}).get(clave), 9)
        if v and not _HEX.match(v):
            err.append(f"El color «{clave}» debe ser como #FE330A.")
            v = ""
        marca[clave] = (v or defecto).upper()
    disp = set(fuentes_disponibles())
    for k, defecto in _FUENTE_DEF.items():
        v = _txt((d.get("marca") or {}).get(k), 80)
        marca[k] = f"fonts/{v if v in disp else defecto}"

    enlaces: dict = {}
    for k in ["web", "youtube"] + [r for r, _ in REDES]:
        v = _txt((d.get("enlaces") or {}).get(k), 300)
        if not v:
            continue
        if not _URL.match(v):
            err.append(f"El enlace de «{k}» debe empezar por https://")
            continue
        enlaces[k] = v
    for k in ("correo", "correo_secundario"):
        v = _txt((d.get("enlaces") or {}).get(k), 120)
        if v and not _CORREO.match(v):
            err.append("El correo no parece válido.")
        elif v:
            enlaces[k] = v
    cta_t, cta_u = _txt((d.get("enlaces") or {}).get("cta_texto"), 90), _txt((d.get("enlaces") or {}).get("cta_url"), 300)
    if cta_u and not _URL.match(cta_u):
        err.append("El enlace del llamado a la acción debe empezar por https://")
    elif cta_u:
        enlaces["cta_url"] = cta_u
        if cta_t:
            enlaces["cta_texto"] = cta_t

    tags = []
    for h in (d.get("hashtags") or []) if isinstance(d.get("hashtags"), list) else re.split(r"[\s,]+", str(d.get("hashtags") or "")):
        h = re.sub(r"[^\wáéíóúñü]", "", str(h), flags=re.I)
        if h:
            tags.append("#" + h)
    tags = list(dict.fromkeys(tags))[:5]

    cam = None
    if not d.get("sin_camara") and isinstance(d.get("camara"), dict):
        try:
            x, y, w, h = (float(d["camara"][k]) for k in ("x", "y", "w", "h"))
            z = float(d["camara"].get("zoom_cara", 1.7))
            if w < 0.05 or h < 0.05 or x < 0 or y < 0 or x + w > 1.001 or y + h > 1.001:
                raise ValueError
            cam = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4), "zoom_cara": round(min(3.0, max(1.2, z)), 2)}
        except (KeyError, TypeError, ValueError):
            err.append("La cámara marcada no es válida: vuelve a marcarla o elige «no tengo cámara».")

    def _lineas(v, n: int, largo: int) -> list[str]:
        partes = v if isinstance(v, list) else re.split(r"[\n,;]+", str(v or ""))
        out = [_txt(x, largo) for x in partes]
        return list(dict.fromkeys(x for x in out if len(x) >= 2))[:n]
    vocabulario = _lineas(d.get("vocabulario"), 60, 40)                 # tu jerga: Whisper la escribe bien
    nombres_propios: dict = {}
    for ln in str(d.get("nombres_propios") or "").replace("\r", "").split("\n"):    # «Nombre: qué es», una por línea
        nom, _, que = ln.partition(":")
        nom, que = _txt(nom, 40), _txt(que, 160)
        if len(nom) >= 2:
            nombres_propios[nom] = que or "una herramienta o marca de este canal"
        if len(nombres_propios) >= 40:
            break
    cen_in = d.get("censura") or {}
    censura = {"enabled": bool(cen_in.get("enabled")) and bool(_lineas(cen_in.get("palabras"), 80, 30)),
               "palabras": _lineas(cen_in.get("palabras"), 80, 30)}
    sus = d.get("suscribete") or {}
    info = {
        "nombre": nombre, "contexto": compone_contexto(perfil, nombre, enlaces.get("web", "")),
        "subtitulos": bool(d.get("subtitulos")), "enlaces": enlaces, "hashtags": tags, "marca": marca,
        "cta_suscribete": {k: v for k, v in (("texto", _txt(sus.get("texto"), 40)), ("extra", _txt(sus.get("extra"), 90))) if v},
        "perfil": perfil,
    }
    if vocabulario:
        info["vocabulario"] = vocabulario
    if nombres_propios:
        info["nombres_propios"] = nombres_propios
    if censura["palabras"]:
        info["censura"] = censura
    if str(d.get("contexto_manual") or "").strip():                # si lo retocó a mano (o lo mejoró la IA), manda
        info["contexto"] = _txt(d["contexto_manual"], 2500)
    if cam:
        info["camara"] = cam
    from . import ediciones_canal as _ed
    edicion = _ed.valida(d.get("edicion"))
    if edicion:
        info["edicion"] = edicion                         # qué ediciones lleva este canal (clipper/ediciones_canal.py)
    return info, err


# ------------------------------------------------------------------ guardar / listar / borrar
def _archivo(slug: str) -> Path:
    return RUTA / f"{slug}.yaml"


def guarda(d: dict) -> tuple[str | None, list[str]]:
    """Crea (o actualiza, si `editar` es el slug de uno propio) el canal. -> (slug, errores)."""
    info, err = valida(d)
    if err:
        return None, err
    todos = config.load().get_path("videos.canales") or {}
    builtin = set(todos.keys()) - {p.stem for p in RUTA.glob("*.yaml")}
    editar = _txt(d.get("editar"), 30)
    slug = editar if editar and _archivo(editar).is_file() else slug_de(info["nombre"])
    quien = normalize(info["nombre"]).replace(" ", "").replace("-", "")
    for otro, oi in todos.items():                                   # ni el mismo identificador ni el mismo nombre
        if otro != editar and otro in builtin and quien in (otro.replace("-", ""), normalize(str((oi or {}).get("nombre", ""))).replace(" ", "")):
            return None, [f"«{(oi or {}).get('nombre') or otro}» ya viene con el programa: elige otro nombre para tu canal."]
    if not editar or slug != editar:
        base, i = slug, 2
        while _archivo(slug).is_file() or slug in builtin:
            slug, i = f"{base}-{i}", i + 1
    RUTA.mkdir(parents=True, exist_ok=True)
    cab = ("# Canal creado desde el panel de Vídeos PRO. Se puede editar a mano o desde «✏️ Editar canal».\n"
           "# No lleva claves ni contraseñas: solo marca, enlaces y texto.\n")
    tmp = _archivo(slug).with_suffix(".tmp")
    tmp.write_text(cab + yaml.safe_dump(info, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    tmp.replace(_archivo(slug))
    return slug, []


def carga(slug: str) -> dict | None:
    try:
        return yaml.safe_load(_archivo(slug).read_text(encoding="utf-8")) or None
    except (OSError, yaml.YAMLError):
        return None


def lista(cfg=None) -> list[dict]:
    """Todos los canales (Mi canal primero) para el selector: [{slug, nombre, propio, colores}]."""
    cfg = cfg or config.load()
    out = []
    for slug, info in config.canales_visibles(cfg).items():
        if not isinstance(info, dict):
            continue
        m = {**(cfg.get_path("videos.marca") or {}), **(info.get("marca") or {})}
        out.append({"slug": slug, "nombre": info.get("nombre") or slug, "propio": bool(info.get("_propio")),
                    "colores": [m.get(k, d) for k, _n, d in COLORES]})
    out.sort(key=lambda c: (c["propio"], c["nombre"].lower()))
    return out


def borra(slug: str) -> bool:
    """No borra: mueve el archivo del canal a canales/_papelera/ (se puede recuperar a mano)."""
    a = _archivo(slug)
    if not a.is_file():
        return False
    dest = RUTA / "_papelera"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(a), str(dest / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.yaml"))
    return True


# ------------------------------------------------------------------ ayudas del asistente
def frame_jpeg(video: Path, seg: float = 10.0, ancho: int = 1100) -> bytes:
    """Un fotograma del vídeo (para marcar dónde cae tu cámara)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, seg):.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={ancho}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
                       capture_output=True, timeout=120)
    if r.returncode or not r.stdout:
        raise RuntimeError("No pude sacar un fotograma de ese vídeo.")
    return r.stdout


def marca_completa(m: dict) -> dict:
    """La marca de un canal lista para dibujar: colores + rutas de fuente absolutas."""
    out = {k: d for k, _n, d in COLORES}
    out.update({k: v for k, v in (m or {}).items() if v})
    for k in _FUENTE_DEF:
        v = str(out.get(k) or _FUENTE_DEF[k])
        if not Path(v).is_absolute() and "/" not in v and "\\" not in v:       # solo el nombre del archivo: está en fonts/
            v = f"fonts/{v}"
        out[k] = v if Path(v).is_absolute() else str(config.ROOT / v)
    return out


def png_tarjetas(m: dict, sus: dict | None = None) -> bytes:
    """Vista previa REAL de cómo quedarán las tarjetas con esa marca (usa el mismo dibujo que el render)."""
    import io

    from PIL import Image

    from . import tarjetas
    marca = marca_completa(m)
    sus = sus or {}
    casos = [{"tipo": "punto", "numero": 1, "texto": "Tu primer punto"}, {"tipo": "herramienta", "texto": "Nombre de una herramienta"},
             {"tipo": "definicion", "texto": "Un término", "extra": "Su explicación en una frase corta"},
             {"tipo": "dato", "texto": "73%", "extra": "una cifra con su contexto"},
             {"tipo": "suscribete", "texto": sus.get("texto") or "¡SUSCRÍBETE!", "extra": sus.get("extra") or "Si te está gustando el video, no te pierdas el próximo"}]
    imgs = [tarjetas.dibuja(c, marca, 720) for c in casos]
    W = max(i.width for i in imgs) + 60
    H = sum(i.height for i in imgs) + 28 * (len(imgs) + 1)
    fondo = Image.new("RGB", (W, H), tuple(int(marca["beige"][i:i + 2], 16) for i in (1, 3, 5)))
    y = 28
    for i in imgs:
        fondo.paste(i, (30, y), i)
        y += i.height + 28
    b = io.BytesIO()
    fondo.save(b, "PNG")
    return b.getvalue()


def prueba() -> list[dict]:
    """Casos del asistente de canales: validar, guardar sin pisar a Mi canal, mezclar con config y armar el contexto."""
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    base = {"nombre": "Canal de Prueba", "perfil": {"tema": "Canal de gaming en español con partidas de shooters y humor.",
            "audiencia": "jóvenes de 15 a 25 años", "tono": ["divertido y con humor", "energético"],
            "reglas": "No cortes las risas ni los gritos."}, "marca": {"naranja": "#22CC88"},
            "enlaces": {"web": "https://canal.example", "instagram": "https://instagram.com/canal", "correo": "hola@canal.example",
                        "cta_texto": "¿Quieres jugar conmigo?", "cta_url": "https://canal.example/unete"},
            "hashtags": "gaming, #shooter, humor", "camara": {"x": 0.7, "y": 0.6, "w": 0.25, "h": 0.3}}
    info, err = valida(base)
    caso("un canal correcto no da errores", not err, err)
    caso("el contexto para Gemini lleva el tema, la audiencia, el tono y las reglas propias",
         all(x in info["contexto"] for x in ("shooters", "15 a 25", "energético", "No cortes las risas")), info["contexto"][:200])
    caso("los colores que no das salen de la marca por defecto y los que das se respetan",
         info["marca"]["naranja"] == "#22CC88" and info["marca"]["tinta"] == "#14100F")
    caso("los hashtags se limpian (# delante, sin repetidos)", info["hashtags"] == ["#gaming", "#shooter", "#humor"], info["hashtags"])
    _i, e1 = valida({**base, "nombre": ""})
    _i, e2 = valida({**base, "marca": {"naranja": "rojo"}})
    _i, e3 = valida({**base, "enlaces": {"web": "canal.example"}})
    _i, e4 = valida({**base, "perfil": {"tema": "corto"}})
    caso("nombre vacío, color mal escrito, enlace sin https y tema vacío se rechazan", all((e1, e2, e3, e4)), (e1, e2, e3, e4))
    i5, _ = valida({**base, "sin_camara": True})
    caso("«no tengo cámara» quita el zoom de cara", "camara" not in i5)
    _i, e6 = valida({**base, "camara": {"x": 0.9, "y": 0.9, "w": 0.3, "h": 0.3}})
    caso("una cámara que se sale del lienzo se rechaza", bool(e6))
    caso("el nombre se convierte en un identificador limpio", slug_de("¡Conquer Gaming ñandú!") == "conquer-gaming-nandu", slug_de("¡Conquer Gaming ñandú!"))
    import tempfile
    global RUTA
    orig = RUTA
    try:
        RUTA = Path(tempfile.mkdtemp(prefix="canales_prueba_"))
        s, e = guarda({**base, "nombre": "mi CANAL (Ejemplo)"})
        caso("no se puede pisar al canal de ejemplo de config.yaml (ni con el mismo nombre escrito distinto)", s is None and e and not any(RUTA.glob("*.yaml")), e)
        s1, e1 = guarda(base)
        s2, e2 = guarda(base)
        caso("se guarda en su archivo y un segundo con el mismo nombre no lo pisa", s1 == "canal-de-prueba" and s2 == "canal-de-prueba-2" and not e1 and not e2, (s1, s2))
        cargado = carga(s1) or {}
        caso("lo guardado se vuelve a leer igual", cargado.get("nombre") == "Canal de Prueba" and cargado.get("marca", {}).get("naranja") == "#22CC88")
        d = {}
        d.setdefault("videos", {}).setdefault("canales", {})["ejemplo"] = {"nombre": "Mi canal"}
        config._mezcla_canales_usuario(d, RUTA)
        caso("config.load() suma los canales propios y no pisa el de config.yaml",
             set(d["videos"]["canales"]) == {"ejemplo", "canal-de-prueba", "canal-de-prueba-2"} and d["videos"]["canales"]["ejemplo"] == {"nombre": "Mi canal"})
        caso("«borrar» mueve a la papelera, no destruye", borra(s2) and not _archivo(s2).is_file() and any((RUTA / "_papelera").glob("*.yaml")))
        m2 = marca_completa({"fuente_titulo": "Rubik-ExtraBold.ttf"})
        caso("una letra dada solo por su nombre se busca en fonts/ (no cae a la de emergencia)", Path(m2["fuente_titulo"]).is_file(), m2["fuente_titulo"])
        png = png_tarjetas(info["marca"])
        caso("la vista previa de tarjetas es una imagen PNG", png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000)
    finally:
        shutil.rmtree(RUTA, ignore_errors=True)
        RUTA = orig
        # el contexto, el nombre y los colores de un canal NUEVO llegan al prompt del director (Gemini falso: no gasta nada)
    from . import director
    cap: dict = {}
    real = director.call_gemini_cadena

    def falso(client, types, modelos, prompt, system=None, valida=None, **k):
        cap["p"], cap["s"] = prompt, system
        plan = {"recortes": [], "camara": [], "tarjetas": [], "imagenes": [], "ilustraciones": [], "titulos_sugeridos": ["a", "b", "c"],
                "miniatura_prompts": ["x", "y", "z"], "capitulos": [], "gancho_descripcion": "g", "articulo_relacionado": "", "hashtag": ""}
        return plan, modelos[0], {"entrada": 1, "salida": 1}
    try:
        director.call_gemini_cadena = falso
        cfg = config.load()
        cfg["videos"]["marca"] = {**(cfg.get_path("videos.marca") or {}), **info["marca"]}
        words = [{"word": f"p{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(40)]
        res = director.analiza(words, [(0.0, 20.0)], cfg, tema=info["nombre"], contexto=info["contexto"], es_completo=True,
                               articulos=[], hashtags=info["hashtags"], canal=info["nombre"])
        if res is None:
            caso("el contexto y los colores del canal llegan al prompt de Gemini (omitido: no hay clave de Gemini)", True)
        else:
            caso("el contexto y los colores del canal llegan al prompt de Gemini, sin nombrar a Mi canal",
                 "Canal de Prueba" in cap["s"] and "Mi canal" not in cap["s"] and "shooters" in cap["p"] and "#22CC88" in cap["p"].upper()
                 and "#FE330A" not in cap["p"].upper(), (cap.get("s") or "")[:120])
    finally:
        director.call_gemini_cadena = real
    # las ediciones que elige el canal (clipper/ediciones_canal.py): se guardan solo los ids conocidos, como booleanos
    info_e, err_e = valida({"nombre": "Canal Ediciones", "perfil": {"tema": "Un canal de prueba con ediciones elegidas a mano."},
                            "edicion": {"shorts": False, "estilo_grafica": True, "no_existe": True}})
    caso("el canal guarda qué ediciones lleva (solo ids conocidos, como booleanos)",
         not err_e and info_e.get("edicion") == {"shorts": False, "estilo_grafica": True}, (info_e.get("edicion"), err_e))
    info_s, _e = valida({"nombre": "Canal Sin Elegir", "perfil": {"tema": "Un canal de prueba sin tocar las ediciones."}})
    caso("un canal que no toca las ediciones no guarda el bloque (todo como diga config.yaml)", "edicion" not in info_s, info_s.get("edicion"))
    return filas
