"""🎯 Anclaje: cada tarjeta, ilustración, imagen, palabra clave y escena sale CUANDO DICES lo que muestra (el autor, 2026-10-11).

El problema (medido en su vídeo «Cómo crear contenido que Google y las IAs amen», 29 elementos): el director (Gemini) solo ve una marca
de tiempo cada ~5 s de la transcripción y, en vez de interpolar, se pega a la marca anterior o al principio de la idea. Resultado: la
mitad de lo que sale en pantalla llegaba 2-11 s ANTES de que lo dijera («Google Developers» 4 s antes, «Gran mejora» 5,8 s, «La vista de
experto» 11 s). Antes de esto el programa solo corregía el segundo de las palabras clave y de las escenas (buscando sus palabras a ±4 s):
tarjetas, ilustraciones e imágenes se quedaban donde dijo Gemini, y un fallo de más de 4 s tampoco se corregía.

Ahora, justo después de que llegue el plan, el programa mueve el `t` de cada elemento a la palabra con la que EMPIEZAS a decir lo que
muestra:
  1. por la CITA que el director añade a cada tarjeta, ilustración e imagen (3-8 palabras copiadas de la transcripción: el programa las
     busca de verdad, no hay que fiarse del segundo que dé);
  2. si no hay cita (un plan de antes) o no se encuentra, por el TEXTO del propio elemento («Google Developers», «Gran mejora»);
  3. si tampoco, se queda donde lo puso la IA (y el registro lo dice).
Tarjetas, ilustraciones e imágenes salen `adelanto_seg` (0,5) ANTES de la palabra; las palabras clave y las escenas, justo en ella (su
propio código ya les pone su pequeño adelanto). Nunca se ancla a palabras de un tramo que se corta (el elemento se perdería), ni se
adelanta antes del principio del tramo que se conserva. Es idempotente (anclar dos veces da lo mismo) y no gasta ninguna petición.

Funciones puras + `prueba()` (con trozos REALES de la transcripción de ese vídeo)."""
from __future__ import annotations

from . import ritmo as _r
from .util import log

_DEF = {
    "activo": True,
    "adelanto_seg": 0.5,        # tarjetas, ilustraciones e imágenes entran esto ANTES de la palabra (el autor: «de 1 a 2 s antes como mucho»)
    "ventana_seg": 12.0,        # se busca hasta esto DESPUÉS del segundo que dio la IA (la IA llega temprano, casi nunca tarde)
    "antes_seg": 5.0,           # …y hasta esto antes
    "cita_extra_seg": 3.0,      # la cita (copiada de lo dicho) se busca algo más lejos que el texto resumido
    "cita_min": 0.6,            # fracción de las palabras de la cita que tienen que estar dichas
    "texto_min": 0.5,           # lo mismo para el texto del elemento (una sola palabra tiene que estar entera)
    "afina_seg": 6.0,           # con cita: el texto del propio elemento se busca hasta esto DESPUÉS de donde empieza la cita
    "igual_seg": 0.35,          # si ya está a menos de esto de su sitio, no se toca
}

# (lista del plan, campos donde mirar —en orden—, ¿lleva el adelanto?)
LISTAS = (
    ("tarjetas", ("cita", "texto"), True),
    ("ilustraciones", ("cita", "etiqueta"), True),
    ("imagenes", ("cita",), True),
    ("palabras_clave", ("texto",), False),
    ("escenas", ("frase",), False),
    ("escenas_estilo", ("cita",), False),
)


def parametros(cfg=None) -> dict:
    """`videos.anclaje` de config.yaml encima de los valores por defecto (un valor raro no rompe nada: se ignora)."""
    p = dict(_DEF)
    try:
        propios = (cfg.get_path("videos.anclaje") if cfg is not None else None) or {}
    except Exception:  # noqa: BLE001
        propios = {}
    for k, v in (propios.items() if isinstance(propios, dict) else []):
        if k not in p or v is None:
            continue
        try:
            p[k] = bool(v) if isinstance(p[k], bool) else type(p[k])(v)
        except (TypeError, ValueError):
            pass
    return p


def _variantes(cont: list[str]) -> list[tuple[list[str], bool]]:
    """Las formas de buscar un texto: entero y, quitándole palabras del principio (la etiqueta «IA de Gemini» se dice «inteligencia
    artificial… Gemini»), cada resto. El bool dice si es un resto (más exigente: si queda UNA sola palabra, tiene que ser rara)."""
    out = [(cont, False)]
    out += [(cont[j:], True) for j in range(1, len(cont))]
    return out


