"""«Shorts · TikTok» — el DIRECTOR de IA y el ritmo (el autor, 16-oct-2026: «aquí se requiere una consulta de IA, porque la IA es el orquestador… tiene que pasar algo cada dos segundos»).

UNA petición a Gemini (la cadena de modelos de siempre, el mejor primero) lee la transcripción YA SIN SILENCIOS (en el reloj del Short) y devuelve:
  · los textos de publicación para YouTube, TikTok e Instagram (título/caption, descripción, tags/hashtags) y el CTA final,
  · el GANCHO de los primeros segundos y
  · los EVENTOS de significado: palabras clave en grande, tarjetas (punto / dato / definición), ilustraciones animadas y escenas a pantalla completa.
El CÓDIGO (aquí, sin IA) hace que sea verdad lo que sale en pantalla y el ritmo:
  · cada evento va anclado a CUANDO lo dices (`cita` copiada de la transcripción, comprobada: lo que no se apoya en lo dicho se descarta);
  · una cifra de una tarjeta de dato tiene que decirse en el vídeo (nada inventado);
  · reglas duras de convivencia (un solo texto grande a la vez, las escenas a pantalla completa no se pisan, nada en los primeros ni en los últimos segundos que ya tienen
    gancho y CTA);
  · REGLA DE RITMO: nunca pasan más de `RITMO_MAX_S` (2 s) sin que ocurra algo (un cambio de plano por corte, un zoom, una palabra, una tarjeta…): lo que la IA no cubre lo rellena
    el código con acercamientos y palabras sacadas de lo que dices (`rellena`). Los subtítulos no cuentan: siempre están.
Es una COPIA PROPIA de las ideas de `director.py`/`ritmo.py` de Vídeos PRO (si cambian allí, los Shorts no cambian).
Funciones puras + `prueba()`.
"""
from __future__ import annotations

import json
import random
import re
import unicodedata

RITMO_MAX_S = 2.5
ADELANTO = 0.40              # los elementos salen ANTES de la palabra (el autor, 16-oct: «hay un pequeño delay… en los Shorts cada segundo cuenta»); en Vídeos PRO es otro, aparte
T_GANCHO = 1.8               # el gancho (arriba) los primeros segundos
T_CTA_FINAL = 2.7            # la tarjeta final
SEP_ESCENAS = 12.0            # entre dos escenas a pantalla completa
MARGEN_FIN = 3.0             # sin escenas en los últimos segundos (ya está el CTA)
TIPOS_IA = ("palabra", "tarjeta", "ilus", "escena")
KINDS_TARJETA = ("punto", "dato", "definicion")
KINDS_ESCENA = ("ilus_grande", "cita", "dato", "lista")
DUR_ESCENA = {"ilus_grande": 2.5, "cita": 3.0, "dato": 2.6, "lista": 3.2}
STOP = set("""a al algo algun alguna algunas alguno algunos ante aqui asi aun aunque bien cada casi como con cual cuando de del desde donde dos el ella ellas ello ellos en entonces eres es esa esas ese eso esos esta
estaba estan estar este esto estos fue ha hacer hay la las le les lo los mas me mi mis mucho muy no nos nosotros o os otra otro para pero poco por porque que quien se ser si sin sobre solo son su sus tambien te tener
tiene todo todos tu tus un una uno unos vamos voy y ya yo""".split())
_URL = re.compile(r"(?i)https?://\S+|www\.\S+")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ ]+", " ", s.replace("ñ", "n")).strip()


def _txt(v, tope: int) -> str:
    s = _URL.sub("", re.sub(r"\s+", " ", str(v or ""))).replace("#", "").strip(" \t\"'`")
    if len(s) > tope:
        s = s[:tope].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return s


# ------------------------------------------------------------------ el prompt
SYSTEM = """Eres el director de edición de Shorts/TikToks de «{canal}»: conviertes un trozo de vídeo ya hablado (sin silencios) en un short de MÁXIMA retención.
Respondes SOLO con JSON válido, sin markdown. Todo en español con tildes y signos ¿¡."""


