"""📝 Carrusel desde un ARTÍCULO (o un texto) en vez de un vídeo (el autor, 13-oct-2026: «que también funcione con artículos web… que yo pegue el texto de un artículo y con base en eso genere ese tipo de carruseles»).

Reusa TODO el carrusel de los vídeos (`carrusel.crea`: la IA propone portada + pasos + cierre y los textos de Instagram/TikTok, las diapositivas se dibujan con tu estilo, «✏ Editar», descargas…).
Lo único distinto es la FUENTE y de dónde sale la imagen de cada diapositiva:
- Fuente: un artículo de TU web (`videos.canales.<canal>.sitio`: se LEE de tu proyecto de Astro, nada se descarga de internet; una URL de tu-sitio.example se convierte en su archivo) o un texto que
  pegas/escribes. Se guarda como `videos/texto-<id>--pro/` (fuente.json + las imágenes del artículo + carrusel/): el 🗑 lo borra TODO. Los «segundos» son de mentira (una frase = 4 s): así las
  reglas del carrusel (citas copiadas del texto, separación entre pasos) sirven igual.
- Imagen de cada paso (3 modos; «auto» = el 1.º y, si no encaja, el 2.º): «articulo» (las imágenes del propio artículo, la más cercana a ese paso; una IA MIRA que ayude a entender el paso de un
  vistazo y, si no, prueba con otra o cae al símbolo), «simbolo» (el icono de tu biblioteca de ilustraciones animadas que mejor representa el paso —lupa, candado, gráfica…—, dibujado fijo con tus
  colores) y «texto» (sin imagen: el título y la frase grandes).
- 🧠 Revisión de CLARIDAD (el autor: «tiene que poderse comunicar bien, sin mucho texto»): una IA lee cada diapositiva como alguien que NO ha leído el artículo y acorta o reescribe (solo con lo que dice
  el artículo) lo que no se entienda de un vistazo; el código exige título corto (≤ 5 palabras) y frase corta (≤ 14).
Gasta 1 petición a Flash-Lite para el carrusel + 1 (a veces 2) para mirar las imágenes + 1 para la claridad: 0 de las 20 de los Flash, 0 de Cloudflare.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from PIL import Image

from .util import log

PREFIJO = "texto-"
SALTO = 4.0                                   # segundos «de mentira» entre frases
MODOS = ("auto", "articulo", "simbolo", "texto")
MAX_TEXTO = 14000                             # caracteres del artículo que lee la IA
MAX_PALABRAS_TITULO, MAX_PALABRAS_TEXTO = 5, 14
_ID_IMG = re.compile(r"img-\d{2}\.jpg")


# ------------------------------------------------------------------ la fuente
def es_texto(jid: str) -> bool:
    return str(jid or "").startswith(PREFIJO) and str(jid).endswith("--pro")


def jid_de(clave: str) -> str:
    return f"{PREFIJO}{hashlib.sha1(str(clave).encode('utf-8')).hexdigest()[:10]}--pro"


def _dir_out(jid: str, dir_outbox=None) -> Path:
    from . import config
    return Path(dir_outbox or config.paths()["outbox"]) / jid


def _limpia_texto(t: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", str(t or ""))     # (primero las imágenes y los enlaces de Markdown: si no, quitar la URL los dejaría a medias)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[ \t]*https?://\S+", "", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[*_`>]+", "", t)
    return t.replace("\r", "")


def parrafos_de(texto: str) -> list[str]:
    """Un texto pegado -> párrafos (por líneas en blanco; si es un solo bloque, en trozos de ~3 frases)."""
    t = _limpia_texto(texto)
    ps = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n", t)]
    ps = [p for p in ps if len(p) >= 12]
    if len(ps) == 1 and len(ps[0]) > 500:
        fr = re.split(r"(?<=[.!?…])\s+", ps[0])
        ps = [" ".join(fr[i:i + 3]) for i in range(0, len(fr), 3)]
    return ps


def _frases(parrafos: list[str]) -> list[dict]:
    out: list[dict] = []
    for pi, par in enumerate(parrafos):
        for t in re.split(r"(?<=[.!?…])\s+", par):
            t = t.strip()
            if len(t.split()) < 3:
                if out and out[-1]["p"] == pi:
                    out[-1]["texto"] += " " + t
                continue
            palabras = t.split()
            while len(palabras) > 38:                          # (una frase larguísima se corta: la cita tiene que caber)
                out.append({"t": len(out) * SALTO, "texto": " ".join(palabras[:30]), "pantalla": False, "p": pi})
                palabras = palabras[30:]
            if palabras:
                out.append({"t": len(out) * SALTO, "texto": " ".join(palabras), "pantalla": False, "p": pi})
    return out


def prepara(texto: str, titulo: str, art_id: str, cfg, dir_outbox=None, canal: str | None = None) -> dict:
    """Deja listo `videos/texto-<id>--pro/` con `fuente.json` (+ las imágenes del artículo en `fuente/`). `art_id` = un artículo de tu web («google/etiquetas-canonicals») o "" para un texto
    pegado. -> {"ok", "jid"} o {"ok": False, "error"}. Nunca lanza."""
    from . import sitio_astro
    from .config import canal_defecto
    canal = canal or canal_defecto(cfg)
    try:
        imagenes: list[dict] = []
        hero = None
        if art_id:
            a = sitio_astro.lee_articulo_completo(cfg, canal, art_id)
            if not a:
                return {"ok": False, "error": "No encuentro ese artículo en tu proyecto de Astro (¿se movió o es un borrador?)."}
            parrafos, titulo, tags, kw, origen = a["parrafos"], str(titulo or a["titulo"]), a["tags"] or ([a["keyword"]] if a["keyword"] else []), a["keyword"], f"web:{art_id}"
            clave = f"web:{art_id}"
        else:
            parrafos = parrafos_de(texto)
            titulo = " ".join(str(titulo or "").split())[:150] or (re.split(r"(?<=[.!?:])\s", parrafos[0], maxsplit=1)[0][:110] if parrafos else "")
            tags, kw, origen, clave, a = [], "", "texto", f"texto:{titulo}|{' '.join(parrafos)[:2000]}", None
        if sum(len(p) for p in parrafos) < 400 or len(parrafos) < 3:
            return {"ok": False, "error": "Hace falta un poco más de texto (unas 100 palabras como mínimo) para sacar de ahí un carrusel."}
        total, recorte = 0, []
        for p in parrafos:
            if total + len(p) > MAX_TEXTO:
                break
            recorte.append(p)
            total += len(p)
        parrafos = recorte
        jid = jid_de(clave)
        out = _dir_out(jid, dir_outbox)
        fuente_dir = out / "fuente"
        if fuente_dir.exists():
            shutil.rmtree(fuente_dir, ignore_errors=True)
        fuente_dir.mkdir(parents=True, exist_ok=True)
        if a:
            for k, im in enumerate(a["imagenes"][:12], 1):
                if im["tras"] >= len(parrafos):
                    continue
                nombre = f"img-{k:02d}.jpg"
                try:
                    with Image.open(im["ruta"]) as src:
                        v = src.convert("RGB")
                        v.thumbnail((1600, 1600))
                        v.save(fuente_dir / nombre, "JPEG", quality=90)
                    imagenes.append({"archivo": nombre, "alt": im["alt"], "tras": im["tras"]})
                except (OSError, ValueError):
                    continue
            if a.get("hero"):
                try:
                    with Image.open(a["hero"]) as src:
                        v = src.convert("RGB")
                        v.thumbnail((1600, 1600))
                        v.save(fuente_dir / "hero.jpg", "JPEG", quality=90)
                    hero = "hero.jpg"
                except (OSError, ValueError):
                    hero = None
        (out / "fuente.json").write_text(json.dumps({"titulo": titulo, "parrafos": parrafos, "tags": tags, "keyword": kw, "origen": origen, "art_id": art_id or "", "canal": canal, "titulos": (a["titulos"] if a else [])[:20],
                                                      "imagenes": imagenes, "hero": hero, "ts": time.time()}, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "jid": jid}
    except Exception as exc:                                   # noqa: BLE001
        log(f"Carrusel desde texto: {exc}")
        return {"ok": False, "error": f"No se pudo preparar el texto ({str(exc)[:160]})."}


def _lee_fuente(jid: str, dir_outbox=None) -> dict | None:
    try:
        d = json.loads((_dir_out(jid, dir_outbox) / "fuente.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def datos(jid: str, dir_outbox=None, dir_work=None) -> dict:
    """Lo mismo que `articulo.datos_del_video` pero de un texto: frases con «segundo» de mentira, sin vídeo ni pantalla. Lanza si falta la fuente."""
    f = _lee_fuente(jid, dir_outbox)
    if not f:
        raise FileNotFoundError("no encuentro la fuente de este carrusel")
    frases = _frases(f.get("parrafos") or [])
    out = _dir_out(jid, dir_outbox)
    imgs = [{**i, "ruta": out / "fuente" / i["archivo"]} for i in f.get("imagenes") or [] if _ID_IMG.fullmatch(str(i.get("archivo", ""))) and (out / "fuente" / i["archivo"]).is_file()]
    for i in imgs:                                             # la frase más cercana al párrafo de cada imagen (para emparejarla con un paso)
        i["frase"] = max([k for k, fr in enumerate(frases) if fr["p"] <= i["tras"]] or [0])
    capitulos = "\n".join(f"- {t}" for t in (f.get("titulos") or []))
    return {"jid": jid, "base": jid, "out_dir": out, "video": None, "titulo": str(f.get("titulo") or ""), "tags": list(f.get("tags") or []), "capitulos": capitulos,
            "duracion": (len(frases) + 1) * SALTO, "canal": str(f.get("canal") or ""), "eventos": [], "pantalla": [], "frases": frases, "texto_mode": True,
            "imagenes": imgs, "origen": f.get("origen", "texto"), "keyword": f.get("keyword", ""), "hero": (out / "fuente" / f["hero"]) if f.get("hero") and (out / "fuente" / f["hero"]).is_file() else None}


# ------------------------------------------------------------------ la petición
def esquema():
    try:
        from pydantic import BaseModel

        class Paso(BaseModel):
            titulo: str
            texto: str
            minuto: str
            cita: str
            icono: str

        class Portada(BaseModel):
            titulo: str
            subtitulo: str

        class Cierre(BaseModel):
            titulo: str
            texto: str

        class Carrusel(BaseModel):
            tipo: str
            portada: Portada
            pasos: list[Paso]
            cierre: Cierre
            pie_instagram: str
            tiktok_titulo: str
            tiktok_texto: str

        return Carrusel
    except Exception:                                          # noqa: BLE001
        return None


def arma_prompt(d: dict, canal: str, objetivo: str, max_pasos: int = 8) -> str:
    from . import ilustraciones
    from .carrusel import MIN_PASOS
    L = [f"Convierte este ARTÍCULO de «{canal}» en un CARRUSEL de imágenes para Instagram y TikTok: una portada, entre {MIN_PASOS} y {max_pasos} diapositivas (una por paso o idea) y un cierre. "
         "Quien solo vea el carrusel tiene que llevarse lo esencial del artículo y querer leerlo completo. Cada diapositiva se tiene que ENTENDER DE UN VISTAZO, con muy poco texto, sin haber leído el artículo.", "",
         f"Objetivo del negocio (tenlo en cuenta, sin vender en cada frase): {objetivo}", "",
         "REGLAS:",
         "- Solo lo que dice el artículo: no inventes pasos, datos, cifras ni herramientas.",
         "- «tipo»: \"pasos\" si enseña a hacer algo paso a paso, \"errores\" si trata de lo que se hace mal, \"consejos\" si son recomendaciones, \"ideas\" si son ideas o conceptos sueltos.",
         f"- «pasos»: de {MIN_PASOS} a {max_pasos}, en el ORDEN del artículo y sin repetir ninguno. Lo muy pequeño se junta con otro; la intro, la despedida y la venta se dejan fuera.",
         f"- Cada paso: «titulo» de 2 a {MAX_PALABRAS_TITULO} palabras, concreto («Revisa el sitemap», «Error: bloquear todo en robots.txt»), SIN número ni «Paso 1» delante (el diseño lo pone); «texto»: UNA frase de "
         f"{MAX_PALABRAS_TEXTO} palabras como MUCHO con lo que hay que hacer y por qué, tuteando, sin relleno ni jerga que haya que explicar.",
         "- «minuto»: el [Pn] EXACTO, tal como sale entre corchetes, de la línea donde se explica ese paso.",
         "- «cita»: de 4 a 12 palabras COPIADAS tal cual, seguidas, de la línea en que se basa ese paso (sin cambiar ninguna palabra). El programa comprueba que existen: un paso cuya cita no está en el artículo se descarta.",
         "- «icono»: la clave del SÍMBOLO que mejor representa ese paso a primera vista, de esta lista (o \"ninguno\" si ninguno encaja de verdad):", ilustraciones.catalogo_para_prompt(),
         "- «portada»: «titulo» de 3 a 8 palabras que prometa el resultado y enganche, SIN cifras (el diseño pone el número de pasos); «subtitulo» de hasta 10 palabras (para quién es o qué consigues).",
         "- «cierre»: «titulo» de 2 a 5 palabras que pida guardar el carrusel (p. ej. «¿Te sirvió? Guárdalo») y «texto» de hasta 16 palabras que invite a leer el artículo completo en mi web.",
         "- «pie_instagram»: el texto de la publicación (700 caracteres como mucho): la 1.ª línea con gancho, 3-5 líneas con lo más útil (una por paso importante, cada una en su línea) y una línea que "
         "pida guardar y diga que el artículo completo está en mi web («enlace en mi perfil»: en Instagram los enlaces no funcionan). SIN hashtags. 0-3 emojis.",
         "- «tiktok_titulo»: 80 caracteres como mucho, con gancho. «tiktok_texto»: 200 caracteres como mucho, 1-2 frases. Sin hashtags.",
         "- Nunca escribas URLs.", "",
         f"DATOS DEL ARTÍCULO: «{d['titulo']}» · palabra clave: {d.get('keyword') or '—'} · etiquetas: {', '.join(d['tags']) or '—'}", "Títulos de sus secciones:", d["capitulos"] or "(sin secciones)", "",
         "TEXTO del artículo (cada línea [Pn] es una frase, en orden):"]
    L += [f"[P{k + 1}] {f['texto']}" for k, f in enumerate(d["frases"])]
    L += ["", 'Responde SOLO con JSON: {"tipo", "portada": {"titulo", "subtitulo"}, "pasos": [{"titulo", "texto", "minuto", "cita", "icono"}], "cierre": {"titulo", "texto"}, '
              '"pie_instagram", "tiktok_titulo", "tiktok_texto"}']
    return "\n".join(L)


# ------------------------------------------------------------------ imágenes de cada paso
def _guarda_jpg(im: Image.Image, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(destino, "JPEG", quality=92)


def render_icono(clave: str, marca: dict, tono: str = "crema") -> Image.Image | None:
    """El símbolo `clave` de tu biblioteca de ilustraciones animadas como IMAGEN FIJA (en su momento más claro), con tus colores, sobre el fondo de la marca. None si la clave no existe."""
    from . import escena_completa
    from .carrusel import _pal_de
    from .ilus_escenas import CATALOGO
    if clave not in CATALOGO:
        return None
    pal = _pal_de(marca)
    dur = max(6.0, float(getattr(CATALOGO[clave], "dur_min", 4.5) or 4.5) + 1.5)
    try:
        return escena_completa.fotograma(clave, dur * 0.45, dur, 1280, 800, pal, frase="", tono=tono).convert("RGB")
    finally:
        escena_completa._fondo.cache_clear()


def _empareja(pasos: list[dict], imagenes: list[dict], n_frases: int) -> dict[str, dict]:
    """A cada paso, la imagen del artículo más cercana a donde se explica (cada imagen, a un solo paso, y a no más de ~25 % del artículo de distancia)."""
    cand = []
    tope = max(8, int(n_frases * 0.25))
    for s in pasos:
        fi = int(round(float(s["t"]) / SALTO))
        for im in imagenes:
            dist = abs(im["frase"] - fi)
            if dist <= tope:
                cand.append((dist, s["id"], im["archivo"]))
    cand.sort()
    usados_p, usadas_i, out = set(), set(), {}
    por_archivo = {i["archivo"]: i for i in imagenes}
    for dist, sid, arch in cand:
        if sid in usados_p or arch in usadas_i:
            continue
        usados_p.add(sid)
        usadas_i.add(arch)
        out[sid] = por_archivo[arch]
    return out


_PROMPT_IMG = """Eres el revisor de un carrusel de Instagram/TikTok hecho a partir de un ARTÍCULO. Para cada PASO te doy lo que dice la diapositiva y la IMAGEN que acompañaría. Decide si la imagen AYUDA a entender ese paso
DE UN VISTAZO (muestra la herramienta, la pantalla, el concepto o el resultado de los que habla, y se ve con claridad aunque se vea pequeña).
- coincide=true: la imagen enseña ESE paso en concreto (la herramienta, el ajuste, el ejemplo o el esquema de los que habla) y se entiende rápido.
- coincide=false: es de otra cosa, es una captura GENÉRICA (la portada de una web, un buscador, un panel o una página cualquiera que no ilustra ese paso concreto), una foto decorativa sin relación,
  un dibujo que no aclara nada, está casi ilegible a ese tamaño o necesita leer mucho texto para entenderse.