def mejor_sitio(texto: str, t: float, words: list[dict], claves: list[str], antes: float, despues: float, minimo: float,
                valido=None) -> tuple[int, float] | None:
    """Dónde DICES `texto` entre `t - antes` y `t + despues`: (índice de la primera palabra, fracción de sus palabras que casan) o None.
    Gana la que casa más palabras y, a igualdad, la más cercana a `t` (una anterior a `t` pesa el doble: la IA llega temprano). Una
    palabra suelta tiene que casar entera, y si es solo un resto del texto, tiene que ser RARA (≤ 8 veces en todo el vídeo: «Gemini» sí,
    «Google» no)."""
    cont = _r._tokens(str(texto or "").replace("/", " "))             # «78/100» -> «78», «100» (dices «78»)
    if not cont or not words:
        return None
    mejor = None
    for tokens, resto in _variantes(cont):
        for i, w in enumerate(words):
            s = float(w.get("start", 0.0))
            if s < t - antes:
                continue
            if s > t + despues:
                break
            if valido is not None and not valido(s):
                continue
            m = _r._busca(tokens, claves, i)
            if m is None:
                continue
            _a, _b, n = m
            fr = n / len(cont)
            if len(tokens) == 1:
                if resto and (len(tokens[0]) < 5 or claves.count(tokens[0]) > 8):
                    continue
                if not resto and len(cont) == 1 and fr < 1.0:
                    continue
            if fr < minimo:
                continue
            dist = (s - t) if s >= t else 2.0 * (t - s)
            k = (round(fr, 2), not resto, -dist)
            if mejor is None or k > mejor[0]:
                mejor = (k, i, fr)
    return (mejor[1], mejor[2]) if mejor else None


def _valida_y_piso(segs):
    """(valido, piso): ¿esa palabra está en un tramo que se CONSERVA? y dónde empieza ese tramo (no se adelanta más allá). Sin `segs`,
    todo vale."""
    if not segs:
        return None, (lambda s: 0.0)
    tramos = [(float(a), float(b)) for a, b in segs]

    def valido(s: float) -> bool:
        return any(a - 0.05 <= s <= b + 0.05 for a, b in tramos)

    def piso(s: float) -> float:
        for a, b in tramos:
            if a - 0.05 <= s <= b + 0.05:
                return a
        return 0.0
    return valido, piso