def lineas_transcripcion(words: list[dict]) -> list[tuple[float, str]]:
    """La transcripción del Short (reloj del Short) en líneas con su segundo de inicio: se parte en pausas ≥ 0,35 s o a las 12 palabras."""
    out: list[tuple[float, str]] = []
    cur: list[str] = []
    t0 = 0.0
    prev_end = 0.0
    for w in words:
        pal = str(w.get("word", "")).strip()
        if not pal:
            continue
        if cur and (float(w["start"]) - prev_end >= 0.35 or len(cur) >= 12):
            out.append((t0, " ".join(cur)))
            cur = []
        if not cur:
            t0 = float(w["start"])
        cur.append(pal)
        prev_end = float(w["end"])
    if cur:
        out.append((t0, " ".join(cur)))
    return out


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def arma_prompt(words: list[dict], dur: float, canal: str, contexto: str, ilus_catalogo: str, regla: str = "") -> str:
    n_pal = max(2, round(dur / 6))
    n_tar = max(1, round(dur / 12))
    n_ilu = max(1, round(dur / 18))
    n_esc = 0 if dur < 20 else max(1, round(dur / 22))
    L = [f"Esto es un Short de «{canal}» de {dur:.0f} segundos (ya sin silencios). Tu trabajo: (1) escribir los textos de publicación y (2) marcar los EVENTOS visuales que hacen que NO se pueda dejar de mirar.", ""]
    if contexto:
        L += [f"CONTEXTO DEL CANAL: {contexto[:900]}", ""]
    if regla:
        L += [regla, ""]
    L += ["TEXTOS (para YouTube Shorts, TikTok e Instagram; SIN URLs; lo llamativo sale de lo que de verdad dice el vídeo, sin prometer lo que no cumple):",
          "- «titulo_yt»: 30-70 caracteres, gancho claro, sin hashtags.",
          "- «descripcion_yt»: 2-3 líneas cortas (la idea + una invitación a suscribirse). Sin URLs.",
          "- «tags_yt»: 5-8 etiquetas (1-3 palabras, sin #).",
          "- «titulo_tt»: el texto de TikTok, 40-90 caracteres, con gancho; «hashtags_tt»: 3-5 hashtags (con #, sin «fyp» ni «viral»).",
          "- «titulo_ig»: el pie de Instagram, 1-3 líneas con gancho y una línea que invite a guardar o seguir; «hashtags_ig»: 3-5 hashtags (con #).",
          "- «gancho»: 3-7 palabras en MAYÚSCULAS que se leen los primeros 2 segundos (intriga o promesa concreta del vídeo).",
          "- «cta_final»: 3-6 palabras en MAYÚSCULAS para la tarjeta del final («SÍGUEME PARA MÁS SEO»).", "",
          f"EVENTOS (lista «eventos»; orientativo para {dur:.0f} s: ≈{n_pal} palabras, ≈{n_tar} tarjetas, ≈{n_ilu} ilustraciones, ≈{n_esc} escenas; el programa añade acercamientos por su cuenta):",
          "Cada evento lleva «cita»: de 4 a 8 palabras COPIADAS tal cual, seguidas, de la transcripción, de la frase en la que SE NOMBRA lo que muestra (el programa lo ancla ahí y comprueba que existe: un evento sin cita real se descarta).",
          "- MUY IMPORTANTE (el contexto manda): la cita NUNCA es la frase que introduce la idea («uno de los conceptos que trabajamos es…»), sino donde se DICE la cosa («…el funnel que hay…»). Si la ilustración es un embudo y él habla del «funnel» cinco segundos después de empezar la frase, la cita es la del «funnel». Entiende de qué se habla en cada momento: el elemento sale cuando se menciona su tema, ni antes ni después.",
          "- Además cada evento lleva «dicho»: la palabra (o 2 palabras) EXACTA de la transcripción en la que se nombra su tema («funnel», «canonical», «40 por ciento»); el elemento sale justo en ella (puede estar en inglés o ser un sinónimo de lo que dibujas).",
          "- OJO: cada evento lleva SIEMPRE su «texto» (la palabra, el título de la tarjeta, la etiqueta de la ilustración, la frase de la escena): sin «texto» no sale nada.",
          "- tipo «palabra»: «texto» = 1-3 palabras en MAYÚSCULAS, lo MÁS importante de esa frase (una cifra, un nombre, la idea). No repitas lo que ya dice una tarjeta.",
          "- tipo «tarjeta»: «kind» = «punto» (un paso o idea numerable: «texto» ≤ 6 palabras), «dato» (una cifra que SE DICE en el vídeo: «valor» = la cifra tal cual, p. ej. «3,6 %» o «10 min», «texto» = qué mide ≤ 6 palabras) o «definicion» (un término que se explica: «texto» = el término ≤ 3 palabras, «subtexto» = su definición ≤ 12 palabras).",
          "- tipo «ilus»: una ilustración animada pequeña que refuerza lo que dices; «ilus» = una clave de esta lista (otra no vale) y «texto» = etiqueta de 1-3 palabras:", ilus_catalogo,
          "- tipo «escena»: un cambio de plano a PANTALLA COMPLETA, sin la cara: «kind» = «ilus_grande» (la ilustración grande: «ilus» + «texto» = la frase clave ≤ 6 palabras), «cita» (SOLO una frase tuya que sea de verdad una cita o una idea de peso, de las que se subrayarían: «texto» = la frase ≤ 14 palabras copiada de lo dicho; no la uses para frases normales), «dato» (una cifra: «valor» + «texto») o «lista» («items» = 2-4 elementos de ≤ 4 palabras que enumeras). Úsalas pocas y en los momentos de más peso.",
          "- Reparte los eventos a lo largo de TODO el short (que no se amontonen en el principio); ninguno en los primeros 2 segundos (ahí va el gancho) ni en los últimos 3.",
          "- Nunca inventes datos, cifras ni nombres que no se digan. Nunca escribas URLs.", "",
          "TRANSCRIPCIÓN del short ([m:ss.d] = su reloj; cada línea = una frase):"]
    L += [f"[{_mmss(t)}] {s}" for t, s in lineas_transcripcion(words)]
    L += ["", 'Responde SOLO con JSON: {"titulo_yt","descripcion_yt","tags_yt":[..],"titulo_tt","hashtags_tt":[..],"titulo_ig","hashtags_ig":[..],"gancho","cta_final","eventos":[{"tipo","t","cita","texto","subtexto","kind","valor","ilus","items":[..]}]}']
    return "\n".join(L)


