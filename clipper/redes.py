"""📣 PUBLICACIONES PARA TUS REDES a partir de un vídeo ya editado (el autor, 2026-09-27: «que haya una opción que diga redes
sociales… que me dé una publicación basada en cómo yo hablo, en toda la transcripción, con puntos clave, super sharp, pero
para cada una de las redes sociales, con cada uno de los estilos… centralizar todo en mi YouTube»; paso 1: «que yo le dé
copiar y lo pego en la red social… como tengo los títulos A/B y la descripción»).

- UNA petición a Flash-Lite (500 al día: no toca las 20 de Flash) escribe TODAS a la vez, cada una con el estilo de su red:
  YouTube · Publicaciones (la de tu propio canal, SIEMPRE y la primera: «🎬 Nuevo vídeo hablando de …» + un resumen de una
  frase + el enlace; la API de YouTube NO deja crear publicaciones de comunidad —`activities.insert` se eliminó en 2020—,
  así que se copia y se pega en YouTube Studio, programada a la hora en que sale el vídeo), LinkedIn (post + primer
  comentario con el enlace), Bluesky (1-3 posts de ≤ 300 caracteres), Reddit (título + texto que aporta por sí solo),
  Discord (aviso a tu comunidad) e Instagram/TikTok SOLO si el vídeo tiene Shorts (el texto de cada Reel).
- Todo lleva a TU vídeo de YouTube. El enlace va como «{ENLACE}» y el panel lo pone al copiar con el del vídeo publicado
  (`historial`): si generas las publicaciones antes de subirlo, no hay que rehacerlas.
- En tu voz: la IA lee la transcripción entera (cómo hablas de verdad), tutea, sin tus muletillas ni frases de IA genéricas.
- Nada se publica solo: el panel enseña cada texto con su «copiar» (automatizar Discord/Bluesky… es el paso 2, más adelante).
Se guarda en `videos/<vídeo>/redes.json`.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .util import log

VERSION = 1
ENLACE = "{ENLACE}"
LARGO_ENLACE = len("https://youtu.be/XXXXXXXXXXX")     # lo que ocupa el enlace de verdad (Bluesky lo cuenta entero)
_YT_ENTRADA = "🎬 Nuevo vídeo hablando de"     # lo pone el CÓDIGO (la IA solo da el tema y el resumen)
REDES = {             # nombre, tope de caracteres y cómo escribe cada una (lo que ve la IA)
    "youtube": ("YouTube · Publicaciones", 500,
                "La publicación de la pestaña «Publicaciones» de TU canal, para quien ya te sigue. NO escribes el texto "
                "entero: das dos campos y el programa arma «🎬 Nuevo vídeo hablando de <tema>», después el resumen y el "
                "enlace. «tema»: de qué trata el vídeo, en 4-10 palabras, escrito para seguir a «hablando de» (minúscula "
                "salvo nombres propios y siglas; SIN empezar por «de» ni «sobre»). «resumen»: UNA sola frase, de 150 "
                "caracteres como mucho, con lo más útil que se lleva quien lo vea (su dato o ejemplo concreto, no una "
                "promesa vaga) y que no repita el tema. Sin hashtags, sin emojis y sin enlace."),
    "linkedin": ("LinkedIn", 3000,
                 "Profesional pero en tu voz. Las 2 PRIMERAS líneas son el gancho (es lo único que se ve antes de «ver más»). "
                 "Párrafos de 1-2 frases, 3-5 ideas clave (con «→» o números), enfocadas a lo que le importa a un dueño de "
                 "negocio o a alguien de marketing (resultados, clientes, tiempo, dinero). Termina con UNA pregunta que invite a "
                 "comentar. 800-1300 caracteres. Sin enlace en el post: el enlace va en el primer comentario (así LinkedIn no le "
                 "quita alcance). Como mucho 3 hashtags al final."),
    "bluesky": ("Bluesky", 300,
                "Cercano y directo. 1 a 3 posts (un hilo), CADA UNO de 300 caracteres como mucho CONTANDO EL ENLACE (≈28). El "
                "primero engancha solo; el enlace, en el último. Sin hashtags (o 1)."),
    "reddit": ("Reddit", 40000,
               "Reddit castiga la autopromoción: el texto tiene que APORTAR por sí solo (lo más útil del vídeo, en puntos), sin "
               "venderse; el vídeo, al final, como «si lo prefieres en vídeo». Título claro y sin clickbait (máx. 120 caracteres). "
               "Formato Markdown sencillo."),
    "discord": ("Discord", 2000,
                "Aviso para TU comunidad del servidor: cercano, 3-5 líneas, qué van a aprender, el enlace y una pregunta para "
                "que comenten. Sin @everyone."),
    "instagram": ("Instagram", 2200,
                  "El texto del Reel (el Short): gancho en la 1.ª línea, 2-3 líneas de valor, «el vídeo completo, en el enlace de "
                  "mi perfil» (en Instagram los enlaces no funcionan) y 5-10 hashtags al final."),
    "tiktok": ("TikTok", 2200,
               "El texto del Short: 1-2 frases con gancho + 3-6 hashtags. Corto."),
}
_GENERICAS = ("en el mundo digital", "en la era de", "descubre cómo", "no te lo pierdas", "¡no te lo pierdas", "sumérgete",
              "en el vertiginoso", "hoy en día más que nunca", "revoluciona", "desbloquea")


# ------------------------------------------------------------------ datos del vídeo
def _datos(jid: str, dir_outbox=None, dir_work=None) -> dict:
    from . import articulo
    d = articulo.datos_del_video(jid, dir_outbox, dir_work)
    man = json.loads((d["out_dir"] / "manifest.json").read_text(encoding="utf-8"))
    e = man[0] if isinstance(man, list) else man
    d["descripcion"] = str(e.get("descripcion") or "")
    d["shorts"] = [{"titulo": str(s.get("titulo") or ""), "idea": str(s.get("idea") or ""), "archivo": s.get("archivo")}
                   for s in e.get("shorts") or [] if isinstance(s, dict) and s.get("titulo")]
    d["muletillas"] = []
    try:                                                       # SUS muletillas de ESTE vídeo (las que más repite), para quitarlas
        from . import config, muletillas
        words = json.loads((Path(dir_work or config.paths()["work"]) / f"{d['base']}--pro" / "words.json").read_text(encoding="utf-8"))
        d["muletillas"] = [str(m["nombre"]) for m in muletillas.analiza(words)["top"][:8]]
    except Exception:                                          # noqa: BLE001 — sin eso, la lista genérica del prompt
        pass
    return d


# ------------------------------------------------------------------ la petición
_SYSTEM = """Eres el community manager de «{canal}»: escribes en la voz del autor, adaptando cada publicación a su red.
Respondes SOLO con JSON válido, sin markdown."""


def arma_prompt(d: dict, redes: list[str], canal: str, objetivo: str) -> str:
    L = [f"El autor de «{canal}» acaba de sacar este vídeo de YouTube y quiere llevar gente de sus otras redes al vídeo "
         "(TODO se centraliza en su YouTube). Escribe una publicación para CADA red de la lista, cada una con el estilo de "
         "su red.", "",
         f"Objetivo del negocio (tenlo en cuenta, sin vender en cada frase): {objetivo}", "",
         "REGLAS PARA TODAS:",
         "- En SU voz: tutea, con sus expresiones y su manera de explicar (lee cómo habla en la transcripción), pero sin sus "
         "muletillas de relleno (" + ", ".join(d.get("muletillas") or ["bueno", "o sea", "digamos"]) + ").",
         "- Super concretas: las ideas clave DE ESTE vídeo, con sus datos y ejemplos. Nada de frases genéricas de IA («en el "
         "mundo digital actual», «descubre cómo», «no te lo pierdas»…). Pocos emojis (0-2 por publicación).",
         "- Solo lo que dice el vídeo: no inventes datos, cifras ni resultados. Una cifra que él da como estimación va como "
         "aproximada. La transcripción es automática y a veces escribe mal un nombre: usa cómo sale en el título, los "
         "capítulos o las etiquetas.",
         f"- El enlace al vídeo se escribe EXACTAMENTE así: {ENLACE} (el programa pone el de verdad). No escribas otras URLs.",
         "", "CADA RED:"]
    for r in redes:
        nombre, _tope, estilo = REDES[r]
        L.append(f"- {nombre} (\"{r}\"): {estilo}")
    if "instagram" in redes or "tiktok" in redes:
        L += ["", "SUS SHORTS (Instagram y TikTok llevan UN texto por Short, en este orden):"]
        L += [f"{k}. «{s['titulo']}» — {s['idea']}" for k, s in enumerate(d["shorts"], 1)]
    L += ["", f"DATOS DEL VÍDEO: «{d['titulo']}» · etiquetas: {', '.join(d['tags']) or '—'}", "Capítulos:",
          d["capitulos"] or "(sin capítulos)", "", "TRANSCRIPCIÓN (el vídeo ya editado):"]
    L += [f["texto"] for f in d["frases"]]
    forma = {"youtube": '"youtube": {"tema": "...", "resumen": "..."}',
             "linkedin": '"linkedin": {"texto": "...", "comentario": "primer comentario con {ENLACE}"}',
             "bluesky": '"bluesky": {"posts": ["...", "..."]}', "reddit": '"reddit": {"titulo": "...", "texto": "..."}',
             "discord": '"discord": {"texto": "..."}', "instagram": '"instagram": {"textos": ["uno por Short"]}',
             "tiktok": '"tiktok": {"textos": ["uno por Short"]}'}
    L += ["", "Responde SOLO con JSON: {" + ", ".join(forma[r] for r in redes) + "}"]
    return "\n".join(L)


def _json_de(texto: str):
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1] if i >= 0 and j > i else t)


def _limpia(s, tope: int) -> str:
    s = re.sub(r"[ \t]+", " ", str(s or "")).strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"https?://\S+", ENLACE, s) if ENLACE in s or "http" in s else s     # una URL que se colara -> el enlace de verdad
    return s[:tope].strip()


def _una_linea(s) -> str:
    """Un texto en UNA línea y sin el enlace ni URLs (el enlace lo pone el código, una vez, al final)."""
    t = re.sub(r"https?://\S+", " ", str(s or "").replace(ENLACE, " "))
    return re.sub(r"\s+", " ", t).strip()


def _recorta(s: str, n: int) -> str:
    """Como mucho `n` caracteres, cortando en una palabra ENTERA y con «…» (nunca una palabra a medias)."""
    if len(s) <= n:
        return s
    corte = s[: n - 1]
    if " " in corte:
        corte = corte[: corte.rfind(" ")]
    return corte.rstrip(" ,;:-–—.") + "…"


def largo_real(s: str) -> int:
    """Lo que ocupará el texto con el enlace de verdad puesto (Bluesky cuenta el enlace entero)."""
    return len(s.replace(ENLACE, "x" * LARGO_ENLACE))


def parte_bluesky(posts: list[str], tope: int = 300) -> list[str]:
    """Posts de Bluesky que pasan del tope -> partidos por frases (y si no, por palabras) sin pasarse NUNCA, con el enlace
    en el último."""
    out: list[str] = []
    for p in posts:
        if largo_real(p) <= tope:
            out.append(p)
            continue
        actual = ""
        for trozo in re.split(r"(?<=[.!?…])\s+|\n+", p):
            for palabra in ([trozo] if largo_real(trozo) <= tope else trozo.split(" ")):
                prueba = f"{actual} {palabra}".strip() if actual else palabra
                if largo_real(prueba) <= tope:
                    actual = prueba
                else:
                    if actual:
                        out.append(actual)
                    actual = palabra if largo_real(palabra) <= tope else palabra[:tope]
        if actual:
            out.append(actual)
    if out and not any(ENLACE in p for p in out):                  # el enlace, siempre (en el último)
        if largo_real(out[-1] + " " + ENLACE) <= tope:
            out[-1] = out[-1] + " " + ENLACE
        else:
            out.append(f"El vídeo completo: {ENLACE}")
    return out[:5]


def parsea(texto: str, redes: list[str], n_shorts: int) -> dict:
    """El JSON de la IA -> las publicaciones limpias, dentro del tope de cada red y con el enlace donde toca. Lanza si
    falta alguna red (así se reintenta)."""
    x = _json_de(texto)
    if not isinstance(x, dict):
        raise ValueError("la respuesta no es un objeto")
    out: dict = {}
    for r in redes:
        v = x.get(r) if isinstance(x.get(r), dict) else {}
        tope = REDES[r][1]
        if r == "youtube":                                     # «🎬 Nuevo vídeo hablando de <tema>» / <resumen> / <enlace>
            tema = _una_linea(v.get("tema"))
            tema = re.sub(r"^(?:nuevo\s+v[ií]deo\s*:?\s*)?(?:hablando\s+)?(?:(?:de|sobre|acerca\s+de)\s+)+", "", tema,
                          flags=re.I).strip(" .:,;-–—")
            resumen = _una_linea(v.get("resumen"))
            if not tema or not resumen:
                raise ValueError("falta YouTube")
            out[r] = {"texto": f"{_YT_ENTRADA} {_recorta(tema, 90)}\n{_recorta(resumen, 190)}\n{ENLACE}"}
        elif r == "linkedin":
            t, c = _limpia(v.get("texto"), tope), _limpia(v.get("comentario"), 1200)
            if not t:
                raise ValueError("falta LinkedIn")
            t = t.replace(ENLACE, "").strip()                  # el enlace, en el comentario (no en el post)
            out[r] = {"texto": t, "comentario": c if ENLACE in c else (f"{c} {ENLACE}".strip() if c else f"El vídeo: {ENLACE}")}
        elif r == "bluesky":
            posts = [_limpia(p, 2000) for p in v.get("posts") or [] if _limpia(p, 2000)]
            if not posts:
                raise ValueError("falta Bluesky")
            out[r] = {"posts": parte_bluesky(posts)}
        elif r == "reddit":
            titulo, t = _limpia(v.get("titulo"), 300), _limpia(v.get("texto"), tope)
            if not titulo or not t:
                raise ValueError("falta Reddit")
            out[r] = {"titulo": titulo.replace(ENLACE, "").strip(), "texto": t if ENLACE in t else f"{t}\n\n{ENLACE}"}
        elif r == "discord":
            t = _limpia(v.get("texto"), tope)
            if not t:
                raise ValueError("falta Discord")
            out[r] = {"texto": t if ENLACE in t else f"{t}\n{ENLACE}"}
        else:                                                  # instagram / tiktok: uno por Short
            textos = [_limpia(s, tope).replace(ENLACE, "").strip() for s in v.get("textos") or []]
            textos = [s for s in textos if s][:n_shorts]
            if not textos:
                raise ValueError(f"falta {REDES[r][0]}")
            out[r] = {"textos": textos}
    return out


def _avisos(pub: dict) -> list[str]:
    av = []
    for r, v in pub.items():
        textos = [v.get("texto", ""), v.get("comentario", ""), v.get("titulo", "")] + list(v.get("posts") or []) + list(v.get("textos") or [])
        if any(g in t.lower() for t in textos for g in _GENERICAS):
            av.append(f"{REDES[r][0]}: tiene alguna frase de relleno típica de IA; revísala antes de publicar.")
        if r == "linkedin" and len(v.get("texto", "")) > 1800:
            av.append("LinkedIn: es largo (más de 1.800 caracteres); se lee mejor más corto.")
    return av


# ------------------------------------------------------------------ crear / leer
def ruta(jid: str, dir_outbox=None) -> Path:
    from . import config
    return Path(dir_outbox or config.paths()["outbox"]) / jid / "redes.json"


def lee(jid: str, dir_outbox=None) -> dict | None:
    try:
        d = json.loads(ruta(jid, dir_outbox).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def redes_activas(enlaces: dict, n_shorts: int) -> list[str]:
    """Las redes para las que se escribe: la de YouTube SIEMPRE y la primera (es tu propio canal: el vídeo ya está ahí, no
    depende de `enlaces`), después las que tienes en `enlaces` del canal (LinkedIn incluido aunque no vaya en la
    descripción) e Instagram/TikTok solo si el vídeo tiene Shorts (el autor: «si no hay short, no se publica en Instagram»)."""
    out = ["youtube"]
    for r in REDES:
        if r == "youtube" or (r in ("instagram", "tiktok") and not n_shorts):
            continue
        if enlaces.get(r):
            out.append(r)
    return out


def _llama_gemini(cfg, prompt: str, canal: str, valida):
    from . import modelos as _m
    from . import util as _util
    from .director import _gemini as _abre
    g = _abre(cfg)
    if not g:
        raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso».")
    client, types = g[:2]
    res, modelo, _uso = _util.call_gemini_cadena(client, types, _m.cadena_lite(cfg, client), prompt,
                                                 system=_SYSTEM.format(canal=canal), valida=valida, reintento_s=60.0,
                                                 pasadas=1, timeout_s=150.0, etiqueta="redes")
    return res, modelo


def crea(jid: str, cfg, dir_outbox=None, dir_work=None, _llama=None) -> dict:
    """Las publicaciones de un vídeo terminado: UNA petición a Flash-Lite para todas las redes. Nunca lanza.
    -> {"ok": True, "redes": {...}} o {"ok": False, "error": qué pasó y qué hacer}. `_llama`: pruebas (sin red)."""
    from .util import nombre_modelo
    try:
        d = _datos(jid, dir_outbox, dir_work)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": f"No encuentro todo lo de este vídeo en el PC ({str(exc)[:160]})."}
    if len(d["frases"]) < 5:
        return {"ok": False, "error": "Este vídeo casi no tiene voz transcrita: no da para publicaciones."}
    info = (cfg.get_path(f"videos.canales.{d['canal']}") or {}) if cfg is not None and d["canal"] else {}
    enlaces = dict(info.get("enlaces") or {})
    redes = redes_activas(enlaces, len(d["shorts"]))
    canal = str(info.get("nombre") or d["canal"] or "tu canal")
    objetivo = str((cfg.get_path("ideas.objetivo") if cfg is not None else "") or
                   "Conseguir clientes para sus servicios de SEO; crecer la audiencia es el medio.")
    prompt = arma_prompt(d, redes, canal, objetivo)
    try:
        from . import uso as _uso
        _uso.contexto(job=jid)
        try:
            llama = _llama or (lambda pr, va: _llama_gemini(cfg, pr, canal, va))
            pub, modelo = llama(prompt, lambda t: parsea(t, redes, len(d["shorts"])))
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Redes de {jid}: {exc}")
        from .ideas import _error_claro
        return {"ok": False, "error": _error_claro(exc).replace("las ideas", "las publicaciones")}
    res = {"version": VERSION, "jid": jid, "ts": time.time(), "modelo": modelo, "nombre_modelo": nombre_modelo(modelo),
           "redes": pub, "orden": redes, "shorts": [s["titulo"] for s in d["shorts"]], "avisos": _avisos(pub)}
    ruta(jid, dir_outbox).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Redes de {jid}: {len(pub)} red(es) ({nombre_modelo(modelo)}).")
    return {"ok": True, "redes": res}


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    enl = {"linkedin": "l", "bluesky": "b", "reddit": "r", "discord": "d", "instagram": "i", "tiktok": "t", "spotify": "s"}
    caso("redes: la de YouTube SIEMPRE y la primera (aunque el canal no tenga ninguna red), luego las del canal (LinkedIn "
         "también) e Instagram/TikTok SOLO si el vídeo tiene Shorts",
         redes_activas(enl, 0) == ["youtube", "linkedin", "bluesky", "reddit", "discord"]
         and redes_activas(enl, 2)[-2:] == ["instagram", "tiktok"] and redes_activas({"bluesky": "b"}, 0) == ["youtube", "bluesky"]
         and redes_activas({}, 0) == ["youtube"])
    largo = "Primera frase con gancho que engancha. " * 12
    posts = parte_bluesky([largo.strip()])
    caso("Bluesky: un post que se pasa de 300 se parte por frases, NINGUNO pasa de 300 contando el enlace, y el enlace va "
         "en el último", all(largo_real(p) <= 300 for p in posts) and len(posts) >= 2 and ENLACE in posts[-1],
         [largo_real(p) for p in posts])
    resp = {"linkedin": {"texto": "Gancho.\n\nIdea 1 → x. Mira https://youtu.be/abc", "comentario": "Aquí el vídeo"},
            "bluesky": {"posts": ["Hola", "Adiós {ENLACE}"]}, "reddit": {"titulo": "Título {ENLACE}", "texto": "Valor"},
            "discord": {"texto": "¡Nuevo vídeo!"}, "instagram": {"textos": ["Reel 1", "Reel 2", "de más"]}}
    pub = parsea(json.dumps(resp), ["linkedin", "bluesky", "reddit", "discord", "instagram"], 2)
    caso("parser: en LinkedIn el enlace sale del post y va al primer comentario; Reddit y Discord lo llevan al final; una URL "
         "que se cuele se cambia por la del vídeo; Instagram, un texto por Short (ni uno más)",
         ENLACE not in pub["linkedin"]["texto"] and "http" not in pub["linkedin"]["texto"]
         and pub["linkedin"]["comentario"].endswith(ENLACE) and pub["reddit"]["texto"].endswith(ENLACE)
         and ENLACE not in pub["reddit"]["titulo"] and pub["discord"]["texto"].endswith(ENLACE)
         and pub["instagram"]["textos"] == ["Reel 1", "Reel 2"], pub)
    try:
        parsea(json.dumps({"linkedin": {"texto": "x"}}), ["linkedin", "bluesky"], 0)
        caso("si falta una red, falla (y se reintenta)", False)
    except ValueError:
        caso("si falta una red, falla (y se reintenta)", True)
    caso("avisa si se cuela una frase de relleno típica de IA",
         _avisos({"discord": {"texto": "Descubre cómo mejorar tu SEO"}}) != [])

    # 📣 YouTube · Publicaciones (29-sep-2026): la IA da tema y resumen; el código arma las 3 líneas
    yt = parsea(json.dumps({"youtube": {"tema": "de canibalización SEO en Search Console",
                                        "resumen": "Así ves si dos de tus páginas compiten por la misma búsqueda. "
                                                   "https://youtu.be/xyz {ENLACE}"}}), ["youtube"], 0)["youtube"]["texto"]
    lin = yt.split("\n")
    caso("YouTube: «🎬 Nuevo vídeo hablando de <tema>» + UNA frase de resumen + el enlace SOLO al final y en su línea (3 "
         "líneas); el «de» que repite la IA se quita y una URL que se cuele no sale",
         len(lin) == 3 and lin[0] == "🎬 Nuevo vídeo hablando de canibalización SEO en Search Console"
         and lin[1] == "Así ves si dos de tus páginas compiten por la misma búsqueda." and lin[2] == ENLACE
         and yt.count(ENLACE) == 1 and "http" not in yt, yt)
    lin2 = parsea(json.dumps({"youtube": {"tema": "Nuevo vídeo hablando de sobre el SEO local",
                                          "resumen": "Una\nsola\nfrase."}}), ["youtube"], 0)["youtube"]["texto"].split("\n")
    caso("YouTube: el «Nuevo vídeo hablando de / sobre» que repite la IA se quita, y un resumen con saltos sale en UNA línea",
         lin2[0] == "🎬 Nuevo vídeo hablando de el SEO local" and lin2[1] == "Una sola frase." and len(lin2) == 3, lin2)
    lin3 = parsea(json.dumps({"youtube": {"tema": "un tema " * 30, "resumen": "palabra " * 80}}),
                  ["youtube"], 0)["youtube"]["texto"].split("\n")
    caso("YouTube: un tema o un resumen larguísimo se corta en una palabra ENTERA con «…» y sin pasarse (tema ≤ 90, resumen "
         "≤ 190)", len(lin3[0]) <= len(_YT_ENTRADA) + 1 + 90 and len(lin3[1]) <= 190 and lin3[1].endswith("palabra…")
         and lin3[2] == ENLACE, [len(x) for x in lin3])
    malos = 0
    for mala in ({"youtube": {"tema": "x"}}, {"youtube": {"resumen": "y"}}, {"youtube": {"tema": "  ", "resumen": "y"}}, {}):
        try:
            parsea(json.dumps(mala), ["youtube"], 0)
        except ValueError:
            malos += 1
    caso("YouTube: si la IA no da el tema o el resumen, falla (y se reintenta)", malos == 4, malos)
    pr = arma_prompt({"titulo": "T", "tags": ["seo"], "capitulos": "", "frases": [{"texto": "hola"}], "muletillas": [],
                      "shorts": []}, ["youtube", "bluesky"], "Mi canal", "obj")
    caso("el prompt de YouTube pide «tema» y «resumen», dice que el programa pone el «Nuevo vídeo hablando de» y va antes que "
         "las demás redes", '"youtube": {"tema": "...", "resumen": "..."}' in pr and "Nuevo vídeo hablando de" in pr
         and pr.index("YouTube · Publicaciones") < pr.index("Bluesky"), pr[-400:])

    with tempfile.TemporaryDirectory() as tmp:                 # de punta a punta, con la IA de mentira
        t = Path(tmp)
        jid = "2026-01-01-redes--pro"
        out = t / "videos" / jid
        out.mkdir(parents=True)
        (t / "work" / jid).mkdir(parents=True)
        (out / "pro.mp4").write_bytes(b"x")
        frases = [{"word": f"frase{k}.", "start": 1.0 + k, "end": 1.5 + k} for k in range(8)]
        (t / "work" / jid / "words.json").write_text(json.dumps(frases), encoding="utf-8")
        (out / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "titulo": "Prueba", "duracion": 10.0,
                                                        "segments": [[0.0, 10.0]], "eventos_visuales": [], "tema": "",
                                                        "tags": ["seo"], "shorts": []}]), encoding="utf-8")
        pedidos: list = []

        def llama(pr, va):
            pedidos.append(pr)
            return va(json.dumps({"youtube": {"tema": "el vídeo de prueba", "resumen": "Un resumen de prueba."},
                                  "bluesky": {"posts": ["Hola {ENLACE}"]}})), "gemini-3.5-flash-lite"

        r = crea(jid, None, t / "videos", t / "work", llama)
        caso("sin más redes en el canal, igual sale la de YouTube (la del propio canal): 1 petición, solo esa",
             r["ok"] and len(pedidos) == 1 and r["redes"]["orden"] == ["youtube"] and "Bluesky" not in pedidos[0], r)
        (out / "manifest.json").write_text(json.dumps([{"archivo": "pro.mp4", "titulo": "Prueba", "duracion": 10.0,
                                                        "segments": [[0.0, 10.0]], "eventos_visuales": [], "tema": "ejemplo",
                                                        "tags": ["seo"], "shorts": []}]), encoding="utf-8")

        class Cfg2:
            def get_path(self, k, dflt=None):
                return {"videos.canales.ejemplo": {"nombre": "Mi canal", "enlaces": {"bluesky": "b"}}}.get(k, dflt)

        r2 = crea(jid, Cfg2(), t / "videos", t / "work", llama)
        g = lee(jid, t / "videos") or {}
        caso("de punta a punta: 1 petición con la transcripción y el estilo de cada red; se guarda redes.json (YouTube "
             "primero, con el enlace donde toca)",
             r2["ok"] and len(pedidos) == 2 and "frase3." in pedidos[1] and "Bluesky" in pedidos[1]
             and g.get("orden") == ["youtube", "bluesky"] and g["redes"]["bluesky"]["posts"] == ["Hola {ENLACE}"]
             and g["redes"]["youtube"]["texto"] == "🎬 Nuevo vídeo hablando de el vídeo de prueba\nUn resumen de prueba.\n" + ENLACE,
             r2.get("error") or g)
    return filas