def ancla_plan(plan: dict, words: list[dict], segs=None, cfg=None) -> dict:
    """Mueve, EN el plan, el `t` de tarjetas, ilustraciones, imágenes, palabras clave, escenas y escenas de estilo a las palabras con
    que las dices (ver arriba). `segs` = los tramos que se conservan (para no anclar a palabras que se cortan). Nunca lanza. Devuelve
    {"activo", "movidos": [{lista, texto, antes, despues, via}], "iguales": n, "sin_ancla": [{lista, texto, t}]}."""
    p = parametros(cfg)
    res: dict = {"activo": bool(p["activo"]), "movidos": [], "iguales": 0, "sin_ancla": []}
    if not p["activo"] or not isinstance(plan, dict) or not words:
        return res
    try:
        claves = [_r._clave(w.get("word")) for w in words]
        valido, piso = _valida_y_piso(segs)
        for lista, campos, con_adelanto in LISTAS:
            for it in plan.get(lista) or []:
                if not isinstance(it, dict):
                    continue
                try:
                    t0 = float(it["t"])
                except (KeyError, TypeError, ValueError):
                    continue
                hallado = None
                for campo in campos:
                    txt = str(it.get(campo) or "").strip()
                    if not txt:
                        continue
                    es_cita = campo == "cita"
                    extra = float(p["cita_extra_seg"]) if es_cita else 0.0
                    r = mejor_sitio(txt, t0, words, claves, float(p["antes_seg"]) + extra, float(p["ventana_seg"]) + extra,
                                    float(p["cita_min"] if es_cita else p["texto_min"]), valido)
                    if r:
                        hallado = (r[0], campo)
                        if es_cita:                                   # la cita da la ZONA; el texto del propio elemento, el segundo exacto
                            s_c = float(words[r[0]].get("start", 0.0))
                            for campo2 in campos[1:]:
                                txt2 = str(it.get(campo2) or "").strip()
                                r2 = mejor_sitio(txt2, s_c, words, claves, 0.3, float(p["afina_seg"]), float(p["texto_min"]), valido) if txt2 else None
                                if r2:
                                    hallado = (r2[0], "cita+texto")
                                    break
                        break
                etiqueta = str(next((it.get(c) for c in ("texto", "etiqueta", "frase", "cita") if it.get(c)), "") or "")
                if hallado is None:
                    res["sin_ancla"].append({"lista": lista, "texto": etiqueta, "t": round(t0, 2)})
                    continue
                s = float(words[hallado[0]].get("start", 0.0))
                nuevo = max(piso(s), s - float(p["adelanto_seg"])) if con_adelanto else s
                nuevo = max(0.0, nuevo)
                if abs(nuevo - t0) <= float(p["igual_seg"]):
                    res["iguales"] += 1
                    continue
                it["t"] = round(nuevo, 2)
                res["movidos"].append({"lista": lista, "texto": etiqueta, "antes": round(t0, 2), "despues": round(nuevo, 2),
                                       "via": hallado[1]})
    except Exception as exc:  # noqa: BLE001 — esto nunca tumba un vídeo: sin anclar, sale como antes
        log(f"AVISO: no pude anclar lo que sale en pantalla a tus palabras ({exc}); se queda donde lo puso la IA.")
    return res


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def resumen(info: dict) -> list[str]:
    """Las líneas del registro del trabajo."""
    if not info or not info.get("activo"):
        return []
    mov, sin = info.get("movidos") or [], info.get("sin_ancla") or []
    if not mov and not sin and not info.get("iguales"):
        return []
    lineas = []
    if mov:
        ant = [m for m in mov if m["despues"] > m["antes"]]
        tarde = [m for m in mov if m["despues"] < m["antes"]]
        partes = []
        if ant:
            partes.append(f"{len(ant)} salían ANTES de decirlo (hasta {max(m['despues'] - m['antes'] for m in ant):.1f} s)")
        if tarde:
            partes.append(f"{len(tarde)} salían DESPUÉS (hasta {max(m['antes'] - m['despues'] for m in tarde):.1f} s)")
        lineas.append(f"🎯 Anclaje a lo que dices: {len(mov)} elemento(s) movidos a sus palabras — " + "; ".join(partes)
                      + f" ({info.get('iguales', 0)} ya estaban bien).")
        for m in sorted(mov, key=lambda x: -abs(x["despues"] - x["antes"]))[:8]:
            lineas.append(f"   {m['lista']} «{m['texto'][:40]}»: {_mmss(m['antes'])} → {_mmss(m['despues'])} "
                          f"({m['despues'] - m['antes']:+.1f} s, por su {'cita y su texto' if m['via'] == 'cita+texto' else ('cita' if m['via'] == 'cita' else 'texto')})")
    else:
        lineas.append(f"🎯 Anclaje a lo que dices: todo ({info.get('iguales', 0)}) ya salía cuando lo dices.")
    if sin:
        lineas.append(f"   {len(sin)} sin poder anclar (no encuentro sus palabras: se quedan donde los puso la IA): "
                      + ", ".join(f"«{s['texto'][:30]}» ({_mmss(s['t'])})" for s in sin[:6]))
    return lineas