def esquema():
    try:
        from pydantic import BaseModel

        class Evento(BaseModel):                       # (todos los campos OBLIGATORIOS: con valores por defecto el modelo se saltaba «texto» e «ilus» y se perdían las tarjetas)
            tipo: str
            t: float
            texto: str
            cita: str
            dicho: str
            kind: str
            ilus: str
            valor: str
            subtexto: str
            items: list[str]

        class Plan(BaseModel):
            titulo_yt: str
            descripcion_yt: str
            tags_yt: list[str]
            titulo_tt: str
            hashtags_tt: list[str]
            titulo_ig: str
            hashtags_ig: list[str]
            gancho: str
            cta_final: str
            eventos: list[Evento]
        return Plan
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ anclar a lo que dices
def ubica(cita: str, words: list[dict], cerca_de: float | None = None) -> int:
    """El índice de la PRIMERA palabra de `cita` en la transcripción (≥ 70 % de las palabras de la cita seguidas, en orden); -1 si no está. Con varias coincidencias, la más cercana a `cerca_de`."""
    toks = [t for t in norm(cita).split() if t]
    if len(toks) < 2:
        return -1
    pals = [norm(w.get("word", "")) for w in words]
    mejores: list[tuple[float, int]] = []
    ventana = len(toks) + 2
    inicios = [i for i in range(len(pals)) if pals[i] == toks[0]]
    if not inicios:                                                            # (si Whisper oyó mal la primera palabra, se parte de la segunda)
        inicios = [i - 1 for i in range(1, len(pals)) if pals[i] == toks[1]]
    for i in inicios:
        k, j = 0, i
        while j < min(len(pals), i + ventana) and k < len(toks):
            if pals[j] == toks[k]:
                k += 1
            j += 1
        sc = k / len(toks)
        if sc >= 0.7 or (sc >= 0.5 and len(toks) <= 3 and k >= 2):
            mejores.append((sc, i))
    if not mejores:
        return -1
    top = max(s for s, _ in mejores)
    cand = [i for s, i in mejores if s >= top - 1e-9]
    if cerca_de is not None:
        cand.sort(key=lambda i: abs(float(words[i]["start"]) - cerca_de))
    return cand[0]


def ancla_por_texto(fragmentos: list[str], words: list[dict], t_ia: float, ventana: float = 8.0) -> int:
    """Si la IA no copió bien la cita: la palabra MÁS CERCANA a su segundo (±`ventana` s) en la que se dice algo del propio texto del elemento (una palabra de contenido de ≥ 4 letras). -1 si no se dice nada de eso ahí."""
    toks = {t for f in fragmentos for t in norm(f).split() if len(t) >= 4 and t not in STOP}
    if not toks:
        return -1
    mejor, dist = -1, 1e9
    for i, w in enumerate(words):
        if norm(w.get("word", "")) in toks:
            d = abs(float(w["start"]) - t_ia)
            if d <= ventana and d < dist:
                mejor, dist = i, d
    return mejor


def _stem(p: str) -> str:
    return norm(p)[:5]


def afina_concepto(i: int, cita: str, dicho: str, claves: list[str], words: list[dict], kind: str = "") -> int:
    """el autor, 16-oct: «a los 7 s sale el embudo pero habla del embudo hasta el 12»: la IA cita la frase que INTRODUCE la idea y el programa anclaba al principio de esa frase (4 s antes). Aquí se corrige por CONTEXTO:
    1) la palabra `dicho` que la IA marcó (o cualquiera de las `claves` del propio elemento) en la ventana que va desde la cita hasta ~9 s después → el elemento sale en ESA palabra;
    2) si no aparece, y no es una escena de frase («cita»), en la ÚLTIMA palabra de la cita (el tema suele cerrarla) en vez de en la primera. Devuelve el índice de palabra."""
    if kind == "cita":                                                        # una escena de FRASE sale cuando EMPIEZA la frase
        return i
    n = len(words)
    largo = max(1, len(norm(cita).split()))
    fin = min(n - 1, i + largo - 1)
    tope = float(words[i]["start"]) + 9.0
    def toks(fuentes, minimo):
        out = []
        for fuente in fuentes:
            for t in norm(fuente).split():
                if len(t) >= minimo and t not in STOP and _stem(t) not in out:
                    out.append(_stem(t))
        return out

    def busca(buscadas, desde):
        for k in range(max(0, desde), n):
            if float(words[k]["start"]) > tope:
                break
            pal = norm(str(words[k].get("word", "")))
            if len(pal) >= 3 and _stem(pal) in buscadas:
                return k
        return -1
    k = busca(toks([dicho] if dicho else [], 3), i - 2)                          # 1) la palabra que la IA dice que nombra el tema
    if k < 0:
        k = busca(toks(claves, 4), i)                                            # 2) palabras propias del elemento (nunca antes de la cita ni cortas como «SEO»)
    if k >= 0:
        return k
    if kind == "cita":
        return i
    return fin


def cifras_dichas(valor: str, words: list[dict]) -> bool:
    """¿Se dice en el vídeo al menos una de las cifras de `valor`? («3,6 %» → «3,6» o «3 coma 6»). Nada de cifras inventadas."""
    texto = " ".join(str(w.get("word", "")) for w in words).lower()
    texto_n = norm(texto)
    for n in re.findall(r"\d+(?:[.,]\d+)?", str(valor)):
        a = n.replace(",", " ").replace(".", " ")
        if n in texto or a in texto_n or n.replace(",", ".") in texto or re.sub(r"\D", "", n) in re.sub(r"\D", " ", texto).split():
            return True
    return False


# ------------------------------------------------------------------ limpiar lo que dice la IA
def _hashtags(v, n: int) -> list[str]:
    out: list[str] = []
    for h in v or []:
        t = "#" + re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]", "", str(h).lstrip("#"))
        if len(t) > 2 and t.lower() not in ("#fyp", "#viral", "#foryou", "#shorts") and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:n]


def _tags(v, n: int) -> list[str]:
    out: list[str] = []
    for h in v or []:
        t = _txt(str(h).lstrip("#"), 30).strip(" .")
        if t and t.lower() not in [x.lower() for x in out] and t.lower() != "shorts":
            out.append(t)
    return out[:n]


