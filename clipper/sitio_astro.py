"""📤 El artículo del vídeo, DIRECTO a tu proyecto de Astro (el autor, 2026-10-11: «copio el markdown y se pierden las imágenes… descargo el
zip y no lo puedo subir a Visual Studio Code… ¿una forma más fácil?»).

Un botón en «📝 ARTÍCULO PARA TU WEB» escribe, en TU proyecto (`videos.canales.<canal>.sitio.carpeta`, p. ej. D:\\Proyecto Astro\\ejemplo):
  - `src/content/blog/<categoría>/<slug>.md` con la CABECERA que pide tu esquema (`src/content.config.ts`: title, heading, description, seoTitle,
    focusKeyword, excerpt, category, pubDate, updatedDate, heroImage, heroImageAlt, tags, draft, urlSlug) y el cuerpo del artículo;
  - las capturas en `src/assets/uploads/<slug>-NN.jpg` (la carpeta de imágenes de tu CMS) y los enlaces del Markdown ya apuntando a ellas con
    la ruta relativa que Astro espera (`../../../assets/uploads/…`: la misma que usa tu panel);
  - el vídeo de YouTube con tu formato (`<div class="jo-embed"><iframe …youtube-nocookie…>`), con su id aunque se haya publicado DESPUÉS de crear el artículo.
Por seguridad: nace como BORRADOR (`draft: true`: tu web no lo publica hasta que lo quites), NUNCA pisa un artículo que ya exista, no escribe fuera de
tu proyecto y no toca Git (tú revisas en Visual Studio Code, haces commit y subes como siempre).

Funciones puras + `prueba()` (con un proyecto de mentira en una carpeta temporal)."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import articulo as _art
from .util import log, normalize

_DEF = {
    "categorias": ["google", "youtube", "video"],     # las de tu `CATEGORIES` en content.config.ts
    "categoria_defecto": "google",
    "borrador": True,                                 # nace con `draft: true`
    "articulos": "src/content/blog",                  # dentro del proyecto
    "imagenes": "src/assets/uploads",                 # la `media_folder` de tu CMS
}
EMBED = ('<div class="jo-embed"><iframe src="https://www.youtube-nocookie.com/embed/{id}" title="{titulo}" loading="lazy" '
         'allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen=""></iframe></div>')


# ------------------------------------------------------------------------------------------------------------------ config
def config_sitio(cfg, canal: str) -> dict:
    """`videos.canales.<canal>.sitio` + valores por defecto. {"configurado": bool, "motivo": str, "carpeta": Path, …}."""
    info = {}
    try:
        info = (cfg.get_path(f"videos.canales.{canal}") or {}) if cfg is not None and canal else {}
    except Exception:  # noqa: BLE001
        info = {}
    s = info.get("sitio") if isinstance(info, dict) else None
    if not isinstance(s, dict) or not str(s.get("carpeta") or "").strip():
        return {"configurado": False, "motivo": "Este canal no tiene `sitio.carpeta` en config.yaml (la carpeta de tu proyecto de Astro)."}
    p = {**_DEF, **{k: v for k, v in s.items() if v is not None}}
    carpeta = Path(str(p["carpeta"]).strip()).expanduser()
    p["categorias"] = [str(c).strip().lower() for c in (p.get("categorias") or _DEF["categorias"]) if str(c).strip()] or list(_DEF["categorias"])
    if str(p["categoria_defecto"]).lower() not in p["categorias"]:
        p["categoria_defecto"] = p["categorias"][0]
    p["categoria_defecto"] = str(p["categoria_defecto"]).lower()
    p["borrador"] = bool(p.get("borrador", True))
    if not carpeta.is_dir() or not (carpeta / "src" / "content").is_dir():
        return {"configurado": False, "motivo": f"No encuentro tu proyecto de Astro en «{carpeta}» (no tiene src/content): revisa `sitio.carpeta`."}
    return {**p, "configurado": True, "motivo": "", "carpeta": carpeta}


def _slug(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", normalize(str(texto or "")).replace(" ", "-")).strip("-")
    return re.sub(r"-{2,}", "-", s)[:80].strip("-")


def _dentro(base: Path, destino: Path) -> bool:
    try:
        destino.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _yaml(v) -> str:
    """Un valor de la cabecera en YAML: un texto JSON es un texto YAML válido (comillas dobles, tildes tal cual, saltos escapados)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yaml(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


# ------------------------------------------------------------------------------------------------------------------ el artículo
def prepara(a: dict, arts: list[dict], categoria: str, slug: str, borrador: bool, rel_img: str, video_id: str = "", ahora: float | None = None,
            hero: tuple[str, str] | None = None) -> tuple[str, list[tuple[str, str]]]:
    """-> (texto del .md, [(captura de origen «img-01.jpg», nombre de destino «<slug>-01.jpg»)]). `rel_img` = la ruta RELATIVA desde la carpeta del .md hasta la
    carpeta de imágenes (`../../../assets/uploads`). Pura: no toca el disco."""
    a2 = copy.deepcopy(a)
    imagenes: list[tuple[str, str]] = []
    primera = None

    def renombra(bl: list[dict]) -> None:
        nonlocal primera
        for b in bl:
            if b.get("tipo") == "imagen" and b.get("archivo"):
                destino = f"{slug}-{len(imagenes) + 1:02d}.jpg"
                imagenes.append((b["archivo"], destino))
                b["archivo"] = f"{rel_img}/{destino}"
                if primera is None:
                    primera = (b["archivo"], str(b.get("alt") or ""))

    renombra(a2.get("intro") or [])
    for s in a2.get("secciones") or []:
        renombra(s.get("bloques") or [])
    if hero:                                                                   # la imagen destacada dibujada con IA (clipper/imagen_articulo.py) manda sobre la primera captura
        destino_h = f"{slug}-hero.jpg"
        imagenes.append((hero[0], destino_h))
        primera = (f"{rel_img}/{destino_h}", str(hero[1] or ""))
    titulo_video = str(a.get("h1") or a.get("title_seo") or "")
    a2["video_html"] = EMBED.format(id=video_id, titulo=titulo_video.replace('"', "&quot;")) if video_id else ""
    cuerpo = _art.a_markdown(a2, arts)
    cuerpo = re.sub(r"\A# .*\n+", "", cuerpo)                                  # el H1 lo pone la cabecera (`heading`), no el cuerpo
    ts = time.time() if ahora is None else ahora
    fecha = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    kw = str(a.get("keyword") or "").strip()
    titulo = (kw[:1].upper() + kw[1:]) if kw else str(a.get("h1") or "")
    meta = str(a.get("meta_descripcion") or "")
    campos: list[tuple[str, object]] = [("title", titulo)]
    if str(a.get("h1") or "").strip() and str(a.get("h1")).strip() != titulo:
        campos.append(("heading", str(a["h1"]).strip()))
    campos += [("description", meta), ("seoTitle", str(a.get("title_seo") or "")), ("focusKeyword", kw), ("excerpt", meta),
               ("category", categoria), ("pubDate", fecha), ("updatedDate", fecha)]
    if primera:
        campos += [("heroImage", primera[0]), ("heroImageAlt", primera[1])]
    campos += [("tags", []), ("draft", bool(borrador)), ("urlSlug", slug)]
    cabecera = "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in campos if v != "" or k in ("description",)) + "\n---\n\n"
    return cabecera + cuerpo, imagenes


# ------------------------------------------------------------------------------------------------------------------ escribir
# ------------------------------------------------------------------------------------------------------------------ tus artículos ya publicados (SOLO lectura)
_ID_ARTICULO = re.compile(r"[a-z0-9][a-z0-9_\-]*/[a-z0-9][a-z0-9_\-]*")


def _cabecera(texto: str) -> tuple[dict, str]:
    """(campos de la cabecera --- ... ---, cuerpo) de un .md. Sin PyYAML o con una cabecera rara, se leen las líneas «clave: valor» simples."""
    m = re.match(r"\ufeff?---\s*\n(.*?)\n---\s*\n?(.*)\Z", texto, flags=re.S)
    if not m:
        return {}, texto
    campos: dict = {}
    for linea in m.group(1).splitlines():
        k = re.match(r"([A-Za-z][\w]*):\s*(.*)$", linea)
        if k:
            v = k.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            campos[k.group(1)] = v
    return campos, m.group(2)


def lista_articulos(cfg, canal: str) -> dict:
    """Los artículos que ya tienes en tu proyecto de Astro (no borradores), para elegir uno en «Tools → Miniaturas de artículos» y ponerle una imagen nueva.
    -> {"configurado", "motivo", "articulos": [{"id": "categoria/slug", "titulo", "categoria", "descripcion"}]}. Solo LEE: no escribe nada en tu proyecto."""
    s = config_sitio(cfg, canal)
    if not s.get("configurado"):
        return {"configurado": False, "motivo": s.get("motivo", ""), "articulos": []}
    base = s["carpeta"] / s["articulos"]
    out = []
    for cat in s["categorias"]:
        d = base / cat
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                campos, _cuerpo = _cabecera(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if str(campos.get("draft", "")).lower() == "true":
                continue
            out.append({"id": f"{cat}/{f.stem}", "titulo": campos.get("title") or campos.get("heading") or f.stem, "categoria": cat,
                        "descripcion": campos.get("description") or campos.get("excerpt") or "", "url_slug": campos.get("urlSlug") or f.stem})
    out.sort(key=lambda x: normalize(x["titulo"]))
    return {"configurado": True, "motivo": "", "articulos": out}


def lee_articulo(cfg, canal: str, art_id: str, max_chars: int = 3500) -> dict | None:
    """El texto de UNO de tus artículos publicados (limpio: sin imágenes, enlaces, HTML ni código), para que la IA dibuje algo ESPECÍFICO de él. None si no existe o el id es raro."""
    if not _ID_ARTICULO.fullmatch(str(art_id or "")):
        return None
    s = config_sitio(cfg, canal)
    if not s.get("configurado"):
        return None
    cat, slug = art_id.split("/")
    if cat not in s["categorias"]:
        return None
    f = s["carpeta"] / s["articulos"] / cat / f"{slug}.md"
    if not f.is_file() or not _dentro(s["carpeta"] / s["articulos"], f):
        return None
    try:
        campos, cuerpo = _cabecera(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    t = re.sub(r"```.*?```", " ", cuerpo, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[*_`>|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return {"id": art_id, "titulo": campos.get("title") or campos.get("heading") or slug, "descripcion": campos.get("description") or campos.get("excerpt") or "",
            "texto": t[:max_chars]}


def _limpia_linea(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_`>|]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def lee_articulo_completo(cfg, canal: str, art_id: str) -> dict | None:
    """Uno de tus artículos publicados ENTERO y estructurado (para el carrusel o el post desde un artículo, SOLO lectura): título, descripción, etiquetas, palabra clave, los párrafos y títulos
    limpios (sin código ni HTML) y sus IMÁGENES con el párrafo al que acompañan (`tras`) y su texto alternativo. Las rutas de imagen se resuelven dentro de tu proyecto (nada fuera de él).
    -> {"id", "titulo", "descripcion", "tags", "keyword", "parrafos": [str], "titulos": [str], "imagenes": [{"ruta": Path, "alt", "tras"}], "hero": Path | None} o None."""
    if not _ID_ARTICULO.fullmatch(str(art_id or "")):
        return None
    cf = config_sitio(cfg, canal)
    if not cf.get("configurado"):
        return None
    cat, slug = art_id.split("/")
    if cat not in cf["categorias"]:
        return None
    base = cf["carpeta"] / cf["articulos"]
    f = base / cat / f"{slug}.md"
    if not f.is_file() or not _dentro(base, f):
        return None
    try:
        campos, cuerpo = _cabecera(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None

    def resuelve(src: str) -> Path | None:
        src = str(src or "").strip().split(" ")[0].strip("<>")
        if not src or src.startswith(("http://", "https://", "data:")):
            return None
        p = (f.parent / src).resolve() if not src.startswith("/") else (cf["carpeta"] / "public" / src.lstrip("/")).resolve()
        return p if p.is_file() and _dentro(cf["carpeta"], p) and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") else None

    cuerpo = re.sub(r"```.*?```", " ", cuerpo, flags=re.S)
    parrafos, titulos, imagenes = [], [], []
    for bloque in re.split(r"\n\s*\n", cuerpo):
        b = bloque.strip()
        if not b:
            continue
        for m in re.finditer(r"!\[([^\]]*)\]\(([^)]*)\)", b):
            ruta = resuelve(m.group(2))
            if ruta:
                imagenes.append({"ruta": ruta, "alt": _limpia_linea(m.group(1)), "tras": max(0, len(parrafos) - 1)})
        if re.match(r"#{1,6}\s", b):
            t = _limpia_linea(re.sub(r"^#{1,6}\s*", "", b.splitlines()[0]))
            if t:
                titulos.append(t)
                parrafos.append(t)
            continue
        t = _limpia_linea(b)
        if len(t) >= 25 and not t.startswith(("---", "|")):
            parrafos.append(t)
    hero = None
    hi = str(campos.get("heroImage") or "").strip()
    if hi:
        hero = resuelve(hi)
    tags = [x.strip(" \"'") for x in re.findall(r"[^,\[\]]+", str(campos.get("tags") or "")) if x.strip(" \"'")][:8]
    return {"id": art_id, "titulo": campos.get("title") or campos.get("heading") or slug, "descripcion": campos.get("description") or campos.get("excerpt") or "",
            "tags": tags, "keyword": campos.get("focusKeyword") or "", "parrafos": parrafos, "titulos": titulos, "imagenes": imagenes, "hero": hero}


def resuelve_url(cfg, canal: str, url: str) -> str | None:
    """Una URL de TU web (tu-sitio.example/<categoría>/<slug>/) -> el id de su archivo en tu proyecto («google/etiquetas-canonicals»), o None si no es de tu web / no existe. Nunca se descarga
    nada de internet: se lee el archivo de tu proyecto (y solo los artículos en español, publicados)."""
    from urllib.parse import urlparse
    cf = config_sitio(cfg, canal)
    if not cf.get("configurado"):
        return None
    try:
        info = (cfg.get_path(f"videos.canales.{canal}") or {}) if cfg is not None else {}
        web = urlparse(str((info.get("enlaces") or {}).get("web") or "")).hostname or ""
        u = urlparse(str(url or "").strip())
    except (ValueError, AttributeError):
        return None
    dominio = web.lower().removeprefix("www.")
    host = (u.hostname or "").lower().removeprefix("www.")
    if not dominio or host != dominio or u.scheme not in ("http", "https"):
        return None
    partes = [p for p in u.path.split("/") if p]
    if len(partes) < 2 or partes[0] == "en":
        return None
    cat, slug = partes[0].lower(), partes[-1].lower()
    if cat.startswith("seo-"):                                 # las URLs viejas (/seo-google/x/) = /google/x/
        cat = cat[4:]
    lista = lista_articulos(cfg, canal)["articulos"]
    por_id = {a["id"]: a for a in lista}
    if f"{cat}/{slug}" in por_id:
        return f"{cat}/{slug}"
    for a in lista:
        if a["categoria"] == cat and str(a.get("url_slug") or "").lower() == slug:
            return a["id"]
    return None


def info(cfg, canal: str, jid: str = "", dir_outbox=None) -> dict:
    """Lo que necesita la pantalla: si está configurado, categorías, y si este artículo ya se mandó a tu web."""
    s = config_sitio(cfg, canal)
    out = {"configurado": bool(s.get("configurado")), "motivo": s.get("motivo", "")}
    if s.get("configurado"):
        out.update(categorias=s["categorias"], categoria_defecto=s["categoria_defecto"], borrador=s["borrador"], carpeta=str(s["carpeta"]))
    if jid:
        try:
            out["hecho"] = json.loads((_art.ruta(jid, dir_outbox) / "sitio.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["hecho"] = None
    return out


def exporta(jid: str, cfg, canal: str, categoria: str | None = None, borrador: bool | None = None, slug: str | None = None,
            dir_outbox=None, video_id: str | None = None, ahora: float | None = None) -> dict:
    """Escribe el artículo de ese vídeo en tu proyecto de Astro (ver arriba). Nunca pisa nada. -> {"ok": True, "archivo", "imagenes", …} o {"ok": False, "error"}."""
    s = config_sitio(cfg, canal)
    if not s.get("configurado"):
        return {"ok": False, "error": s.get("motivo") or "Falta configurar tu proyecto de Astro."}
    a = _art.lee(jid, dir_outbox)
    if not a:
        return {"ok": False, "error": "Este vídeo todavía no tiene artículo: créalo primero."}
    categoria = str(categoria or s["categoria_defecto"]).strip().lower()
    if categoria not in s["categorias"]:
        return {"ok": False, "error": f"La categoría «{categoria}» no existe en tu web (las tuyas: {', '.join(s['categorias'])})."}
    slug = _slug(slug or a.get("slug") or a.get("keyword") or "")
    if not slug:
        return {"ok": False, "error": "El artículo no tiene un slug válido: escribe uno (solo minúsculas, números y guiones)."}
    base = Path(s["carpeta"])
    carpeta_md = base / str(s["articulos"]) / categoria
    carpeta_img = base / str(s["imagenes"])
    destino_md = carpeta_md / f"{slug}.md"
    if not (_dentro(base, carpeta_md) and _dentro(base, carpeta_img) and _dentro(base, destino_md)):
        return {"ok": False, "error": "Las rutas de `sitio` en config.yaml se salen de tu proyecto: no escribo nada."}
    previos = [p for p in (base / str(s["articulos"])).rglob(f"{slug}.md")] if (base / str(s["articulos"])).is_dir() else []
    if previos:
        rel = previos[0].relative_to(base).as_posix()
        return {"ok": False, "error": f"Ya hay un artículo con ese slug en tu web ({rel}). No lo piso: cambia el slug y vuelve a intentarlo."}
    rel_img = os.path.relpath(carpeta_img, carpeta_md).replace("\\", "/")
    if video_id is None:
        try:
            from . import historial
            video_id = historial.video_id_de(a.get("jid") and (_base_de(a.get("jid")) or "") or "") or ""
        except Exception:  # noqa: BLE001
            video_id = ""
    arts: list[dict] = []
    try:
        from . import enlaces as enl
        info_c = cfg.get_path(f"videos.canales.{canal}") or {}
        arts = enl.articulos_del_canal(info_c) if info_c else []
    except Exception:  # noqa: BLE001
        arts = []
    try:
        from . import imagen_articulo
        h = imagen_articulo.elegida(jid, dir_outbox)
    except Exception:  # noqa: BLE001
        h = None
    hero = (h["ruta"].name, h["alt"]) if h and h["ruta"].is_file() else None
    texto, imagenes = prepara(a, arts, categoria, slug, s["borrador"] if borrador is None else bool(borrador), rel_img, video_id or "", ahora, hero)
    origen = _art.ruta(jid, dir_outbox)
    faltan = [o for o, _d in imagenes if not (origen / o).is_file()]
    if faltan:
        return {"ok": False, "error": f"Faltan capturas del artículo en el PC ({', '.join(faltan[:3])}): pulsa «↻ Rehacer» y vuelve a intentarlo."}
    for _o, d in imagenes:                                                    # nada se pisa: ni el .md ni una captura con otro contenido
        dest = carpeta_img / d
        if dest.exists() and dest.read_bytes() != (origen / _o).read_bytes():
            return {"ok": False, "error": f"Ya existe la imagen «{d}» en tu proyecto y es distinta: cambia el slug."}
    carpeta_img.mkdir(parents=True, exist_ok=True)
    carpeta_md.mkdir(parents=True, exist_ok=True)
    for _o, d in imagenes:
        if not (carpeta_img / d).exists():
            shutil.copy2(origen / _o, carpeta_img / d)
    destino_md.write_text(texto, encoding="utf-8", newline="\n")
    res = {"ok": True, "archivo": str(destino_md), "relativo": destino_md.relative_to(base).as_posix(), "slug": slug, "categoria": categoria,
           "borrador": "draft: true" in texto, "imagenes": [str(carpeta_img / d) for _o, d in imagenes], "video": bool(video_id), "hero_ia": bool(hero),
           "carpeta": str(base), "ts": time.time() if ahora is None else ahora}
    try:
        (origen / "sitio.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    log(f"Artículo de {jid} → tu web: {res['relativo']} ({len(imagenes)} imagen(es), {'borrador' if res['borrador'] else 'PUBLICADO'}).")
    return res


def _base_de(jid: str) -> str:
    """La «familia» del vídeo (sin --pro / -v2): lo que usa el Historial para saber su id de YouTube."""
    try:
        from . import versiones
        return versiones.parse(jid)["base"]
    except Exception:  # noqa: BLE001
        return re.sub(r"--pro(?:-v\d+)?$", "", jid)


def _vscode() -> str | None:
    """Code.exe de Visual Studio Code, si está."""
    cand = []
    w = shutil.which("code")
    if w:
        cand.append(Path(w).resolve().parent.parent / "Code.exe")
    for var in ("LOCALAPPDATA", "ProgramFiles"):
        if os.environ.get(var):
            cand.append(Path(os.environ[var]) / "Programs" / "Microsoft VS Code" / "Code.exe")
            cand.append(Path(os.environ[var]) / "Microsoft VS Code" / "Code.exe")
    return next((str(c) for c in cand if c.is_file()), None)


def abre(jid: str, dir_outbox=None, _lanza=None) -> dict:
    """Abre en Visual Studio Code el artículo que se acaba de mandar a tu web (la ruta sale de `sitio.json`, nunca de la página)."""
    try:
        r = json.loads((_art.ruta(jid, dir_outbox) / "sitio.json").read_text(encoding="utf-8"))
        archivo, carpeta = Path(r["archivo"]), Path(r["carpeta"])
    except (OSError, ValueError, KeyError):
        return {"ok": False, "error": "Este artículo todavía no se ha mandado a tu web."}
    if not archivo.is_file() or not _dentro(carpeta, archivo):
        return {"ok": False, "error": "El archivo ya no está en tu proyecto."}
    code = _vscode()
    lanza = _lanza or (lambda cmd: subprocess.Popen(cmd, creationflags=0x08000000, close_fds=True))
    try:
        if code:
            lanza([code, "-r", str(carpeta), str(archivo)])
            return {"ok": True, "con": "Visual Studio Code"}
        os.startfile(str(archivo))                                            # el programa que tengas para .md
        return {"ok": True, "con": "tu editor de .md"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"No pude abrirlo ({str(exc)[:100]}). Está en {archivo}."}


# ------------------------------------------------------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    """Con un proyecto de Astro de MENTIRA en una carpeta temporal (y su `content.config.ts` real como referencia de campos si existe)."""
    import tempfile
    try:
        import yaml
    except ImportError:                                                       # pragma: no cover
        yaml = None
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:300]})

    from PIL import Image

    class Cfg:
        def __init__(self, sitio):
            self.sitio = sitio

        def get_path(self, k, d=None):
            if k == "videos.canales.ejemplo":
                return {"nombre": "Mi canal", "sitio": self.sitio} if self.sitio is not None else {"nombre": "Mi canal"}
            return d

    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        web = t / "web"
        for c in ("google", "youtube", "video"):
            (web / "src" / "content" / "blog" / c).mkdir(parents=True)
        (web / "src" / "assets" / "uploads").mkdir(parents=True)
        (web / "src" / "content" / "blog" / "google" / "ya-existe.md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
        salida = t / "out"
        jid = "2026-01-01-12-00-00-abc--pro"
        carp = salida / jid / "articulo"
        carp.mkdir(parents=True)
        for i in (1, 2):
            Image.new("RGB", (64, 36), (30 * i, 80, 120)).save(carp / f"img-{i:02d}.jpg", "JPEG")
        a = {"title_seo": "Interlinking SEO: Qué Es y Cómo Hacerlo [2026]", "meta_descripcion": "Aprende el \"interlinking\": enlaces internos que retienen usuarios.",
             "slug": "como-dominar-el-interlinking", "keyword": "interlinking en SEO", "h1": "Cómo Dominar el Interlinking: Multiplica Tu Tráfico",
             "intro": [{"tipo": "parrafo", "texto": "El **interlinking** conecta tus páginas."}],
             "secciones": [{"h2": "Qué es", "bloques": [{"tipo": "parrafo", "texto": "Es enlazar contenidos."},
                                                        {"tipo": "imagen", "alt": "Ejemplo de enlaces internos", "pie": "", "archivo": "img-01.jpg", "t": 5.0},
                                                        {"tipo": "lista", "numerada": False, "items": ["uno", "dos"]}]},
                           {"h2": "Cómo", "bloques": [{"tipo": "imagen", "alt": "Clúster de contenido", "pie": "", "archivo": "img-02.jpg", "t": 9.0}]}],
             "faq": [{"pregunta": "¿Cuántos enlaces?", "respuesta": "Los que aporten."}], "conclusion": "Empieza hoy.", "cta_texto": "Reserva", "cta_url": "https://x.test/c/",
             "video_html": "", "jid": jid}
        (carp / "articulo.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        cfg = Cfg({"carpeta": str(web)})

        r = exporta(jid, cfg, "ejemplo", dir_outbox=salida, video_id="sADR29d-93Y", ahora=1_700_000_000)
        md = web / "src/content/blog/google/como-dominar-el-interlinking.md"
        texto = md.read_text(encoding="utf-8") if md.is_file() else ""
        fm = {}
        if yaml and texto.startswith("---\n"):
            fm = yaml.safe_load(texto.split("---\n")[1]) or {}
        caso("crea el .md en src/content/blog/<categoría>/<slug>.md y cuenta lo que hizo", r.get("ok") and md.is_file() and r["relativo"].endswith("google/como-dominar-el-interlinking.md"), r)
        caso("la cabecera es la de tu esquema: title (la palabra clave), heading (el H1), description, seoTitle, focusKeyword, excerpt, category, fechas, urlSlug",
             fm.get("title") == "Interlinking en SEO" and fm.get("heading") == a["h1"] and fm.get("description") == a["meta_descripcion"]
             and fm.get("seoTitle") == a["title_seo"] and fm.get("focusKeyword") == "interlinking en SEO" and fm.get("excerpt") == a["meta_descripcion"]
             and fm.get("category") == "google" and fm.get("urlSlug") == "como-dominar-el-interlinking" and "pubDate" in fm and "updatedDate" in fm, fm)
        caso("nace como BORRADOR (`draft: true`) y con tags vacíos", fm.get("draft") is True and fm.get("tags") == [], fm)
        caso("la imagen destacada es la primera captura, con su alt, con la ruta relativa de tu CMS",
             fm.get("heroImage") == "../../../assets/uploads/como-dominar-el-interlinking-01.jpg" and fm.get("heroImageAlt") == "Ejemplo de enlaces internos", fm)
        caso("las capturas se copian a src/assets/uploads con el slug por delante, idénticas",
             all((web / "src/assets/uploads" / f"como-dominar-el-interlinking-0{i}.jpg").read_bytes() == (carp / f"img-0{i}.jpg").read_bytes() for i in (1, 2)), r.get("imagenes"))
        cuerpo = texto.split("---\n", 2)[-1] if texto else ""
        caso("el cuerpo no repite el H1, enlaza las imágenes con la ruta de tu web (no «img-01.jpg») y trae sus listas y negritas",
             not cuerpo.lstrip().startswith("# ") and "![Ejemplo de enlaces internos](../../../assets/uploads/como-dominar-el-interlinking-01.jpg)" in cuerpo
             and "![Clúster de contenido](../../../assets/uploads/como-dominar-el-interlinking-02.jpg)" in cuerpo and "(img-0" not in cuerpo
             and "**interlinking**" in cuerpo and "- uno" in cuerpo and "## Preguntas frecuentes" in cuerpo, cuerpo[:300])
        caso("el vídeo va con TU formato (jo-embed + youtube-nocookie + su id)", '<div class="jo-embed"><iframe src="https://www.youtube-nocookie.com/embed/sADR29d-93Y"' in cuerpo
             and "Cómo Dominar" in cuerpo, cuerpo[:200])
        caso("el texto con comillas y dos puntos (la meta lleva «interlinking» entre comillas) sale como YAML válido y se lee idéntico", fm.get("description") == a["meta_descripcion"], fm.get("description"))
        # --- la imagen destacada dibujada con IA (si el autor eligió una)
        jh = "2026-01-03-12-00-00-hhh--pro"
        ch = salida / jh / "articulo"
        shutil.copytree(carp, ch)
        Image.new("RGB", (64, 36), (128, 128, 128)).save(ch / "hero-2.jpg", "JPEG")
        (ch / "hero.json").write_text(json.dumps({"concepto": {"escena": "x" * 50, "alt": "Caricatura de un imán"}, "imagenes": [{"archivo": "hero-2.jpg"}], "elegida": "hero-2.jpg"}), encoding="utf-8")
        a_h = dict(a, jid=jh)
        (ch / "articulo.json").write_text(json.dumps(a_h, ensure_ascii=False), encoding="utf-8")
        rh = exporta(jh, cfg, "ejemplo", slug="con-hero-ia", dir_outbox=salida, video_id="")
        th = (web / "src/content/blog/google/con-hero-ia.md").read_text(encoding="utf-8") if rh.get("ok") else ""
        fh = yaml.safe_load(th.split("---\n")[1]) if yaml and th else {}
        caso("con una imagen destacada elegida (IA), esa es el heroImage (con su alt) y se copia como <slug>-hero.jpg; las capturas siguen en el cuerpo",
             rh.get("ok") and rh.get("hero_ia") and fh.get("heroImage") == "../../../assets/uploads/con-hero-ia-hero.jpg" and fh.get("heroImageAlt") == "Caricatura de un imán"
             and (web / "src/assets/uploads/con-hero-ia-hero.jpg").read_bytes() == (ch / "hero-2.jpg").read_bytes() and "con-hero-ia-01.jpg)" in th, (rh, fh))

        # --- reglas de seguridad
        r2 = exporta(jid, cfg, "ejemplo", dir_outbox=salida, video_id="x")
        caso("NO pisa: el mismo artículo otra vez se niega y dice cuál es", not r2["ok"] and "Ya hay un artículo" in r2["error"] and md.read_text(encoding="utf-8") == texto, r2)
        r3 = exporta(jid, cfg, "ejemplo", slug="ya-existe", dir_outbox=salida, video_id="")
        caso("…y tampoco si el slug ya está en OTRA categoría o lo escribió otro (ya-existe.md)", not r3["ok"] and "ya-existe.md" in r3["error"], r3)
        r4 = exporta(jid, cfg, "ejemplo", categoria="inventada", dir_outbox=salida)
        caso("una categoría que no existe en tu web se rechaza (y dice las tuyas)", not r4["ok"] and "google, youtube, video" in r4["error"], r4)
        r5 = exporta(jid, cfg, "ejemplo", categoria="video", slug="../../../../etc/Mi Slug Raro!", borrador=False, dir_outbox=salida, video_id="")
        md5 = web / "src/content/blog/video/etc-mi-slug-raro.md"
        caso("un slug con rutas o signos se limpia («../../etc/Mi Slug Raro!» → etc-mi-slug-raro) y nunca sale de tu proyecto; borrador=False → draft: false",
             r5["ok"] and md5.is_file() and "draft: false" in md5.read_text(encoding="utf-8") and not (t / "etc").exists(), r5)
        caso("sin id de YouTube (el vídeo aún no se publicó) deja el aviso para pegarlo, sin romper nada",
             "<!-- Pega aquí el vídeo de YouTube" in md5.read_text(encoding="utf-8"), md5.read_text(encoding="utf-8")[-200:])
        # --- configuración
        caso("sin `sitio` en config.yaml no hay nada que hacer y lo dice", not exporta(jid, Cfg(None), "ejemplo", dir_outbox=salida)["ok"]
             and info(Cfg(None), "ejemplo")["configurado"] is False)
        mal = exporta(jid, Cfg({"carpeta": str(t / "no-existe")}), "ejemplo", dir_outbox=salida)
        caso("una carpeta que no es un proyecto de Astro (sin src/content) se avisa", not mal["ok"] and "no tiene src/content" in mal["error"] or "No encuentro tu proyecto" in mal["error"], mal)
        raro = exporta(jid, Cfg({"carpeta": str(web), "imagenes": "../fuera"}), "ejemplo", slug="otro-uno", dir_outbox=salida)
        caso("una carpeta de imágenes que se sale del proyecto se rechaza (no escribe fuera)", not raro["ok"] and "se salen" in raro["error"] and not (t / "fuera").exists(), raro)
        r6 = exporta(jid, Cfg({"carpeta": str(web), "imagenes": "public/img"}), "ejemplo", slug="con-public", dir_outbox=salida, video_id="")
        t6 = (web / "src/content/blog/google/con-public.md").read_text(encoding="utf-8") if r6.get("ok") else ""
        caso("la carpeta de imágenes se puede cambiar y la ruta relativa se recalcula sola (public/img → ../../../../public/img)",
             r6.get("ok") and "../../../../public/img/con-public-01.jpg" in t6 and (web / "public/img/con-public-01.jpg").is_file(), r6)
        # --- sin capturas
        a2 = dict(a); a2["secciones"] = [{"h2": "Solo texto", "bloques": [{"tipo": "parrafo", "texto": "Sin fotos."}]}]
        j2 = "2026-01-02-12-00-00-def--pro"; c2 = salida / j2 / "articulo"; c2.mkdir(parents=True)
        (c2 / "articulo.json").write_text(json.dumps({**a2, "jid": j2, "slug": "sin-fotos"}, ensure_ascii=False), encoding="utf-8")
        r7 = exporta(j2, cfg, "ejemplo", dir_outbox=salida, video_id="")
        t7 = (web / "src/content/blog/google/sin-fotos.md").read_text(encoding="utf-8") if r7.get("ok") else ""
        caso("un artículo sin capturas sale sin imagen destacada (no inventa una)", r7.get("ok") and "heroImage" not in t7 and not r7["imagenes"], r7)
        # --- una captura que falta
        (carp / "img-02.jpg").unlink()
        r8 = exporta(jid, cfg, "ejemplo", slug="faltan-capturas", dir_outbox=salida, video_id="")
        caso("si falta una captura en el PC no escribe nada a medias", not r8["ok"] and "Faltan capturas" in r8["error"] and not (web / "src/content/blog/google/faltan-capturas.md").exists(), r8)
        # --- abrir
        lanzado = []
        ab = abre(jid, salida, _lanza=lanzado.append)
        caso("«Abrir en Visual Studio Code» abre el ÚLTIMO artículo mandado (la ruta sale de lo apuntado, no de la página): carpeta + archivo",
             ab["ok"] and (not lanzado or (lanzado[0][-1].endswith("con-public.md") and lanzado[0][-3] == "-r" and lanzado[0][-2].endswith("web"))), (ab, lanzado))
        caso("…y de un vídeo que no se mandó a la web, no abre nada", not abre("2026-09-09-zz--pro", salida)["ok"])
    # --- tus artículos ya publicados (solo lectura): para ponerles una imagen nueva
    with tempfile.TemporaryDirectory() as t4:
        t4 = Path(t4)
        web4 = t4 / "web"
        for cat in ("google", "youtube", "video"):
            (web4 / "src" / "content" / "blog" / cat).mkdir(parents=True)
        (web4 / "src" / "content" / "blog" / "google" / "etiquetas-canonicals.md").write_text(
            '---\ntitle: "¿Cómo usar etiquetas canonicals?"\nheading: "ETIQUETAS CANONICALS"\ndescription: "Evita el contenido duplicado."\ndraft: false\n---\n\n'
            'Una **canonical** le dice a Google cuál es la URL original.\n\n![captura](../../../assets/x.webp)\n\n## Dónde ponerla\n\nEn el `head` de [tu página](https://ejemplo.com).\n\n```html\n<link rel="canonical">\n```\n', encoding="utf-8")
        (web4 / "src" / "content" / "blog" / "youtube" / "borrador.md").write_text("---\ntitle: Borrador\ndraft: true\n---\ntexto\n", encoding="utf-8")
        (web4 / "src" / "content" / "blog" / "video" / "a-video.md").write_text("---\ntitle: Otro artículo\n---\ntexto del otro\n", encoding="utf-8")
        cfg4 = Cfg({"carpeta": str(web4)})
        la = lista_articulos(cfg4, "ejemplo")
        caso("lista tus artículos publicados (con categoría y descripción), sin borradores y ordenados por título",
             la["configurado"] and [x["id"] for x in la["articulos"]] == ["google/etiquetas-canonicals", "video/a-video"] and la["articulos"][0]["descripcion"] == "Evita el contenido duplicado."
             and la["articulos"][0]["categoria"] == "google", la)
        a4 = lee_articulo(cfg4, "ejemplo", "google/etiquetas-canonicals")
        caso("lee el texto de uno limpio (sin imágenes, enlaces, código, HTML ni marcas de Markdown) y con su título y descripción",
             a4 and a4["titulo"] == "¿Cómo usar etiquetas canonicals?" and a4["descripcion"] == "Evita el contenido duplicado." and "canonical le dice a Google" in a4["texto"]
             and "tu página" in a4["texto"] and "http" not in a4["texto"] and "![" not in a4["texto"] and "<link" not in a4["texto"] and "**" not in a4["texto"], a4)
        caso("ids raros no salen del proyecto: ../, categoría que no existe o artículo que no existe → None",
             all(lee_articulo(cfg4, "ejemplo", x) is None for x in ("../../etc/passwd", "google/../../x", "otra/etiquetas-canonicals", "google/no-existe", "google/etiquetas-canonicals/../x", "")))
        caso("sin proyecto configurado avisa y no lista nada", not lista_articulos(Cfg(None), "ejemplo")["configurado"] and lista_articulos(Cfg(None), "ejemplo")["articulos"] == [])
    # --- tus artículos ENTEROS (para el carrusel/post desde un artículo) y las URLs de tu web
    with tempfile.TemporaryDirectory() as t5:
        t5 = Path(t5)
        web5 = t5 / "web"
        for cat in ("google", "youtube", "video"):
            (web5 / "src" / "content" / "blog" / cat).mkdir(parents=True)
        (web5 / "src" / "assets" / "uploads").mkdir(parents=True)
        Image.new("RGB", (800, 450), (10, 120, 200)).save(web5 / "src" / "assets" / "uploads" / "uno.jpg", "JPEG")
        Image.new("RGB", (800, 450), (200, 40, 40)).save(web5 / "src" / "assets" / "uploads" / "hero.webp", "WEBP")
        (web5 / "src" / "content" / "blog" / "google" / "etiquetas-canonicals.md").write_text(
            '---\ntitle: "Etiquetas canonical"\ndescription: "Evita duplicados."\nfocusKeyword: "canonical"\ntags: ["seo técnico", "canonical"]\nurlSlug: "como-usar-canonical"\n'
            'heroImage: "../../../assets/uploads/hero.webp"\n---\n\nUna **canonical** le dice a Google cuál es la URL original de una página duplicada.\n\n'
            '## Dónde ponerla\n\nPonla en el `head` de cada página duplicada, apuntando siempre a la versión buena.\n\n![captura de Search Console](../../../assets/uploads/uno.jpg)\n\n'
            'Comprueba en Search Console que Google respeta tu canonical.\n\n![remota](https://ejemplo.com/x.jpg)\n\n![fuera](../../../../../secreto.jpg)\n', encoding="utf-8")
        cfg5 = Cfg({"carpeta": str(web5)})
        comp = lee_articulo_completo(cfg5, "ejemplo", "google/etiquetas-canonicals")
        caso("el artículo entero: título, etiquetas, palabra clave, párrafos y títulos limpios, y SOLO sus imágenes locales con el párrafo al que acompañan (la remota y la que se sale del proyecto no entran)",
             comp and comp["titulo"] == "Etiquetas canonical" and comp["tags"] == ["seo técnico", "canonical"] and comp["keyword"] == "canonical"
             and comp["titulos"] == ["Dónde ponerla"] and len(comp["parrafos"]) == 4 and "canonical le dice a Google" in comp["parrafos"][0] and "**" not in comp["parrafos"][0]
             and [(i["ruta"].name, i["alt"], i["tras"]) for i in comp["imagenes"]] == [("uno.jpg", "captura de Search Console", 2)] and comp["hero"] and comp["hero"].name == "hero.webp", comp)
        caso("ids raros o de otro sitio → None", all(lee_articulo_completo(cfg5, "ejemplo", x) is None for x in ("../x/y", "google/../../x", "otra/etiquetas-canonicals", "google/no-existe", "")))

        class Cfg6(Cfg):
            def get_path(self, k, d=None):
                if k == "videos.canales.ejemplo":
                    return {"nombre": "Mi canal", "sitio": self.sitio, "enlaces": {"web": "https://tu-sitio.example/"}}
                return d
        cfg6 = Cfg6({"carpeta": str(web5)})
        caso("una URL de TU web se convierte en el archivo de tu proyecto (por su nombre, por su urlSlug y con las URLs viejas /seo-google/…); una de otro sitio, de la web en inglés o inexistente, no",
             resuelve_url(cfg6, "ejemplo", "https://tu-sitio.example/google/etiquetas-canonicals/") == "google/etiquetas-canonicals"
             and resuelve_url(cfg6, "ejemplo", "https://www.tu-sitio.example/google/como-usar-canonical") == "google/etiquetas-canonicals"
             and resuelve_url(cfg6, "ejemplo", "https://tu-sitio.example/seo-google/etiquetas-canonicals/") == "google/etiquetas-canonicals"
             and resuelve_url(cfg6, "ejemplo", "https://otraweb.com/google/etiquetas-canonicals/") is None and resuelve_url(cfg6, "ejemplo", "https://tu-sitio.example/en/google/x/") is None
             and resuelve_url(cfg6, "ejemplo", "https://tu-sitio.example/google/no-existe/") is None and resuelve_url(cfg6, "ejemplo", "javascript:alert(1)") is None
             and resuelve_url(cfg6, "ejemplo", "") is None)
    return filas