# ================================================================================================================ pruebas
# Trozos REALES de la transcripción del vídeo del autor del 6-oct (tiempos de su vídeo final): (palabra, segundo de inicio)
_REAL = {
    "developers": "esta(519.6) página(519.8) web(520.2) actualmente(520.5) live(521.4) tiene(521.6) muchos(522.6) enlaces(522.9) externos(523.3) "
                  "hacia(524.1) google(524.7) developers(525.1) que(525.7) es(526.2) el(526.5) gran(526.7) dominio(527.4)",
    "fase1": "Bueno,(94.0) Content(95.9) 6000,(96.4) como(97.3) les(97.5) decía,(97.7) funciona(98.1) con(98.3) distintas(98.7) fases.(99.2) "
             "La(99.6) primera(99.7) fase(100.0) es(100.3) Insights(100.6) de(101.3) MedidoNator(101.9) entonces(102.4) lo(103.1) que(103.2) hace(103.4)",
    "mantener": "pero(464.4) va(464.7) a(464.8) guardar(465.0) y(465.6) mantener,(465.7) esa(466.3) es(466.4) su(466.5) prioridad(466.6) "
                "mantener(467.0) tu(467.6) voz(468.0) a(468.3) la(468.8) vez(469.0) va(469.2) a(469.4) verificar,(469.5)",
    "experto": "algo(302.5) que(303.3) hay(303.6) que(303.9) tener(303.9) en(304.2) cuenta(304.3) hoy(304.5) en(304.7) geo(304.9) para(305.1) "
               "por(305.3) ejemplo(305.5) posicionarse(305.8) muy(306.6) bien(306.9) en(307.1) temas(307.4) de(307.6) inteligencias(307.8) "
               "artificiales(308.2) y(308.9) a(309.2) la(309.4) vez(309.4) que(309.5) es(309.6) como(312.2) les(313.0) decía(313.2) yo(313.4) "
               "antes(313.6) es(313.8) tener(314.0) esa(314.2) vista(314.4) de(314.6) expertise(315.0) esto(315.6)",
    "mejora": "teníamos(696.7) 33(697.5) puntos(697.9) y(698.4) efectivamente(699.0) como(699.5) cogimos(699.7) esas(700.0) consideraciones(700.3) "
              "estamos(701.1) en(701.5) 78(701.7) bueno(702.2) que(703.2) pues(704.2) fue(704.9) una(705.0) gran(705.2) mejora(705.4) bien(705.9)",
    "gemini": "temas(181.3) de(181.5) LLMs.(181.7) Entonces,(182.3) esto(182.7) funciona(183.2) con(183.5) inteligencia(183.7) artificial(184.2) "
              "y(184.4) lo(185.0) que(185.4) hace(185.6) esta(185.8) parte(186.1) de(186.3) evaluación(186.5) es(186.9) que(187.5) una(187.7) "
              "inteligencia(188.1) artificial,(188.7) en(189.1) este(189.3) caso(189.4) la(189.6) de(189.7) Gemini,(189.9) analiza(190.3)",
}


def _palabras(clave: str) -> list[dict]:
    import re
    out = []
    for m in re.finditer(r"(\S+)\(([\d.]+)\)", _REAL[clave]):
        s = float(m.group(2))
        out.append({"word": m.group(1), "start": s, "end": s + 0.25})
    return out