def parsea_meta(x: dict) -> dict:
    return {"titulo_yt": _txt(x.get("titulo_yt"), 90), "descripcion_yt": re.sub(r"[ \t]+\n", "\n", _URL.sub("", str(x.get("descripcion_yt") or ""))).strip()[:600],
            "tags_yt": _tags(x.get("tags_yt"), 8), "titulo_tt": _txt(x.get("titulo_tt"), 110), "hashtags_tt": _hashtags(x.get("hashtags_tt"), 5),
            "titulo_ig": _URL.sub("", str(x.get("titulo_ig") or "")).replace("#", "").strip()[:300], "hashtags_ig": _hashtags(x.get("hashtags_ig"), 5),
            "gancho": _txt(x.get("gancho"), 48).upper(), "cta_final": _txt(x.get("cta_final"), 40).upper()}


def meta_por_codigo(words: list[dict], canal: str = "") -> dict:
    """Si la IA no responde (o un campo sale vacío): el Short sale igual, con textos sacados de lo que dices."""
    texto = " ".join(str(w.get("word", "")) for w in words).strip()
    primera = re.split(r"(?<=[.!?])\s+", texto)[0] if texto else (canal or "Short")
    titulo = _txt(primera, 70) or (canal or "Short")
    claves = []
    for w in words:
        t = norm(w.get("word", ""))
        if len(t) >= 6 and t not in STOP and t not in claves:
            claves.append(t)
    claves = claves[:6]
    return {"titulo_yt": titulo, "descripcion_yt": f"{titulo}\n\nSuscríbete para más.", "tags_yt": claves, "titulo_tt": titulo, "hashtags_tt": _hashtags(claves[:4], 4),
            "titulo_ig": f"{titulo}\n\nGuárdalo y sígueme para más.", "hashtags_ig": _hashtags(claves[:4], 4), "gancho": _txt(titulo, 40).upper(), "cta_final": "SÍGUEME PARA MÁS"}


def completa_meta(meta: dict, words: list[dict], canal: str = "") -> dict:
    base = meta_por_codigo(words, canal)
    out = dict(meta)
    for k, v in base.items():
        if not out.get(k):
            out[k] = v
    return out


# ------------------------------------------------------------------ los eventos
def _dur_palabra(texto: str) -> float:
    return min(2.4, max(1.1, 0.8 + 0.28 * len(texto.split())))


def valida_eventos(raw: list[dict], words: list[dict], dur: float, ilus_ok: set[str]) -> tuple[list[dict], list[str]]:
    """Los eventos de la IA → los que valen, anclados a lo que dices, con su duración. -> (eventos, motivos de lo descartado)."""
    out: list[dict] = []
    descartes: list[str] = []
    for e in (raw or [])[:60]:
        if not isinstance(e, dict):
            continue
        tipo = str(e.get("tipo") or "").strip().lower()
        if tipo not in TIPOS_IA:
            descartes.append(f"tipo desconocido «{tipo}»")
            continue
        try:
            t_ia = float(e.get("t") or 0.0)
        except (TypeError, ValueError):
            t_ia = 0.0
        i = ubica(str(e.get("cita") or ""), words, t_ia if t_ia > 0 else None)
        if i < 0 and t_ia > 0:                                  # (la IA no copió bien la cita: se ancla donde se dice lo que el elemento enseña)
            i = ancla_por_texto([str(e.get("texto") or ""), str(e.get("subtexto") or "")] + [str(x) for x in (e.get("items") or [])], words, t_ia)
            if i < 0 and tipo == "ilus":                          # una ilustración es decorativa: vale en el segundo que da la IA si cae dentro del vídeo
                i = min(range(len(words)), key=lambda k: abs(float(words[k]["start"]) - t_ia)) if words and t_ia < dur - 1.0 else -1
        if i < 0:
            descartes.append(f"{tipo} «{_txt(e.get('texto'), 24)}»: lo que enseña no se dice en ese momento")
            continue
        if tipo != "palabra":
            claves = [str(e.get("texto") or "")] + [str(x) for x in (e.get("items") or [])] + ([str(e.get("valor") or "")] if e.get("valor") else [])
            if tipo == "ilus" or str(e.get("kind") or "") == "ilus_grande":
                claves.append(str(e.get("ilus") or "").replace("_", " "))
            i = afina_concepto(i, str(e.get("cita") or ""), str(e.get("dicho") or ""), claves, words, str(e.get("kind") or "").strip().lower() if tipo == "escena" else "")
        t = max(0.0, float(words[i]["start"]) - ADELANTO)
        ev = {"tipo": tipo, "t": round(t, 2), "cita": _txt(e.get("cita"), 120), "dicho": _txt(e.get("dicho"), 40), "origen": "ia"}
        texto = _txt(e.get("texto"), 60)
        kind = str(e.get("kind") or "").strip().lower()
        if tipo == "palabra":
            texto = texto.upper()
            if not texto or len(texto.split()) > 4 or len(texto) > 28:
                descartes.append("palabra vacía o demasiado larga")
                continue
            ev.update(texto=texto, dur=_dur_palabra(texto))
        elif tipo == "tarjeta":
            if kind not in KINDS_TARJETA:
                kind = "punto"
            valor = _txt(e.get("valor"), 14)
            if kind == "dato":
                if not valor or not re.search(r"\d", valor) or not cifras_dichas(valor, words):
                    descartes.append(f"dato «{valor}»: la cifra no se dice en el vídeo")
                    continue
            if not texto or len(texto.split()) > 8:
                descartes.append("tarjeta sin texto o demasiado larga")
                continue
            if kind == "definicion" and not _txt(e.get("subtexto"), 120):
                kind = "punto"
            ev.update(kind=kind, texto=texto, subtexto=_txt(e.get("subtexto"), 120), valor=valor, dur=3.0 if kind == "definicion" else 2.4)
        elif tipo == "ilus":
            ilu = str(e.get("ilus") or "").strip()
            if ilu not in ilus_ok:
                descartes.append(f"ilustración «{ilu}» no existe")
                continue
            if not texto:
                try:
                    from .st_ilus import CATALOGO
                    texto = CATALOGO[ilu].nombre
                except Exception:  # noqa: BLE001
                    texto = ""
            ev.update(ilus=ilu, texto=texto.upper()[:24], dur=2.6)
        else:                                            # escena
            if kind not in KINDS_ESCENA:
                descartes.append(f"escena «{kind}» desconocida")
                continue
            ev["kind"] = kind
            if kind == "ilus_grande":
                ilu = str(e.get("ilus") or "").strip()
                if ilu not in ilus_ok or not texto:
                    descartes.append("escena de ilustración sin ilustración o sin frase")
                    continue
                ev.update(ilus=ilu, texto=texto.upper()[:40])
            elif kind == "cita":
                if len(texto.split()) < 4:
                    descartes.append("escena de frase demasiado corta")
                    continue
                ev.update(texto=_txt(e.get("texto"), 110))
            elif kind == "dato":
                valor = _txt(e.get("valor"), 14)
                if not valor or not re.search(r"\d", valor) or not cifras_dichas(valor, words) or not texto:
                    descartes.append("escena de dato sin cifra dicha")
                    continue
                ev.update(valor=valor, texto=texto.upper()[:40])
            else:
                items = [x for x in (_txt(v, 30) for v in (e.get("items") or [])) if x][:4]
                if len(items) < 2:
                    descartes.append("escena de lista con menos de 2 elementos")
                    continue
                ev.update(items=items)
            ev["dur"] = DUR_ESCENA[kind]
        ev["dur"] = round(min(ev["dur"], max(0.8, dur - ev["t"] - 0.1)), 2)
        out.append(ev)
    out.sort(key=lambda z: z["t"])
    return out, descartes


