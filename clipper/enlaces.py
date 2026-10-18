"""Enlaces reales de Mi canal para la descripción de YouTube de Vídeos PRO.

La IA solo aporta lo que de verdad necesita criterio (el gancho de las dos
primeras líneas, cuál artículo pega mejor con el vídeo, qué hashtag).
TODO lo demás — redes, contacto, CTAs, y las URLs en sí — sale de
`videos.canales.<canal>.enlaces` en config.yaml o del catálogo de
artículos (`clipper/data/articulos_ejemplo.json`). Así un link mal puesto
es un dato desactualizado que se corrige en el config, nunca una
alucinación del modelo — la IA nunca escribe una URL de la nada.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .util import log, normalize

_CATALOGO = Path(__file__).parent / "data" / "articulos_ejemplo.json"


# ------------------------------------------------------------------ UTM en los enlaces a TU web
# el autor (2026-09-26): «cuando sea una URL para mi dominio, tu-sitio.example, ponle una UTM… de vídeo de YouTube… para yo desde
# Google Analytics saber de dónde viene ese tráfico». Solo a TU dominio (el de `enlaces.web` y sus subdominios: las redes,
# las herramientas y los créditos no se tocan): utm_source=youtube + utm_medium=video (Google Analytics 4 lo cuenta en su
# canal «Organic Video»), utm_campaign = QUÉ vídeo y utm_content = QUÉ enlace de la descripción (articulo, web, cta…).
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
LIMITE_BYTES = 4900            # YouTube admite 5000 bytes de descripción: si con las UTM no cabe, se recortan


def slug_utm(texto: str, tope: int = 60) -> str:
    """«2026-09-26 Google Search Console · IAs en inglés» -> «2026-09-26-google-search-console-ias-en-ingles»: sin tildes,
    en minúsculas, con guiones y cortado en una palabra entera."""
    s = re.sub(r"[^a-z0-9]+", "-", normalize(str(texto or ""))).strip("-")
    if len(s) > tope:
        s = s[:tope].rsplit("-", 1)[0]
    return s


def utm_para(enlaces: dict | None, cfg_utm: dict | None, campana: str) -> dict | None:
    """Las UTM de la descripción de UN vídeo, o None si están apagadas (`utm.activo: false` del canal) o el canal no
    tiene web (`enlaces.web`). CORTAS por defecto: solo `?utm_source=youtube` (el autor, 2026-09-27: «están requete largos…
    lo único que necesito es un UTM corto que me diga que vinieron de YouTube»). El resto, solo si se enciende en el
    config: `medium` (p. ej. «video»), `campana: true` (qué vídeo: `campana` pasada a slug) y `contenido: true` (qué
    enlace de la descripción)."""
    c = dict(cfg_utm or {})
    if not c.get("activo", True):
        return None
    dominios = [_host_de(str((enlaces or {}).get("web") or ""))]
    dominios += [str(x).strip().lower().removeprefix("www.") for x in (c.get("dominios") or [])]
    dominios = list(dict.fromkeys(d for d in dominios if d))
    if not dominios:
        return None
    return {"dominios": dominios, "source": str(c.get("source") or "youtube"), "medium": str(c.get("medium") or ""),
            "campaign": (slug_utm(campana) or "video") if c.get("campana") else "",
            "sin_contenido": not c.get("contenido", False)}


def _host_de(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def con_utm(url: str, utm: dict | None, contenido: str = "") -> str:
    """`url` con las UTM si es de TU dominio (o un subdominio suyo); cualquier otra, tal cual. Conserva lo que ya traía
    (otros parámetros, el «#…») y SUSTITUYE las utm_ que tuviera: aplicarlo dos veces da lo mismo."""
    if not utm or not url:
        return url
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host = (p.hostname or "").lower().removeprefix("www.")
    if p.scheme not in ("http", "https") or not any(host == d or host.endswith("." + d) for d in utm.get("dominios") or []):
        return url
    pares = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    pares += [(k, v) for k, v in (("utm_source", utm.get("source")), ("utm_medium", utm.get("medium")),
                                   ("utm_campaign", utm.get("campaign")),
                                   ("utm_content", "" if utm.get("sin_contenido") else slug_utm(contenido, 30))) if v]
    return urlunparse(p._replace(path=p.path or "/", query=urlencode(pares)))     # «tu-sitio.example/?utm…», no «.com?utm…»


def utm_en_texto(texto: str, utm: dict | None, contenido: str = "texto") -> str:
    """Las URL de TU dominio escritas a mano dentro de un texto (el gancho de un vídeo antiguo…) que aún no llevan UTM."""
    if not utm or not texto:
        return texto

    def _una(m: re.Match) -> str:
        u, cola = m.group(0), ""
        while u and u[-1] in ".,;:!?)]":
            u, cola = u[:-1], u[-1] + cola
        return m.group(0) if "utm_" in u else con_utm(u, utm, contenido) + cola

    return _URL_RE.sub(_una, texto)


def articulos_del_canal(info: dict | None) -> list[dict]:
    """El catálogo de artículos de ESE canal (`articulos_archivo` en su config); sin él, ninguno: un canal
    nuevo no debe ofrecerle a la IA los artículos de otro."""
    archivo = str((info or {}).get("articulos_archivo") or "").strip()
    if not archivo:
        return []
    from .config import ROOT
    propio = ROOT / "canales" / archivo                       # tu catálogo vive junto a tu canal (canales/<archivo>.json)
    return cargar_articulos(propio if propio.is_file() else Path(__file__).parent / "data" / archivo)


def cargar_articulos(ruta: Path | None = None) -> list[dict]:
    if ruta is None:
        return []
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
        return [a for a in (data.get("articulos") or [])
                if a.get("titulo") and a.get("url")]
    except (OSError, ValueError) as exc:
        log(f"AVISO: no pude leer el catálogo de artículos ({exc}).")
        return []


def articulo_por_titulo(titulo_elegido: str, articulos: list[dict]) -> dict | None:
    """La IA devuelve el TÍTULO tal cual como salió en la lista que se le
    mandó; aquí se busca ese título EXACTO (normalizado) en el catálogo
    real — nunca se confía en que la IA haya copiado bien una URL."""
    if not titulo_elegido:
        return None
    obj = normalize(titulo_elegido).strip()
    for a in articulos:
        if normalize(a["titulo"]).strip() == obj:
            return a
    return None


def materiales_de_tarjetas(tarjetas: list[dict], tabla: dict) -> list[dict]:
    """Las tarjetas "herramienta" del plan -> la lista de "Materiales" de la
    descripción: el nombre EXACTO que ya dio el director (nunca inventado
    por la IA otra vez) + su URL si esa herramienta está en
    `videos.herramientas_conocidas`. Si no está en la tabla, sale sin URL
    (una línea con solo el nombre) en vez de omitirla — el autor la pidió
    explícita para que vea de un vistazo qué mencionó, tenga link o no."""
    tabla = tabla or {}
    vistos: set[str] = set()
    out: list[dict] = []
    for t in tarjetas or []:
        if t.get("tipo") != "herramienta":
            continue
        nombre = str(t.get("texto", "")).strip()
        clave = normalize(nombre)
        if not nombre or clave in vistos:
            continue
        vistos.add(clave)
        url = next((u for k, u in tabla.items() if normalize(k) == clave), None)
        out.append({"nombre": nombre, "url": url})
    return out


def enlaces_mencionados(texto: str, reglas: list[dict], enlaces: dict) -> list[dict]:
    """Busca en `texto` (la transcripción) las claves de
    `enlaces_por_tema` y devuelve los que de verdad se mencionaron, con su
    etiqueta y su URL ya resueltas contra `enlaces`. Detección por texto,
    no por IA: cero riesgo de que se invente que mencionaste algo."""
    t = normalize(texto)
    out: list[dict] = []
    vistos: set[str] = set()
    for regla in reglas or []:
        clave = regla.get("enlace")
        if not clave or clave in vistos:
            continue
        claves = [normalize(c) for c in (regla.get("claves") or [])]
        if any(c and c in t for c in claves):
            url = (enlaces or {}).get(clave)
            if url:
                out.append({"etiqueta": regla.get("etiqueta", clave), "url": url})
                vistos.add(clave)
    return out


_CONECTORES_HASHTAG = {"de", "del", "la", "las", "el", "los", "en", "para", "por", "con", "y", "o", "a", "al", "un", "una"}


def hashtag_de(tag: str) -> str:
    """«SEO para Google» -> «#SEOparaGoogle»: sin espacios ni signos, cada palabra con mayúscula inicial (las siglas se
    quedan como están) y los conectores en minúscula, para que se lea. Con sus tildes (YouTube las admite)."""
    import re as _re
    partes = []
    for i, w in enumerate(_re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", str(tag or ""))):
        if w.isupper() and len(w) > 1:
            partes.append(w)
        elif i > 0 and w.lower() in _CONECTORES_HASHTAG:
            partes.append(w.lower())
        else:
            partes.append(w[:1].upper() + w[1:])
    return "#" + "".join(partes) if partes else ""


def hashtags_de(tags: list[str] | None) -> str:
    """La línea de hashtags de la descripción: las etiquetas del vídeo como «#Etiqueta», separadas por espacios."""
    vistos, out = set(), []
    for t in tags or []:
        h = hashtag_de(t)
        if len(h) > 1 and h.lower() not in vistos:
            vistos.add(h.lower())
            out.append(h)
    return " ".join(out)


def arma_descripcion(gancho: str, articulo: dict | None, hashtag: str,
                     enlaces: dict, extra_enlaces: list[dict],
                     capitulos_texto: str,
                     creditos_imagenes: list[str] | None = None,
                     materiales: list[dict] | None = None,
                     creditos_musica: list[str] | None = None,
                     utm: dict | None = None) -> str:
    """Junta todo en la descripción final, mismo orden que el template real
    del autor: gancho -> MATERIALES -> artículo -> web (solo si el canal la quiere: `web_en_descripcion`) -> CTA de servicios ->
    lo que se mencionó -> redes -> hashtag -> contacto -> capítulos ->
    créditos de imágenes. Cualquier campo vacío (una red que aún no está
    configurada, sin artículo que encaje...) simplemente no sale — nunca
    una línea rota tipo "Discord: ". `creditos_imagenes`: la atribución que
    exige la licencia CC de cada imagen ilustrativa que se usó
    (clipper/imagenes.py) — no es opcional saltársela, la licencia la
    exige. `materiales`: las herramientas que se nombraron en el vídeo
    (ver enlaces.materiales_de_tarjetas) — el autor la pidió explícita, justo
    debajo del gancho y antes del artículo relacionado. `utm` (ver `utm_para`): los enlaces a TU web llevan
    las UTM del vídeo, cada uno con su `utm_content` (qué enlace es); si así no cabe en los 5000 bytes de YouTube, se
    quita primero el `utm_content` y, si ni así, las UTM (con un aviso)."""
    args = (gancho, articulo, hashtag, enlaces, extra_enlaces, capitulos_texto, creditos_imagenes, materiales,
            creditos_musica)
    texto = _arma(*args, utm=utm)
    if utm and len(texto.encode("utf-8")) > LIMITE_BYTES:
        texto = _arma(*args, utm={**utm, "sin_contenido": True})
        if len(texto.encode("utf-8")) > LIMITE_BYTES:
            log("AVISO: con las UTM la descripción no cabe en los 5000 bytes de YouTube: va sin UTM.")
            texto = _arma(*args, utm=None)
    return texto


def _arma(gancho, articulo, hashtag, enlaces, extra_enlaces, capitulos_texto, creditos_imagenes, materiales,
          creditos_musica, utm: dict | None = None) -> str:
    enlaces = enlaces or {}
    L: list[str] = []

    def u(url: str, que: str) -> str:
        return con_utm(url, utm, que)

    if gancho:
        L += [utm_en_texto(gancho.strip(), utm, "texto"), ""]

    if materiales:
        L.append("📦MATERIALES / LINKS MENCIONADOS:")
        for m in materiales:
            L.append(f"- {m['nombre']}: {u(m['url'], 'materiales')}" if m.get("url") else f"- {m['nombre']}")
        L.append("")

    if articulo:
        cab = str(articulo.get("cabecera") or "").strip()
        if cab:           # vídeo antiguo: se respeta la cabecera que el autor escribió («Artículo sobre …»)
            titulo = str(articulo.get("titulo") or "").strip()
            punto = "" if cab.endswith(("?", "!", ":")) else ":"
            L += [f"🖥️{cab}{punto}" + (f" {titulo}" if titulo else ""), u(articulo["url"], "articulo"), ""]
        else:
            L += [f"🖥️Artículo relacionado: {articulo['titulo']}", u(articulo["url"], "articulo"), ""]

    # La línea «🔍Mi página web: …» (el autor, 2026-10-05: «no lo veo importante por ahora»): `enlaces.web_en_descripcion: false` del canal la quita. El
    # dato `web` se queda en el config (las UTM reconocen tu dominio por él y las ideas lo usan como destino); sin la clave, sale como siempre.
    if enlaces.get("web") and enlaces.get("web_en_descripcion", True) is not False:
        L += [f"🔍Mi página web: {u(enlaces['web'], 'web')}", ""]

    cta_url = enlaces.get("cta_url") or enlaces.get("consultor_seo")     # el llamado a la acción de ESTE canal
    if cta_url:
        cta = (enlaces.get("cta_texto") or "¿Quieres que te ayude a hacer el SEO de tu sitio web?").strip()
        L.append(f"🛑{cta}: {u(cta_url, 'cta')}")
    for extra in extra_enlaces or []:
        L.append(f"➡️{extra['etiqueta']}: {u(extra['url'], extra['etiqueta'])}")
    if cta_url or extra_enlaces:
        L.append("")

    redes = [("☕", "Instagram", enlaces.get("instagram")),
             ("💥", "Discord", enlaces.get("discord")),
             ("🎤", "Spotify", enlaces.get("spotify")),
             ("🎱", "TikTok", enlaces.get("tiktok")),
             ("👽", "Reddit", enlaces.get("reddit")),
             ("🦋", "Bluesky", enlaces.get("bluesky")),
             ("🐦", "X (Twitter)", enlaces.get("twitter")), ("📘", "Facebook", enlaces.get("facebook")),
             ("🟣", "Twitch", enlaces.get("twitch")), ("🟢", "Kick", enlaces.get("kick")),
             ("🧵", "Threads", enlaces.get("threads")), ("✈️", "Telegram", enlaces.get("telegram")),
             ("💬", "WhatsApp", enlaces.get("whatsapp")), ("🎁", "Patreon", enlaces.get("patreon"))]
    # LinkedIn NO va en la descripción (el autor lo pidió explícito) aunque
    # `enlaces.linkedin` tenga URL en config.yaml — se deja el dato ahí por
    # si algún día lo quiere de vuelta, pero la plantilla nunca lo usa.
    activas = [(e, n, url) for e, n, url in redes if url]
    if activas:
        L += ["_" * 60, ""]
        L += [f"{emoji}{nombre}: {u(url, nombre)}" for emoji, nombre, url in activas]
        L.append("")

    if hashtag:
        L += ["_" * 60, "", hashtag]

    if enlaces.get("correo"):
        L += ["", "¿Quieres contactar conmigo?", "", f"Correo: {enlaces['correo']}"]
        if enlaces.get("correo_secundario"):
            L.append(f"Correo Secundario: {enlaces['correo_secundario']}")

    if capitulos_texto:
        L += ["", "CAPÍTULOS", capitulos_texto]

    if creditos_imagenes:
        L += ["", "Créditos de imágenes:"]
        L += [f"- {c}" for c in creditos_imagenes]

    if creditos_musica:
        L += ["", "Créditos de música:"]
        L += [f"- {c}" for c in creditos_musica]

    return "\n".join(L).strip()