Sé MUY estricto: una captura de una web solo vale si lo que se ve es justo lo que el paso explica; una imagen que no ayuda es peor que un símbolo o que ninguna. Responde SOLO con JSON: {"pasos": [{"id": "p1", "coincide": true, "ve": "qué se ve, 8 palabras como mucho"}]} (todos los pasos, con su «id»)."""

_PROMPT_ALT = """Estos pasos de un carrusel tienen una imagen que NO ayuda. Para cada paso te doy varias imágenes ALTERNATIVAS del mismo artículo (cada una con su número). Elige la que de verdad ayuda a entender ese
paso de un vistazo, o 0 si NINGUNA lo hace (es mejor un símbolo o ninguna). Responde SOLO con JSON: {"pasos": [{"id": "p1", "elegida": 2}]} (todos los pasos, con su «id»)."""


def verifica_imagenes(c: dict, d: dict, carpeta: Path, cfg, _verifica=None, progreso=None) -> list[str]:
    """🧭 Una IA MIRA que cada imagen del artículo ayude a entender su paso: las que no, se cambian por otra imagen cercana del artículo (si ayuda) o se quitan (caerán al símbolo). Devuelve avisos."""
    from . import carrusel_coherencia as coh
    pasos = [s for s in c["pasos"] if s.get("captura") and s.get("imagen_tipo") == "articulo"]
    if not pasos:
        return []
    _v = _verifica or (lambda partes, va: coh.llama_imagenes(cfg, partes, va, c.get("jid", "")))
    partes: list = [_PROMPT_IMG]
    ids: set[str] = set()
    for s in pasos:
        try:
            jpg = coh._jpeg_chico(carpeta / "capturas" / s["captura"])
        except OSError:
            continue
        ids.add(s["id"])
        partes += [f"\n=== PASO {s['id']}: {s['titulo']} ===\nLa diapositiva dice: {s['texto']}\nLa imagen es del artículo (texto alternativo: «{s.get('img_alt', '')}»)", jpg]
    pasos = [s for s in pasos if s["id"] in ids]
    if not pasos:
        return []
    try:
        if progreso:
            progreso("Comprobando que cada imagen ayude a entender su paso…")
        res, _m = _v(partes, lambda t: coh.lee_veredictos(t, ids, "coincide"))
    except Exception as exc:                                    # noqa: BLE001
        log(f"AVISO: no pude comprobar las imágenes del carrusel desde texto ({str(exc)[:140]}).")
        for s in pasos:
            s["coherencia"] = "sin_comprobar"
        return ["No pude comprobar que cada imagen ayude a entender su paso (Gemini no respondió): míralas antes de publicar."]
    mal = [s for s in pasos if res.get(s["id"]) in (False, "false", "False", "no", 0)]
    for s in pasos:
        if s not in mal:
            s["coherencia"] = "ok"
    if not mal:
        log(f"Carrusel desde texto: las {len(pasos)} imágenes ayudan a entender su paso.")
        return []
    usadas = {s.get("img_origen") for s in c["pasos"] if s.get("captura")}
    libres = [i for i in d["imagenes"] if i["archivo"] not in usadas]
    cand: dict[str, list[dict]] = {}
    for s in mal:
        fi = int(round(float(s["t"]) / SALTO))
        cand[s["id"]] = sorted(libres, key=lambda i: abs(i["frase"] - fi))[:3]
    con_alt = [s for s in mal if cand.get(s["id"])]
    elegidas: dict = {}
    if con_alt:
        partes2: list = [_PROMPT_ALT]
        for s in con_alt:
            partes2.append(f"\n=== PASO {s['id']}: {s['titulo']} ===\nLa diapositiva dice: {s['texto']}")
            for j, im in enumerate(cand[s["id"]], 1):
                partes2 += [f"Alternativa {j} (texto alternativo: «{im.get('alt', '')}»):", coh._jpeg_chico(im["ruta"])]
        try:
            ids2 = {s["id"] for s in con_alt}
            elegidas, _m = _v(partes2, lambda t: coh.lee_veredictos(t, ids2, "elegida"))
        except Exception as exc:                                # noqa: BLE001
            log(f"AVISO: no pude elegir otra imagen del artículo para el carrusel ({str(exc)[:140]}).")
    cambiadas = quitadas = 0
    ya = set()
    for s in mal:
        try:
            k = int(elegidas.get(s["id"]) or 0)
        except (TypeError, ValueError):
            k = 0
        alts = [i for i in cand.get(s["id"]) or [] if i["archivo"] not in ya]
        if 1 <= k <= len(cand.get(s["id"]) or []) and cand[s["id"]][k - 1]["archivo"] not in ya:
            im = cand[s["id"]][k - 1]
            ya.add(im["archivo"])
            with Image.open(im["ruta"]) as src:
                _guarda_jpg(src, carpeta / "capturas" / s["captura"])
            s.update(img_origen=im["archivo"], img_alt=im.get("alt", ""), coherencia="cambiada")
            cambiadas += 1
        else:
            (carpeta / "capturas" / s["captura"]).unlink(missing_ok=True)
            s.update(captura="", imagen_tipo="", img_origen="", img_alt="", coherencia="quitada")
            quitadas += 1
        _ = alts
    log(f"Carrusel desde texto: {len(mal)} imagen(es) no ayudaban: {cambiadas} cambiada(s), {quitadas} quitada(s).")
    return [x for x in (f"{cambiadas} imagen(es) no ayudaban a entender su paso y se cambiaron por otra del artículo." if cambiadas else "",
                        f"{quitadas} imagen(es) del artículo no ayudaban y se sustituyeron por un símbolo (o por solo texto)." if quitadas else "") if x]


# ------------------------------------------------------------------ claridad
_PROMPT_CLARO = """Eres el editor de claridad de un carrusel de Instagram/TikTok hecho de un ARTÍCULO. Lees cada diapositiva como alguien que NO ha leído el artículo y que desliza rápido con el dedo: tiene que entenderla
de un VISTAZO, con muy poco texto. Para cada PASO te doy su título, su frase, la cita del artículo en que se apoya y el contexto de alrededor.
- claro=true si el título y la frase se entienden solos, sin jerga que haya que explicar, y son cortos.
- claro=false si hace falta haber leído el artículo, hay jerga sin explicar, es largo o es vago.
En ese caso (y SOLO en ese caso) reescríbelo: «titulo» de 2 a {mt} palabras y «texto» de UNA frase de {mx} palabras como MUCHO, tuteando, usando SOLO lo que dice el artículo (no inventes nada ni cifras nuevas) y sin copiar una frase larga.
Si es claro, devuelve su título y su frase TAL CUAL. Revisa también la PORTADA: «titulo» de 3 a 8 palabras que se entienda sola.
Responde SOLO con JSON: {{"portada": {{"titulo": "..."}}, "pasos": [{{"id": "p1", "claro": true, "titulo": "...", "texto": "..."}}]}} (todos los pasos, con su «id»)."""


def _lee_claridad(texto: str, ids: set[str]) -> dict:
    from .carrusel import _json_de
    x = _json_de(texto)
    if not isinstance(x, dict) or not isinstance(x.get("pasos"), list):
        raise ValueError("la respuesta no trae los pasos")
    out = {}
    for f in x["pasos"]:
        if isinstance(f, dict) and str(f.get("id")) in ids:
            out[str(f["id"])] = f
    if set(out) != ids:
        raise ValueError("faltan pasos en la respuesta")
    return {"pasos": out, "portada": (x.get("portada") or {}).get("titulo") if isinstance(x.get("portada"), dict) else None}


def revisa_claridad(c: dict, d: dict, cfg, _verifica=None, progreso=None) -> list[str]:
    """🧠 Cada diapositiva tiene que entenderse de un vistazo y con poco texto. Una IA la lee como alguien que no ha leído el artículo; lo que no se entiende (o se pasa de largo) se reescribe con lo que dice
    el artículo, y el CÓDIGO exige las medidas (título ≤ 5 palabras, frase ≤ 14) y que el título siga hablando del artículo. Si la IA falla, se quedan los textos de antes. -> avisos."""
    from . import carrusel_coherencia as coh
    from .carrusel import _habla_del_video, _sin_urls, _txt, _vocabulario
    pasos = c["pasos"]
    if not pasos:
        return []
    vocab = _vocabulario(d["frases"])
    partes = [_PROMPT_CLARO.format(mt=MAX_PALABRAS_TITULO, mx=MAX_PALABRAS_TEXTO)]
    ids = {s["id"] for s in pasos}
    for s in pasos:
        i = int(round(float(s["t"]) / SALTO))
        ctx = " ".join(f["texto"] for f in d["frases"][max(0, i - 1): i + 3])[:420]
        partes.append(f"\n=== PASO {s['id']} ===\nTítulo: {s['titulo']}\nFrase: {s['texto']}\nCita del artículo: «{s.get('cita', '')}»\nContexto: {ctx}")
    partes.append(f"\nPORTADA: {c['portada']['titulo']}")
    _v = _verifica or (lambda p, va: coh.llama_imagenes(cfg, p, va, c.get("jid", "")))
    try:
        if progreso:
            progreso("Revisando que cada diapositiva se entienda de un vistazo…")
        res, _m = _v(["\n".join(map(str, partes))], lambda t: _lee_claridad(t, ids))
    except Exception as exc:                                    # noqa: BLE001
        log(f"AVISO: no pude revisar la claridad del carrusel desde texto ({str(exc)[:140]}).")
        return ["No pude revisar que cada diapositiva se entienda de un vistazo (Gemini no respondió): léelas antes de publicar."]
    cambios = 0
    for s in pasos:
        r = res["pasos"][s["id"]]
        s["claridad"] = "ok"
        if r.get("claro") in (True, "true", "True"):
            continue
        t_new = _txt(_sin_urls(r.get("titulo")), 60).strip(" .:")
        x_new = _txt(_sin_urls(r.get("texto")), 130)
        if not t_new or not x_new:
            continue
        if len(t_new.split()) > MAX_PALABRAS_TITULO + 1 or len(x_new.split()) > MAX_PALABRAS_TEXTO + 2 or not _habla_del_video(t_new, vocab):
            continue                                            # la reescritura no cumple las reglas: se queda lo de antes
        if (t_new, x_new) != (s["titulo"], s["texto"]):
            s.update(titulo_antes=s["titulo"], texto_antes=s["texto"], titulo=t_new, texto=x_new, claridad="reescrita")
            cambios += 1
    nuevo_t = _txt(_sin_urls(res.get("portada")), 80)
    if nuevo_t and 2 <= len(nuevo_t.split()) <= 10 and _habla_del_video(nuevo_t, vocab) and nuevo_t != c["portada"]["titulo"] and not any(ch.isdigit() for ch in nuevo_t):
        c["portada"]["titulo_antes"] = c["portada"]["titulo"]
        c["portada"]["titulo"] = nuevo_t
        cambios += 1
    log(f"Carrusel desde texto: claridad revisada, {cambios} cambio(s).")
    return [f"{cambios} texto(s) se reescribieron más cortos para que se entiendan de un vistazo (los de antes quedan en «✏ Editar»)."] if cambios else []


# ------------------------------------------------------------------ todo junto
def pone_imagenes(c: dict, d: dict, carpeta: Path, cfg, modo: str = "auto", progreso=None, _verifica=None, ia: bool = True) -> list[str]:
    """Pone la imagen de cada paso según el `modo`, la revisa con una IA y revisa la claridad de los textos. -> avisos para el panel.
    `ia=False` (pruebas con una IA de mentira): no se llama a ninguna revisión real."""
    from .carrusel import _contexto
    modo = modo if modo in MODOS else "auto"
    avisos: list[str] = []
    ctx = _contexto(cfg, d)
    (carpeta / "capturas").mkdir(parents=True, exist_ok=True)
    for s in c["pasos"]:
        s.update(captura="", t_captura=None, imagen_tipo="", img_origen="", img_alt="")
    usa_art, usa_sim = modo in ("auto", "articulo"), modo in ("auto", "simbolo")
    if usa_art and not d["imagenes"]:
        avisos.append("Este texto no trae imágenes: las diapositivas llevan un símbolo." if modo == "articulo" else "")
        usa_art, usa_sim = False, True
    if usa_art:
        for sid, im in _empareja(c["pasos"], d["imagenes"], len(d["frases"])).items():
            s = next(x for x in c["pasos"] if x["id"] == sid)
            with Image.open(im["ruta"]) as src:
                _guarda_jpg(src, carpeta / "capturas" / f"cap-{sid}.jpg")
            s.update(captura=f"cap-{sid}.jpg", imagen_tipo="articulo", img_origen=im["archivo"], img_alt=im.get("alt", ""))
        if ia and (_verifica is not None or (cfg is not None and cfg.get_path("carrusel.verifica_coherencia", True))):
            c.setdefault("jid", d["jid"])
            avisos += verifica_imagenes(c, d, carpeta, cfg, _verifica, progreso)
    if usa_sim:
        k = 0
        for s in c["pasos"]:
            if s.get("captura"):
                continue
            icono = s.get("icono") or ""
            im = render_icono(icono, ctx["marca"], "naranja" if k % 2 else "crema") if icono else None
            if im is not None:
                _guarda_jpg(im, carpeta / "capturas" / f"cap-{s['id']}.jpg")
                s.update(captura=f"cap-{s['id']}.jpg", imagen_tipo="simbolo")
                k += 1
    if ia and (_verifica is not None or (cfg is not None and cfg.get_path("carrusel.verifica_claridad", True))):
        avisos += revisa_claridad(c, d, cfg, _verifica, progreso)
    return [a for a in avisos if a]


def cambia_imagen(s: dict, eleccion: str, d: dict, carpeta: Path, marca: dict, k: int = 0) -> str | None:
    """«✏ Editar» en un carrusel de texto: `eleccion` = «» (dejarla como está), «ninguna», «art:img-02.jpg» (una imagen del artículo) o «sim:lupa» (un símbolo). -> un aviso si no se pudo."""
    eleccion = str(eleccion or "").strip()
    if not eleccion or eleccion == "conservar":
        return None
    destino = carpeta / "capturas" / f"cap-{s['id']}.jpg"
    if eleccion == "ninguna":
        destino.unlink(missing_ok=True)
        s.update(captura="", imagen_tipo="", img_origen="", img_alt="")
        return None
    if eleccion.startswith("art:"):
        im = next((i for i in d["imagenes"] if i["archivo"] == eleccion[4:]), None)
        if im is None:
            return f"No encuentro esa imagen del artículo para «{s['titulo']}»: me quedé con la de antes."
        with Image.open(im["ruta"]) as src:
            _guarda_jpg(src, destino)
        s.update(captura=destino.name, imagen_tipo="articulo", img_origen=im["archivo"], img_alt=im.get("alt", ""))
        return None
    if eleccion.startswith("sim:"):
        im = render_icono(eleccion[4:], marca, "naranja" if k % 2 else "crema")
        if im is None:
            return f"No conozco ese símbolo para «{s['titulo']}»: me quedé con la imagen de antes."
        _guarda_jpg(im, destino)
        s.update(captura=destino.name, imagen_tipo="simbolo", icono=eleccion[4:], img_origen="", img_alt="")
    return None


def imagen_cierre(d: dict, marca: dict) -> Image.Image:
    """La imagen del cierre: la imagen principal del artículo si la tiene; si no (o es un texto), la tarjeta con tu logo (si lo subiste)."""
    if d.get("hero"):
        try:
            return Image.open(d["hero"]).convert("RGB")
        except OSError:
            pass
    from . import escena_completa
    from .carrusel import _pal_de
    pal = _pal_de(marca)
    fondo = escena_completa.fondo(1280, 720, pal, "crema").copy().convert("RGB")
    try:
        logo = Image.open(Path(__file__).resolve().parent.parent / "mi_marca" / "logo.webp").convert("RGBA")
        logo.thumbnail((560, 560))
        fondo.paste(logo, ((1280 - logo.width) // 2, (720 - logo.height) // 2), logo)
    except OSError:
        pass
    escena_completa._fondo.cache_clear()
    return fondo


def lista(dir_outbox=None) -> list[dict]:
    """Los carruseles hechos desde un artículo o texto (los más recientes primero)."""
    from . import config
    base = Path(dir_outbox or config.paths()["outbox"])
    out = []
    for dd in base.glob(f"{PREFIJO}*--pro"):
        f = _lee_fuente(dd.name, base)
        if not f:
            continue
        c = dd / "carrusel" / "carrusel.json"
        out.append({"jid": dd.name, "titulo": f.get("titulo", ""), "origen": f.get("origen", "texto"), "hay_carrusel": c.is_file(), "ts": (c if c.is_file() else dd / "fuente.json").stat().st_mtime})
    return sorted(out, key=lambda x: -x["ts"])


def borra(jid: str, dir_outbox=None) -> bool:
    """Borra TODO lo de un carrusel de texto (su fuente, las imágenes copiadas y el carrusel). Solo carpetas `texto-…--pro`; nunca toca tu proyecto de Astro."""
    if not es_texto(jid) or not re.fullmatch(r"[\w.\-]+", jid or ""):
        return False
    out = _dir_out(jid, dir_outbox)
    if not out.is_dir():
        return False
    shutil.rmtree(out, ignore_errors=True)
    return True


# ------------------------------------------------------------------ pruebas (sin red: un proyecto de Astro y una IA de mentira; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile

    from . import carrusel as K
    from . import config as _config
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    real = _config.load()

    class CfgT:                                   # (la config real —marca, web— + un proyecto de Astro de mentira)
        def __init__(self, carpeta):
            self.carpeta = carpeta

        def get_path(self, k, d=None):
            if k == "videos.canales.ejemplo":
                return {**(real.get_path(k) or {}), "sitio": {"carpeta": str(self.carpeta)}}
            return real.get_path(k, d)

    tema = [("sitemap", "Primero revisa el sitemap de tu sitio con calma. Debe llevar solo las páginas que quieres que Google indexe."),
            ("robots", "Después comprueba que el archivo robots no bloquee las páginas importantes. Un solo error ahí puede esconder todo tu sitio."),
            ("canonical", "Usa la etiqueta canonical para decirle a Google cuál es la página original. Así evitas el contenido duplicado entre direcciones parecidas."),
            ("enlaces", "Revisa tus enlaces internos para que lleven a las páginas más valiosas. Cada enlace reparte autoridad dentro de tu sitio."),
            ("velocidad", "Mide la velocidad de carga de tus páginas con una herramienta gratis. Una página lenta pierde visitas antes de mostrar nada."),
            ("movil", "Comprueba que tu sitio se vea bien en el móvil. La mayoría de las búsquedas hoy se hacen desde un teléfono."),
            ("search", "Conecta tu sitio a Search Console para ver qué páginas indexa Google. Ahí aparecen los errores que debes corregir primero."),
            ("schema", "Añade datos estructurados para ayudar a Google a entender tu contenido. Algunos resultados enriquecidos muestran estrellas y precios.")]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        web = t / "web"
        (web / "src" / "content" / "blog" / "google").mkdir(parents=True)
        (web / "src" / "assets" / "uploads").mkdir(parents=True)
        colores = [(20, 110, 200), (200, 60, 40), (30, 150, 70), (200, 170, 20), (110, 40, 150)]
        for k, col in enumerate(colores):
            Image.new("RGB", (900, 500), col).save(web / "src" / "assets" / "uploads" / f"img{k}.jpg", "JPEG")
        Image.new("RGB", (900, 500), (10, 10, 10)).save(web / "src" / "assets" / "uploads" / "hero.jpg", "JPEG")
        cuerpo = ["---", 'title: "Guía de indexación"', 'description: "Cómo conseguir que Google indexe tu sitio."', 'focusKeyword: "indexación"', 'tags: ["seo técnico", "indexación"]',
                  'heroImage: "../../../assets/uploads/hero.jpg"', "---", ""]
        for k, (_, txt) in enumerate(tema):
            cuerpo += [f"## Paso {k + 1}", "", txt, ""]
            if k in (0, 2, 4, 6):
                cuerpo += [f"![captura {tema[k][0]}](../../../assets/uploads/img{k // 2}.jpg)", ""]
        (web / "src" / "content" / "blog" / "google" / "guia-indexacion.md").write_text("\n".join(cuerpo), encoding="utf-8")
        cfg = CfgT(web)
        out_dir = t / "videos"

        # --- la fuente: un artículo de tu web (con imágenes) y un texto pegado
        r = prepara("", "", "google/guia-indexacion", cfg, out_dir)
        jid = r.get("jid", "")
        d = datos(jid, out_dir) if r.get("ok") else {}
        caso("un artículo de tu web se prepara: sus párrafos, sus imágenes COPIADAS al carrusel (con el párrafo al que acompañan) y su imagen principal; el jid es «texto-…--pro»",
             r.get("ok") and es_texto(jid) and len(d["frases"]) >= 14 and len(d["imagenes"]) == 4 and d["hero"] is not None and d["titulo"] == "Guía de indexación"
             and all(i["ruta"].is_file() for i in d["imagenes"]) and "indexación" in d["tags"] and d["texto_mode"] and d["video"] is None, (r, len(d.get("frases", [])), len(d.get("imagenes", []))))
        caso("cada frase lleva un «segundo» de mentira (4 s por frase, en orden) y cada imagen la frase más cercana a donde estaba en el artículo",
             [f["t"] for f in d["frases"]] == [k * SALTO for k in range(len(d["frases"]))] and [i["frase"] for i in d["imagenes"]] == sorted(i["frase"] for i in d["imagenes"])
             and d["duracion"] > d["frases"][-1]["t"], [i["frase"] for i in d["imagenes"]])
        caso("la misma fuente da el mismo jid (volver a crear el carrusel de un artículo lo sustituye, no lo duplica)", prepara("", "", "google/guia-indexacion", cfg, out_dir)["jid"] == jid)
        malo = prepara("", "", "google/no-existe", cfg, out_dir)
        corto = prepara("Un texto demasiado corto para un carrusel.", "", "", cfg, out_dir)
        caso("un artículo que no existe, o un texto demasiado corto, se rechaza con un mensaje claro (sin crear carpetas)", not malo["ok"] and not corto["ok"] and "100 palabras" in corto["error"]
             and not [p for p in out_dir.glob("texto-*--pro") if p.name != jid], (malo, corto))
        pegado = " ".join(x for _, x in tema)
        rp = prepara(pegado.replace(". ", ".\n\n"), "Mi borrador de SEO", "", cfg, out_dir)
        dp = datos(rp["jid"], out_dir) if rp.get("ok") else {}
        caso("un texto pegado se parte en párrafos y frases, sin imágenes ni etiquetas, con su título",
             rp.get("ok") and dp["titulo"] == "Mi borrador de SEO" and not dp["imagenes"] and dp["hero"] is None and len(dp["frases"]) >= 14 and dp["origen"] == "texto", rp)
        cin = prepara("Mira https://x.com/a y ![img](http://x.com/i.png) [enlace](http://y.com) **negrita**.\n\n" + pegado.replace(". ", ".\n\n"), "", "", cfg, out_dir)
        textos = " ".join(f["texto"] for f in datos(cin["jid"], out_dir)["frases"]) if cin.get("ok") else "http"
        caso("al pegar un texto se limpian las URLs, las imágenes y el formato Markdown (la IA solo ve texto)", "http" not in textos and "![" not in textos and "**" not in textos and "negrita" in textos, textos[:120])

        # --- la petición a la IA
        pr = arma_prompt(d, "Mi canal", "Conseguir clientes", 8)
        caso("el prompt numera cada frase [P1]…, pide el símbolo de la biblioteca, títulos de ≤ 5 palabras y frases de ≤ 14, solo lo que dice el artículo, y no habla de YouTube",
             "[P1]" in pr and f"[P{len(d['frases'])}]" in pr and '"lupa"' in pr and f"2 a {MAX_PALABRAS_TITULO}" in pr and f"{MAX_PALABRAS_TEXTO} palabras" in pr and "YouTube" not in pr
             and "COPIADAS" in pr and "web" in pr, pr[:200])

        def frase_de(palabra):
            return next(k for k, f in enumerate(d["frases"]) if palabra in f["texto"].lower())

        def paso_de(palabra, titulo, icono, texto):
            k = frase_de(palabra)
            return {"titulo": titulo, "texto": texto, "minuto": f"P{k + 1}", "cita": " ".join(d["frases"][k]["texto"].split()[:7]), "icono": icono}
        pasos_ia = [paso_de("sitemap", "Revisa el sitemap", "lupa", "Que solo lleve las páginas que quieres indexar."),
                    paso_de("robots", "Cuida el archivo robots", "candado", "Un error ahí puede esconder todo tu sitio."),
                    paso_de("canonical", "Usa la etiqueta canonical", "inventado", "Dile a Google cuál es la página original."),
                    paso_de("velocidad", "Mide la velocidad de carga", "cohete", "Una página lenta pierde visitas antes de mostrar nada.")]
        resp = {"tipo": "pasos", "portada": {"titulo": "Indexa tu sitio en 9 pasos", "subtitulo": "Para quien no sale en Google"}, "pasos": pasos_ia,
                "cierre": {"titulo": "¿Te sirvió? Guárdalo", "texto": "El artículo completo está en mi web."}, "pie_instagram": "Indexa tu sitio\nUna línea por paso\nGuárdalo https://x.com",
                "tiktok_titulo": "Indexa tu sitio", "tiktok_texto": "Mira esto"}
        pedidos: list = []

        def llama(pr_, va):
            pedidos.append(pr_)
            return va(json.dumps(resp)), "gemini-fake-lite"

        # --- el parser (cita comprobada contra el artículo, «P12», símbolo válido)
        c0 = K.parsea(json.dumps(resp), d)
        inv = {**pasos_ia[0], "titulo": "Configura el píxel de Facebook", "cita": "instala el pixel de facebook en tu tienda online ya", "minuto": "P3"}
        c1 = K.parsea(json.dumps({**resp, "pasos": pasos_ia + [inv]}), d)
        caso("el parser usa el «P12» como segundo de mentira, conserva el símbolo SOLO si existe en la biblioteca («inventado» → ninguno) y descarta el paso cuya cita no está en el artículo",
             [s["t"] for s in c0["pasos"]] == [d["frases"][frase_de(w)]["t"] for w in ("sitemap", "robots", "canonical", "velocidad")] and [s["icono"] for s in c0["pasos"]] == ["lupa", "candado", "", "cohete"]
             and c1["descartados"] == 1 and len(c1["pasos"]) == 4 and c0["portada"]["titulo"] == "Indexa tu sitio en 4 pasos", [(s["t"], s["icono"]) for s in c0["pasos"]])

        # --- una IA de mentira para las revisiones (imágenes y claridad)
        llamadas: list = []
        guion = {"rechaza": 2}                    # cuántos pasos con imagen del artículo rechaza el revisor

        def verifica(partes, valida):
            primero = str(partes[0])
            llamadas.append(primero[:30])
            ids = re.findall(r"=== PASO (p\d+)", " ".join(str(x) for x in partes if isinstance(x, str)))
            if primero.startswith("Eres el revisor de un carrusel de Instagram/TikTok hecho a partir de un ARTÍCULO"):
                mal = ids[:guion["rechaza"]]
                return valida(json.dumps({"pasos": [{"id": i, "coincide": i not in mal, "ve": "x"} for i in ids]})), "fake"
            if primero.startswith("Estos pasos de un carrusel tienen una imagen"):
                return valida(json.dumps({"pasos": [{"id": i, "elegida": 1 if n == 0 else 0} for n, i in enumerate(ids)]})), "fake"
            # claridad: el primer paso se reescribe bien; el segundo con una reescritura que rompe las reglas (muy larga y de otro tema)
            filas_ = []
            for n, i in enumerate(ids):
                if n == 0:
                    filas_.append({"id": i, "claro": False, "titulo": "Sitemap al día", "texto": "Solo con las páginas que quieres indexar."})
                elif n == 1:                      # (de lo que dice el artículo, pero demasiado larga)
                    filas_.append({"id": i, "claro": False, "titulo": "Comprueba con mucha calma que el archivo robots no bloquee nada importante", "texto": "Revisa el archivo robots."})
                elif n == 2:                      # (corta, pero de otro tema: no se dice en el artículo)
                    filas_.append({"id": i, "claro": False, "titulo": "Instala el píxel", "texto": "Esto no se dice en el artículo."})
                else:
                    filas_.append({"id": i, "claro": True, "titulo": "x", "texto": "y"})
            return valida(json.dumps({"portada": {"titulo": "Indexa tu sitio sin sustos"}, "pasos": filas_})), "fake"

        # --- de punta a punta, modo automático, con las dos revisiones
        r1 = K.crea(jid, cfg, out_dir, out_dir, llama, dir_caras=t / "sin_caras", _verifica=verifica)
        c = K.lee(jid, out_dir) or {}
        carp = K.ruta(jid, out_dir)
        arch = c.get("archivos") or {}
        medidas = {k: {Image.open(carp / k / n).size for n in arch.get(k, [])} for k in ("ig", "tiktok")}
        caso("de punta a punta (modo automático): UNA petición de carrusel + las revisiones, 6 diapositivas por formato a 1080×1350 y 1080×1920, PDF y el carrusel queda marcado como de texto",
             r1.get("ok") and len(pedidos) == 1 and len(arch.get("ig", [])) == 6 and len(arch.get("tiktok", [])) == 6 and medidas["ig"] == {(1080, 1350)} and medidas["tiktok"] == {(1080, 1920)}
             and (carp / "carrusel.pdf").is_file() and c.get("texto_mode") and c.get("modo_imagen") == "auto", r1.get("error") or (medidas, arch))
        caso("la IA mira las imágenes en 1-2 peticiones y los textos en 1 (3 como mucho en total), nunca una por paso", 2 <= len(llamadas) <= 3, llamadas)
        tipos = [s.get("imagen_tipo") for s in c["pasos"]]
        caso("TODO paso con símbolo válido o imagen del artículo acaba con imagen, y las imágenes existen en disco",
             all(s.get("captura") for s in c["pasos"] if s.get("icono") or s.get("imagen_tipo") == "articulo") and set(tipos) <= {"articulo", "simbolo", ""}
             and all((carp / "capturas" / s["captura"]).is_file() for s in c["pasos"] if s.get("captura")), tipos)
        rech = [s for s in c["pasos"] if s.get("coherencia") in ("cambiada", "quitada")]
        caso("las imágenes que la IA dice que NO ayudan a entender su paso se cambian por otra del artículo o se quitan (y caen al símbolo): se avisa de ello",
             len(rech) >= 1 and any("no ayudaban" in a for a in c.get("avisos", [])), (rech, c.get("avisos")))
        arts = [s["img_origen"] for s in c["pasos"] if s.get("imagen_tipo") == "articulo"]
        caso("las imágenes del artículo son SOLO las del propio artículo (cada `img_origen` existe en su fuente) y ninguna se repite en dos pasos",
             all(a in {i["archivo"] for i in d["imagenes"]} for a in arts) and len(arts) == len(set(arts)), arts)
        p1 = c["pasos"][0]
        caso("la revisión de CLARIDAD reescribe un texto que no se entiende (conservando el de antes) y rechaza la reescritura que rompe las reglas (una muy larga y otra de un tema que el artículo no toca)",
             p1["titulo"] == "Sitemap al día" and p1.get("titulo_antes") == "Revisa el sitemap" and p1.get("claridad") == "reescrita" and c["pasos"][1]["titulo"] == "Cuida el archivo robots" and c["pasos"][2]["titulo"] == "Usa la etiqueta canonical"
             and c["pasos"][1].get("claridad") == "ok" and c["pasos"][2].get("claridad") == "ok" and c["portada"]["titulo"] == "Indexa tu sitio sin sustos" and any("más cortos" in a for a in c["avisos"]), (p1, c["pasos"][1]["titulo"]))
        caso("el texto de Instagram no lleva URLs (la IA nunca las escribe)", "http" not in texto_instagram_(c), texto_instagram_(c)[:100])

        # --- cierre y modos
        marca = dict(cfg.get_path("videos.marca") or {})
        cie = K.dibuja_cierre(c, K.FORMATOS["ig"], marca, Image.new("RGB", (800, 450), (90, 90, 90)))
        yt = K.dibuja_cierre({**c, "texto_mode": False}, K.FORMATOS["ig"], marca, Image.new("RGB", (800, 450), (90, 90, 90)))
        caso("el cierre de un artículo dice «ARTÍCULO COMPLETO EN MI WEB» y no lleva el logo de YouTube (el del vídeo, sí): se dibujan distinto", _difiere(cie.final(), yt.final()))
        for modo in ("simbolo", "texto", "articulo"):
            rr = K.crea(jid, cfg, out_dir, out_dir, llama, dir_caras=t / "sin_caras", modo_imagen=modo)
            cc = K.lee(jid, out_dir) or {}
            ts_ = [s.get("imagen_tipo") for s in cc.get("pasos", [])]
            if modo == "simbolo":
                ok_ = rr.get("ok") and set(ts_) <= {"simbolo", ""} and all(s.get("imagen_tipo") == "simbolo" for s in cc["pasos"] if s.get("icono"))
            elif modo == "texto":
                ok_ = rr.get("ok") and set(ts_) == {""} and not list((carp / "capturas").glob("*.jpg"))
            else:
                ok_ = rr.get("ok") and "articulo" in ts_ and set(ts_) <= {"articulo", "simbolo", ""}
            caso(f"modo «{modo}»: " + {"simbolo": "solo símbolos fijos de la biblioteca", "texto": "ninguna imagen (solo texto)", "articulo": "imágenes del artículo (a las que no tienen, símbolo)"}[modo], ok_, ts_)
        n_ll = len(llamadas)
        caso("sin la IA de revisión inyectada, crear con una IA de mentira NO llama a las revisiones reales (las pruebas no gastan nada): las 3 de antes siguen siendo las mismas", n_ll <= 3, llamadas)

        # --- «✏ Editar»: la imagen de cada paso se ELIGE (sin segundos, sin IA)
        K.crea(jid, cfg, out_dir, out_dir, llama, dir_caras=t / "sin_caras", modo_imagen="auto", _verifica=verifica)
        c = K.lee(jid, out_dir)
        ids = [s["id"] for s in c["pasos"]]
        n_ped = len(pedidos)
        cambios = {"pasos": [{"id": ids[0], "titulo": "Sitemap", "texto": "Texto nuevo.", "imagen": "art:img-02.jpg"}, {"id": ids[1], "titulo": "Robots", "texto": "Otro.", "imagen": "sim:lupa"},
                             {"id": ids[2], "titulo": "Canonical", "texto": "Otro más.", "imagen": "ninguna"}, {"id": ids[3], "titulo": "Velocidad", "texto": "Y otro.", "imagen": "conservar"}]}
        antes3 = c["pasos"][3].get("img_origen"), c["pasos"][3].get("imagen_tipo")
        rh = K.rehaz(jid, cambios, cfg, out_dir, out_dir, dir_caras=t / "sin_caras")
        c2 = K.lee(jid, out_dir) or {}
        p = c2.get("pasos", [{}] * 4)
        caso("«✏ Editar» en un texto: elige una imagen del artículo, un símbolo, ninguna o deja la de antes, sin segundos y SIN llamar a la IA",
             rh.get("ok") and len(pedidos) == n_ped and p[0].get("imagen_tipo") == "articulo" and p[0].get("img_origen") == "img-02.jpg" and p[1].get("imagen_tipo") == "simbolo" and p[1].get("icono") == "lupa"
             and p[2].get("captura") == "" and p[2].get("imagen_tipo") == "" and (p[3].get("img_origen"), p[3].get("imagen_tipo")) == antes3 and len(c2["archivos"]["ig"]) == 6, rh.get("error") or p)
        rh2 = K.rehaz(jid, {"pasos": [{"id": ids[0], "titulo": "a b", "texto": "c d", "imagen": "art:img-99.jpg"}, {"id": ids[1], "titulo": "a b", "texto": "c d", "imagen": "sim:no-existe"}]},
                      cfg, out_dir, out_dir, dir_caras=t / "sin_caras")
        c3 = K.lee(jid, out_dir) or {}
        caso("una imagen o símbolo que no existe en ese carrusel no rompe nada: el paso se queda con la de antes y se avisa",
             rh2.get("ok") and len(c3["avisos"]) >= 2 and c3["pasos"][0]["img_origen"] == "img-02.jpg" and c3["pasos"][1]["icono"] == "lupa", c3.get("avisos"))
        caso("la vista previa de fotogramas no existe en un carrusel de texto (no hay vídeo)", K.captura_previa(jid, 5.0, out_dir, out_dir) is None)

        # --- si la IA de revisión falla, el carrusel sale igual (con aviso) y con los textos de antes
        def roto(partes, valida):
            raise RuntimeError("Gemini saturado")
        K.crea(jid, cfg, out_dir, out_dir, llama, dir_caras=t / "sin_caras", _verifica=roto)
        cr = K.lee(jid, out_dir) or {}
        caso("si Gemini no responde en las revisiones, el carrusel sale igual con sus textos e imágenes y un aviso (nunca se cae)",
             cr.get("pasos") and cr["pasos"][0]["titulo"] == "Revisa el sitemap" and len(cr["archivos"]["ig"]) == 6 and sum("No pude" in a for a in cr["avisos"]) >= 1, cr.get("avisos"))

        # --- borrar: solo lo suyo
        (out_dir / "2026-10-09-otro--pro").mkdir()
        (out_dir / "2026-10-09-otro--pro" / "pro.mp4").write_bytes(b"x" * 10)
        caso("borrar quita TODO el carrusel de texto (fuente, imágenes copiadas, diapositivas) y se niega con un nombre que no es de texto o con rutas raras; tu proyecto de Astro no se toca",
             borra(jid, out_dir) and not (out_dir / jid).exists() and not borra("2026-10-09-otro--pro", out_dir) and not borra("texto-../../x--pro", out_dir) and not borra("texto-zz--pro", out_dir) and (out_dir / "2026-10-09-otro--pro" / "pro.mp4").is_file()
             and (web / "src" / "content" / "blog" / "google" / "guia-indexacion.md").is_file() and (web / "src" / "assets" / "uploads" / "img0.jpg").is_file(), list(out_dir.iterdir()))
        caso("la lista de carruseles de texto enseña los que quedan", lista(out_dir) and all(es_texto(x["jid"]) for x in lista(out_dir)), lista(out_dir))
    return filas


def _difiere(a: Image.Image, b: Image.Image) -> bool:
    from PIL import ImageChops
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


def texto_instagram_(c: dict) -> str:
    from .carrusel import texto_instagram
    return texto_instagram(c)