def _choca(a: dict, b: dict) -> bool:
    return a["t"] < b["t"] + b["dur"] and b["t"] < a["t"] + a["dur"]


def _zona(ev: dict) -> str:
    """Qué ocupa en pantalla: escena = todo; palabra/tarjeta comparten la franja de texto; ilus va arriba a la derecha."""
    if ev["tipo"] == "escena":
        return "todo"
    if ev["tipo"] in ("palabra", "tarjeta"):
        return "texto"
    if ev["tipo"] == "ilus":
        return "ilus"
    return "ninguna"


def convivencia(eventos: list[dict], dur: float) -> tuple[list[dict], list[str]]:
    """Reglas duras: una escena a pantalla completa tapa todo (nada más a la vez) y se separa ≥ 9 s de otra y de los bordes; solo UN texto grande a la vez (palabra o tarjeta);
    una ilustración a la vez; nada que empiece en el gancho ni en el cierre. Gana el primero (los de la IA ya vienen por orden de aparición)."""
    out: list[dict] = []
    motivos: list[str] = []
    for ev in sorted(eventos, key=lambda z: z["t"]):
        if ev["t"] < T_GANCHO and ev["tipo"] != "escena":
            ev = {**ev, "t": round(T_GANCHO, 2)}
        if ev["t"] + 0.6 > dur - 0.3:
            motivos.append(f"{ev['tipo']} fuera del final")
            continue
        if ev["tipo"] == "escena" and (ev["t"] < T_GANCHO + 0.4 or ev["t"] + ev["dur"] > dur - MARGEN_FIN):
            motivos.append("escena demasiado cerca del principio o del final")
            continue
        malo = False
        for o in out:
            if not _choca(ev, o) and not (ev["tipo"] == "escena" and o["tipo"] == "escena" and abs(ev["t"] - o["t"]) < SEP_ESCENAS):
                continue
            zz, zo = _zona(ev), _zona(o)
            if "todo" in (zz, zo) or zz == zo:
                malo = True
                break
        if malo:
            motivos.append(f"{ev['tipo']} «{ev.get('texto', '')[:20]}» se pisaba con otro")
            continue
        out.append(ev)
    return out, motivos


def instantes(eventos: list[dict], cortes: list[float]) -> list[float]:
    """Cuándo «pasa algo»: cada corte de plano y cada evento que empieza (una escena a pantalla completa cuenta mientras dura: se mueve)."""
    xs = [0.0] + list(cortes)
    for e in eventos:
        xs.append(e["t"])
        if e["tipo"] == "escena":
            k = e["t"] + 1.0
            while k < e["t"] + e["dur"]:
                xs.append(k)
                k += 1.0
            xs.append(e["t"] + e["dur"])
    return sorted(xs)


def hueco_mayor(eventos: list[dict], cortes: list[float], dur: float) -> float:
    xs = instantes(eventos, cortes) + [dur]
    return max((b - a for a, b in zip(xs, xs[1:])), default=0.0)