def prueba() -> list[dict]:
    """Casos REALES de su vídeo (qué decía y dónde salió cada cosa) + las reglas; con controles negativos en el desarrollo."""
    filas: list[dict] = []

    def caso(nombre, ok, detalle=""):
        filas.append({"caso": nombre, "ok": bool(ok), "detalle": str(detalle)[:240]})

    def una(lista, item, clave, segs=None, cfg=None, plan_extra=None):
        pl = {lista: [dict(item)], **(plan_extra or {})}
        info = ancla_plan(pl, _palabras(clave), segs, cfg)
        return pl[lista][0], info

    # --- sus casos: la tarjeta que salía 4 s antes de decir «Google Developers»
    it, info = una("tarjetas", {"t": 520.7, "tipo": "herramienta", "texto": "Google Developers", "duracion": 3.5}, "developers")
    caso("«Google Developers»: la tarjeta salía en el 520,7 y lo dices en el 524,7 → sale 0,5 s antes de la palabra (524,2)",
         abs(it["t"] - 524.2) < 0.02 and info["movidos"] and info["movidos"][0]["via"] == "texto", it)
    # --- «Fase 1» por su CITA (el texto del punto no es lo que dices: «La primera fase es Insights»)
    it, info = una("tarjetas", {"t": 95.5, "tipo": "punto", "texto": "Fase 1: Insights de Medidor 5000", "numero": 1,
                                "cita": "la primera fase es Insights de MedidoNator"}, "fase1")
    caso("«Fase 1»: por su cita («la primera fase es…») y afinada con su texto pasa del 95,5 al 99,5 («fase», 100,0)",
         abs(it["t"] - 99.5) < 0.02 and info["movidos"][0]["via"] == "cita+texto", it)
    it, info = una("tarjetas", {"t": 95.5, "tipo": "punto", "texto": "Fase 1: Insights de Medidor 5000", "numero": 1}, "fase1")
    caso("…y sin cita (un plan de antes), por su texto: sale cuando dices «fase… Insights» (99,5)",
         abs(it["t"] - 99.5) < 0.02 and info["movidos"][0]["via"] == "texto", it)
    # --- la palabra clave que salía 6 s antes y que con la ventana de 4 s no se corregía
    it, info = una("palabras_clave", {"t": 460.8, "texto": "MANTENER TU VOZ", "importancia": 3}, "mantener")
    caso("«MANTENER TU VOZ»: salía en el 460,8 y lo dices en el 467,0 → ahora justo en la palabra (las palabras clave no llevan adelanto)",
         abs(it["t"] - 467.0) < 0.02, it)
    # --- la escena que salía 11 s antes
    it, info = una("escenas", {"t": 303.0, "tipo": "estrellas", "frase": "LA VISTA DE EXPERTO", "duracion": 5.0}, "experto")
    caso("escena «LA VISTA DE EXPERTO»: salía en el 303,0 y lo dices en el 314,4 (11 s más tarde) → se ancla a 314,4",
         abs(it["t"] - 314.4) < 0.02, it)
    # --- la ilustración que salía 5,8 s antes
    it, info = una("ilustraciones", {"t": 699.4, "tipo": "grafica_sube", "duracion": 3.4, "etiqueta": "Gran mejora"}, "mejora")
    caso("ilustración «Gran mejora»: del 699,4 al 704,7 (0,5 s antes de «gran mejora» 705,2)", abs(it["t"] - 704.7) < 0.02, it)
    # --- «IA de Gemini»: la etiqueta no es lo que dices, la cita sí
    it, info = una("ilustraciones", {"t": 180.6, "tipo": "robot", "duracion": 3.4, "etiqueta": "IA de Gemini",
                                     "cita": "esto funciona con inteligencia artificial"}, "gemini")
    caso("«IA de Gemini»: con su cita («esto funciona con inteligencia artificial») sale en el 182,2, antes eran 9 s de adelanto",
         abs(it["t"] - 182.2) < 0.02, it)
    it, info = una("ilustraciones", {"t": 180.6, "tipo": "robot", "duracion": 3.4, "etiqueta": "IA de Gemini"}, "gemini")
    caso("…y sin cita, por la palabra RARA de su etiqueta («Gemini», dicha 3 veces en el vídeo): 189,4", abs(it["t"] - 189.4) < 0.02, it)
    it, info = una("ilustraciones", {"t": 180.6, "tipo": "robot", "duracion": 3.4, "etiqueta": "IA"}, "gemini")
    caso("una etiqueta sin ninguna palabra que se diga («IA») no se ancla: se queda y se avisa",
         it["t"] == 180.6 and info["sin_ancla"] and not info["movidos"], info)

    # --- la cita da la ZONA y el texto del propio elemento el segundo exacto (si la cita empieza una frase antes, la tarjeta no sale con ella)
    it, info = una("tarjetas", {"t": 520.7, "tipo": "herramienta", "texto": "Google Developers",
                                "cita": "muchos enlaces externos hacia google developers"}, "developers")
    caso("cita + texto: la cita empieza en «muchos enlaces» (522,6) pero la tarjeta sale en «google developers» (524,2), no 2 s antes",
         abs(it["t"] - 524.2) < 0.02 and info["movidos"][0]["via"] == "cita+texto", it)
    it, info = una("tarjetas", {"t": 95.5, "tipo": "punto", "texto": "Plan y notas", "cita": "la primera fase es insights"}, "fase1")
    caso("…y si el texto del elemento no se dice («Plan y notas»), manda la cita sola: 99,2", abs(it["t"] - 99.2) < 0.02 and info["movidos"][0]["via"] == "cita", it)
    it, info = una("tarjetas", {"t": 693.5, "tipo": "dato", "texto": "78/100", "extra": "Puntuación tras la optimización"}, "mejora")
    caso("«78/100»: dices solo «78» (701,7) y la tarjeta salía 8 s antes → ahora en 701,2", abs(it["t"] - 701.2) < 0.02, it)

    # --- reglas
    it, info = una("tarjetas", {"t": 524.2, "tipo": "herramienta", "texto": "Google Developers"}, "developers")
    caso("idempotente: anclar lo ya anclado no lo mueve (y se cuenta como «ya estaba bien»)", it["t"] == 524.2 and info["iguales"] == 1 and not info["movidos"], info)
    pl = {"tarjetas": [{"t": 520.7, "tipo": "herramienta", "texto": "Google Developers"}]}
    ancla_plan(pl, _palabras("developers"), None, None)
    t1 = pl["tarjetas"][0]["t"]
    ancla_plan(pl, _palabras("developers"), None, None)
    caso("…dos pasadas seguidas dan el mismo segundo", pl["tarjetas"][0]["t"] == t1, pl)
    it, info = una("tarjetas", {"t": 520.7, "tipo": "herramienta", "texto": "Google Developers"}, "developers", segs=[(500.0, 524.5), (530.0, 600.0)])
    caso("no ancla a palabras que se CORTAN: «Google Developers» cae en un corte (524,5-530) → se queda donde estaba",
         it["t"] == 520.7 and info["sin_ancla"], (it, info))
    it, info = una("tarjetas", {"t": 520.7, "tipo": "herramienta", "texto": "Google Developers"}, "developers", segs=[(524.3, 600.0)])
    caso("nunca antes del principio del tramo que se conserva: el tramo empieza en el 524,3 → la tarjeta en 524,3, no en 524,2", abs(it["t"] - 524.3) < 0.02, it)
    it, info = una("tarjetas", {"t": 300.0, "tipo": "herramienta", "texto": "Google Developers"}, "developers")
    caso("lo que está a más de la ventana (12 s) no se persigue: se queda y se avisa", it["t"] == 300.0 and info["sin_ancla"], info)
    it, info = una("tarjetas", {"t": 520.7, "tipo": "herramienta", "texto": "Google Developers"}, "developers", cfg=_Cfg({"activo": False}))
    caso("apagable (`videos.anclaje.activo: false`): no toca nada", it["t"] == 520.7 and not info["movidos"], info)
    it, info = una("tarjetas", {"t": 520.7, "tipo": "herramienta", "texto": "Google Developers"}, "developers", cfg=_Cfg({"adelanto_seg": 1.5}))
    caso("el adelanto se cambia en config (1,5 s → 523,2)", abs(it["t"] - 523.2) < 0.02, it)
    # una cita inventada (no está en lo dicho) no mueve nada por su cuenta: cae al texto del elemento
    it, info = una("tarjetas", {"t": 520.7, "tipo": "herramienta", "texto": "Google Developers", "cita": "esta frase no la dice nadie jamás"}, "developers")
    caso("una cita que NO está en lo dicho se ignora y se usa el texto del elemento", abs(it["t"] - 524.2) < 0.02 and info["movidos"][0]["via"] == "texto", it)
    # varias menciones: gana la que casa más palabras / la más cercana por delante
    w = [{"word": "google", "start": 11.0, "end": 11.2}, {"word": "developers", "start": 11.3, "end": 11.6},
         {"word": "otra", "start": 14.5, "end": 14.7}, {"word": "google", "start": 17.5, "end": 17.7}, {"word": "developers", "start": 17.8, "end": 18.1}]
    pl = {"tarjetas": [{"t": 14.0, "tipo": "herramienta", "texto": "Google Developers"}]}
    ancla_plan(pl, w, None, None)
    caso("dos menciones (11,0 a 3 s antes y 17,5 a 3,5 s después): gana la de DELANTE (17,0): la IA llega temprano, casi nunca tarde",
         abs(pl["tarjetas"][0]["t"] - 17.0) < 0.02, pl)
    # una palabra tan repetida como «Google» (más de 8 veces) no basta por sí sola para anclar la etiqueta «IA de Google»
    w = [{"word": "google", "start": 3.0 * k, "end": 3.0 * k + 0.4} for k in range(12)]
    pl = {"ilustraciones": [{"t": 15.0, "tipo": "robot", "etiqueta": "IA de Google"}]}
    info = ancla_plan(pl, w, None, None)
    caso("una palabra suelta MUY repetida («Google», 12 veces) no ancla una etiqueta («IA de Google»): se queda y se avisa",
         pl["ilustraciones"][0]["t"] == 15.0 and info["sin_ancla"], info)
    # robustez
    caso("un plan raro o sin transcripción no revienta", ancla_plan({"tarjetas": [{"texto": "x"}, 5, None]}, [], None, None)["movidos"] == []
         and ancla_plan(None, _palabras("mejora"), None, None)["movidos"] == [])
    # el registro
    pl = {"tarjetas": [{"t": 520.7, "tipo": "herramienta", "texto": "Google Developers"}, {"t": 50.0, "tipo": "herramienta", "texto": "Nada De Esto"}]}
    info = ancla_plan(pl, _palabras("developers"), None, None)
    lineas = resumen(info)
    caso("el registro cuenta lo movido (con cuánto y por qué) y lo que no se pudo anclar",
         any("ANTES de decirlo" in x for x in lineas) and any("sin poder anclar" in x for x in lineas) and any("Google Developers" in x for x in lineas), lineas)
    return filas


class _Cfg:
    """Un config de mentira para las pruebas: `videos.anclaje.*` con lo que se pida."""
    def __init__(self, anclaje: dict):
        self._a = anclaje

    def get_path(self, k, d=None):
        return self._a if k == "videos.anclaje" else d