def candidatas_palabra(words: list[dict]) -> list[dict]:
    """Palabras de ESTE vídeo que sirven de palabra clave por código: las largas que no son de relleno, con preferencia por cifras y nombres propios."""
    out = []
    for k, w in enumerate(words):
        pal = re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ%$.,]", "", str(w.get("word", "")))
        n = norm(pal)
        if not n or n in STOP:
            continue
        digito = bool(re.search(r"\d", pal))
        if len(n) < 6 and not digito:
            continue
        pt = 2 if digito else (1 if pal[:1].isupper() and k > 0 and not str(words[k - 1].get("word", "")).strip().endswith((".", "?", "!")) else 0)
        out.append({"i": k, "t": float(w["start"]), "texto": pal.strip(".,").upper(), "peso": pt + min(2, len(n) // 6)})
    return out


def rellena(eventos: list[dict], cortes: list[float], dur: float, words: list[dict], rng=None) -> list[dict]:
    """REGLA DE RITMO: si pasan más de `RITMO_MAX_S` segundos sin nada, se añade en medio un acercamiento (a la pantalla o a la cara, alternando) o una palabra clave sacada de lo
    que dices (si cabe sin pisar otro texto). Repite hasta que no quede ningún hueco largo. Determinista (el azar solo desempata)."""
    r = rng or random.Random(7)
    ev = [dict(e) for e in eventos]
    cands = candidatas_palabra(words)
    alterna = 0
    for _ in range(400):
        xs = instantes(ev, cortes) + [dur]
        mejor = max(zip(xs, xs[1:]), key=lambda p: p[1] - p[0])
        a, b = mejor
        if b - a <= RITMO_MAX_S + 1e-6:
            break
        t = a + min(RITMO_MAX_S * 0.9, (b - a) / 2.0 if b - a < 2 * RITMO_MAX_S else RITMO_MAX_S * 0.9)
        t = round(max(a + 0.4, min(t, b - 0.4)), 2)
        en_escena = any(e["tipo"] == "escena" and e["t"] <= t <= e["t"] + e["dur"] for e in ev)
        if en_escena:
            ev.append({"tipo": "flash", "t": t, "dur": 0.2, "origen": "codigo"})
            continue
        nuevo = None
        if alterna % 3 == 1 and t > T_GANCHO and t + 1.4 < dur - 0.3:
            pos = [c for c in cands if abs(c["t"] - t) <= 0.9 and c["peso"] >= 1]
            pos.sort(key=lambda c: (-c["peso"], abs(c["t"] - t)))
            for c in pos:
                cand = {"tipo": "palabra", "t": round(max(T_GANCHO, c["t"] - ADELANTO), 2), "texto": c["texto"][:26], "dur": _dur_palabra(c["texto"]), "origen": "codigo"}
                if not any(_choca(cand, o) and _zona(o) in ("texto", "todo") for o in ev) and not any(o["tipo"] == "palabra" and norm(o["texto"]) == norm(cand["texto"]) and abs(o["t"] - cand["t"]) < 20 for o in ev):
                    nuevo = cand
                    break
        if nuevo is None:
            nuevo = {"tipo": "zoom_cara" if alterna % 2 else "zoom_pantalla", "t": t, "dur": 0.7, "origen": "codigo"}
        ev.append(nuevo)
        alterna += 1
    ev.sort(key=lambda z: z["t"])
    return ev


def mas_escenas(eventos: list[dict], words: list[dict], dur: float, rng=None) -> list[dict]:
    """Cambios DRÁSTICOS (el autor, 16-oct: «faltan»… y luego «no te pongas tan dopamínico»): objetivo ≈ una escena a pantalla completa cada 22 s. Las ilustraciones pequeñas alternas de la IA pasan a
    ilustración GRANDE hasta llegar a ese número. La escena «cita» SOLO la pone la IA (una frase que de verdad es una cita): el código ya no inventa ninguna. Nunca pisa otra escena ni el gancho ni el cierre."""
    r = rng or random.Random(5)
    ev = [dict(e) for e in eventos]
    meta = 0 if dur < 20 else max(1, round(dur / 22))

    def escenas():
        return [e for e in ev if e["tipo"] == "escena"]

    def libre(t, d):
        if t < T_GANCHO + 0.4 or t + d > dur - MARGEN_FIN:
            return False
        return all(abs(t - o["t"]) >= SEP_ESCENAS and not (t < o["t"] + o["dur"] and o["t"] < t + d) for o in escenas())
    k = 0
    for e in sorted(ev, key=lambda z: z["t"]):
        if len(escenas()) >= meta:
            break
        if e["tipo"] == "ilus" and e.get("origen") == "ia":
            k += 1
            if k % 2 == 1 and libre(e["t"], DUR_ESCENA["ilus_grande"]):
                e.update(tipo="escena", kind="ilus_grande", dur=DUR_ESCENA["ilus_grande"])
    ev.sort(key=lambda z: z["t"])
    return ev


# ------------------------------------------------------------------ layouts: «cámara arriba · pantalla en medio · subtítulos» es UNA escena; hay más
LAYOUT_DURS = {"base": (6.0, 9.0), "pantalla": (4.5, 7.0), "cara": (3.5, 5.5)}


def layouts(dur: float, cortes: list[float], eventos: list[dict], rng=None) -> list[dict]:
    """El calendario de layouts del Short: [{«t», «dur», «nombre»}] sin huecos. Empieza y acaba en «base» (gancho y cierre), alterna base / pantalla / cara y cambia en un corte
    de plano si hay uno a menos de 0,8 s. Las escenas a pantalla completa mandan mientras duran (el montaje las pinta encima)."""
    r = rng or random.Random(3)
    orden = ["pantalla", "base", "cara", "base", "pantalla", "cara"]
    cortes = sorted(cortes)
    out: list[dict] = []
    t, k, nombre = 0.0, 0, "base"
    while t < dur - 0.01:
        lo, hi = LAYOUT_DURS[nombre]
        fin = t + r.uniform(lo, hi) if t > 0 else T_GANCHO + 1.8
        cerca = [c for c in cortes if abs(c - fin) <= 0.8 and c > t + 1.2]
        if cerca:
            fin = min(cerca, key=lambda c: abs(c - fin))
        if dur - fin < 3.2 or fin >= dur:
            fin = dur
        out.append({"t": round(t, 2), "dur": round(fin - t, 2), "nombre": nombre})
        t = fin
        nombre = orden[k % len(orden)] if (dur - t) > T_CTA_FINAL + 2.0 else "base"
        k += 1
    if len(out) > 1 and out[-1]["nombre"] != "base":
        out[-1]["nombre"] = "base"
    juntos: list[dict] = []
    for x in out:                                                   # (dos iguales seguidos = uno solo)
        if juntos and juntos[-1]["nombre"] == x["nombre"]:
            juntos[-1]["dur"] = round(juntos[-1]["dur"] + x["dur"], 2)
        else:
            juntos.append(dict(x))
    return juntos


def layout_en(lays: list[dict], t: float) -> str:
    for x in lays:
        if x["t"] <= t < x["t"] + x["dur"]:
            return x["nombre"]
    return "base"


def sonido_de(ev: dict) -> str:
    return {"palabra": "pop", "tarjeta": "pop", "ilus": "pop", "escena": "whoosh", "zoom_pantalla": "tick", "zoom_cara": "tick", "flash": "tick"}.get(ev["tipo"], "")


# ------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    frase = ("Hoy te voy a enseñar cómo Google decide cuál es tu página principal. Si tienes dos páginas parecidas, Google elige solo y a veces se equivoca. "
             "La solución es la etiqueta canonical. Con eso le dices cuál es la buena. El 40 por ciento de los sitios no la usa y pierde tráfico. "
             "Revisa Search Console cada semana y arregla lo que veas. Así evitas el contenido duplicado y subes posiciones.").split()
    words = []
    for k, w in enumerate(frase):
        t = 0.2 + k * 0.33
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.28, 2)})
    dur = words[-1]["end"] + 0.3
    ilus = {"lupa", "candado"}
    e_ok = {"tipo": "palabra", "t": 99, "cita": "la etiqueta canonical", "texto": "canonical"}
    pos = ubica("la etiqueta canonical", words)
    caso("la cita se ancla donde se dice (aunque el segundo de la IA esté lejos)", pos >= 0 and norm(words[pos]["word"]) == "la" and 0 < words[pos]["start"] < dur)
    caso("una cita que no está en el vídeo no ancla (-1) y una con una palabra de más sí (≥ 70 % de las palabras seguidas)", ubica("instala el pixel de facebook ahora", words) == -1 and ubica("la etiqueta canonical elige", words) >= 0)
    caso("una cifra tiene que decirse: «40 %» sí («40 por ciento»), «73 %» no", cifras_dichas("40 %", words) and not cifras_dichas("73 %", words))
    raw = [e_ok, {"tipo": "palabra", "t": 3, "cita": "invento total que no existe aqui", "texto": "NADA"},
           {"tipo": "tarjeta", "kind": "dato", "valor": "73 %", "texto": "de sitios", "cita": "el 40 por ciento", "t": 8},
           {"tipo": "tarjeta", "kind": "dato", "valor": "40 %", "texto": "no la usa", "cita": "el 40 por ciento de los", "t": 8},
           {"tipo": "ilus", "ilus": "lupa", "texto": "buscar", "cita": "Revisa Search Console cada semana", "t": 12},
           {"tipo": "ilus", "ilus": "inventada", "cita": "Revisa Search Console cada", "t": 12},
           {"tipo": "escena", "kind": "lista", "items": ["Una sola URL", "Canonical", "Revisar"], "cita": "La solución es la etiqueta", "t": 5}]
    ev, desc = valida_eventos(raw, words, dur, ilus)
    caso("valida: queda lo anclado y verdadero (palabra, dato con cifra dicha, ilustración válida, lista) y se descarta cita inventada, cifra no dicha e ilustración inexistente",
         [e["tipo"] for e in ev].count("palabra") == 1 and [e.get("kind") for e in ev if e["tipo"] == "tarjeta"] == ["dato"] and [e["ilus"] for e in ev if e["tipo"] == "ilus"] == ["lupa"]
         and any(e["tipo"] == "escena" for e in ev) and len(desc) >= 3, (len(ev), desc))
    mal = [{"tipo": "tarjeta", "kind": "punto", "texto": "Etiqueta canonical", "cita": "frase que la IA se inventa mal copiada", "t": 6.0},
           {"tipo": "palabra", "texto": "FUNNEL", "cita": "otra frase mal copiada", "t": 6.0},
           {"tipo": "ilus", "ilus": "lupa", "texto": "", "cita": "nada que ver", "t": 8.0}]
    ev3, desc3 = valida_eventos(mal, words, dur, ilus)
    caso("si la IA copia mal la cita, el evento se ancla donde se dice lo que ENSEÑA (canonical) y se descarta el que enseña algo que no se dice (funnel); una ilustración sin etiqueta recibe la de la biblioteca",
         sorted(e["tipo"] for e in ev3) == ["ilus", "tarjeta"] and abs(next(e["t"] for e in ev3 if e["tipo"] == "tarjeta") - (next(float(w["start"]) for w in words if norm(w["word"]) == "etiqueta") - ADELANTO)) < 0.05
         and next(e["texto"] for e in ev3 if e["tipo"] == "ilus") != "", (ev3, desc3))
    out, mot = convivencia(ev + [{"tipo": "palabra", "t": ev[0]["t"] + 0.2, "texto": "OTRA", "dur": 1.5}, {"tipo": "palabra", "t": 0.5, "texto": "PRONTO", "dur": 1.2}], dur)
    caso("convivencia: dos textos grandes a la vez → solo el primero; lo del gancho (t<2.4) se retrasa; la escena no queda en los últimos 3 s",
         len([e for e in out if e["tipo"] in ("palabra", "tarjeta") and e["t"] < e["t"] + 0.01 and _choca(e, {"t": ev[0]["t"], "dur": ev[0]["dur"]})]) <= 1
         and all(e["t"] >= T_GANCHO - 1e-6 for e in out if e["tipo"] != "escena") and all(e["t"] + e["dur"] <= dur - MARGEN_FIN + 1e-6 for e in out if e["tipo"] == "escena"), mot)
    cortes = [4.0, 7.5]
    antes = hueco_mayor(out, cortes, dur)
    lleno = rellena(out, cortes, dur, words)
    caso("REGLA DE RITMO: tras rellenar no queda ningún hueco de más de 2 s sin que pase algo (y antes sí lo había)", antes > RITMO_MAX_S and hueco_mayor(lleno, cortes, dur) <= RITMO_MAX_S + 1e-6, (round(antes, 1), round(hueco_mayor(lleno, cortes, dur), 2)))
    caso("lo que añade el código son acercamientos y palabras del propio vídeo (nunca un dato ni una tarjeta inventada)", all(e["tipo"] in ("zoom_pantalla", "zoom_cara", "palabra", "flash") for e in lleno if e.get("origen") == "codigo"))
    sin_nada = rellena([], [], 20.0, words)
    caso("sin ningún evento de la IA, el Short igual se llena (un cambio cada ≤ RITMO_MAX_S)", hueco_mayor(sin_nada, [], 20.0) <= RITMO_MAX_S + 1e-6 and len(sin_nada) >= 8, len(sin_nada))
    caso("determinista: mismo texto, mismo resultado", json.dumps(rellena(out, cortes, dur, words), sort_keys=True) == json.dumps(rellena(out, cortes, dur, words), sort_keys=True))
    m = parsea_meta({"titulo_yt": "Google elige mal https://x.com #seo", "hashtags_tt": ["seo", "#viral", "#Canonical Tag"], "tags_yt": ["#SEO", "seo", "Shorts", "canonical"], "gancho": "tu página principal", "cta_final": "sígueme"})
    caso("meta: sin URLs ni #, hashtags sin «viral» y sin repetir, tags sin «Shorts», gancho y CTA en mayúsculas", "http" not in m["titulo_yt"] and "#" not in m["titulo_yt"] and m["hashtags_tt"] == ["#seo", "#CanonicalTag"]
         and m["tags_yt"] == ["SEO", "canonical"] and m["gancho"] == "TU PÁGINA PRINCIPAL" and m["cta_final"] == "SÍGUEME", m)
    c = completa_meta({}, words, "Mi canal")
    caso("sin respuesta de la IA, los textos salen por código desde lo que dices", c["titulo_yt"] and c["gancho"] and c["tags_yt"] and c["cta_final"], c["titulo_yt"])
    p = arma_prompt(words, dur, "Mi canal", "SEO en español", '   - "lupa": buscar')
    caso("el prompt trae la transcripción con su reloj, las reglas de la cita y la lista de ilustraciones", "[0:00.2]" in p and "cita" in p and '"lupa"' in p and "SIN URLs" in p, p[:80])
    pals = [("Entonces,", 6.5), ("por", 6.8), ("ejemplo,", 6.8), ("uno", 7.3), ("de", 7.5), ("los", 7.6), ("conceptos", 7.7), ("que", 8.2), ("trabajamos", 8.3), ("acá", 8.8), ("en", 8.9), ("el", 9.0), ("SEO", 9.1), ("es", 9.3),
            ("básicamente", 9.5), ("el", 10.8), ("funnel", 11.1), ("que", 11.4), ("hay,", 11.6), ("¿no?", 11.9), ("Tú", 12.0), ("generalmente", 12.3), ("pasas", 12.7), ("de", 13.2), ("una", 13.4), ("parte", 13.5), ("informacional,", 13.8), ("a", 14.4), ("otra", 14.5)]
    wr = [{"word": w, "start": t0, "end": t0 + 0.25} for w, t0 in pals]
    base_ev = {"tipo": "escena", "kind": "ilus_grande", "ilus": "embudo", "texto": "EMBUDO", "cita": "uno de los conceptos que trabajamos acá en el SEO es básicamente", "t": 7.0}
    con_dicho, _ = valida_eventos([{**base_ev, "dicho": "funnel"}], wr, 40.0, {"embudo"})
    sin_dicho, _ = valida_eventos([base_ev], wr, 40.0, {"embudo"})
    caso("CONTEXTO (caso real del autor: el embudo salía a los 7 s y él habla del funnel a los 11): con la palabra «dicho» la ilustración sale en ELLA (11,1 − adelanto), no en la frase que la introduce",
         abs(con_dicho[0]["t"] - (11.1 - ADELANTO)) < 0.05, con_dicho)
    caso("sin «dicho» (plan de la IA flojo) sale en la ÚLTIMA palabra de la cita (el tema la cierra), mucho más cerca de lo que dice que su principio (6,9 s)", sin_dicho and 9.0 <= sin_dicho[0]["t"] < 11.0, sin_dicho)
    cita_ev, _ = valida_eventos([{"tipo": "escena", "kind": "cita", "texto": "Uno de los conceptos que trabajamos es el funnel", "cita": "uno de los conceptos que trabajamos acá", "t": 7.0}], wr, 40.0, {"embudo"})
    caso("una escena de FRASE (cita) sigue saliendo al empezar la frase", cita_ev and abs(cita_ev[0]["t"] - (7.3 - ADELANTO)) < 0.05, cita_ev)
    return filas
